# machiavelli/scenario.py
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from types import MappingProxyType

PowerDict: MappingProxyType[str, str] = MappingProxyType(
    {
        "M": "Milan",
        "V": "Venice",
        "L": "Florence",
        "N": "Naples",
        "P": "Papacy",
        "F": "France",
        "T": "Turks",
        "A": "Austria",
    }
)

@dataclass
class Power:
    """Representa una potencia activa en la partida.

    Attributes:
        id (str): Identificador de una sola letra de la potencia (clave de PowerDict).
        name (str): Nombre completo de la potencia, asignado automáticamente.
        home_countries (list[str]): IDs de los países natales controlados actualmente (ej. ["M"]).
        controlled_provinces (list[str]): IDs de las provincias controladas actualmente (ej. ["rome"]).
        armies (list[str]): IDs de las provincias donde tiene ejércitos desplegados.
        fleets (list[str]): IDs de las provincias o mares donde tiene flotas desplegadas.
        garrisons (list[str]): IDs de las provincias donde tiene guarniciones defendiendo.
    """

    id: str
    name: str = field(init=False)
    home_countries: list[str] = field(default_factory=list)
    controlled_provinces: list[str] = field(default_factory=list)
    armies: list[str] = field(default_factory=list)
    fleets: list[str] = field(default_factory=list)
    garrisons: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Configuración inicial."""
        if self.id not in PowerDict:
            raise ValueError(f"El ID de potencia '{self.id}' no es válido en el PowerDict.")
        self.name = PowerDict[self.id]


@dataclass(frozen=True)
class VictoryConditions:
    """Define los requisitos necesarios para que un jugador o facción gane la partida.

    Attributes:
        cities (int): Número de ciudades controladas requeridas para la victoria.
        home_countries (int): Número de países natales controlados requeridos para la victoria.
    """

    cities: int
    home_countries: int


@dataclass(frozen=True)
class HomeCountry:
    """Define el territorio inicial y originario de una facción en el escenario.

    Attributes:
        faction_id (str): ID de la facción propietaria (ej. "M", "V" de map.PowerDict).
        province_ids (list[str]): Lista de IDs de las provincias que componen este país natal.
    """

    faction_id: str
    province_ids: list[str]


@dataclass
class Rules:
    """Contiene la configuración de mecánicas activas y parámetros de simulación del escenario.

    Attributes:
        fortress_active (bool): Indica si las fortalezas están habilitadas.
        assassinations_active (bool): Indica si la regla de asesinatos está activa.
        famine_active (bool): Indica si la regla de hambre están activa.
        plague_active (bool): Indica si la regla de plagas está activa.
    """

    fortress_active: bool = True
    assassinations_active: bool = True
    famine_active: bool = True
    plague_active: bool = True


@dataclass
class Scenario:
    """Representa un escenario histórico o temático para la partida de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo del escenario (ej. "Italia 1494").
        year (int): El año histórico en el que se ambienta el inicio del escenario.
        victory_conditions (VictoryConditions): Los requisitos de victoria en la partida.
        rules (Rules): La configuración de qué reglas opcionales están activas.
        home_countries (list[HomeCountry]): Países natales configurados para las facciones participantes.
        powers (list[Power]): Lista de potencias que juegan en este escenario.
        excluded_locations (list[str]): IDs de las localizaciones que deben eliminarse del mapa.
        variable_income_home_countries (list[str]): IDs de home countries que reciben tiradas de ingresos variables.
        variable_income_provinces (list[str]): IDs de provincias que reciben tiradas de ingresos variables.
    """

    name: str
    year: int
    victory_conditions: VictoryConditions
    rules: Rules = field(default_factory=Rules)
    home_countries: list[HomeCountry] = field(default_factory=list)
    powers: list[Power] = field(default_factory=list)
    excluded_locations: list[str] = field(default=list)
    variable_income_home_countries: list[str] = field(default_factory=list)
    variable_income_provinces: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Automatiza la asignación de territorios iniciales a las potencias del escenario."""

        hc_map = {hc.faction_id: hc for hc in self.home_countries}

        for power in self.powers:
            if power.id in hc_map:
                # Añade su home country natal
                if not power.home_countries:
                    power.home_countries.append(power.id)

                # Añadimos las provincias de su home country
                for prov_id in hc_map[power.id].province_ids:
                    if prov_id not in power.controlled_provinces:
                        power.controlled_provinces.append(prov_id)

    @classmethod
    def load_scenarios(cls) -> dict[str, Self]:
        """Lee el JSON de escenarios, y los devuelve en un diccionario."""
        json_path = Path(__file__).parent / "scenarios.json"

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        sc_dict = {}

        for sc_id, sc_data in data.items():
            # Condiciones de victoria
            vc = VictoryConditions(**sc_data["victory_conditions"])
            rules = Rules(**sc_data.get("rules", {}))
            hcs = [HomeCountry(**hc) for hc in sc_data.get("home_countries", [])]

            # Potencias en juego
            powers = []
            for p_data in sc_data.get("powers", []):
                power = Power(
                    id=p_data["id"],
                    controlled_provinces=p_data.get("controlled_provinces", []),
                    armies=p_data.get("armies", []),
                    fleets=p_data.get("fleets", []),
                    garrisons=p_data.get("garrisons", []),
                )
                powers.append(power)

            # Añade el escenario a la lista
            sc_dict[sc_id] = cls(
                name=sc_data["name"],
                year=sc_data["year"],
                victory_conditions=vc,
                rules=rules,
                home_countries=hcs,
                powers=powers,
                excluded_locations=sc_data.get("excluded_locations", []),
                variable_income_home_countries=sc_data.get("variable_income_home_countries", []),
                variable_income_provinces=sc_data.get("variable_income_provinces", []),
            )
        
        return sc_dict
