"""Central configuration.

Anything a jury might ask us to change live — thresholds, weights, autonomy
limit, model id — is here or in `safety/limits.yaml`, never hard-coded in a
decision path (master context §31).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .core.enums import RiskLevel

# repo root = .../Astrix AI
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
MODEL_DIR = DATA_DIR / "models"

# pydantic-settings reads .env into Settings only; the Anthropic SDK looks in
# os.environ. Export .env so ANTHROPIC_API_KEY set there reaches the SDK.
# Real environment variables still win (override=False).
load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_prefix="ASTRIX_",
        extra="ignore",
        protected_namespaces=(),
    )

    # ---------- service ----------
    app_name: str = "Astrix-AI"
    tagline: str = "Detect. Reason. Recover. Learn."
    api_base: str = "http://127.0.0.1:8000"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
        ]
    )

    # ---------- agent reasoning (multi-model gateway) ----------
    llm_enabled: bool = True
    llm_provider: str = "auto"  # "auto", "anthropic", "gemini", "groq", "openai", "local"
    llm_model: str = "claude-3-7-sonnet-20250219"
    llm_fast_model: str = "claude-3-5-haiku-20241022"
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 30.0
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_base: str = "https://api.openai.com/v1"
    local_model_url: str = "http://localhost:11434/v1"
    thinking_budget_tokens: int = 1024

    # ---------- memory ----------
    database_url: str = f"sqlite:///{(DATA_DIR / 'astrix.db').as_posix()}"
    vector_backend: str = "json"
    vector_path: str = str(DATA_DIR / "vector_store.json")
    vector_top_k: int = 4
    vector_min_similarity: float = 0.18

    # ---------- detection ----------
    telemetry_window: int = 180  # frames retained per spacecraft
    detector_path: str = str(MODEL_DIR / "isolation_forest.joblib")
    detector_contamination: float = 0.02
    # At least two full orbits (5400 s each), so eclipse, imaging, ground contact
    # and slews are all represented; less than one orbit leaves late-orbit battery
    # and thermal states outside the learned envelope.
    detector_train_frames: int = 11000
    detector_validation_frames: int = 5400

    # Weights for the context-aware risk score (§18). They are normalised at
    # use time, so these are relative importances, not a partition of 1.0.
    weight_ml_score: float = 0.40
    weight_context_deviation: float = 0.15
    weight_subsystem_correlation: float = 0.15
    weight_historical_similarity: float = 0.10
    weight_persistence: float = 0.10
    weight_mission_impact: float = 0.10

    # Final-score cut points for the four severity levels.
    severity_watch: float = 0.35
    severity_warning: float = 0.55
    severity_critical: float = 0.75

    # Exponential smoothing applied to the raw Isolation Forest score before it
    # enters the context score. A per-frame score on a single sample is noisy
    # enough to cross a severity threshold on sensor noise alone; smoothing the
    # input (rather than the output) keeps the published score breakdown
    # internally consistent. Lower = smoother and slower to react.
    ml_smoothing_alpha: float = 0.25

    # Hysteresis band around each severity cut point. A severity must exceed a
    # threshold by this margin to escalate and fall below it by the same margin
    # to de-escalate, so a score resting on a boundary does not strobe between
    # two levels frame after frame.
    severity_hysteresis: float = 0.05

    # ML score above which a frame counts toward an "elevated" streak, feeding the
    # persistence term. Below it a streak resets.
    persistence_ml_threshold: float = 0.45
    # ML score above which mission memory is queried. Retrieving history for every
    # nominal frame is wasted work; below this the recall term is simply zero.
    recall_ml_threshold: float = 0.30
    # Frames to observe after executing a recovery before scoring its real outcome.
    outcome_evaluation_frames: int = 30

    # ---------- autonomy ----------
    auto_execute_max_risk: RiskLevel = RiskLevel.GREEN
    simulation_horizon_seconds: int = 60
    simulation_step_seconds: float = 1.0

    @property
    def severity_cutpoints(self) -> tuple[float, float, float]:
        return (self.severity_watch, self.severity_warning, self.severity_critical)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "telemetry").mkdir(parents=True, exist_ok=True)
    return settings
