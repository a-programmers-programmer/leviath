//! Process-wide content interning for region entry text.
//!
//! Identical strings share one `Arc<str>` allocation. Used by
//! [`crate::region::RegionEntry`] so fleets of agents with the same pinned
//! material (or any other duplicated entry text) do not pay N copies in RSS.
//!
//! Entries are held weakly in the interner table: once no live
//! [`InternedString`] references a blob, the next intern of a different string
//! (or an explicit purge) drops the dead weak. Unique, short-lived conversation
//! text is therefore not permanently retained by the table.

use std::collections::HashMap;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::ops::Deref;
use std::sync::{Arc, Mutex, OnceLock, Weak};

use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// Interned, reference-counted string used for region entry content.
///
/// Cheap to clone (bumps an `Arc` refcount). Compares equal to any string with
/// the same contents, whether or not the allocation is shared.
#[derive(Clone, Eq)]
pub struct InternedString(Arc<str>);

impl InternedString {
    /// Intern `s` and return a handle. Identical content reuses the same
    /// allocation across the process.
    pub fn new(s: impl AsRef<str>) -> Self {
        Self(intern(s.as_ref()))
    }

    /// Borrow the interned text.
    #[inline]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// True when both handles point at the same allocation (not merely equal
    /// content). Useful in tests that assert sharing.
    #[inline]
    pub fn ptr_eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }

    /// Strong reference count of the underlying allocation (for tests / metrics).
    #[inline]
    pub fn strong_count(&self) -> usize {
        Arc::strong_count(&self.0)
    }
}

impl PartialEq for InternedString {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0) || self.0.as_ref() == other.0.as_ref()
    }
}

impl PartialEq<str> for InternedString {
    fn eq(&self, other: &str) -> bool {
        self.0.as_ref() == other
    }
}

impl PartialEq<&str> for InternedString {
    fn eq(&self, other: &&str) -> bool {
        self.0.as_ref() == *other
    }
}

impl PartialEq<String> for InternedString {
    fn eq(&self, other: &String) -> bool {
        self.0.as_ref() == other.as_str()
    }
}

impl PartialEq<InternedString> for str {
    fn eq(&self, other: &InternedString) -> bool {
        self == other.0.as_ref()
    }
}

impl PartialEq<InternedString> for &str {
    fn eq(&self, other: &InternedString) -> bool {
        *self == other.0.as_ref()
    }
}

impl PartialEq<InternedString> for String {
    fn eq(&self, other: &InternedString) -> bool {
        self.as_str() == other.0.as_ref()
    }
}

impl Hash for InternedString {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0.as_ref().hash(state);
    }
}

impl Deref for InternedString {
    type Target = str;

    fn deref(&self) -> &str {
        &self.0
    }
}

impl AsRef<str> for InternedString {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<&str> for InternedString {
    fn from(s: &str) -> Self {
        Self::new(s)
    }
}

impl From<String> for InternedString {
    fn from(s: String) -> Self {
        Self::new(s)
    }
}

impl From<&String> for InternedString {
    fn from(s: &String) -> Self {
        Self::new(s.as_str())
    }
}

impl From<InternedString> for String {
    fn from(s: InternedString) -> String {
        s.0.to_string()
    }
}

impl From<&InternedString> for String {
    fn from(s: &InternedString) -> String {
        s.0.to_string()
    }
}

impl fmt::Debug for InternedString {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self.0.as_ref(), f)
    }
}

impl fmt::Display for InternedString {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl Serialize for InternedString {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for InternedString {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let s = String::deserialize(deserializer)?;
        Ok(Self::new(s))
    }
}

/// Process-global interner. Weak entries so unused blobs can be reclaimed.
struct Interner {
    /// Hash → weak handles that currently or recently hashed here.
    buckets: HashMap<u64, Vec<Weak<str>>>,
}

impl Interner {
    fn new() -> Self {
        Self {
            buckets: HashMap::new(),
        }
    }

    fn intern(&mut self, s: &str) -> Arc<str> {
        let hash = {
            let mut hasher = std::collections::hash_map::DefaultHasher::new();
            s.hash(&mut hasher);
            hasher.finish()
        };

        let bucket = self.buckets.entry(hash).or_default();
        // Drop dead weaks so the table does not accumulate junk.
        bucket.retain(|w| w.strong_count() > 0);

        for weak in bucket.iter() {
            if let Some(arc) = weak.upgrade()
                && arc.as_ref() == s
            {
                return arc;
            }
        }

        let arc: Arc<str> = Arc::from(s);
        bucket.push(Arc::downgrade(&arc));
        arc
    }

    #[cfg(test)]
    fn live_unique_count(&self) -> usize {
        self.buckets
            .values()
            .flat_map(|b| b.iter())
            .filter(|w| w.strong_count() > 0)
            .count()
    }
}

fn global() -> &'static Mutex<Interner> {
    static INTERNER: OnceLock<Mutex<Interner>> = OnceLock::new();
    INTERNER.get_or_init(|| Mutex::new(Interner::new()))
}

fn intern(s: &str) -> Arc<str> {
    // Empty string is common; still intern it so clones stay shared.
    let mut guard = global()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.intern(s)
}

/// Number of unique strings currently held live by the interner (test/metrics).
#[cfg(test)]
pub fn interned_live_count() -> usize {
    let guard = global()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.live_unique_count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_content_shares_allocation() {
        let a = InternedString::new("pinned architecture block");
        let b = InternedString::new("pinned architecture block");
        assert!(a.ptr_eq(&b));
        assert_eq!(a, b);
        assert_eq!(a, "pinned architecture block");
    }

    #[test]
    fn distinct_content_does_not_share() {
        let a = InternedString::new("alpha");
        let b = InternedString::new("beta");
        assert!(!a.ptr_eq(&b));
        assert_ne!(a, b);
    }

    #[test]
    fn clone_is_cheap_and_shares() {
        let a = InternedString::new("shared");
        let b = a.clone();
        assert!(a.ptr_eq(&b));
        assert!(a.strong_count() >= 2);
    }

    #[test]
    fn serde_roundtrip_reinterns() {
        let a = InternedString::new("serde me");
        let json = serde_json::to_string(&a).unwrap();
        assert_eq!(json, "\"serde me\"");
        let b: InternedString = serde_json::from_str(&json).unwrap();
        assert!(a.ptr_eq(&b));
        assert_eq!(a, b);
    }

    #[test]
    fn into_string_preserves_text() {
        let a = InternedString::new("hello");
        let s: String = a.into();
        assert_eq!(s, "hello");
    }
}
