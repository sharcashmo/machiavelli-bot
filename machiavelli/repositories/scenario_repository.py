"""Carga de escenarios de juego desde recursos JSON."""

from __future__ import annotations

import json
from pathlib import Path

from machiavelli.game.resources import read_package_json
from machiavelli.game.scenario import (
    HomeCountry,
    Power,
    Rules,
    Scenario,
    VictoryConditions,
)


class ScenarioRepository:
    """Construye escenarios de dominio a partir del recurso JSON empaquetado."""

    def __init__(self, json_path: Path | str | None = None) -> None:
        self.json_path = Path(json_path) if json_path is not None else None

    def load_scenarios(self) -> dict[str, Scenario]:
        """Lee los escenarios configurados y los indexa por su identificador."""
        data = self._read_data()
        if not isinstance(data, dict):
            raise TypeError("El recurso de escenarios debe contener un objeto JSON")

        scenarios = {}
        for scenario_id, scenario_data in data.items():
            scenarios[scenario_id] = self._to_scenario(scenario_data)
        return scenarios

    def _read_data(self) -> object:
        if self.json_path is None:
            return read_package_json("scenarios.json")
        with self.json_path.open(encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _to_scenario(scenario_data: object) -> Scenario:
        if not isinstance(scenario_data, dict):
            raise TypeError("Cada escenario debe contener un objeto JSON")

        victory_conditions = VictoryConditions(**scenario_data["victory_conditions"])
        rules = Rules(**scenario_data.get("rules", {}))
        home_countries = {
            home_country_id: HomeCountry(provinces=provinces)
            for home_country_id, provinces in scenario_data.get(
                "home_countries", {}
            ).items()
        }
        powers = {
            power_id: Power(
                home_countries=power_data.get("home_countries", []),
                armies=power_data.get("armies", []),
                fleets=power_data.get("fleets", []),
                garrisons=power_data.get("garrisons", []),
                extra_provinces=power_data.get("extra_provinces", []),
            )
            for power_id, power_data in scenario_data.get("powers", {}).items()
        }
        return Scenario(
            name=scenario_data["name"],
            year=scenario_data["year"],
            victory_conditions=victory_conditions,
            rules=rules,
            home_countries=home_countries,
            powers=powers,
            excluded_locations=scenario_data.get("excluded_locations", []),
            variable_income_home_countries=scenario_data.get(
                "variable_income_home_countries", []
            ),
            variable_income_provinces=scenario_data.get(
                "variable_income_provinces", []
            ),
        )
