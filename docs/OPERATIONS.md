# Operations runbook

## Read-only checks

```bash
slp status
slp verify-latest
slp audit
```

`status` is offline. `verify-latest` fetches official and backup sources and
appends source evidence. `audit` verifies every production hash chain, locked
file, directory/index bijection, calibration binding, and the complete legacy
CSV hash inventory.

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
idempotent:

```bash
slp generate \
  --supersede-bundle-id slp-YYYY-MM-DD-v1-... \
  --correction-reason "Specific engineering reason" \
  --force-reselection
```

## Backtest and reports

```bash
slp backtest --evaluations 3
slp report
```

Backtests refit on each historical prefix and assert that every training/fold cutoff precedes its target. Diagnostic pools may be smaller and are labeled as such; production generation always enforces at least 50,000 candidates. Reports are content-addressed and never overwrite a prior report.

## GitHub automation

The scheduled workflow uses a stable `automation/slp-cycle-YYYY-MM-DD` branch. If the branch or PR already exists, reruns reuse it. Only `data/` and `reports/` artifacts are staged. Failures create or update a date-deduplicated issue and always leave a workflow summary. Main remains protected; source or artifact changes merge through pull requests after CI.
