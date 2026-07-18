"""Validated domain models for draws, locked bundles, and scoring artifacts."""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Strategy = Literal["aggressive", "balanced", "conservative"]
SourceRole = Literal["official", "backup"]
VerificationStatus = Literal["verified", "mismatch", "unverified", "not_posted"]


def _sorted_valid_mains(value: tuple[int, ...], *, label: str) -> tuple[int, int, int, int, int]:
    if len(value) != 5 or len(set(value)) != 5:
        raise ValueError(f"{label} must contain five unique numbers")
    if any(number < 1 or number > 47 for number in value):
        raise ValueError("main number outside 1-47")
    sorted_value = tuple(sorted(value))
    return (sorted_value[0], sorted_value[1], sorted_value[2], sorted_value[3], sorted_value[4])


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_aware_utc(value: datetime | None) -> datetime | None:
    return _aware_utc(value) if value is not None else None


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Draw(FrozenModel):
    draw_date: date
    mains: tuple[int, int, int, int, int]
    mega: int
    draw_id: str | None = None

    @field_validator("mains")
    @classmethod
    def validate_mains(
        cls, value: tuple[int, int, int, int, int]
    ) -> tuple[int, int, int, int, int]:
        return _sorted_valid_mains(value, label="mains")

    @field_validator("mega")
    @classmethod
    def validate_mega(cls, value: int) -> int:
        if not 1 <= value <= 27:
            raise ValueError("mega outside 1-27")
        return value

    @field_validator("draw_id")
    @classmethod
    def nonempty_draw_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("draw_id cannot be blank")
        return value


class Ticket(FrozenModel):
    mains: tuple[int, int, int, int, int]
    mega: int

    @field_validator("mains")
    @classmethod
    def validate_mains(
        cls, value: tuple[int, int, int, int, int]
    ) -> tuple[int, int, int, int, int]:
        return _sorted_valid_mains(value, label="ticket mains")

    @field_validator("mega")
    @classmethod
    def validate_mega(cls, value: int) -> int:
        if not 1 <= value <= 27:
            raise ValueError("mega outside 1-27")
        return value


class SourceEvidence(FrozenModel):
    source_name: str = Field(min_length=1)
    role: SourceRole
    source_url: str = Field(min_length=8)
    fetched_timestamp_utc: datetime
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    draw_id: str | None = None
    parser_version: str = Field(min_length=1)
    http_status: int | None = Field(default=None, ge=100, le=599)
    cache_hit: bool = False

    _normalize_fetched = field_validator("fetched_timestamp_utc")(_aware_utc)


class VerificationMetadata(FrozenModel):
    status: VerificationStatus
    verified_timestamp_utc: datetime | None = None
    official_post_timestamp_utc: datetime
    sources: tuple[SourceEvidence, ...] = ()
    comparison_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mismatch_details: str | None = None

    _normalize_verified = field_validator("verified_timestamp_utc")(_optional_aware_utc)
    _normalize_posted = field_validator("official_post_timestamp_utc")(_aware_utc)

    @model_validator(mode="after")
    def verified_requires_two_sources(self) -> VerificationMetadata:
        if self.status == "verified":
            if self.verified_timestamp_utc is None:
                raise ValueError("verified metadata requires verified_timestamp_utc")
            if len(self.sources) < 2:
                raise ValueError("verified metadata requires at least two sources")
            if sum(source.role == "official" for source in self.sources) != 1:
                raise ValueError("verified metadata requires exactly one official source")
            if not any(source.role == "backup" for source in self.sources):
                raise ValueError("verified metadata requires an approved backup source")
            if not self.comparison_sha256:
                raise ValueError("verified metadata requires a comparison hash")
        return self


class VerifiedDraw(Draw):
    verification: VerificationMetadata

    @model_validator(mode="after")
    def must_be_verified(self) -> VerifiedDraw:
        if self.verification.status != "verified":
            raise ValueError("VerifiedDraw requires verified source metadata")
        return self


class SelectedHyperparameters(FrozenModel):
    main_window: int = Field(ge=60)
    mega_window: int = Field(ge=60)
    main_sigma: float = Field(gt=0)
    mega_sigma: float = Field(gt=0)
    main_half_life_draws: float = Field(gt=0)
    mega_half_life_draws: float = Field(gt=0)
    selected_by: Literal["forward_bundle_simulation"] = "forward_bundle_simulation"
    forward_objective: float
    heldout_log_likelihood: float
    anchor_240_accepted: bool = False
    selection_timestamp_utc: datetime
    training_draw_count: int = Field(ge=60)

    _normalize_selected = field_validator("selection_timestamp_utc")(_aware_utc)


class ExactUniformMetrics(FrozenModel):
    """Exact coverage when all valid main/Mega outcomes are equiprobable."""

    method: Literal["exact-uniform-enumeration-v1"] = "exact-uniform-enumeration-v1"
    main_draw_outcome_count: Literal[1_533_939]
    full_draw_outcome_count: Literal[41_416_353]
    covered_ge_3_mains_count: int = Field(ge=0)
    covered_ge_4_mains_count: int = Field(ge=0)
    covered_3_plus_mega_count: int = Field(ge=0)
    covered_4_plus_mega_count: int = Field(ge=0)
    covered_5_mains_count: int = Field(ge=0)
    covered_jackpot_count: int = Field(ge=0)
    p_any_ge_3_mains: float = Field(ge=0, le=1)
    p_any_ge_4_mains: float = Field(ge=0, le=1)
    p_any_3_plus_mega: float = Field(ge=0, le=1)
    p_any_4_plus_mega: float = Field(ge=0, le=1)
    p_any_5_mains: float = Field(ge=0, le=1)
    p_jackpot: float = Field(ge=0, le=1)
    mean_best_main_matches: float = Field(ge=0, le=5)
    best_match_histogram: tuple[int, int, int, int, int, int]

    @model_validator(mode="after")
    def validate_exact_arithmetic(self) -> ExactUniformMetrics:
        if self.full_draw_outcome_count != self.main_draw_outcome_count * 27:
            raise ValueError("full fair-outcome denominator must equal mains times 27")
        if sum(self.best_match_histogram) != self.main_draw_outcome_count:
            raise ValueError("fair best-match histogram does not cover the main outcome space")
        if any(count < 0 for count in self.best_match_histogram):
            raise ValueError("fair best-match histogram cannot contain negative counts")
        if (
            sum(self.best_match_histogram[3:]) != self.covered_ge_3_mains_count
            or sum(self.best_match_histogram[4:]) != self.covered_ge_4_mains_count
            or self.best_match_histogram[5] != self.covered_5_mains_count
        ):
            raise ValueError("fair event numerators do not match the best-match histogram")
        if not (
            self.covered_5_mains_count
            <= self.covered_ge_4_mains_count
            <= self.covered_ge_3_mains_count
            <= self.main_draw_outcome_count
        ):
            raise ValueError("fair main-event numerators are not properly nested")
        if not (
            self.covered_jackpot_count
            <= self.covered_4_plus_mega_count
            <= self.covered_3_plus_mega_count
            <= self.full_draw_outcome_count
        ):
            raise ValueError("fair Mega-event numerators are not properly nested")
        expected = (
            (self.p_any_ge_3_mains, self.covered_ge_3_mains_count / self.main_draw_outcome_count),
            (self.p_any_ge_4_mains, self.covered_ge_4_mains_count / self.main_draw_outcome_count),
            (self.p_any_3_plus_mega, self.covered_3_plus_mega_count / self.full_draw_outcome_count),
            (self.p_any_4_plus_mega, self.covered_4_plus_mega_count / self.full_draw_outcome_count),
            (self.p_any_5_mains, self.covered_5_mains_count / self.main_draw_outcome_count),
            (self.p_jackpot, self.covered_jackpot_count / self.full_draw_outcome_count),
        )
        if any(
            not math.isclose(value, calculated, abs_tol=1e-15) for value, calculated in expected
        ):
            raise ValueError("fair probability does not match its exact numerator")
        histogram_mean = (
            sum(matches * count for matches, count in enumerate(self.best_match_histogram))
            / self.main_draw_outcome_count
        )
        if not math.isclose(self.mean_best_main_matches, histogram_mean, abs_tol=1e-15):
            raise ValueError("fair mean does not match its best-match histogram")
        return self


class SimulationSummary(FrozenModel):
    simulation_count: int = Field(ge=1_000)
    candidate_pool_size: int = Field(ge=50_000)
    confidence_level: float = Field(gt=0.5, lt=1)
    maximum_confidence_half_width: float = Field(ge=0)
    stable: bool
    stable_batches: int = Field(default=0, ge=0)
    p_any_ge_3_mains: float = Field(ge=0, le=1)
    p_any_ge_4_mains: float = Field(ge=0, le=1)
    p_any_3_plus_mega: float = Field(ge=0, le=1)
    # Kept for schema compatibility with the handoff's ambiguous field name.
    # New artifacts define it as the official 4+Mega-or-better event.
    p_any_4_plus: float = Field(ge=0, le=1)
    p_any_4_plus_mega: float = Field(default=0.0, ge=0, le=1)
    mean_best_main_matches: float = Field(ge=0, le=5)
    fair_uniform_exact: ExactUniformMetrics | None = None


class FairCoverageChallengerEvidence(FrozenModel):
    evidence_version: Literal[1, 2, 3, 4] = 1
    selection_policy: Literal[
        "legacy_exact_fair_gate",
        "fair_null_robustness_over_unvalidated_model_v1",
    ] = "legacy_exact_fair_gate"
    model_skill_status: Literal["unvalidated", "validated"] = "unvalidated"
    selected: bool
    global_optimum_certified: bool = False
    certificate_bundle_size: int | None = Field(default=None, ge=1)
    selection_reason: str = Field(min_length=1)
    minimum_relative_improvement: float = Field(ge=0)
    relative_primary_improvement: float
    model_optimized_candidate: ExactUniformMetrics
    challenger: ExactUniformMetrics
    incumbent: ExactUniformMetrics | None = None
    model_optimized_simulation: SimulationSummary | None = None
    challenger_model_simulation: SimulationSummary | None = None
    incumbent_model_simulation: SimulationSummary | None = None
    relative_challenger_model_p_ge_3_change: float | None = None
    relative_primary_change_vs_incumbent: float | None = None

    @model_validator(mode="after")
    def validate_promotion_evidence(self) -> FairCoverageChallengerEvidence:
        if self.evidence_version >= 4:
            if self.certificate_bundle_size is None:
                raise ValueError("version 4 evidence requires certificate_bundle_size")
            certificate_bundle_size = self.certificate_bundle_size
            if certificate_bundle_size != self.challenger.covered_jackpot_count:
                raise ValueError("certificate bundle size does not match challenger tickets")
        else:
            # Evidence versions 1-3 encoded the original 30-line certificate
            # implicitly. Preserve those immutable artifact semantics.
            if self.certificate_bundle_size is not None:
                raise ValueError(
                    "evidence versions before 4 cannot declare certificate_bundle_size"
                )
            certificate_bundle_size = 30

        # Imported lazily because fair_odds depends on these domain models.
        from .fair_odds import fair_coverage_certificate

        expected_certificate = fair_coverage_certificate(certificate_bundle_size)
        certificate = (
            self.challenger.covered_ge_3_mains_count
            == expected_certificate.covered_ge_3_mains_count
            and self.challenger.covered_ge_4_mains_count
            == expected_certificate.covered_ge_4_mains_count
            and self.challenger.covered_3_plus_mega_count
            == expected_certificate.covered_3_plus_mega_count
            and self.challenger.covered_4_plus_mega_count
            == expected_certificate.covered_4_plus_mega_count
            and self.challenger.covered_5_mains_count == expected_certificate.bundle_size
            and self.challenger.covered_jackpot_count == expected_certificate.covered_jackpot_count
        )
        if self.global_optimum_certified != certificate:
            raise ValueError("fair global-certificate flag does not match exact metrics")
        guarded_references = [self.model_optimized_candidate]
        if self.incumbent is not None:
            guarded_references.append(self.incumbent)
        reference_primary = (
            self.model_optimized_candidate.p_any_ge_3_mains
            if self.evidence_version >= 3
            else max(item.p_any_ge_3_mains for item in guarded_references)
        )
        expected_relative = self.challenger.p_any_ge_3_mains / reference_primary - 1.0
        if not math.isclose(self.relative_primary_improvement, expected_relative, abs_tol=1e-15):
            raise ValueError("fair primary-improvement claim does not match its references")
        if self.selected:
            if self.challenger.p_any_ge_3_mains + 1e-15 < reference_primary * (
                1.0 + self.minimum_relative_improvement
            ):
                raise ValueError("selected fair challenger misses its primary threshold")
            if (
                self.incumbent is not None
                and self.challenger.p_any_ge_3_mains + 1e-15 < self.incumbent.p_any_ge_3_mains
            ):
                raise ValueError("selected fair challenger regresses incumbent 3+ coverage")
            if self.challenger.p_any_ge_4_mains + 1e-15 < max(
                item.p_any_ge_4_mains for item in guarded_references
            ):
                raise ValueError("selected fair challenger regresses exact 4+ coverage")
            if self.challenger.p_jackpot + 1e-15 < max(
                item.p_jackpot for item in guarded_references
            ):
                raise ValueError("selected fair challenger regresses jackpot coverage")
        if self.evidence_version >= 3 and self.incumbent is not None:
            expected_incumbent_change = (
                self.challenger.p_any_ge_3_mains / self.incumbent.p_any_ge_3_mains - 1.0
            )
            if self.relative_primary_change_vs_incumbent is None or not math.isclose(
                self.relative_primary_change_vs_incumbent,
                expected_incumbent_change,
                abs_tol=1e-15,
            ):
                raise ValueError("incumbent fair-coverage comparison is inconsistent")
        if self.evidence_version == 1:
            return self
        if self.selection_policy != "fair_null_robustness_over_unvalidated_model_v1":
            raise ValueError("versioned challenger evidence requires the robustness policy")
        if self.model_skill_status != "unvalidated":
            raise ValueError("the fair-null robustness policy requires unvalidated model skill")
        if self.model_optimized_simulation is None or self.challenger_model_simulation is None:
            raise ValueError("versioned challenger evidence requires model estimates")
        if (
            not self.model_optimized_simulation.stable
            or not self.challenger_model_simulation.stable
        ):
            raise ValueError("challenger comparison simulations must be stable")
        if self.incumbent is not None and self.incumbent_model_simulation is None:
            raise ValueError("versioned correction evidence requires incumbent model estimates")
        model_primary = self.model_optimized_simulation.p_any_ge_3_mains
        if model_primary <= 0:
            raise ValueError("model candidate requires positive model-conditional P(>=3)")
        expected_model_change = (
            self.challenger_model_simulation.p_any_ge_3_mains / model_primary - 1.0
        )
        if self.relative_challenger_model_p_ge_3_change is None or not math.isclose(
            self.relative_challenger_model_p_ge_3_change,
            expected_model_change,
            abs_tol=1e-15,
        ):
            raise ValueError("model-conditional tradeoff does not match stored simulations")
        if (
            self.model_optimized_simulation.fair_uniform_exact != self.model_optimized_candidate
            or self.challenger_model_simulation.fair_uniform_exact != self.challenger
        ):
            raise ValueError("model simulations are not bound to their exact fair candidates")
        if (
            self.incumbent is not None
            and self.incumbent_model_simulation is not None
            and self.incumbent_model_simulation.fair_uniform_exact != self.incumbent
        ):
            raise ValueError("incumbent simulation is not bound to its exact fair metrics")
        return self


class OptimizerSettings(FrozenModel):
    algorithm: str = Field(min_length=1)
    optimization_basis: Literal["adaptive_model_simulation", "exact_fair_uniform_coverage"] = (
        "adaptive_model_simulation"
    )
    objective_mode: Literal["grind", "spike"] = "grind"
    objective_weights: dict[str, float]
    constraints: dict[str, int | float | bool | str]
    anti_cannibalization_weight: float = Field(ge=0)
    optimization_simulation_count: int = Field(default=0, ge=0)
    local_search_iterations: int = Field(default=0, ge=0)
    recenter_evaluation_seed: int | None = Field(default=None, ge=0)
    recenter_evaluation_simulations: int = Field(default=0, ge=0)
    recenter_original_objective: float | None = None
    recenter_proposed_objective: float | None = None
    recenter_accepted_count: int = Field(default=0, ge=0)
    recenter_decisions: tuple[dict[str, float | int | bool | str], ...] = ()
    marginal_contribution_basis: Literal["optimizer_selected_candidates", "final_locked_lines"] = (
        "optimizer_selected_candidates"
    )
    marginal_contributions: tuple[dict[str, float | int | str], ...] = ()
    fair_coverage_challenger: FairCoverageChallengerEvidence | None = None

    @model_validator(mode="after")
    def exact_basis_requires_certified_evidence(self) -> OptimizerSettings:
        if self.optimization_basis == "exact_fair_uniform_coverage" and (
            self.fair_coverage_challenger is None
            or not self.fair_coverage_challenger.selected
            or not self.fair_coverage_challenger.global_optimum_certified
        ):
            raise ValueError("exact fair optimization requires selected certified evidence")
        return self


class BundleMetadata(FrozenModel):
    bundle_id: str = Field(min_length=8, pattern=r"^[A-Za-z0-9._-]+$")
    generated_timestamp_utc: datetime
    intended_draw_date: date
    draw_id: str | None = None
    game_rules_version: Literal["slp-5of47-mega-1of27-v1"] = "slp-5of47-mega-1of27-v1"
    model_version: str = Field(min_length=1)
    runtime_environment: dict[str, str] = Field(default_factory=dict)
    configuration_snapshot: dict[str, Any]
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    random_seed: int = Field(ge=0)
    candidate_pool_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_pool_algorithm_version: str | None = Field(default=None, min_length=1)
    source_verification_metadata: VerificationMetadata
    history_cutoff_date: date
    history_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    calibration_id: str | None = Field(default=None, pattern=r"^cal-[0-9a-f]{20}$")
    calibration_random_seed: int | None = Field(default=None, ge=0)
    selected_hyperparameters: SelectedHyperparameters
    simulation: SimulationSummary
    optimizer: OptimizerSettings
    bundle_size: int = Field(default=30, ge=1)
    lock_version: int = Field(default=1, ge=1)
    supersedes_bundle_id: str | None = None
    correction_reason: str | None = Field(default=None, min_length=8)
    disclaimer: str = "Lottery outcomes are random; modeled bundles are not guarantees."

    _normalize_generated = field_validator("generated_timestamp_utc")(_aware_utc)

    @model_validator(mode="after")
    def history_must_precede_target(self) -> BundleMetadata:
        if self.history_cutoff_date >= self.intended_draw_date:
            raise ValueError("history cutoff must precede the intended draw")
        if self.intended_draw_date.weekday() not in (2, 5):
            raise ValueError("intended draw must be a Wednesday or Saturday")
        if self.source_verification_metadata.status != "verified":
            raise ValueError("bundle generation requires verified history source metadata")
        if not self.simulation.stable:
            raise ValueError("a locked production bundle requires stable simulation estimates")
        official_post = datetime.combine(
            self.intended_draw_date,
            time(20, 0),
            tzinfo=ZoneInfo("America/Los_Angeles"),
        ).astimezone(UTC)
        if self.generated_timestamp_utc >= official_post:
            raise ValueError("bundle must be generated before its intended draw post time")
        if self.lock_version == 1:
            if self.supersedes_bundle_id is not None or self.correction_reason is not None:
                raise ValueError("an initial bundle cannot claim correction metadata")
        elif self.supersedes_bundle_id is None or self.correction_reason is None:
            raise ValueError("a corrected bundle requires a parent ID and correction reason")
        evidence = self.optimizer.fair_coverage_challenger
        if (
            evidence is not None
            and evidence.evidence_version >= 4
            and evidence.certificate_bundle_size != self.bundle_size
        ):
            raise ValueError("fair certificate bundle size does not match locked bundle size")
        if (
            self.lock_version > 1
            and evidence is not None
            and evidence.evidence_version >= 2
            and (evidence.incumbent is None or evidence.incumbent_model_simulation is None)
        ):
            raise ValueError("versioned correction evidence must bind its incumbent bundle")
        if self.optimizer.optimization_basis == "exact_fair_uniform_coverage":
            if (
                self.candidate_pool_sha256 is None
                or self.simulation.fair_uniform_exact is None
                or evidence is None
                or self.simulation.fair_uniform_exact != evidence.challenger
            ):
                raise ValueError("exact fair bundle lacks bound pool, metrics, or evidence")
            if evidence.evidence_version >= 2 and self.candidate_pool_algorithm_version is None:
                raise ValueError(
                    "versioned fair evidence requires a candidate-pool algorithm version"
                )
        return self


class LockedLine(Ticket):
    strategy: Strategy
    line_id: int = Field(ge=1)


class LockedBundle(FrozenModel):
    metadata: BundleMetadata
    lines: tuple[LockedLine, ...]

    @model_validator(mode="after")
    def line_count_and_ids(self) -> LockedBundle:
        if len(self.lines) != self.metadata.bundle_size:
            raise ValueError("locked line count does not match bundle_size")
        keys = {(line.strategy, line.line_id) for line in self.lines}
        if len(keys) != len(self.lines):
            raise ValueError("duplicate strategy/line_id in locked bundle")
        if self.metadata.bundle_size % 3:
            raise ValueError("locked bundle size must support equal tier quotas")
        expected_per_tier = self.metadata.bundle_size // 3
        tier_counts = Counter(line.strategy for line in self.lines)
        if tier_counts != Counter(
            {
                "aggressive": expected_per_tier,
                "balanced": expected_per_tier,
                "conservative": expected_per_tier,
            }
        ):
            raise ValueError("locked bundle must contain equal, exact tier quotas")
        for strategy in ("aggressive", "balanced", "conservative"):
            line_ids = sorted(line.line_id for line in self.lines if line.strategy == strategy)
            if line_ids != list(range(1, expected_per_tier + 1)):
                raise ValueError(f"{strategy} line IDs must be sequential from one")
        return self


PrizeCategory = Literal[
    "Jackpot (5+Mega)",
    "5 mains",
    "4+Mega",
    "4 mains",
    "3+Mega",
    "3 mains",
    "2+Mega",
    "1+Mega",
    "Mega only",
    "No prize",
]


class ScoredLine(FrozenModel):
    strategy: Strategy
    line_id: int = Field(ge=1)
    mains: tuple[int, int, int, int, int]
    mega: int
    matched_mains: tuple[int, ...]
    main_match_count: int = Field(ge=0, le=5)
    mega_hit: bool
    prize_category: PrizeCategory

    @model_validator(mode="after")
    def score_is_consistent(self) -> ScoredLine:
        validated_mains = _sorted_valid_mains(self.mains, label="scored ticket mains")
        if validated_mains != self.mains:
            raise ValueError("scored ticket mains must be sorted")
        if self.main_match_count != len(self.matched_mains):
            raise ValueError("main_match_count does not match matched_mains")
        if not set(self.matched_mains).issubset(self.mains):
            raise ValueError("matched_mains must be contained in ticket mains")
        if not 1 <= self.mega <= 27:
            raise ValueError("mega outside 1-27")
        return self


class ScoreStatistics(FrozenModel):
    histogram: dict[int, int]
    mega_hit_count: int = Field(ge=0)
    mega_hit_rate: float = Field(ge=0, le=1)
    mean_main_matches: float = Field(ge=0, le=5)
    population_stddev: float = Field(ge=0)
    sample_stddev: float = Field(ge=0)
    empirical_p_ge_2: float = Field(ge=0, le=1)
    empirical_p_ge_3: float = Field(ge=0, le=1)
    empirical_p_ge_4: float = Field(ge=0, le=1)
    category_counts: dict[str, int]


class BundleScore(FrozenModel):
    score_id: str = Field(min_length=8, pattern=r"^[A-Za-z0-9._-]+$")
    scored_timestamp_utc: datetime
    bundle_id: str
    intended_draw_date: date
    draw: VerifiedDraw
    lines: tuple[ScoredLine, ...]
    # These provenance fields were added after the first immutable score schema.
    # ``None``/``unknown`` therefore remain valid when loading legacy artifacts;
    # new scoring always persists concrete values from the locked bundle.
    bundle_size: int | None = Field(default=None, ge=1)
    model_version: str = Field(default="unknown", min_length=1)
    overall: ScoreStatistics
    tiers: dict[Strategy, ScoreStatistics]
    best_line_keys: tuple[str, ...]
    predicted_metrics: SimulationSummary
    realized_metrics: dict[str, float | bool]
    calibration_error: dict[str, float]

    _normalize_scored = field_validator("scored_timestamp_utc")(_aware_utc)

    @model_validator(mode="after")
    def identity_matches(self) -> BundleScore:
        if self.draw.draw_date != self.intended_draw_date:
            raise ValueError("score draw date does not match bundle intended draw date")
        if self.bundle_size is not None and self.bundle_size != len(self.lines):
            raise ValueError("score bundle_size does not match scored line count")
        return self
