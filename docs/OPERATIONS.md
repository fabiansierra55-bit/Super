# Operations runbook

## Inspection and verification

```bash
slp status
slp show-bundle
slp odds
slp verify-latest
slp audit
```

`status` is offline. `verify-latest` fetches official and backup sources and
appends source evidence. `audit` verifies every production hash chain, locked
file, directory/index bijection, calibration binding, and the complete legacy
CSV hash inventory.

`show-bundle` prints the active immutable lines (or accepts `--bundle-id`).
`odds` derives exact fair-uniform coverage without modifying the locked
artifact and labels the separately stored model-conditional simulation.
`status`, `show-bundle`, `odds`, and `audit` are read-only; `verify-latest` is
networked and may append a source-verification audit event.

## Bootstrap

```bash
slp rebuild-history --minimum-draws 100
slp generate
slp audit
```

Do not import the legacy CSVs into production history. They are retained only as untrusted audit input.

## Normal draw operation

After the conservative 8:00 p.m. Pacific result gate:

```bash
slp cycle
```

The command is safe to retry. A successful replay cannot create a duplicate score, history row, or next bundle. `--publish` is intended only on a non-`main` artifact branch; scheduled automation commits through a pull request.

## Expected halts

- `PrematureDrawError`: wait until the Pacific gate.
- `SourceMismatchError`: do not edit numbers; inspect the audit record and sources, then retry only after publishers resolve the discrepancy.
- `InsufficientEvidenceError` or parser failure: do not substitute another unapproved result. Repair/test the adapter or wait for the source.
- `BundleNotFoundError`: no properly locked bundle exists for the result; never manufacture a historical prediction.
- `SimulationStabilityError`: increase the configured maximum simulations or relax tolerance only through reviewed configuration evidence.
- `IntegrityError`: preserve the files, stop automation, and investigate checksums/index history.

## Corrections

Never modify a directory under `data/predictions/locked` or `data/scoring/locked`. A valid prediction correction must use a new `bundle_id`, increment `lock_version` by one, and name the current active bundle in `supersedes_bundle_id`. It must still be generated before the intended draw post time. Scoring artifacts are not reissued silently; conflicting evidence requires an explicit reviewed reconciliation design.

Use the explicit parent ID and a durable reason; rerunning the same command is
idempotent. An exact replay resolves the existing direct child before model
work begins, and a repeated deterministic fit reuses its content-identified
calibration rather than appending another artifact:

```bash
CURRENT_ACTIVE_BUNDLE_ID="slp-YYYY-MM-DD-vN-..."
slp generate \
  --supersede-bundle-id "$CURRENT_ACTIVE_BUNDLE_ID" \
  --correction-reason "Specific engineering reason" \
  --force-reselection
```

## Backtest and reports

```bash
slp backtest --evaluations 3
slp report
```

Backtests refit on each historical prefix and assert that every training/fold
cutoff precedes its target. A production-policy evaluation must record both
`production_equivalent_candidate_pool: true` and
`production_selection_path_exercised: true`; smaller diagnostic pools remain
useful but are labeled as non-production-equivalent. Selection evidence stores
exact fair coverage separately from model-conditional estimates and exposes
the tradeoff when the unvalidated-model robustness policy chooses the fair
challenger. Production generation always enforces at least 50,000 candidates.
Reports are content-addressed and never overwrite a prior report.

New candidate pools use `portable-fixed-point-splitmix64-v2`. Their digest is
portable across the supported CI matrix and is invariant to generation batch
size. Historical v1 pool replay is a best-effort forensic operation: pass the
recorded algorithm version (treat a missing version as v1), expect it to work
only in the exact originating numerical environment, and never interpret a
replay mismatch on another runtime or runner as permission to alter the locked
bundle. CI verifies the preserved v1 identities and semantic artifacts without
making an invalid cross-environment replay assertion.

## GitHub automation

The scheduled workflow uses a stable `automation/slp-cycle-YYYY-MM-DD` branch.
Pacific-time triggers are configured for 8:30, 9:00, 9:30, and 10:00 p.m.
GitHub concurrency permits one running and one pending cycle, so a newer trigger
can replace an intermediate pending run when a prior cycle exceeds 30 minutes.
The immutable stores make every execution safe to retry. If the branch or PR
already exists, reruns reuse it.

Only `data/` and `reports/` paths can be published. A complete successful cycle
may stage both. If source verification or a later lifecycle stage fails but
the final `slp audit` succeeds, the workflow may preserve only the audited
`data/` evidence through explicit CI and an artifact PR. That partial evidence
publication never turns the originating run green: the run remains failed and
the single repository-wide production incident is created or updated. Reports
are not staged by this partial path. All runs upload their available evidence
and leave a workflow summary. Main remains protected; every artifact change
merges through a pull request after CI.
