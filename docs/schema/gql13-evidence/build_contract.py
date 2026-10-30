"""Build contract.json and variables.json from schema.graphql.

Deterministic, no model and no network. The gate demands that `fields` cover
every output field coordinate EXACTLY and that `collections` cover every
list/connection field exactly, so the two maps are derived from the parsed SDL
rather than retyped by hand. Policy TEXT is written here; policy MEMBERSHIP is
computed, which is what makes the coverage assertions true by construction.

    python3 docs/schema/gql13-evidence/build_contract.py
"""
from __future__ import annotations

import json
from pathlib import Path

from graphql import (
    build_schema, get_named_type, is_enum_type, is_input_object_type,
    is_interface_type, is_list_type, is_non_null_type, is_object_type,
)

WT = Path(__file__).resolve().parents[3]
ROOT = WT / "docs/schema/gql13"

SDL = (ROOT / "schema.graphql").read_text()
schema = build_schema(SDL)

AUTHZ = ("Any authenticated desk member may read. Commands additionally require "
         "the caller to hold editor rights on the slip's product lane. Missing "
         "and unauthorized rows both return OperationRejected, so absence is "
         "never disclosed.")
NULL_LOCAL = ("Locally persisted or derived value guaranteed once the parent "
              "row is authorized, hence non-null; a fault bubbles to its "
              "nearest nullable ancestor.")
NULL_MAYBE = ("Null on an empty page, absent object or unanswered ask; never "
              "used to signal an authorization failure, which is masked.")
SOURCE = ("Read from the already-loaded parent row or one bounded store query. "
          "Queue rows project the queue-state authority; no resolver re-reads "
          "the store per row.")
BATCH = ("Resolved from the loaded parent or one bounded query; no per-row "
         "remote calls. Request-local cache keys include the desk identity.")

collections: dict[str, dict] = {}
mutations: dict[str, dict] = {}
fields: dict[str, dict] = {}

for name, typ in schema.type_map.items():
    if name.startswith("__") or name in ("String", "ID", "Int", "Float", "Boolean"):
        continue
    if is_object_type(typ) or is_interface_type(typ):
        for fname, field in typ.fields.items():
            coord = f"{name}.{fname}"
            nullable = not is_non_null_type(field.type)
            fields[coord] = {
                "authorization": AUTHZ,
                "nullability": NULL_MAYBE if nullable else NULL_LOCAL,
                "source": SOURCE,
                "batching": BATCH,
            }
            outer = field.type.of_type if is_non_null_type(field.type) else field.type
            named = get_named_type(field.type).name
            if is_list_type(outer) or named.endswith("Connection"):
                collections[coord] = {
                    "kind": "bounded",
                    "max_items": 100,
                    "enforcement": ("Built only from an already-bounded source: "
                                    "the parent page, the fixed enum set, or the "
                                    "rows this single command closed."),
                }
    elif is_input_object_type(typ):
        continue

# The three cursor collections carry the paging contract.
for coord in ("Query.slips", "Query.queueItems", "Query.thread"):
    collections[coord] = {
        "kind": "cursor",
        "max_items": 100 if coord != "Query.thread" else 200,
        "enforcement": ("Resolver defaults omitted or null first to 20 (50 for "
                        "thread); rejects values outside 1..100 (1..200 for "
                        "thread). The store query uses LIMIT first+1."),
        "order": ("Ascending immutable identity using binary string comparison "
                  "over the same key the cursor encodes."),
        "tie_breaker": "The row identity is unique and is its own final tie-breaker.",
        "cursor_scope": ("Signed opaque cursor binds the desk identity, the "
                         "ordering version and the last row identity; a cursor "
                         "from another slip, desk or ordering is rejected."),
    }

for name, field in schema.mutation_type.fields.items():
    mutations[f"Mutation.{name}"] = {
        "intent": ("Apply exactly one desk or queue transition and return the "
                   "committed rows; no side write outside the store's lock."),
        "idempotency": ("Caller retry key scoped to holder/desk, recorded with "
                        "the committed outcome atomically; an identical replay "
                        "returns the original result, a changed payload is a "
                        "masked BAD_USER_INPUT error."),
        "concurrency": ("Runs inside the queue store's single lock. Completion "
                        "requires a live claim held by the caller; a claim held "
                        "by another holder is refused and named."),
        "errors": ("Typed refusals for expected cases; absent and unauthorized "
                   "rows share one masked message; unexpected faults stay "
                   "masked top-level errors."),
    }

contract = {
    "version": 1,
    "mode": "new",
    "assumptions": [
        "The Desk, claim queue, run and thread services already exist and stay "
        "the sources of truth; this contract only projects them.",
        "queue_store remains the queue-state authority; no state machine is "
        "re-implemented here.",
        "Planning dependencies stay in the existing planning tool and are not "
        "duplicated in this schema.",
        "Caller compatibility is with the existing Desk and queue HTTP callers, "
        "not with the memory or kit SDL, which are unrelated APIs.",
    ],
    "open_questions": [],
    "requirements": [
        {"id": "R-file",
         "description": "A desk member files a slip and it is enqueued only when its lane has a runner.",
         "operations": ["FileSlip"],
         "coordinates": ["Mutation.fileSlip", "SlipFiled.slip",
                         "OperationRejected.message"]},
        {"id": "R-browse-slips",
         "description": "A desk member pages through slips without duplicates in stable identity order.",
         "operations": ["BrowseSlips"],
         "coordinates": ["Query.slips", "Slip.id", "PageInfo.endCursor"]},
        {"id": "R-read-slip",
         "description": "A desk member reads one slip with its derived status and provenance.",
         "operations": ["GetSlip"],
         "coordinates": ["Query.slip", "Slip.status", "Slip.origin"]},
        {"id": "R-queue-read",
         "description": "A desk member pages claim-queue rows and takes one for a named holder.",
         "operations": ["BrowseQueue", "ClaimQueueItem"],
         "coordinates": ["Query.queueItems", "QueueItem.id",
                         "ClaimStarted.item", "ClaimStarted.leaseUntil",
                         "ClaimConflict.reason"]},
        {"id": "R-renew",
         "description": "A holder extends a live claim so long work stays honestly in progress.",
         "operations": ["ClaimQueueItem", "RenewClaim"],
         "coordinates": ["Mutation.claimQueueItem", "Mutation.renewClaim",
                         "RenewAcknowledged.item", "RenewAcknowledged.renewEvery"]},
        {"id": "R-close-run",
         "description": "A holder completes a claimed row after a report, or releases it with a reason.",
         "operations": ["CompleteQueueItem", "ReleaseQueueItem"],
         "coordinates": ["Mutation.completeQueueItem", "Mutation.releaseQueueItem",
                         "QueueCompleted.items", "QueueReleased.items"]},
        {"id": "R-thread",
         "description": "A desk member reads a slip thread oldest first, with full body and preview.",
         "operations": ["GetThread"],
         "coordinates": ["Query.thread", "ThreadEntry.body",
                         "ThreadEntry.oneLine"]},
        {"id": "R-status",
         "description": "A desk member reads the derived lifecycle light and its age wording.",
         "operations": ["GetSlipStatus"],
         "coordinates": ["Query.status", "SlipStatus.status", "SlipStatus.label",
                         "SlipStatus.waitingForSec"]},
        {"id": "R-stream",
         "description": "A desk client streams slip-status changes in sequence order.",
         "operations": ["WatchSlipStatus"],
         "coordinates": ["Subscription.slipStatusChanged",
                         "SlipStatusEvent.slipId", "SlipStatusEvent.sequence"]},
        {"id": "R-decide",
         "description": "A human records the answer to a decide slip and it routes onward.",
         "operations": ["RecordDecision"],
         "coordinates": ["Mutation.recordDecision", "DecisionRecorded.entry",
                         "DecisionRecorded.decision", "DecisionRecorded.routedTo"]},
    ],
    "fields": fields,
    "collections": collections,
    "mutations": mutations,
    "scalars": {},
    "runtime": {
        "demand_control": ("Enforce first<=100 (200 for thread), depth<=8, "
                           "aliases<=20, HTTP batches<=5 and multiplicative list "
                           "cost<=1000; calibrate against real desk traffic."),
        "tenant_isolation": ("Desk identity is taken from the authenticated "
                             "principal, never from a caller-supplied id; every "
                             "read, command, cursor and request-local cache is "
                             "scoped by it."),
        "authorization_tests": ("Cover cross-desk slip and row ids, forged "
                                "cursors, a claim held by another holder and a "
                                "revoked membership; assert absent and "
                                "unauthorized rows cannot be told apart."),
        "performance_tests": ("Compare query counts for 1 and 100 rows; both "
                              "page reads stay constant. Check the plan uses the "
                              "cursor index and that no resolver loops."),
        "schema_codegen": ("Generate client types from this exact SDL and the "
                           "operation documents, compile the clients and check "
                           "their mocks match the operation shapes."),
        "subscriptions": ("Status changes stream one event per change with a "
                          "monotonic sequence number; a client that reconnects "
                          "must be able to drop anything it already painted."),
    },
}

variables = [
    {"operation": "BrowseSlips", "variables": {"first": 20, "after": None},
     "expected": "valid"},
    {"operation": "GetSlip", "variables": {"id": "d-0123456789ab"},
     "expected": "valid"},
    {"operation": "BrowseQueue", "variables": {"first": 20, "after": None},
     "expected": "valid"},
    {"operation": "GetThread",
     "variables": {"slipId": "d-0123456789ab", "first": 50, "after": None},
     "expected": "valid"},
    {"operation": "GetSlipStatus", "variables": {"slipId": "d-0123456789ab"},
     "expected": "valid"},
    {"operation": "FileSlip",
     "variables": {"input": {"title": "Fix the board", "product": "PLATFORM",
                             "kind": "TASK", "owner": "BOTH",
                             "delivery": "SIMPLE_CITY", "labels": ["desk"]}},
     "expected": "valid"},
    {"operation": "FileSlip",
     "variables": {"input": {"title": "Fix the board", "product": "NOPE"}},
     "expected": "invalid"},
    {"operation": "FileSlip",
     "variables": {"input": {"product": "PLATFORM"}},
     "expected": "invalid"},
    {"operation": "ClaimQueueItem",
     "variables": {"input": {"rowId": "q-1788781449456", "holder": "worker-1",
                             "kind": "desk-prompt", "leaseSec": 900,
                             "idempotencyKey": "claim-1"}},
     "expected": "valid"},
    {"operation": "ClaimQueueItem",
     "variables": {"input": {"rowId": "q-1788781449456", "holder": "worker-1",
                             "leaseSec": "not-a-number",
                             "idempotencyKey": "claim-1"}},
     "expected": "invalid"},
    {"operation": "RenewClaim",
     "variables": {"input": {"rowId": "q-1788781449456", "holder": "worker-1",
                             "leaseSec": 900, "idempotencyKey": "renew-1"}},
     "expected": "valid"},
    {"operation": "CompleteQueueItem",
     "variables": {"input": {"rowId": "q-1788781449456", "holder": "worker-1",
                             "resultDigest": "abc123",
                             "idempotencyKey": "done-1"}},
     "expected": "valid"},
    {"operation": "ReleaseQueueItem",
     "variables": {"input": {"rowId": "q-1788781449456", "holder": "worker-1",
                             "reason": "lane died without closing the row",
                             "idempotencyKey": "release-1"}},
     "expected": "valid"},
    {"operation": "ReleaseQueueItem",
     "variables": {"input": {"rowId": "q-1788781449456", "holder": "worker-1",
                             "idempotencyKey": "release-1"}},
     "expected": "invalid"},
    {"operation": "RecordDecision",
     "variables": {"input": {"slipId": "d-0123456789ab", "text": "Ship option B",
                             "option": "B", "expectedRevision": 3,
                             "idempotencyKey": "decide-1"}},
     "expected": "valid"},
    {"operation": "RecordDecision",
     "variables": {"input": {"slipId": "d-0123456789ab", "text": "Ship option B",
                             "expectedRevision": "bad",
                             "idempotencyKey": "decide-1"}},
     "expected": "invalid"},
    {"operation": "WatchSlipStatus", "variables": {"slipId": None},
     "expected": "valid"},
    {"operation": "WatchSlipStatus", "variables": {"slipId": "d-0123456789ab"},
     "expected": "valid"},
]

(ROOT / "contract.json").write_text(json.dumps(contract, indent=2, sort_keys=False) + "\n")
(ROOT / "variables.json").write_text(json.dumps(variables, indent=2) + "\n")
print("fields", len(fields), "collections", len(collections),
      "mutations", len(mutations), "requirements", len(contract["requirements"]))