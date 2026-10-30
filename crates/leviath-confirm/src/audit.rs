//! The append-only, hash-chained record of what the gate decided.
//!
//! Every transition and every refusal appends one event. The chain is what makes
//! the ledger evidence rather than commentary: a removed, reordered or edited
//! event breaks [`AuditLog::verify`], so "the replay attempt was refused" is a
//! claim a reader can check instead of take on trust.
//!
//! The ledger is safe to keep beside a value's store because it never contains
//! the value: identities, versions, digests, intents, labels and reasons only.
//! That is also what makes it loadable across a restart — and what keeps the
//! restart honest, because a restored ledger restores *history* and not
//! *authority*: the approvals it describes died with the process.

use std::fmt;

use sha2::{Digest as _, Sha256};

use crate::gate::{GateError, RequestId};
use crate::intent::{Intent, PrincipalId};
use crate::policy::{Gated, Policy, PolicyName};
use crate::{CONFIRM_CONTRACT, ValueBinding};

/// Domain-separation prefix for the ledger chain.
const LEDGER_DOMAIN: &[u8] = b"leviath-confirm/ledger/v1\0";

/// What an audit event records.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum EventKind {
    /// The ledger was created for a policy.
    LedgerOpened,
    /// A confirmation request was raised.
    Requested,
    /// An approval was recorded.
    ApprovalRecorded,
    /// The request was refused by the approver.
    RejectionRecorded,
    /// The request ran out of time. Recorded when the gate observes the expiry,
    /// because the gate is the only thing that can.
    Expired,
    /// The approval was spent on a value read.
    Consumed,
    /// The gate refused an operation. `code` is the machine-readable reason, so a
    /// replay attempt is a distinct ledger entry rather than an absence of one.
    Refused {
        /// The [`GateError::code`](crate::GateError::code) of the refusal.
        code: String,
    },
}

impl EventKind {
    /// A stable label for logs and for grouping in evidence.
    pub const fn label(&self) -> &'static str {
        match self {
            Self::LedgerOpened => "ledger_opened",
            Self::Requested => "requested",
            Self::ApprovalRecorded => "approval_recorded",
            Self::RejectionRecorded => "rejection_recorded",
            Self::Expired => "expired",
            Self::Consumed => "consumed",
            Self::Refused { .. } => "refused",
        }
    }
}

/// One line of the ledger.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct AuditEvent {
    /// Monotonic sequence number, starting at 0.
    pub seq: u64,
    /// When it happened.
    pub at: crate::Tick,
    /// What happened.
    pub kind: EventKind,
    /// Which request it happened to. [`RequestId::ledger`] for ledger-level
    /// events that belong to no confirmation request.
    pub request: RequestId,
    /// The principal that acted, where there is one.
    pub actor: Option<PrincipalId>,
    /// The policy in force, as its stable name.
    ///
    /// Stored as a string rather than as [`PolicyName`] so that a restored
    /// ledger is self-describing: the name is data read back from disk, and
    /// [`PolicyName`] is a `'static` label that belongs to the compiled policy
    /// a caller wrote.
    pub policy: String,
    /// Which value was in play, and at which revision and digest. Absent on
    /// ledger-level events.
    pub binding: Option<ValueBinding>,
    /// The intent the request was bound to. Absent on ledger-level events.
    pub intent: Option<Intent>,
    /// A short, non-secret explanation.
    pub detail: String,
    /// The hash of the preceding event, or [`GENESIS`] for the first.
    pub prev_hash: String,
    /// The hash of this event's content under the previous event.
    pub hash: String,
}

impl AuditEvent {
    /// Recompute this event's hash from its content and `prev_hash`.
    ///
    /// Every field that carries meaning is folded in, including the refusal code
    /// and the contract name, so neither a changed reason nor a ledger presented
    /// under a different contract can verify.
    fn compute_hash(&self) -> String {
        let mut hasher = Sha256::new();
        hasher.update(LEDGER_DOMAIN);
        hasher.update(self.prev_hash.as_bytes());
        hasher.update(self.seq.to_be_bytes());
        hasher.update(self.at.to_be_bytes());
        hasher.update(self.kind.label().as_bytes());
        if let EventKind::Refused { code } = &self.kind {
            hasher.update(b"\0refused\0");
            hasher.update(code.as_bytes());
        }
        hasher.update(self.request.as_str().as_bytes());
        hasher.update(
            self.actor
                .as_ref()
                .map(PrincipalId::as_str)
                .unwrap_or("-")
                .as_bytes(),
        );
        hasher.update(self.policy.as_bytes());
        hasher.update(
            self.binding
                .map(|binding| binding.to_string())
                .unwrap_or_else(|| "-".to_owned())
                .as_bytes(),
        );
        hasher.update(
            self.intent
                .as_ref()
                .map(ToString::to_string)
                .unwrap_or_else(|| "-".to_owned())
                .as_bytes(),
        );
        hasher.update(self.detail.as_bytes());
        hasher.update(CONFIRM_CONTRACT.as_bytes());
        hex32(&hasher.finalize())
    }
}

fn hex32(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(char::from_digit((byte >> 4) as u32, 16).unwrap_or('0'));
        out.push(char::from_digit((byte & 0x0f) as u32, 16).unwrap_or('0'));
    }
    out
}

/// The genesis link: the first event's chain has no predecessor.
pub const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

/// One event's worth of content, before it is placed in the chain.
///
/// The named field list [`AuditLog::append`] takes. Every field is `pub` because
/// the gate constructs drafts and lives in a sibling module, but the type is not
/// re-exported from the crate root, so it is scaffolding rather than API.
#[derive(Debug, Clone)]
pub(crate) struct EventDraft {
    /// What happened.
    pub(crate) kind: EventKind,
    /// Which request it happened to.
    pub(crate) request: RequestId,
    /// The principal that acted, where there is one.
    pub(crate) actor: Option<PrincipalId>,
    /// Which value was in play, and at which revision and digest.
    pub(crate) binding: Option<ValueBinding>,
    /// The intent the request was bound to.
    pub(crate) intent: Option<Intent>,
    /// A short, non-secret explanation.
    pub(crate) detail: String,
    /// When it happened.
    pub(crate) at: crate::Tick,
}

/// An append-only log of gate decisions.
#[derive(Debug, Clone)]
pub struct AuditLog {
    policy: PolicyName,
    events: Vec<AuditEvent>,
}

impl AuditLog {
    /// Open a ledger for a policy.
    pub fn open<P: Policy>(at: crate::Tick) -> Self {
        Self::open_named(P::NAME, at)
    }

    /// Open a ledger for a policy name, for a host that only knows the name.
    ///
    /// The opening entry binds the ledger to the contract and the policy, so a
    /// ledger presented as evidence says which contract wrote it.
    pub fn open_named(policy: PolicyName, at: crate::Tick) -> Self {
        let mut log = Self {
            policy,
            events: Vec::new(),
        };
        log.append(EventDraft {
            kind: EventKind::LedgerOpened,
            request: RequestId::ledger(),
            actor: None,
            binding: None,
            intent: None,
            detail: format!("contract={CONFIRM_CONTRACT} policy={policy}"),
            at,
        });
        log
    }

    /// The policy this ledger belongs to.
    pub const fn policy(&self) -> PolicyName {
        self.policy
    }

    /// Every event, in order.
    pub fn events(&self) -> &[AuditEvent] {
        &self.events
    }

    /// The hash of the last event, for pinning the ledger's head in evidence.
    pub fn head(&self) -> &str {
        self.events
            .last()
            .map(|event| event.hash.as_str())
            .unwrap_or(GENESIS)
    }

    /// How many events the ledger holds.
    pub fn len(&self) -> usize {
        self.events.len()
    }

    /// Whether the ledger is empty. A ledger always holds at least its opening
    /// entry, so this is true only for a value built by `restore` from nothing.
    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    /// Append one event.
    ///
    /// Takes the event by name rather than as seven positional arguments. The
    /// gate appends from nine places and several of them shuffle a `binding`, an
    /// `intent` and an `actor` past each other; at that arity a swap is a silent
    /// one. Named fields make each call site say which is which, and the
    /// compiler makes a missing or misspelled one a build error rather than a
    /// wrong ledger entry.
    pub(crate) fn append(&mut self, draft: EventDraft) {
        let EventDraft {
            kind,
            request,
            actor,
            binding,
            intent,
            detail,
            at,
        } = draft;
        let seq = self.events.len() as u64;
        let prev_hash = self
            .events
            .last()
            .map(|event| event.hash.clone())
            .unwrap_or_else(|| GENESIS.to_owned());
        let mut event = AuditEvent {
            seq,
            at,
            kind,
            request,
            actor,
            policy: self.policy.as_str().to_owned(),
            binding,
            intent,
            detail,
            prev_hash,
            hash: String::new(),
        };
        event.hash = event.compute_hash();
        self.events.push(event);
    }

    /// Check the chain end to end.
    ///
    /// A ledger that fails this has been edited: an event removed, reordered,
    /// rewritten, or appended by a process that did not hold the chain.
    pub fn verify(&self) -> Result<(), GateError> {
        let mut previous = GENESIS.to_owned();
        for (index, event) in self.events.iter().enumerate() {
            if event.seq != index as u64 {
                return Err(GateError::LedgerBroken {
                    seq: event.seq,
                    reason: format!("expected sequence {index}"),
                });
            }
            if event.prev_hash != previous {
                return Err(GateError::LedgerBroken {
                    seq: event.seq,
                    reason: "prev_hash does not match the preceding event".to_owned(),
                });
            }
            if event.hash != event.compute_hash() {
                return Err(GateError::LedgerBroken {
                    seq: event.seq,
                    reason: "content hash does not match the event".to_owned(),
                });
            }
            previous = event.hash.clone();
        }
        Ok(())
    }

    /// Rebuild a ledger from stored events, verifying the chain as it loads.
    ///
    /// This is the restart path for the ledger alone: the events survive, the
    /// in-memory approvals do not, so a restored ledger records history without
    /// restoring authority.
    pub fn restore(events: Vec<AuditEvent>) -> Result<Self, GateError> {
        let policy_name = events
            .first()
            .map(|event| event.policy.clone())
            .ok_or(GateError::LedgerEmpty)?;
        let policy = PolicyName::from_known(&policy_name).ok_or(GateError::LedgerBroken {
            seq: 0,
            reason: format!("unknown policy name `{policy_name}`"),
        })?;
        let log = Self { policy, events };
        log.verify()?;
        Ok(log)
    }

    /// Every event that names `request`.
    pub fn for_request(&self, request: &RequestId) -> impl Iterator<Item = &AuditEvent> {
        let request = request.clone();
        self.events
            .iter()
            .filter(move |event| event.request == request)
    }

    /// One line per event: the form quoted in evidence.
    pub fn lines(&self) -> impl Iterator<Item = String> + '_ {
        self.events.iter().map(|event| {
            format!(
                "seq={} at={} kind={} request={} actor={} policy={} binding={} intent={} detail={}",
                event.seq,
                event.at,
                event.kind.label(),
                event.request,
                event.actor.as_ref().map(PrincipalId::as_str).unwrap_or("-"),
                event.policy,
                event
                    .binding
                    .map(|binding| binding.to_string())
                    .unwrap_or_else(|| "-".to_owned()),
                event
                    .intent
                    .as_ref()
                    .map(ToString::to_string)
                    .unwrap_or_else(|| "-".to_owned()),
                event.detail,
            )
        })
    }
}

impl fmt::Display for AuditLog {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for line in self.lines() {
            writeln!(f, "{line}")?;
        }
        Ok(())
    }
}

/// Verifies a whole gate's history in one call, and returns the head hash.
///
/// A free function so a reviewer can check a ledger that arrived as data,
/// without constructing a gate.
pub fn verify_ledger(log: &AuditLog) -> Result<String, GateError> {
    log.verify()?;
    Ok(log.head().to_owned())
}

/// The policy name of a gated type, for hosts that carry the type only as a
/// type parameter.
pub fn policy_name_of<G: Gated>() -> PolicyName {
    G::policy_name()
}
