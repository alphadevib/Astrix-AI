"""Composition root.

Everything is constructed here, once, and handed to the pipeline. No module
reaches for a global; if a component needs the detector or the memory store, it
is given one. That is what makes the stages individually testable and lets n8n
drive them out of order without surprises.

Detector training happens at startup if no model file exists: the simulator
generates several orbits of fault-free telemetry and the Isolation Forest fits on
it. Training on the *simulator* rather than on live frames is deliberate — a
detector trained on whatever happens to be arriving would learn the fault as
normal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .agents.llm import AgentLLM
from .auth.conversations import ConversationStore
from .auth.service import AuthService
from .config import Settings, get_settings
from .memory.db import build_session_factory, init_db
from .memory.seed import seed_all
from .memory.store import MissionMemory
from .memory.vector import build_vector_store
from .ml.context import ContextScorer
from .ml.detector import AnomalyDetector
from .safety.engine import SafetyEngine
from .services.buffer import TelemetryBuffer
from .services.bus import EventBus
from .services.pipeline import AstrixPipeline
from .simulation.twin import DigitalTwin

log = logging.getLogger(__name__)


class Astrix:
    """Container for every long-lived ASTRIX component."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        s = self.settings

        # ---------- memory ----------
        self.engine = init_db(s.database_url)
        self.session_factory = build_session_factory(self.engine)
        self.vectors = build_vector_store(s.vector_backend, s.vector_path)
        self.memory = MissionMemory(self.session_factory, self.vectors)
        seeded = seed_all(self.session_factory, self.vectors)
        log.info(
            "mission memory ready (structured seeded=%s, documents added=%d, total documents=%d)",
            seeded["structured_seeded"],
            seeded["documents_added"],
            self.vectors.count(),
        )

        # ---------- accounts ----------
        # Operator sign-in and per-account conversation threads share the mission
        # database, so a deployment has one thing to back up, not two.
        self.auth = AuthService(self.session_factory, s.session_ttl_hours)
        self.conversations = ConversationStore(self.session_factory)
        expired = self.auth.purge_expired()
        log.info(
            "accounts ready (%d registered, %d expired session(s) purged)",
            self.auth.count_users(),
            expired,
        )

        # ---------- detection ----------
        self.detector = self._load_or_train_detector()
        self.scorer = ContextScorer(s)

        # ---------- safety and simulation ----------
        self.safety = SafetyEngine(auto_execute_max_risk=s.auto_execute_max_risk)
        self.twin = DigitalTwin(self.safety.simulation_config)

        # ---------- reasoning ----------
        self.llm = AgentLLM(s)
        if self.llm.available:
            status = self.llm.status
            log.info("agent reasoning: LLM enabled (%s · %s)", status.get("provider_label"), status.get("model"))
        else:
            log.warning(
                "agent reasoning: DETERMINISTIC only — %s", self.llm.status["reason"]
            )

        # ---------- orchestration ----------
        self.buffer = TelemetryBuffer(maxlen=s.telemetry_window)
        self.bus = EventBus()
        self.pipeline = AstrixPipeline(
            settings=s,
            detector=self.detector,
            scorer=self.scorer,
            memory=self.memory,
            safety=self.safety,
            twin=self.twin,
            llm=self.llm,
            buffer=self.buffer,
            bus=self.bus,
        )

        # ---------- hardware-in-the-loop ----------
        from .hardware import HardwareHub

        self.hardware = HardwareHub(self.bus, stale_seconds=s.hardware_stale_seconds)

        # ---------- Astrix-LM training corpus ----------
        self.model_lab = None
        if s.corpus_enabled:
            try:
                from .training import ModelLab

                self.model_lab = ModelLab(
                    s.corpus_path,
                    s.corpus_key,
                    s.corpus_auto_train_every,
                    self.bus,
                    mission_summaries=s.mission_summaries_enabled,
                    open_world=s.open_world_diagnosis,
                    novelty_threshold=s.novelty_similarity_threshold,
                )
                log.info("training corpus ready (%d examples)", self.model_lab.corpus.count())
            except Exception as exc:  # noqa: BLE001 — learning must never block operations
                log.error("training corpus unavailable: %s", exc)

        # On-board Small Language & Neural Decision Model
        from .training.small_llm import AstrixSmallLM

        self.small_lm = self.model_lab.small_lm if self.model_lab else AstrixSmallLM()
        # Astrix's own model is a first-class reasoner, not just an endpoint: the
        # gateway routes to it through its own lazy accessor, which is what lets
        # the console answer an operational question with no third-party API.
        if self.small_lm.is_trained:
            log.info("on-board Small LM available as a reasoner (%s)", self.small_lm.version)

        # Set by main.py once the app owns an event loop.
        self.runner = None

    # ------------------------------------------------------------------ #

    def _load_or_train_detector(self) -> AnomalyDetector:
        path = Path(self.settings.detector_path)
        if path.exists():
            try:
                detector = AnomalyDetector.load(path)
                log.info("loaded anomaly detector from %s (%d training frames)", path, detector.n_train)
                return detector
            except (ValueError, KeyError, EOFError) as exc:
                log.warning("stored detector unusable (%s); retraining", exc)

        detector = AnomalyDetector(contamination=self.settings.detector_contamination)
        try:
            from telemetry.simulator import generate_nominal_frames
        except ImportError as exc:
            log.error(
                "cannot train the detector: the telemetry simulator is not importable (%s). "
                "Run uvicorn from the repository root so `telemetry` is on sys.path. "
                "ASTRIX will start, but detection will report 'untrained'.",
                exc,
            )
            return detector

        count = self.settings.detector_train_frames
        log.info("training anomaly detector on %d fault-free frames (first run only)...", count)
        frames = generate_nominal_frames(count)
        # Calibrated on a separate orbit (different noise seed): thresholds set on
        # the training data itself understate how far healthy telemetry wanders.
        validation = generate_nominal_frames(self.settings.detector_validation_frames, seed=11)
        detector.fit(frames, validation=validation)
        detector.save(self.settings.detector_path)
        log.info("detector trained and saved to %s", self.settings.detector_path)
        return detector

    def health(self) -> dict:
        return {
            "app": self.settings.app_name,
            "tagline": self.settings.tagline,
            "detector_fitted": self.detector.is_fitted,
            "detector_training_frames": self.detector.n_train,
            "llm": self.llm.status,
            "memory": self.memory.stats(),
            "autonomy_limit": self.settings.auto_execute_max_risk.value,
            "vector_backend": self.settings.vector_backend,
            "database": self.settings.database_url.split("://")[0],
            "hardware": {"connected": self.hardware.connected},
            "corpus_examples": self.model_lab.corpus.count() if self.model_lab else None,
            "accounts": self.auth.count_users(),
        }
