//! The gate: request → approve/deny → consume, with every refusal named.
//!
//! This is the only place a [`Grant`] is created, which is what makes
//! [`Confidential::reveal`](crate::Confidential::reveal) unreachable except
//! through a passed approval. Everything the summary calls a "failure mode" is a
//! variant of [`GateError`] and a named arm of [`Gate::with_confirmed_value`], so
//! the tests assert on the *reason* rather than on "it errored somehow".

use std::collections::BTreeMap;
use std::fmt;

use sha2::{Digest as _, Sha256};

use crate::audit::{AuditLog, EventDraft, EventKind};
use crate::gate::GateError::*;
use crate::intent::{Intent, Nonce, PrincipalId, PrincipalRole, VerifiedPrincipal};
use crate::policy::{ConsumerBinding, GateSpec, Gated, ReplayPolicy, authority_admits};
use crate::{Confidential, Tick, ValueBinding};

/// An opaque handle for one confirmation request.
///
/// Opaque on purpose: a caller cannot forge one by formatting a plausible string,
/// because the fields are private and only [`Gate::request`] mints them. The
/// identity of both the requester and the nonce are hashed in, so two requests
/// for the same value and intent are still distinct handles.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
pub struct RequestId(String);

impl RequestId {
    fn mint(
        requester: &PrincipalId,
        nonce: &Nonce,
        binding: ValueBinding,
        intent: &Intent,
    ) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(b"leviath-confirm/request/v1\0");
        hasher.update(requester.as_str().as_bytes());
        hasher.update(b"\0");
        hasher.update(nonce.as_str().as_bytes());
        hasher.update(b"\0");
        hasher.update(binding.to_string().as_bytes());
        hasher.update(b"\0");
        hasher.update(intent.to_string().as_bytes());
        let digest = hasher.finalize();
        let mut out = String::with_capacity(48);
        out.push_str("creq_");
        for byte in digest.iter().take(16) {
            out.push(char::from_digit((byte >> 4) as u32, 16).unwrap_or('0'));
            out.push(char::from_digit((byte & 0x0f) as u32, 16).unwrap_or('0'));
        }
        Self(out)
    }

    /// The ledger's own pseudo-request, used for events that belong to no request.
    pub(crate) fn ledger() -> Self {
        Self("creq_ledger".to_owned())
    }

    /// The handle as a string, for logs and evidence.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for RequestId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl<'de> serde::Deserialize<'de> for RequestId {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error as _;
        let text = String::deserialize(deserializer)?;
        // `strip_prefix` rather than `text[5..]`: the index form panics when the
        // byte after `creq_` lands inside a multi-byte character, so a malformed
        // payload from a client would abort the process instead of being
        // refused. `strip_prefix` returns `None` for a non-match and never
        // slices at a non-boundary.
        let plausible = text
            .strip_prefix("creq_")
            .is_some_and(|rest| rest.len() == 32 && rest.bytes().all(|b| b.is_ascii_hexdigit()));
        if !plausible {
            return Err(D::Error::custom(
                "request id must be `creq_` plus 32 hex digits",
            ));
        }
        Ok(Self(text))
    }
}

/// The approver's decision. The input-side choice set, mirroring the SDL's
/// `GateDecision { APPROVE REJECT }` — deliberately without an `EXPIRED` arm,
/// because a caller cannot decide to expire.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    /// Grant the approval.
    Approve,
    /// Refuse it, with a reason.
    Reject,
}

/// What a decision produced. An outcome enum, mirroring the SDL's
/// `GateOutcome { APPROVED REJECTED EXPIRED }`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecisionOutcome {
    /// The approval was recorded and is live until it expires.
    Approved,
    /// The request was refused; the reason was recorded.
    Rejected,
    /// The request could not be decided because it had already run out of time.
    /// A distinct outcome from `Rejected`: nothing was decided.
    Expired,
}

impl DecisionOutcome {
    /// The SDL spelling.
    pub const fn sdl_name(self) -> &'static str {
        match self {
            Self::Approved => "APPROVED",
            Self::Rejected => "REJECTED",
            Self::Expired => "EXPIRED",
        }
    }
}

impl fmt::Display for DecisionOutcome {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.sdl_name())
    }
}

/// Where a request is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequestState {
    /// Raised, not yet decided.
    Pending,
    /// Approved, live, not yet spent.
    Approved,
    /// Approved and then spent. Terminal.
    Consumed,
    /// Refused by an approver. Terminal.
    Rejected,
    /// Ran out of time undecided. Terminal.
    Expired,
}

impl RequestState {
    /// A stable label.
    pub const fn label(self) -> &'static str {
        match self {
            Self::Pending => "PENDING",
            Self::Approved => "APPROVED",
            Self::Consumed => "CONSUMED",
            Self::Rejected => "REJECTED",
            Self::Expired => "EXPIRED",
        }
    }
}

impl fmt::Display for RequestState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.label())
    }
}

/// A read-only view of a request, for a UI and for evidence.
///
/// Carries no secret: identity, version, digest, the intent, the approver's
/// label and the timestamps. This is what a Desk panel renders, which is why
/// nothing here needed redacting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestView {
    /// The handle.
    pub id: RequestId,
    /// Where it is *now*, with expiry applied.
    pub state: RequestState,
    /// The value it concerns.
    pub binding: ValueBinding,
    /// What it authorises.
    pub intent: Intent,
    /// Who raised it.
    pub requester: PrincipalId,
    /// The role of the principal that raised it.
    pub requester_role: PrincipalRole,
    /// The nonce that makes this request distinguishable from a re-issue.
    pub nonce: Nonce,
    /// Who decided it, if anyone.
    pub decided_by: Option<PrincipalId>,
    /// What they decided, if they did.
    pub outcome: Option<DecisionOutcome>,
    /// When it was raised.
    pub issued_at: Tick,
    /// When it expires. Derived, never stored.
    pub expires_at: Tick,
    /// Whether it has been spent.
    pub consumed: bool,
}

/// One confirmation request's record.
#[derive(Debug, Clone)]
struct RequestRecord {
    id: RequestId,
    binding: ValueBinding,
    intent: Intent,
    requester: PrincipalId,
    requester_role: PrincipalRole,
    nonce: Nonce,
    issued_at: Tick,
    decided_by: Option<PrincipalId>,
    outcome: Option<DecisionOutcome>,
    consumed: bool,
    /// The spec in force when the request was raised. Re-checked at consumption,
    /// so narrowing a policy mid-flight fails closed.
    spec: GateSpec,
}

impl RequestRecord {
    fn expires_at(&self) -> Tick {
        self.issued_at.saturating_add(self.spec.ttl)
    }

    /// The state as of `now`, deriving expiry rather than storing it.
    fn state_at(&self, now: Tick) -> RequestState {
        match self.outcome {
            None => {
                if now >= self.expires_at() {
                    RequestState::Expired
                } else {
                    RequestState::Pending
                }
            }
            Some(DecisionOutcome::Rejected) => RequestState::Rejected,
            Some(DecisionOutcome::Expired) => RequestState::Expired,
            Some(DecisionOutcome::Approved) => {
                if self.consumed {
                    RequestState::Consumed
                } else if now >= self.expires_at() {
                    RequestState::Expired
                } else {
                    RequestState::Approved
                }
            }
        }
    }
}

/// A passed approval, in the hand of the consuming call.
///
/// No public constructor, no `Clone`, no `Deserialize`: the only way to hold one
/// is to have received it from [`Gate::with_confirmed_value`], which returns it
/// *inside* the closure. It cannot be stashed, and it cannot be replayed by
/// reserialising it.
pub struct Grant {
    request: RequestId,
    binding: ValueBinding,
    grantee: PrincipalId,
    intent: Intent,
    expires_at: Tick,
    spec: GateSpec,
}

impl Grant {
    /// The request this grant came from.
    pub const fn request(&self) -> &RequestId {
        &self.request
    }

    /// The value the grant opens.
    pub const fn binding(&self) -> ValueBinding {
        self.binding
    }

    /// The intent the grant is for.
    pub const fn intent(&self) -> &Intent {
        &self.intent
    }

    /// When the grant dies.
    pub const fn expires_at(&self) -> Tick {
        self.expires_at
    }

    /// Open `value`, re-checking everything.
    ///
    /// Called by [`Confidential::reveal`](crate::Confidential::reveal). The
    /// re-checks are not redundancy: the grant may have been held across a
    /// rotation, past its expiry, or be pointed at a different value, and none of
    /// those are visible at the call site.
    pub(crate) fn open(self, value: &Confidential, now: Tick) -> Result<&str, GateError> {
        if now >= self.expires_at {
            return Err(GrantExpired {
                expires_at: self.expires_at,
                now,
            });
        }
        if self.spec.bind_digest {
            let live = value.binding();
            if live.value_id != self.binding.value_id || live.version != self.binding.version {
                return Err(ValueNotBound {
                    granted: self.binding.to_string(),
                    presented: live.to_string(),
                });
            }
            if live.digest != self.binding.digest {
                return Err(DigestChanged {
                    granted: self.binding.digest.hex(),
                    presented: live.digest.hex(),
                });
            }
        } else if value.binding().value_id != self.binding.value_id {
            return Err(ValueNotBound {
                granted: self.binding.to_string(),
                presented: value.binding().to_string(),
            });
        }
        // The grant is consumed by this call: `self` is moved, so the compiler
        // makes "one approval, one read" true without a runtime flag.
        Ok(value.raw_bytes())
    }
}

impl fmt::Debug for Grant {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Grant")
            .field("request", &self.request.as_str())
            .field("binding", &self.binding)
            .field("grantee", &self.grantee.as_str())
            .field("intent", &self.intent)
            .field("expires_at", &self.expires_at)
            .finish()
    }
}

/// Everything the gate refuses, by name.
///
/// Each variant is a DoD failure mode: `Pending`, `Denied`/`Rejected`,
/// `Expired`, `WrongApprover`, `DigestChanged`, `ValueNotBound`,
/// `IntentNotCovered`, `AlreadyConsumed`, `RequestNotKnown`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateError {
    /// The request exists but no decision has been recorded, so nothing is
    /// authorised yet.
    ApprovalPending {
        /// The request still awaiting a decision.
        request: RequestId,
    },
    /// An approver refused the request.
    ApprovalRejected {
        /// The request that was refused.
        request: RequestId,
    },
    /// The request ran out of time before it was decided.
    ApprovalExpired {
        /// The request.
        request: RequestId,
        /// When it expired.
        expires_at: Tick,
        /// The reading that found it expired.
        now: Tick,
    },
    /// The approval lives no longer.
    GrantExpired {
        /// When it died.
        expires_at: Tick,
        /// The reading that found it dead.
        now: Tick,
    },
    /// The decision was presented after the request had already expired: an
    /// approval cannot resurrect it, and this is reported as `EXPIRED` rather
    /// than as a rejection because nothing was decided.
    DecisionAfterExpiry {
        /// The request.
        request: RequestId,
        /// When it expired.
        expires_at: Tick,
        /// The reading the decision arrived at.
        now: Tick,
    },
    /// The approver's role does not satisfy the declared authority. A machine
    /// principal cannot resolve a `HUMAN_GATE`.
    WrongAuthority {
        /// The authority the policy declares.
        required: &'static str,
        /// The role of the principal that tried.
        approver_role: &'static str,
    },
    /// The approver is the requester, and the policy requires separation of
    /// duties.
    SelfApprovalRefused {
        /// The one principal that held both roles.
        principal: PrincipalId,
    },
    /// The principal presenting the decision is not the principal that raised
    /// the request, and the policy binds consumption to the requester.
    WrongPrincipal {
        /// The principal bound to the request.
        expected: PrincipalId,
        /// The principal that presented the decision.
        presented: PrincipalId,
    },
    /// The consumption names an intent the approval does not cover: a different
    /// action, a different target, or a scope the approval does not contain.
    IntentNotCovered {
        /// The approved intent.
        approved: String,
        /// The intent the consumer asked for.
        requested: String,
    },
    /// The value's bytes changed under a stable identity and version.
    DigestChanged {
        /// The digest the approval was bound to.
        granted: String,
        /// The digest found live.
        presented: String,
    },
    /// The approval binds a different value (or a different revision) than the
    /// one presented.
    ValueNotBound {
        /// What the approval names.
        granted: String,
        /// What was presented.
        presented: String,
    },
    /// The approval was already spent. The replay arm.
    AlreadyConsumed {
        /// The spent request.
        request: RequestId,
        /// The principal that spent it.
        consumed_by: PrincipalId,
    },
    /// No such request. Also the arm a forged handle lands in.
    RequestNotKnown {
        /// The handle that was presented.
        request: RequestId,
    },
    /// The same nonce and intent were used twice in a mode that does not permit
    /// repeat requests.
    NonceReused {
        /// The nonce.
        nonce: Nonce,
        /// The request the nonce first minted.
        first: RequestId,
    },
    /// The policy in force has no workable gate.
    PolicyNotGateable {
        /// The policy.
        policy: &'static str,
    },
    /// A field did not satisfy its own contract.
    MalformedField {
        /// Which field.
        field: &'static str,
        /// Why.
        reason: &'static str,
    },
    /// A ledger failed its chain check.
    LedgerBroken {
        /// The first event that failed.
        seq: u64,
        /// Why.
        reason: String,
    },
    /// A ledger carried no events.
    LedgerEmpty,
    /// The gate has no ledger, so nothing can be recorded and nothing is
    /// authorised.
    LedgerMissing,
}

impl GateError {
    /// A machine-readable code, stable across versions and safe to log.
    ///
    /// This is the string that appears in a refusal's audit event, so a test can
    /// assert that a replay was refused *as a replay* rather than merely refused.
    pub fn code(&self) -> &'static str {
        match self {
            Self::ApprovalPending { .. } => "approval_pending",
            Self::ApprovalRejected { .. } => "approval_rejected",
            Self::ApprovalExpired { .. } => "approval_expired",
            Self::GrantExpired { .. } => "grant_expired",
            Self::DecisionAfterExpiry { .. } => "decision_after_expiry",
            Self::WrongAuthority { .. } => "wrong_authority",
            Self::SelfApprovalRefused { .. } => "self_approval_refused",
            Self::WrongPrincipal { .. } => "wrong_principal",
            Self::IntentNotCovered { .. } => "intent_not_covered",
            Self::DigestChanged { .. } => "digest_changed",
            Self::ValueNotBound { .. } => "value_not_bound",
            Self::AlreadyConsumed { .. } => "already_consumed",
            Self::RequestNotKnown { .. } => "request_not_known",
            Self::NonceReused { .. } => "nonce_reused",
            Self::PolicyNotGateable { .. } => "policy_not_gateable",
            Self::MalformedField { .. } => "malformed_field",
            Self::LedgerBroken { .. } => "ledger_broken",
            Self::LedgerEmpty => "ledger_empty",
            Self::LedgerMissing => "ledger_missing",
        }
    }
}

impl fmt::Display for GateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ApprovalPending { request } => {
                write!(f, "approval_pending: {request} has no decision recorded")
            }
            Self::ApprovalRejected { request } => {
                write!(
                    f,
                    "approval_rejected: {request} was refused by its approver"
                )
            }
            Self::ApprovalExpired {
                request,
                expires_at,
                now,
            } => {
                write!(
                    f,
                    "approval_expired: {request} expired at {expires_at}, observed at {now}"
                )
            }
            Self::GrantExpired { expires_at, now } => {
                write!(
                    f,
                    "grant_expired: the approval died at {expires_at}, read at {now}"
                )
            }
            Self::DecisionAfterExpiry {
                request,
                expires_at,
                now,
            } => write!(
                f,
                "decision_after_expiry: {request} expired at {expires_at}; the decision arrived at {now} and nothing was decided"
            ),
            Self::WrongAuthority {
                required,
                approver_role,
            } => write!(
                f,
                "wrong_authority: @user_confirmation(by: {required}) is not satisfiable by a {approver_role} principal"
            ),
            Self::SelfApprovalRefused { principal } => write!(
                f,
                "self_approval_refused: {principal} raised the request and may not also approve it"
            ),
            Self::WrongPrincipal {
                expected,
                presented,
            } => {
                write!(
                    f,
                    "wrong_principal: the request is bound to {expected}, not {presented}"
                )
            }
            Self::IntentNotCovered {
                approved,
                requested,
            } => write!(
                f,
                "intent_not_covered: the approval covers `{approved}`, the consumer asked for `{requested}`"
            ),
            Self::DigestChanged { granted, presented } => write!(
                f,
                "digest_changed: approved {granted}, live bytes are {presented}"
            ),
            Self::ValueNotBound { granted, presented } => {
                write!(
                    f,
                    "value_not_bound: approved {granted}, presented {presented}"
                )
            }
            Self::AlreadyConsumed {
                request,
                consumed_by,
            } => {
                write!(
                    f,
                    "already_consumed: {request} was spent by {consumed_by}; this is a replay"
                )
            }
            Self::RequestNotKnown { request } => {
                write!(f, "request_not_known: no record of {request}")
            }
            Self::NonceReused { nonce, first } => {
                write!(
                    f,
                    "nonce_reused: nonce {nonce} already minted {first} for this intent"
                )
            }
            Self::PolicyNotGateable { policy } => {
                write!(
                    f,
                    "policy_not_gateable: policy `{policy}` declares no workable gate"
                )
            }
            Self::MalformedField { field, reason } => {
                write!(f, "malformed_field: `{field}` {reason}")
            }
            Self::LedgerBroken { seq, reason } => {
                write!(f, "ledger_broken: event {seq}: {reason}")
            }
            Self::LedgerEmpty => f.write_str("ledger_empty: the ledger carries no events"),
            Self::LedgerMissing => f.write_str("ledger_missing: the gate has no ledger"),
        }
    }
}

impl std::error::Error for GateError {}

impl serde::Serialize for GateError {
    /// Serialise as an envelope of code, message and the non-secret structure.
    ///
    /// A `Serialize` impl exists so a GraphQL resolver can put a refusal into an
    /// error extension; it carries no secret, by construction, because no
    /// `GateError` variant holds one. `Deserialize` is deliberately absent.
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct as _;
        let code = self.code();
        let message = self.to_string();
        let mut state = serializer.serialize_struct("GateError", 2)?;
        state.serialize_field("code", code)?;
        state.serialize_field("message", &message)?;
        state.end()
    }
}

/// The confirm gate for one gated type.
///
/// Holds the requests it has raised and the ledger it writes, and nothing else:
/// it never sees the value's bytes, which is why compromising the gate does not
/// by itself disclose a secret — it only produces the authority to read one, and
/// that authority still has to be handed to [`Confidential::reveal`].
pub struct Gate<G: Gated> {
    spec: GateSpec,
    policy: crate::PolicyName,
    requests: BTreeMap<RequestId, RequestRecord>,
    /// The first request each nonce minted, for `SingleUsePerIntent`.
    nonces: BTreeMap<(String, String, String), RequestId>,
    ledger: AuditLog,
    _gated: std::marker::PhantomData<fn() -> G>,
}

impl<G: Gated> fmt::Debug for Gate<G> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Gate")
            .field("policy", &self.policy.as_str())
            .field("authority", &self.spec.authority.sdl_name())
            .field("requests", &self.requests.len())
            .field("ledger_len", &self.ledger.len())
            .finish()
    }
}

/// The nonce key: (requester, nonce, intent). Two different requesters may reuse
/// a nonce string; one requester may not reuse it for the same intent.
type NonceKey = (String, String, String);

impl<G: Gated> Gate<G> {
    /// Open a gate for `G`, writing an opening entry to a fresh ledger.
    pub fn open(at: Tick) -> Self {
        let spec = G::spec();
        let policy = G::policy_name();
        Self {
            spec,
            policy,
            requests: BTreeMap::new(),
            nonces: BTreeMap::new(),
            ledger: AuditLog::open_named(policy, at),
            _gated: std::marker::PhantomData,
        }
    }

    /// Open a gate for `G` with a ledger that was restored after a restart.
    pub fn open_with_ledger(ledger: AuditLog) -> Result<Self, GateError> {
        // The ledger carries history, not authority: `requests` starts empty, so
        // nothing that happened before the restart is spendable after it.
        ledger.verify()?;
        Ok(Self {
            spec: G::spec(),
            policy: G::policy_name(),
            requests: BTreeMap::new(),
            nonces: BTreeMap::new(),
            ledger,
            _gated: std::marker::PhantomData,
        })
    }

    /// The policy in force.
    pub const fn policy(&self) -> crate::PolicyName {
        self.policy
    }

    /// The rules in force.
    pub const fn spec(&self) -> GateSpec {
        self.spec
    }

    /// The ledger so far.
    pub const fn ledger(&self) -> &AuditLog {
        &self.ledger
    }

    /// The ledger's head hash: the value to pin in evidence.
    pub fn ledger_head(&self) -> &str {
        self.ledger.head()
    }

    /// Raise a confirmation request.
    ///
    /// `requester` is the verified principal that wants to act. `nonce` makes the
    /// request distinguishable; `intent` is what it asks to authorise; `binding`
    /// names the value, its revision and its digest.
    ///
    /// In [`ReplayPolicy::SingleUsePerIntent`] a repeat of the same
    /// (requester, nonce, intent) is **idempotent**: it returns the existing
    /// handle rather than minting a second approval. In
    /// [`ReplayPolicy::SingleUse`] it is a refusal, because that mode has no
    /// intent-scoped dedupe to lean on.
    pub fn request(
        &mut self,
        requester: &VerifiedPrincipal,
        nonce: Nonce,
        intent: Intent,
        binding: ValueBinding,
        at: Tick,
    ) -> Result<RequestId, GateError> {
        if self.spec.ttl == 0 {
            return Err(PolicyNotGateable {
                policy: self.policy.as_str(),
            });
        }

        let key: NonceKey = (
            requester.id().as_str().to_owned(),
            nonce.as_str().to_owned(),
            intent.to_string(),
        );
        if let Some(existing) = self.nonces.get(&key).cloned() {
            match self.spec.replay {
                ReplayPolicy::SingleUsePerIntent => {
                    self.ledger.append(EventDraft {
                kind: EventKind::Requested,
                request: existing.clone(),
                actor: Some(requester.id().clone()),
                binding: Some(binding),
                intent: Some(intent),
                detail: "repeat request with the same nonce and intent; returning the existing handle".to_owned(),
                at,
            });
                    return Ok(existing);
                }
                ReplayPolicy::SingleUse => {
                    self.ledger.append(EventDraft {
                        kind: EventKind::Refused {
                            code: NonceReused {
                                nonce: nonce.clone(),
                                first: existing.clone(),
                            }
                            .code()
                            .to_owned(),
                        },
                        request: existing.clone(),
                        actor: Some(requester.id().clone()),
                        binding: Some(binding),
                        intent: Some(intent),
                        detail: "nonce reused for the same intent".to_owned(),
                        at,
                    });
                    return Err(NonceReused {
                        nonce,
                        first: existing,
                    });
                }
            }
        }

        let id = RequestId::mint(requester.id(), &nonce, binding, &intent);
        let record = RequestRecord {
            id: id.clone(),
            binding,
            intent: intent.clone(),
            requester: requester.id().clone(),
            requester_role: requester.role(),
            nonce: nonce.clone(),
            issued_at: at,
            decided_by: None,
            outcome: None,
            consumed: false,
            spec: self.spec,
        };
        self.nonces.insert(key, id.clone());
        self.requests.insert(id.clone(), record);
        self.ledger.append(EventDraft {
            kind: EventKind::Requested,
            request: id.clone(),
            actor: Some(requester.id().clone()),
            binding: Some(binding),
            intent: Some(intent),
            detail: format!(
                "raised by {} ({})",
                requester.id(),
                requester.role().label()
            ),
            at,
        });
        Ok(id)
    }

    /// Record a decision.
    ///
    /// Every check the DoD names happens here or is refused here:
    ///
    /// * **wrong-principal** — the decision is attributed to `approver.id()`, a
    ///   verified principal, never to a string the caller supplied;
    /// * **the wrong authority** — `approver`'s role must satisfy the policy's
    ///   declared authority (a machine cannot resolve a `HUMAN_GATE`);
    /// * **self-approval** — refused when the policy requires separation of
    ///   duties and the approver is the requester;
    /// * **pending** — a request that is not pending cannot be decided again;
    /// * **expired** — a decision that arrives after the deadline produces
    ///   [`DecisionOutcome::Expired`] and no approval; it is not a rejection,
    ///   because the requester did nothing wrong and the approver decided
    ///   nothing.
    pub fn approve(
        &mut self,
        approver: &VerifiedPrincipal,
        request: &RequestId,
        decision: Decision,
        note: Option<&str>,
        at: Tick,
    ) -> Result<DecisionOutcome, GateError> {
        let record = self
            .requests
            .get(request)
            .cloned()
            .ok_or_else(|| RequestNotKnown {
                request: request.clone(),
            })?;

        let state = record.state_at(at);
        match state {
            RequestState::Rejected => {
                return Err(ApprovalRejected {
                    request: request.clone(),
                });
            }
            RequestState::Consumed => {
                return Err(AlreadyConsumed {
                    request: request.clone(),
                    consumed_by: record
                        .decided_by
                        .clone()
                        .unwrap_or_else(|| record.requester.clone()),
                });
            }
            RequestState::Expired => {
                self.record_expiry(&record, at);
                return Ok(DecisionOutcome::Expired);
            }
            RequestState::Approved => {
                // A second approval of a decided request changes nothing; the
                // live approval stands and is reported as such.
                return Ok(DecisionOutcome::Approved);
            }
            RequestState::Pending => {}
        }

        // The authority the SDL declared, checked against the verified role.
        authority_admits(record.spec, approver.role())?;
        if record.spec.separation_of_duties && approver.id() == &record.requester {
            self.ledger.append(EventDraft {
                kind: EventKind::Refused {
                    code: SelfApprovalRefused {
                        principal: approver.id().clone(),
                    }
                    .code()
                    .to_owned(),
                },
                request: request.clone(),
                actor: Some(approver.id().clone()),
                binding: Some(record.binding),
                intent: Some(record.intent.clone()),
                detail: "approver is the requester and separation of duties applies".to_owned(),
                at,
            });
            return Err(SelfApprovalRefused {
                principal: approver.id().clone(),
            });
        }

        let detail = note.unwrap_or("").trim();
        match decision {
            Decision::Reject => {
                if detail.is_empty() {
                    return Err(MalformedField {
                        field: "note",
                        reason: "a rejection must state a reason",
                    });
                }
                self.mutate(request, |record| {
                    record.outcome = Some(DecisionOutcome::Rejected);
                    record.decided_by = Some(approver.id().clone());
                });
                self.ledger.append(EventDraft {
                    kind: EventKind::RejectionRecorded,
                    request: request.clone(),
                    actor: Some(approver.id().clone()),
                    binding: Some(record.binding),
                    intent: Some(record.intent.clone()),
                    detail: format!("refused by {}: {detail}", approver.id()),
                    at,
                });
                Ok(DecisionOutcome::Rejected)
            }
            Decision::Approve => {
                self.mutate(request, |record| {
                    record.outcome = Some(DecisionOutcome::Approved);
                    record.decided_by = Some(approver.id().clone());
                });
                self.ledger.append(EventDraft {
                    kind: EventKind::ApprovalRecorded,
                    request: request.clone(),
                    actor: Some(approver.id().clone()),
                    binding: Some(record.binding),
                    intent: Some(record.intent.clone()),
                    detail: if detail.is_empty() {
                        format!(
                            "approved by {} ({})",
                            approver.id(),
                            approver.role().label()
                        )
                    } else {
                        format!(
                            "approved by {} ({}): {detail}",
                            approver.id(),
                            approver.role().label()
                        )
                    },
                    at,
                });
                Ok(DecisionOutcome::Approved)
            }
        }
    }

    /// The state of a request as of `at`, applying expiry.
    pub fn state(&self, request: &RequestId, at: Tick) -> Result<RequestState, GateError> {
        let record = self.requests.get(request).ok_or_else(|| RequestNotKnown {
            request: request.clone(),
        })?;
        Ok(record.state_at(at))
    }

    /// A renderable view of a request, carrying no secret.
    pub fn view(&self, request: &RequestId, at: Tick) -> Result<RequestView, GateError> {
        let record = self.requests.get(request).ok_or_else(|| RequestNotKnown {
            request: request.clone(),
        })?;
        Ok(RequestView {
            id: record.id.clone(),
            state: record.state_at(at),
            binding: record.binding,
            intent: record.intent.clone(),
            requester: record.requester.clone(),
            requester_role: record.requester_role,
            nonce: record.nonce.clone(),
            decided_by: record.decided_by.clone(),
            outcome: record.outcome,
            issued_at: record.issued_at,
            expires_at: record.expires_at(),
            consumed: record.consumed,
        })
    }

    /// Spend an approval: the gate's one authorised consumption path.
    ///
    /// `consumer` is the verified principal that will read the value;
    /// `wanted` is the intent it is about to perform; `value` is the value it
    /// wants to read. On success the closure is called with a read-only `&str`
    /// and a [`Grant`], and the approval is marked consumed **before** the
    /// closure runs, so a panic inside the closure cannot leave a reusable
    /// approval behind.
    ///
    /// The closure's return value is returned unchanged, so the gate does not
    /// dictate what the caller does with the secret — only that it may.
    pub fn with_confirmed_value<T, F>(
        &mut self,
        consumer: &VerifiedPrincipal,
        request: &RequestId,
        wanted: &Intent,
        value: &Confidential,
        at: Tick,
        use_value: F,
    ) -> Result<T, GateError>
    where
        F: FnOnce(&str, Grant) -> T,
    {
        let record = self
            .requests
            .get(request)
            .cloned()
            .ok_or_else(|| RequestNotKnown {
                request: request.clone(),
            })?;

        let refuse = |gate: &mut Self, code: &str, detail: String| {
            gate.ledger.append(EventDraft {
                kind: EventKind::Refused {
                    code: code.to_owned(),
                },
                request: request.clone(),
                actor: Some(consumer.id().clone()),
                binding: Some(record.binding),
                intent: Some(record.intent.clone()),
                detail,
                at,
            });
        };

        match record.state_at(at) {
            RequestState::Pending => {
                let error = ApprovalPending {
                    request: request.clone(),
                };
                refuse(self, error.code(), error.to_string());
                return Err(error);
            }
            RequestState::Rejected => {
                let error = ApprovalRejected {
                    request: request.clone(),
                };
                refuse(self, error.code(), error.to_string());
                return Err(error);
            }
            RequestState::Consumed => {
                let error = AlreadyConsumed {
                    request: request.clone(),
                    consumed_by: record
                        .decided_by
                        .clone()
                        .unwrap_or_else(|| record.requester.clone()),
                };
                refuse(self, error.code(), error.to_string());
                return Err(error);
            }
            RequestState::Expired => {
                self.record_expiry(&record, at);
                let error = ApprovalExpired {
                    request: request.clone(),
                    expires_at: record.expires_at(),
                    now: at,
                };
                refuse(self, error.code(), error.to_string());
                return Err(error);
            }
            RequestState::Approved => {}
        }

        // The policy in force must still be the policy the request was raised
        // under: narrowing a gate mid-flight fails closed instead of applying
        // the new rules to an approval granted under the old ones.
        if record.spec != self.spec {
            let error = PolicyNotGateable {
                policy: self.policy.as_str(),
            };
            refuse(
                self,
                error.code(),
                "the policy changed while the request was live".to_owned(),
            );
            return Err(error);
        }

        if record.spec.consumer == ConsumerBinding::MustBeRequester
            && consumer.id() != &record.requester
        {
            let error = WrongPrincipal {
                expected: record.requester.clone(),
                presented: consumer.id().clone(),
            };
            refuse(self, error.code(), error.to_string());
            return Err(error);
        }

        // An approval of one action cannot authorise another.
        if let Err(error) = record.intent.require_covers(wanted) {
            refuse(self, error.code(), error.to_string());
            return Err(error);
        }

        // The live value must be the value that was approved.
        let live = value.binding();
        if live.value_id != record.binding.value_id || live.version != record.binding.version {
            let error = ValueNotBound {
                granted: record.binding.to_string(),
                presented: live.to_string(),
            };
            refuse(self, error.code(), error.to_string());
            return Err(error);
        }
        if record.spec.bind_digest && live.digest != record.binding.digest {
            let error = DigestChanged {
                granted: record.binding.digest.hex(),
                presented: live.digest.hex(),
            };
            refuse(self, error.code(), error.to_string());
            return Err(error);
        }

        let grant = Grant {
            request: request.clone(),
            binding: record.binding,
            grantee: consumer.id().clone(),
            intent: record.intent.clone(),
            expires_at: record.expires_at(),
            spec: record.spec,
        };

        // Spend it before the closure runs: if the closure panics, the approval
        // is already gone.
        self.mutate(request, |record| record.consumed = true);
        self.ledger.append(EventDraft {
            kind: EventKind::Consumed,
            request: request.clone(),
            actor: Some(consumer.id().clone()),
            binding: Some(record.binding),
            intent: Some(record.intent.clone()),
            detail: format!("spent by {} for {}", consumer.id(), wanted),
            at,
        });

        Ok(use_value(value.raw_bytes(), grant))
    }

    /// Fold the derived expiry into the ledger, once.
    fn record_expiry(&mut self, record: &RequestRecord, at: Tick) {
        if record.outcome == Some(DecisionOutcome::Expired) {
            return;
        }
        self.mutate(&record.id, |record| {
            record.outcome = Some(DecisionOutcome::Expired)
        });
        self.ledger.append(EventDraft {
            kind: EventKind::Expired,
            request: record.id.clone(),
            actor: None,
            binding: Some(record.binding),
            intent: Some(record.intent.clone()),
            detail: format!("expired at {}", record.expires_at()),
            at,
        });
    }

    fn mutate(&mut self, request: &RequestId, edit: impl FnOnce(&mut RequestRecord)) {
        if let Some(record) = self.requests.get_mut(request) {
            edit(record);
        }
    }

    /// Every request the gate knows, as of `at`.
    pub fn requests(&self, at: Tick) -> impl Iterator<Item = RequestView> + '_ {
        self.requests.values().map(move |record| RequestView {
            id: record.id.clone(),
            state: record.state_at(at),
            binding: record.binding,
            intent: record.intent.clone(),
            requester: record.requester.clone(),
            requester_role: record.requester_role,
            nonce: record.nonce.clone(),
            decided_by: record.decided_by.clone(),
            outcome: record.outcome,
            issued_at: record.issued_at,
            expires_at: record.expires_at(),
            consumed: record.consumed,
        })
    }

    /// How many requests the gate holds.
    pub fn request_count(&self) -> usize {
        self.requests.len()
    }
}
