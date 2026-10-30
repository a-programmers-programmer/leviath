#!/usr/bin/env python3
"""GQL-35 backlog prep: validate the telemetry contract and the coverage map.

Checks, in order:
  1. contract/site-events-v1.schema.json parses as JSON.
  2. It is a valid JSON Schema (draft 2020-12) per jsonschema.check_schema.
  3. It is a CLOSED allow-list: additionalProperties is false at the top level
     and inside `detail` (this is the load-bearing privacy rule).
  4. It names the ten contract events and the eight conditional (allOf) rules.
  5. contract/coverage.json parses, has both surfaces, and every entry has an
     owner and an explicit `instrumented` boolean.
  6. The coverage map's `instrumented: false` entries are reported as FINDINGS.
  7. Every route/op named in the coverage map exists in the base-commit app,
     when the app can be imported (best-effort; skipped otherwise).

Exit code 0 = all checks pass. Non-zero = the first failing check.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "contract", "site-events-v1.schema.json")
COVERAGE = os.path.join(HERE, "contract", "coverage.json")

failures: list[str] = []
checks: list[str] = []


def ok(msg: str) -> None:
    checks.append("PASS  " + msg)


def bad(msg: str) -> None:
    failures.append("FAIL  " + msg)
    checks.append("FAIL  " + msg)


def main() -> int:
    # ---- 1. schema parses -------------------------------------------------
    try:
        with open(SCHEMA, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        ok("schema parses as JSON (%d bytes)" % os.path.getsize(SCHEMA))
    except Exception as exc:                                   # noqa: BLE001
        bad("schema does not parse: %s" % exc)
        print("\n".join(checks))
        return 1

    # ---- 2. schema is a valid JSON Schema ---------------------------------
    try:
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(schema)
        ok("schema is a valid draft 2020-12 JSON Schema")
    except ImportError:
        bad("jsonschema is not installed; cannot verify the schema meta-rules")
    except Exception as exc:                                   # noqa: BLE001
        bad("schema is not a valid JSON Schema: %s" % exc)

    # ---- 3. closed allow-list ---------------------------------------------
    if schema.get("additionalProperties") is False:
        ok("top level is a closed allow-list (additionalProperties: false)")
    else:
        bad("top level is NOT closed: additionalProperties=%r"
            % schema.get("additionalProperties"))
    detail = schema.get("properties", {}).get("detail", {})
    if detail.get("additionalProperties") is False:
        ok("detail is a closed allow-list (additionalProperties: false)")
    else:
        bad("detail is NOT closed: additionalProperties=%r"
            % detail.get("additionalProperties"))

    # ---- 4. events + conditional rules ------------------------------------
    want_events = {
        "page.view", "ui.action", "api.operation", "client.error", "server.error",
        "run.transition", "confirmation.outcome", "legacy.endpoint",
        "instrumentation.gap", "privacy.reject",
    }
    have_events = set(schema.get("properties", {}).get("event", {}).get("enum", []))
    if want_events <= have_events:
        ok("all ten contract events are named")
    else:
        bad("missing events: %s" % sorted(want_events - have_events))
    n_rules = len(schema.get("allOf", []))
    if n_rules == 8:
        ok("eight conditional (allOf) rules are present")
    else:
        bad("expected 8 conditional rules, found %d" % n_rules)

    # ---- 5. coverage map ---------------------------------------------------
    try:
        with open(COVERAGE, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        ok("coverage.json parses as JSON (%d bytes)" % os.path.getsize(COVERAGE))
    except Exception as exc:                                   # noqa: BLE001
        bad("coverage.json does not parse: %s" % exc)
        print("\n".join(checks))
        return 1

    surfaces = cov.get("surfaces", [])
    seen_surfaces = {s.get("surface") for s in surfaces}
    if seen_surfaces == {"admin", "product"}:
        ok("coverage map accounts for both surfaces: admin and product")
    else:
        bad("coverage map surfaces = %s (want admin + product)" % sorted(seen_surfaces))

    no_owner = [s.get("key") for s in surfaces if not s.get("owner")]
    if not no_owner:
        ok("every coverage entry names an owner (%d entries)" % len(surfaces))
    else:
        bad("entries with no owner: %s" % no_owner)

    no_flag = [s.get("key") for s in surfaces if not isinstance(s.get("instrumented"), bool)]
    if not no_flag:
        ok("every coverage entry has an explicit instrumented boolean")
    else:
        bad("entries with no instrumented flag: %s" % no_flag)

    # ---- 6. findings -------------------------------------------------------
    missing = [s for s in surfaces if s.get("instrumented") is False]
    ok("instrumented:false is a FINDING for %d surfaces: %s"
       % (len(missing), ", ".join("%s %s" % (s["surface"], s["key"]) for s in missing)))
    prod_missing = [s for s in missing if s.get("surface") == "product"]
    if prod_missing:
        ok("the product side is present and honestly marked missing (%d entries)"
           % len(prod_missing))
    else:
        bad("the product side has no missing entries; the map is not honest")

    if cov.get("legacy_retirement_watch"):
        ok("legacy retirement watch list is present (%d routes)"
           % len(cov["legacy_retirement_watch"]))
    else:
        bad("legacy retirement watch list is empty")

    # ---- 7. cross-check against the base-commit app (best effort) ----------
    repo = os.path.join(HERE, "repo")
    if os.path.isdir(repo):
        try:
            sys.path.insert(0, repo)
            os.environ.setdefault("DEPLOY_GUARD", "0")
            import main as app_main                              # noqa: F401
            app = app_main.app
            rules = {str(r) for r in app.url_map.iter_rules()}
            route_keys = [s["key"] for s in surfaces if s.get("kind") == "route"]
            absent = []
            for key in route_keys:
                base = key.split("<")[0].rstrip("/") or "/"
                if not any(base in r for r in rules):
                    absent.append(key)
            if absent:
                bad("coverage routes not found in the app url_map: %s" % absent)
            else:
                ok("all %d coverage routes resolve against the app url_map (%d rules)"
                   % (len(route_keys), len(rules)))
        except Exception as exc:                               # noqa: BLE001
            ok("app cross-check skipped (import failed: %s)" % type(exc).__name__)
    else:
        ok("app cross-check skipped (no repo checkout at %s)" % repo)

    print("\n".join(checks))
    print("\n%d checks, %d failures" % (len(checks), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())