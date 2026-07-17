# First Codex Task

Review this repository and complete the production implementation of the SuperLotto Plus lock → score → recalibrate → generate workflow.

Start by:

1. Auditing all CSVs under `data/` and reporting schema inconsistencies or impossible historical claims.
2. Implementing official CA Lottery and backup-source adapters with caching, retries, date normalization, and mismatch halts.
3. Creating append-only history, prediction, and scoring stores.
4. Implementing a forward-validation harness for windows 60/90/120/180 and optional 240; main sigma 1.0/1.125/1.15/1.3; Mega sigma 0.9/1.0/1.15/1.3; and a draw-based half-life grid.
5. Implementing candidate-pool generation and a simulation-based greedy/submodular optimizer for a 30-line bundle.
6. Enforcing overlap, Hamming, pair, triple, Mega-repeat, and immutable-lock rules.
7. Adding comprehensive tests and a reproducible CLI.

Do not generate production picks until source verification and tests pass.
