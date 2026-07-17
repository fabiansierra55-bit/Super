# SuperLotto Plus Modeling Project

Codex-ready handoff for the SuperLotto Plus lock → score → recalibrate → generate workflow.

## Game rules

- Five main numbers from 1–47, without replacement
- One Mega number from 1–27
- Draws Wednesday and Saturday

## Required workflow

1. Fetch the latest official CA Lottery history.
2. Cross-check every draw used against an independent backup source.
3. Stop on any disagreement.
4. Score only the locked bundle whose `intended_draw_date` matches the verified result.
5. Append scoring; never overwrite a locked bundle.
6. Recalibrate, simulate candidate tickets, globally optimize the next bundle, then lock it.

## Model specification

- Window candidates: 60, 90, 120, 180; 240 only as an anchor that must clearly improve the forward objective.
- Exponential recency decay; tune half-life in draws.
- Gaussian-smoothed frequencies fitted independently for mains and Mega.
- Main sigma grid: 1.0, 1.125, 1.15, 1.3.
- Mega sigma grid: 0.9, 1.0, 1.15, 1.3.
- Held-out log-likelihood is a stability check.
- Final hyperparameter selection maximizes simulated bundle performance.
- Generate a candidate pool of 5,000–50,000 tickets.
- Default bundle objective: maximize simulated probability of at least one ticket matching three or more mains.

## Bundle constraints

- 30 lines by default.
- No duplicate main sets or full tickets.
- Pairwise mains overlap no greater than 3.
- Hamming distance at least 2.
- Any two-number pair appears at most twice across the bundle.
- Any three-number triple appears at most once.
- Use an anti-cannibalization penalty during global selection.
- Mega repetition is softly capped, with a hard safety cap.
- Adjacency allowed.
- Parity and value-band rules disabled.
- Positional recentering is mild and must not collapse ticket diversity.

## Bundle metadata

Every prediction artifact must include:

- `bundle_id`
- `generated_timestamp_utc`
- `intended_draw_date`
- `game_rules_version`
- `strategy`
- `line_id`
- `n1` through `n5`
- `mega`

## Commands

```bash
python -m slp_model.cli status
python -m slp_model.cli generate --draw-date YYYY-MM-DD
python -m slp_model.cli score --draw-date YYYY-MM-DD
pytest
```

The current code is a handoff scaffold. Codex should first implement and test the official-source adapters, backup-source verification, append-only locking, simulation engine, and optimizer before any production generation.
