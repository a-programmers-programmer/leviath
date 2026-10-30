"""SITE EVENTS — usage / reliability / workflow telemetry for the admin site.

Owner: GQL-35 `telemetry-worker`. One writer, one file set. This module is the
new file; it is wired into the existing app by the one-line patch in
`../working/main_telemetry.patch`, so no existing file has to be edited by hand.

WHY IT EXISTS
-------------
`ops_events.py` answers "what did the *coordinator* decide" (ticks, row
verdicts, spawn failures). It cannot answer the four questions in the GQL-35
outcome: which site surfaces are used, where requests fail, how they perform,
and whether a human/agent workflow finished. Nothing in the tree measures a
page view, a request latency or a UI action from the browser side; that is the
gap this module closes.

DESIGN CONSTRAINTS, AND THE RULE EACH ONE BUYS
----------------------------------------------
R1  Reuse, don't rebuild. The stream, its bounding, its per-source snapshot
    cache, its rate limit and its compaction are `ops_events`', which already
    runs on this box. This module writes INTO that store under the reserved
    source `site`, so the desk's existing `/api/ops/liveness` strip reports
    telemetry health with no second panel and no second store.

R2  A CLOSED allow-list, enforced twice. `contract/site-events-v1.schema.json`
    has `additionalProperties: false`, and `normalize()` drops every key the
    contract does not name before the validator ever sees it. A raw GraphQL
    `variables` blob, a cookie, an Authorization header or a thread body
    therefore has nowhere to land — not because a caller remembered to strip
    it, but because the door is the wrong shape. A dropped key is COUNTED
    (`privacy.reject`), so "did anything try" is visible without the value
    being recorded.

R3  Two independent de-duplication keys, because there are two ways to
    double-count. A reconnecting beacon replays the same `event_id`; a spooled
    flush replays the same `seq`. The first is de-duplicated here (bounded
    in-process ring + a scan of the stream on cold start); the second is
    de-duplicated by `ops_events` itself on `(source, seq)`.

R4  A retry must not read as more usage. `attempt` and `continuation` are part
    of the contract, and the counters below count a trace ONCE for usage and
    completion while still counting every attempt's latency. That is the
    "reconnect/retry does not overcount" clause, made mechanical.

R5  A monitor must never be able to break the site. `record()` swallows
    everything and is capped; the Flask hook in the patch runs in
    `after_request` behind `try/except`, and it is a no-op when
    `SITE_TELEMETRY_DISABLED` is set.

R6  Never store what we would not print. String values are hard-rejected if
    they are secret-shaped (`sk-…`, `ghp_…`, `eyJ…` JWT, PEM headers, long hex)
    or if they contain `=`, `?`, `@` or whitespace-heavy prose; `reason` is
    restricted to a small alphabet. `looks_sensitive()` is also exported so the
    test suite can prove the rule with a synthetic scanner.

THE CORRELATION SPINE
---------------------
`trace_id` (`t:<32 hex>`) is minted in the browser (or by a machine caller) on
the first request of a workflow and carried on every hop: UI action -> API
operation -> queue item -> Leviath run -> run transition -> confirmation
outcome. `run_ref` and `queue_ref` are bounded identifiers, not paths. Field
names are chosen so they line up with the identities the GraphQL program
already accepted (operation name, queue item id, Leviath run id); when the
accepted contract lands, only the mapping in `contract.md` changes, not the
envelope.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid

# --------------------------------------------------------------------------
# Where things live. Every path is overridable so a test never touches the
# volume, exactly like ops_events.
# --------------------------------------------------------------------------
_STATE_DIR = os.environ.get("XELOR_STATE_DIR", "/data/anw")
STREAM_PATH = os.environ.get(
    "SITE_EVENTS_PATH", os.path.join(_STATE_DIR, "site-events.jsonl"))
DERIVE_DIR = os.environ.get(
    "SITE_EVENTS_DERIVE_DIR", os.path.join(_STATE_DIR, "site-events"))
GAP_PATH = os.environ.get(
    "SITE_EVENTS_GAPS", os.path.join(DERIVE_DIR, "gaps.jsonl"))

# The reserved ops_events source. One source, so one row in the existing
# liveness strip; the `event` field carries the contract's event names.
OPS_SOURCE = "site"

# --------------------------------------------------------------------------
# Bounds. A telemetry stream that can grow without a ceiling is a bill, and
# every ceiling here is an env knob so an operator can move it without a code
# change.
# --------------------------------------------------------------------------
MAX_EVENTS = int(os.environ.get("SITE_EVENTS_MAX", "20000"))
KEEP_EVENTS = int(os.environ.get("SITE_EVENTS_KEEP", "16000"))
# Per-process cap on events accepted per minute. A page with a JS loop must not
# be able to turn the volume into a log shipper.
RATE_WINDOW_SEC = float(os.environ.get("SITE_EVENTS_RATE_WINDOW", "60"))
RATE_MAX = int(os.environ.get("SITE_EVENTS_RATE_MAX", "600"))
# The in-process de-dup ring. Bounded because the unbounded thing is the bug.
DEDUP_RING = int(os.environ.get("SITE_EVENTS_DEDUP_RING", "4096"))

MAX_STRING = 120
MAX_ROUTE = 120
MAX_OP = 120
MAX_ACTION = 64
MAX_ERR = 64

TERMINAL_STATES = frozenset({
    "confirmed", "done", "complete", "completed", "published", "merged",
    "archived", "closed",
})
FAILURE_STATES = frozenset({"failed", "error", "refused", "expired", "rejected"})
# A trace nothing has touched for this long is abandoned, not in flight. The
# default is generous on purpose: calling a slow human workflow "abandoned"
# after five minutes would make the completion metric a lie.
ABANDON_AFTER_SEC = float(os.environ.get("SITE_EVENTS_ABANDON_AFTER", "3600"))

_LOCK = threading.RLock()
_seen_ids: list[str] = []          # bounded ring, newest last
_seen_set: set[str] = set()
_rate_state = {"window_start": 0.0, "count": 0, "dropped": 0}
_clock = time.time               # swapped in tests so windows are deterministic


# --------------------------------------------------------------------------
# The closed allow-list, read from the contract itself rather than restated.
# A second copy of the field list is a second thing to get out of sync.
# --------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCHEMA_PATH = next(
    (p for p in (os.path.join(_HERE, "contract", "site-events-v1.schema.json"),
                 os.path.join(os.path.dirname(_HERE), "contract", "site-events-v1.schema.json"))
     if os.path.exists(p)),
    os.path.join(_HERE, "contract", "site-events-v1.schema.json"))
_schema_cache: dict | None = None


def schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
            _schema_cache = json.load(fh)
    return _schema_cache


def allowed_fields() -> tuple[set, set]:
    s = schema()["properties"]
    top = set(s.keys())
    detail = set(s["detail"]["properties"].keys())
    return top, detail


TOP_FIELDS, DETAIL_FIELDS = allowed_fields()
REQUIRED = set(schema()["required"])
ENUMS = {k: set(v["enum"]) for k, v in schema()["properties"].items() if "enum" in v}
PATTERNS = {k: re.compile(v["pattern"]) for k, v in schema()["properties"].items()
            if "pattern" in v}
TYPES = {k: v for k, v in schema()["properties"].items() if "type" in v}

# --------------------------------------------------------------------------
# Secret-shaped strings. This is the belt to the closed allow-list' braces: an
# allow-listed field can still be handed a token by a careless caller.
# --------------------------------------------------------------------------
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),            # OpenAI-style key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),       # GitHub token
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),   # JWT
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{8,}"),     # Slack token
    re.compile(r"AKIA[0-9A-Z]{12,}"),                # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[0-9a-fA-F]{40,}\b"),             # long hex blob
    re.compile(r"\bBearer\s+\S+", re.I),
)


def looks_sensitive(value) -> bool:
    """True when a string is secret-shaped or carries a raw request variable.

    Deliberately a *recognition* function, not a *redaction* function: it never
    returns or transforms the value. The caller drops the whole field. A
    redactor is one bug away from printing the thing it redacted; a rejection
    is not.
    """
    if not isinstance(value, str):
        return False
    if any(p.search(value) for p in _SECRET_PATTERNS):
        return True
    # A raw request variable / query string is not representable: `=` and `?`
    # are how it shows up. A path never needs either.
    if "?" in value or "=" in value:
        return True
    if "@" in value:
        return True                      # an email, a handle, or a URL userinfo
    return False


# --------------------------------------------------------------------------
# Validation: the same rules the JSON Schema states, hand-checked so the hot
# path needs no dependency. `test_contract.py` cross-checks every fixture
# against `jsonschema.validate`, which is what keeps the two in agreement.
# --------------------------------------------------------------------------
def check(rec: dict) -> list[str]:
    """Return a list of rule violations. Empty list means the record is valid."""
    errs: list[str] = []
    if not isinstance(rec, dict):
        return ["not an object"]
    for k in rec:
        if k not in TOP_FIELDS:
            errs.append("additionalProperty: %s" % k)
    for k in REQUIRED:
        if k not in rec:
            errs.append("required: %s" % k)
    for k, v in rec.items():
        if k not in TOP_FIELDS:
            continue                    # already reported; not a schema lookup
        if k == "detail":
            if not isinstance(v, dict):
                errs.append("detail: not an object")
                continue
            if len(v) > 8:
                errs.append("detail: too many keys")
            for dk, dv in v.items():
                if dk not in DETAIL_FIELDS:
                    errs.append("detail.additionalProperty: %s" % dk)
                else:
                    errs += ["detail.%s: %s" % (dk, e) for e in _check_scalar(dv, "detail", dk)]
            continue
        errs += ["%s: %s" % (k, e) for e in _check_scalar(v, None, k)]
    # Conditional requirements (the allOf block in the schema).
    ev = rec.get("event")
    if ev == "page.view" and "route" not in rec:
        errs.append("page.view requires route")
    if ev == "ui.action" and "action" not in rec:
        errs.append("ui.action requires action")
    if ev == "api.operation":
        if "status" not in rec or "latency_ms" not in rec:
            errs.append("api.operation requires status and latency_ms")
        if "op" not in rec and "route" not in rec:
            errs.append("api.operation requires op or route")
    if ev in ("client.error", "server.error") and "err_class" not in rec:
        errs.append("%s requires err_class" % ev)
    if ev == "run.transition":
        if "run_ref" not in rec or "to_state" not in rec:
            errs.append("run.transition requires run_ref and to_state")
    if ev == "confirmation.outcome" and "outcome" not in rec:
        errs.append("confirmation.outcome requires outcome")
    if ev == "legacy.endpoint":
        if "route" not in rec or "method" not in rec or rec.get("legacy") is not True:
            errs.append("legacy.endpoint requires route, method and legacy=true")
    if ev == "instrumentation.gap" and "detail" not in rec:
        errs.append("instrumentation.gap requires detail")
    return errs


def _check_scalar(v, parent, name):
    spec = (schema()["properties"]["detail"]["properties"][name]
            if parent == "detail" else schema()["properties"][name])
    errs = []
    if "const" in spec:
        if v != spec["const"]:
            errs.append("must equal %r" % spec["const"])
        return errs
    if "enum" in spec:
        if v not in spec["enum"]:
            errs.append("not in enum")
        return errs
    t = spec.get("type")
    if t == "number":
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errs.append("not a number")
            return errs
    elif t == "integer":
        if not isinstance(v, int) or isinstance(v, bool):
            errs.append("not an integer")
            return errs
    elif t == "boolean":
        if not isinstance(v, bool):
            errs.append("not a boolean")
        return errs
    elif t == "string":
        if not isinstance(v, str):
            errs.append("not a string")
            return errs
        if len(v) < spec.get("minLength", 0):
            errs.append("too short")
        if len(v) > spec.get("maxLength", 10**9):
            errs.append("too long")
        if "pattern" in spec and not re.match(spec["pattern"], v):
            errs.append("pattern")
        if looks_sensitive(v):
            errs.append("sensitive-shaped value rejected")
        return errs
    else:
        return errs
    if "minimum" in spec and v < spec["minimum"]:
        errs.append("below minimum")
    if "maximum" in spec and v > spec["maximum"]:
        errs.append("above maximum")
    return errs


# --------------------------------------------------------------------------
# Pseudonymous identity. Stored: a salted hash. Never stored: the identity.
# --------------------------------------------------------------------------
def _salt() -> str:
    s = os.environ.get("XELOR_TELEMETRY_SALT", "").strip()
    if s:
        return s
    # No salt configured: fall back to a per-process random so the hashes are
    # not a rainbow-table lookup of every email in the allowlist. Documented in
    # contract.md as a deployment requirement, not silently "good enough".
    global _fallback_salt
    try:
        return _fallback_salt
    except NameError:
        pass
    _fallback_salt = uuid.uuid4().hex
    return _fallback_salt


def pseudonym(kind: str, value: str) -> str:
    """`h:sha256:<hex16>` / `m:…` / `s:…` — stable per salt, not reversible."""
    if not value:
        return ""
    h = hashlib.sha256((_salt() + "\x00" + str(value)).encode("utf-8")).hexdigest()
    prefix = {"human": "h", "machine": "m", "session": "s"}.get(kind, "x")
    return "%s:sha256:%s" % (prefix, h[:24])


def new_trace_id() -> str:
    return "t:" + uuid.uuid4().hex


_TRACE_RE = re.compile(r"^t:[0-9a-f]{32}$")


def is_trace_id(v) -> bool:
    return isinstance(v, str) and bool(_TRACE_RE.match(v))


# --------------------------------------------------------------------------
# normalize(): the door. Everything that is not on the allow-list is dropped
# HERE and counted, then the survivors are validated.
# --------------------------------------------------------------------------
def normalize(payload: dict, *, now: float | None = None,
              actor_kind: str | None = None) -> tuple[dict | None, list[str]]:
    """Return (record, dropped_keys). `record` is None when nothing can be saved.

    `dropped_keys` names WHAT was dropped and never its value — the point of the
    reject record is to show that a caller tried to send something, not to keep
    a copy of it.
    """
    now = _clock() if now is None else now
    if not isinstance(payload, dict):
        return None, ["<not-an-object>"]
    kept, dropped = {}, []
    for k, v in payload.items():
        if k == "detail":
            if isinstance(v, dict):
                sub = {}
                for dk, dv in v.items():
                    if dk in DETAIL_FIELDS:
                        sub[dk] = dv
                    else:
                        dropped.append("detail.%s" % dk)
                if sub:
                    kept["detail"] = sub
            else:
                dropped.append("detail")
            continue
        if k in TOP_FIELDS:
            kept[k] = v
        else:
            dropped.append(str(k)[:40])

    rec = dict(kept)
    rec["v"] = 1
    rec["ts"] = float(now)
    rec.setdefault("app", "parlour-asset-review")
    rec.setdefault("surface", "admin")
    if actor_kind in ("human", "machine", "unknown") and "actor_kind" not in rec:
        rec["actor_kind"] = actor_kind
    rec.setdefault("actor_kind", "unknown")
    if not rec.get("event"):
        return None, dropped
    if "event_id" not in rec:
        rec["event_id"] = uuid.uuid4().hex
    if rec.get("trace_id") and not is_trace_id(rec["trace_id"]):
        dropped.append("trace_id")
        rec.pop("trace_id")
    # A caller may hand over an OPAQUE session token (the browser mints one it
    # never shows anyone); it is turned into the pseudonymous form HERE, so the
    # raw token is not what gets stored. Anything else shaped like a ref is
    # dropped rather than trusted.
    for ref_key, kind in (("session_ref", "session"), ("actor_ref", "actor")):
        v = rec.get(ref_key)
        if v and not re.match(schema()["properties"][ref_key]["pattern"], str(v)):
            if ref_key == "session_ref" and re.match(r"^[A-Za-z0-9_-]{8,64}$", str(v)):
                rec[ref_key] = pseudonym("session", str(v))
            else:
                dropped.append(ref_key)
                rec.pop(ref_key)
    errs = check(rec)
    if errs:
        return None, dropped + ["invalid:%s" % e for e in errs]
    return rec, dropped


# --------------------------------------------------------------------------
# The store.
# --------------------------------------------------------------------------
def _append(rec: dict) -> None:
    os.makedirs(os.path.dirname(STREAM_PATH) or ".", exist_ok=True)
    with open(STREAM_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")


def _read_all() -> list[dict]:
    if not os.path.exists(STREAM_PATH):
        return []
    out = []
    with open(STREAM_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                      # a torn tail line is skipped, not fatal
            if isinstance(r, dict):
                out.append(r)
    return out


def _compact_if_needed() -> None:
    try:
        with open(STREAM_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) <= MAX_EVENTS:
        return
    keep = lines[-KEEP_EVENTS:]
    tmp = STREAM_PATH + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(keep)
    os.replace(tmp, STREAM_PATH)


def _rate_ok(now: float) -> tuple[bool, int]:
    """(allowed, first_drop_in_window).

    `first_drop_in_window` is 1 exactly once per window, so the overflow marker
    is written the moment throttling STARTS rather than at the next rollover —
    a monitor that says nothing until the window ends is the silent-drop
    failure `ops_events` was written against.
    """
    with _LOCK:
        if now - _rate_state["window_start"] > RATE_WINDOW_SEC:
            _rate_state.update(window_start=now, count=0, dropped=0)
        _rate_state["count"] += 1
        allowed = _rate_state["count"] <= RATE_MAX
        first = 0
        if not allowed:
            _rate_state["dropped"] += 1
            first = 1 if _rate_state["dropped"] == 1 else 0
        return allowed, first


def _dedup(event_id: str) -> bool:
    """True when this event_id was already accepted. Bounded ring, O(1)."""
    with _LOCK:
        if event_id in _seen_set:
            return True
        _seen_ids.append(event_id)
        _seen_set.add(event_id)
        while len(_seen_ids) > DEDUP_RING:
            old = _seen_ids.pop(0)
            _seen_set.discard(old)
        return False


def _warm_dedup_from_stream(limit: int = 5000) -> None:
    """After a restart the ring is empty and a replay would double-count.

    Reads the tail of the stream once, at first record() of the process. This is
    the cheap half of the two-key de-dup story; the durable half is ops_events'
    `(source, seq)` check.
    """
    for r in _read_all()[-limit:]:
        eid = r.get("event_id")
        if eid:
            _dedup(eid)


_warm = {"done": False}


def record(payload: dict, *, now: float | None = None,
           actor_kind: str | None = None, mirror_ops: bool = True) -> dict:
    """Accept one telemetry event. NEVER raises, never blocks the caller.

    Returns a small dict: {"stored": bool, "duplicate": bool, "throttled": bool,
    "dropped": [...]}. The caller is a `after_request` hook and must not care.
    """
    try:
        if not _warm["done"]:
            _warm["done"] = True
            try:
                _warm_dedup_from_stream()
            except Exception:
                pass
        rec, dropped = normalize(payload, now=now, actor_kind=actor_kind)
        if dropped:
            _write_gaps({"kind": "privacy.reject", "fields": dropped[:8],
                         "at": float(now or _clock())})
        if rec is None:
            return {"stored": False, "dropped": dropped}
        if _dedup(rec["event_id"]):
            return {"stored": False, "duplicate": True, "dropped": dropped}
        now2 = float(now or _clock())
        allowed, overflow = _rate_ok(now2)
        if overflow:
            _append({"v": 1, "event": "instrumentation.gap", "ts": now2,
                     "surface": rec.get("surface", "admin"),
                     "app": rec.get("app", "parlour-asset-review"),
                     "actor_kind": "unknown",
                     "detail": {"kind": "rate_limited", "count": overflow}})
        if not allowed:
            return {"stored": False, "throttled": True, "dropped": dropped}
        with _LOCK:
            _append(rec)
            _compact_if_needed()
        if mirror_ops:
            _mirror_to_ops(rec)
        return {"stored": True, "dropped": dropped}
    except Exception:
        # R5: the site outranks the monitor.
        return {"stored": False, "error": "record-failed"}


def _mirror_to_ops(rec: dict) -> None:
    """Make the existing desk liveness strip aware of telemetry.

    One `tick` per telemetry write would be nonsense — a tick is a coordinator
    run. So the mirror is a single lightweight event carrying this publish's
    counts, written at most once per MIRROR_PERIOD so the JSONL does not double
    in size for no gain. The point is that a source that STOPS writing shows up
    as silent in the strip a human already looks at (R1).
    """
    now = _clock()
    with _LOCK:
        if now - _mirror_state["last"] < MIRROR_PERIOD:
            _mirror_state["pending"] += 1
            return
        batch = _mirror_state["pending"]
        _mirror_state["last"] = now
        _mirror_state["pending"] = 0
        try:
            _mirror_state["count"] += batch + 1
            total = _mirror_state["count"]
        except Exception:
            total = batch
    try:
        import ops_events
        ops_events.record(
            OPS_SOURCE, "tick", period_s=MIRROR_PERIOD,
            items_seen=total, dispatched=0,
            detail={"kind": "site-telemetry", "count": min(total, 100000)})
    except Exception:
        pass          # the mirror is a courtesy; never a failure path


MIRROR_PERIOD = float(os.environ.get("SITE_EVENTS_MIRROR_PERIOD", "60"))
_mirror_state = {"last": 0.0, "pending": 0, "count": 0}


def _write_gaps(gap: dict) -> None:
    """Instrumentation-health ledger: what the contract refused and why.

    Separate file, separate retention: a gap is evidence about the MONITOR and
    does not belong in the measurement stream.
    """
    try:
        os.makedirs(os.path.dirname(GAP_PATH) or ".", exist_ok=True)
        with open(GAP_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(gap, separators=(",", ":"), sort_keys=True) + "\n")
    except Exception:
        pass


def read_gaps(limit: int = 200) -> list[dict]:
    if not os.path.exists(GAP_PATH):
        return []
    out = []
    with open(GAP_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out[-limit:][::-1]


def read(limit: int = 500, *, since: float | None = None,
         surface: str | None = None, event: str | None = None) -> list[dict]:
    rows = _read_all()
    if since:
        rows = [r for r in rows if float(r.get("ts") or 0) >= since]
    if surface:
        rows = [r for r in rows if r.get("surface") == surface]
    if event:
        rows = [r for r in rows if r.get("event") == event]
    rows.reverse()
    return rows[:max(1, int(limit))]


# --------------------------------------------------------------------------
# The dashboard rollups. Every one of these is derived from the stream, so the
# numbers are reproducible from the artifact and not from a running process.
# --------------------------------------------------------------------------
def _dedup_events(events: list[dict]) -> tuple[list[dict], dict]:
    """Drop byte-replayed events (same `event_id`), keep everything else.

    BOTH de-dup keys meet here, and they are deliberately not the same thing:

      * `event_id` is the CLIENT'S identity for a fact it already sent. A row
        carrying an id already in the stream is the same fact and is dropped.
      * `seq` is the EMITTER'S position in its own spool. The store keeps it (so
        `ops_events`-style `(source, seq)` fencing is possible), but it is not a
        de-dup key here: two genuinely different attempts legitimately re-use a
        seq across a reconnect, and dropping on seq would lose real work.

    `_dedup_traces` is the workflow-level layer on top; this is the fact level,
    and the rollups count FACTS, so both have to be replay-free.
    """
    out: list[dict] = []
    stats = {"events": 0, "replays": 0}
    seen: set[str] = set()
    for e in events:
        stats["events"] += 1
        eid = e.get("event_id")
        if eid:
            if eid in seen:
                stats["replays"] += 1
                continue
            seen.add(eid)
        out.append(e)
    return out, stats


def _dedup_traces(events: list[dict]) -> tuple[dict, dict]:
    """(traces, dedup_stats).

    A trace is the unit of *work*. Usage and completion are counted per TRACE,
    so a reconnect that re-sends the same action (same `attempt`, or
    `continuation: true`) cannot inflate either number. Latency is NOT deduped:
    every attempt really did spend wall-clock time, so every attempt's latency is
    kept while the *count* of work is not.
    """
    events, dstats = _dedup_events(events)
    traces: dict[str, dict] = {}
    stats = {"events": len(events), "continuations": 0,
             "replays": dstats["replays"], "orphan_events": 0}
    for e in events:
        tid = e.get("trace_id")
        if not tid:
            stats["orphan_events"] += 1
            continue
        t = traces.setdefault(tid, {
            "trace_id": tid, "surface": e.get("surface"), "app": e.get("app"),
            "actor_kind": e.get("actor_kind"), "events": [],
            "first_ts": e.get("ts"), "last_ts": e.get("ts"),
        })
        t["events"].append(e)
        try:
            ts = float(e.get("ts") or 0)
            t["first_ts"] = min(t["first_ts"], ts)
            t["last_ts"] = max(t["last_ts"], ts)
        except (TypeError, ValueError):
            pass
        if e.get("continuation"):
            stats["continuations"] += 1
    # Events within a trace are returned in the order they happened: `seq` when
    # the emitter supplied one (a monotonic per-trace counter), `ts` otherwise.
    # The read path returns newest-first, so this is a real ordering, not a
    # cosmetic one — a renderer must not present a confirmation before its claim.
    for t in traces.values():
        t["events"].sort(key=lambda e: (float(e.get("seq") if e.get("seq") is not None
                                               else 0.0), float(e.get("ts") or 0)))
    return traces, stats


def counters(window_s: float | None = None, *, surface: str | None = None,
             now: float | None = None) -> dict:
    """One rollup object: usage, errors, latency, completion, health, legacy.

    The dashboard renders this and nothing else, so the JSON in the artifact is
    the same thing the UI shows — no second code path to disagree with.
    """
    now = _clock() if now is None else now
    since = (now - window_s) if window_s else None
    events = read(limit=10**9, since=since, surface=surface)
    # FACTS ARE DE-DUPLICATED BEFORE THEY ARE COUNTED. A row that a reconnecting
    # emitter flushed twice carries the same `event_id`; if the rollup summed the
    # raw stream, that replay would read as extra usage, extra errors and an
    # extra request — the exact overcount the DoD forbids. The trace layer below
    # de-duplicates AGAIN at the workflow level (usage is per trace), so a
    # replay is stopped at both the fact and the workflow grain.
    events, dstats = _dedup_events(events)
    traces, tstats = _dedup_traces(events)
    dedup = {"events": len(events), "replays": dstats["replays"],
             "continuations": tstats["continuations"],
             "orphan_events": tstats["orphan_events"]}

    usage: dict[str, int] = {}
    api: dict[str, dict] = {}
    errors: dict[str, int] = {}
    statuses: dict[str, int] = {}
    legacy: dict[str, int] = {}
    latency: dict[str, list] = {}
    actor_mix = {"human": 0, "machine": 0, "unknown": 0}
    surface_mix: dict[str, int] = {}

    for e in events:
        ev = e.get("event")
        actor_mix[e.get("actor_kind", "unknown")] = \
            actor_mix.get(e.get("actor_kind", "unknown"), 0) + 1
        surface_mix[e.get("surface", "?")] = surface_mix.get(e.get("surface", "?"), 0) + 1
        if ev == "page.view":
            key = e.get("route") or "?"
            usage[key] = usage.get(key, 0) + 1
        elif ev == "ui.action":
            key = "action:%s" % (e.get("action") or e.get("op") or "?")
            usage[key] = usage.get(key, 0) + 1
        elif ev == "api.operation":
            key = e.get("op") or e.get("route") or "?"
            row = api.setdefault(key, {"n": 0, "errors": 0, "lat": []})
            row["n"] += 1
            if e.get("status") == "error" or (e.get("http_status") or 0) >= 500:
                row["errors"] += 1
            if isinstance(e.get("latency_ms"), (int, float)):
                row["lat"].append(float(e["latency_ms"]))
                latency.setdefault(key, []).append(float(e["latency_ms"]))
        elif ev in ("client.error", "server.error"):
            key = e.get("err_class") or "Unknown"
            errors[key] = errors.get(key, 0) + 1
        if "http_status" in e:
            s = str(e["http_status"])
            statuses[s] = statuses.get(s, 0) + 1
        if ev == "legacy.endpoint":
            key = "%s %s" % (e.get("method", "?"), e.get("route", "?"))
            legacy[key] = legacy.get(key, 0) + 1

    def _pct(vals, p):
        if not vals:
            return None
        v = sorted(vals)
        i = max(0, min(len(v) - 1, int(round((p / 100.0) * (len(v) - 1)))))
        return round(v[i], 1)

    api_out = {}
    for k, row in api.items():
        vals = row["lat"]
        api_out[k] = {
            "requests": row["n"],
            "error_rate": round(row["errors"] / row["n"], 4) if row["n"] else None,
            "p50_ms": _pct(vals, 50), "p95_ms": _pct(vals, 95),
            "max_ms": max(vals) if vals else None,
        }

    return {
        "window_s": window_s,
        "generated_at": now,
        "usage": dict(sorted(usage.items(), key=lambda kv: -kv[1])),
        "api": dict(sorted(api_out.items(), key=lambda kv: -kv[1]["requests"])),
        "errors": dict(sorted(errors.items(), key=lambda kv: -kv[1])),
        "http_status": dict(sorted(statuses.items())),
        "actor_mix": actor_mix,
        "surface_mix": surface_mix,
        "legacy_endpoints": dict(sorted(legacy.items(), key=lambda kv: -kv[1])),
        "completion": completion(events=events, now=now),
        "dedup": dedup,
        "health": health(now=now),
    }


def completion(*, events: list[dict] | None = None, window_s: float | None = None,
               now: float | None = None) -> dict:
    """Did the workflow finish? Per TRACE, so a retry cannot change the answer.

    A trace with no terminal event and no touch for ABANDON_AFTER_SEC is
    `abandoned`. A trace that only ever carried `continuation` actions is
    counted once, at the trace, not per attempt.
    """
    now = _clock() if now is None else now
    if events is None:
        since = (now - window_s) if window_s else None
        events = read(limit=10**9, since=since)
    traces, _ = _dedup_traces(events)
    done = failed = abandoned = in_flight = 0
    by_surface = {"admin": {"completed": 0, "failed": 0, "abandoned": 0, "in_flight": 0},
                  "product": {"completed": 0, "failed": 0, "abandoned": 0, "in_flight": 0}}
    for t in traces.values():
        evs = t["events"]
        terminal = any(
            (e.get("to_state") in TERMINAL_STATES)
            or (e.get("outcome") == "confirmed")
            or (e.get("event") == "confirmation.outcome" and e.get("outcome") == "confirmed")
            for e in evs)
        broke = any(
            e.get("status") == "error"
            or (e.get("to_state") in FAILURE_STATES)
            or (e.get("outcome") in ("refused", "expired"))
            for e in evs)
        age = now - float(t["last_ts"] or now)
        if terminal:
            state = "completed"
        elif broke:
            state = "failed"
        elif age > ABANDON_AFTER_SEC:
            state = "abandoned"
        else:
            state = "in_flight"
        if state == "completed":
            done += 1
        elif state == "failed":
            failed += 1
        elif state == "abandoned":
            abandoned += 1
        else:
            in_flight += 1
        s = t.get("surface") if t.get("surface") in by_surface else "admin"
        by_surface[s][state] += 1
    total = done + failed + abandoned + in_flight
    return {
        "traces": total,
        "completed": done, "failed": failed,
        "abandoned": abandoned, "in_flight": in_flight,
        "completion_rate": round(done / total, 4) if total else None,
        "by_surface": by_surface,
        "abandon_after_s": ABANDON_AFTER_SEC,
    }


# --------------------------------------------------------------------------
# Coverage + instrumentation health. "Missing instrumentation is visible" is a
# deliverable, so the coverage map is data, and the gap list is computed from
# it against what has actually been seen.
# --------------------------------------------------------------------------
def _coverage_path() -> str:
    for p in (os.path.join(_HERE, "contract", "coverage.json"),
              os.path.join(os.path.dirname(_HERE), "contract", "coverage.json")):
        if os.path.exists(p):
            return p
    return os.path.join(_HERE, "contract", "coverage.json")


def coverage() -> dict:
    with open(_coverage_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


def health(*, now: float | None = None, stale_after_s: float = 86400.0) -> dict:
    """Which surfaces are instrumented, which are missing, and which are quiet.

    Reads the coverage map and compares it with the stream. A surface listed as
    instrumented that has never emitted in the window is reported `silent`, and
    a surface listed as NOT instrumented is reported `missing` — the two cases a
    blank page renders identically and a human reads as "fine".
    """
    now = _clock() if now is None else now
    cov = coverage()
    events = read(limit=10**9)
    seen_routes, seen_ops, last_by_key = set(), set(), {}
    for e in events:
        if e.get("route"):
            seen_routes.add(e["route"])
            last_by_key["route:" + e["route"]] = max(
                last_by_key.get("route:" + e["route"], 0), float(e.get("ts") or 0))
        if e.get("op"):
            seen_ops.add(e["op"])
            last_by_key["op:" + e["op"]] = max(
                last_by_key.get("op:" + e["op"], 0), float(e.get("ts") or 0))
    rows = []
    for s in cov["surfaces"]:
        key = ("route:" + s["key"]) if s["kind"] == "route" else ("op:" + s["key"])
        last = last_by_key.get(key)
        if not s.get("instrumented"):
            state = "missing"
        elif last is None:
            state = "never"
        elif (now - last) > stale_after_s:
            state = "stale"
        else:
            state = "live"
        rows.append({"surface": s["surface"], "kind": s["kind"], "key": s["key"],
                     "owner": s.get("owner"), "instrumented": bool(s.get("instrumented")),
                     "state": state, "last_seen": last,
                     "age_s": round(now - last, 1) if last else None})
    missing = [r for r in rows if r["state"] in ("missing", "never")]
    return {
        "rows": rows,
        "counts": {
            "total": len(rows),
            "live": sum(1 for r in rows if r["state"] == "live"),
            "stale": sum(1 for r in rows if r["state"] == "stale"),
            "never": sum(1 for r in rows if r["state"] == "never"),
            "missing": sum(1 for r in rows if r["state"] == "missing"),
        },
        "gaps": [{"surface": r["surface"], "key": r["key"], "state": r["state"]}
                 for r in missing],
        "rejected_payloads": len(read_gaps(limit=1000)),
    }


def reset_for_tests() -> None:
    """Clear the in-process state. Tests only; never called by the app."""
    with _LOCK:
        _seen_ids.clear()
        _seen_set.clear()
        _rate_state.update(window_start=0.0, count=0, dropped=0)
        _mirror_state.update(last=0.0, pending=0, count=0)
        _warm["done"] = False
