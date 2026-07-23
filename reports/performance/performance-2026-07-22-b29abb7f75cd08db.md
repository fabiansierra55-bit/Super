# SuperLotto Plus Performance Report

Lottery outcomes are random; these statistics do not establish predictability.

Scored bundles: 2
Draw range: 2026-07-18 through 2026-07-22
Calibration regimes: 1

## Calibration regimes

| Regime | Draws | Range | Lines scored |
|---|---:|---|---:|
| `60-line::slp-robust-fair-coverage-v5` | 2 | 2026-07-18 through 2026-07-22 | 120 |

Overall statistics below pool line outcomes for descriptive reporting only; rolling calibration is calculated within matching bundle-size/model regimes.

## Overall line statistics

- Lines: 120
- Main-match histogram (0-5): {'0': 66, '1': 46, '2': 7, '3': 1, '4': 0, '5': 0}
- Mega hits: 6 (0.0500)
- Mean main matches: 0.5250
- Population/sample standard deviation: 0.6450 / 0.6477
- Empirical P(>=2): 0.0667
- Empirical P(>=3): 0.0083
- Empirical P(>=4): 0.0000

## Tier summary

| Tier | Lines | Mean mains | Mega rate | P(>=3) | Histogram |
|---|---:|---:|---:|---:|---|
| aggressive | 40 | 0.5000 | 0.0750 | 0.0250 | {'0': 24, '1': 13, '2': 2, '3': 1, '4': 0, '5': 0} |
| balanced | 40 | 0.4750 | 0.0250 | 0.0000 | {'0': 22, '1': 17, '2': 1, '3': 0, '4': 0, '5': 0} |
| conservative | 40 | 0.6000 | 0.0500 | 0.0000 | {'0': 20, '1': 16, '2': 4, '3': 0, '4': 0, '5': 0} |

## Best-performing tickets

| Draw | Tier/line | Ticket | Matches | Mega | Category |
|---|---|---|---|---|---|
| 2026-07-22 | aggressive:10 | 5 24 30 35 42 + 22 | [5, 30, 35] | no | 3 mains |
| 2026-07-22 | conservative:4 | 11 16 17 23 29 + 15 | [16, 29] | no | No prize |
| 2026-07-22 | conservative:8 | 19 29 30 36 41 + 14 | [29, 30] | no | No prize |
| 2026-07-18 | aggressive:5 | 2 9 10 19 32 + 2 | [2, 10] | no | No prize |
| 2026-07-18 | conservative:7 | 1 5 11 27 37 + 6 | [27, 37] | no | No prize |
| 2026-07-18 | conservative:10 | 3 10 30 31 38 + 19 | [3, 10] | no | No prize |
| 2026-07-18 | balanced:11 | 7 10 20 27 29 + 16 | [10, 27] | no | No prize |
| 2026-07-18 | aggressive:15 | 3 6 21 37 47 + 22 | [3, 37] | no | No prize |
| 2026-07-22 | balanced:1 | 16 32 34 41 46 + 17 | [16] | yes | 1+Mega |
| 2026-07-22 | aggressive:20 | 5 12 19 39 45 + 17 | [5] | yes | 1+Mega |

## Predicted versus realized

- 2026-07-18 `slp-2026-07-18-v6-1b2ab3a08f1855e3` (`60-line::slp-robust-fair-coverage-v5`): predicted P(>=3)=0.3247; realized=False
- 2026-07-22 `slp-2026-07-22-v1-28dde562ef294549` (`60-line::slp-robust-fair-coverage-v5`): predicted P(>=3)=0.3285; realized=True
