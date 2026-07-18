"""Command-line interface for the strict SuperLotto Plus production workflow."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from .application import Application, print_json
from .exceptions import SLPError
from .legacy_audit import verify_legacy_inventory
from .sources import SourceError
from .verification import VerificationError as SourceVerificationError


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slp", description="Auditable SuperLotto Plus modeling workflow"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="show local workflow state without network access")

    show_bundle = subparsers.add_parser(
        "show-bundle", help="show the active or requested immutable ticket bundle"
    )
    show_bundle.add_argument("--bundle-id")

    odds = subparsers.add_parser(
        "odds", help="calculate exact fair-uniform coverage for a locked bundle"
    )
    odds.add_argument("--bundle-id")

    rebuild = subparsers.add_parser(
        "rebuild-history", help="fetch and lock a two-source-verified history snapshot"
    )
    rebuild.add_argument(
        "--minimum-draws",
        type=int,
        default=100,
        help=(
            "verified draws to lock (default: 100, matching the current bounded "
            "official web archive)"
        ),
    )

    subparsers.add_parser(
        "verify-latest", help="fetch official and backup sources and verify the latest draw"
    )

    generate = subparsers.add_parser(
        "generate", help="recalibrate, optimize, validate, and lock the next bundle"
    )
    generate.add_argument("--draw-date", type=_date)
    generate.add_argument("--seed", type=int)
    generate.add_argument("--force-reselection", action="store_true")
    generate.add_argument(
        "--supersede-bundle-id",
        help="explicit active bundle ID to preserve and replace with a new lock version",
    )
    generate.add_argument(
        "--correction-reason",
        help="auditable reason for a superseding correction (requires parent bundle ID)",
    )
    generate.add_argument(
        "--no-live-check",
        action="store_true",
        help="skip latest-source freshness check (offline diagnostics only)",
    )

    score = subparsers.add_parser(
        "score", help="verify a result and score only its date-matched locked bundle"
    )
    score.add_argument("--draw-date", type=_date)

    cycle = subparsers.add_parser(
        "cycle", help="verify, score, update history, recalibrate, generate, and lock"
    )
    cycle.add_argument("--seed", type=int)
    cycle.add_argument(
        "--publish",
        action="store_true",
        help="commit and push generated artifacts from the current non-main branch",
    )

    subparsers.add_parser("audit", help="verify all append-only indexes and checksums")

    backtest = subparsers.add_parser(
        "backtest", help="run a no-future-information walk-forward diagnostic"
    )
    backtest.add_argument("--evaluations", type=int, default=3)
    backtest.add_argument(
        "--candidate-pool-size",
        type=int,
        default=50_000,
        help="candidate count per fold (50,000 preserves the production selection path)",
    )

    subparsers.add_parser("report", help="write immutable JSON and Markdown score reports")
    return parser


def _legacy_audit_summary(project_root: Path) -> dict[str, object] | None:
    path = project_root / "data" / "reconciled" / "legacy_audit_manifest.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    integrity = verify_legacy_inventory(path, repository_root=project_root)
    return {
        "manifest": path.relative_to(project_root).as_posix(),
        "integrity": integrity,
        "file_count": len(raw.get("files", [])),
        "finding_count": len(raw.get("findings", [])),
        "summary": raw.get("summary", {}),
        "correction_count": len(raw.get("corrections", [])),
    }


def run(args: argparse.Namespace) -> object:
    seed = getattr(args, "seed", None)
    if seed is not None and seed < 0:
        raise ValueError("seed must be non-negative")
    app = Application.create(project_root=args.project_root, config_path=args.config)
    if args.command == "status":
        return app.status()
    if args.command == "show-bundle":
        return app.bundle_view(args.bundle_id)
    if args.command == "odds":
        return app.bundle_odds(args.bundle_id)
    if args.command == "rebuild-history":
        path = app.rebuild_history(minimum_draws=args.minimum_draws)
        history = app.history_store.load_latest()
        assert history is not None
        return {
            "status": "locked",
            "artifact": str(path),
            "draw_count": len(history[0]),
            "history_cutoff_date": history[0][-1].draw_date.isoformat(),
            "history_snapshot_sha256": history[1],
        }
    if args.command == "verify-latest":
        draw = app.verify_latest()
        return draw.model_dump(mode="json")
    if args.command == "generate":
        bundle = app.generate(
            draw_date=args.draw_date,
            random_seed=args.seed,
            verify_current_sources=not args.no_live_check,
            force_reselection=args.force_reselection,
            supersede_bundle_id=args.supersede_bundle_id,
            correction_reason=args.correction_reason,
        )
        return {
            "status": "locked",
            "bundle_id": bundle.metadata.bundle_id,
            "intended_draw_date": bundle.metadata.intended_draw_date.isoformat(),
            "random_seed": bundle.metadata.random_seed,
            "candidate_pool_size": bundle.metadata.simulation.candidate_pool_size,
            "simulation_count": bundle.metadata.simulation.simulation_count,
            "estimated_metrics": bundle.metadata.simulation.model_dump(mode="json"),
        }
    if args.command == "score":
        score = app.score(draw_date=args.draw_date)
        return score.model_dump(mode="json")
    if args.command == "cycle":
        return app.cycle(random_seed=args.seed, publish=args.publish).as_dict()
    if args.command == "audit":
        return {
            "production_artifacts": app.audit(),
            "legacy_handoff": _legacy_audit_summary(app.project_root),
        }
    if args.command == "backtest":
        json_path, markdown_path = app.backtest(
            evaluations=args.evaluations,
            diagnostic_candidate_pool_size=args.candidate_pool_size,
        )
        return {"json_report": str(json_path), "markdown_report": str(markdown_path)}
    if args.command == "report":
        json_path, markdown_path = app.report()
        return {"json_report": str(json_path), "markdown_report": str(markdown_path)}
    raise AssertionError(f"unhandled command {args.command}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print_json(run(args))
    except (SLPError, SourceError, SourceVerificationError, ValidationError, ValueError) as exc:
        payload: dict[str, object] = {
            "status": "halted",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if isinstance(exc, SourceVerificationError):
            payload["audit_record"] = exc.audit_record
        print(json.dumps(payload, indent=2, sort_keys=True, default=str), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
