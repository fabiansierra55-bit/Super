# Backtest artifact status

- `backtest-2026-07-15-8b1a9edbd72de84d` is the current cutoff-safe,
  production-equivalent champion/challenger diagnostic. It runs 50,000
  candidates per fold and preserves predicted and realized results for both
  the selected fair bundle and displaced adaptive-model bundle.
- `backtest-2026-07-15-ed35eacc2d22067d` is a valid preserved predecessor. It
  exercises the same production selection path but does not store the paired
  counterfactual realized results.
- `backtest-2026-07-15-bb46bccd81f51c02` is a valid cutoff-safe model-only v2
  diagnostic. Its records persist each prefix hash and derive seeds without
  target or future draw content, but it does not exercise the v4/v5 selection
  policy.
- `backtest-2026-07-15-cef4a11d83ff00c4` is preserved for audit history but is
  invalidated for performance evaluation. Its seed used the full history
  snapshot hash, which included target and future draws. The original files
  remain byte-for-byte preserved and therefore still contain false embedded
  cutoff-safety labels. The authoritative machine-readable status is
  [`backtest-2026-07-15-cef4a11d83ff00c4.invalidation.json`](backtest-2026-07-15-cef4a11d83ff00c4.invalidation.json).

The invalidated report must not be used to evaluate model performance. No
report implies that lottery outcomes are predictable; three current held-out
draws are far too few to establish model skill.
