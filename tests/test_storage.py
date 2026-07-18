from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from slp_model.calibration import CalibrationArtifact, CalibrationStore
from slp_model.exceptions import ImmutableArtifactError, IntegrityError, SourceMismatchError
from slp_model.models import (
    BundleMetadata,
    LockedBundle,
    LockedLine,
    OptimizerSettings,
    SelectedHyperparameters,
    SimulationSummary,
    SourceEvidence,
    VerificationMetadata,
    VerifiedDraw,
)
from slp_model.scoring import score_locked_bundle
from slp_model.storage import (
    AppendOnlyLog,
    BundleStore,
    HistoryStore,
    ScoreStore,
    audit_all_stores,
    canonical_json_bytes,
    resolve_indexed_artifact,
    sha256_file,
)


def evidence(name: str, role: str, marker: str) -> SourceEvidence:
    return SourceEvidence(
        source_name=name,
        role=role,
        source_url=f"https://example.test/{name}",
        fetched_timestamp_utc=datetime(2026, 1, 1, 5, tzinfo=UTC),
        raw_sha256=marker * 64,
        parser_version="fixture-v1",
        http_status=200,
    )


def verified_draw(
    draw_date: date = date(2025, 12, 31),
    mains: tuple[int, int, int, int, int] = (1, 2, 3, 4, 5),
    mega: int = 6,
) -> VerifiedDraw:
    return VerifiedDraw(
        draw_date=draw_date,
        draw_id=f"draw-{draw_date.isoformat()}",
        mains=mains,
        mega=mega,
        verification=VerificationMetadata(
            status="verified",
            verified_timestamp_utc=datetime(2026, 1, 1, 5, tzinfo=UTC),
            official_post_timestamp_utc=datetime(2026, 1, 1, 4, tzinfo=UTC),
            sources=(
                evidence("calottery", "official", "a"),
                evidence("lotteryusa", "backup", "b"),
            ),
            comparison_sha256="c" * 64,
        ),
    )


def simulation() -> SimulationSummary:
    return SimulationSummary(
        simulation_count=50_000,
        candidate_pool_size=50_000,
        confidence_level=0.95,
        maximum_confidence_half_width=0.002,
        stable=True,
        p_any_ge_3_mains=0.10,
        p_any_ge_4_mains=0.01,
        p_any_3_plus_mega=0.005,
        p_any_4_plus=0.01,
        mean_best_main_matches=1.5,
    )


def bundle(*, bundle_id: str = "bundle-test-v1", version: int = 1, supersedes: str | None = None):
    draw = verified_draw()
    metadata = BundleMetadata(
        bundle_id=bundle_id,
        generated_timestamp_utc=datetime(2026, 1, 2, 4, tzinfo=UTC),
        intended_draw_date=date(2026, 1, 3),
        game_rules_version="slp-5of47-mega-1of27-v1",
        model_version="test-model",
        configuration_snapshot={"test": True},
        configuration_sha256="d" * 64,
        random_seed=123,
        source_verification_metadata=draw.verification,
        history_cutoff_date=draw.draw_date,
        history_snapshot_sha256="e" * 64,
        selected_hyperparameters=SelectedHyperparameters(
            main_window=60,
            mega_window=60,
            main_sigma=1.0,
            mega_sigma=1.0,
            main_half_life_draws=20,
            mega_half_life_draws=20,
            forward_objective=0.1,
            heldout_log_likelihood=-3.0,
            selection_timestamp_utc=datetime(2026, 1, 2, 3, tzinfo=UTC),
            training_draw_count=60,
        ),
        simulation=simulation(),
        optimizer=OptimizerSettings(
            algorithm="test-greedy",
            objective_weights={"p_ge_3": 1.0},
            constraints={"overlap": 3},
            anti_cannibalization_weight=0.1,
        ),
        bundle_size=3,
        lock_version=version,
        supersedes_bundle_id=supersedes,
        correction_reason="deterministic test correction" if supersedes else None,
    )
    return LockedBundle(
        metadata=metadata,
        lines=(
            LockedLine(strategy="aggressive", line_id=1, mains=(1, 6, 11, 16, 21), mega=1),
            LockedLine(strategy="balanced", line_id=1, mains=(2, 7, 12, 17, 22), mega=2),
            LockedLine(strategy="conservative", line_id=1, mains=(3, 8, 13, 18, 23), mega=3),
        ),
    )


def stores(root: Path):
    audit = AppendOnlyLog(root / "audit" / "events.jsonl")
    return (
        audit,
        HistoryStore(root / "history", audit),
        BundleStore(root / "predictions", audit),
        ScoreStore(root / "scoring", audit),
    )


def calibration_artifact() -> CalibrationArtifact:
    return CalibrationArtifact(
        calibration_id=f"cal-{'a' * 20}",
        created_timestamp_utc=datetime(2026, 1, 2, tzinfo=UTC),
        hyperparameter_selection_timestamp_utc=datetime(2026, 1, 2, tzinfo=UTC),
        kind="full_reselection",
        reasons=("fixture",),
        game_rules_version="slp-5of47-mega-1of27-v1",
        model_version="test-model",
        configuration_sha256="d" * 64,
        history_snapshot_sha256="e" * 64,
        history_cutoff_date=date(2025, 12, 31),
        history_draw_count=60,
        scored_draw_count=0,
        scored_draw_count_at_full_reselection=0,
        model_parameters={
            "mains": {"window": 60, "sigma": 1.0, "half_life": 20.0},
            "mega": {"window": 60, "sigma": 1.0, "half_life": 20.0},
        },
        fitted_model={},
        joint_forward_bundle_score=0.1,
        selected_heldout_log_likelihood=-3.0,
    )


@pytest.fixture
def before_draw_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "slp_model.storage.utc_now",
        lambda: datetime(2026, 1, 2, 5, tzinfo=UTC),
    )


def test_append_only_log_is_idempotent_and_detects_tampering(tmp_path: Path):
    log = AppendOnlyLog(tmp_path / "events.jsonl")
    first = log.append(event_id="one", event_type="test", payload={"value": 1})
    assert log.append(event_id="one", event_type="test", payload={"value": 1}) == first
    assert log.verify() == 1

    content = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(content.replace('"value":1', '"value":2'))
    with pytest.raises(IntegrityError, match="hash"):
        log.verify()


def test_history_is_versioned_idempotently_and_conflicts_halt(tmp_path: Path):
    _, history, _, _ = stores(tmp_path)
    draw = verified_draw()
    first = history.store_snapshot([draw], reason="fixture")
    second = history.store_snapshot([draw], reason="rerun")
    assert first == second
    assert len(history.index.read()) == 1
    assert history.index.read()[0]["payload"]["artifact_path"].startswith("versions/")

    conflicting = verified_draw(mains=(1, 2, 3, 4, 7))
    with pytest.raises(SourceMismatchError, match="conflicts"):
        history.merge_verified([conflicting], reason="bad update")


def test_legacy_absolute_index_path_rebases_inside_fresh_clone(tmp_path: Path) -> None:
    root = tmp_path / "data" / "history"
    expected = root / "versions" / "history-example.json"

    assert (
        resolve_indexed_artifact(
            root,
            "/Users/another/worktree/data/history/versions/history-example.json",
            expected_parent="versions",
        )
        == expected
    )
    with pytest.raises(IntegrityError, match="unsafe"):
        resolve_indexed_artifact(
            root,
            "../versions/history-example.json",
            expected_parent="versions",
        )


def test_locked_bundle_cannot_be_overwritten_and_correction_versions(
    tmp_path: Path, before_draw_clock: None
):
    _, _, bundle_store, _ = stores(tmp_path)
    original = bundle()
    path = bundle_store.lock(original, previous_draw_mains=(30, 31, 32, 33, 34))
    assert bundle_store.lock(original, previous_draw_mains=(30, 31, 32, 33, 34)) == path

    different = bundle(bundle_id="bundle-other-v1")
    with pytest.raises(ImmutableArtifactError, match="supersede"):
        bundle_store.lock(different, previous_draw_mains=(30, 31, 32, 33, 34))

    correction = bundle(bundle_id="bundle-test-v2", version=2, supersedes="bundle-test-v1")
    bundle_store.lock(correction, previous_draw_mains=(30, 31, 32, 33, 34))
    assert bundle_store.active_for_draw(date(2026, 1, 3)).metadata.bundle_id == "bundle-test-v2"
    assert len(bundle_store.list_for_draw(date(2026, 1, 3))) == 2


def test_scoring_artifact_is_idempotent_and_tamper_evident(tmp_path: Path, before_draw_clock: None):
    _, _, bundle_store, score_store = stores(tmp_path)
    locked = bundle()
    bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    result = verified_draw(draw_date=date(2026, 1, 3), mains=(1, 2, 3, 40, 41), mega=1)
    score = score_locked_bundle(
        locked, result, scored_timestamp_utc=datetime(2026, 1, 4, tzinfo=UTC)
    )
    first = score_store.append(score)
    rerun = score.model_copy(update={"scored_timestamp_utc": datetime(2026, 1, 5, tzinfo=UTC)})
    assert score_store.append(rerun) == first

    bundle_file = (
        tmp_path / "predictions" / "locked" / "2026-01-03" / "bundle-test-v1" / "bundle.json"
    )
    os.chmod(bundle_file, 0o644)
    raw = json.loads(bundle_file.read_text(encoding="utf-8"))
    raw["bundle"]["lines"][0]["mega"] = 27
    bundle_file.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(IntegrityError, match="checksum"):
        bundle_store.find("bundle-test-v1")


def test_wall_clock_prevents_a_new_after_draw_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, bundle_store, _ = stores(tmp_path)
    monkeypatch.setattr(
        "slp_model.storage.utc_now",
        lambda: datetime(2026, 1, 4, 5, tzinfo=UTC),
    )
    with pytest.raises(IntegrityError, match="lock was attempted"):
        bundle_store.lock(bundle(), previous_draw_mains=(30, 31, 32, 33, 34))
    assert not bundle_store.locked_root.exists()


def test_index_bound_manifest_detects_csv_and_manifest_rewrite(
    tmp_path: Path, before_draw_clock: None
) -> None:
    _, _, bundle_store, _ = stores(tmp_path)
    locked = bundle()
    directory = bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    csv_path = directory / "tickets.csv"
    manifest_path = directory / "manifest.json"
    os.chmod(csv_path, 0o644)
    os.chmod(manifest_path, 0o644)
    csv_path.write_bytes(csv_path.read_bytes() + b"forged,row\n")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["tickets.csv"] = sha256_file(csv_path)
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(IntegrityError, match="manifest is not bound"):
        bundle_store.find(locked.metadata.bundle_id)


def test_score_index_is_authoritative_and_binds_csv_manifest(
    tmp_path: Path, before_draw_clock: None
) -> None:
    _, _, bundle_store, score_store = stores(tmp_path)
    locked = bundle()
    bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    result = verified_draw(draw_date=date(2026, 1, 3), mains=(1, 2, 3, 40, 41), mega=1)
    score = score_locked_bundle(
        locked,
        result,
        scored_timestamp_utc=datetime(2026, 1, 4, tzinfo=UTC),
    )
    directory = score_store.append(score)
    csv_path = directory / "tickets.csv"
    manifest_path = directory / "manifest.json"
    os.chmod(csv_path, 0o644)
    os.chmod(manifest_path, 0o644)
    csv_path.write_bytes(csv_path.read_bytes() + b"forged,row\n")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["tickets.csv"] = sha256_file(csv_path)
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(IntegrityError, match="manifest is not bound"):
        score_store.list_scores()


def test_exact_score_crash_orphan_is_recovered_on_retry(
    tmp_path: Path,
    before_draw_clock: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, bundle_store, score_store = stores(tmp_path)
    locked = bundle()
    bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    result = verified_draw(draw_date=date(2026, 1, 3), mains=(1, 2, 3, 40, 41), mega=1)
    score = score_locked_bundle(
        locked,
        result,
        scored_timestamp_utc=datetime(2026, 1, 4, tzinfo=UTC),
    )
    original_append = score_store.index.append

    def fail_index_append(**_kwargs: object) -> dict[str, object]:
        raise OSError("simulated score index crash")

    monkeypatch.setattr(score_store.index, "append", fail_index_append)
    with pytest.raises(OSError, match="simulated score"):
        score_store.append(score)
    monkeypatch.setattr(score_store.index, "append", original_append)

    recovered = score_store.append(score)
    assert recovered.is_dir()
    assert score_store.for_bundle(locked.metadata.bundle_id) == score
    assert len(score_store.index.read()) == 1


def test_bundle_index_directory_bijection_rejects_orphan_and_mislocation(
    tmp_path: Path, before_draw_clock: None
) -> None:
    audit, history, bundle_store, score_store = stores(tmp_path)
    directory = bundle_store.lock(bundle(), previous_draw_mains=(30, 31, 32, 33, 34))
    misplaced = bundle_store.locked_root / "2026-01-10" / directory.name
    misplaced.parent.mkdir(parents=True)
    shutil.copytree(directory, misplaced)

    with pytest.raises(IntegrityError, match="orphan bundle"):
        audit_all_stores(
            audit_log=audit,
            history=history,
            bundles=bundle_store,
            scores=score_store,
        )


def test_audit_requires_authoritative_events_in_global_audit(
    tmp_path: Path, before_draw_clock: None
) -> None:
    audit, history, bundle_store, score_store = stores(tmp_path)
    bundle_store.lock(bundle(), previous_draw_mains=(30, 31, 32, 33, 34))
    audit.path.unlink()
    empty_audit = AppendOnlyLog(audit.path)

    with pytest.raises(IntegrityError, match="global audit is missing"):
        audit_all_stores(
            audit_log=empty_audit,
            history=history,
            bundles=bundle_store,
            scores=score_store,
        )


def test_portable_relative_indexes_survive_store_move(
    tmp_path: Path, before_draw_clock: None
) -> None:
    original_root = tmp_path / "first"
    _, history, bundle_store, _ = stores(original_root)
    history.store_snapshot([verified_draw()], reason="portable")
    locked = bundle()
    bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    assert not Path(bundle_store.index.read()[0]["payload"]["artifact_path"]).is_absolute()

    moved_root = tmp_path / "moved"
    original_root.rename(moved_root)
    _, moved_history, moved_bundles, _ = stores(moved_root)
    assert moved_history.load_latest() is not None
    assert moved_bundles.find(locked.metadata.bundle_id) == locked


def test_exact_bundle_crash_orphan_is_recovered_on_retry(
    tmp_path: Path,
    before_draw_clock: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, bundle_store, _ = stores(tmp_path)
    locked = bundle()
    original_append = bundle_store.index.append

    def fail_index_append(**_kwargs: object) -> dict[str, object]:
        raise OSError("simulated index crash")

    monkeypatch.setattr(bundle_store.index, "append", fail_index_append)
    with pytest.raises(OSError, match="simulated"):
        bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    expected = (
        bundle_store.locked_root
        / locked.metadata.intended_draw_date.isoformat()
        / locked.metadata.bundle_id
    )
    assert expected.is_dir()
    monkeypatch.setattr(bundle_store.index, "append", original_append)

    recovered = bundle_store.lock(locked, previous_draw_mains=(30, 31, 32, 33, 34))
    assert recovered.is_dir()
    assert bundle_store.find(locked.metadata.bundle_id) == locked
    assert len(bundle_store.index.read()) == 1


def test_history_full_file_binding_detects_metadata_tamper(tmp_path: Path) -> None:
    _, history, _, _ = stores(tmp_path)
    path = history.store_snapshot([verified_draw()], reason="original")
    os.chmod(path, 0o644)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["reason"] = "forged-but-same-draws"
    path.write_bytes(canonical_json_bytes(raw))

    with pytest.raises(IntegrityError, match="full-file checksum"):
        history.load_latest()


def test_weak_history_index_can_be_attested_without_mutating_artifact(
    tmp_path: Path,
) -> None:
    _, history, _, _ = stores(tmp_path)
    path = history.store_snapshot([verified_draw()], reason="legacy")
    original_bytes = path.read_bytes()
    event = history.index.read()[0]
    weak_payload = dict(event["payload"])
    weak_payload.pop("file_sha256")
    history.index.path.unlink()
    history.index = AppendOnlyLog(history.root / "index.jsonl")
    history.index.append(
        event_id=str(event["event_id"]),
        event_type="history_snapshot_locked",
        payload=weak_payload,
        timestamp_utc=datetime.fromisoformat(str(event["timestamp_utc"])),
    )

    with pytest.raises(IntegrityError, match="needs a full-file attestation"):
        history.load_latest()
    assert history.attest_existing(timestamp_utc=datetime(2026, 1, 2, tzinfo=UTC)) == 1
    assert path.read_bytes() == original_bytes
    assert history.load_latest() is not None
    assert history.attest_existing(timestamp_utc=datetime(2026, 1, 3, tzinfo=UTC)) == 0


def test_calibration_index_is_portable_and_orphans_fail_audit(tmp_path: Path) -> None:
    root = tmp_path / "calibration"
    audit = AppendOnlyLog(tmp_path / "audit" / "events.jsonl")
    store = CalibrationStore(root, audit)
    artifact = calibration_artifact()
    path = store.lock(artifact)
    assert store.latest() == artifact
    assert artifact.selection_random_seed == 0
    assert not Path(store.index.read()[0]["payload"]["artifact_path"]).is_absolute()

    orphan = store.locked_root / "cal-bbbbbbbbbbbbbbbbbbbb.json"
    orphan.write_bytes(path.read_bytes())
    with pytest.raises(IntegrityError, match="orphan calibration"):
        store.audit_integrity()
