# Codex Instructions

Treat this repository as an auditable lottery-modeling experiment, not as a claim that lottery outcomes are predictable.

## Non-negotiable safeguards

- Never generate from unverified or mismatched draw history.
- Verify official CA Lottery results against one backup source.
- Never silently correct, fabricate, or infer winning numbers.
- Never score a bundle unless its intended draw date exactly matches the verified draw.
- Locked prediction artifacts are immutable. Create a new version rather than modifying one.
- Use deterministic random seeds in tests and record the production seed in metadata.
- Validate all generated lines and constraints before saving.
- Keep append-only audit logs.

## First implementation tasks

1. Implement official and backup source adapters with fixtures.
2. Build a deduplicated history store keyed by draw date.
3. Add bundle lock/version management.
4. Implement correct hyperparameter forward validation.
5. Implement 50,000-ticket candidate generation.
6. Implement simulation-based greedy/submodular bundle selection.
7. Add pair/triple caps and anti-cannibalization penalties.
8. Add complete line-by-line scoring and aggregate statistics.
9. Add tests covering date identity, mismatch halts, constraints, scoring, and immutable artifacts.
