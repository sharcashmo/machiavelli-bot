from __future__ import annotations

import json
from pathlib import Path

from machiavelli.repositories.scenario_repository import ScenarioRepository


def test_load_scenarios_builds_domain_objects_from_json(tmp_path: Path) -> None:
    scenarios_path = tmp_path / "scenarios.json"
    scenarios_path.write_text(
        json.dumps(
            {
                "test": {
                    "name": "Test scenario",
                    "year": 1454,
                    "victory_conditions": {"cities": 12, "home_countries": 2},
                    "home_countries": {"M": ["milan"]},
                    "powers": {"M": {"home_countries": ["M"]}},
                }
            }
        ),
        encoding="utf-8",
    )

    scenarios = ScenarioRepository(scenarios_path).load_scenarios()

    assert scenarios["test"].name == "Test scenario"
    assert scenarios["test"].powers["M"].controlled_provinces == ["milan"]
