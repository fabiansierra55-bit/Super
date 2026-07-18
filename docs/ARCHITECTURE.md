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
8. The optimizer greedily selects maximum marginal simulated coverage under global diversity constraints and records each contribution.
9. Positional recenter proposals are bounded and screened locally. The entire
   proposal is then rejected unless original and proposed bundles are stable on
   identical production-scale holdout scenarios and the global objective does
   not decline.
10. Adaptive simulation must reach the configured confidence half-width for consecutive batches.
11. Bundle storage atomically publishes JSON, CSV, and a manifest into a never-replaced directory, then appends hash-chained index/audit events.

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

Mains and Mega are fitted and selected independently. Every walk-forward fold trains on a prefix whose cutoff precedes its target. The selection rank is bundle performance; likelihood only filters unstable candidates. The 240-draw anchor cannot win without the configured improvement. Calibration artifacts preserve all candidate scores, fold cutoffs, fitted distributions, selected parameters, triggers, parent calibration, history hash, configuration hash, and scored-draw cadence.

## Simulation objective

Grind mode keeps `P(any ticket >=3 mains)` primary. Secondary terms reward `>=4`, `3+Mega`, and `4+Mega`; an explicit spike mode is required to let secondary events dominate. Coverage is computed over shared simulated future draws, so a candidate is valued by new bundle coverage rather than standalone rank. Repetition and correlation penalties reduce cannibalization without introducing parity, band, adjacency, or other superstition rules.

## Failure semantics

Source, verification, date, identity, constraint, stability, or checksum failures terminate the current operation. A source mismatch is appended to the audit log and never converted to an inferred result. Artifact writes occur only after validation; index events occur only after immutable content is published. `cycle` refuses to back-date a missing prediction after its draw.
