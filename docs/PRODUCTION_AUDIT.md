# Production implementation audit — 2026-07-17

Lottery outcomes are random. This audit evaluates software integrity and
reproducibility; it does not establish predictive advantage or guarantee a
prize.

## Release outcome

- The production store audit passes with three immutable 106-draw history
  snapshots, four calibration artifacts, five locked July 18 bundle versions,
  230 hash-chained audit events, and no scoring artifact before the intended
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

## Active v5 evidence-v3 correction

`slp-2026-07-18-v5-ca0077ce15c2753f` is the sole active bundle. It was
generated at 02:14:16 UTC and locked at 02:14:53 UTC, before the intended draw
gate. It explicitly supersedes v4 without modifying any earlier artifact. Its
durable reason states that it restores certified fair coverage after the
incumbent-equivalence gate correction while preserving v4 as regression
evidence.

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
immutable artifact and index event; no fifth calibration was created. A replay
of the same parent/reason resolves the already locked v5 child before another
generation, and `slp cycle` returns the active bundle idempotently. After these
replay checks, the store remains at five bundles, four calibrations, zero
scores, and 230 audit events.

## Walk-forward diagnostic

The current cutoff-safe production-selection-path report is
`reports/backtests/backtest-2026-07-15-8b1a9edbd72de84d.json`. Every seed is
derived only from its training prefix and target identity. Each of the three
evaluations used a 50,000-ticket candidate pool, exercised the production
selection path, and selected the exact fair challenger under the explicit
unvalidated-model robustness policy. The target dates were July 8, 11, and 15;
their best main-match counts were 2, 3, and 2. Realized bundle P(3+) was 1/3 and
P(4+) was 0. Mean model-conditional estimates were 0.1716 and 0.0046,
respectively. Three draws are far too few to estimate model skill, and these
outcomes provide no evidence of an advantage.

The report also preserves a paired champion/challenger comparison on the same
three targets. The selected fair challenger reached 3+ mains on one draw; the
adaptive model candidate reached 3+ on none. Both reached 4+ on none. The model
candidate nevertheless carried substantially higher fitted-model P(3+)
estimates (`0.2529`, `0.2469`, and `0.2588`) than the fair challenger (`0.1739`,
`0.1651`, and `0.1757`). This paired result makes the policy tradeoff
observable, but one hit versus zero across three draws is noise-scale evidence
and cannot validate either policy or establish predictability.

The preceding `ed35eacc2d22067d` report remains valid and preserves the same
production-equivalent selection evaluations, but it predates the explicit
paired realized metrics. The earlier `bb46bccd81f51c02` report also remains a
valid cutoff-safe diagnostic, but its 6,000-ticket pools are below the
production minimum and it does not exercise the current production selection
path. Both are preserved rather than rewritten and are no longer the current
production-policy report.

The earlier `cef4a11d83ff00c4` report is preserved but invalidated for model
evaluation because its seed was derived from a full-history hash containing
the target and later draws. Its original JSON and Markdown still contain the
now-known false labels `cutoff_safe_walk_forward_backtest` and
`No future information: True`; consumers must consult the immutable
[machine-readable invalidation sidecar](../reports/backtests/backtest-2026-07-15-cef4a11d83ff00c4.invalidation.json)
rather than treating those embedded claims as valid.

## Remaining operational risks

- The official public web archive currently yields only 106 draws, so the 120,
  180, and 240 windows remain unavailable until verified local history grows
  or a deeper official endpoint is independently established. No rows are
  padded or inferred.
- Backup adapters depend on external HTML schemas and intentionally halt when
  a parser or source changes.
- The active v5 bundle has not been scored because its intended result has not
  posted. It must never be scored from an inferred or single-source result.
- Evidence-v3 promotion is a conservative choice under an unvalidated model,
  not proof that either the adaptive weights or structural bundle will predict
  random lottery outcomes. Continued cutoff-safe scoring is required before
  changing the recorded model-skill status.
- Simulation confidence quantifies Monte Carlo estimation error under the
  fitted model, not uncertainty that the model describes a random future draw.
