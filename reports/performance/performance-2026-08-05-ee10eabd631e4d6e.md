# SuperLotto Plus Performance Report

Lottery outcomes are random; these statistics do not establish predictability.

Scored bundles: 4
Draw range: 2026-07-18 through 2026-08-05
Calibration regimes: 1

## Calibration regimes

| Regime | Draws | Range | Lines scored |
|---|---:|---|---:|
| `60-line::slp-robust-fair-coverage-v5` | 4 | 2026-07-18 through 2026-08-05 | 240 |

Overall statistics below pool line outcomes for descriptive reporting only; rolling calibration is calculated within matching bundle-size/model regimes.

## Overall line statistics

- Lines: 240
- Main-match histogram (0-5): {'0': 136, '1': 84, '2': 18, '3': 2, '4': 0, '5': 0}
- Mega hits: 9 (0.0375)
- Mean main matches: 0.5250
- Population/sample standard deviation: 0.6704 / 0.6718
- Empirical P(>=2): 0.0833
- Empirical P(>=3): 0.0083
- Empirical P(>=4): 0.0000

## Tier summary

| Tier | Lines | Mean mains | Mega rate | P(>=3) | Histogram |
|---|---:|---:|---:|---:|---|
| aggressive | 80 | 0.4500 | 0.0375 | 0.0125 | {'0': 50, '1': 25, '2': 4, '3': 1, '4': 0, '5': 0} |
| balanced | 80 | 0.6000 | 0.0500 | 0.0125 | {'0': 41, '1': 31, '2': 7, '3': 1, '4': 0, '5': 0} |
| conservative | 80 | 0.5250 | 0.0250 | 0.0000 | {'0': 45, '1': 28, '2': 7, '3': 0, '4': 0, '5': 0} |

## Best-performing tickets

| Draw | Tier/line | Ticket | Matches | Mega | Category |
|---|---|---|---|---|---|
| 2026-08-05 | balanced:12 | 1 2 4 13 14 + 5 | [2, 4, 13] | yes | 3+Mega |
| 2026-07-22 | aggressive:10 | 5 24 30 35 42 + 22 | [5, 30, 35] | no | 3 mains |
| 2026-08-05 | balanced:13 | 9 20 21 28 32 + 24 | [9, 20] | no | No prize |
| 2026-08-05 | balanced:17 | 13 15 20 31 47 + 21 | [13, 20] | no | No prize |
| 2026-08-05 | conservative:14 | 2 9 19 23 24 + 16 | [2, 9] | no | No prize |
| 2026-08-05 | aggressive:18 | 4 9 29 31 34 + 17 | [4, 9] | no | No prize |
| 2026-07-25 | aggressive:2 | 3 4 28 30 36 + 7 | [3, 28] | no | No prize |
| 2026-07-25 | balanced:2 | 2 3 7 18 29 + 2 | [3, 29] | no | No prize |
| 2026-07-25 | conservative:3 | 6 20 27 28 43 + 4 | [27, 28] | no | No prize |
| 2026-07-25 | balanced:9 | 3 27 31 32 44 + 27 | [3, 27] | no | No prize |

## Predicted versus realized

- 2026-07-18 `slp-2026-07-18-v6-1b2ab3a08f1855e3` (`60-line::slp-robust-fair-coverage-v5`): predicted P(>=3)=0.3247; realized=False
- 2026-07-22 `slp-2026-07-22-v1-28dde562ef294549` (`60-line::slp-robust-fair-coverage-v5`): predicted P(>=3)=0.3285; realized=True
- 2026-07-25 `slp-2026-07-25-v1-765d7003bbcfc8f7` (`60-line::slp-robust-fair-coverage-v5`): predicted P(>=3)=0.3263; realized=False
- 2026-08-05 `slp-2026-08-05-v1-3ca0537bd63d905e` (`60-line::slp-robust-fair-coverage-v5`): predicted P(>=3)=0.3258; realized=True
