"""Persistent request receipts, not a task queue or a claim on exactly-once effects."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from fastmcp.exceptions import ToolError


class Receipts:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                principal TEXT NOT NULL, request_id TEXT NOT NULL,
                operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, created_at INTEGER NOT NULL,
                PRIMARY KEY(principal, request_id))""")
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def begin(self, principal, request_id, operation, payload):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT operation, fingerprint, state, result FROM receipts WHERE principal=? AND request_id=?",
                             (principal, request_id)).fetchone()
            if row:
                db.commit()
                if row[0] != operation or row[1] != fingerprint:
                    raise ToolError("IDEMPOTENCY_CONFLICT: request_id already names a different operation/payload")
                if row[2] != "completed":
                    raise ToolError("OUTCOME_UNKNOWN: previous request may be running or applied; reconcile it before issuing new work")
                return json.loads(row[3])
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                       (principal, request_id, operation, fingerprint, "pending", None, int(time.time())))
            db.commit()
        return None

    def complete(self, principal, request_id, result):
        with closing(self.connect()) as db:
            changed = db.execute("UPDATE receipts SET state='completed', result=? WHERE principal=? AND request_id=? AND state='pending'",
                                 (json.dumps(result), principal, request_id)).rowcount
            if changed != 1:
                raise ToolError("Receipt could not be completed; reconcile before retrying")

    def status(self, principal, request_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT operation,state,result,created_at FROM receipts WHERE principal=? AND request_id=?",
                             (principal, request_id)).fetchone()
        if not row:
            return {"state": "not_found", "request_id": request_id}
        return {"request_id": request_id, "operation": row[0], "state": row[1],
                "result": json.loads(row[2]) if row[2] else None, "created_at": row[3]}
