import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from slp_model.config import AppConfig


def test_production_defaults_select_sixty_line_bundle() -> None:
    config = AppConfig()

    assert config.model_version == "slp-robust-fair-coverage-v5"
    assert config.training.forward_bundle_size == 60
    assert config.bundle.size == 60
    assert (
        config.bundle.aggressive_count,
        config.bundle.balanced_count,
        config.bundle.conservative_count,
    ) == (20, 20, 20)
    assert config.fair_coverage.mega_soft_cap == 2
    assert config.fair_coverage.mega_hard_cap == 3


def test_example_config_matches_production_defaults() -> None:
    payload = json.loads(Path("config.example.json").read_text(encoding="utf-8"))

    assert AppConfig.model_validate(payload) == AppConfig()


def test_training_bundle_size_cannot_diverge_from_production() -> None:
    with pytest.raises(ValidationError, match="must match production bundle size"):
        AppConfig(training={"forward_bundle_size": 30})


def test_production_tier_quotas_must_be_equal() -> None:
    with pytest.raises(ValidationError, match="equal tier quotas"):
        AppConfig(
            bundle={
                "size": 60,
                "aggressive_count": 10,
                "balanced_count": 20,
                "conservative_count": 30,
            }
        )


def test_fair_mega_caps_are_configurable_and_capacity_checked() -> None:
    legacy = AppConfig(
        training={"forward_bundle_size": 30},
        bundle={
            "size": 30,
            "aggressive_count": 10,
            "balanced_count": 10,
            "conservative_count": 10,
        },
        fair_coverage={"mega_soft_cap": 1, "mega_hard_cap": 2},
    )
    assert legacy.fair_coverage.mega_hard_cap == 2

    with pytest.raises(ValidationError, match="cannot accommodate"):
        AppConfig(fair_coverage={"mega_soft_cap": 2, "mega_hard_cap": 2})
