//! Desk, claim-queue and decision vocabulary, plus the invariants the
//! GQL-13 contract states.
//!
//! This module is DELIBERATELY DEPENDENCY-FREE: it uses no crate outside the
//! Rust standard library, so it can be compiled and tested on its own with
//! plain `rustc`. That is not a style preference — it is what makes the
//! invariants provable from a lane that may not touch `Cargo.toml` or
//! `lib.rs`. Wire it with one line (`pub mod schema;`) once that write is
//! granted; nothing here changes when it is.
//!
//! SCOPE. Every constant below is COPIED FROM an observed running service and
//! is marked with its source. Nothing here is invented: an invented vocabulary
//! entry would be a second authority for a value the services already own, and
//! the two would drift.
//!
//! WHAT THIS MODULE DOES NOT DO. It does not own queue state. `queue_store`
//! remains the queue-state authority; the types here describe what the store
//! already enforces and are used to check a request BEFORE it reaches the
//! store. No lock, no file, no clock and no lease arithmetic lives here.

use std::fmt;

/// Product lane a slip belongs to. Source: `desk_pub.PRODUCTS`.
pub const PRODUCTS: [&str; 3] = ["anw", "parlour", "platform"];

/// Desk kind. Source: `desk_pub.KINDS`.
///
/// `decide` is kept in this list and ALSO named separately, because it is the
/// one kind that is an ask to a person rather than a job: filing one must never
/// enqueue it, and a human answer is the only thing that closes it.
pub const KINDS: [&str; 5] = ["task", "review", "decide", "unblock", "look"];

/// The decide kind, by name. Source: `desk_pub.DECIDE_KIND`.
pub const DECIDE_KIND: &str = "decide";

/// Desk a slip is routed to. Source: `desk_pub.OWNERS`.
pub const OWNERS: [&str; 3] = ["andy", "josh", "both"];

/// Filing state of the slip itself. Source: `desk_pub.STATES`.
///
/// Distinct from [`QueueState`] on purpose: a slip can be `open` while its run
/// is `done`, and `done` while a queue row is still live. One type would let a
/// caller read a run outcome off a filing state.
pub const SLIP_STATES: [&str; 3] = ["open", "later", "done"];

/// Runner lane a row is restricted to. Source: `queue_store.DELIVERIES`,
/// widened by the empty string that `desk_pub.DELIVERIES` prepends.
///
/// The empty string is a real member and means "the coordinator picks". It is
/// modelled as [`Delivery::Auto`] rather than as absence, because the desk
/// stores it on every new slip and reads it back.
pub const DELIVERIES: [&str; 3] = ["", "simple-city", "hive"];

/// Queue state owned by the queue store. Source: `queue_store` states.
pub const QUEUE_STATES: [&str; 5] =
    ["queued", "claimed", "released", "done", "archived"];

/// The two states a row may be archived FROM. Source:
/// `queue_store.ARCHIVABLE = (done, released)`.
pub const ARCHIVABLE: [&str; 2] = ["done", "released"];

/// Row kinds a blind `/queue/claim` sweep may NOT take. Source:
/// `queue_store.NAMED_KIND_ONLY`.
///
/// A blank `holder` is the marker for "not a named worker". The refusal is
/// applied to the HOLDER and not only to the kind, because a watchdog that
/// fills the kind in from the board names the kind correctly and still must not
/// be served the row.
pub const NAMED_KIND_ONLY: [&str; 8] = [
    "advisor-plan-moa", "advisor-plan", "director-review", "waiting-3d",
    "mockup-regen", "mesh-generate", "mesh-texture", "process-queue",
];

/// The holder a caller uses when it is NOT a named worker. Source:
/// `queue_store` (the default of `claim`, `claim_row` and `start_by_source`).
pub const DEFAULT_HOLDER: &str = "jarvis";

/// Thread entry kinds a report route may file. Source:
/// `desk_pub.THREAD_REPORT_KINDS`.
///
/// `prompt` is absent by design: a directive is human-session only.
pub const THREAD_REPORT_KINDS: [&str; 6] =
    ["report", "event", "digest", "decision", "tg-prompt", "tg-note"];

/// Provenance keys a report may carry. Source: `desk_pub.THREAD_META_KEYS`.
pub const THREAD_META_KEYS: [&str; 6] = [
    "source", "telegram_chat_id", "telegram_message_id", "how", "confidence",
    "status",
];

/// The derived lifecycle lights. Source: the `status` values `_status_of`
/// emits in `desk_pub`.
pub const LIFECYCLE_LIGHTS: [&str; 5] =
    ["idle", "sent", "working", "reported", "done"];

/// One bounded page's default size. Stated by the GQL-13 contract, not copied
/// from a service: the desk has no paging default to copy.
pub const DEFAULT_PAGE_SIZE: u32 = 20;

/// The largest page a caller may ask for.
pub const MAX_PAGE_SIZE: u32 = 100;

/// Delivery lane for one slip.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Delivery {
    /// No restriction; dispatch picks the runner. Wire value `""`.
    Auto,
    /// Restricted to the Simple City runner lane.
    SimpleCity,
    /// Restricted to the Hive runner lane.
    Hive,
}

impl Delivery {
    /// The wire value the desk and store persist.
    pub fn as_str(self) -> &'static str {
        match self {
            Delivery::Auto => "",
            Delivery::SimpleCity => "simple-city",
            Delivery::Hive => "hive",
        }
    }

    /// Parse a wire value, case-folded the way `_slip_record` folds it.
    ///
    /// Returns `None` for a value that is not a delivery. It MUST NOT fall back
    /// to `Auto`: the observed filing route refuses a typo with a 400 so the
    /// filer sees it, and a silent fallback here would hand the work to the one
    /// runner the filer was steering away from.
    pub fn parse(raw: &str) -> Option<Delivery> {
        let folded = raw.trim().to_lowercase();
        match folded.as_str() {
            "" => Some(Delivery::Auto),
            "simple-city" => Some(Delivery::SimpleCity),
            "hive" => Some(Delivery::Hive),
            _ => None,
        }
    }
}

/// Queue state owned by the queue store.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueState {
    /// Waiting for a runner to claim it.
    Queued,
    /// Held under a live lease.
    Claimed,
    /// Lapsed or failed, and reclaimable by a named worker.
    Released,
    /// Closed because the work finished.
    Done,
    /// Archived by board hygiene, which is NOT an outcome.
    Archived,
}

impl QueueState {
    /// The wire value the store persists.
    pub fn as_str(self) -> &'static str {
        match self {
            QueueState::Queued => "queued",
            QueueState::Claimed => "claimed",
            QueueState::Released => "released",
            QueueState::Done => "done",
            QueueState::Archived => "archived",
        }
    }

    /// Parse a wire value, case-folded.
    pub fn parse(raw: &str) -> Option<QueueState> {
        match raw.trim().to_lowercase().as_str() {
            "queued" => Some(QueueState::Queued),
            "claimed" => Some(QueueState::Claimed),
            "released" => Some(QueueState::Released),
            "done" => Some(QueueState::Done),
            "archived" => Some(QueueState::Archived),
            _ => None,
        }
    }

    /// Whether a row in this state may be archived at all.
    pub fn is_archivable(self) -> bool {
        matches!(self, QueueState::Done | QueueState::Released)
    }

    /// Whether a row in this state still counts as live for a by-source lookup.
    ///
    /// `archived` is not live: the board is done with it. `done` is not live
    /// either, but it IS the terminal answer, which is why the two are not
    /// collapsed into one predicate.
    pub fn is_live(self) -> bool {
        matches!(self, QueueState::Queued | QueueState::Claimed
                      | QueueState::Released)
    }
}

/// Why a request was refused before it reached the store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refusal {
    /// A field was absent or empty.
    Missing(&'static str),
    /// A value was present but not in its vocabulary.
    Unknown {
        /// Which field was wrong.
        field: &'static str,
        /// The value that was refused.
        value: String,
    },
    /// A blind claim tried to take a row only a named worker may take.
    NamedKindOnly {
        /// The row kind that is off limits to a blind sweep.
        kind: String,
    },
    /// A completion was attempted on a row that was never claimed.
    NotClaimed,
    /// The claim is held by somebody else.
    HeldBy(String),
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Refusal::Missing(field) => write!(f, "{field} required"),
            Refusal::Unknown { field, value } => {
                write!(f, "{field} must be one of the known values, not {value:?}")
            }
            Refusal::NamedKindOnly { kind } => write!(
                f,
                "kind {kind:?} must be claimed by a named worker, not a blind sweep"
            ),
            Refusal::NotClaimed => {
                write!(f, "row not claimed — a report is not a completion")
            }
            Refusal::HeldBy(holder) => write!(f, "held by {holder}"),
        }
    }
}

impl std::error::Error for Refusal {}

/// Validate a filing the way `_slip_record` does, WITHOUT any I/O.
///
/// The order of checks is the observed order: title, product, kind, owner,
/// delivery. Delivery is checked rather than normalised, so a typo is a refusal
/// the filer can see.
pub fn validate_filing(
    title: &str,
    product: &str,
    kind: &str,
    owner: &str,
    delivery: &str,
) -> Result<Delivery, Refusal> {
    if title.trim().is_empty() {
        return Err(Refusal::Missing("title"));
    }
    let product = product.trim().to_lowercase();
    if !PRODUCTS.contains(&product.as_str()) {
        return Err(Refusal::Unknown { field: "product", value: product });
    }
    let kind = kind.trim().to_lowercase();
    if !KINDS.contains(&kind.as_str()) {
        return Err(Refusal::Unknown { field: "kind", value: kind });
    }
    let owner = owner.trim().to_lowercase();
    if !OWNERS.contains(&owner.as_str()) {
        return Err(Refusal::Unknown { field: "owner", value: owner });
    }
    Delivery::parse(delivery)
        .ok_or_else(|| Refusal::Unknown {
            field: "delivery",
            value: delivery.trim().to_lowercase(),
        })
}

/// Whether a blind claim may take a row of `kind` for `holder`.
///
/// A blind sweep is one whose holder is [`DEFAULT_HOLDER`]. The refusal is on
/// the HOLDER first: a watchdog can name the kind correctly and still be the
/// wrong worker for that row.
pub fn named_kind_refusal(kind: &str, holder: &str) -> Option<Refusal> {
    let named = !holder.trim().is_empty() && holder.trim() != DEFAULT_HOLDER;
    if named {
        return None;
    }
    if NAMED_KIND_ONLY.contains(&kind.trim()) {
        return Some(Refusal::NamedKindOnly { kind: kind.trim().to_string() });
    }
    None
}

/// Whether a slip filed with this delivery and kind should be enqueued.
///
/// Mirrors `desk_pub.autoqueue_slip` exactly. Narrow on purpose: a row enqueued
/// for a runner that will never claim it looks started and is not.
pub fn should_autoqueue(delivery: Delivery, kind: &str, state: &str) -> bool {
    if delivery != Delivery::SimpleCity {
        return false;
    }
    if kind.trim().to_lowercase() == DECIDE_KIND {
        return false;
    }
    let state = state.trim().to_lowercase();
    state.is_empty() || state == "open"
}

/// Whether a completion is allowed from `state` under `require_claimed`.
///
/// This is the `report != done` rule. A report in the thread is not proof the
/// work finished, so a row that was never claimed cannot be completed; saying
/// the work FAILED is legal from any live state, which is why release is not
/// routed through here.
pub fn completion_allowed(state: QueueState, require_claimed: bool) -> Result<(), Refusal> {
    if !require_claimed {
        return Ok(());
    }
    match state {
        QueueState::Claimed => Ok(()),
        _ => Err(Refusal::NotClaimed),
    }
}

/// Clamp a requested page size, refusing anything outside the contract's range.
///
/// An omitted or null `first` is [`DEFAULT_PAGE_SIZE`]; it is not a zero and not
/// an error. Out-of-range values are refused rather than clamped: a client that
/// asked for 5000 and received 100 without being told cannot tell a small desk
/// from a silent truncation.
pub fn page_size(first: Option<u32>) -> Result<u32, Refusal> {
    match first {
        None => Ok(DEFAULT_PAGE_SIZE),
        Some(n) if n >= 1 && n <= MAX_PAGE_SIZE => Ok(n),
        Some(n) => Err(Refusal::Unknown { field: "first", value: n.to_string() }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_delivery_is_a_member_not_an_absence() {
        assert_eq!(Delivery::parse(""), Some(Delivery::Auto));
        assert_eq!(Delivery::parse("  "), Some(Delivery::Auto));
        assert_eq!(Delivery::Auto.as_str(), "");
    }

    #[test]
    fn a_typoed_delivery_is_refused_and_never_folds_to_auto() {
        // The whole point: no silent fallback to the lane the filer avoided.
        assert_eq!(Delivery::parse("simplecity"), None);
        assert_eq!(Delivery::parse("smple-city"), None);
        let err = validate_filing("t", "platform", "task", "both", "smple-city")
            .unwrap_err();
        assert_eq!(err, Refusal::Unknown {
            field: "delivery", value: "smple-city".to_string() });
    }

    #[test]
    fn filing_vocabulary_matches_the_observed_services() {
        assert_eq!(Delivery::parse("SIMPLE-CITY"), Some(Delivery::SimpleCity));
        assert!(validate_filing("t", "anw", "decide", "andy", "hive").is_ok());
        assert!(validate_filing("  ", "platform", "task", "both", "").is_err());
        assert!(validate_filing("t", "nope", "task", "both", "").is_err());
        assert!(validate_filing("t", "platform", "nope", "both", "").is_err());
        assert!(validate_filing("t", "platform", "task", "nope", "").is_err());
    }

    #[test]
    fn a_decide_slip_is_never_autoqueued() {
        // An ask to a person handed to a runner answers the wrong question.
        assert!(!should_autoqueue(Delivery::SimpleCity, "decide", "open"));
        assert!(should_autoqueue(Delivery::SimpleCity, "task", "open"));
        assert!(should_autoqueue(Delivery::SimpleCity, "look", ""));
    }

    #[test]
    fn only_simple_city_has_a_runner_behind_it() {
        assert!(!should_autoqueue(Delivery::Hive, "task", "open"));
        assert!(!should_autoqueue(Delivery::Auto, "task", "open"));
    }

    #[test]
    fn an_already_closed_slip_is_never_autoqueued() {
        assert!(!should_autoqueue(Delivery::SimpleCity, "task", "done"));
        assert!(!should_autoqueue(Delivery::SimpleCity, "task", "later"));
    }

    #[test]
    fn report_is_not_done() {
        assert_eq!(completion_allowed(QueueState::Queued, true),
                   Err(Refusal::NotClaimed));
        assert_eq!(completion_allowed(QueueState::Released, true),
                   Err(Refusal::NotClaimed));
        assert_eq!(completion_allowed(QueueState::Claimed, true), Ok(()));
    }

    #[test]
    fn release_may_say_unstarted_work_failed() {
        // Distinct from completion: failure is legal from any state.
        assert_eq!(completion_allowed(QueueState::Queued, false), Ok(()));
    }

    #[test]
    fn a_blind_sweep_cannot_take_a_named_kind_only_row() {
        let refused = named_kind_refusal("waiting-3d", DEFAULT_HOLDER);
        assert!(refused.is_some());
        // The named worker may take it.
        assert_eq!(named_kind_refusal("waiting-3d", "process-queue-worker"), None);
        // A plain row is fine for anybody.
        assert_eq!(named_kind_refusal("desk-prompt", DEFAULT_HOLDER), None);
    }

    #[test]
    fn the_refusal_is_on_the_holder_not_only_the_kind() {
        // A watchdog that names the kind correctly is still the wrong worker.
        let refusal = named_kind_refusal("advisor-plan-moa", DEFAULT_HOLDER);
        assert!(matches!(refusal, Some(Refusal::NamedKindOnly { .. })));
    }

    #[test]
    fn archive_is_hygiene_and_needs_a_finished_state() {
        assert!(QueueState::Done.is_archivable());
        assert!(QueueState::Released.is_archivable());
        assert!(!QueueState::Queued.is_archivable());
        assert!(!QueueState::Claimed.is_archivable());
        assert!(!QueueState::Archived.is_archivable());
    }

    #[test]
    fn live_states_are_the_three_the_store_calls_live() {
        assert!(QueueState::Queued.is_live());
        assert!(QueueState::Claimed.is_live());
        assert!(QueueState::Released.is_live());
        assert!(!QueueState::Done.is_live());
        assert!(!QueueState::Archived.is_live());
    }

    #[test]
    fn paging_defaults_on_absent_and_refuses_out_of_range() {
        assert_eq!(page_size(None), Ok(20));
        assert_eq!(page_size(Some(1)), Ok(1));
        assert_eq!(page_size(Some(100)), Ok(100));
        assert!(page_size(Some(0)).is_err());
        assert!(page_size(Some(101)).is_err());
    }

    #[test]
    fn queue_state_round_trips_and_refuses_an_unknown_value() {
        for raw in QUEUE_STATES {
            let parsed = QueueState::parse(raw).expect("known state parses");
            assert_eq!(parsed.as_str(), raw);
        }
        assert_eq!(QueueState::parse("finished"), None);
    }

    #[test]
    fn the_vocabularies_are_the_observed_ones() {
        assert_eq!(PRODUCTS, ["anw", "parlour", "platform"]);
        assert_eq!(KINDS, ["task", "review", "decide", "unblock", "look"]);
        assert_eq!(OWNERS, ["andy", "josh", "both"]);
        assert_eq!(DELIVERIES, ["", "simple-city", "hive"]);
        assert_eq!(ARCHIVABLE, ["done", "released"]);
        assert!(KINDS.contains(&DECIDE_KIND));
        assert!(!THREAD_REPORT_KINDS.contains(&"prompt"));
    }
}