"""Validated JSON configuration and reproducible configuration snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .exceptions import ConfigurationError

GAME_RULES_VERSION = "slp-5of47-mega-1of27-v1"
MODEL_VERSION = "slp-adaptive-bundle-v2"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GameConfig(FrozenModel):
    name: Literal["SuperLotto Plus"] = "SuperLotto Plus"
    main_min: Literal[1] = 1
    main_max: Literal[47] = 47
    main_count: Literal[5] = 5
    mega_min: Literal[1] = 1
    mega_max: Literal[27] = 27
    draw_timezone: Literal["America/Los_Angeles"] = "America/Los_Angeles"
    draw_weekdays: tuple[int, int] = (2, 5)  # Wednesday and Saturday
    official_post_time_pacific: time = time(20, 0)
    rules_version: Literal["slp-5of47-mega-1of27-v1"] = "slp-5of47-mega-1of27-v1"

    @field_validator("draw_weekdays")
    @classmethod
    def exact_draw_weekdays(cls, value: tuple[int, int]) -> tuple[int, int]:
        if tuple(value) != (2, 5):
            raise ValueError("SuperLotto Plus draw weekdays must be Wednesday and Saturday")
        return value

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.draw_timezone)


class SourceEndpointConfig(FrozenModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=8)
    role: Literal["official", "backup"]
    enabled: bool = True


class SourcesConfig(FrozenModel):
    endpoints: tuple[SourceEndpointConfig, ...] = (
        SourceEndpointConfig(
            name="california_lottery",
            role="official",
            url="https://www.calottery.com/draw-games/superlotto-plus",
        ),
        SourceEndpointConfig(
            name="lotteryusa",
            role="backup",
            url="https://www.lotteryusa.com/california/super-lotto-plus/",
        ),
        SourceEndpointConfig(
            name="lottery_net",
            role="backup",
            url="https://www.lottery.net/california/superlotto-plus/numbers",
        ),
        SourceEndpointConfig(
            name="lotterycorner",
            role="backup",
            url="https://www.lotterycorner.com/ca/superlotto-plus/",
            enabled=False,
        ),
    )
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    retries: int = Field(default=3, ge=0, le=10)
    backoff_seconds: float = Field(default=0.75, ge=0, le=30)
    cache_ttl_seconds: int = Field(default=900, ge=0)
    user_agent: str = "slp-production-audit/1.0 (+https://github.com/fabiansierra55-bit/Super)"
    minimum_agreeing_sources: Literal[2] = 2

    @model_validator(mode="after")
    def require_official_and_backup(self) -> SourcesConfig:
        enabled = [endpoint for endpoint in self.endpoints if endpoint.enabled]
        if sum(endpoint.role == "official" for endpoint in enabled) != 1:
            raise ValueError("exactly one official source must be enabled")
        if not any(endpoint.role == "backup" for endpoint in enabled):
            raise ValueError("at least one approved backup source must be enabled")
        return self


class TrainingConfig(FrozenModel):
    windows: tuple[int, ...] = (60, 90, 120, 180, 240)
    anchor_window: Literal[240] = 240
    anchor_min_relative_improvement: float = Field(default=0.01, ge=0)
    main_sigmas: tuple[float, ...] = (1.0, 1.125, 1.15, 1.3)
    mega_sigmas: tuple[float, ...] = (0.9, 1.0, 1.15, 1.3)
    half_lives_draws: tuple[float, ...] = (16, 20, 24, 28, 36, 45, 60)
    forward_folds: int = Field(default=12, ge=3)
    forward_bundle_size: int = Field(default=30, ge=3, le=100)
    likelihood_stability_margin: float = Field(default=1.5, ge=0)
    reselection_interval_scored_draws: int = Field(default=10, ge=1)
    drift_calibration_error_threshold: float = Field(default=0.12, ge=0)
    drift_underperformance_draws: int = Field(default=5, ge=2)

    @model_validator(mode="after")
    def validate_grids(self) -> TrainingConfig:
        if self.windows != (60, 90, 120, 180, 240):
            raise ValueError("training windows must be exactly 60, 90, 120, 180, 240")
        if self.main_sigmas != (1.0, 1.125, 1.15, 1.3):
            raise ValueError("main sigma grid does not match the production specification")
        if self.mega_sigmas != (0.9, 1.0, 1.15, 1.3):
            raise ValueError("Mega sigma grid does not match the production specification")
        if not self.half_lives_draws or any(value <= 0 for value in self.half_lives_draws):
            raise ValueError("half-life grid must contain positive draw counts")
        return self


class SimulationConfig(FrozenModel):
    candidate_pool_size: int = Field(default=50_000, ge=50_000)
    optimization_draws: int = Field(default=4_096, ge=2_048)
    recenter_evaluation_draws: int = Field(default=10_000, ge=2_000)
    initial_draws: int = Field(default=50_000, ge=1_000)
    batch_draws: int = Field(default=25_000, ge=1_000)
    maximum_draws: int = Field(default=400_000, ge=1_000)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)
    confidence_half_width_tolerance: float = Field(default=0.0025, gt=0)
    stable_batches_required: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def maximum_covers_initial(self) -> SimulationConfig:
        if self.maximum_draws < self.initial_draws:
            raise ValueError("maximum_draws must be at least initial_draws")
        return self


class BundleConfig(FrozenModel):
    size: int = Field(default=30, ge=1)
    aggressive_count: int = Field(default=10, ge=0)
    balanced_count: int = Field(default=10, ge=0)
    conservative_count: int = Field(default=10, ge=0)
    max_main_overlap: int = Field(default=3, ge=0, le=5)
    min_hamming_distance: int = Field(default=2, ge=0, le=5)
    pair_repeat_cap: int = Field(default=2, ge=1)
    triple_repeat_cap: int = Field(default=1, ge=1)
    mega_soft_cap: int = Field(default=4, ge=1)
    mega_hard_cap: int = Field(default=5, ge=1)
    aggressive_previous_draw_overlap_cap: int = Field(default=1, ge=0, le=5)
    adjacency_allowed: Literal[True] = True
    parity_rule: Literal[False] = False
    band_rule: Literal[False] = False
    positional_recentering: Literal["mild"] = "mild"
    recenter_strength: float = Field(default=0.15, ge=0, le=0.25)

    @model_validator(mode="after")
    def validate_counts_and_caps(self) -> BundleConfig:
        if self.aggressive_count + self.balanced_count + self.conservative_count != self.size:
            raise ValueError("tier counts must add up to bundle size")
        if self.mega_soft_cap > self.mega_hard_cap:
            raise ValueError("Mega soft cap cannot exceed hard cap")
        return self


class ObjectiveConfig(FrozenModel):
    mode: Literal["grind", "spike"] = "grind"
    p_ge_3_weight: float = Field(default=1.0, ge=0)
    p_ge_4_weight: float = Field(default=0.15, ge=0)
    three_plus_mega_weight: float = Field(default=0.10, ge=0)
    four_plus_weight: float = Field(default=0.10, ge=0)
    anti_cannibalization_weight: float = Field(default=0.05, ge=0)
    mega_repeat_penalty: float = Field(default=0.01, ge=0)
    aggressive_secondary_multiplier: float = Field(default=1.25, ge=1)

    @model_validator(mode="after")
    def preserve_primary_objective(self) -> ObjectiveConfig:
        if self.mode == "grind" and self.p_ge_3_weight <= 0:
            raise ValueError("grind mode requires a positive P(>=3 mains) weight")
        return self


class PathsConfig(FrozenModel):
    data_dir: Path = Path("data")
    history_dir: Path = Path("data/history")
    predictions_dir: Path = Path("data/predictions")
    scoring_dir: Path = Path("data/scoring")
    calibration_dir: Path = Path("data/calibration")
    audit_dir: Path = Path("data/audit")
    reports_dir: Path = Path("reports")
    cache_dir: Path = Path(".cache/slp")


class GitConfig(FrozenModel):
    artifact_branch_prefix: str = "automation/slp-cycle"
    protected_branch: Literal["main"] = "main"
    expected_repository: Literal["fabiansierra55-bit/Super"] = "fabiansierra55-bit/Super"
    auto_publish: bool = False


class AppConfig(FrozenModel):
    game: GameConfig = GameConfig()
    sources: SourcesConfig = SourcesConfig()
    training: TrainingConfig = TrainingConfig()
    simulation: SimulationConfig = SimulationConfig()
    bundle: BundleConfig = BundleConfig()
    objective: ObjectiveConfig = ObjectiveConfig()
    paths: PathsConfig = PathsConfig()
    git: GitConfig = GitConfig()
    model_version: str = MODEL_VERSION

    @classmethod
    def load(cls, path: str | Path | None = None) -> AppConfig:
        candidate = Path(path) if path else Path("config.json")
        if not candidate.exists():
            return cls()
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ConfigurationError(f"invalid configuration {candidate}: {exc}") from exc

    def snapshot(self) -> dict[str, object]:
        """Return the JSON-safe configuration embedded in every locked bundle."""

        return self.model_dump(mode="json")

    def snapshot_sha256(self) -> str:
        payload = json.dumps(
            self.snapshot(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def resolve_path(project_root: Path, path: Path) -> Path:
    """Resolve a configured path while preventing accidental writes outside the project."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    resolved = resolved.resolve()
    if resolved != root and root not in resolved.parents:
        raise ConfigurationError(f"configured path escapes project root: {path}")
    return resolved
