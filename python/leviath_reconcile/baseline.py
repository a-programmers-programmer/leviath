"""The convergence baseline: "the last agreed version" both sides diff against.

One accepted baseline per domain, stored as JSON next to nothing else.  It holds:

* the current-revision document identity of each projection (its document
  version) and the canonical digest of its facts;
* the agreed fact body, a :class:`~leviath_reconcile.model.FactSet`;
* the canonical-symbol resolution in force, so identities do not churn;
* a monotone ``revision`` — the *version number* a caller must present as
  ``expected_revision`` before its work is allowed to overwrite the baseline.

Writes are compare-and-set: ``commit(expected_revision=...)`` fails closed when
the caller's revision is not the live one, and the write itself is atomic
(temp file + ``os.replace``) so a crash cannot leave a half-written baseline.

Rollback: each committed baseline keeps the previous generation beside it
(``<store>.prev``), and :func:`BaselineStore.rollback` restores it.  The store
never touches any other file, any database or any route.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional

from .identity import IdentityResolver
from .model import FactSet, sha256_bytes

BASELINE_FORMAT = "leviath-reconcile-baseline/v1"


class BaselineConflict(RuntimeError):
    """The caller's view of the baseline was not the live one."""


@dataclass
class Baseline:
    """An immutable-in-spirit accepted agreement between the two projections."""

    revision: int = 1
    contract: str = ""
    contract_fingerprint: str = ""
    documents: Dict[str, str] = field(default_factory=dict)     # projection -> document identity
    digests: Dict[str, str] = field(default_factory=dict)       # projection -> fact digest at agree time
    body: FactSet = field(default_factory=FactSet)
    identity: IdentityResolver = field(default_factory=IdentityResolver)
    accepted_at: str = ""
    history_digest: str = ""

    @property
    def body_digest(self) -> str:
        return self.body.digest

    @property
    def facts(self) -> FactSet:
        """The agreed fact body.  Alias for :attr:`body` for reader clarity."""
        return self.body

    def to_json(self) -> str:
        payload = {
            "format": BASELINE_FORMAT,
            "revision": self.revision,
            "contract": self.contract,
            "contract_fingerprint": self.contract_fingerprint,
            "documents": {k: self.documents[k] for k in sorted(self.documents)},
            "digests": {k: self.digests[k] for k in sorted(self.digests)},
            "identity": self.identity.as_dict(),
            "accepted_at": self.accepted_at,
            "body": self.body.as_dict(),
        }
        payload["history_digest"] = sha256_bytes(
            json.dumps(
                {
                    "revision": self.revision,
                    "body": payload["body"],
                    "documents": payload["documents"],
                    "identity": payload["identity"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Baseline":
        payload = json.loads(text)
        fmt = payload.get("format", BASELINE_FORMAT)
        if fmt != BASELINE_FORMAT:
            raise ValueError(f"unknown baseline format {fmt!r}")
        return cls(
            revision=int(payload["revision"]),
            contract=payload.get("contract", ""),
            contract_fingerprint=payload.get("contract_fingerprint", ""),
            documents=dict(payload.get("documents", {})),
            digests=dict(payload.get("digests", {})),
            body=FactSet.from_dict(payload.get("body", {"facts": []})),
            identity=IdentityResolver.from_dict(payload.get("identity", {})),
            accepted_at=payload.get("accepted_at", ""),
            history_digest=payload.get("history_digest", ""),
        )


class BaselineStore:
    """File-backed, compare-and-set baseline store."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.previous_path = self.path.with_name(self.path.name + ".prev")

    # -- load / save --------------------------------------------------------
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Baseline:
        if not self.path.is_file():
            return Baseline()
        return Baseline.from_json(self.path.read_text(encoding="utf-8"))

    def _write_atomic(self, target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".baseline-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def commit(self, baseline: Baseline, expected_revision: int) -> int:
        """Compare-and-set commit.  Returns the new revision.

        Raises :class:`BaselineConflict` when ``expected_revision`` is not the
        live revision — the mechanism that stops a stale document version from
        overwriting newer work.
        """
        live = self.load()
        if expected_revision != live.revision:
            raise BaselineConflict(
                f"stale baseline: expected revision {expected_revision}, live revision {live.revision}"
            )
        new = Baseline(
            revision=live.revision + 1,
            contract=baseline.contract,
            contract_fingerprint=baseline.contract_fingerprint,
            documents=dict(baseline.documents),
            digests=dict(baseline.digests),
            body=baseline.body,
            identity=baseline.identity,
            accepted_at=baseline.accepted_at,
        )
        if self.path.is_file():
            self._write_atomic(self.previous_path, self.path.read_text(encoding="utf-8"))
        self._write_atomic(self.path, new.to_json())
        return new.revision

    def rollback(self) -> Baseline:
        """Restore the previous generation (best-effort, still atomic)."""
        if not self.previous_path.is_file():
            raise BaselineConflict("no previous baseline generation to roll back to")
        text = self.previous_path.read_text(encoding="utf-8")
        self._write_atomic(self.path, text)
        return Baseline.from_json(text)

    def digest(self) -> str:
        return sha256_bytes(self.path.read_bytes()) if self.path.is_file() else ""
