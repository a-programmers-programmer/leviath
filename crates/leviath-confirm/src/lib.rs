//! Confirmation values and the confirm gate (work order GQL-06).
//!
//! # What this crate is
//!
//! The workflow model reconciled by GQL-01 declares sensitive values as the
//! opaque scalar `Confidential` and declares *that* a value is gated with
//! `@user_confirmation(by: …)`. A declaration is not a gate. This crate is the
//! gate: it turns a declared-policy sensitive string into an opaque runtime
//! value that **cannot be read by ordinary code at all**, and it makes reading
//! it require a recorded approval bound to the exact intent the approver saw.
//!
//! # The three properties, and how each one is enforced rather than promised
//!
//! 1. **Opacity is a type property, not a convention.** [`Confidential`] has no
//!    public byte accessor and no public accessor that takes anything a caller
//!    can build. Reading the bytes needs a [`Grant`], whose constructor is
//!    private to this crate; the only way to obtain one is
//!    [`Gate::with_confirmed_value`], which is also the only place the approval
//!    checks run. Serialisation is implemented and *redacts*; deserialisation is
//!    refused outright, so a generated client cannot round-trip a secret.
//!
//! 2. **Authority is the authenticated approver plus the exact approved intent,
//!    not a directive and not a string in the request.** An approval is bound to
//!    the approver's verified identity, the action, the target, the scope, the
//!    value's version and digest, the expiry, and the replay policy (one
//!    request id, one nonce, one successful consumption). [`VerifiedPrincipal`]
//!    has a private constructor: an unverified principal cannot even be passed
//!    to [`Gate::approve`], so "the caller said it was a human" is not
//!    expressible.
//!
//! 3. **The policy is derived from the implementing type.** Which authority must
//!    approve, how long the approval lives, whether the value is bound by digest
//!    and whether it is single-use are constants of the [Gated] *implementation*
//!    (`<RefreshToken as Gated>::SPEC`), exactly as GQL-01's R1 derives
//!    `dataType` from the implementing type. There is no per-call policy
//!    argument to get wrong.
//!
//! # The state machine
//!
//! ```text
//!            request()                 approve(Approve)                with_confirmed_value()
//!   (none) ─────────────▶ Pending ─────────────────────▶ Approved ────────────────────────▶ Consumed
//!                            │                              │
//!                            │ approve(Reject)              │ now >= expiresAt  ──▶ Expired
//!                            └──────────────▶ Rejected      │
//!                                                           │ now >= expiresAt  ──▶ Expired
//! ```
//!
//! Expiry is *derived*, never stored: `expiresAt = issuedAt + ttl`, so a record
//! cannot be edited to extend its own life without changing the integrity digest
//! the ledger is checked against.
//!
//! # What this crate deliberately does not do
//!
//! - It never stores the secret. The ledger holds the value's **digest** and
//!   **version** only, so the at-rest artefact cannot leak a secret, and a
//!   restart cannot resurrect one. The runtime keeps holding the value; the gate
//!   decides whether the runtime may read it.
//! - It does not authenticate the transport that delivered the approval. It
//!   requires a [`VerifiedPrincipal`], which the ingress is responsible for
//!   producing (GQL-12's `admin_principal.Principal` is that ingress on the
//!   Parlour side). Wiring the Desk/UI path is GQL-17.
//! - It is at-most-once, not idempotent: a retry after a *successful*
//!   consumption is a replay and is refused. A retry that failed for a
//!   correctable reason (wrong value, wrong intent) does not burn the approval.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod audit;
pub mod gate;
pub mod intent;
pub mod policy;
pub mod secret;

pub use audit::{AuditEvent, AuditLog, EventKind};
pub use gate::{
    Decision, DecisionOutcome, Gate, GateError, Grant, RequestId, RequestState, RequestView,
};
pub use intent::{
    ActionId, Intent, Nonce, PrincipalId, PrincipalRole, Scope, TargetRef, VerifiedPrincipal,
};
pub use policy::{
    Authority, ConsumerBinding, GateSpec, Gated, GatedValue, HumanGateApproval, Policy, PolicyName,
    PrincipalApproval, ReplayPolicy, RunnerApproval, Ungated,
};
pub use secret::{Confidential, Digest, ValueBinding, ValueId};

/// The versioned name of the contract this crate implements.
///
/// GQL-01 pinned `workflow-model v1`; this is the confirmation half of it, and
/// it is recorded verbatim in every ledger snapshot so a restored ledger states
/// which contract wrote it.
pub const CONFIRM_CONTRACT: &str = "confirmation-values/v1";

/// The clock reading used throughout: seconds since the Unix epoch.
///
/// An explicit `now: Tick` is a parameter of every gate operation rather than a
/// hidden `SystemTime::now()` call, so expiry, restart and retry are decided by
/// data the tests control.
pub type Tick = u64;

/// The process clock, for callers that have no better source.
///
/// A gate operation never calls this itself; it exists so that a caller can pass
/// `leviath_confirm::system_now()` without importing `std::time` in the happy
/// path. A pre-1970 system clock reads as `0`, which expires rather than grants.
pub fn system_now() -> Tick {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}
