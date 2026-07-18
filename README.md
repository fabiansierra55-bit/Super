# SuperLotto Plus production modeling system

An auditable, fail-closed implementation of the SuperLotto Plus workflow:

> **LOCK → SCORE → RECALIBRATE → GENERATE → LOCK THE NEXT BUNDLE**

Lottery draws are random. This project measures and records a modeling experiment; it does not claim that a ticket or bundle is guaranteed to win.

## Safety and game invariants

- Exactly five unique mains in `1..47`, sampled without replacement.
- One separate Mega in `1..27`.
- Wednesday and Saturday draw schedule in `America/Los_Angeles`.
- Results are ineligible before the configured 8:00 p.m. Pacific post gate.
- Every accepted result must agree exactly between the California Lottery official backend and at least one approved independent source.
- Any date, draw-ID, main-number, Mega, parser-schema, or source-role disagreement halts the operation and records an audit event.
- A score can only use the active immutable bundle whose `intended_draw_date` equals the verified result date.
- Locked history, bundles, and scores are never overwritten. Corrections create a new version that explicitly supersedes the prior bundle.

## Installation

Python 3.11 or newer is required; CI tests both Python 3.11 and 3.13 from
`requirements.lock`.

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp config.example.json config.json  # optional; validated defaults work without it
```

The installed entry point is `slp`:

```bash
slp status
slp rebuild-history
slp verify-latest
slp generate
slp score
slp cycle
slp audit
slp backtest
slp report
```

All commands print JSON. Expected integrity/source failures exit with status 2 and a fail-closed error record.

## Initial bootstrap

```bash
slp rebuild-history --minimum-draws 100
slp generate
slp audit
```

`generate` performs a live source-freshness check by default. Once the intended result posts, `slp cycle` verifies it, scores its locked bundle, appends scoring, advances verified history, recalibrates when due, generates at least 50,000 candidates, globally optimizes the next 30 lines, validates every constraint, and locks the result.

Re-running a successful operation is idempotent: it returns the existing content-addressed artifact and cannot create a duplicate history row, bundle, or score.

## Modeling and optimization

- Independent Gaussian-smoothed recency distributions for mains and Mega.
- Main sigma grid: `1.0, 1.125, 1.15, 1.3`.
- Mega sigma grid: `0.9, 1.0, 1.15, 1.3`.
- Rolling windows: `60, 90, 120, 180`; `240` is accepted only through an explicit improvement gate.
- The California Lottery web archive currently exposes a bounded rolling history (106 draws as of July 17, 2026). Bootstrap therefore defaults to 100 fully verified draws. Larger candidate windows become eligible automatically as the immutable local history grows; unavailable windows are never padded or inferred.
- Tuned draw-unit exponential half-lives.
- Cutoff-safe walk-forward complete-bundle performance is the selection target; held-out log-likelihood is only a stability gate.
- Full adaptive reselection at least every ten scored draws, with earlier drift, configuration, rule, or persistent-underperformance triggers.
- Deterministic weighted sampling of at least 50,000 unique valid candidate tickets.
- Simulation-backed greedy submodular selection using each candidate's marginal bundle contribution.
- Adaptive final simulation until Wilson confidence intervals meet the configured tolerance for consecutive batches.
- Mild positional recentering is screened locally, then accepted only when a
  separate common-scenario production-scale comparison is stable, the global
  objective does not decline, and all constraints remain valid.

Production bundles contain ten genuinely distinct Aggressive, Balanced, and Conservative lines. Aggressive candidates emphasize recent conviction and may overlap the previous official mains by at most one; Balanced lines emphasize marginal bundle coverage; Conservative lines blend toward the stable long-run distribution.

## Bundle constraints

- No duplicate full ticket or main set.
- Maximum three shared mains and minimum two main replacements between tickets.
- Any main pair appears at most twice; any triple appears at most once.
- Mega repeats receive a soft penalty and cannot exceed the hard cap.
- Reused mains, pairs, triples, correlated tickets, and excess Mega repetition incur anti-cannibalization penalties.
- Adjacency is allowed. Parity and band rules are disabled.

Every locked bundle directory includes canonical JSON, a line CSV, and a checksum manifest. Metadata captures the bundle/draw identities, timestamps, rule/model versions, full configuration snapshot and hash, seed, source evidence, history cutoff/snapshot hash, selected independent hyperparameters, candidate and simulation counts, confidence result, optimizer settings, constraints, and all marginal contributions.

## Immutable data layout

```text
data/
  audit/events.jsonl                 hash-chained source and lifecycle events
  history/versions/*.json            content-addressed verified snapshots
  calibration/locked/*.json          versioned model fits/reselections
  predictions/locked/<date>/<id>/    immutable bundle JSON/CSV/manifest
  scoring/locked/<date>/<id>/        immutable score JSON/CSV/manifest
  legacy/handoff-20260717/           untouched supplied CSV bytes
  reconciled/                         legacy audit manifest; no silent repairs
reports/                              immutable backtest/performance reports
```

Indexes are append-only JSONL hash chains. Artifact files are SHA-256 checked
before use. Exact validated crash-orphans can be recovered by appending their
missing index/audit binding; conflicting or incomplete artifacts halt and are
never overwritten.

## Source adapters

- Official: California Lottery public `DrawGamePastDrawResults` backend for game ID 8.
- Approved backups implemented: LotteryUSA and Lottery.net.
- LotteryCorner remains approved but disabled until a separately tested parser is enabled.

Adapters use bounded retries, backoff, timeouts, size limits, deterministic normalization, explicit Mega markers, strict schema checks, and a short integrity-checked cache. Cached evidence is never used beyond its TTL as an unannounced network fallback.

## Historical handoff audit

The supplied CSVs remain byte-for-byte under `data/legacy/handoff-20260717`.
Every `slp audit` invocation re-hashes the complete legacy inventory. The
deterministic audit found 73 material findings, including a post-draw
prediction filename claim, orphan/partial score associations, pervasive
metadata gaps, pair/triple violations, competing bundles, and likely recenter
collapse. It found no invalid ranges, winning-number mismatches, or score
arithmetic errors in the four independently verified scoring files. No
historical correction was applied.

See [the audit report](docs/LEGACY_AUDIT.md) and [machine-readable manifest](data/reconciled/legacy_audit_manifest.json).

The production handoff review and preserved-v1/corrected-v2 rationale are in
[the production audit](docs/PRODUCTION_AUDIT.md).

## Validation and automation

```bash
ruff format --check .
ruff check .
mypy src
pytest -q
```

GitHub Actions runs formatting, lint, type checks, and tests for pushes and pull requests. The scheduled production workflow runs on Wednesday/Saturday evenings Pacific, uses the same fail-closed CLI, opens a date-stable artifact branch and PR, uploads prediction/scoring manifests and CSVs, and creates a deduplicated failure issue plus workflow summary. It never purchases tickets or touches lottery accounts.

More detail: [architecture](docs/ARCHITECTURE.md), [operations](docs/OPERATIONS.md), and [handoff audit](docs/LEGACY_AUDIT.md).
