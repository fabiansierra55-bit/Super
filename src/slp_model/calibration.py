"""Adaptive reselection cadence and immutable calibration artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import AppConfig
from .exceptions import CalibrationError, IntegrityError
from .modeling import (
    AdaptiveSelection,
    ComponentParameters,
    FittedModel,
    ForwardCandidateScore,
    ModelParameters,
    fit_model,
    select_hyperparameters,
)
from .models import BundleScore, SelectedHyperparameters, VerifiedDraw
from .storage import (
    AppendOnlyLog,
    canonical_json_bytes,
    indexed_artifact_reference,
    resolve_indexed_artifact,
    sha256_bytes,
    sha256_file,
    write_new_file,
)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class CalibrationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calibration_id: str = Field(pattern=r"^cal-[0-9a-f]{20}$")
    created_timestamp_utc: datetime
    hyperparameter_selection_timestamp_utc: datetime
    kind: Literal["full_reselection", "parameter_refit"]
    reasons: tuple[str, ...]
    parent_calibration_id: str | None = None
    game_rules_version: str
    model_version: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    history_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    history_cutoff_date: date
    history_draw_count: int = Field(ge=60)
    selection_random_seed: int = Field(default=0, ge=0)
    scored_draw_count: int = Field(ge=0)
    scored_draw_count_at_full_reselection: int = Field(ge=0)
    model_parameters: dict[str, dict[str, float | int]]
    fitted_model: dict[str, Any]
    joint_forward_bundle_score: float
    selected_heldout_log_likelihood: float
    forward_scores: tuple[dict[str, Any], ...] = ()
    fold_training_cutoffs: tuple[date, ...] = ()

    _normalize_created = field_validator("created_timestamp_utc")(_aware_utc)
    _normalize_selected = field_validator("hyperparameter_selection_timestamp_utc")(_aware_utc)

    def restore_model(self) -> FittedModel:
        mains_raw = self.model_parameters["mains"]
        mega_raw = self.model_parameters["mega"]
        mains = ComponentParameters(
            window=int(mains_raw["window"]),
            sigma=float(mains_raw["sigma"]),
            half_life=float(mains_raw["half_life"]),
        )
        mega = ComponentParameters(
            window=int(mega_raw["window"]),
            sigma=float(mega_raw["sigma"]),
            half_life=float(mega_raw["half_life"]),
        )
        raw = self.fitted_model
        return FittedModel(
            parameters=ModelParameters(mains=mains, mega=mega),
            mains_probabilities=tuple(raw["mains_probabilities"]),
            mega_probabilities=tuple(raw["mega_probabilities"]),
            recent_mains_probabilities=tuple(raw["recent_mains_probabilities"]),
            recent_mega_probabilities=tuple(raw["recent_mega_probabilities"]),
            stable_mains_probabilities=tuple(raw["stable_mains_probabilities"]),
            stable_mega_probabilities=tuple(raw["stable_mega_probabilities"]),
            positional_medians=tuple(raw["positional_medians"]),
            positional_dispersions=tuple(raw["positional_dispersions"]),
            history_start_date=date.fromisoformat(raw["history_start_date"]),
            history_cutoff_date=date.fromisoformat(raw["history_cutoff_date"]),
            history_draw_count=int(raw["history_draw_count"]),
        )

    def bundle_hyperparameters(self) -> SelectedHyperparameters:
        mains = self.model_parameters["mains"]
        mega = self.model_parameters["mega"]
        return SelectedHyperparameters(
            main_window=int(mains["window"]),
            mega_window=int(mega["window"]),
            main_sigma=float(mains["sigma"]),
            mega_sigma=float(mega["sigma"]),
            main_half_life_draws=float(mains["half_life"]),
            mega_half_life_draws=float(mega["half_life"]),
            forward_objective=self.joint_forward_bundle_score,
            heldout_log_likelihood=self.selected_heldout_log_likelihood,
            anchor_240_accepted=(int(mains["window"]) == 240 or int(mega["window"]) == 240),
            selection_timestamp_utc=self.hyperparameter_selection_timestamp_utc,
            training_draw_count=self.history_draw_count,
        )


def _component_dict(value: ComponentParameters) -> dict[str, float | int]:
    return {"window": value.window, "sigma": value.sigma, "half_life": value.half_life}


def _model_dict(model: FittedModel) -> dict[str, Any]:
    return {
        "mains_probabilities": list(model.mains_probabilities),
        "mega_probabilities": list(model.mega_probabilities),
        "recent_mains_probabilities": list(model.recent_mains_probabilities),
        "recent_mega_probabilities": list(model.recent_mega_probabilities),
        "stable_mains_probabilities": list(model.stable_mains_probabilities),
        "stable_mega_probabilities": list(model.stable_mega_probabilities),
        "positional_medians": list(model.positional_medians),
        "positional_dispersions": list(model.positional_dispersions),
        "history_start_date": model.history_start_date.isoformat(),
        "history_cutoff_date": model.history_cutoff_date.isoformat(),
        "history_draw_count": model.history_draw_count,
    }


def _score_dict(score: ForwardCandidateScore) -> dict[str, Any]:
    return {
        "component": score.component,
        "parameters": _component_dict(score.parameters),
        "forward_bundle_score": score.forward_bundle_score,
        "primary_hit_rate": score.primary_hit_rate,
        "secondary_hit_rate": score.secondary_hit_rate,
        "heldout_log_likelihood": score.heldout_log_likelihood,
        "stable": score.stable,
        "folds": score.folds,
    }


def _selected_log_likelihood(selection: AdaptiveSelection) -> float:
    parameters = selection.model.parameters
    matches = [
        score.heldout_log_likelihood
        for score in (*selection.mains_scores, *selection.mega_scores)
        if (
            (score.component == "mains" and score.parameters == parameters.mains)
            or (score.component == "mega" and score.parameters == parameters.mega)
        )
    ]
    return sum(matches) / len(matches) if matches else float("nan")


class CalibrationStore:
    def __init__(self, root: Path, audit_log: AppendOnlyLog) -> None:
        self.root = root
        self.locked_root = root / "locked"
        self.index = AppendOnlyLog(root / "index.jsonl")
        self.audit_log = audit_log

    def _load_path(self, path: Path, expected_sha256: str | None = None) -> CalibrationArtifact:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"cannot read calibration artifact {path}") from exc
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version",
            "calibration",
            "content_sha256",
        }:
            raise IntegrityError(f"calibration artifact has an invalid envelope: {path}")
        stored = raw.pop("content_sha256", None)
        calculated = sha256_bytes(canonical_json_bytes(raw))
        if (
            raw.get("schema_version") != 1
            or stored != calculated
            or (expected_sha256 and stored != expected_sha256)
        ):
            raise IntegrityError(f"calibration artifact failed checksum: {path}")
        return CalibrationArtifact.model_validate(raw["calibration"])

    @staticmethod
    def _same_reproducible_fit(
        existing: CalibrationArtifact, proposed: CalibrationArtifact
    ) -> bool:
        """Ignore lifecycle-only fields when a deterministic fit identity is replayed."""

        lifecycle = {
            "created_timestamp_utc",
            "hyperparameter_selection_timestamp_utc",
            "reasons",
            "parent_calibration_id",
        }
        return existing.model_dump(exclude=lifecycle) == proposed.model_dump(exclude=lifecycle)

    def latest(self) -> CalibrationArtifact | None:
        events = self.index.read()
        if not events:
            return None
        return self._load_event(events[-1])[0]

    def find(self, calibration_id: str) -> CalibrationArtifact:
        """Resolve one immutable calibration by its durable identity."""

        self.audit_integrity()
        matches = [
            artifact
            for _, artifact, _ in self._indexed_entries()
            if artifact.calibration_id == calibration_id
        ]
        if len(matches) != 1:
            raise CalibrationError(f"calibration_id {calibration_id!r} is missing or ambiguous")
        return matches[0]

    def _load_event(self, event: Mapping[str, Any]) -> tuple[CalibrationArtifact, Path]:
        if event.get("event_type") != "model_calibrated":
            raise IntegrityError("unexpected event type in calibration index")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise IntegrityError("calibration index event has no object payload")
        calibration_id = payload.get("calibration_id")
        if not isinstance(calibration_id, str):
            raise IntegrityError("calibration index event has no calibration ID")
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="locked",
        )
        expected_path = self.locked_root / f"{calibration_id}.json"
        if path != expected_path:
            raise IntegrityError(f"calibration is indexed at a noncanonical path: {path}")
        artifact = self._load_path(path, expected_sha256=str(payload.get("content_sha256")))
        if (
            artifact.calibration_id != calibration_id
            or artifact.kind != payload.get("kind")
            or artifact.history_cutoff_date.isoformat() != payload.get("history_cutoff_date")
        ):
            raise IntegrityError(f"calibration index metadata does not match artifact: {path}")
        file_sha256 = payload.get("file_sha256")
        if file_sha256 is not None and sha256_file(path) != file_sha256:
            raise IntegrityError(f"calibration full-file checksum failed: {path}")
        return artifact, path

    def _indexed_entries(
        self,
    ) -> list[tuple[Mapping[str, Any], CalibrationArtifact, Path]]:
        entries: list[tuple[Mapping[str, Any], CalibrationArtifact, Path]] = []
        identities: set[str] = set()
        paths: set[Path] = set()
        for event in self.index.read():
            artifact, path = self._load_event(event)
            if artifact.calibration_id in identities:
                raise IntegrityError(
                    f"calibration ID is indexed more than once: {artifact.calibration_id}"
                )
            if path in paths:
                raise IntegrityError(f"calibration path is indexed more than once: {path}")
            identities.add(artifact.calibration_id)
            paths.add(path)
            entries.append((event, artifact, path))
        return entries

    def lock(self, artifact: CalibrationArtifact) -> Path:
        body = {
            "schema_version": 1,
            "calibration": artifact.model_dump(mode="json"),
        }
        content_sha256 = sha256_bytes(canonical_json_bytes(body))
        payload = dict(body)
        payload["content_sha256"] = content_sha256
        encoded = canonical_json_bytes(payload)
        file_sha256 = sha256_bytes(encoded)
        path = self.locked_root / f"{artifact.calibration_id}.json"
        entries = self._indexed_entries()
        indexed = [
            (event, existing, existing_path)
            for event, existing, existing_path in entries
            if existing.calibration_id == artifact.calibration_id
        ]
        if indexed:
            if len(indexed) != 1 or not self._same_reproducible_fit(indexed[0][1], artifact):
                raise IntegrityError(f"calibration ID collision: {artifact.calibration_id}")
            self.audit_integrity()
            event, _, existing_path = indexed[0]
            event_payload = event["payload"]
            self.audit_log.append(
                event_id=str(event["event_id"]),
                event_type="model_calibrated",
                payload=event_payload,
                timestamp_utc=datetime.fromisoformat(str(event["timestamp_utc"])),
            )
            return existing_path
        physical = set(self.locked_root.iterdir()) if self.locked_root.exists() else set()
        indexed_paths = {item_path for _, _, item_path in entries}
        missing = indexed_paths - physical
        unrelated_orphans = (physical - indexed_paths) - {path}
        if missing:
            raise IntegrityError(f"indexed calibration artifact is missing: {sorted(missing)[0]}")
        if unrelated_orphans:
            raise IntegrityError(
                f"unrelated orphan calibration blocks locking: {sorted(unrelated_orphans)[0]}"
            )
        if path.exists():
            existing = self._load_path(path)
            if existing != artifact or sha256_file(path) != file_sha256:
                raise IntegrityError(f"calibration crash-orphan differs: {artifact.calibration_id}")
        else:
            write_new_file(path, encoded)
        event_payload = {
            "calibration_id": artifact.calibration_id,
            "kind": artifact.kind,
            "history_cutoff_date": artifact.history_cutoff_date.isoformat(),
            "artifact_path": indexed_artifact_reference(self.root, path),
            "content_sha256": content_sha256,
            "file_sha256": file_sha256,
        }
        event_id = f"calibration:{artifact.calibration_id}"
        self.index.append(
            event_id=event_id,
            event_type="model_calibrated",
            payload=event_payload,
            timestamp_utc=artifact.created_timestamp_utc,
        )
        self.audit_log.append(
            event_id=event_id,
            event_type="model_calibrated",
            payload=event_payload,
            timestamp_utc=artifact.created_timestamp_utc,
        )
        return path

    def audit_integrity(self) -> int:
        entries = self._indexed_entries()
        indexed_paths = {path for _, _, path in entries}
        if self.locked_root.exists():
            invalid = [
                path
                for path in self.locked_root.iterdir()
                if path.is_symlink() or not path.is_file() or path.suffix != ".json"
            ]
            if invalid:
                raise IntegrityError(f"unexpected calibration store entry: {invalid[0]}")
            physical_paths = set(self.locked_root.iterdir())
        else:
            physical_paths = set()
        missing = indexed_paths - physical_paths
        orphaned = physical_paths - indexed_paths
        if missing:
            raise IntegrityError(f"indexed calibration artifact is missing: {sorted(missing)[0]}")
        if orphaned:
            raise IntegrityError(
                f"orphan calibration artifact is not indexed: {sorted(orphaned)[0]}"
            )
        return len(entries)


def reselection_reasons(
    *,
    previous: CalibrationArtifact | None,
    scores: Sequence[BundleScore],
    config: AppConfig,
) -> tuple[str, ...]:
    if previous is None:
        return ("no_prior_calibration",)
    reasons: list[str] = []
    if previous.game_rules_version != config.game.rules_version:
        reasons.append("game_rules_changed")
    if previous.model_version != config.model_version:
        reasons.append("model_version_changed")
    if previous.configuration_sha256 != config.snapshot_sha256():
        reasons.append("configuration_changed")
    scored_since = len(scores) - previous.scored_draw_count_at_full_reselection
    if scored_since >= config.training.reselection_interval_scored_draws:
        reasons.append("ten_scored_draw_interval")
    if scored_since >= 5:
        calibration = scores[-1].calibration_error.get("rolling_5_calibration_p_ge_3", 0.0)
        if calibration >= config.training.drift_calibration_error_threshold:
            reasons.append("calibration_drift")
        underperformance_window = config.training.drift_underperformance_draws
        recent = scores[-underperformance_window:]
        if len(recent) == underperformance_window and not any(
            bool(score.realized_metrics["any_ge_3_mains"]) for score in recent
        ):
            reasons.append("persistent_underperformance")
    return tuple(dict.fromkeys(reasons))


def calibrate(
    draws: Sequence[VerifiedDraw],
    *,
    history_snapshot_sha256: str,
    scores: Sequence[BundleScore],
    config: AppConfig,
    store: CalibrationStore,
    random_seed: int,
    timestamp_utc: datetime | None = None,
    force_full: bool = False,
) -> CalibrationArtifact:
    if len(draws) < min(config.training.windows) + 2:
        raise ValueError(f"at least {min(config.training.windows) + 2} verified draws are required")
    created = (timestamp_utc or datetime.now(UTC)).astimezone(UTC)
    previous = store.latest()
    reasons = reselection_reasons(previous=previous, scores=scores, config=config)
    if force_full and "operator_forced" not in reasons:
        reasons = (*reasons, "operator_forced")
    full = force_full or previous is None or bool(reasons)

    selection: AdaptiveSelection | None = None
    if full:
        selection = select_hyperparameters(
            draws,
            cutoff_date=draws[-1].draw_date,
            windows=config.training.windows,
            main_sigmas=config.training.main_sigmas,
            mega_sigmas=config.training.mega_sigmas,
            half_lives=config.training.half_lives_draws,
            validation_draws=config.training.forward_folds,
            forward_bundle_size=config.training.forward_bundle_size,
            random_seed=random_seed,
            anchor_min_improvement=config.training.anchor_min_relative_improvement,
            likelihood_stability_margin=config.training.likelihood_stability_margin,
            objective_weights=(
                config.objective.p_ge_3_weight,
                config.objective.p_ge_4_weight,
                config.objective.three_plus_mega_weight,
                config.objective.four_plus_weight,
            ),
            objective_mode=config.objective.mode,
        )
        model = selection.model
        selected_timestamp = created
        full_score_count = len(scores)
        joint_score = selection.joint_forward_bundle_score
        heldout = _selected_log_likelihood(selection)
        forward_scores = tuple(
            _score_dict(score) for score in (*selection.mains_scores, *selection.mega_scores)
        )
        cutoffs = selection.fold_training_cutoffs
        kind: Literal["full_reselection", "parameter_refit"] = "full_reselection"
        if not reasons:
            reasons = ("scheduled_full_reselection",)
    else:
        assert previous is not None
        model = fit_model(draws, previous.restore_model().parameters)
        selected_timestamp = previous.hyperparameter_selection_timestamp_utc
        full_score_count = previous.scored_draw_count_at_full_reselection
        joint_score = previous.joint_forward_bundle_score
        heldout = previous.selected_heldout_log_likelihood
        forward_scores = ()
        cutoffs = ()
        kind = "parameter_refit"
        reasons = ("new_verified_history",)

    identity = {
        "kind": kind,
        "history_snapshot_sha256": history_snapshot_sha256,
        "configuration_sha256": config.snapshot_sha256(),
        "selection_random_seed": random_seed,
        "parameters": {
            "mains": _component_dict(model.parameters.mains),
            "mega": _component_dict(model.parameters.mega),
        },
    }
    calibration_id = f"cal-{sha256_bytes(canonical_json_bytes(identity))[:20]}"
    artifact = CalibrationArtifact(
        calibration_id=calibration_id,
        created_timestamp_utc=created,
        hyperparameter_selection_timestamp_utc=selected_timestamp,
        kind=kind,
        reasons=reasons,
        parent_calibration_id=previous.calibration_id if previous else None,
        game_rules_version=config.game.rules_version,
        model_version=config.model_version,
        configuration_sha256=config.snapshot_sha256(),
        history_snapshot_sha256=history_snapshot_sha256,
        history_cutoff_date=draws[-1].draw_date,
        history_draw_count=len(draws),
        selection_random_seed=random_seed,
        scored_draw_count=len(scores),
        scored_draw_count_at_full_reselection=full_score_count,
        model_parameters={
            "mains": _component_dict(model.parameters.mains),
            "mega": _component_dict(model.parameters.mega),
        },
        fitted_model=_model_dict(model),
        joint_forward_bundle_score=joint_score,
        selected_heldout_log_likelihood=heldout,
        forward_scores=forward_scores,
        fold_training_cutoffs=cutoffs,
    )
    path = store.lock(artifact)
    return store._load_path(path)
