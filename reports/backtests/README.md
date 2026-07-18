# Backtest artifact status

- `backtest-2026-07-15-c300d7839e51e141` is the current schema-v4,
  cutoff-safe production-selection-path champion/challenger diagnostic for
  model `slp-robust-fair-coverage-v5`. Each fold uses the full 50,000-candidate
  minimum and a 60-line bundle with 20 Aggressive, 20 Balanced, and 20
  Conservative lines. It uses 2,048 optimizer scenarios and 10,000 metric
  draws per fold, so it is not a full production-simulation-scale run.
- Its training-prefix contract, `cutoff-draw-facts-v1`, hashes only facts that
  belong to the cutoff: draw date, draw ID, five mains, and Mega. It excludes
  source-response hashes, fetch timestamps, and all other verification
  provenance. `prefix-draw-facts-target-model-v3` derives each seed from that
  sanitized prefix digest, target identity, and model version. The history
  snapshot hash remains report provenance and is explicitly not used as seed
  input.
- On the July 8, 11, and 15 folds, both the selected fair challenger and the
  adaptive model candidate achieved a best main-match count of two on every
  draw. Neither reached 3+ or 4+. The selected bundles' fitted-model P(3+) and
  P(4+) means were `0.3301667` and `0.0086667`. Three folds do not validate
  model skill. The JSON SHA-256 is
  `c300d7839e51e141ee92831985be1ec03c75612b8051c8a77c916716c01ac741`;
  the Markdown SHA-256 is
  `c4f7a0376f868d5dc3952161a56ed1c19bc2e72f95de8f636e32104ad5bf9560`.

The preserved `bb46bccd81f51c02`, `ed35eacc2d22067d`,
`8b1a9edbd72de84d`, and `be1d0fc2d611af48` reports are all invalid for model
performance evaluation. Their prefix hashes included complete `VerifiedDraw`
verification provenance, including archive-response hashes and fetch metadata
recorded after the held-out targets. This indirectly committed their seeds to
future response content. Their original JSON and Markdown bytes remain
preserved; the authoritative collective status and replacement hashes are in
[`backtest-prefix-provenance-invalidation-20260718.json`](backtest-prefix-provenance-invalidation-20260718.json).

`backtest-2026-07-15-cef4a11d83ff00c4` remains separately preserved and
invalidated for its earlier, distinct full-history-snapshot seed leakage. Its
original invalidation history remains in
[`backtest-2026-07-15-cef4a11d83ff00c4.invalidation.json`](backtest-2026-07-15-cef4a11d83ff00c4.invalidation.json).

No invalidated report should be used to evaluate model performance. No report
implies that lottery outcomes are predictable; model skill remains
`unvalidated`, and three current held-out draws are far too few to change that
status.
