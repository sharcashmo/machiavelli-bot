"""Carga de escenarios de juego desde recursos JSON."""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from machiavelli.game.resources import PackageResourceError
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

        scenarios = {}
        for scenario_id, scenario_data in data.items():
            scenarios[scenario_id] = self._to_scenario(scenario_data)
        return scenarios

    def _read_data(self) -> dict[str, object]:
        if self.json_path is None:
            resource_dir = files("machiavelli").joinpath("assets", "scenarios")
            return self._read_package_directory(resource_dir)
        if self.json_path.is_dir():
            return self._read_directory(self.json_path)
        with self.json_path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise TypeError("El recurso de escenarios debe contener un objeto JSON")
        return data

    @classmethod
    def _read_package_directory(cls, resource_dir) -> dict[str, object]:
        try:
            resources = sorted(
                (
                    resource
                    for resource in resource_dir.iterdir()
                    if resource.name.endswith(".json")
                ),
                key=lambda resource: resource.name,
            )
            return cls._read_resources(resources)
        except (OSError, UnicodeError) as exc:
            raise PackageResourceError(
                "No se pudo leer el directorio de escenarios del paquete"
            ) from exc

    @classmethod
    def _read_directory(cls, directory: Path) -> dict[str, object]:
        resources = sorted(directory.glob("*.json"))
        return cls._read_resources(resources)

    @classmethod
    def _read_resources(
        cls, resources: Iterable[Traversable | Path]
    ) -> dict[str, object]:
        scenarios = {}
        for resource in resources:
            try:
                with resource.open("r", encoding="utf-8") as stream:
                    scenario_data = json.load(stream)
            except json.JSONDecodeError as exc:
                raise PackageResourceError(
                    f"El escenario {resource.name!r} no contiene JSON válido"
                ) from exc
            cls._add_scenario(scenarios, scenario_data, resource.name)
        return scenarios

    @staticmethod
    def _add_scenario(
        scenarios: dict[str, object], scenario_data: object, filename: str
    ) -> None:
        if not isinstance(scenario_data, dict):
            raise TypeError(f"El escenario {filename!r} debe contener un objeto JSON")
        scenario_id = scenario_data.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError(
                f"El escenario {filename!r} no tiene un scenario_id válido"
            )
        if scenario_id in scenarios:
            raise ValueError(f"El scenario_id {scenario_id!r} está duplicado")
        scenarios[scenario_id] = scenario_data

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
