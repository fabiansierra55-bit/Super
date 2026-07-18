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
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
cp config.example.json config.json  # optional; validated defaults work without it
```

The installed entry point is `slp`:

```bash
slp status
slp show-bundle
slp odds
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
- Versioned deterministic weighted sampling of at least 50,000 unique valid
  candidate tickets. New pools use fixed-point tier weights and a specified
  SplitMix64 integer stream so pool identities replay across supported Python,
  NumPy, and operating-system combinations.
- SHA-256 binding of the algorithm version, seed, previous official mains,
  fixed-point weight snapshot, and complete ordered candidate pool.
- Simulation-backed greedy submodular selection using each candidate's marginal bundle contribution.
- An exact fair-uniform structural challenger, evaluated over all `1,533,939`
  valid main draws. Evidence schema v3 requires the configured improvement over
  the model-selected candidate, a separate non-regression check against any
  same-date incumbent, preserved 4+/jackpot coverage, and the configured
  max-overlap-one, pairwise-linear 30-line certificate: `258,582` covered 3+
  main draws and `264,630` covered 3+Mega full outcomes. The evidence also binds
  stable model-conditional estimates for both choices and records the tradeoff;
  while fitted-model skill remains unvalidated, the explicit policy favors
  exact fair-null robustness rather than an unsupported modeled advantage.
- Adaptive final simulation until Wilson confidence intervals meet the configured tolerance for consecutive batches.
- Mild positional recentering is screened locally, then accepted only when a
  separate common-scenario production-scale comparison is stable, the global
  objective does not decline, and all constraints remain valid.

Production bundles contain ten genuinely distinct Aggressive, Balanced, and Conservative lines. Aggressive candidates emphasize recent conviction and may overlap the previous official mains by at most one; Balanced lines emphasize marginal bundle coverage; Conservative lines blend toward the stable long-run distribution.

`slp odds` reports exact fair-uniform combinatorial coverage separately from
model-conditional simulation. The latter is an experimental assumption, not
an objective lottery probability. For 30 distinct full tickets, jackpot
coverage is always `30 / 41,416,353` (about 1 in 1,380,545); number selection
cannot improve that quantity. Reducing overlap waste can increase the fair
probability that at least one line reaches a lower-match threshold by reducing
correlated coverage. It does not change a line's fair odds, increase the
expected number of prizes, or establish a player or expected-value edge.

## Bundle constraints

- No duplicate full ticket or main set.
- Maximum three shared mains and minimum two main replacements between tickets.
- Any main pair appears at most twice; any triple appears at most once.
- A promoted fair-coverage bundle tightens main overlap and pair repetition to
  one, balances all 150 main incidences, and permits Mega repeats only between
  main-disjoint lines.
- Mega repeats receive a soft penalty and cannot exceed the hard cap.
- Reused mains, pairs, triples, correlated tickets, and excess Mega repetition incur anti-cannibalization penalties.
- Adjacency is allowed. Parity and band rules are disabled.

Every locked bundle directory includes canonical JSON, a line CSV, and a checksum manifest. Metadata captures the bundle/draw identities, timestamps, rule/model versions, full configuration snapshot and hash, seed, source evidence, history cutoff/snapshot hash, selected independent hyperparameters, candidate-pool digest, candidate and simulation counts, model-conditional confidence result, exact fair-uniform coverage, promotion evidence, optimizer settings, constraints, and all marginal contributions.

Candidate-pool algorithm v1 remains available only for forensic replay of
immutable v1-v5 evidence. Its NumPy sampling and float-bearing digest are
runtime-bound; absence of an algorithm field on an older bundle means v1. New
generation uses `portable-fixed-point-splitmix64-v2`. This portability claim is
limited to candidate-pool construction: model fitting, future-draw simulation,
and optimizer tie-breaking remain locked and auditable but are not represented
as bit-identical across arbitrary numerical-library builds.

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

At the 2026-07-17 release audit, the immutable correction chain contains five
July 18 bundles and four calibrations across 230 hash-chained audit events.
Versions v1 through v4 remain preserved; v4 is retained as gate-regression
evidence, and `slp-2026-07-18-v5-ca0077ce15c2753f` is the sole active bundle.
The complete rationale is in [the production audit](docs/PRODUCTION_AUDIT.md).

## Validation and automation

```bash
ruff format --check .
ruff check .
mypy src
pytest -q
```

GitHub Actions runs formatting, lint, type checks, and tests for pushes and
pull requests. Pacific-time triggers schedule the production cycle for 8:30,
9:00, 9:30, and 10:00 p.m. on Wednesday/Saturday evenings so delayed source
publication can recover idempotently. GitHub may coalesce intermediate pending
runs when an earlier run is still active. The workflow uses the same
fail-closed CLI, reuses a date-stable artifact branch and PR, and uploads the
complete data/report/audit evidence. If a later lifecycle stage fails after
`slp audit` succeeds, only the audited `data/` changes may pass CI and merge;
the workflow still fails and updates the single reusable incident issue.
Reports are published only after a complete successful cycle. It never
purchases tickets or touches lottery accounts.

More detail: [architecture](docs/ARCHITECTURE.md), [operations](docs/OPERATIONS.md), and [handoff audit](docs/LEGACY_AUDIT.md).
