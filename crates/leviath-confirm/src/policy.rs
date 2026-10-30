//! Type-level policy: *which* authority must approve, and under what rules.
//!
//! GQL-01's R1 derives `dataType` from the implementing type and R6 says the
//! directive *declares* the gate while authority is "the authenticated principal
//! plus the exact approved intent". This module is where the declaration stops
//! being a comment: [`Policy`] implementations carry the rules as constants, and
//! [`GatedValue`] pairs a [`Confidential`](crate::Confidential) with exactly one
//! such implementation. A value's policy therefore cannot be chosen per call
//! site, because there is no argument in which to choose it — getting it wrong
//! is a type error, not a review comment.

use std::fmt;
use std::marker::PhantomData;

use crate::gate::GateError;
use crate::{Confidential, Tick, ValueBinding};

/// Which authority the SDL's `@user_confirmation(by: …)` names.
///
/// The variants are the `ConfirmationAuthority` enum from GQL-01's
/// `schema-workflow.graphql`, spelled the same way so a reader can go from the
/// SDL to the enforcing code without a translation table.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize,
)]
pub enum Authority {
    /// `AUTHENTICATED_PRINCIPAL` — the verified principal on the approving request.
    AuthenticatedPrincipal,
    /// `HUMAN_GATE` — a human review gate (Desk/UI) resolved the request.
    HumanGate,
    /// `STEP_RUNNER` — the step runner unwraps the value for one step execution.
    StepRunner,
}

impl Authority {
    /// The SDL spelling, for error messages and evidence.
    pub const fn sdl_name(self) -> &'static str {
        match self {
            Self::AuthenticatedPrincipal => "AUTHENTICATED_PRINCIPAL",
            Self::HumanGate => "HUMAN_GATE",
            Self::StepRunner => "STEP_RUNNER",
        }
    }
}

impl fmt::Display for Authority {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.sdl_name())
    }
}

/// How far an approval reaches.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub enum ConsumerBinding {
    /// The consumer must be the same principal that raised the request, so an
    /// approval raised for one step cannot be consumed by another.
    /// Approvals of one action cannot authorise another action's reader.
    #[default]
    MustBeRequester,
    /// Any verified principal may consume, subject to the other checks.
    AnyAuthenticated,
}

/// What a successful consumption does to the approval.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub enum ReplayPolicy {
    /// One request, one approval, one successful consumption. Any further
    /// attempt is a replay and is refused; a fresh approval is required.
    #[default]
    SingleUse,
    /// Like [`ReplayPolicy::SingleUse`], and additionally a *repeated request*
    /// with the same nonce and the same intent is idempotent: it returns the
    /// existing request instead of minting a second approval. This is the mode a
    /// retry loop should declare, because it makes a client retry after a
    /// dropped response harmless while a retry after a success is still refused.
    SingleUsePerIntent,
}

/// The rules an implementation declares for its own sensitive values.
///
/// `Copy` and comparable on purpose: the gate records the spec a request was
/// raised under and re-checks it at consumption, so narrowing a policy while a
/// request is live fails closed rather than silently applying the new rules to
/// an approval granted under the old ones.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct GateSpec {
    /// Which authority may approve.
    pub authority: Authority,
    /// Whether the approver must be a different principal from the consumer.
    pub separation_of_duties: bool,
    /// How the consumer is bound to the requester.
    pub consumer: ConsumerBinding,
    /// How long an approval lives, in seconds from the moment it is issued.
    pub ttl: Tick,
    /// Whether the approval binds the value's content digest as well as its
    /// identity and version. True is the strict setting: an approval taken
    /// against one revision cannot be spent on different bytes that were pushed
    /// into the same value id without a version bump.
    pub bind_digest: bool,
    /// What a successful consumption does.
    pub replay: ReplayPolicy,
}

impl GateSpec {
    /// The strict default: a human gate, separation of duties, digest-bound,
    /// single use, 15-minute life.
    pub const fn human_gated() -> Self {
        Self {
            authority: Authority::HumanGate,
            separation_of_duties: true,
            consumer: ConsumerBinding::MustBeRequester,
            ttl: 900,
            bind_digest: true,
            replay: ReplayPolicy::SingleUsePerIntent,
        }
    }

    /// The step runner's own unwrap during one step execution, where the runner
    /// is both the party asserting the need and the party reading: separation of
    /// duties cannot apply because there is only one principal, so the binding
    /// that replaces it is the requester assertion plus the digest and the short
    /// life.
    pub const fn runner_unwrap() -> Self {
        Self {
            authority: Authority::StepRunner,
            separation_of_duties: false,
            consumer: ConsumerBinding::MustBeRequester,
            ttl: 60,
            bind_digest: true,
            replay: ReplayPolicy::SingleUse,
        }
    }

    /// A verified non-human principal approves, with separation of duties.
    pub const fn principal_approved() -> Self {
        Self {
            authority: Authority::AuthenticatedPrincipal,
            separation_of_duties: true,
            consumer: ConsumerBinding::MustBeRequester,
            ttl: 300,
            bind_digest: true,
            replay: ReplayPolicy::SingleUse,
        }
    }
}

/// The name of a policy, as it appears in a ledger, a log line and an error.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct PolicyName(&'static str);

impl PolicyName {
    /// Name a policy. Names are `kebab-case` by convention.
    pub const fn new(name: &'static str) -> Self {
        Self(name)
    }

    /// The name as a string.
    pub const fn as_str(self) -> &'static str {
        self.0
    }

    /// Recognise one of the compiled policies by name.
    ///
    /// Used when a ledger is loaded from disk: the name is data, and only a name
    /// this build knows how to enforce resolves to a policy. An unfamiliar name
    /// is a refusal, not a default — a ledger written by a build with a policy
    /// this one cannot enforce must not be silently reinterpreted.
    pub fn from_known(name: &str) -> Option<Self> {
        KNOWN_POLICIES.iter().copied().find(|known| known.0 == name)
    }
}

/// Every policy this build can enforce.
pub const KNOWN_POLICIES: &[PolicyName] = &[
    PolicyName("human-gate"),
    PolicyName("authenticated-principal"),
    PolicyName("step-runner"),
    PolicyName("ungated"),
];

impl fmt::Display for PolicyName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}

/// A declared policy: a name plus the rules, as constants of a type.
///
/// Implement this on a marker type per sensitive value class. The gate never
/// takes a spec from a caller; it reads it from the type, which is what "type-
/// level policy derived from the implementation" means in practice.
pub trait Policy {
    /// Stable name of this policy, recorded in every ledger entry it produces.
    const NAME: PolicyName;
    /// The rules.
    const SPEC: GateSpec;
}

/// An opaque sensitive string carrying the policy of `P`.
pub struct GatedValue<P: Policy> {
    value: Confidential,
    _policy: PhantomData<fn() -> P>,
}

impl<P: Policy> GatedValue<P> {
    /// Wrap `secret` as a value governed by `P`.
    pub fn new(id: crate::ValueId, version: u32, secret: impl Into<String>) -> Self {
        Self {
            value: Confidential::new(id, version, secret),
            _policy: PhantomData,
        }
    }

    /// The value's identity, version and digest — never its bytes.
    pub const fn binding(&self) -> ValueBinding {
        self.value.binding()
    }

    /// The underlying opaque value, for handing to code that must not read it.
    pub const fn confidential(&self) -> &Confidential {
        &self.value
    }

    /// A stable description for logs.
    pub fn redacted(&self) -> String {
        format!(
            "{}[{}] {}",
            P::NAME,
            P::SPEC.authority.sdl_name(),
            self.value.redacted()
        )
    }
}

impl<P: Policy> fmt::Debug for GatedValue<P> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GatedValue")
            .field("policy", &P::NAME.as_str())
            .field("authority", &P::SPEC.authority.sdl_name())
            .field("value", &self.value)
            .finish()
    }
}

impl<P: Policy> fmt::Display for GatedValue<P> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.redacted())
    }
}

/// A type whose sensitive value is gated.
///
/// Implemented for [`GatedValue`] and for thin newtypes a vertical already has
/// (an OAuth refresh token, an API key, a payment credential), so an existing
/// domain type can be gated without changing its shape:
///
/// ```ignore
/// pub struct RefreshToken(GatedValue<HumanGateApproval>);
/// ```
pub trait Gated {
    /// The declared policy, read from the type rather than from an argument.
    fn spec() -> GateSpec;
    /// The policy's name.
    fn policy_name() -> PolicyName;
    /// The value's identity, version and digest.
    fn binding(&self) -> ValueBinding;
    /// The opaque value itself.
    fn confidential(&self) -> &Confidential;
}

impl<P: Policy> Gated for GatedValue<P> {
    fn spec() -> GateSpec {
        P::SPEC
    }

    fn policy_name() -> PolicyName {
        P::NAME
    }

    fn binding(&self) -> ValueBinding {
        self.value.binding()
    }

    fn confidential(&self) -> &Confidential {
        &self.value
    }
}

/// A `Confidential` that refuses to be read for anything but its own identity.
///
/// Used where the honest policy is "this crate does not gate it yet": the value
/// stays opaque and the gate refuses every approval with
/// [`GateError::PolicyNotGateable`](crate::GateError::PolicyNotGateable), so a
/// vertical cannot accidentally inherit a working gate it did not design.
pub struct Ungated;

impl Policy for Ungated {
    const NAME: PolicyName = PolicyName::new("ungated");
    const SPEC: GateSpec = GateSpec {
        authority: Authority::HumanGate,
        separation_of_duties: true,
        consumer: ConsumerBinding::MustBeRequester,
        // A zero ttl means every approval is born expired; with the marker type
        // below it is belt and braces on a state that cannot be reached.
        ttl: 0,
        bind_digest: true,
        replay: ReplayPolicy::SingleUse,
    };
}

/// The `HUMAN_GATE` policy: a human review gate resolved the request.
///
/// The trail a person leaves is the approval, so the request must be raised by
/// one principal and consumed by it, and the approval must be given by a
/// different one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HumanGateApproval;

impl Policy for HumanGateApproval {
    const NAME: PolicyName = PolicyName::new("human-gate");
    const SPEC: GateSpec = GateSpec::human_gated();
}

/// The `AUTHENTICATED_PRINCIPAL` policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PrincipalApproval;

impl Policy for PrincipalApproval {
    const NAME: PolicyName = PolicyName::new("authenticated-principal");
    const SPEC: GateSpec = GateSpec::principal_approved();
}

/// The `STEP_RUNNER` policy: the runner unwraps for one step execution.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RunnerApproval;

impl Policy for RunnerApproval {
    const NAME: PolicyName = PolicyName::new("step-runner");
    const SPEC: GateSpec = GateSpec::runner_unwrap();
}

/// Does `spec` authorise a principal in `role` to be the approver?
///
/// A machine principal may not resolve a human gate. That check lives here, on
/// the spec, so it applies to every value using the policy rather than to the
/// call sites that remember it.
pub fn authority_admits(spec: GateSpec, role: crate::PrincipalRole) -> Result<(), GateError> {
    use crate::PrincipalRole as Role;

    let admitted = match spec.authority {
        Authority::HumanGate => matches!(role, Role::Human),
        Authority::AuthenticatedPrincipal => {
            matches!(role, Role::Human | Role::Agent | Role::Service)
        }
        Authority::StepRunner => matches!(role, Role::Agent | Role::Service),
    };
    if admitted {
        Ok(())
    } else {
        Err(GateError::WrongAuthority {
            required: spec.authority.sdl_name(),
            approver_role: role.label(),
        })
    }
}
