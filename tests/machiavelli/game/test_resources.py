from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from machiavelli.game.resources import PackageResourceError, read_package_json
from machiavelli.repositories.map_repository import MapRepository
from machiavelli.repositories.scenario_repository import ScenarioRepository


def test_default_map_loads_from_package_resource() -> None:
    game_map = MapRepository().load_map()

    assert game_map.provinces
    assert game_map.seas


def test_default_scenarios_load_from_package_resource() -> None:
    scenarios = ScenarioRepository().load_scenarios()

    assert scenarios
    assert all(scenario.name for scenario in scenarios.values())


def test_map_loads_from_explicit_path(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "provinces": [{"name": "Rome"}],
                "seas": [{"name": "Ionian Sea"}],
            }
        ),
        encoding="utf-8",
    )

    game_map = MapRepository(map_path).load_map()

    assert list(game_map.provinces) == ["rome"]
    assert list(game_map.seas) == ["IS"]


def test_scenarios_load_from_explicit_path(tmp_path: Path) -> None:
    scenarios_path = tmp_path / "scenarios"
    scenarios_path.mkdir()
    (scenarios_path / "test.json").write_text(
        json.dumps(
            {
                "scenario_id": "test",
                "name": "Test scenario",
                "year": 1454,
                "victory_conditions": {"cities": 12, "home_countries": 2},
            }
        ),
        encoding="utf-8",
    )

    scenarios = ScenarioRepository(scenarios_path).load_scenarios()

    assert scenarios["test"].name == "Test scenario"
    assert scenarios["test"].year == 1454


def test_missing_package_json_has_descriptive_error() -> None:
    with pytest.raises(PackageResourceError, match="missing-resource.json"):
        read_package_json("missing-resource.json")


def test_resources_load_after_installing_wheel(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    dist_dir = tmp_path / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(dist_dir),
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    wheel = next(dist_dir.glob("machiavelli-*.whl"))
    environment_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(environment_dir)
    executable_path = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    executable = environment_dir / executable_path

    subprocess.run(
        [str(executable), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    clean_cwd = tmp_path / "outside-repository"
    clean_cwd.mkdir()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            str(executable),
            "-c",
            (
                "from pathlib import Path; "
                "import machiavelli; "
                "from machiavelli.engine import GameEngine; "
                "from machiavelli.game import Command, Game, Player; "
                "from machiavelli.repositories.map_repository import MapRepository; "
                "from machiavelli.repositories.scenario_repository import "
                "ScenarioRepository; "
                "assert 'site-packages' in Path(machiavelli.__file__).parts; "
                "assert GameEngine and Game and Player and Command; "
                "assert MapRepository().load_map().provinces; "
                "assert ScenarioRepository().load_scenarios()"
            ),
        ],
        cwd=clean_cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
