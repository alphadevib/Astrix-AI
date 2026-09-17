"""Astrix-LM training corpus: secure capture of verified agent decisions.

Every consolidated `cycle` the pipeline publishes that reached a diagnosis is
turned into a supervised example — the situation ASTRIX saw (detection,
deviating channels, context factors) paired with what the agents concluded
(diagnosis, risk, selected recovery and its safety verdict). These examples are
the teaching signal for Astrix's own models:

* `nano.py` trains an on-box classifier that runs anywhere scikit-learn runs;
* `scripts/train_astrix_lm.py` exports the same examples as chat-format JSONL
  and LoRA fine-tunes a small open LLM that can be served through Ollama.

Security model
--------------
* Payloads are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256). The
  key comes from `ASTRIX_CORPUS_KEY`; without it a key file is generated beside
  the corpus with owner-only permissions and a warning is logged.
* Each row stores the SHA-256 of its plaintext and a hash chain over all
  previous rows, so deletion, reordering or edits are detectable (`verify()`).
* Only labels needed for filtering (subsystem, failure mode, action, reasoner
  kind, safety status) are stored in clear. Telemetry context is encrypted.
* Duplicate examples (identical situation and decision) are skipped, so a
  long-running anomaly does not flood the corpus with one lesson.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GENESIS = "0" * 64

SYSTEM_PROMPT = (
    "You are Astrix-LM, a spacecraft fault-management model. Given detection context "
    "from on-board telemetry, return the diagnosis, risk and recovery decision as JSON. "
    "Your output is advisory and must pass the deterministic safety engine before execution."
)


def _fernet(key: str | None, key_path: Path):
    from cryptography.fernet import Fernet

    if key:
        return Fernet(key.encode() if isinstance(key, str) else key), "environment"
    if key_path.exists():
        return Fernet(key_path.read_bytes().strip()), "key file"
    generated = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(generated)
    try:
        os.chmod(key_path, 0o600)
    except OSError:  # pragma: no cover — not enforceable on every filesystem
        pass
    log.warning(
        "generated a corpus encryption key at %s — set ASTRIX_CORPUS_KEY from a secret "
        "manager for any shared or production deployment",
        key_path,
    )
    return Fernet(generated), "generated key file"


def build_example(cycle: dict[str, Any]) -> dict[str, Any] | None:
    """Situation → decision pair from one published cycle, or None if not a lesson."""
    detection = cycle.get("detection") or {}
    diagnosis = cycle.get("diagnosis")
    if not diagnosis or detection.get("suppressed"):
        return None
    plan = cycle.get("plan") or {}
    safety = cycle.get("safety") or {}
    risk = cycle.get("risk") or {}
    selected = plan.get("selected_action_id")
    option = next((o for o in plan.get("options", []) if o.get("action_id") == selected), None)

    situation = {
        "severity": detection.get("severity"),
        "ml_score": round(float(detection.get("ml_score", 0.0)), 3),
        "final_score": round(float(detection.get("final_score", 0.0)), 3),
        "persistence_seconds": round(float(detection.get("persistence_seconds", 0.0)), 1),
        "deviating_parameters": sorted(detection.get("deviating_parameters", [])),
        "context_factors": {
            f.get("name"): round(float(f.get("value", 0.0)), 3) for f in detection.get("context_factors", [])
        },
    }
    decision = {
        "diagnosis": {
            "subsystem": diagnosis.get("subsystem"),
            "component": diagnosis.get("component"),
            "failure_mode": diagnosis.get("failure_mode"),
            "probable_cause": diagnosis.get("probable_cause"),
            "confidence": diagnosis.get("confidence"),
        },
        "risk": {
            "severity": risk.get("severity"),
            "mission_impact": risk.get("mission_impact"),
            "time_to_impact_minutes": risk.get("time_to_impact_minutes"),
        }
        if risk
        else None,
        "recovery": {
            "action_id": selected,
            "risk_level": option.get("risk_level") if option else None,
            "rationale": option.get("rationale") if option else None,
        }
        if selected
        else None,
        "safety_status": safety.get("status"),
    }
    reasoners = {diagnosis.get("reasoner"), risk.get("reasoner") if risk else None, plan.get("reasoner") if plan else None}
    reasoners.discard(None)
    return {
        "situation": situation,
        "decision": decision,
        "labels": {
            "subsystem": diagnosis.get("subsystem"),
            "failure_mode": diagnosis.get("failure_mode") or "unspecified",
            "action_id": selected or "none",
            "reasoner": "LLM" if "LLM" in reasoners else "DETERMINISTIC",
            "safety_status": safety.get("status") or "NOT_RUN",
        },
        "cycle_id": cycle.get("cycle_id"),
        "spacecraft_id": cycle.get("spacecraft_id"),
    }


class TrainingCorpus:
    def __init__(self, path: str | Path, key: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet, self.key_source = _fernet(key, self.path.parent / ".corpus.key")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                spacecraft_id TEXT,
                subsystem TEXT,
                failure_mode TEXT,
                action_id TEXT,
                reasoner TEXT,
                safety_status TEXT,
                payload BLOB NOT NULL,
                digest TEXT NOT NULL UNIQUE,
                chain TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self.listeners: list[Any] = []

    # -- capture ------------------------------------------------------------ #

    def on_event(self, event_type: str, payload: Any) -> None:
        if event_type == "cycle" and isinstance(payload, dict):
            example = build_example(payload)
            if example is not None:
                self.add(example)

    def add(self, example: dict[str, Any]) -> int | None:
        body = {"situation": example["situation"], "decision": example["decision"]}
        plaintext = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(plaintext).hexdigest()
        labels = example["labels"]
        with self._lock:
            if self._conn.execute("SELECT 1 FROM examples WHERE digest = ?", (digest,)).fetchone():
                return None
            head = self._head()
            chain = hashlib.sha256((head + digest).encode()).hexdigest()
            cur = self._conn.execute(
                "INSERT INTO examples (created_at, spacecraft_id, subsystem, failure_mode, action_id, reasoner, "
                "safety_status, payload, digest, chain) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    example.get("spacecraft_id"),
                    labels["subsystem"],
                    labels["failure_mode"],
                    labels["action_id"],
                    labels["reasoner"],
                    labels["safety_status"],
                    self._fernet.encrypt(plaintext),
                    digest,
                    chain,
                ),
            )
            self._conn.commit()
            row_id = cur.lastrowid
        for listener in self.listeners:
            try:
                listener(row_id)
            except Exception:  # noqa: BLE001
                log.exception("corpus listener failed")
        return row_id

    def _head(self) -> str:
        row = self._conn.execute("SELECT chain FROM examples ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else GENESIS

    # -- read --------------------------------------------------------------- #

    def examples(self, only_verified: bool = False, reasoner: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, created_at, subsystem, failure_mode, action_id, reasoner, safety_status, payload FROM examples"
        clauses, params = [], []
        if only_verified:
            clauses.append("safety_status = 'PASS'")
        if reasoner:
            clauses.append("reasoner = ?")
            params.append(reasoner)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY id", params).fetchall()
        out = []
        for row_id, created, subsystem, mode, action, kind, safety, payload in rows:
            body = json.loads(self._fernet.decrypt(payload))
            out.append(
                {
                    "id": row_id,
                    "created_at": created,
                    "labels": {
                        "subsystem": subsystem,
                        "failure_mode": mode,
                        "action_id": action,
                        "reasoner": kind,
                        "safety_status": safety,
                    },
                    **body,
                }
            )
        return out

    def recent_examples(self, limit: int = 200) -> list[dict[str, Any]]:
        """Newest examples first, bounded.

        `examples()` decrypts the whole corpus, which is fine for training and far
        too much work for the open-world lookup that runs inside a telemetry cycle.
        """
        query = (
            "SELECT id, created_at, subsystem, failure_mode, action_id, reasoner, safety_status, payload "
            "FROM examples ORDER BY id DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(query, (max(1, limit),)).fetchall()
        out = []
        for row_id, created, subsystem, mode, action, kind, safety, payload in rows:
            try:
                body = json.loads(self._fernet.decrypt(payload))
            except Exception:  # noqa: BLE001 — one unreadable row must not blind recall
                continue
            out.append(
                {
                    "id": row_id,
                    "created_at": created,
                    "labels": {
                        "subsystem": subsystem,
                        "failure_mode": mode,
                        "action_id": action,
                        "reasoner": kind,
                        "safety_status": safety,
                    },
                    **body,
                }
            )
        return out

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM examples").fetchone()[0])

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = int(self._conn.execute("SELECT COUNT(*) FROM examples").fetchone()[0])
            by = {}
            for column in ("subsystem", "reasoner", "safety_status"):
                by[column] = dict(
                    self._conn.execute(f"SELECT {column}, COUNT(*) FROM examples GROUP BY {column}").fetchall()
                )
            head = self._head()
        return {
            "examples": total,
            "by_subsystem": by["subsystem"],
            "by_reasoner": by["reasoner"],
            "by_safety_status": by["safety_status"],
            "chain_head": head,
            "encryption": "Fernet (AES-128-CBC + HMAC-SHA256)",
            "key_source": self.key_source,
        }

    def verify(self) -> dict[str, Any]:
        """Recompute the hash chain and decrypt every payload."""
        prev = GENESIS
        checked = 0
        with self._lock:
            rows = self._conn.execute("SELECT id, payload, digest, chain FROM examples ORDER BY id").fetchall()
        for row_id, payload, digest, chain in rows:
            try:
                plaintext = self._fernet.decrypt(payload)
            except Exception:  # noqa: BLE001 — InvalidToken or corrupt blob
                return {"ok": False, "checked": checked, "broken_at": row_id, "reason": "payload failed authentication"}
            if hashlib.sha256(plaintext).hexdigest() != digest:
                return {"ok": False, "checked": checked, "broken_at": row_id, "reason": "digest mismatch"}
            expected = hashlib.sha256((prev + digest).encode()).hexdigest()
            if expected != chain:
                return {"ok": False, "checked": checked, "broken_at": row_id, "reason": "hash chain broken"}
            prev = chain
            checked += 1
        return {"ok": True, "checked": checked, "chain_head": prev}

    def export_sft(self, only_verified: bool = True) -> list[dict[str, Any]]:
        """Chat-format supervised fine-tuning records (one per example).

        Frame examples and mission summaries train the same model but answer
        different questions, so each carries the system prompt for its own scope.
        """
        from .mission_summary import SUMMARY_SYSTEM_PROMPT

        records = []
        for ex in self.examples(only_verified=only_verified):
            mission_scope = ex.get("scope") == "mission"
            records.append(
                {
                    "messages": [
                        {
                            "role": "system",
                            "content": SUMMARY_SYSTEM_PROMPT if mission_scope else SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": json.dumps(ex["situation"], sort_keys=True)},
                        {"role": "assistant", "content": json.dumps(ex["decision"], sort_keys=True)},
                    ],
                    "scope": "mission" if mission_scope else "frame",
                }
            )
        return records

    def close(self) -> None:
        with self._lock:
            self._conn.close()
