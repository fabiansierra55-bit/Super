"""Append-only, checksummed persistence for history, predictions, scores, and audit events."""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .constraints import validate_bundle
from .exceptions import (
    AlreadyScoredError,
    BundleNotFoundError,
    ImmutableArtifactError,
    IntegrityError,
    SourceMismatchError,
)
from .models import BundleScore, LockedBundle, VerifiedDraw

SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(UTC)


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            default=_json_default,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def indexed_artifact_reference(root: Path, artifact: Path) -> str:
    """Return a repository-portable path for an append-only store index."""

    try:
        relative = artifact.relative_to(root)
    except ValueError as exc:
        raise IntegrityError(f"artifact is outside its indexed store: {artifact}") from exc
    if not relative.parts or ".." in relative.parts:
        raise IntegrityError(f"invalid indexed artifact path: {artifact}")
    return relative.as_posix()


def resolve_indexed_artifact(
    root: Path,
    stored_path: str,
    *,
    expected_parent: str,
) -> Path:
    """Resolve current relative references and fail-safe legacy absolute ones.

    Early local artifacts recorded an absolute workstation path. Their hash
    chains must not be rewritten, so a fresh clone rebases only the suffix
    beginning at the known store parent (``versions`` or ``locked``).
    """

    stored = Path(stored_path)
    if stored.is_absolute():
        positions = [index for index, part in enumerate(stored.parts) if part == expected_parent]
        if not positions:
            raise IntegrityError(f"indexed artifact has no {expected_parent!r} parent: {stored}")
        relative = Path(*stored.parts[positions[-1] :])
    else:
        relative = stored
    if not relative.parts or relative.parts[0] != expected_parent or ".." in relative.parts:
        raise IntegrityError(f"unsafe indexed artifact reference: {stored_path}")
    return root / relative


def write_new_file(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    """Create a file exactly once, refusing to replace any existing bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise ImmutableArtifactError(f"refusing to replace existing artifact: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, mode)
    except BaseException:
        # The file may be partial, but is deliberately not unlinked: operators must audit it.
        raise


class AppendOnlyLog:
    """A line-delimited audit log with idempotent event IDs and a SHA-256 chain."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.endswith("\n"):
                    raise IntegrityError(f"truncated append-only log at {self.path}:{line_number}")
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise IntegrityError(
                        f"invalid JSON in append-only log {self.path}:{line_number}"
                    ) from exc
                if not isinstance(event, dict):
                    raise IntegrityError(f"non-object audit event at {self.path}:{line_number}")
                events.append(event)
        self._verify_events(events)
        return events

    @staticmethod
    def _verify_events(events: Sequence[Mapping[str, Any]]) -> None:
        previous_hash: str | None = None
        seen_ids: set[str] = set()
        for offset, stored in enumerate(events, start=1):
            event = dict(stored)
            event_hash = event.pop("event_hash", None)
            if not isinstance(event_hash, str):
                raise IntegrityError(f"event {offset} has no event_hash")
            if event.get("previous_hash") != previous_hash:
                raise IntegrityError(f"event {offset} breaks the append-only hash chain")
            if sha256_bytes(canonical_json_bytes(event)) != event_hash:
                raise IntegrityError(f"event {offset} hash does not match its content")
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or event_id in seen_ids:
                raise IntegrityError(f"event {offset} has an invalid or duplicate event_id")
            seen_ids.add(event_id)
            previous_hash = event_hash

    def read(self) -> list[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
            try:
                return self._read_unlocked()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        timestamp_utc: datetime | None = None,
    ) -> dict[str, Any]:
        if not event_id or not event_type:
            raise ValueError("event_id and event_type are required")
        timestamp = timestamp_utc or utc_now()
        if timestamp.tzinfo is None:
            raise ValueError("audit timestamp must be timezone-aware")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                events = self._read_unlocked()
                comparable_payload = json.loads(canonical_json_bytes(payload))
                for existing in events:
                    if existing["event_id"] != event_id:
                        continue
                    if (
                        existing["event_type"] != event_type
                        or existing["payload"] != comparable_payload
                    ):
                        raise IntegrityError(
                            f"event_id {event_id!r} already exists with different content"
                        )
                    return existing

                event: dict[str, Any] = {
                    "event_id": event_id,
                    "event_type": event_type,
                    "timestamp_utc": timestamp.astimezone(UTC).isoformat(),
                    "payload": comparable_payload,
                    "previous_hash": events[-1]["event_hash"] if events else None,
                }
                event["event_hash"] = sha256_bytes(canonical_json_bytes(event))
                with self.path.open("ab") as output:
                    output.write(canonical_json_bytes(event))
                    output.flush()
                    os.fsync(output.fileno())
                return event
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def verify(self) -> int:
        return len(self.read())


class HistoryStore:
    """Versioned, immutable snapshots of draw history."""

    def __init__(self, root: Path, audit_log: AppendOnlyLog) -> None:
        self.root = root
        self.versions = root / "versions"
        self.index = AppendOnlyLog(root / "index.jsonl")
        self.attestations = AppendOnlyLog(root / "attestations.jsonl")
        self.audit_log = audit_log

    @staticmethod
    def _validate_draws(draws: Sequence[VerifiedDraw]) -> list[VerifiedDraw]:
        ordered = sorted(draws, key=lambda draw: draw.draw_date)
        dates: set[date] = set()
        draw_ids: set[str] = set()
        for draw in ordered:
            if draw.draw_date in dates:
                raise IntegrityError(f"duplicate draw date in history: {draw.draw_date}")
            dates.add(draw.draw_date)
            if draw.draw_id:
                if draw.draw_id in draw_ids:
                    raise IntegrityError(f"duplicate official draw ID in history: {draw.draw_id}")
                draw_ids.add(draw.draw_id)
            if draw.verification.status != "verified":
                raise IntegrityError(f"unverified draw cannot enter history: {draw.draw_date}")
        return ordered

    @staticmethod
    def _verification_is_causal(draw: VerifiedDraw) -> bool:
        latest_fetch = max(source.fetched_timestamp_utc for source in draw.verification.sources)
        verified = draw.verification.verified_timestamp_utc
        official_post = draw.verification.official_post_timestamp_utc
        if verified is None or official_post is None:
            return False
        return verified >= latest_fetch and verified >= official_post

    def store_snapshot(
        self,
        draws: Sequence[VerifiedDraw],
        *,
        reason: str,
        created_timestamp_utc: datetime | None = None,
    ) -> Path:
        ordered = self._validate_draws(draws)
        if not ordered:
            raise IntegrityError("refusing to store an empty history snapshot")
        created = (created_timestamp_utc or utc_now()).astimezone(UTC)
        draw_payload = [draw.model_dump(mode="json") for draw in ordered]
        content_sha256 = sha256_bytes(
            canonical_json_bytes({"schema_version": SCHEMA_VERSION, "draws": draw_payload})
        )
        artifact: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "created_timestamp_utc": created.isoformat(),
            "reason": reason,
            "draw_count": len(ordered),
            "history_cutoff_date": ordered[-1].draw_date.isoformat(),
            "draws": draw_payload,
            "content_sha256": content_sha256,
        }
        filename = f"history-{ordered[-1].draw_date.isoformat()}-{content_sha256[:16]}.json"
        path = self.versions / filename
        encoded = canonical_json_bytes(artifact)
        file_sha256 = sha256_bytes(encoded)
        index_events = self.index.read()
        for existing_event in index_events:
            existing_payload = _event_payload(existing_event, event_type="history_snapshot_locked")
            if "file_sha256" not in existing_payload:
                self._attest_event(existing_event, timestamp_utc=utc_now())
        indexed_paths: set[Path] = set()
        for event in index_events:
            event_payload = _event_payload(event, event_type="history_snapshot_locked")
            indexed_path = resolve_indexed_artifact(
                self.root,
                str(event_payload.get("artifact_path")),
                expected_parent="versions",
            )
            if indexed_path in indexed_paths:
                raise IntegrityError(
                    f"history artifact path is indexed more than once: {indexed_path}"
                )
            indexed_paths.add(indexed_path)
        physical_paths = set(self.versions.iterdir()) if self.versions.exists() else set()
        missing = indexed_paths - physical_paths
        unrelated_orphans = (physical_paths - indexed_paths) - {path}
        if missing:
            raise IntegrityError(f"indexed history artifact is missing: {sorted(missing)[0]}")
        if unrelated_orphans:
            raise IntegrityError(
                f"unrelated orphan history artifact blocks locking: {sorted(unrelated_orphans)[0]}"
            )
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise IntegrityError(f"cannot validate existing history artifact: {path}") from exc
            existing_hash = sha256_bytes(
                canonical_json_bytes(
                    {
                        "schema_version": existing.get("schema_version"),
                        "draws": existing.get("draws"),
                    }
                )
            )
            if existing.get("content_sha256") != existing_hash or existing_hash != content_sha256:
                raise IntegrityError(f"history artifact hash collision or mutation: {path}")
            # An idempotent rerun may supply a new reason/timestamp. The first
            # immutable file remains authoritative and is bound in the index.
            file_sha256 = sha256_file(path)
        else:
            write_new_file(path, encoded)

        event_payload = {
            "artifact_path": indexed_artifact_reference(self.root, path),
            "content_sha256": content_sha256,
            "file_sha256": file_sha256,
            "draw_count": len(ordered),
            "history_cutoff_date": ordered[-1].draw_date.isoformat(),
        }
        event_id = f"history:{content_sha256}"
        indexed = [event for event in index_events if event.get("event_id") == event_id]
        if indexed:
            if len(indexed) != 1:
                raise IntegrityError(f"duplicate history identity in index: {event_id}")
            weak_payload = _event_payload(indexed[0], event_type="history_snapshot_locked")
            if (
                weak_payload.get("content_sha256") != content_sha256
                or resolve_indexed_artifact(
                    self.root,
                    str(weak_payload.get("artifact_path")),
                    expected_parent="versions",
                )
                != path
            ):
                raise IntegrityError(f"history index identity conflicts with artifact: {path}")
            if "file_sha256" not in weak_payload:
                self._attest_event(indexed[0], timestamp_utc=created)
            self.audit_log.append(
                event_id=str(indexed[0]["event_id"]),
                event_type="history_snapshot_locked",
                payload=weak_payload,
                timestamp_utc=datetime.fromisoformat(str(indexed[0]["timestamp_utc"])),
            )
        else:
            self.index.append(
                event_id=event_id,
                event_type="history_snapshot_locked",
                payload=event_payload,
                timestamp_utc=created,
            )
            self.audit_log.append(
                event_id=event_id,
                event_type="history_snapshot_locked",
                payload=event_payload,
                timestamp_utc=created,
            )
        return path

    def _load_event_artifact(self, event: Mapping[str, Any]) -> tuple[list[VerifiedDraw], str]:
        payload = _event_payload(event, event_type="history_snapshot_locked")
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="versions",
        )
        expected_path = self.versions / path.name
        if path != expected_path:
            raise IntegrityError(f"history artifact is indexed at a noncanonical path: {path}")
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"cannot read indexed history artifact {path}") from exc
        stored_hash = artifact.get("content_sha256")
        calculated = sha256_bytes(
            canonical_json_bytes(
                {
                    "schema_version": artifact.get("schema_version"),
                    "draws": artifact.get("draws"),
                }
            )
        )
        if stored_hash != calculated or stored_hash != payload["content_sha256"]:
            raise IntegrityError(f"history artifact failed checksum validation: {path}")
        expected_file_hash = payload.get("file_sha256")
        if expected_file_hash is None:
            expected_file_hash = _attestation_for(
                self.attestations,
                index_event=event,
                binding_name="file_sha256",
            )
        if expected_file_hash is None:
            raise IntegrityError(
                f"history index event {event.get('event_id')} needs a full-file attestation"
            )
        if sha256_file(path) != expected_file_hash:
            raise IntegrityError(f"history full-file checksum failed: {path}")
        draws = [VerifiedDraw.model_validate(item) for item in artifact["draws"]]
        self._validate_draws(draws)
        if not draws:
            raise IntegrityError(f"indexed history artifact is empty: {path}")
        expected_filename = f"history-{draws[-1].draw_date.isoformat()}-{calculated[:16]}.json"
        if (
            path.name != expected_filename
            or payload.get("draw_count") != len(draws)
            or payload.get("history_cutoff_date") != draws[-1].draw_date.isoformat()
        ):
            raise IntegrityError(f"history index metadata does not match artifact: {path}")
        return draws, calculated

    def _attest_event(
        self,
        event: Mapping[str, Any],
        *,
        timestamp_utc: datetime,
    ) -> bool:
        payload = _event_payload(event, event_type="history_snapshot_locked")
        if "file_sha256" in payload:
            return False
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="versions",
        )
        # Validate the original content checksum before recording a stronger binding.
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"cannot attest history artifact {path}") from exc
        calculated = sha256_bytes(
            canonical_json_bytes(
                {
                    "schema_version": artifact.get("schema_version"),
                    "draws": artifact.get("draws"),
                }
            )
        )
        if (
            calculated != payload.get("content_sha256")
            or artifact.get("content_sha256") != calculated
        ):
            raise IntegrityError(f"cannot attest mutated history artifact: {path}")
        file_sha256 = sha256_file(path)
        existing_attestation = _attestation_for(
            self.attestations,
            index_event=event,
            binding_name="file_sha256",
        )
        if existing_attestation is not None and existing_attestation != file_sha256:
            raise IntegrityError(f"history artifact changed after attestation: {path}")
        _append_attestation(
            log=self.attestations,
            audit_log=self.audit_log,
            store_kind="history",
            index_event=event,
            artifact_path=indexed_artifact_reference(self.root, path),
            binding_name="file_sha256",
            binding_value=file_sha256,
            timestamp_utc=timestamp_utc,
        )
        return existing_attestation is None

    def attest_existing(self, *, timestamp_utc: datetime | None = None) -> int:
        timestamp = (timestamp_utc or utc_now()).astimezone(UTC)
        return sum(
            self._attest_event(event, timestamp_utc=timestamp) for event in self.index.read()
        )

    def audit_integrity(self) -> int:
        events = self.index.read()
        indexed_paths: set[Path] = set()
        previous_results: dict[date, VerifiedDraw] = {}
        for event in events:
            payload = _event_payload(event, event_type="history_snapshot_locked")
            path = resolve_indexed_artifact(
                self.root,
                str(payload.get("artifact_path")),
                expected_parent="versions",
            )
            if path in indexed_paths:
                raise IntegrityError(f"history artifact path is indexed more than once: {path}")
            indexed_paths.add(path)
            draws, _ = self._load_event_artifact(event)
            current_results = {draw.draw_date: draw for draw in draws}
            for draw_date, previous in previous_results.items():
                current = current_results.get(draw_date)
                if current is None:
                    raise IntegrityError(f"history snapshot removed locked draw {draw_date}")
                if (
                    current.mains != previous.mains
                    or current.mega != previous.mega
                    or (
                        current.draw_id is not None
                        and previous.draw_id is not None
                        and current.draw_id != previous.draw_id
                    )
                ):
                    raise IntegrityError(f"history snapshot changed locked result {draw_date}")
            previous_results = current_results
        physical = (
            {path for path in self.versions.iterdir() if path.is_file() and not path.is_symlink()}
            if self.versions.exists()
            else set()
        )
        if self.versions.exists():
            invalid = [
                path for path in self.versions.iterdir() if path.is_symlink() or not path.is_file()
            ]
            if invalid:
                raise IntegrityError(f"unexpected history version entry: {invalid[0]}")
        missing = indexed_paths - physical
        orphaned = physical - indexed_paths
        if missing:
            raise IntegrityError(f"indexed history artifact is missing: {sorted(missing)[0]}")
        if orphaned:
            raise IntegrityError(f"orphan history artifact is not indexed: {sorted(orphaned)[0]}")
        event_paths = {
            event["event_hash"]: resolve_indexed_artifact(
                self.root,
                str(event["payload"]["artifact_path"]),
                expected_parent="versions",
            )
            for event in events
        }
        for attestation in self.attestations.read():
            payload = _event_payload(attestation, event_type="immutable_artifact_attested")
            expected_path = event_paths.get(payload.get("index_event_hash"))
            attested_path = resolve_indexed_artifact(
                self.root,
                str(payload.get("artifact_path")),
                expected_parent="versions",
            )
            if (
                payload.get("store_kind") != "history"
                or expected_path is None
                or attested_path != expected_path
            ):
                raise IntegrityError("history attestation has no matching index event")
        return len(events)

    def load_latest(self) -> tuple[list[VerifiedDraw], str] | None:
        events = self.index.read()
        if not events:
            return None
        return self._load_event_artifact(events[-1])

    def merge_verified(
        self,
        new_draws: Iterable[VerifiedDraw],
        *,
        reason: str,
        created_timestamp_utc: datetime | None = None,
    ) -> Path:
        current = self.load_latest()
        merged = {draw.draw_date: draw for draw in current[0]} if current else {}
        for draw in new_draws:
            existing = merged.get(draw.draw_date)
            if existing and (existing.mains != draw.mains or existing.mega != draw.mega):
                raise SourceMismatchError(
                    f"verified result conflicts with locked history for {draw.draw_date}"
                )
            if existing and existing.draw_id and draw.draw_id and existing.draw_id != draw.draw_id:
                raise SourceMismatchError(
                    f"official draw ID conflicts with locked history for {draw.draw_date}"
                )
            if existing is None:
                merged[draw.draw_date] = draw
            elif not self._verification_is_causal(existing):
                if not self._verification_is_causal(draw):
                    raise IntegrityError(
                        f"replacement verification remains chronologically invalid for "
                        f"{draw.draw_date}"
                    )
                # This creates a new immutable history version; the original
                # artifact and its hash-chained index event remain untouched.
                merged[draw.draw_date] = draw
        return self.store_snapshot(
            list(merged.values()),
            reason=reason,
            created_timestamp_utc=created_timestamp_utc,
        )


def _csv_bytes(fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _build_manifest(files: Mapping[str, bytes], *, artifact_sha256: str) -> bytes:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_sha256": artifact_sha256,
        "files": {name: sha256_bytes(payload) for name, payload in sorted(files.items())},
    }
    return canonical_json_bytes(manifest)


def _write_artifact_directory(final_directory: Path, files: Mapping[str, bytes]) -> None:
    """Publish a complete directory atomically without replacing an existing lock."""

    final_directory.parent.mkdir(parents=True, exist_ok=True)
    if final_directory.exists():
        raise ImmutableArtifactError(f"locked artifact already exists: {final_directory}")
    staging_parent = final_directory.parent
    staging = Path(tempfile.mkdtemp(prefix=f".{final_directory.name}-", dir=staging_parent))
    try:
        for name, payload in files.items():
            write_new_file(staging / name, payload)
        try:
            os.rename(staging, final_directory)
        except FileExistsError as exc:
            raise ImmutableArtifactError(
                f"locked artifact was created concurrently: {final_directory}"
            ) from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _verify_artifact_directory(directory: Path) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise IntegrityError(f"artifact path is not a regular directory: {directory}")
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise IntegrityError(f"artifact manifest is a symbolic link: {directory}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"missing or invalid artifact manifest: {directory}") from exc
    if not isinstance(manifest, dict):
        raise IntegrityError(f"artifact manifest root is not an object: {directory}")
    if set(manifest) != {"schema_version", "artifact_sha256", "files"}:
        raise IntegrityError(f"artifact manifest has unexpected fields: {directory}")
    artifact_sha256 = manifest.get("artifact_sha256")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or not isinstance(artifact_sha256, str)
        or len(artifact_sha256) != 64
    ):
        raise IntegrityError(f"artifact manifest has invalid identity fields: {directory}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise IntegrityError(f"artifact manifest has no file checksums: {directory}")
    declared = set(files)
    if declared not in ({"bundle.json", "tickets.csv"}, {"score.json", "tickets.csv"}):
        raise IntegrityError(f"artifact manifest declares an invalid file set: {directory}")
    actual = {path.name for path in directory.iterdir()}
    if actual != declared | {"manifest.json"}:
        raise IntegrityError(f"artifact directory and manifest file sets differ: {directory}")
    for name, expected in files.items():
        if not isinstance(name, str) or Path(name).name != name:
            raise IntegrityError(f"artifact manifest has an unsafe filename: {directory}")
        if not isinstance(expected, str) or len(expected) != 64:
            raise IntegrityError(f"artifact manifest has an invalid checksum: {directory}")
        path = directory / name
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise IntegrityError(f"locked artifact file failed checksum: {path}")
    manifest_sha256 = sha256_file(manifest_path)
    manifest["manifest_sha256"] = manifest_sha256
    return cast(dict[str, Any], manifest)


def _event_payload(event: Mapping[str, Any], *, event_type: str) -> dict[str, Any]:
    if event.get("event_type") != event_type:
        raise IntegrityError(
            f"unexpected {event.get('event_type')!r} event in {event_type!r} index"
        )
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise IntegrityError(f"{event_type} index event has no object payload")
    return cast(dict[str, Any], payload)


def _physical_artifact_directories(locked_root: Path) -> set[Path]:
    """Return every two-level artifact directory without trusting its name."""

    if not locked_root.exists():
        return set()
    if locked_root.is_symlink() or not locked_root.is_dir():
        raise IntegrityError(f"locked artifact root is not a regular directory: {locked_root}")
    directories: set[Path] = set()
    for first_level in locked_root.iterdir():
        if first_level.is_symlink() or not first_level.is_dir():
            raise IntegrityError(f"unexpected entry in locked artifact root: {first_level}")
        for artifact in first_level.iterdir():
            if artifact.is_symlink() or not artifact.is_dir():
                raise IntegrityError(f"unexpected entry in artifact date directory: {artifact}")
            directories.add(artifact)
    return directories


def _attestation_for(
    log: AppendOnlyLog,
    *,
    index_event: Mapping[str, Any],
    binding_name: str,
) -> str | None:
    matches: list[dict[str, Any]] = []
    for event in log.read():
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise IntegrityError("attestation event has no object payload")
        if payload.get("index_event_hash") == index_event.get("event_hash"):
            matches.append(event)
    if len(matches) > 1:
        raise IntegrityError(
            f"duplicate attestations for index event {index_event.get('event_id')}"
        )
    if not matches:
        return None
    payload = matches[0].get("payload")
    if not isinstance(payload, dict) or payload.get("index_event_id") != index_event.get(
        "event_id"
    ):
        raise IntegrityError(f"attestation identity mismatch for {index_event.get('event_id')}")
    value = payload.get(binding_name)
    if not isinstance(value, str) or len(value) != 64:
        raise IntegrityError(f"attestation has no valid {binding_name}")
    return value


def _append_attestation(
    *,
    log: AppendOnlyLog,
    audit_log: AppendOnlyLog,
    store_kind: str,
    index_event: Mapping[str, Any],
    artifact_path: str,
    binding_name: str,
    binding_value: str,
    timestamp_utc: datetime,
) -> None:
    event_hash = index_event.get("event_hash")
    event_id = index_event.get("event_id")
    if not isinstance(event_hash, str) or not isinstance(event_id, str):
        raise IntegrityError("cannot attest an invalid index event")
    payload = {
        "store_kind": store_kind,
        "index_event_id": event_id,
        "index_event_hash": event_hash,
        "artifact_path": artifact_path,
        binding_name: binding_value,
    }
    attestation_id = f"attestation:{store_kind}:{event_hash}"
    log.append(
        event_id=attestation_id,
        event_type="immutable_artifact_attested",
        payload=payload,
        timestamp_utc=timestamp_utc,
    )
    audit_log.append(
        event_id=attestation_id,
        event_type="immutable_artifact_attested",
        payload=payload,
        timestamp_utc=timestamp_utc,
    )


class BundleStore:
    """Immutable prediction bundle directories and an append-only identity index."""

    def __init__(self, root: Path, audit_log: AppendOnlyLog) -> None:
        self.root = root
        self.locked_root = root / "locked"
        self.index = AppendOnlyLog(root / "index.jsonl")
        self.attestations = AppendOnlyLog(root / "attestations.jsonl")
        self.audit_log = audit_log

    def _load_directory(self, directory: Path) -> LockedBundle:
        manifest = _verify_artifact_directory(directory)
        payload = (directory / "bundle.json").read_bytes()
        if sha256_bytes(payload) != manifest["artifact_sha256"]:
            raise IntegrityError(f"bundle content hash mismatch: {directory}")
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"invalid locked bundle JSON: {directory}") from exc
        bundle = LockedBundle.model_validate(raw["bundle"])
        self._validate_semantic_claims(bundle)
        return bundle

    @staticmethod
    def _validate_semantic_claims(bundle: LockedBundle) -> None:
        """Recompute claims that hashes and schema validation cannot establish."""

        metadata = bundle.metadata
        constraints = metadata.optimizer.constraints
        try:
            validate_bundle(
                bundle.lines,
                max_overlap=int(constraints["max_main_overlap"]),
                min_hamming=int(constraints["min_hamming_distance"]),
                pair_cap=int(constraints["pair_repeat_cap"]),
                triple_cap=int(constraints["triple_repeat_cap"]),
                mega_hard_cap=int(constraints["mega_hard_cap"]),
                expected_size=metadata.bundle_size,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("locked bundle violates its recorded constraints") from exc
        stored = metadata.simulation.fair_uniform_exact
        if stored is not None:
            from .fair_odds import exact_uniform_metrics

            calculated = exact_uniform_metrics(bundle.lines)
            if calculated != stored:
                raise IntegrityError("locked fair-coverage metrics do not match bundle lines")
        evidence = metadata.optimizer.fair_coverage_challenger
        if (
            metadata.lock_version > 1
            and evidence is not None
            and evidence.evidence_version >= 2
            and (evidence.incumbent is None or evidence.incumbent_model_simulation is None)
        ):
            raise IntegrityError("versioned correction evidence omits its incumbent binding")
        if evidence is not None and evidence.selected and stored != evidence.challenger:
            raise IntegrityError("selected challenger evidence does not match locked bundle lines")
        if evidence is not None and evidence.evidence_version >= 2:
            raw_config = json.dumps(
                metadata.configuration_snapshot,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            if hashlib.sha256(raw_config).hexdigest() != metadata.configuration_sha256:
                raise IntegrityError("locked configuration snapshot hash does not match")

    def _load_event(self, event: Mapping[str, Any]) -> tuple[LockedBundle, Path]:
        payload = _event_payload(event, event_type="prediction_bundle_locked")
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="locked",
        )
        bundle_id = payload.get("bundle_id")
        draw_date = payload.get("intended_draw_date")
        if not isinstance(bundle_id, str) or not isinstance(draw_date, str):
            raise IntegrityError("bundle index event has invalid identity fields")
        expected = self.locked_root / draw_date / bundle_id
        if path != expected:
            raise IntegrityError(f"bundle is indexed at a noncanonical path: {path}")
        manifest = _verify_artifact_directory(path)
        expected_manifest_hash = payload.get("manifest_sha256")
        if expected_manifest_hash is None:
            expected_manifest_hash = _attestation_for(
                self.attestations,
                index_event=event,
                binding_name="manifest_sha256",
            )
        if expected_manifest_hash is None:
            raise IntegrityError(
                f"bundle index event {event.get('event_id')} needs a manifest attestation"
            )
        if manifest["manifest_sha256"] != expected_manifest_hash:
            raise IntegrityError(f"bundle manifest is not bound to its index: {path}")
        bundle = self._load_directory(path)
        metadata = bundle.metadata
        if (
            metadata.bundle_id != bundle_id
            or metadata.intended_draw_date.isoformat() != draw_date
            or metadata.lock_version != payload.get("lock_version")
            or metadata.supersedes_bundle_id != payload.get("supersedes_bundle_id")
            or manifest["artifact_sha256"] != payload.get("artifact_sha256")
        ):
            raise IntegrityError(f"bundle index metadata does not match artifact: {path}")
        return bundle, path

    def _indexed_entries(self) -> list[tuple[Mapping[str, Any], LockedBundle, Path]]:
        entries: list[tuple[Mapping[str, Any], LockedBundle, Path]] = []
        bundle_ids: set[str] = set()
        paths: set[Path] = set()
        for event in self.index.read():
            bundle, path = self._load_event(event)
            bundle_id = bundle.metadata.bundle_id
            if bundle_id in bundle_ids:
                raise IntegrityError(f"bundle_id is reused globally: {bundle_id}")
            if path in paths:
                raise IntegrityError(f"bundle artifact path is indexed more than once: {path}")
            bundle_ids.add(bundle_id)
            paths.add(path)
            entries.append((event, bundle, path))
        by_id = {bundle.metadata.bundle_id: bundle for _, bundle, _ in entries}
        for _, bundle, _ in entries:
            evidence = bundle.metadata.optimizer.fair_coverage_challenger
            parent_id = bundle.metadata.supersedes_bundle_id
            if evidence is None or parent_id is None:
                continue
            if evidence.evidence_version >= 2 and (
                evidence.incumbent is None or evidence.incumbent_model_simulation is None
            ):
                raise IntegrityError("versioned correction evidence omits its incumbent")
            if evidence.incumbent is None:
                continue
            parent = by_id.get(parent_id)
            if parent is None:
                raise IntegrityError("challenger evidence references a missing incumbent bundle")
            from .fair_odds import exact_uniform_metrics

            if exact_uniform_metrics(parent.lines) != evidence.incumbent:
                raise IntegrityError("challenger incumbent metrics do not match its parent bundle")
            if (
                evidence.incumbent_model_simulation is not None
                and evidence.incumbent_model_simulation != parent.metadata.simulation
            ):
                raise IntegrityError(
                    "challenger incumbent simulation does not match its parent bundle"
                )
        return entries

    def list_for_draw(self, draw_date: date) -> list[LockedBundle]:
        self.audit_integrity()
        return [
            bundle
            for _, bundle, _ in self._indexed_entries()
            if bundle.metadata.intended_draw_date == draw_date
        ]

    def list_bundles(self) -> list[LockedBundle]:
        """Return every immutable version in canonical draw/version order."""

        self.audit_integrity()
        bundles = [bundle for _, bundle, _ in self._indexed_entries()]
        return sorted(
            bundles,
            key=lambda bundle: (
                bundle.metadata.intended_draw_date,
                bundle.metadata.lock_version,
                bundle.metadata.bundle_id,
            ),
        )

    def active_for_draw(self, draw_date: date) -> LockedBundle:
        bundles = self.list_for_draw(draw_date)
        if not bundles:
            raise BundleNotFoundError(f"no locked bundle for draw {draw_date}")
        superseded = {
            bundle.metadata.supersedes_bundle_id
            for bundle in bundles
            if bundle.metadata.supersedes_bundle_id
        }
        active = [bundle for bundle in bundles if bundle.metadata.bundle_id not in superseded]
        if len(active) != 1:
            raise IntegrityError(
                f"draw {draw_date} has {len(active)} active bundle versions; expected exactly one"
            )
        return active[0]

    def find(self, bundle_id: str) -> LockedBundle:
        self.audit_integrity()
        matches = [
            bundle
            for _, bundle, _ in self._indexed_entries()
            if bundle.metadata.bundle_id == bundle_id
        ]
        if len(matches) != 1:
            raise BundleNotFoundError(f"bundle_id {bundle_id!r} is missing or ambiguous")
        return matches[0]

    def lock(
        self,
        bundle: LockedBundle,
        *,
        previous_draw_mains: tuple[int, int, int, int, int] | None,
        max_overlap: int = 3,
        min_hamming: int = 2,
        pair_cap: int = 2,
        triple_cap: int = 1,
        mega_hard_cap: int = 5,
        aggressive_previous_overlap_cap: int = 1,
        official_post_time_pacific: time = time(20, 0),
    ) -> Path:
        metadata = bundle.metadata
        self._validate_semantic_claims(bundle)
        validate_bundle(
            bundle.lines,
            max_overlap=max_overlap,
            min_hamming=min_hamming,
            pair_cap=pair_cap,
            triple_cap=triple_cap,
            mega_hard_cap=mega_hard_cap,
            expected_size=metadata.bundle_size,
            previous_draw_mains=previous_draw_mains,
            aggressive_previous_overlap_cap=aggressive_previous_overlap_cap,
        )
        post_timestamp = datetime.combine(
            metadata.intended_draw_date,
            official_post_time_pacific,
            tzinfo=ZoneInfo("America/Los_Angeles"),
        ).astimezone(UTC)
        if metadata.generated_timestamp_utc >= post_timestamp:
            raise IntegrityError("bundle was generated at or after the intended draw post time")

        indexed_entries = self._indexed_entries()
        identity_matches = [
            (event, item, path)
            for event, item, path in indexed_entries
            if item.metadata.bundle_id == metadata.bundle_id
        ]
        if identity_matches:
            if len(identity_matches) != 1 or identity_matches[0][1] != bundle:
                raise ImmutableArtifactError(
                    f"bundle_id {metadata.bundle_id!r} is already locked with different content"
                )
            self.audit_integrity()
            event, _, path = identity_matches[0]
            existing_event_payload = _event_payload(event, event_type="prediction_bundle_locked")
            event_timestamp = datetime.fromisoformat(str(event["timestamp_utc"]))
            self.audit_log.append(
                event_id=str(event["event_id"]),
                event_type="prediction_bundle_locked",
                payload=existing_event_payload,
                timestamp_utc=event_timestamp,
            )
            return path

        existing = [
            item
            for _, item, _ in indexed_entries
            if item.metadata.intended_draw_date == metadata.intended_draw_date
        ]
        if existing:
            superseded = {
                item.metadata.supersedes_bundle_id
                for item in existing
                if item.metadata.supersedes_bundle_id
            }
            active_items = [item for item in existing if item.metadata.bundle_id not in superseded]
            if len(active_items) != 1:
                raise IntegrityError(
                    f"draw {metadata.intended_draw_date} has {len(active_items)} active bundles"
                )
            active = active_items[0]
            if metadata.supersedes_bundle_id != active.metadata.bundle_id:
                raise ImmutableArtifactError(
                    "a correction must explicitly supersede the active locked bundle"
                )
            if metadata.lock_version != active.metadata.lock_version + 1:
                raise ImmutableArtifactError("correction lock_version must increase by exactly one")
        elif metadata.supersedes_bundle_id is not None or metadata.lock_version != 1:
            raise ImmutableArtifactError("first bundle for a draw must be lock_version 1")

        lock_timestamp = utc_now().astimezone(UTC)
        if lock_timestamp >= post_timestamp:
            raise IntegrityError(
                "bundle lock was attempted at or after the intended draw post time"
            )
        if metadata.generated_timestamp_utc > lock_timestamp:
            raise IntegrityError("bundle generation timestamp is later than its lock timestamp")

        payload = canonical_json_bytes(
            {"schema_version": SCHEMA_VERSION, "bundle": bundle.model_dump(mode="json")}
        )
        artifact_sha256 = sha256_bytes(payload)
        verification_sha256 = sha256_bytes(
            canonical_json_bytes(metadata.source_verification_metadata.model_dump(mode="json"))
        )
        optimizer_json = json.dumps(
            metadata.optimizer.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        rows: list[dict[str, object]] = []
        for line in bundle.lines:
            selected = metadata.selected_hyperparameters
            rows.append(
                {
                    "bundle_id": metadata.bundle_id,
                    "lock_version": metadata.lock_version,
                    "generated_timestamp_utc": metadata.generated_timestamp_utc.isoformat(),
                    "intended_draw_date": metadata.intended_draw_date.isoformat(),
                    "draw_id": metadata.draw_id or "",
                    "game_rules_version": metadata.game_rules_version,
                    "model_version": metadata.model_version,
                    "configuration_sha256": metadata.configuration_sha256,
                    "random_seed": metadata.random_seed,
                    "source_verification_sha256": verification_sha256,
                    "history_cutoff_date": metadata.history_cutoff_date.isoformat(),
                    "history_snapshot_sha256": metadata.history_snapshot_sha256,
                    "main_window": selected.main_window,
                    "mega_window": selected.mega_window,
                    "main_sigma": selected.main_sigma,
                    "mega_sigma": selected.mega_sigma,
                    "main_half_life_draws": selected.main_half_life_draws,
                    "mega_half_life_draws": selected.mega_half_life_draws,
                    "candidate_pool_size": metadata.simulation.candidate_pool_size,
                    "candidate_pool_sha256": metadata.candidate_pool_sha256 or "",
                    "candidate_pool_algorithm_version": (
                        metadata.candidate_pool_algorithm_version or ""
                    ),
                    "simulation_count": metadata.simulation.simulation_count,
                    "fair_challenger_selected": (
                        metadata.optimizer.fair_coverage_challenger.selected
                        if metadata.optimizer.fair_coverage_challenger is not None
                        else ""
                    ),
                    "fair_exact_p_any_ge_3_mains": (
                        metadata.simulation.fair_uniform_exact.p_any_ge_3_mains
                        if metadata.simulation.fair_uniform_exact is not None
                        else ""
                    ),
                    "fair_exact_p_any_ge_4_mains": (
                        metadata.simulation.fair_uniform_exact.p_any_ge_4_mains
                        if metadata.simulation.fair_uniform_exact is not None
                        else ""
                    ),
                    "fair_exact_p_any_3_plus_mega": (
                        metadata.simulation.fair_uniform_exact.p_any_3_plus_mega
                        if metadata.simulation.fair_uniform_exact is not None
                        else ""
                    ),
                    "fair_exact_p_jackpot": (
                        metadata.simulation.fair_uniform_exact.p_jackpot
                        if metadata.simulation.fair_uniform_exact is not None
                        else ""
                    ),
                    "optimizer": optimizer_json,
                    "strategy": line.strategy,
                    "line_id": line.line_id,
                    "n1": line.mains[0],
                    "n2": line.mains[1],
                    "n3": line.mains[2],
                    "n4": line.mains[3],
                    "n5": line.mains[4],
                    "mega": line.mega,
                }
            )
        fields = list(rows[0]) if rows else []
        csv_payload = _csv_bytes(fields, rows)
        base_files = {"bundle.json": payload, "tickets.csv": csv_payload}
        files = dict(base_files)
        files["manifest.json"] = _build_manifest(base_files, artifact_sha256=artifact_sha256)
        final = self.locked_root / metadata.intended_draw_date.isoformat() / metadata.bundle_id
        same_id_paths = (
            list(self.locked_root.glob(f"*/{metadata.bundle_id}"))
            if self.locked_root.exists()
            else []
        )
        if any(path != final for path in same_id_paths):
            raise IntegrityError(
                f"bundle_id is reused outside its intended draw: {metadata.bundle_id}"
            )
        indexed_paths = {path for _, _, path in indexed_entries}
        physical_paths = _physical_artifact_directories(self.locked_root)
        missing = indexed_paths - physical_paths
        unrelated_orphans = (physical_paths - indexed_paths) - {final}
        if unrelated_orphans:
            raise IntegrityError(
                f"unrelated orphan bundle blocks locking: {sorted(unrelated_orphans)[0]}"
            )
        if missing:
            raise IntegrityError(f"indexed bundle artifact is missing: {sorted(missing)[0]}")
        if final.exists():
            # Recover only the exact atomic directory left by a crash between
            # directory publication and index append.
            manifest = _verify_artifact_directory(final)
            if (
                manifest["manifest_sha256"] != sha256_bytes(files["manifest.json"])
                or (final / "bundle.json").read_bytes() != payload
                or (final / "tickets.csv").read_bytes() != csv_payload
            ):
                raise IntegrityError(f"orphan bundle does not match retry content: {final}")
        else:
            _write_artifact_directory(final, files)

        event_payload = {
            "bundle_id": metadata.bundle_id,
            "intended_draw_date": metadata.intended_draw_date.isoformat(),
            "lock_version": metadata.lock_version,
            "supersedes_bundle_id": metadata.supersedes_bundle_id,
            "artifact_path": indexed_artifact_reference(self.root, final),
            "artifact_sha256": artifact_sha256,
            "manifest_sha256": sha256_bytes(files["manifest.json"]),
            "locked_timestamp_utc": lock_timestamp.isoformat(),
        }
        event_id = f"bundle:{metadata.bundle_id}:{artifact_sha256}"
        self.index.append(
            event_id=event_id,
            event_type="prediction_bundle_locked",
            payload=event_payload,
            timestamp_utc=lock_timestamp,
        )
        self.audit_log.append(
            event_id=event_id,
            event_type="prediction_bundle_locked",
            payload=event_payload,
            timestamp_utc=lock_timestamp,
        )
        return final

    def _attest_event(
        self,
        event: Mapping[str, Any],
        *,
        timestamp_utc: datetime,
    ) -> bool:
        payload = _event_payload(event, event_type="prediction_bundle_locked")
        if "manifest_sha256" in payload:
            return False
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="locked",
        )
        manifest = _verify_artifact_directory(path)
        bundle = self._load_directory(path)
        metadata = bundle.metadata
        expected = self.locked_root / metadata.intended_draw_date.isoformat() / metadata.bundle_id
        if (
            path != expected
            or payload.get("bundle_id") != metadata.bundle_id
            or payload.get("intended_draw_date") != metadata.intended_draw_date.isoformat()
            or payload.get("artifact_sha256") != manifest.get("artifact_sha256")
        ):
            raise IntegrityError(f"cannot attest mismatched bundle artifact: {path}")
        manifest_sha256 = str(manifest["manifest_sha256"])
        existing_attestation = _attestation_for(
            self.attestations,
            index_event=event,
            binding_name="manifest_sha256",
        )
        if existing_attestation is not None and existing_attestation != manifest_sha256:
            raise IntegrityError(f"bundle artifact changed after attestation: {path}")
        _append_attestation(
            log=self.attestations,
            audit_log=self.audit_log,
            store_kind="bundle",
            index_event=event,
            artifact_path=indexed_artifact_reference(self.root, path),
            binding_name="manifest_sha256",
            binding_value=manifest_sha256,
            timestamp_utc=timestamp_utc,
        )
        return existing_attestation is None

    def attest_existing(self, *, timestamp_utc: datetime | None = None) -> int:
        timestamp = (timestamp_utc or utc_now()).astimezone(UTC)
        return sum(
            self._attest_event(event, timestamp_utc=timestamp) for event in self.index.read()
        )

    def audit_integrity(self) -> int:
        entries = self._indexed_entries()
        indexed_paths = {path for _, _, path in entries}
        physical_paths = _physical_artifact_directories(self.locked_root)
        missing = indexed_paths - physical_paths
        orphaned = physical_paths - indexed_paths
        if missing:
            raise IntegrityError(f"indexed bundle artifact is missing: {sorted(missing)[0]}")
        if orphaned:
            raise IntegrityError(f"orphan bundle artifact is not indexed: {sorted(orphaned)[0]}")

        by_draw: dict[date, list[LockedBundle]] = {}
        for _, bundle, _ in entries:
            by_draw.setdefault(bundle.metadata.intended_draw_date, []).append(bundle)
        for draw_date, versions in by_draw.items():
            ordered = sorted(versions, key=lambda item: item.metadata.lock_version)
            first = ordered[0].metadata
            if first.lock_version != 1 or first.supersedes_bundle_id is not None:
                raise IntegrityError(f"bundle correction chain has no valid v1 for {draw_date}")
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if (
                    current.metadata.lock_version != previous.metadata.lock_version + 1
                    or current.metadata.supersedes_bundle_id != previous.metadata.bundle_id
                ):
                    raise IntegrityError(f"bundle correction chain is broken for {draw_date}")

        event_paths = {event["event_hash"]: path for event, _, path in entries}
        for attestation in self.attestations.read():
            payload = _event_payload(attestation, event_type="immutable_artifact_attested")
            expected_path = event_paths.get(payload.get("index_event_hash"))
            attested_path = resolve_indexed_artifact(
                self.root,
                str(payload.get("artifact_path")),
                expected_parent="locked",
            )
            if (
                payload.get("store_kind") != "bundle"
                or expected_path is None
                or attested_path != expected_path
            ):
                raise IntegrityError("bundle attestation has no matching index event")
        for event, bundle, _ in entries:
            payload = _event_payload(event, event_type="prediction_bundle_locked")
            locked_raw = payload.get("locked_timestamp_utc")
            if locked_raw is None:
                continue  # Legacy event; its immutable manifest is separately attested.
            try:
                locked = datetime.fromisoformat(str(locked_raw)).astimezone(UTC)
                event_timestamp = datetime.fromisoformat(str(event["timestamp_utc"])).astimezone(
                    UTC
                )
            except ValueError as exc:
                raise IntegrityError("bundle index has an invalid lock timestamp") from exc
            post = datetime.combine(
                bundle.metadata.intended_draw_date,
                time(20, 0),
                tzinfo=ZoneInfo("America/Los_Angeles"),
            ).astimezone(UTC)
            if (
                locked != event_timestamp
                or locked >= post
                or bundle.metadata.generated_timestamp_utc > locked
            ):
                raise IntegrityError("bundle index has a noncausal lock timestamp")
        return len(entries)


class ScoreStore:
    """Immutable line-by-line scoring artifacts and an append-only score index."""

    def __init__(self, root: Path, audit_log: AppendOnlyLog) -> None:
        self.root = root
        self.locked_root = root / "locked"
        self.index = AppendOnlyLog(root / "index.jsonl")
        self.attestations = AppendOnlyLog(root / "attestations.jsonl")
        self.audit_log = audit_log

    def _directory(self, score: BundleScore) -> Path:
        return self.locked_root / score.intended_draw_date.isoformat() / score.score_id

    def _load_directory(self, directory: Path) -> BundleScore:
        manifest = _verify_artifact_directory(directory)
        payload = (directory / "score.json").read_bytes()
        if sha256_bytes(payload) != manifest["artifact_sha256"]:
            raise IntegrityError(f"score content hash mismatch: {directory}")
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IntegrityError(f"invalid locked score JSON: {directory}") from exc
        return BundleScore.model_validate(raw["score"])

    def _load_event(self, event: Mapping[str, Any]) -> tuple[BundleScore, Path]:
        payload = _event_payload(event, event_type="bundle_scored")
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="locked",
        )
        score_id = payload.get("score_id")
        draw_date = payload.get("draw_date")
        if not isinstance(score_id, str) or not isinstance(draw_date, str):
            raise IntegrityError("score index event has invalid identity fields")
        expected = self.locked_root / draw_date / score_id
        if path != expected:
            raise IntegrityError(f"score is indexed at a noncanonical path: {path}")
        manifest = _verify_artifact_directory(path)
        expected_manifest_hash = payload.get("manifest_sha256")
        if expected_manifest_hash is None:
            expected_manifest_hash = _attestation_for(
                self.attestations,
                index_event=event,
                binding_name="manifest_sha256",
            )
        if expected_manifest_hash is None:
            raise IntegrityError(
                f"score index event {event.get('event_id')} needs a manifest attestation"
            )
        if expected_manifest_hash != manifest["manifest_sha256"]:
            raise IntegrityError(f"score manifest is not bound to its index: {path}")
        score = self._load_directory(path)
        if (
            score.score_id != score_id
            or score.bundle_id != payload.get("bundle_id")
            or score.intended_draw_date.isoformat() != draw_date
            or manifest["artifact_sha256"] != payload.get("artifact_sha256")
        ):
            raise IntegrityError(f"score index metadata does not match artifact: {path}")
        return score, path

    def _indexed_entries(self) -> list[tuple[Mapping[str, Any], BundleScore, Path]]:
        entries: list[tuple[Mapping[str, Any], BundleScore, Path]] = []
        score_ids: set[str] = set()
        bundle_ids: set[str] = set()
        paths: set[Path] = set()
        for event in self.index.read():
            score, path = self._load_event(event)
            if score.score_id in score_ids:
                raise IntegrityError(f"score_id is reused globally: {score.score_id}")
            if score.bundle_id in bundle_ids:
                raise IntegrityError(f"bundle has duplicate score index entries: {score.bundle_id}")
            if path in paths:
                raise IntegrityError(f"score artifact path is indexed more than once: {path}")
            score_ids.add(score.score_id)
            bundle_ids.add(score.bundle_id)
            paths.add(path)
            entries.append((event, score, path))
        return entries

    def list_scores(self) -> list[BundleScore]:
        self.audit_integrity()
        return [score for _, score, _ in self._indexed_entries()]

    def for_bundle(self, bundle_id: str) -> BundleScore | None:
        matches = [score for score in self.list_scores() if score.bundle_id == bundle_id]
        if len(matches) > 1:
            raise IntegrityError(f"bundle {bundle_id} has duplicate scoring artifacts")
        return matches[0] if matches else None

    def append(self, score: BundleScore) -> Path:
        indexed_entries = self._indexed_entries()
        identity = [
            (event, existing, path)
            for event, existing, path in indexed_entries
            if existing.bundle_id == score.bundle_id or existing.score_id == score.score_id
        ]
        if identity:
            if len(identity) != 1:
                raise IntegrityError(f"ambiguous existing score identity: {score.score_id}")
            event, existing, path = identity[0]
            if (
                existing.bundle_id == score.bundle_id
                and existing.intended_draw_date == score.intended_draw_date
                and existing.draw == score.draw
                and existing.lines == score.lines
            ):
                self.audit_integrity()
                event_payload = _event_payload(event, event_type="bundle_scored")
                self.audit_log.append(
                    event_id=str(event["event_id"]),
                    event_type="bundle_scored",
                    payload=event_payload,
                    timestamp_utc=datetime.fromisoformat(str(event["timestamp_utc"])),
                )
                return path
            raise AlreadyScoredError(f"bundle {score.bundle_id} already has an immutable score")
        payload = canonical_json_bytes(
            {"schema_version": SCHEMA_VERSION, "score": score.model_dump(mode="json")}
        )
        artifact_sha256 = sha256_bytes(payload)
        rows: list[dict[str, object]] = []
        for line in score.lines:
            rows.append(
                {
                    "score_id": score.score_id,
                    "bundle_id": score.bundle_id,
                    "draw_date": score.intended_draw_date.isoformat(),
                    "draw_id": score.draw.draw_id or "",
                    "strategy": line.strategy,
                    "line_id": line.line_id,
                    "n1": line.mains[0],
                    "n2": line.mains[1],
                    "n3": line.mains[2],
                    "n4": line.mains[3],
                    "n5": line.mains[4],
                    "mega": line.mega,
                    "matched_mains": " ".join(str(number) for number in line.matched_mains),
                    "main_match_count": line.main_match_count,
                    "mega_hit": "yes" if line.mega_hit else "no",
                    "official_prize_category": line.prize_category,
                }
            )
        fields = list(rows[0]) if rows else []
        csv_payload = _csv_bytes(fields, rows)
        base_files = {"score.json": payload, "tickets.csv": csv_payload}
        files = dict(base_files)
        files["manifest.json"] = _build_manifest(base_files, artifact_sha256=artifact_sha256)
        final = self._directory(score)
        same_id_paths = (
            list(self.locked_root.glob(f"*/{score.score_id}")) if self.locked_root.exists() else []
        )
        if any(path != final for path in same_id_paths):
            raise IntegrityError(f"score_id is reused outside its draw date: {score.score_id}")
        indexed_paths = {path for _, _, path in indexed_entries}
        physical_paths = _physical_artifact_directories(self.locked_root)
        missing = indexed_paths - physical_paths
        unrelated_orphans = (physical_paths - indexed_paths) - {final}
        if unrelated_orphans:
            raise IntegrityError(
                f"unrelated orphan score blocks append: {sorted(unrelated_orphans)[0]}"
            )
        if missing:
            raise IntegrityError(f"indexed score artifact is missing: {sorted(missing)[0]}")
        if final.exists():
            manifest = _verify_artifact_directory(final)
            if (
                manifest["manifest_sha256"] != sha256_bytes(files["manifest.json"])
                or (final / "score.json").read_bytes() != payload
                or (final / "tickets.csv").read_bytes() != csv_payload
            ):
                raise IntegrityError(f"orphan score does not match retry content: {final}")
        else:
            _write_artifact_directory(final, files)

        event_payload = {
            "score_id": score.score_id,
            "bundle_id": score.bundle_id,
            "draw_date": score.intended_draw_date.isoformat(),
            "artifact_path": indexed_artifact_reference(self.root, final),
            "artifact_sha256": artifact_sha256,
            "manifest_sha256": sha256_bytes(files["manifest.json"]),
        }
        event_id = f"score:{score.score_id}:{artifact_sha256}"
        self.index.append(
            event_id=event_id,
            event_type="bundle_scored",
            payload=event_payload,
            timestamp_utc=score.scored_timestamp_utc,
        )
        self.audit_log.append(
            event_id=event_id,
            event_type="bundle_scored",
            payload=event_payload,
            timestamp_utc=score.scored_timestamp_utc,
        )
        return final

    def _attest_event(
        self,
        event: Mapping[str, Any],
        *,
        timestamp_utc: datetime,
    ) -> bool:
        payload = _event_payload(event, event_type="bundle_scored")
        if "manifest_sha256" in payload:
            return False
        path = resolve_indexed_artifact(
            self.root,
            str(payload.get("artifact_path")),
            expected_parent="locked",
        )
        manifest = _verify_artifact_directory(path)
        score = self._load_directory(path)
        expected = self.locked_root / score.intended_draw_date.isoformat() / score.score_id
        if (
            path != expected
            or payload.get("score_id") != score.score_id
            or payload.get("bundle_id") != score.bundle_id
            or payload.get("draw_date") != score.intended_draw_date.isoformat()
            or payload.get("artifact_sha256") != manifest.get("artifact_sha256")
        ):
            raise IntegrityError(f"cannot attest mismatched score artifact: {path}")
        manifest_sha256 = str(manifest["manifest_sha256"])
        existing_attestation = _attestation_for(
            self.attestations,
            index_event=event,
            binding_name="manifest_sha256",
        )
        if existing_attestation is not None and existing_attestation != manifest_sha256:
            raise IntegrityError(f"score artifact changed after attestation: {path}")
        _append_attestation(
            log=self.attestations,
            audit_log=self.audit_log,
            store_kind="score",
            index_event=event,
            artifact_path=indexed_artifact_reference(self.root, path),
            binding_name="manifest_sha256",
            binding_value=manifest_sha256,
            timestamp_utc=timestamp_utc,
        )
        return existing_attestation is None

    def attest_existing(self, *, timestamp_utc: datetime | None = None) -> int:
        timestamp = (timestamp_utc or utc_now()).astimezone(UTC)
        return sum(
            self._attest_event(event, timestamp_utc=timestamp) for event in self.index.read()
        )

    def audit_integrity(self) -> int:
        entries = self._indexed_entries()
        indexed_paths = {path for _, _, path in entries}
        physical_paths = _physical_artifact_directories(self.locked_root)
        missing = indexed_paths - physical_paths
        orphaned = physical_paths - indexed_paths
        if missing:
            raise IntegrityError(f"indexed score artifact is missing: {sorted(missing)[0]}")
        if orphaned:
            raise IntegrityError(f"orphan score artifact is not indexed: {sorted(orphaned)[0]}")
        event_paths = {event["event_hash"]: path for event, _, path in entries}
        for attestation in self.attestations.read():
            payload = _event_payload(attestation, event_type="immutable_artifact_attested")
            expected_path = event_paths.get(payload.get("index_event_hash"))
            attested_path = resolve_indexed_artifact(
                self.root,
                str(payload.get("artifact_path")),
                expected_parent="locked",
            )
            if (
                payload.get("store_kind") != "score"
                or expected_path is None
                or attested_path != expected_path
            ):
                raise IntegrityError("score attestation has no matching index event")
        return len(entries)


def verify_audit_mirrors(
    audit_log: AppendOnlyLog,
    *source_logs: AppendOnlyLog,
) -> int:
    """Require every authoritative store event to be mirrored in the global audit."""

    audit_events = {event["event_id"]: event for event in audit_log.read()}
    checked = 0
    for source_log in source_logs:
        for event in source_log.read():
            mirror = audit_events.get(event["event_id"])
            if mirror is None:
                raise IntegrityError(
                    f"global audit is missing authoritative event {event['event_id']}"
                )
            if (
                mirror.get("event_type") != event.get("event_type")
                or mirror.get("timestamp_utc") != event.get("timestamp_utc")
                or mirror.get("payload") != event.get("payload")
            ):
                raise IntegrityError(f"global audit mirror differs for event {event['event_id']}")
            checked += 1
    return checked


def audit_all_stores(
    *,
    audit_log: AppendOnlyLog,
    history: HistoryStore,
    bundles: BundleStore,
    scores: ScoreStore,
) -> dict[str, int]:
    """Verify every hash chain and artifact checksum without mutating state."""

    summary = {
        "audit_events": audit_log.verify(),
        "history_index_events": history.index.verify(),
        "bundle_index_events": bundles.index.verify(),
        "score_index_events": scores.index.verify(),
        "history_attestations": history.attestations.verify(),
        "bundle_attestations": bundles.attestations.verify(),
        "score_attestations": scores.attestations.verify(),
        "history_snapshots": history.audit_integrity(),
        "locked_bundles": bundles.audit_integrity(),
        "scoring_artifacts": scores.audit_integrity(),
    }
    summary["mirrored_store_events"] = verify_audit_mirrors(
        audit_log,
        history.index,
        history.attestations,
        bundles.index,
        bundles.attestations,
        scores.index,
        scores.attestations,
    )
    bundle_by_id = {
        bundle.metadata.bundle_id: bundle for _, bundle, _ in bundles._indexed_entries()
    }
    for _, score, _ in scores._indexed_entries():
        bundle = bundle_by_id.get(score.bundle_id)
        if bundle is None:
            raise IntegrityError(
                f"score {score.score_id} references an unindexed bundle {score.bundle_id}"
            )
        if bundle.metadata.intended_draw_date != score.intended_draw_date:
            raise IntegrityError(f"score {score.score_id} has the wrong draw association")
        bundle_lines = {
            (line.strategy, line.line_id, line.mains, line.mega) for line in bundle.lines
        }
        score_lines = {(line.strategy, line.line_id, line.mains, line.mega) for line in score.lines}
        if bundle_lines != score_lines:
            raise IntegrityError(f"score {score.score_id} does not cover its locked bundle")
    return summary
