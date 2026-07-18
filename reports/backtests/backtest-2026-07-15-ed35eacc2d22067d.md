# Cutoff-safe SuperLotto Plus backtest

Diagnostic backtests do not imply that random lottery outcomes are predictable.

Evaluations: 3
History cutoff: 2026-07-15
No future information: True
Diagnostic candidate pool: 50000 (production minimum: 50000)
Production selection path exercised: True
Production-equivalent candidate pool: True

Realized rate any >=3 mains: 0.3333
Mean predicted P(any >=3 mains): 0.1716
Realized rate any >=4 mains: 0.0000
Mean predicted P(any >=4 mains): 0.0046

| Target | Training cutoff | Selection | Main params | Mega params | Pred P>=3 | Realized | Best |
|---|---|---|---|---|---:|---|---:|
| 2026-07-08 | 2026-07-04 | exact_fair_uniform_coverage | {'window': 60, 'sigma': 1.0, 'half_life': 20.0} | {'window': 60, 'sigma': 0.9, 'half_life': 24.0} | 0.1739 | False | 2 |
| 2026-07-11 | 2026-07-08 | exact_fair_uniform_coverage | {'window': 90, 'sigma': 1.125, 'half_life': 16.0} | {'window': 60, 'sigma': 0.9, 'half_life': 16.0} | 0.1651 | True | 3 |
| 2026-07-15 | 2026-07-11 | exact_fair_uniform_coverage | {'window': 60, 'sigma': 1.0, 'half_life': 36.0} | {'window': 60, 'sigma': 0.9, 'half_life': 36.0} | 0.1757 | False | 2 |
