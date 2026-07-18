# Architecture

The system treats external result pages, legacy CSVs, caches, and CLI arguments as untrusted. Pydantic domain models and global constraint validation are the trust boundary. Only a `VerifiedDraw` with one official and at least one approved backup evidence record can enter versioned history or scoring.

## Lifecycle

1. Source adapters fetch raw bytes with bounded retry, timeout, size, and cache policies.
2. Each parser requires its expected source schema and explicit main/Mega roles.
3. Verification normalizes and compares date, optional draw ID, all five mains, and Mega. It stops on any disagreement or premature fetch.
4. Scoring resolves the single active locked bundle for exactly that draw date and appends line-level plus aggregate results.
5. History advances only with the same verified result.
6. Calibration either performs a complete cutoff-safe hyperparameter reselection or refits the prior selected parameters to the new verified cutoff.
7. Candidate generation samples mains without replacement and Mega independently from tier-specific distributions using a recorded deterministic seed.
8. The model optimizer greedily selects maximum marginal simulated coverage under global diversity constraints and records each contribution.
9. A same-pool structural challenger builds a balanced linear packing and
   measures every possible fair main draw exactly. Evidence schema v4 applies
   the configured improvement threshold against the model candidate and a
   separate non-regression gate against the actual same-date incumbent. It
   binds stable model-conditional simulations for each choice, records their
   relative tradeoff, and requires the global coverage certificate for the
   configured bundle size. Evidence schema v4 records that certificate size;
   older evidence schemas remain available for immutable historical replay.
   While model skill is explicitly unvalidated, the configured policy favors
   fair-null robustness. The full ordered candidate pool is SHA-256 bound.
   New pools use `portable-fixed-point-splitmix64-v2`: decimal tier transforms
   are normalized to exact integer weights, unbiased bounded draws come from a
   specified SplitMix64 transition, and the digest binds the algorithm, seed,
   previous mains, weight snapshot, and ordered semantic ticket records. The
   unused floating log weight is deliberately outside the v2 digest.
10. Positional recenter proposals are bounded and screened locally. The entire
   proposal is then rejected unless original and proposed bundles are stable on
   identical production-scale holdout scenarios, the global objective does
   not decline, and exact fair 3+ coverage does not decline. A promoted fair
   packing bypasses recentering because changing a certified optimum can only
   preserve or reduce its primary fair coverage. Before any correction is
   locked, the final post-recenter candidate is also required not to regress
   the active incumbent on exact 3+, 4+, 3+Mega, 4+Mega, or jackpot coverage.
11. Adaptive simulation must reach the configured confidence half-width for consecutive batches.
12. Bundle storage atomically publishes JSON, CSV, and a manifest into a never-replaced directory, then appends hash-chained index/audit events.

## Integrity model

- Canonical JSON uses sorted keys and rejects NaN/Infinity.
- SHA-256 identifies history content and protects every locked file.
- Exclusive file/directory creation prevents replacement races.
- Append-only logs use stable event IDs, previous-event hashes, and per-event hashes.
- Bundle correction chains use `supersedes_bundle_id` and exact `lock_version + 1` transitions.
- Only one non-superseded bundle may be active for a draw.
- Scoring identity is deterministic from the locked bundle and verified draw evidence.
- Reruns compare identity/content and return the existing artifact.
- A validated crash-orphan is recovered only by appending the missing
  authoritative index/audit binding; incomplete or conflicting bytes halt.

## Calibration

Mains and Mega are fitted and selected independently. Every walk-forward fold trains on a prefix whose cutoff precedes its target. The selection rank is bundle performance; likelihood only filters unstable candidates. The 240-draw anchor cannot win without the configured improvement. Calibration artifacts preserve all candidate scores, fold cutoffs, fitted distributions, selected parameters, triggers, parent calibration, history hash, configuration hash, deterministic seed, and scored-draw cadence. Calibration identity is content-derived from the reproducible fit inputs and selected parameters. Replaying the same fit may change only lifecycle timestamps/reasons; storage returns the existing immutable artifact and does not append a duplicate calibration or audit event.

Backtest fold seeds are separately derived from a canonical prefix containing
only draw date, official draw ID, mains, and Mega. Verification timestamps,
source-response hashes, and whole-snapshot hashes are lineage metadata, not seed
inputs; excluding them prevents a later-fetched archive page from indirectly
committing a held-out result into an earlier fold's candidate bundle.

Candidate-pool v1 is frozen for backward compatibility. It used NumPy
high-level sampling and included an exact floating log weight in its digest, so
replay is supported only as a best-effort forensic check in the exact originating
numerical environment; that environment was not fully captured by v1. A missing
historical algorithm value resolves to v1. A replay mismatch is therefore
unsupported, not evidence that a locked artifact may be rewritten. All new
pools use v2. Whole-pipeline numerical portability is not claimed until
simulations and optimizer tie-breaking also move to specified integer contracts.

## Simulation objective

Grind mode keeps `P(any ticket >=3 mains)` primary. Secondary terms reward `>=4`, `3+Mega`, and `4+Mega`; an explicit spike mode is required to let secondary events dominate. Coverage is computed over shared simulated future draws, so a candidate is valued by new bundle coverage rather than standalone rank. Repetition and correlation penalties reduce cannibalization without introducing parity, band, adjacency, or other superstition rules.

## Exact fair-coverage guard

The fair null enumerates all `C(47,5) = 1,533,939` main outcomes and all 27
Mega values. A line covers 8,821 main outcomes at 3+ and 211 at 4+. For `n`
pairwise-linear lines, let `q, r = divmod(5n, 47)`. The certificate balances
main-number degrees between `q` and `q + 1`, giving
`(47-r) C(q,2) + r C(q+1,2)` shared-line intersections. The certified 3+
coverage is `8,821n` minus 36 times that intersection count; 4+ coverage is
`211n`.

The default 60-line optimizer balances 300 incidences as 29 numbers used six
times and 18 used seven times. In the max-overlap-one, pair-cap-one structural
class, its certificate covers 499,992 main outcomes at 3+ and 12,660 at 4+.
Mega reuse is allowed only between main-disjoint lines, reaching 529,260
3+Mega full outcomes and 12,660 4+Mega full outcomes. The immutable v5 baseline
retains its independent 30-line certificate: 258,582 at 3+, 6,330 at 4+, and
264,630 at 3+Mega. The 60-line result is separately optimized and is not
defined as a nested extension of that historical bundle.

These exact combinatorial values are stored and reported separately from
fitted-model simulations. Sixty distinct tickets double cost and jackpot
coverage relative to 30, but neither certificate changes per-ticket odds,
proves that historical weights predict a future draw, creates an expected-value
edge, or guarantees a prize.

## Failure semantics

Source, verification, date, identity, constraint, stability, or checksum
failures terminate the current operation. A source mismatch is appended to the
audit log and never converted to an inferred result. Artifact writes occur only
after validation; index events occur only after immutable content is published.
`cycle` refuses to back-date a missing prediction after its draw.

Scheduled automation distinguishes a failed lifecycle from an invalid audit
trail. When the lifecycle fails but the final store audit succeeds, data-only
evidence already written by completed stages may still pass CI and merge via an
artifact PR. No report or unverified result is published by that path, and the
originating workflow remains failed with an open incident.
