//! The opaque sensitive value, its digest, and the binding an approval names.
//!
//! The GQL-01 contract declares sensitive data as `scalar Confidential` and
//! says "no read path returns its contents". This module is how that sentence
//! becomes true of the code rather than of the comment:
//!
//! * [`Confidential`] exposes **no** accessor that yields the secret without a
//!   [`Grant`] (defined in [`crate::gate`], which is the only thing that can
//!   construct one). There is no `as_str`, no `Deref`, no `Display`, no `Clone`.
//! * `Serialize` exists and **redacts**, so ordinary serialisation, structured
//!   logs and generated clients cannot leak the secret by accident.
//! * `Deserialize` is **refused unconditionally**: a sensitive value may never
//!   be reconstructed from an inbound payload, so there is no parse path that a
//!   client, a cache or a database row could use to re-introduce one.
//! * The bytes are zeroed on drop (`Zeroizing`), so a dropped value does not
//!   linger in freed heap memory for a later allocation to pick up.

use std::fmt;

use serde::de::{Deserialize, Deserializer, Error as _};
use serde::ser::{Serialize, Serializer};
use sha2::{Digest as _, Sha256};
use zeroize::Zeroizing;

/// Stable identity of one sensitive value.
///
/// Two values with identical bytes are still two different values, so an
/// approval for one never authorises the other. The identity is chosen by the
/// owning vertical (an entity id, a row id, a handle) and is *not* a secret.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, serde::Serialize, serde::Deserialize,
)]
pub struct ValueId(u64);

impl ValueId {
    /// Wrap an existing vertical-owned identifier.
    pub const fn new(id: u64) -> Self {
        Self(id)
    }

    /// The identifier as a number.
    pub const fn get(self) -> u64 {
        self.0
    }
}

impl fmt::Display for ValueId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "value#{}", self.0)
    }
}

/// A domain-separated SHA-256 over some bytes.
///
/// A digest is derived data, not a secret, so it may be stored, logged and
/// compared freely; it is what lets the ledger bind an approval to a value
/// without holding the value. Domain separation means a digest produced here can
/// never collide with a digest the same bytes would produce under another
/// scheme in the tree.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Digest([u8; 32]);

/// Domain-separation prefix for value digests.
const VALUE_DOMAIN: &[u8] = b"leviath-confirm/value/v1\0";

impl Digest {
    /// Digest `bytes` under this crate's value domain.
    pub fn of_bytes(bytes: &[u8]) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(VALUE_DOMAIN);
        hasher.update((bytes.len() as u64).to_be_bytes());
        hasher.update(bytes);
        Self(hasher.finalize().into())
    }

    /// Digest a string under this crate's value domain.
    pub fn of_str(value: &str) -> Self {
        Self::of_bytes(value.as_bytes())
    }

    /// The raw 32 bytes.
    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }

    /// Lowercase hex, the form that appears in ledgers and logs.
    pub fn hex(&self) -> String {
        let mut out = String::with_capacity(64);
        for byte in self.0 {
            out.push(char::from_digit((byte >> 4) as u32, 16).unwrap_or('0'));
            out.push(char::from_digit((byte & 0x0f) as u32, 16).unwrap_or('0'));
        }
        out
    }

    /// Parse the lowercase-hex form produced by [`Digest::hex`].
    ///
    /// Returns `None` for anything that is not exactly 64 hex digits, so a
    /// truncated or padded ledger digest is a hard parse failure rather than a
    /// silently different digest.
    pub fn parse_hex(text: &str) -> Option<Self> {
        if text.len() != 64 {
            return None;
        }
        let mut bytes = [0u8; 32];
        for (index, chunk) in text.as_bytes().chunks_exact(2).enumerate() {
            let hi = (chunk[0] as char).to_digit(16)?;
            let lo = (chunk[1] as char).to_digit(16)?;
            bytes[index] = ((hi << 4) | lo) as u8;
        }
        Some(Self(bytes))
    }
}

impl fmt::Debug for Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("sha256:")?;
        f.write_str(&self.hex())
    }
}

impl fmt::Display for Digest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.hex())
    }
}

impl Serialize for Digest {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.hex())
    }
}

impl<'de> Deserialize<'de> for Digest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        Self::parse_hex(&text).ok_or_else(|| D::Error::custom("digest must be 64 hex digits"))
    }
}

/// Exactly which version of which value an approval was granted for.
///
/// This is the "value version/digest" half of the approval binding required by
/// the work order. It is derived data and travels freely.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ValueBinding {
    /// Which value.
    pub value_id: ValueId,
    /// Which revision of it. A rotation bumps this.
    pub version: u32,
    /// Digest of the bytes at that revision.
    pub digest: Digest,
}

impl ValueBinding {
    /// The binding of a specific value.
    pub const fn new(value_id: ValueId, version: u32, digest: Digest) -> Self {
        Self {
            value_id,
            version,
            digest,
        }
    }
}

impl fmt::Display for ValueBinding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}/v{}/{}", self.value_id, self.version, self.digest)
    }
}

/// An opaque sensitive string.
///
/// The only way to read the bytes is [`Confidential::reveal`], which requires
/// ownership of a `Grant`. `Grant` cannot be constructed, cloned or deserialised
/// outside this crate, and only the confirm gate issues one — so "only the
/// gate's authorized consumption path may unwrap" is a property the compiler
/// checks, not a rule a reviewer has to notice.
pub struct Confidential {
    binding: ValueBinding,
    bytes: Zeroizing<String>,
}

impl Confidential {
    /// Wrap `secret` as an opaque value at `version`.
    ///
    /// `id` is the owning vertical's identity for the value; `version` starts at
    /// `1` for a fresh value and is bumped by a rotation, so an approval taken
    /// against an earlier revision binds to the earlier revision.
    pub fn new(id: ValueId, version: u32, secret: impl Into<String>) -> Self {
        let bytes = Zeroizing::new(secret.into());
        let binding = ValueBinding::new(id, version, Digest::of_str(&bytes));
        Self { binding, bytes }
    }

    /// The value's identity, version and digest — never its bytes.
    pub const fn binding(&self) -> ValueBinding {
        self.binding
    }

    /// Digest of the bytes as they are right now.
    pub const fn digest(&self) -> Digest {
        self.binding.digest
    }

    /// Length of the secret in bytes.
    ///
    /// Exposed because redaction and truncation logic legitimately needs it
    /// ("the token is too long"); it reveals no content. Callers that only need
    /// "is there a secret at all" should use `len() > 0`.
    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    /// Whether the value is the empty string.
    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }

    /// Unwrap the bytes. The **only** read path.
    ///
    /// Consumes the `Grant`, so one approval yields at most one read, and
    /// re-checks the binding and the grant's own expiry before returning: a
    /// grant issued for another value, or for an earlier digest of this value,
    /// or held past its expiry, does not open this value.
    pub fn reveal(
        &self,
        grant: crate::gate::Grant,
        now: crate::Tick,
    ) -> Result<&str, crate::gate::GateError> {
        grant.open(self, now)
    }

    /// The bytes, crate-internal.
    ///
    /// `pub(crate)` and reachable only from `Grant::open`, which is itself
    /// reachable only from `Gate::with_confirmed_value`. That chain is the whole
    /// enforcement: there is no public method, no `Deref`, and no `Display` that
    /// yields these bytes.
    pub(crate) fn raw_bytes(&self) -> &str {
        &self.bytes
    }

    /// A stable description for logs: identity, version and digest, no content.
    pub fn redacted(&self) -> String {
        format!("Confidential({})", self.binding)
    }
}

impl fmt::Debug for Confidential {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Note the deliberate absence of the bytes from the field list.
        f.debug_struct("Confidential")
            .field("value_id", &self.binding.value_id)
            .field("version", &self.binding.version)
            .field("digest", &self.binding.digest)
            .field("bytes", &"<redacted>")
            .finish()
    }
}

impl fmt::Display for Confidential {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.redacted())
    }
}

impl Serialize for Confidential {
    /// Serialise the value **redacted**: identity, version and digest only.
    ///
    /// This is what makes "ordinary serialization … do[es] not expose the
    /// secret" true for any consumer that merely derives `Serialize`, including
    /// the generated GraphQL clients: there is no byte field to omit.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct as _;

        let mut state = serializer.serialize_struct("Confidential", 5)?;
        state.serialize_field("$redacted", &true)?;
        state.serialize_field("valueId", &self.binding.value_id)?;
        state.serialize_field("version", &self.binding.version)?;
        state.serialize_field("digest", &self.binding.digest)?;
        // Named explicitly so the on-the-wire shape states the boundary rather
        // than leaving a reader to infer it from a missing key.
        state.serialize_field("bytes", &Option::<String>::None)?;
        state.end()
    }
}

impl<'de> Deserialize<'de> for Confidential {
    /// Always fails.
    ///
    /// A sensitive value is created where the secret lives, never parsed from a
    /// payload. Refusing the impl outright means a cache, a database row, a
    /// request body or a test fixture cannot smuggle one in, and it removes the
    /// entire class of "the client round-tripped the secret" bugs.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        // Still read the value so the error is reported against the payload
        // rather than as a mysterious short-circuit.
        let ignored =
            serde_json::Value::deserialize(deserializer).unwrap_or(serde_json::Value::Null);
        let _ = ignored;
        Err(D::Error::custom(
            "Confidential cannot be deserialised: a sensitive value may never be reconstructed from a payload",
        ))
    }
}
