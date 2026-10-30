"""SITE TELEMETRY WIRING — the isolated mount for `site_events`.

Owner: GQL-35 `telemetry-worker`. This is the ONLY file that touches the Flask
app, and it touches it in exactly three additive ways:

  1. `before_request` / `after_request` hooks that time the request and record
     `api.operation` for instrumented endpoints,
  2. one new route, `POST /api/site/event` (contract v1 batch ingest) and one
     read route `GET /api/site/telemetry` for the dashboard,
  3. a static path for `static/js/site_events.js`.

It never edits an existing route, never changes an existing response, and the
whole thing is off with one environment variable (`SITE_TELEMETRY_DISABLED=1`),
which is the rollback the work order asks for: "disable instrumentation through
an existing configuration path".

WHY A MOUNT RATHER THAN EDITS IN main.py
----------------------------------------
`main.py` is a 74 KB single-writer file with an OAuth guard whose semantics are
the security boundary of the whole site. GQL-00's inventory records that the
`_guard` allow-lists (`_SYNC_GET` / `_SYNC_POST`) are the machine surface. Adding
telemetry by editing that file means every later diff of it carries a telemetry
hunk, which is how an instrumentation change quietly becomes a security review.
The mount is additive, and rollback is `CALL register(app)` -> delete the line.

THE GUARD, AND WHY THE INGEST ROUTE IS SAFE UNDER IT
----------------------------------------------------
`register()` runs AFTER `main.py` has installed its `before_request` guard, so
`/api/site/event` is OAuth-gated exactly like every other human surface: the
browser that emits the event is an authenticated browser. That is intentional —
telemetry must not become the one unauthenticated POST on the site. The cost is
that a machine caller needs a Google session; the benefit is that "who is
sending this" is answered by the guard and not by a field.
"""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("xelor-admin.telemetry")

# Endpoints whose latency is measured by the hook. An allow-list, not a
# deny-list: an endpoint added tomorrow is unmeasured until it is named, which
# is the safe direction (an unmeasured route reads as `missing`, never as
# "fine").
INSTRUMENTED_ENDPOINTS = {
    "index", "work", "anw", "desk.api_list", "desk.api_thread", "desk.api_status",
    "desk.api_board", "queue.queue_list", "queue.queue_stats", "proposals.index",
    "proposals.draft_brief", "mockup_review.review", "mockup_review.api_desks",
    "devtools_index", "settings", "simple_city.index", "kit_asset",
    "graphql_server",
}
# Endpoints that are usage on a RETIRED surface. Counted so their retirement is
# a number rather than an opinion; never a blocker.
LEGACY_ENDPOINTS = {
    "runthrough": "/runthrough",
    "parlour": "/parlour",
    "anw_legacy": "/anw/legacy",
    "anw_legacy_slug": "/anw/legacy",
}
# Probe/liveness endpoints: measuring them measures the operator, not the site.
SKIP_ENDPOINTS = {
    "static", "api_health", "api_version", "api_selfcheck", "api_ops_event",
    "api_ops_liveness", "api_agent_clock", "site_event", "site_telemetry",
    "login", "logout", "oauth2callback", "oauth2callback_alias",
}

# A GraphQL body's OPERATION NAME is `query Name(...)` / `mutation Name`. The
# name is read; the selection set, the variables and the result are not. The
# regex is anchored to the keyword so a document that opens with a fragment or a
# comment simply yields no name (and the route is recorded instead).
_OP_RE = re.compile(r"^\s*(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)")
_ANON_OP_RE = re.compile(r"^\s*(query|mutation|subscription)\s*[\{\(]")

_client = None          # the site_events module, imported lazily so an import
                        # failure degrades to "no telemetry" not "no site"


def _events():
    global _client
    if _client is None:
        import site_events
        _client = site_events
    return _client


def gql_operation(payload) -> str | None:
    """The operation NAME out of a parsed GraphQL body, or None. Bounded.

    Deliberately reads `body["query"]` and stops at the name: the variables map
    is never touched, which is what makes "no raw request variables" true by
    construction on the server side rather than by a filter.
    """
    try:
        if not isinstance(payload, dict):
            return None
        q = payload.get("query")
        if not isinstance(q, str):
            return None
        m = _OP_RE.match(q)
        if m:
            return m.group(1)[:120]
        if _ANON_OP_RE.match(q):
            return "anonymous"
        return None
    except Exception:
        return None


def classify_actor(request) -> str:
    """`machine` when the caller used the sync token, `human` otherwise.

    The distinction is drawn from HOW the call authenticated, not from a
    self-declared field: a caller cannot label itself machine to dodge the
    usage numbers.
    """
    try:
        from flask import request as _r
        expected = os.environ.get("XELOR_SYNC_TOKEN", "").strip()
        got = (_r.headers.get("X-Xelor-Sync-Token") or _r.args.get("token") or "").strip()
        if expected and got and len(got) == len(expected):
            import secrets
            if secrets.compare_digest(got, expected):
                return "machine"
    except Exception:
        pass
    return "human"


def register(app, *, disable_env: str = "SITE_TELEMETRY_DISABLED") -> dict:
    """Install the hooks and routes. Returns a small status dict for logging.

    Idempotent by marker attribute: mounting twice (a test that imports the app
    twice, a reloader) does not double-count requests.
    """
    if getattr(app, "_site_telemetry_mounted", False):
        return {"mounted": False, "reason": "already-mounted"}
    disabled = os.environ.get(disable_env, "").strip().lower() in {"1", "true", "yes", "on"}
    if disabled:
        log.info("SITE TELEMETRY disabled by %s", disable_env)
        return {"mounted": False, "reason": "disabled"}

    from flask import jsonify, request, send_from_directory

    try:
        ev = _events()
    except Exception:
        log.exception("SITE TELEMETRY not mounted: site_events failed to import")
        return {"mounted": False, "reason": "import-failed"}

    @app.before_request
    def _site_telemetry_start():                       # noqa: ANN001
        try:
            request.environ["xelor.telemetry.t0"] = time.perf_counter()
        except Exception:
            pass

    @app.after_request
    def _site_telemetry_after(resp):                   # noqa: ANN001
        # R5: nothing in here may break a response. A bare except is the point.
        try:
            ep = request.endpoint or ""
            if ep in SKIP_ENDPOINTS or ep.startswith("site_"):
                return resp
            t0 = request.environ.get("xelor.telemetry.t0")
            latency_ms = (time.perf_counter() - t0) * 1000.0 if t0 else None
            actor = classify_actor(request)
            route = request.path
            op = None
            if ep == "graphql_server":
                op = gql_operation(request.get_json(silent=True))
            if ep in LEGACY_ENDPOINTS:
                ev.record({"event": "legacy.endpoint",
                           "route": LEGACY_ENDPOINTS[ep],
                           "method": request.method, "legacy": True,
                           "actor_kind": actor,
                           "http_status": int(resp.status_code)})
            if ep in INSTRUMENTED_ENDPOINTS or ep in LEGACY_ENDPOINTS:
                payload = {
                    "event": "api.operation",
                    "op": op or None,
                    "route": route,
                    "method": request.method,
                    "op_type": "mutation" if _is_mutation(request, op) else "query",
                    "status": "ok" if resp.status_code < 400 else "error",
                    "http_status": int(resp.status_code),
                    "latency_ms": round(latency_ms, 3) if latency_ms else 0.0,
                    "actor_kind": actor,
                    "trace_id": _trace_id_from(request),
                    "session_ref": _session_ref(request),
                }
                ev.record({k: v for k, v in payload.items() if v is not None})
        except Exception:
            pass
        return resp

    @app.post("/api/site/event")
    def site_event():                                  # noqa: ANN202
        """Contract v1 batch ingest. Answers 200 for anything parseable.

        A telemetry endpoint that 4xx's at its caller teaches the caller to stop
        calling — the same rule `/api/ops/event` states at length. Auth is the
        app's existing guard, which runs before this.
        """
        body = request.get_json(silent=True) or {}
        batch = body.get("events") if isinstance(body.get("events"), list) else [body]
        stored = dup = throttled = 0
        dropped: list[str] = []
        actor = classify_actor(request)
        for item in batch[:200]:
            if not isinstance(item, dict):
                continue
            res = ev.record(item, actor_kind=actor)
            if res.get("duplicate"):
                dup += 1
            elif res.get("throttled"):
                throttled += 1
            elif res.get("stored"):
                stored += 1
            dropped += list(res.get("dropped") or [])[:4]
        return jsonify({"ok": True, "stored": stored, "duplicates": dup,
                        "throttled": throttled, "dropped_fields": dropped[:8]})

    @app.get("/api/site/telemetry")
    def site_telemetry():                              # noqa: ANN202
        """The dashboard's data. Human surface: OAuth-gated like every page.

        `?window=` is the time filter (seconds; 0/absent = all retained).
        `?surface=admin|product` narrows to one site. Carries counts only —
        no route bodies, no reasons, no identities.
        """
        try:
            window = float(request.args.get("window") or 0) or None
        except ValueError:
            window = None
        surface = request.args.get("surface") or None
        if surface not in (None, "admin", "product"):
            surface = None
        c = ev.counters(window_s=window, surface=surface)
        c["recent"] = ev.read(limit=50, surface=surface)
        c["rejected"] = ev.read_gaps(limit=20)
        return jsonify(c)

    @app.get("/static/js/site_events.js")
    def site_events_js():                              # noqa: ANN202
        """Serve the beacon from the repo tree.

        `/static/...` is already open to a page's own dependencies, and this is a
        dependency's dependency; it carries no content, no key and no endpoint.
        """
        return send_from_directory(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js"),
            "site_events.js", mimetype="application/javascript")

    app._site_telemetry_mounted = True
    log.info("SITE TELEMETRY mounted at POST /api/site/event + GET /api/site/telemetry "
             "(contract site-events-v1; admin surfaces instrumented; "
             "product surfaces reported missing in the coverage map)")
    return {"mounted": True, "contract": "site-events-v1"}


def _is_mutation(request, op: str | None) -> bool:
    try:
        if request.method != "POST":
            return False
        q = (request.get_json(silent=True) or {}).get("query")
        return isinstance(q, str) and bool(re.match(r"^\s*mutation", q))
    except Exception:
        return False


def _trace_id_from(request) -> str | None:
    """Only an EXISTING, well-formed trace id is carried; none is invented here.

    A server-minted trace id would join nothing: the browser is the only party
    that knows two requests belong to one workflow.
    """
    try:
        t = request.headers.get("X-Xelor-Trace") or request.args.get("_trace") or ""
        return t if _events().is_trace_id(t) else None
    except Exception:
        return None


def _session_ref(request) -> str | None:
    """A pseudonym of the SESSION COOKIE, computed server-side.

    The browser never sends, and never sees, an identity. What is stored is a
    salted hash prefix with no way back to the cookie value. A caller that
    supplies its own `session_ref` is ignored here: the server's own derivation
    is the one that is safe to trust.
    """
    try:
        raw = request.cookies.get("session")
        if not raw:
            return None
        return _events().pseudonym("session", raw)
    except Exception:
        return None
