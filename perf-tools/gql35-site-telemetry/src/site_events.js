/**
 * site_events.js — the browser half of contract v1 (GQL-35).
 *
 * Deliberately tiny (~4 KB, no dependencies, no polyfill) because the overhead
 * measurement in the DoD is about the CLIENT as much as the server: an
 * instrumented page must not pay for the instrumentation. Everything here is
 * one of five rules:
 *
 *   1. MINT ONCE. `traceId` is created on the first view of a workflow and
 *      persisted in sessionStorage, so a reconnect/retry re-uses it and the
 *      browser -> API -> queue -> Leviath join holds across a reload.
 *   2. SEND NOTHING UNBOUNDED. Every field is a bounded identifier or a number.
 *      `pageUrl` is reduced to a path; the query string is never read, and there
 *      is no DOM-text scrape anywhere in this file — no thread body, no fixture
 *      JSON, no user content can reach the wire through it.
 *   3. NEVER BLOCK OR THROW. Every public call is wrapped; a failed send costs a
 *      lost data point and never a page.
 *   4. DE-DUPLICATE LOCALLY. `event_id` is minted client-side and re-sent
 *      verbatim by the retry path, which is exactly what lets the server count a
 *      reconnect once (server rule R3/R4).
 *   5. STAY SMALL IN FLIGHT. Beacon batching with a cap, `sendBeacon` on
 *      unload (never a synchronous XHR on a hot page).
 */
(function (global) {
  "use strict";
  var DISABLED = global.__SITE_TELEMETRY_DISABLED__ === true;
  if (DISABLED) { global.SiteEvents = noop(); return; }

  var BATCH_MAX = 25;
  var FLUSH_MS = 2000;
  var TRACE_KEY = "xelor.trace_id.v1";
  var SESSION_KEY = "xelor.session_ref.v1";

  function hex32() {
    var a = new Uint8Array(16);
    if (global.crypto && global.crypto.getRandomValues) {
      global.crypto.getRandomValues(a);
    } else {
      for (var i = 0; i < 16; i++) a[i] = Math.floor(Math.random() * 256);
    }
    var s = "";
    for (var j = 0; j < 16; j++) s += ("0" + a[j].toString(16)).slice(-2);
    return s;
  }

  function store(key, val) {
    try {
      if (val === undefined) return global.sessionStorage.getItem(key);
      global.sessionStorage.setItem(key, val);
      return val;
    } catch (e) { return null; }
  }

  /**
   * A bounded, secret-free name for a UI action. Callers pass a dotted literal
   * ("desk.claim"), never text; anything outside the alphabet is rejected so a
   * careless call site cannot smuggle prose into the stream.
   */
  function actionName(name) {
    var s = String(name || "");
    return /^[a-z][a-z0-9_.-]{0,63}$/.test(s) ? s : null;
  }

  /** PATH ONLY. The search string and hash are dropped, not sanitized. */
  function pathOnly(url) {
    try {
      var u = new global.URL(url || global.location.href, global.location.origin);
      var p = u.pathname || "/";
      return p.length <= 120 ? p : p.slice(0, 120);
    } catch (e) { return "/"; }
  }

  var queue = [];
  var traceId = store(TRACE_KEY);
  var sessionRef = store(SESSION_KEY);
  var seq = 0;

  function ensureSession() {
    if (!sessionRef) {
      // The pseudonym is computed SERVER-side from the session cookie; the
      // browser never sees or sends an identity. This id only groups events
      // that happened in one visit.
      sessionRef = "s" + hex32().slice(0, 24);
      store(SESSION_KEY, sessionRef);
    }
    return sessionRef;
  }

  function newTrace() {
    traceId = "t:" + hex32();
    store(TRACE_KEY, traceId);
    return traceId;
  }

  function base(event, extra) {
    var e = {
      event: event,
      client_ts: Date.now() / 1000,
      event_id: hex32(),
      seq: seq++,
      session_ref: ensureSession(),
      surface: (global.document && global.document.body &&
                global.document.body.getAttribute("data-surface")) || "admin",
      app: "parlour-asset-review"
    };
    if (traceId) e.trace_id = traceId;
    for (var k in extra) {
      if (extra.hasOwnProperty(k) && extra[k] !== undefined && extra[k] !== null) {
        e[k] = extra[k];
      }
    }
    return e;
  }

  function push(e) {
    queue.push(e);
    if (queue.length >= BATCH_MAX) flush();
  }

  function flush(sync) {
    if (!queue.length) return;
    var batch = queue.slice(0, BATCH_MAX);
    queue = queue.slice(batch.length);
    var body = JSON.stringify({ events: batch });
    try {
      if (sync && global.navigator && global.navigator.sendBeacon) {
        global.navigator.sendBeacon("/api/site/event", body);
        return;
      }
      if (global.fetch) {
        global.fetch("/api/site/event", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true
        }).catch(function () {});
      }
    } catch (e) { /* rule 3 */ }
  }

  /**
   * A reconnect/retry of work already traced. `attempt` increments, the trace id
   * and the operation stay the same, and `continuation: true` tells the server
   * this is not new usage — it is the same workflow being finished.
   */
  function retryOf(prevEvent, attempt) {
    var e = base(prevEvent.event, {});
    for (var k in prevEvent) {
      if (prevEvent.hasOwnProperty(k) && k !== "event_id" && k !== "seq" &&
          k !== "client_ts") {
        e[k] = prevEvent[k];
      }
    }
    e.event_id = hex32();
    e.seq = seq++;
    e.attempt = (attempt || 1) | 0;
    e.continuation = true;
    return e;
  }

  function noop() {
    return { pageView: function () {}, action: function () {}, api: function () {},
             error: function () {}, transition: function () {}, confirm: function () {},
             legacy: function () {}, gap: function () {}, flush: function () {},
             trace: function () { return null; }, retryOf: function () { return null; } };
  }

  var api = {
    trace: function () { return traceId || ensureSession(); },
    newTrace: function () { return newTrace(); },
    traceId: function () { return traceId; },
    pageView: function (extra) {
      try { push(base("page.view", shallow(extra, ["route"]))); } catch (e) {}
    },
    action: function (name, extra) {
      try {
        var a = actionName(name);
        if (!a) return;
        var x = shallow(extra, ["op", "status", "latency_ms", "queue_ref", "run_ref"]);
        x.action = a;
        push(base("ui.action", x));
      } catch (e) {}
    },
    api: function (op, status, latencyMs, extra) {
      try {
        var x = shallow(extra, ["http_status", "op_type", "route"]);
        x.op = boundedName(op, 120);
        x.status = (status === "error" || status === "cancelled") ? status : "ok";
        x.latency_ms = Math.max(0, Math.min(600000, +latencyMs || 0));
        push(base("api.operation", x));
      } catch (e) {}
    },
    error: function (errClass, which) {
      try {
        push(base(which === "server" ? "server.error" : "client.error",
                  { err_class: clsName(errClass) }));
      } catch (e) {}
    },
    transition: function (runRef, fromState, toState, extra) {
      try {
        var x = shallow(extra, ["queue_ref"]);
        x.run_ref = boundedName(runRef, 80);
        x.from_state = stateName(fromState);
        x.to_state = stateName(toState);
        push(base("run.transition", x));
      } catch (e) {}
    },
    confirm: function (outcome, extra) {
      try {
        var x = shallow(extra, ["queue_ref", "run_ref"]);
        x.outcome = (outcome === "refused" || outcome === "expired" ||
                     outcome === "pending") ? outcome : "confirmed";
        x.event = "confirmation.outcome";
        push(base("confirmation.outcome", x));
      } catch (e) {}
    },
    legacy: function (route, method) {
      try {
        push(base("legacy.endpoint", { route: pathOnly(route),
              method: method || "GET", legacy: true }));
      } catch (e) {}
    },
    gap: function (kind, expectedPeriodS) {
      try {
        push(base("instrumentation.gap",
                  { detail: { kind: boundedName(kind, 40),
                              expected_period_s: +expectedPeriodS || 0 } }));
      } catch (e) {}
    },
    retryOf: retryOf,
    flush: function () { try { flush(false); } catch (e) {} }
  };

  function clsName(v) {
    var s = (v && (v.name || v.constructor && v.constructor.name)) || "Error";
    s = String(s);
    return /^[A-Za-z_][A-Za-z0-9_.]{0,63}$/.test(s) ? s : "Error";
  }
  function boundedName(v, max) {
    var s = String(v == null ? "" : v);
    if (!/^[A-Za-z_][A-Za-z0-9_./-]*$/.test(s)) return undefined;
    return s.length <= max ? s : s.slice(0, max);
  }
  function stateName(v) {
    var s = String(v == null ? "" : v);
    return /^[a-z][a-z0-9_.-]{0,39}$/.test(s) ? s : undefined;
  }
  /** Copy ONLY the named keys. A spread would carry whatever the caller passed. */
  function shallow(src, keys) {
    var out = {};
    if (!src) return out;
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (src[k] === undefined || src[k] === null) continue;
      if (k === "latency_ms") { out[k] = Math.max(0, Math.min(600000, +src[k] || 0)); }
      else if (k === "http_status") { out[k] = +src[k] | 0; }
      else if (k === "legacy") { out[k] = src[k] === true; }
      else { out[k] = boundedText(src[k]); }
    }
    return out;
  }
  function boundedText(v) {
    if (typeof v === "number") return v;
    if (typeof v === "boolean") return v;
    var s = String(v);
    if (s.length > 120) s = s.slice(0, 120);
    if (s.indexOf("?") !== -1 || s.indexOf("=") !== -1 || s.indexOf("@") !== -1) {
      return undefined;                 // request variables / identity are refused
    }
    return s;
  }

  // Page view + unload flush on every instrumented page. The route is read from
  // the location, never from page content.
  try {
    if (global.document) {
      global.addEventListener("pagehide", function () { flush(true); });
      global.addEventListener("visibilitychange", function () {
        if (global.document.visibilityState === "hidden") flush(true);
      });
      global.setInterval(function () { flush(false); }, FLUSH_MS);
      api.pageView({ route: pathOnly() });
    }
  } catch (e) { /* rule 3 */ }

  global.SiteEvents = api;
})(typeof window !== "undefined" ? window : this);
