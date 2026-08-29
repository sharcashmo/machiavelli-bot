# machiavelli/game/scenario.py
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from machiavelli.game.tables import GameTables

from .resources import read_package_json


@dataclass
class Power:
    """Representa una potencia activa en la partida.

    Attributes:
        name (str): Nombre completo de la potencia, asignado automáticamente.
        home_countries (list[str]): IDs de los países natales controlados.
        controlled_provinces (list[str]): IDs de las provincias controladas.
        armies (list[str]): IDs de las provincias donde tiene ejércitos.
        fleets (list[str]): IDs de las provincias o mares donde tiene flotas.
        garrisons (list[str]): IDs de las provincias donde tiene guarniciones.
        extra_provinces (list[str]): IDs de provincias adicionales controladas.
    """

    name: str = field(default="", init=False)
    home_countries: list[str] = field(default_factory=list)
    controlled_provinces: list[str] = field(default_factory=list)
    armies: list[str] = field(default_factory=list)
    fleets: list[str] = field(default_factory=list)
    garrisons: list[str] = field(default_factory=list)
    extra_provinces: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VictoryConditions:
    """Define los requisitos necesarios para que un jugador gane la partida."""

    cities: int
    home_countries: int


@dataclass(frozen=True)
class HomeCountry:
    """Define el territorio originario de un país natal en el escenario."""

    provinces: list[str]


@dataclass
class Rules:
    """Contiene la configuración de mecánicas activas del escenario."""

    fortress_active: bool = True
    assassinations_active: bool = True
    famine_active: bool = True
    first_turn_famine: bool = True
    plague_active: bool = True


@dataclass
class Scenario:
    """Representa un escenario histórico o temático para la partida de Machiavelli."""

    name: str
    year: int
    victory_conditions: VictoryConditions
    rules: Rules = field(default_factory=Rules)
    home_countries: dict[str, HomeCountry] = field(default_factory=dict)
    powers: dict[str, Power] = field(default_factory=dict)
    excluded_locations: list[str] = field(default_factory=list)
    variable_income_home_countries: list[str] = field(default_factory=list)
    variable_income_provinces: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Asigna los territorios iniciales a las potencias del escenario."""
        for p_id, power in self.powers.items():
            power.name = GameTables.powers.get(p_id, p_id.capitalize())
            power.controlled_provinces = []

            # Asignar provincias de sus países natales
            provinces = []
            for hc_id in power.home_countries:
                if hc_id in self.home_countries:
                    provinces.extend(self.home_countries[hc_id].provinces)

            # Asignar provincias adicionales si las hay
            provinces.extend(power.extra_provinces)

            # Elimina duplicados manteniendo el orden
            power.controlled_provinces = list(dict.fromkeys(provinces))

    @classmethod
    def load_scenarios(cls, json_path: Path | str | None = None) -> dict[str, Self]:
        """Lee el JSON de escenarios y los devuelve en un diccionario."""
        if json_path is None:
            data = read_package_json("scenarios.json")
        else:
            with Path(json_path).open(encoding="utf-8") as stream:
                data = json.load(stream)

        if not isinstance(data, dict):
            raise TypeError("El recurso de escenarios debe contener un objeto JSON")

        sc_dict = {}

        for sc_id, sc_data in data.items():
            vc = VictoryConditions(**sc_data["victory_conditions"])
            rules = Rules(**sc_data.get("rules", {}))

            # Parsear home_countries como dict[str, HomeCountry]
            hcs = {
                hc_id: HomeCountry(provinces=provinces)
                for hc_id, provinces in sc_data.get("home_countries", {}).items()
            }

            # Parsear powers como dict[str, Power]
            powers = {}
            for p_id, p_data in sc_data.get("powers", {}).items():
                powers[p_id] = Power(
                    home_countries=p_data.get("home_countries", []),
                    armies=p_data.get("armies", []),
                    fleets=p_data.get("fleets", []),
                    garrisons=p_data.get("garrisons", []),
                    extra_provinces=p_data.get("extra_provinces", []),
                )

            sc_dict[sc_id] = cls(
                name=sc_data["name"],
                year=sc_data["year"],
                victory_conditions=vc,
                rules=rules,
                home_countries=hcs,
                powers=powers,
                excluded_locations=sc_data.get("excluded_locations", []),
                variable_income_home_countries=sc_data.get(
                    "variable_income_home_countries", []
                ),
                variable_income_provinces=sc_data.get("variable_income_provinces", []),
            )

        return sc_dict

    def province_home_country(self, province: str) -> str | None:
        """Devuelve el ID del país natal al que pertenece una provincia.

        Soporta consulta por ID base ("prove") o por costa ("prove S").

        Args:
            province (str): ID de la provincia o costa.
        Returns:
            str | None: ID del país natal (ej. "M"), o `None` si no pertenece a ninguno.
        """
        base_province = province.split()[0]
        for hc_id, hc in self.home_countries.items():
            if base_province in hc.provinces:
                return hc_id
        return None

    def home_countries_provinces(self, home_countries: list[str]) -> list[str] | None:
        """Devuelve los ID de las provincias que pertenecen a los home countries."""
        provinces = []
        for hc in home_countries:
            if hc in self.home_countries:
                provinces.extend(self.home_countries[hc].provinces)

        return provinces
