# Production implementation audit — 2026-07-17

Lottery outcomes are random. This audit evaluates software integrity and
reproducibility; it does not establish predictive advantage or guarantee a
prize.

## Release outcome

- The production store audit passes with three immutable 106-draw history
  snapshots, two calibration artifacts, two locked July 18 bundle versions,
  and no scoring artifact before the intended result exists.
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

## Active v2 correction

`slp-2026-07-18-v2-647373683ab9e4b8` is the sole active bundle for the
2026-07-18 draw and explicitly names v1 as its parent. It uses model version
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
marginal contribution, and the complete recenter decision trail. Replaying the
same correction command and `slp cycle` created no additional index or audit
rows.

## Walk-forward diagnostic

The current cutoff-safe report is
`reports/backtests/backtest-2026-07-15-bb46bccd81f51c02.json`. Every seed is
derived only from its training prefix and target identity. Across three
diagnostic evaluations, the best line matched two mains in each draw; realized
bundle P(3+) and P(4+) were both 0. The mean modeled probabilities were 0.2513
and 0.0081 respectively. Three draws are far too few to estimate model skill,
and these outcomes provide no evidence of an advantage.

The earlier `cef4a11d83ff00c4` report is preserved but invalidated for model
evaluation because its seed was derived from a full-history hash containing
the target and later draws.

## Remaining operational risks

- The official public web archive currently yields only 106 draws, so the 120,
  180, and 240 windows remain unavailable until verified local history grows
  or a deeper official endpoint is independently established. No rows are
  padded or inferred.
- Backup adapters depend on external HTML schemas and intentionally halt when
  a parser or source changes.
- The active bundle has not been scored because its intended result has not
  posted. It must never be scored from an inferred or single-source result.
- Simulation confidence quantifies Monte Carlo estimation error under the
  fitted model, not uncertainty that the model describes a random future draw.
