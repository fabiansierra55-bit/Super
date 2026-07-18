# Legacy handoff audit

This report treats every CSV under `data/legacy/handoff-20260717` as untrusted input. The audit is read-only: no historical artifact was corrected, normalized, or overwritten.

## Outcome

Audited 16 CSV files and recorded 73 findings: 3 critical, 50 error, 20 warning, and 0 informational.

Two-source winning-result claims verified: 4. Unverified claims: 0. Corrections applied: 0.

Checks with no findings: invalid number ranges, duplicate bundle IDs, duplicate main sets, overlap-cap violations, two-source result mismatches, internal score recomputation errors.

The reconciled directory contains only the audit manifest. It contains no replacement history, prediction, or scoring rows because the audit never silently repairs untrusted data.

## Material findings

- `empty_history_artifact` — `data/legacy/handoff-20260717/history/history_schema.csv`: History artifact contains a schema header but no verified draws.
- `incomplete_history_verification_schema` — `data/legacy/handoff-20260717/history/history_schema.csv`: History schema cannot record both source fetches and status.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250911_051441Z.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250911_051441Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250911_051441Z.csv` (CSV rows 2, 4, 8, 9, 10, 14, 16, 17, 21, 22, 25, 26, 27, 29, 30): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250911_051441Z.csv` (CSV rows 10, 11, 13, 14, 17, 27, 29, 30): A three-number main combination occurs more than once.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250922_023056Z.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250922_023056Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250922_023056Z.csv` (CSV rows 3, 4, 8, 10, 11, 15, 16, 19, 20, 22, 23, 29, 30, 31): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250922_023056Z.csv` (CSV rows 3, 4, 6, 8, 20, 22, 23, 26, 29, 30): A three-number main combination occurs more than once.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250925_044532Z.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250925_044532Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250925_044532Z.csv` (CSV rows 5, 6, 8, 9, 13, 15, 20, 21, 22, 23, 24, 25, 26, 29, 31): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20250925_044532Z.csv` (CSV rows 4, 8, 9, 10, 13, 18, 19, 20, 23, 28, 31): A three-number main combination occurs more than once.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251011.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251011.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251011.csv` (CSV rows 2, 3, 5, 7, 8, 12, 16, 19, 20, 24, 26, 27, 30, 31): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251011.csv` (CSV rows 2, 3, 5, 7, 8, 12, 16, 27, 31): A three-number main combination occurs more than once.
- `generated_after_intended_draw` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251015.csv`: Prediction was generated after its claimed intended draw date.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251015.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251015.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251015.csv` (CSV rows 2, 5, 6, 8, 9, 11, 14, 15, 16, 17, 18, 19, 20, 23, 24, 26): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251015.csv` (CSV rows 2, 3, 5, 8, 9, 14, 17, 19, 20, 21, 23, 25): A three-number main combination occurs more than once.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251018.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251018.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251018.csv` (CSV rows 2, 4, 9, 11, 17, 19, 20, 21, 22, 25, 30, 31): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20251018.csv` (CSV rows 5, 11, 15, 17, 21, 22, 25, 31): A three-number main combination occurs more than once.
- `likely_recenter_collapse` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260125_000000Z.csv` (CSV rows 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31): Ticket concentration crosses the audit's collapse heuristic; this is a warning, not proof of recentering provenance.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260125_000000Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `multiple_bundles_for_intended_draw` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260125_000000Z.csv`: More than one supplied bundle targets the same draw.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260125_000000Z.csv` (CSV rows 2, 3, 4, 5, 6, 7, 8, 10, 11, 14, 15, 16, 18, 19, 20, 21, 23, 24, 25, 29, 30, 31): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260125_000000Z.csv` (CSV rows 2, 3, 6, 8, 10, 13, 15, 16, 18, 19, 20, 21, 23, 24, 29, 30): A three-number main combination occurs more than once.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260126_044959Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260129_000000Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_20260201_000000Z.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `missing_prediction_identity_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv`: Prediction cannot be treated as a production locked bundle.
- `missing_prediction_reproducibility_metadata` — `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv`: Prediction lacks data required to reproduce its model fit, simulation, and optimizer decision.
- `pair_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv` (CSV rows 6, 8, 14, 19, 20, 21, 22, 23, 24, 25, 28): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv` (CSV rows 4, 8, 9, 10, 11, 17, 18, 19, 22, 27, 30): A three-number main combination occurs more than once.
- `untracked_corrected_variant` — `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv`: Filename claims a correction, but no parent hash, reason, version, or correction manifest is present.
- `aggregate_nonwinning_category` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv` (CSV rows 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 25, 26, 27, 29, 30, 31): Legacy category combines 0, 1, and 2-main nonwinning outcomes instead of recording the exact category.
- `missing_exact_matched_mains` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv`: Scoring rows do not state the exact matched main numbers.
- `missing_scoring_identity_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv`: Scoring artifact is not permanently tied to a draw and bundle.
- `missing_scoring_source_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv`: Original scoring artifact has no auditable two-source record.
- `pair_cap_violation` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv` (CSV rows 4, 22, 23, 29): A two-number main pair occurs in more than two tickets.
- `partial_scoring_bundle_association` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv` (CSV rows 14, 15, 16, 17, 18, 19, 20): Scoring rows only partially match the nearest prediction bundle.
- `triple_cap_violation` — `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv` (CSV rows 2, 4, 6, 8, 17, 22, 23, 26, 29, 30): A three-number main combination occurs more than once.
- `aggregate_nonwinning_category` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv` (CSV rows 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 28, 29, 30, 31): Legacy category combines 0, 1, and 2-main nonwinning outcomes instead of recording the exact category.
- `missing_exact_matched_mains` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv`: Scoring rows do not state the exact matched main numbers.
- `missing_scoring_identity_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv`: Scoring artifact is not permanently tied to a draw and bundle.
- `missing_scoring_source_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv`: Original scoring artifact has no auditable two-source record.
- `orphan_scoring_artifact` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv` (CSV rows 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31): No supplied prediction bundle matches this scoring artifact.
- `pair_cap_violation` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv` (CSV rows 8, 18, 30): A two-number main pair occurs in more than two tickets.
- `triple_cap_violation` — `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv` (CSV rows 8, 11, 15, 16, 18, 27, 30): A three-number main combination occurs more than once.
- `missing_scoring_identity_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20260128.csv`: Scoring artifact is not permanently tied to a draw and bundle.
- `missing_scoring_source_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20260128.csv`: Original scoring artifact has no auditable two-source record.
- `missing_scoring_source_metadata` — `data/legacy/handoff-20260717/scoring/slp_scoring_20260204.csv`: Original scoring artifact has no auditable two-source record.

## Scoring-to-bundle associations

| Scoring artifact | Status | Nearest prediction | Matching rows |
|---|---:|---|---:|
| `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv` | partial_content_match | `data/legacy/handoff-20260717/predictions/slp_predictions_20250922_023056Z.csv` | 23/30 |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv` | no_match | `data/legacy/handoff-20260717/predictions/slp_predictions_20250911_051441Z.csv` | 0/30 |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20260128.csv` | exact_content_match | `data/legacy/handoff-20260717/predictions/slp_predictions_20260126_044959Z.csv` | 30/30 |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20260204.csv` | exact_content_match | `data/legacy/handoff-20260717/predictions/slp_predictions_20260201_000000Z.csv` | 30/30 |

## Claimed correction variants

- `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv` is closest to `data/legacy/handoff-20260717/predictions/slp_predictions_20250925_044532Z.csv`. It retains 28/30 full tickets, but only 6 retain the same strategy/line identity. Two tickets were removed and two were added. No parent hash, correction reason, or version manifest exists, so no reconciled replacement was created.

## Source verification

All four scoring dates were checked against the California Lottery official backend and an approved backup. The 2025 dates use Lottery.net; the 2026 dates use LotteryUSA. Every source pair agreed exactly. The original scoring CSVs still lack their own source URLs and fetch timestamps, which remains a provenance finding.

2026 official: <https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/3/20>

2025 official: <https://www.calottery.com/api/DrawGameApi/DrawGamePastDrawResults/8/5/20>

2025 approved backup: <https://www.lottery.net/california/superlotto-plus/numbers/2025>

Approved backup: <https://www.lotteryusa.com/california/super-lotto-plus/year>

For the two files without embedded winners, recomputation used the stored two-source observations above. The audit never infers a winning result from recorded match counts.

## Integrity inventory

| Artifact | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `data/legacy/handoff-20260717/history/history_schema.csv` | 0 | 83 | `62337210931c1c7b9202da414be94e5d10314431c36e8c5762d630258d3b6dd4` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20250911_051441Z.csv` | 30 | 1579 | `5260055ccfacc9183b58031a4ef4162a3e8c78139f1a285367b7323a85bce1cb` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20250922_023056Z.csv` | 30 | 1577 | `6dcba3770d9776b7b6ac94312bb6bae84ac660955e63c8383f9e7f62d79b9510` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20250925_044532Z.csv` | 30 | 1573 | `e042c572e4f8104226d60c745951703fdcdb579e48e0b1c9c60b48ed2085c6e8` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20251011.csv` | 30 | 1577 | `c4a9625098e807b03ced91b3d427c9fabe15606488af7bc2663dc7ab31383ced` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20251015.csv` | 30 | 1578 | `8f3dec93d546ccae9a7721762adfc6c30beb9c8890faf65bb4411c68fc22975f` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20251018.csv` | 30 | 1573 | `0634d1bd9b9338ca72ac7f7cd733868182c962626fede9e15c1c3c7303d0d8a4` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20260125_000000Z.csv` | 30 | 4741 | `fb615d7770e3a0c1db1fd7c8d9239fdf3e2ec06ddf8b15842af195634ce765fe` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20260126_044959Z.csv` | 30 | 3604 | `fc789f9d1e44580208fae1b3949b1ba1bee41b29d98d483575485beb35b24373` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20260129_000000Z.csv` | 30 | 4744 | `64de3d75d897642df2f771bab6d51f00adc929b95cbbc2fa5f9c7ee62999e4f0` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_20260201_000000Z.csv` | 30 | 4988 | `1a4570a1a45459d7443e47a1e7f39aa678cfef920f5b45061e0b8dffc2935c41` |
| `data/legacy/handoff-20260717/predictions/slp_predictions_corrected_for_20250927.csv` | 30 | 1572 | `d2cb04b586f8dc45058d7432cee86836594faaaaaa40f5f9bb93a7b03405b275` |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20250924.csv` | 30 | 1492 | `243426dc578b07816f8a66193679fca4baddfb86ad3c28d049e9b8331eeba8f0` |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20251008.csv` | 30 | 1833 | `9ce2d30bb22165bcf40e904a39e30e8cb0359babb75abd56066539ff2e50beae` |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20260128.csv` | 30 | 2173 | `7c8761500063f2fac189fcb7d3aa03c2d592d76cfd74c976722c6cfaf61b5bca` |
| `data/legacy/handoff-20260717/scoring/slp_scoring_20260204.csv` | 30 | 2667 | `5f58754ad243fe8d1f68bcd0c1d6288383d443aea08ac355f56104466c1e7e70` |

## Interpretation limits

- A content match can establish which supplied ticket rows were scored; it cannot prove that an artifact was locked before the draw.
- A date taken from a filename is reported as a filename claim, not trusted bundle metadata.
- The recenter-collapse warning is a deterministic concentration heuristic, not proof of how a bundle was generated.
- No prediction performance claim implies that lottery outcomes are predictable or guaranteed.

The complete row-level evidence and all finding identifiers are in `data/reconciled/legacy_audit_manifest.json`.
