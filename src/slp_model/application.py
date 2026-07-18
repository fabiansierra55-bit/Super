"""Strict LOCK -> SCORE -> RECALIBRATE -> GENERATE application workflow."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .backtesting import run_backtest, write_backtest_report
from .calibration import CalibrationStore, calibrate
from .config import AppConfig, resolve_path
from .dates import next_draw_date
from .exceptions import CyclePreconditionError, IntegrityError
from .fair_odds import exact_uniform_metrics
from .generation import build_locked_bundle, deterministic_seed
from .models import BundleScore, LockedBundle, VerifiedDraw
from .reporting import write_performance_report
from .scoring import score_locked_bundle
from .source_runtime import SourceManager
from .storage import (
    AppendOnlyLog,
    BundleStore,
    HistoryStore,
    ScoreStore,
    audit_all_stores,
    verify_audit_mirrors,
)


@dataclass(frozen=True)
class CycleResult:
    verified_draw_date: date
    scored_bundle_id: str | None
    score_id: str | None
    history_updated: bool
    next_bundle_id: str
    next_draw_date: date
    idempotent_replay: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "verified_draw_date": self.verified_draw_date.isoformat(),
            "scored_bundle_id": self.scored_bundle_id,
            "score_id": self.score_id,
            "history_updated": self.history_updated,
            "next_bundle_id": self.next_bundle_id,
            "next_draw_date": self.next_draw_date.isoformat(),
            "idempotent_replay": self.idempotent_replay,
        }


class Application:
    def __init__(
        self,
        *,
        project_root: Path,
        config: AppConfig,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        audit_dir = resolve_path(self.project_root, config.paths.audit_dir)
        self.audit_log = AppendOnlyLog(audit_dir / "events.jsonl")
        self.history_store = HistoryStore(
            resolve_path(self.project_root, config.paths.history_dir), self.audit_log
        )
        self.bundle_store = BundleStore(
            resolve_path(self.project_root, config.paths.predictions_dir), self.audit_log
        )
        self.score_store = ScoreStore(
            resolve_path(self.project_root, config.paths.scoring_dir), self.audit_log
        )
        self.calibration_store = CalibrationStore(
            resolve_path(self.project_root, config.paths.calibration_dir), self.audit_log
        )
        self._source_manager: SourceManager | None = None

    @classmethod
    def create(
        cls,
        *,
        project_root: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> Application:
        root = Path(project_root or Path.cwd()).resolve()
        config_candidate: Path | None = None
        if config_path is not None:
            config_candidate = Path(config_path)
            if not config_candidate.is_absolute():
                config_candidate = root / config_candidate
        config = AppConfig.load(config_candidate)
        return cls(project_root=root, config=config)

    @property
    def sources(self) -> SourceManager:
        if self._source_manager is None:
            self._source_manager = SourceManager(
                config=self.config,
                cache_dir=resolve_path(self.project_root, self.config.paths.cache_dir),
                audit_log=self.audit_log,
            )
        return self._source_manager

    def status(self) -> dict[str, Any]:
        history = self.history_store.load_latest()
        scores = self.score_store.list_scores()
        calibration = self.calibration_store.latest()
        bundle_directories = (
            sorted(path for path in self.bundle_store.locked_root.glob("*/*") if path.is_dir())
            if self.bundle_store.locked_root.exists()
            else []
        )
        loaded_bundles = [
            self.bundle_store.find(directory.name) for directory in bundle_directories
        ]
        superseded_bundle_ids = {
            bundle.metadata.supersedes_bundle_id
            for bundle in loaded_bundles
            if bundle.metadata.supersedes_bundle_id is not None
        }
        bundle_summaries: list[dict[str, Any]] = []
        for bundle in loaded_bundles:
            score = self.score_store.for_bundle(bundle.metadata.bundle_id)
            bundle_summaries.append(
                {
                    "bundle_id": bundle.metadata.bundle_id,
                    "intended_draw_date": bundle.metadata.intended_draw_date.isoformat(),
                    "bundle_size": bundle.metadata.bundle_size,
                    "lock_version": bundle.metadata.lock_version,
                    "active": bundle.metadata.bundle_id not in superseded_bundle_ids,
                    "supersedes_bundle_id": bundle.metadata.supersedes_bundle_id,
                    "scored": score is not None,
                }
            )
        if history:
            draws, history_sha256 = history
            history_summary: dict[str, Any] | None = {
                "draw_count": len(draws),
                "cutoff_date": draws[-1].draw_date.isoformat(),
                "snapshot_sha256": history_sha256,
            }
            expected_next = next_draw_date(draws[-1].draw_date)
            next_locked = any(
                item["intended_draw_date"] == expected_next.isoformat() and item["active"]
                for item in bundle_summaries
            )
            next_action = "wait_for_result" if next_locked else "generate_next_bundle"
        else:
            history_summary = None
            next_action = "rebuild_history"
        return {
            "project_root": str(self.project_root),
            "game_rules_version": self.config.game.rules_version,
            "model_version": self.config.model_version,
            "production_bundle": {
                "bundle_size": self.config.bundle.size,
                "tier_counts": {
                    "aggressive": self.config.bundle.aggressive_count,
                    "balanced": self.config.bundle.balanced_count,
                    "conservative": self.config.bundle.conservative_count,
                },
            },
            "history": history_summary,
            "calibration": (
                {
                    "calibration_id": calibration.calibration_id,
                    "kind": calibration.kind,
                    "history_cutoff_date": calibration.history_cutoff_date.isoformat(),
                }
                if calibration
                else None
            ),
            "locked_bundles": bundle_summaries,
            "scoring_artifact_count": len(scores),
            "audit_event_count": len(self.audit_log.read()),
            "next_action": next_action,
            "disclaimer": "Lottery outcomes are random; modeled bundles are not guarantees.",
        }

    def resolve_bundle(self, bundle_id: str | None = None) -> LockedBundle:
        if bundle_id is not None:
            return self.bundle_store.find(bundle_id)
        bundles = self.bundle_store.list_bundles()
        if not bundles:
            raise CyclePreconditionError("no locked prediction bundle exists")
        latest_date = max(bundle.metadata.intended_draw_date for bundle in bundles)
        return self.bundle_store.active_for_draw(latest_date)

    def bundle_view(self, bundle_id: str | None = None) -> dict[str, Any]:
        bundle = self.resolve_bundle(bundle_id)
        return {
            "bundle_id": bundle.metadata.bundle_id,
            "intended_draw_date": bundle.metadata.intended_draw_date.isoformat(),
            "model_version": bundle.metadata.model_version,
            "bundle_size": bundle.metadata.bundle_size,
            "tier_counts": {
                tier: sum(line.strategy == tier for line in bundle.lines)
                for tier in ("aggressive", "balanced", "conservative")
            },
            "lock_version": bundle.metadata.lock_version,
            "lines": [line.model_dump(mode="json") for line in bundle.lines],
        }

    def bundle_odds(self, bundle_id: str | None = None) -> dict[str, Any]:
        bundle = self.resolve_bundle(bundle_id)
        exact = exact_uniform_metrics(bundle.lines)
        evidence = bundle.metadata.optimizer.fair_coverage_challenger
        return {
            "bundle_id": bundle.metadata.bundle_id,
            "intended_draw_date": bundle.metadata.intended_draw_date.isoformat(),
            "bundle_size": bundle.metadata.bundle_size,
            "fair_uniform_exact": exact.model_dump(mode="json"),
            "model_conditional_simulation": bundle.metadata.simulation.model_dump(
                mode="json", exclude={"fair_uniform_exact"}
            ),
            "selection_evidence": (
                {
                    "evidence_version": evidence.evidence_version,
                    "selection_policy": evidence.selection_policy,
                    "model_skill_status": evidence.model_skill_status,
                    "selected": evidence.selected,
                    "selection_reason": evidence.selection_reason,
                    "global_optimum_certified": evidence.global_optimum_certified,
                    "certificate_bundle_size": evidence.certificate_bundle_size,
                    "recorded_gate_relative_primary_improvement": (
                        evidence.relative_primary_improvement
                    ),
                    "relative_exact_p_ge_3_improvement_over_model_candidate": (
                        evidence.challenger.p_any_ge_3_mains
                        / evidence.model_optimized_candidate.p_any_ge_3_mains
                        - 1.0
                    ),
                    "relative_exact_p_ge_3_change_vs_incumbent": (
                        evidence.challenger.p_any_ge_3_mains / evidence.incumbent.p_any_ge_3_mains
                        - 1.0
                        if evidence.incumbent is not None
                        else None
                    ),
                    "relative_model_conditional_p_ge_3_change": (
                        evidence.relative_challenger_model_p_ge_3_change
                    ),
                    "model_candidate_p_ge_3": (
                        evidence.model_optimized_simulation.p_any_ge_3_mains
                        if evidence.model_optimized_simulation is not None
                        else None
                    ),
                    "challenger_model_p_ge_3": (
                        evidence.challenger_model_simulation.p_any_ge_3_mains
                        if evidence.challenger_model_simulation is not None
                        else None
                    ),
                }
                if evidence is not None
                else None
            ),
            "interpretation": (
                "Fair-uniform values are exact combinatorial coverage. Model-conditional "
                "values assume the fitted historical distribution and are not objective odds."
            ),
        }

    def verify_latest(self, *, as_of_utc: datetime | None = None) -> VerifiedDraw:
        return self.sources.verify_latest(as_of_utc=as_of_utc)

    def rebuild_history(
        self,
        *,
        minimum_draws: int = 100,
        as_of_utc: datetime | None = None,
    ) -> Path:
        draws = self.sources.rebuild_history(minimum_draws=minimum_draws, as_of_utc=as_of_utc)
        return self.history_store.merge_verified(
            draws,
            reason="two_source_rebuild",
            created_timestamp_utc=as_of_utc,
        )

    @staticmethod
    def _same_result(left: VerifiedDraw, right: VerifiedDraw) -> bool:
        return (
            left.draw_date == right.draw_date
            and left.mains == right.mains
            and left.mega == right.mega
            and (left.draw_id is None or right.draw_id is None or left.draw_id == right.draw_id)
        )

    def _score_verified(self, draw: VerifiedDraw) -> tuple[BundleScore, bool]:
        bundle = self.bundle_store.active_for_draw(draw.draw_date)
        existing = self.score_store.for_bundle(bundle.metadata.bundle_id)
        if existing is not None:
            if not self._same_result(existing.draw, draw):
                raise IntegrityError("rerun source result differs from locked scoring artifact")
            return existing, True
        score = score_locked_bundle(
            bundle,
            draw,
            previous_scores=self.score_store.list_scores(),
        )
        self.score_store.append(score)
        return score, False

    def score(
        self,
        *,
        draw_date: date | None = None,
        as_of_utc: datetime | None = None,
        update_history: bool = True,
    ) -> BundleScore:
        draw = (
            self.sources.verify_date(draw_date, as_of_utc=as_of_utc)
            if draw_date is not None
            else self.sources.verify_latest(as_of_utc=as_of_utc)
        )
        score, _ = self._score_verified(draw)
        if update_history:
            self.history_store.merge_verified(
                [draw],
                reason="verified_result_scored",
                created_timestamp_utc=as_of_utc,
            )
        return score

    def _generate_from_current_history(
        self,
        *,
        random_seed: int | None = None,
        generated_timestamp_utc: datetime | None = None,
        force_reselection: bool = False,
        supersede_bundle_id: str | None = None,
        correction_reason: str | None = None,
    ) -> tuple[LockedBundle, bool]:
        current = self.history_store.load_latest()
        if current is None:
            raise CyclePreconditionError("verified history must be rebuilt before generation")
        draws, history_sha256 = current
        target = next_draw_date(draws[-1].draw_date)
        existing = self.bundle_store.list_for_draw(target)
        lock_version = 1
        normalized_reason: str | None = None
        incumbent_bundle: LockedBundle | None = None
        if existing:
            if supersede_bundle_id is None:
                return self.bundle_store.active_for_draw(target), True
            normalized_reason = (correction_reason or "").strip()
            if len(normalized_reason) < 8:
                raise CyclePreconditionError(
                    "a superseding correction requires a specific correction reason"
                )
            direct_children = [
                bundle
                for bundle in existing
                if bundle.metadata.supersedes_bundle_id == supersede_bundle_id
            ]
            if direct_children:
                if len(direct_children) != 1:
                    raise IntegrityError("a bundle has multiple direct correction children")
                child = direct_children[0]
                if child.metadata.correction_reason != normalized_reason:
                    raise CyclePreconditionError(
                        "the existing correction uses a different correction reason"
                    )
                return child, True
            active = self.bundle_store.active_for_draw(target)
            incumbent_bundle = active
            if active.metadata.bundle_id != supersede_bundle_id:
                raise CyclePreconditionError("correction parent must be the current active bundle")
            if self.score_store.for_bundle(active.metadata.bundle_id) is not None:
                raise CyclePreconditionError("a scored bundle cannot be superseded")
            lock_version = active.metadata.lock_version + 1
        elif supersede_bundle_id is not None or correction_reason is not None:
            raise CyclePreconditionError("there is no locked bundle to supersede")
        effective_seed = (
            random_seed
            if random_seed is not None
            else deterministic_seed(
                history_snapshot_sha256=history_sha256,
                intended_draw_date=target,
                model_version=self.config.model_version,
            )
        )
        calibration = calibrate(
            draws,
            history_snapshot_sha256=history_sha256,
            scores=self.score_store.list_scores(),
            config=self.config,
            store=self.calibration_store,
            random_seed=effective_seed,
            timestamp_utc=generated_timestamp_utc,
            force_full=force_reselection,
        )
        bundle = build_locked_bundle(
            draws,
            history_snapshot_sha256=history_sha256,
            calibration=calibration,
            config=self.config,
            random_seed=effective_seed,
            generated_timestamp_utc=generated_timestamp_utc,
            lock_version=lock_version,
            supersedes_bundle_id=supersede_bundle_id,
            correction_reason=normalized_reason,
            incumbent_bundle=incumbent_bundle,
        )
        self.bundle_store.lock(
            bundle,
            previous_draw_mains=draws[-1].mains,
            max_overlap=int(bundle.metadata.optimizer.constraints["max_main_overlap"]),
            min_hamming=self.config.bundle.min_hamming_distance,
            pair_cap=int(bundle.metadata.optimizer.constraints["pair_repeat_cap"]),
            triple_cap=self.config.bundle.triple_repeat_cap,
            mega_hard_cap=int(bundle.metadata.optimizer.constraints["mega_hard_cap"]),
            aggressive_previous_overlap_cap=(
                self.config.bundle.aggressive_previous_draw_overlap_cap
            ),
            official_post_time_pacific=(self.config.game.official_post_time_pacific),
        )
        return bundle, False

    def generate(
        self,
        *,
        draw_date: date | None = None,
        random_seed: int | None = None,
        generated_timestamp_utc: datetime | None = None,
        verify_current_sources: bool = True,
        force_reselection: bool = False,
        supersede_bundle_id: str | None = None,
        correction_reason: str | None = None,
    ) -> LockedBundle:
        current = self.history_store.load_latest()
        if current is None:
            raise CyclePreconditionError("verified history must be rebuilt before generation")
        draws, _ = current
        expected = next_draw_date(draws[-1].draw_date)
        if draw_date is not None and draw_date != expected:
            raise CyclePreconditionError(
                f"next intended draw is {expected}; refusing target {draw_date}"
            )
        if (supersede_bundle_id is None) != (correction_reason is None):
            raise CyclePreconditionError(
                "--supersede-bundle-id and --correction-reason must be provided together"
            )
        if verify_current_sources:
            latest = self.sources.verify_latest(as_of_utc=generated_timestamp_utc)
            if not self._same_result(latest, draws[-1]):
                raise CyclePreconditionError(
                    "verified history is not current; score/update the latest draw first"
                )
        bundle, _ = self._generate_from_current_history(
            random_seed=random_seed,
            generated_timestamp_utc=generated_timestamp_utc,
            force_reselection=force_reselection,
            supersede_bundle_id=supersede_bundle_id,
            correction_reason=correction_reason,
        )
        return bundle

    def cycle(
        self,
        *,
        as_of_utc: datetime | None = None,
        random_seed: int | None = None,
        publish: bool = False,
    ) -> CycleResult:
        verified = self.sources.verify_latest(as_of_utc=as_of_utc)
        current = self.history_store.load_latest()
        if current is None:
            raise CyclePreconditionError(
                "no verified history exists; run rebuild-history, then generate"
            )
        draws, _ = current
        cutoff = draws[-1]
        scored_bundle_id: str | None = None
        score_id: str | None = None
        history_updated = False
        replay = False

        if verified.draw_date < cutoff.draw_date:
            raise CyclePreconditionError("official latest result is older than locked history")
        if verified.draw_date == cutoff.draw_date:
            if not self._same_result(verified, cutoff):
                raise IntegrityError("official latest result conflicts with verified history")
            replay = True
        else:
            expected = next_draw_date(cutoff.draw_date)
            if verified.draw_date != expected:
                raise CyclePreconditionError(
                    "latest result skips one or more cycle dates; refusing to create an "
                    "after-the-draw prediction"
                )
            score, score_replay = self._score_verified(verified)
            scored_bundle_id = score.bundle_id
            score_id = score.score_id
            replay = score_replay
            self.history_store.merge_verified(
                [verified],
                reason="cycle_scored_verified_result",
                created_timestamp_utc=as_of_utc,
            )
            history_updated = True

        next_bundle, bundle_replay = self._generate_from_current_history(
            random_seed=random_seed,
            generated_timestamp_utc=as_of_utc,
        )
        replay = replay and bundle_replay
        result = CycleResult(
            verified_draw_date=verified.draw_date,
            scored_bundle_id=scored_bundle_id,
            score_id=score_id,
            history_updated=history_updated,
            next_bundle_id=next_bundle.metadata.bundle_id,
            next_draw_date=next_bundle.metadata.intended_draw_date,
            idempotent_replay=replay,
        )
        if publish:
            self.publish_generated_artifacts(result.next_draw_date)
        return result

    def audit(self) -> dict[str, int]:
        summary = audit_all_stores(
            audit_log=self.audit_log,
            history=self.history_store,
            bundles=self.bundle_store,
            scores=self.score_store,
        )
        summary["calibration_index_events"] = self.calibration_store.index.verify()
        summary["calibration_artifacts"] = self.calibration_store.audit_integrity()
        summary["mirrored_calibration_events"] = verify_audit_mirrors(
            self.audit_log, self.calibration_store.index
        )
        return summary

    def attest_existing_artifacts(
        self,
        *,
        timestamp_utc: datetime | None = None,
    ) -> dict[str, int]:
        """Append strong bindings for pre-hardening immutable artifacts.

        This migration never modifies a history, bundle, or score artifact. It
        only adds hash-chained attestations and is safe to rerun.
        """

        timestamp = (timestamp_utc or datetime.now(UTC)).astimezone(UTC)
        return {
            "history_attestations_added": self.history_store.attest_existing(
                timestamp_utc=timestamp
            ),
            "bundle_attestations_added": self.bundle_store.attest_existing(timestamp_utc=timestamp),
            "score_attestations_added": self.score_store.attest_existing(timestamp_utc=timestamp),
        }

    def backtest(
        self,
        *,
        evaluations: int = 3,
        diagnostic_candidate_pool_size: int = 50_000,
    ) -> tuple[Path, Path]:
        current = self.history_store.load_latest()
        if current is None:
            raise CyclePreconditionError("verified history is required for backtesting")
        draws, history_sha256 = current
        report = run_backtest(
            draws,
            history_snapshot_sha256=history_sha256,
            config=self.config,
            evaluations=evaluations,
            diagnostic_candidate_pool_size=diagnostic_candidate_pool_size,
        )
        return write_backtest_report(
            report, resolve_path(self.project_root, self.config.paths.reports_dir)
        )

    def report(self) -> tuple[Path, Path]:
        return write_performance_report(
            self.score_store.list_scores(),
            resolve_path(self.project_root, self.config.paths.reports_dir),
        )

    def publish_generated_artifacts(self, draw_date: date) -> str | None:
        def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *arguments],
                cwd=self.project_root,
                check=check,
                text=True,
                capture_output=True,
            )

        branch = git("branch", "--show-current").stdout.strip()
        expected_prefix = f"{self.config.git.artifact_branch_prefix}-"
        if not branch or not branch.startswith(expected_prefix):
            raise CyclePreconditionError(
                f"artifact publishing requires a {expected_prefix}* branch"
            )
        remote_url = git("remote", "get-url", "origin").stdout.strip().removesuffix(".git")
        repository = self.config.git.expected_repository
        accepted_remotes = {
            f"https://github.com/{repository}",
            f"git@github.com:{repository}",
            f"ssh://git@github.com/{repository}",
        }
        if remote_url not in accepted_remotes:
            raise CyclePreconditionError("origin does not match the configured GitHub repository")

        def allowed(path: str) -> bool:
            normalized = path.removeprefix("./")
            return (
                normalized == "data"
                or normalized.startswith("data/")
                or normalized == ("reports")
                or normalized.startswith("reports/")
            )

        committed_delta = git(
            "diff", "--name-only", f"origin/{self.config.git.protected_branch}...HEAD"
        ).stdout.splitlines()
        if unexpected := sorted(path for path in committed_delta if not allowed(path)):
            raise CyclePreconditionError(
                "artifact branch contains out-of-scope committed paths: " + ", ".join(unexpected)
            )
        worktree_paths = set(git("diff", "--name-only").stdout.splitlines())
        worktree_paths.update(git("diff", "--cached", "--name-only").stdout.splitlines())
        worktree_paths.update(git("ls-files", "--others", "--exclude-standard").stdout.splitlines())
        if unexpected := sorted(path for path in worktree_paths if not allowed(path)):
            raise CyclePreconditionError(
                "refusing to publish with out-of-scope worktree changes: " + ", ".join(unexpected)
            )
        self.audit()
        configured_paths = (
            self.config.paths.history_dir,
            self.config.paths.predictions_dir,
            self.config.paths.scoring_dir,
            self.config.paths.calibration_dir,
            self.config.paths.audit_dir,
            self.config.paths.reports_dir,
        )
        paths = [str(path) for path in configured_paths if (self.project_root / path).exists()]
        if not paths:
            return None
        git("add", "-A", "--", *paths)
        staged_paths = git("diff", "--cached", "--name-only").stdout.splitlines()
        if unexpected := sorted(path for path in staged_paths if not allowed(path)):
            raise CyclePreconditionError(
                "staged artifact set contains out-of-scope paths: " + ", ".join(unexpected)
            )
        changed = git("diff", "--cached", "--quiet", check=False).returncode
        if changed == 0:
            return None
        message = f"Lock SuperLotto artifacts for {draw_date.isoformat()}"
        git("commit", "-m", message)
        git("push", "-u", "origin", branch)
        return git("rev-parse", "HEAD").stdout.strip()


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))
