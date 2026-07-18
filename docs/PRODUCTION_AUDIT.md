# Production implementation audit — 2026-07-17

Lottery outcomes are random. This audit evaluates software integrity and
reproducibility; it does not establish predictive advantage or guarantee a
prize.

## Release outcome

- The production store audit passes with three immutable 106-draw history
  snapshots, five calibration artifacts, six locked July 18 bundle versions,
  233 hash-chained audit events, and no scoring artifact before the intended
  result exists.
- The 16 supplied legacy CSVs still match the reconciled SHA-256 inventory.
  Their separate audit records 73 findings and applies no silent correction.
- The corrected verified-history snapshot is
  `c3630deb8abbc072296eff80274c1c1bb2ec6437bbd3f91b7377c9d92943b167`.
  California Lottery, Lottery.net, and LotteryUSA agreed exactly on the latest
  eligible draw: draw 4099 on 2026-07-15, mains 2, 5, 34, 36, 37 and Mega 3.
  Its verification timestamp follows all three recorded fetch timestamps.

## Preserved v1 findings

`slp-2026-07-18-v1-6cc88594c0f70fa8` remains byte-for-byte immutable and is
explicitly superseded, not overwritten. The review found four reasons it could
not remain the active production bundle:

1. Its verification timestamp was captured before one backup fetch completed.
2. A 10,000-scenario recenter screen accepted four changes, but a shared
   150,000-scenario comparison showed that the recentered bundle reduced the
   production objective (0.193341 to 0.190907).
3. Its ambiguous `p_any_4_plus` field represented mains-only 4+ coverage rather
   than the intended 4+Mega event.
4. Its calibration selection seed was not bound to the bundle's recorded
   generation seed.

The v1 manifest and CSV are now cryptographically bound by a migration
attestation. That attestation proves the bytes observed during this handoff; it
cannot retroactively prove an operating-system wall-clock lock instant.

## Preserved v2 correction

`slp-2026-07-18-v2-647373683ab9e4b8` explicitly names v1 as its parent and
remains byte-for-byte preserved. It uses model version
`slp-adaptive-bundle-v2`, the corrected history snapshot, and calibration
`cal-2ea0176a1a652c2496aa`.

- Candidate pool: 50,000 unique valid tickets.
- Final simulations: 150,000; stable for two consecutive batches.
- Tiers: 10 Aggressive, 10 Balanced, 10 Conservative.
- Selected mains: window 90, sigma 1.0, half-life 60 draws.
- Selected Mega: window 60, sigma 1.3, half-life 24 draws.
- Estimated P(any 3+ mains): 0.241787.
- Estimated P(any 4+ mains): 0.007487.
- Estimated P(any 3+Mega): 0.010400.
- Estimated P(any 4+Mega): 0.000300.
- Recenter production gate: rejected; shared-scenario objective declined from
  0.189911 to 0.189299.

The bundle records its actual lock timestamp, source evidence, full
configuration and hash, runtime versions, history/calibration identities,
shared deterministic seed, optimizer settings, constraint settings, every
marginal contribution, and the complete recenter decision trail. At release,
replaying the same correction command and `slp cycle` created no additional
index or audit rows.

## Preserved v3 fair-coverage correction

`slp-2026-07-18-v3-b675d398a4163433` was generated at 2026-07-18 01:44:27 UTC
and locked at 01:45:04 UTC, before the intended Pacific draw gate. It explicitly
superseded v2 without modifying it and is itself now superseded without
modification. Its durable correction reason is the operator-approved v3 exact
fair-coverage optimization with a 30-line certificate for the promoted
max-overlap-one, pairwise-linear structural class.

- Model version: `slp-adaptive-fair-guarded-v3`.
- Calibration: `cal-161c17adcec2dc803f29`, full reselection on the unchanged
  verified history cutoff.
- Candidate pool: 50,000, SHA-256
  `f560992a8c2abe5376211f5102bcd7cda6116f0adc2d043eb2f5ab203b26fb72`.
- Exact fair P(any 3+ mains): `258582 / 1533939 = 0.1685738481`.
- v2 exact fair P(any 3+ mains): `247177 / 1533939 = 0.1611387415`.
- Improvement: 11,405 additional main outcomes, 0.743511 percentage points,
  or 4.6141% relative in fair P(at least one 3+ line). This is a bundle-union
  coverage change, not an increase in per-line odds, expected prize count, or
  expected value.
- Exact fair P(any 4+ mains): `6330 / 1533939 = 0.0041266309`, unchanged at
  the 30-line maximum.
- Exact fair P(any 3+Mega): `264630 / 41416353 = 0.0063895051`, the maximum
  without duplicate full tickets.
- Exact jackpot coverage: `30 / 41416353`, unchanged because every valid set
  of 30 distinct tickets has the same jackpot probability.
- Model-conditional simulation: 125,000 stable draws; P(3+) `0.167264`, P(4+)
  `0.004024`, P(3+Mega) `0.006544`.
- Stronger locked constraints: main overlap 1, pair repetition 1, triple
  repetition 1, and Mega repetition at most 2.
- Recenter: not applied because the selected bundle already holds the exact
  primary coverage certificate within its locked structural class.

The promotion evidence stores the model-optimized candidate, v2 incumbent,
exact challenger, relative improvement, and certificate result. The complete
ordered candidate pool is hash-bound. When v3 was active, replaying both the
correction command and `slp cycle` left bundle, calibration, and audit index
counts unchanged.

## Preserved v4 gate-regression evidence

`slp-2026-07-18-v4-3f8e6c663d84a997` was generated at 02:09:07 UTC and locked
at 02:09:47 UTC, before the intended draw gate. It supersedes v3 and remains an
immutable record of a promotion-gate defect; it is not the active scoring
target.

- Model version: `slp-robust-fair-coverage-v4`.
- Calibration: `cal-1fbf4901ae333f66b4fa`, a full reselection on the unchanged
  106-draw history cutoff, with mains window 60, sigma 1.15, half-life 28 and
  Mega window 60, sigma 0.9, half-life 16.
- Candidate pool: 50,000, SHA-256
  `39fc94d4a9566219cc69f79649aae6e1c59d123917c38297b9383d8f517ac07f`.
- The evidence-v2 gate compared the certified challenger with the same-date v3
  incumbent. Both covered exactly 258,582 fair 3+ main outcomes, so the gate
  measured zero relative improvement and did not promote the challenger.
- Falling back to the model path then locked only 245,015 exact fair 3+
  outcomes, or P(3+) `0.1597292982`. That is 13,567 fewer outcomes, 0.884455
  percentage points, and 5.2467% lower than v3's certified coverage.
- The fitted-model simulation favored the model-optimized candidate at P(3+)
  `0.2587867` over the challenger at `0.1697600`, a stored relative tradeoff of
  -34.4016%. The evidence recorded that comparison correctly, but the gate
  incorrectly allowed incumbent equivalence to lead to an exact-coverage
  regression.
- Mild recentering accepted three changes on the retained model path; the final
  stable 150,000-draw simulation estimated P(3+) `0.2568600`, P(4+)
  `0.0084733`, and P(3+Mega) `0.0131867`. These are conditional model estimates,
  not fair lottery probabilities or evidence that v4 should displace v3.

No v4 bytes were repaired. The correction is the separately locked v5 child,
so the defect and its measured consequence remain independently auditable.

## Preserved v5 evidence-v3 30-line baseline

`slp-2026-07-18-v5-ca0077ce15c2753f` was generated at 02:14:16 UTC and locked
at 02:14:53 UTC, before the intended draw gate. It explicitly superseded v4
without modifying any earlier artifact and is now itself superseded without
modification by the separately optimized 60-line v6 correction. It remains the
certified immutable 30-line baseline. Its durable reason states that it
restores certified fair coverage after the incumbent-equivalence gate
correction while preserving v4 as regression evidence.

The v5 candidate pool remains immutably labeled
`deterministic-tiered-weighted-sampling-v1`. CI portability review established
that v1 replay is bound to the complete originating numerical environment, not
merely a Python or NumPy version; even nominally matching Linux runners can
differ from the originating Darwin result. No v5 bytes were changed. CI checks
their immutable identities and semantic contents without treating an
unsupported replay as corruption. New generation now defaults to
`portable-fixed-point-splitmix64-v2`, whose digest excludes unused float noise
and binds fixed-point tier weights plus the prior draw. The production-sized v2
golden pool is exercised in both supported Python jobs.

- Model version: `slp-robust-fair-coverage-v4`.
- Calibration, deterministic seed, selected parameters, and ordered candidate
  pool are identical to v4: calibration `cal-1fbf4901ae333f66b4fa`, seed
  `4672558677021009273`, and the 50,000-ticket pool hash shown above.
- Evidence schema v3 evaluates the configured improvement threshold against
  the provisional model candidate, then records a separate no-regression
  comparison with the actual same-date incumbent. The certified challenger has
  exact fair P(3+) `0.1685738481`, 6.1842% above the model candidate's
  `0.1587559870` and 5.5372% above the v4 incumbent's `0.1597292982`.
- Exact fair P(4+) remains `0.0041266309`; exact P(3+Mega) remains
  `0.0063895051`; and jackpot coverage remains `30 / 41416353`. The global
  max-overlap-one certificate is present and recentering is not applied.
- The evidence binds stable model simulations to both alternatives. Under the
  fitted model, the challenger estimates P(3+) `0.1697600`, versus `0.2587867`
  for the model candidate, so the stored model-conditional change is -34.4016%.
  The selection does not conceal that cost.
- The explicit policy is
  `fair_null_robustness_over_unvalidated_model_v1`, and the recorded model-skill
  status is `unvalidated`. The system therefore chooses the exact fair-null
  certificate, not an unvalidated claim that historical weighting predicts the
  next random draw.

The v5 correction proposed the same deterministic calibration identity as v4.
Calibration locking recognized the reproducible fit and reused the original
immutable artifact and index event; no fifth calibration was created at that
stage. A replay of the same parent/reason resolves the already locked v5 child
before another generation. Before the v6 expansion, those replay checks left
the store at five bundles, four calibrations, zero scores, and 230 audit events.

## Active v6 evidence-v4 60-line correction

`slp-2026-07-18-v6-1b2ab3a08f1855e3` is the sole active bundle. It was
generated at 05:08:46 UTC and locked at 05:11:30 UTC on July 18 (10:11 p.m.
Pacific on July 17), before the intended draw gate. It explicitly supersedes
v5 without modifying it. The correction is a separately optimized 60-line
bundle, not a nested add-on to the preserved v5 tickets.

- Model version: `slp-robust-fair-coverage-v5`; optimizer algorithm:
  `exact-fair-linear-packing-lns-exchange-v5`; promotion evidence schema: v4.
- Calibration: `cal-67a8d7c028d5267c6270`, a full reselection caused by the
  model/configuration change and operator request. The shared deterministic
  seed is `607457921982413620`. Selected mains use window 90, sigma 1.0, and
  half-life 60 draws; Mega uses window 90, sigma 0.9, and half-life 45 draws.
- Candidate pool: 50,000 unique tickets generated by
  `portable-fixed-point-splitmix64-v2`, SHA-256
  `a4df89f8fff82c6e6fa7d694e972d271cce7e6c0f1cbadb60e78f44faae55fe0`.
- Final model-conditional simulation: 175,000 draws, stable for two batches,
  with maximum 95% confidence half-width `0.002193813`.
- Tiers: 20 Aggressive, 20 Balanced, and 20 Conservative. The certificate has
  maximum main overlap 1, pair and triple repetition 1, Mega hard cap 3, and
  no recentering. All 300 main incidences are balanced: 29 numbers occur six
  times and 18 occur seven times.
- Exact fair P(any 3+ mains): `499992 / 1533939 = 0.3259529877`.
- Exact fair P(any 4+ mains): `12660 / 1533939 = 0.0082532617`.
- Exact fair P(any 3+Mega): `529260 / 41416353 = 0.0127790103`.
- Exact fair P(any 4+Mega): `12660 / 41416353 = 0.0003056764`.
- Exact jackpot coverage: `60 / 41416353 = 0.0000014487`, about 1 in
  690,273. These values are bundle-union coverage, not per-ticket improvements
  or a claim of positive expected value.

The evidence-v4 certificate is explicitly bound to bundle size 60. Its exact
3+ main coverage exceeds the 60-line model candidate by 37,416 outcomes, or
2.439210 percentage points and 8.0886% relative. It exceeds the immutable
30-line v5 incumbent by 241,410 outcomes, or 15.737914 percentage points and
93.3592% relative; that comparison also reflects twice the ticket count and
cost. The selected challenger and displaced model candidate each reached
stable 175,000-draw simulations. Under the fitted model, their estimated P(3+)
was `0.3246571` and `0.4000114`, respectively, a recorded -18.8380% relative
tradeoff. Model skill remains `unvalidated`, so policy continues to prefer the
exact fair-null certificate rather than treating the larger fitted estimate as
evidence of predictability.

The bundle-bound source metadata records the complete history rebuild
verification that finished at 00:21:51 UTC: California Lottery, Lottery.net,
and LotteryUSA agreed on draw 4099 from July 15, with comparison SHA-256
`089bb0470ff8bcb0bca3b255afc212c44043b77714cdcfe5889f9cfe45588a96`.
Immediately before generation, the live freshness gate fetched all three
sources again and recorded the same result at 05:08:34 UTC under verification
id `3a12827946db7b4f6fa7b7c9a3bc9d40e6207339dd403a5993122c9931682ac0`.
The history cutoff remained July 15 with snapshot SHA-256
`c3630deb8abbc072296eff80274c1c1bb2ec6437bbd3f91b7377c9d92943b167`.

The bundle artifact SHA-256 is
`1210fb8865e5ab8033c9f290f63f895b26676a2153207e901cf0ac483ad1b99c`;
the tickets CSV is
`3261637e361bbfa37a727c40be99e2b5142d2c4a3819772ce2c2494e8ea68173`;
and the manifest is
`ddef30d0bfc360a777bd8c1c00a239cf30293bc7e60121bf1bd061cd408d6450`.
The calibration content/file hashes are
`567aa7947072ffc0388a64309ac3968819f717fcb1a1edc8e6b12e42afc1f323`
and `53776fb08be0b0bd79dc663247e1aaecfb4d526c1050d5c95054de7fa71615e8`.
At this audit point, the store contains six bundles, five calibrations, zero
scores, and 233 hash-chained audit events.

## Walk-forward diagnostic

The current cutoff-safe production-selection-path report is the schema-v4
`reports/backtests/backtest-2026-07-15-c300d7839e51e141.json`, SHA-256
`c300d7839e51e141ee92831985be1ec03c75612b8051c8a77c916716c01ac741`.
Its Markdown SHA-256 is
`c4f7a0376f868d5dc3952161a56ed1c19bc2e72f95de8f636e32104ad5bf9560`.
Each of the three evaluations used a 50,000-ticket candidate pool and a
60-line 20/20/20 tier allocation, exercised the production selection path, and
selected the exact fair challenger under the explicit unvalidated-model
robustness policy. The diagnostic uses 2,048 optimizer scenarios and 10,000
metric draws per fold, so it does not claim full production simulation scale.

The `cutoff-draw-facts-v1` prefix contract hashes only draw date, draw ID, five
mains, and Mega from rows preceding the held-out target. It excludes raw source
response hashes, fetch timestamps, and all other verification provenance.
`prefix-draw-facts-target-model-v3` derives each deterministic seed from that
sanitized prefix digest, target identity, and model version. The report retains
the verified history snapshot hash for provenance but records
`history_snapshot_used_for_seed: false`.

The target dates were July 8, 11, and 15. Both the selected fair challenger and
the paired adaptive model candidate had a best main-match count of two on all
three draws; neither reached 3+ or 4+. The selected bundles' per-fold fitted
P(3+) estimates were `0.3307`, `0.3288`, and `0.3310`, and their mean fitted
P(3+)/P(4+) estimates were `0.3301667`/`0.0086667`. These are three diagnostic
draws, not evidence that either policy predicts a random future result.

The preserved `bb46bccd81f51c02`, `ed35eacc2d22067d`,
`8b1a9edbd72de84d`, and `be1d0fc2d611af48` reports are invalid for model
performance evaluation. Their prefix hashes serialized complete `VerifiedDraw`
objects, including raw archive-response hashes and fetch provenance recorded
after the held-out targets. Because the underlying archive/year responses
contained target and later results, those seeds were indirectly committed to
future response content even though model fitting used prefix rows. The
original artifacts remain byte-for-byte preserved. Consumers must use the
[collective machine-readable invalidation sidecar](../reports/backtests/backtest-prefix-provenance-invalidation-20260718.json)
and the schema-v4 replacement rather than their embedded cutoff-safety claims.

The earlier `cef4a11d83ff00c4` report remains separately preserved and
invalidated for its distinct full-history-snapshot seed leakage. Its original
JSON and Markdown still contain the now-known false labels
`cutoff_safe_walk_forward_backtest` and `No future information: True`; its
separate immutable
[invalidation sidecar](../reports/backtests/backtest-2026-07-15-cef4a11d83ff00c4.invalidation.json)
remains part of the audit history.

## Remaining operational risks

- The official public web archive currently yields only 106 draws, so the 120,
  180, and 240 windows remain unavailable until verified local history grows
  or a deeper official endpoint is independently established. No rows are
  padded or inferred.
- Backup adapters depend on external HTML schemas and intentionally halt when
  a parser or source changes.
- The active v6 bundle has not been scored because its intended result has not
  posted. It must never be scored from an inferred or single-source result.
- Evidence-v4 promotion is a conservative choice under an unvalidated model,
  not proof that either the adaptive weights or structural bundle will predict
  random lottery outcomes. Continued cutoff-safe scoring is required before
  changing the recorded model-skill status.
- Simulation confidence quantifies Monte Carlo estimation error under the
  fitted model, not uncertainty that the model describes a random future draw.
