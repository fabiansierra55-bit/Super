# Backtest artifact status

- `backtest-2026-07-15-bb46bccd81f51c02` is the current cutoff-safe diagnostic.
  Its records persist each prefix hash and derive seeds without target or
  future draw content.
- `backtest-2026-07-15-cef4a11d83ff00c4` is preserved for audit history but is
  invalidated for performance evaluation. Its seed used the full history
  snapshot hash, which included target and future draws.

Neither report implies that lottery outcomes are predictable.
