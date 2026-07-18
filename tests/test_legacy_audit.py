from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from slp_model.legacy_audit import (
    EMBEDDED_TWO_SOURCE_VERIFICATIONS,
    audit_legacy_tree,
    render_human_report,
    verify_legacy_inventory,
    write_audit_outputs,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = REPOSITORY_ROOT / "data" / "legacy" / "handoff-20260717"


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.csv"))
    }


def _codes(manifest: dict) -> set[str]:
    return {finding["code"] for finding in manifest["findings"]}


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def test_handoff_audit_is_deterministic_and_read_only() -> None:
    before = _hashes(LEGACY_ROOT)
    first = audit_legacy_tree(
        LEGACY_ROOT,
        repository_root=REPOSITORY_ROOT,
        verified_results=EMBEDDED_TWO_SOURCE_VERIFICATIONS,
    )
    second = audit_legacy_tree(
        LEGACY_ROOT,
        repository_root=REPOSITORY_ROOT,
        verified_results=EMBEDDED_TWO_SOURCE_VERIFICATIONS,
    )

    assert first == second
    assert _hashes(LEGACY_ROOT) == before
    assert first["source_integrity"]["file_count"] == 16
    assert first["source_integrity"]["originals_preserved_byte_for_byte"] is True
    assert all(len(item["sha256"]) == 64 for item in first["files"])
    assert first["summary"]["verified_winning_result_claims"] == 4
    assert first["summary"]["winning_result_mismatches"] == 0
    assert first["summary"]["internal_scoring_inconsistency_findings"] == 0


def test_handoff_identifies_material_legacy_failures() -> None:
    manifest = audit_legacy_tree(
        LEGACY_ROOT,
        repository_root=REPOSITORY_ROOT,
        verified_results=EMBEDDED_TWO_SOURCE_VERIFICATIONS,
    )
    codes = _codes(manifest)

    assert "empty_history_artifact" in codes
    assert "missing_prediction_reproducibility_metadata" in codes
    assert "pair_cap_violation" in codes
    assert "triple_cap_violation" in codes
    assert "generated_after_intended_draw" in codes
    assert "partial_scoring_bundle_association" in codes
    assert "orphan_scoring_artifact" in codes
    assert "untracked_corrected_variant" in codes
    assert "likely_recenter_collapse" in codes

    associations = {Path(item["scoring_path"]).name: item for item in manifest["associations"]}
    partial = associations["slp_scoring_20250924.csv"]
    assert partial["status"] == "partial_content_match"
    assert partial["matching_rows"] == 23
    assert partial["unmatched_scoring_rows"] == list(range(14, 21))
    assert associations["slp_scoring_20251008.csv"]["status"] == "no_match"
    assert associations["slp_scoring_20260128.csv"]["status"] == "exact_content_match"
    assert associations["slp_scoring_20260204.csv"]["status"] == "exact_content_match"

    variant = manifest["corrected_variants"][0]
    assert variant["shared_full_tickets_ignoring_line_identity"] == 28
    assert variant["shared_tickets_with_same_strategy_and_line_id"] == 6
    assert len(variant["removed_full_tickets"]) == 2
    assert len(variant["added_full_tickets"]) == 2


def test_detects_ticket_rules_duplicates_constraints_and_collapse(
    tmp_path: Path,
) -> None:
    prediction = tmp_path / "legacy" / "predictions" / "slp_predictions_20260101.csv"
    headers = [
        "generated_timestamp_utc",
        "bundle_id",
        "intended_draw_date",
        "game_rules_version",
        "strategy",
        "line_id",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "mega",
    ]
    rows = [
        [
            "2026-01-03T08:00:00Z",
            "bundle-a",
            "2026-01-01",
            "test",
            "balanced",
            index,
            1,
            2,
            3,
            4,
            5 if index < 10 else 48,
            1,
        ]
        for index in range(1, 11)
    ]
    _write_csv(prediction, headers, rows)

    manifest = audit_legacy_tree(tmp_path / "legacy", repository_root=tmp_path)
    codes = _codes(manifest)

    assert "invalid_number_range" in codes
    assert "duplicate_main_set" in codes
    assert "duplicate_full_ticket" in codes
    assert "main_overlap_cap_violation" in codes
    assert "pair_cap_violation" in codes
    assert "triple_cap_violation" in codes
    assert "likely_recenter_collapse" in codes
    assert "generated_after_intended_draw" in codes


def test_scoring_is_unverified_without_two_sources_and_recomputed_with_them(
    tmp_path: Path,
) -> None:
    scoring = tmp_path / "legacy" / "scoring" / "slp_scoring_20260128.csv"
    headers = [
        "draw_date",
        "win_n1",
        "win_n2",
        "win_n3",
        "win_n4",
        "win_n5",
        "win_mega",
        "bundle_id",
        "strategy",
        "line_id",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "mega",
        "main_matches",
        "mega_hit",
        "matched_mains",
        "category",
    ]
    _write_csv(
        scoring,
        headers,
        [
            [
                "2026-01-28",
                3,
                6,
                14,
                26,
                38,
                2,
                "bundle-a",
                "balanced",
                1,
                3,
                6,
                20,
                21,
                22,
                2,
                1,
                "False",
                "3",
                "1",
            ]
        ],
    )

    unverified = audit_legacy_tree(tmp_path / "legacy", repository_root=tmp_path)
    assert "unverified_winning_result_claim" in _codes(unverified)
    assert unverified["summary"]["verified_winning_result_claims"] == 0

    one_source_only = {
        "2026-01-28": {
            "mains": [3, 6, 14, 26, 38],
            "mega": 2,
            "sources": [
                {
                    "role": "official",
                    "name": "California Lottery",
                    "url": "https://www.calottery.com/result",
                    "fetch_timestamp_utc": "2026-07-17T00:00:00Z",
                }
            ],
        }
    }
    still_unverified = audit_legacy_tree(
        tmp_path / "legacy",
        repository_root=tmp_path,
        verified_results=one_source_only,
    )
    assert "unverified_winning_result_claim" in _codes(still_unverified)

    verified = audit_legacy_tree(
        tmp_path / "legacy",
        repository_root=tmp_path,
        verified_results=EMBEDDED_TWO_SOURCE_VERIFICATIONS,
    )
    codes = _codes(verified)
    assert "unverified_winning_result_claim" not in codes
    assert "scoring_main_match_count_inconsistency" in codes
    assert "scoring_mega_hit_inconsistency" in codes
    assert "scoring_matched_mains_inconsistency" in codes
    assert "scoring_category_inconsistency" in codes
    assert verified["summary"]["verified_winning_result_claims"] == 1


def test_writes_outputs_without_mutating_inputs(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    history = legacy / "history" / "history_schema.csv"
    _write_csv(history, ["draw_date", "n1", "n2", "n3", "n4", "n5", "mega"], [])
    before = _hashes(legacy)
    manifest_path = tmp_path / "reconciled" / "manifest.json"
    report_path = tmp_path / "docs" / "report.md"

    manifest = write_audit_outputs(
        legacy,
        manifest_path,
        report_path,
        repository_root=tmp_path,
    )

    assert _hashes(legacy) == before
    assert manifest_path.is_file()
    assert report_path.read_text(encoding="utf-8") == render_human_report(manifest)
    with pytest.raises(ValueError, match="outside the immutable legacy tree"):
        write_audit_outputs(
            legacy,
            legacy / "manifest.json",
            report_path,
            repository_root=tmp_path,
        )


def test_legacy_inventory_rehash_detects_tampering_and_additions(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    history = legacy / "history" / "history_schema.csv"
    _write_csv(history, ["draw_date", "n1", "n2", "n3", "n4", "n5", "mega"], [])
    manifest_path = tmp_path / "reconciled" / "manifest.json"
    write_audit_outputs(
        legacy,
        manifest_path,
        tmp_path / "docs" / "report.md",
        repository_root=tmp_path,
    )

    verified = verify_legacy_inventory(manifest_path, repository_root=tmp_path)
    assert verified["status"] == "verified"
    assert verified["file_count"] == 1

    history.write_bytes(history.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_legacy_inventory(manifest_path, repository_root=tmp_path)

    history.write_bytes(history.read_bytes()[: -len(b"tampered")])
    _write_csv(
        legacy / "history" / "untracked.csv",
        ["draw_date", "n1", "n2", "n3", "n4", "n5", "mega"],
        [],
    )
    with pytest.raises(ValueError, match="unmanifested"):
        verify_legacy_inventory(manifest_path, repository_root=tmp_path)
