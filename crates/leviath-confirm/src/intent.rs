//! The verified approver and the exact intent an approval is bound to.
//!
//! Two of the DoD's failure modes live here:
//!
//! * **wrong-principal** — an approval is bound to the *verified* identity that
//!   gave it. [`VerifiedPrincipal`] has a private field and one constructor, and
//!   that constructor is named for the boundary it must be called at: the place
//!   where the ingress has authenticated the transport and checked the identity
//!   claim. A caller that has not done that work has nothing to pass to
//!   [`Gate::approve`](crate::Gate::approve).
//! * **approval of one action cannot authorise another** — an approval is bound
//!   to an [`Intent`] (action, target, scope) and consumption must name an intent
//!   the approval *covers*, meaning equal action, equal target, and a scope the
//!   approval's scope contains. There is no "wildcard" arm.

use std::collections::BTreeSet;
use std::fmt;

use crate::gate::GateError;

/// The label of an authenticated principal.
///
/// A label, not a credential: nothing here is trusted because of its contents,
/// only because the ingress says the principal holding it was authenticated.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct PrincipalId(String);

impl PrincipalId {
    /// Label a principal. Empty or over-long labels are refused so that a
    /// missing identity cannot be represented as `""`.
    pub fn new(label: impl Into<String>) -> Result<Self, GateError> {
        let label = label.into();
        if label.trim().is_empty() {
            return Err(GateError::MalformedField {
                field: "principalId",
                reason: "must not be blank",
            });
        }
        if label.len() > 256 {
            return Err(GateError::MalformedField {
                field: "principalId",
                reason: "longer than 256 bytes",
            });
        }
        Ok(Self(label))
    }

    /// The label.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for PrincipalId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// What kind of thing a principal is.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize,
)]
pub enum PrincipalRole {
    /// A person. The only role that can resolve a `HUMAN_GATE`.
    Human,
    /// An agent process acting under its own identity.
    Agent,
    /// A service.
    Service,
}

impl PrincipalRole {
    /// The role's label, for errors and evidence.
    pub const fn label(self) -> &'static str {
        match self {
            Self::Human => "human",
            Self::Agent => "agent",
            Self::Service => "service",
        }
    }
}

/// A principal whose identity the ingress verified.
///
/// The field is private and the constructor is explicit about what it asserts.
/// This is a *boundary* type, not a proof: it makes "I forgot to authenticate"
/// visible as a missing call at one place instead of invisible at every call
/// site. The assertion it carries is stated in the constructor's contract, and
/// the only in-tree implementation of that contract is the Parlour ingress from
/// GQL-12 (`admin_principal.Principal`), which derives the principal from the
/// session rather than from a header the caller supplies.
pub struct VerifiedPrincipal {
    id: PrincipalId,
    role: PrincipalRole,
}

impl VerifiedPrincipal {
    /// The ingress boundary.
    ///
    /// Constructing this asserts, on the caller's authority, that:
    ///
    /// 1. the request's transport was authenticated (a session was presented and
    ///    validated, or a service credential was checked by the peer), and
    /// 2. `id` is the authenticated identity that transport belongs to — not a
    ///    value copied out of a request body, query parameter, header, JWT
    ///    payload or token substring, and
    /// 3. `role` describes that identity, not the identity the caller wishes it
    ///    had.
    ///
    /// A caller that has not done those three things must not call this. The
    /// type exists so that the decision is made once, at the boundary, and every
    /// later check can be structural.
    pub fn from_verified_ingress(id: PrincipalId, role: PrincipalRole) -> Self {
        Self { id, role }
    }

    /// The verified identity.
    pub const fn id(&self) -> &PrincipalId {
        &self.id
    }

    /// The verified identity's role.
    pub const fn role(&self) -> PrincipalRole {
        self.role
    }
}

impl fmt::Debug for VerifiedPrincipal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("VerifiedPrincipal")
            .field("id", &self.id.as_str())
            .field("role", &self.role.label())
            .finish()
    }
}

impl fmt::Display for VerifiedPrincipal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({})", self.id, self.role.label())
    }
}

/// What an action is called. `namespace.verb` by convention (`oauth.refresh`,
/// `provider.spend`, `desk.publish`).
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct ActionId(String);

impl ActionId {
    /// Name an action.
    pub fn new(name: impl Into<String>) -> Result<Self, GateError> {
        let name = name.into();
        if name.trim().is_empty() || name.len() > 128 {
            return Err(GateError::MalformedField {
                field: "action",
                reason: "must be 1..=128 bytes and not blank",
            });
        }
        Ok(Self(name))
    }

    /// The name.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ActionId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// What the action acts on.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct TargetRef {
    /// The target's kind, e.g. `provider-credential`, `funding-source`.
    pub kind: String,
    /// The target's identity within that kind.
    pub id: String,
}

impl TargetRef {
    /// Point at a target.
    pub fn new(kind: impl Into<String>, id: impl Into<String>) -> Result<Self, GateError> {
        let kind = kind.into();
        let id = id.into();
        if kind.trim().is_empty() || id.trim().is_empty() || kind.len() > 128 || id.len() > 256 {
            return Err(GateError::MalformedField {
                field: "target",
                reason: "kind and id must be non-blank and within their length limits",
            });
        }
        Ok(Self { kind, id })
    }
}

impl fmt::Display for TargetRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.kind, self.id)
    }
}

/// The set of effects an approval covers.
///
/// A set, not a pattern: an approval either contains an effect or it does not.
/// There is deliberately no `*` entry, because a wildcard is how a narrow
/// approval becomes a broad one.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Scope {
    entries: BTreeSet<String>,
}

impl Scope {
    /// The empty scope: covers no effect at all.
    pub fn empty() -> Self {
        Self::default()
    }

    /// A scope from named effects.
    pub fn of<I, S>(entries: I) -> Result<Self, GateError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut set = BTreeSet::new();
        for entry in entries {
            let entry = entry.into();
            if entry.trim().is_empty() || entry.len() > 128 {
                return Err(GateError::MalformedField {
                    field: "scope",
                    reason: "entries must be 1..=128 bytes and not blank",
                });
            }
            if entry == "*" {
                return Err(GateError::MalformedField {
                    field: "scope",
                    reason: "a wildcard scope entry is not a scope",
                });
            }
            set.insert(entry);
        }
        Ok(Self { entries: set })
    }

    /// Whether `self` covers every entry of `other`.
    pub fn covers(&self, other: &Scope) -> bool {
        other.entries.is_subset(&self.entries)
    }

    /// Whether the scope is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The entries, in sorted order.
    pub fn entries(&self) -> impl Iterator<Item = &str> {
        self.entries.iter().map(String::as_str)
    }
}

impl fmt::Display for Scope {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("{")?;
        for (index, entry) in self.entries.iter().enumerate() {
            if index > 0 {
                f.write_str(",")?;
            }
            f.write_str(entry)?;
        }
        f.write_str("}")
    }
}

/// A caller-supplied value that makes one request distinguishable from another.
///
/// The nonce is what turns "the same approval was presented twice" into a
/// detectable event: it is recorded with the approval and with every consumption
/// attempt, so a replay is visible as the same nonce arriving twice rather than
/// as an anonymous duplicate.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct Nonce(String);

impl Nonce {
    /// The shortest admissible nonce. Short nonces are guessable, and a guessable
    /// nonce is a request id a third party can aim at.
    pub const MIN_LEN: usize = 16;
    /// The longest admissible nonce.
    pub const MAX_LEN: usize = 128;

    /// Register a nonce.
    pub fn new(value: impl Into<String>) -> Result<Self, GateError> {
        let value = value.into();
        if value.len() < Self::MIN_LEN || value.len() > Self::MAX_LEN {
            return Err(GateError::MalformedField {
                field: "nonce",
                reason: "must be 16..=128 bytes",
            });
        }
        if !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err(GateError::MalformedField {
                field: "nonce",
                reason: "must be ASCII alphanumeric, '-' or '_'",
            });
        }
        Ok(Self(value))
    }

    /// The nonce's bytes.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for Nonce {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Exactly what an approval authorises: one action, on one target, for a finite
/// set of effects.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Intent {
    action: ActionId,
    target: TargetRef,
    scope: Scope,
}

impl Intent {
    /// Build an intent.
    pub const fn new(action: ActionId, target: TargetRef, scope: Scope) -> Self {
        Self {
            action,
            target,
            scope,
        }
    }

    /// The action.
    pub const fn action(&self) -> &ActionId {
        &self.action
    }

    /// The target.
    pub const fn target(&self) -> &TargetRef {
        &self.target
    }

    /// The scope.
    pub const fn scope(&self) -> &Scope {
        &self.scope
    }

    /// Whether an approval of `self` covers `wanted`.
    ///
    /// Equal action **and** equal target **and** `self.scope ⊇ wanted.scope`.
    /// Anything else is a different intent: approving `oauth.refresh` on one
    /// provider credential never covers `provider.spend` on another, and a scope
    /// that is merely a subset relationship the other way round widens the
    /// approval instead of narrowing it, so it is refused.
    pub fn covers(&self, wanted: &Intent) -> bool {
        self.action == wanted.action
            && self.target == wanted.target
            && self.scope.covers(&wanted.scope)
    }

    /// Refuse `wanted` unless this intent covers it, naming both sides.
    pub fn require_covers(&self, wanted: &Intent) -> Result<(), GateError> {
        if self.covers(wanted) {
            return Ok(());
        }
        Err(GateError::IntentNotCovered {
            approved: format!("{}", self),
            requested: format!("{}", wanted),
        })
    }
}

impl fmt::Display for Intent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}@{} {}", self.action, self.target, self.scope)
    }
}
