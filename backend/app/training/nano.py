"""Astrix-LM nano: Astrix's own on-box decision model.

A small, fast student model distilled from the verified decisions in the
training corpus. It learns three heads from the same detection context the
agents saw:

    subsystem · failure mode · recovery action

It is deliberately modest: scikit-learn only, trains in seconds on a CPU, runs
on a Vercel-sized container, and never executes anything. Its job is to learn
from the deterministic reasoners until it agrees with them, and to report how
often it does (shadow evaluation), which is the evidence an operator needs
before trusting a learned model with any authority.

Each training run is versioned with its metrics, example count and the corpus
hash-chain head it was trained on (data provenance). Model files are checked
against their recorded SHA-256 before loading.

For a generative model, export the corpus and run `scripts/train_astrix_lm.py`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

from .corpus import TrainingCorpus

log = logging.getLogger(__name__)

HEADS = ("subsystem", "failure_mode", "action_id")
MIN_EXAMPLES = 6
SEVERITY_RANK = {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}


def featurize(situation: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {
        "severity": float(SEVERITY_RANK.get(situation.get("severity") or "NORMAL", 0)),
        "ml_score": float(situation.get("ml_score") or 0.0),
        "final_score": float(situation.get("final_score") or 0.0),
        "persistence": min(1.0, float(situation.get("persistence_seconds") or 0.0) / 120.0),
    }
    for name in situation.get("deviating_parameters", []):
        features[f"dev:{name}"] = 1.0
        # Channel family (wheel_3_vibration -> wheel_vibration) generalises across units.
        parts = name.split("_")
        if len(parts) >= 3 and parts[1].isdigit():
            features[f"family:{parts[0]}_{'_'.join(parts[2:])}"] = 1.0
    for name, value in (situation.get("context_factors") or {}).items():
        features[f"ctx:{name}"] = float(value)
    return features


class NanoModel:
    def __init__(self, corpus: TrainingCorpus, model_dir: str | Path) -> None:
        self.corpus = corpus
        self.dir = Path(model_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.dir / "registry.json"
        self._lock = threading.Lock()
        self._training = False
        self._bundle: dict[str, Any] | None = None
        self.shadow = {"compared": 0, "agreed": 0}
        self.registry: list[dict[str, Any]] = (
            json.loads(self.registry_path.read_text()) if self.registry_path.exists() else []
        )
        self._load_latest()

    # -- persistence -------------------------------------------------------- #

    def _load_latest(self) -> None:
        if not self.registry:
            return
        entry = self.registry[-1]
        path = self.dir / entry["file"]
        if not path.exists():
            return
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            log.error("nano model %s failed its integrity check; not loading it", path.name)
            return
        self._bundle = joblib.load(path)

    @property
    def version(self) -> str | None:
        return self.registry[-1]["version"] if self.registry and self._bundle else None

    # -- training ----------------------------------------------------------- #

    def train(self, only_verified: bool = False) -> dict[str, Any]:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import make_pipeline

        with self._lock:
            if self._training:
                return {"trained": False, "reason": "a training run is already in progress"}
            self._training = True
        try:
            examples = self.corpus.examples(only_verified=only_verified)
            if len(examples) < MIN_EXAMPLES:
                return {
                    "trained": False,
                    "reason": f"need at least {MIN_EXAMPLES} examples, corpus has {len(examples)} — run missions and inject faults to collect more",
                }
            X = [featurize(e["situation"]) for e in examples]
            heads: dict[str, Any] = {}
            metrics: dict[str, Any] = {}
            for head in HEADS:
                y = [str(e["labels"].get(head) or "none") for e in examples]
                classes = sorted(set(y))
                if len(classes) < 2:
                    heads[head] = {"constant": classes[0]}
                    metrics[head] = {"classes": 1, "accuracy": 1.0, "evaluation": "single class"}
                    continue
                counts = {c: y.count(c) for c in classes}
                can_split = len(examples) >= 20 and min(counts.values()) >= 2
                if can_split:
                    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=7, stratify=y)
                    probe = make_pipeline(DictVectorizer(), LogisticRegression(max_iter=2000, C=2.0))
                    probe.fit(X_tr, y_tr)
                    acc = float(probe.score(X_te, y_te))
                    evaluation = f"held-out ({len(y_te)} examples)"
                else:
                    acc = None
                    evaluation = "too few examples for a held-out split"
                model = make_pipeline(DictVectorizer(), LogisticRegression(max_iter=2000, C=2.0))
                model.fit(X, y)
                heads[head] = {"model": model}
                metrics[head] = {
                    "classes": len(classes),
                    "accuracy": None if acc is None else round(acc, 4),
                    "train_accuracy": round(float(model.score(X, y)), 4),
                    "evaluation": evaluation,
                }

            version = f"nano-v{len(self.registry) + 1}"
            filename = f"astrix-{version}.joblib"
            path = self.dir / filename
            bundle = {"version": version, "heads": heads}
            joblib.dump(bundle, path)
            stats = self.corpus.stats()
            entry = {
                "version": version,
                "file": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "examples": len(examples),
                "only_verified": only_verified,
                "corpus_chain_head": stats["chain_head"],
                "metrics": metrics,
            }
            self.registry.append(entry)
            self.registry_path.write_text(json.dumps(self.registry, indent=2))
            self._bundle = bundle
            self.shadow = {"compared": 0, "agreed": 0}
            log.info("trained %s on %d examples", version, len(examples))
            return {"trained": True, **entry}
        finally:
            self._training = False

    # -- inference ---------------------------------------------------------- #

    def predict(self, situation: dict[str, Any]) -> dict[str, Any] | None:
        bundle = self._bundle
        if bundle is None:
            return None
        features = featurize(situation)
        out: dict[str, Any] = {"version": bundle["version"]}
        for head, spec in bundle["heads"].items():
            if "constant" in spec:
                out[head] = {"label": spec["constant"], "confidence": 1.0}
                continue
            model = spec["model"]
            proba = model.predict_proba([features])[0]
            best = int(proba.argmax())
            out[head] = {"label": str(model.classes_[best]), "confidence": round(float(proba[best]), 3)}
        return out

    def shadow_compare(self, example: dict[str, Any]) -> None:
        """Score the student against the decision the agents actually made."""
        prediction = self.predict(example["situation"])
        if prediction is None:
            return
        agreed = all(prediction[h]["label"] == str(example["labels"].get(h) or "none") for h in ("subsystem", "action_id"))
        self.shadow["compared"] += 1
        self.shadow["agreed"] += int(agreed)

    def status(self) -> dict[str, Any]:
        compared = self.shadow["compared"]
        return {
            "version": self.version,
            "training": self._training,
            "runs": self.registry[-10:],
            "shadow": {
                **self.shadow,
                "agreement": round(self.shadow["agreed"] / compared, 4) if compared else None,
            },
            "min_examples": MIN_EXAMPLES,
        }
