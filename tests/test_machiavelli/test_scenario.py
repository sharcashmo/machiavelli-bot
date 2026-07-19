# tests/test_machiavelli/test_scenario.py
from machiavelli.scenario import Power, Scenario, VictoryConditions, Rules, HomeCountry
from typing import Self
from unittest.mock import patch
import pytest


@pytest.fixture
def mock_scenario_data():
    """JSON fake para los tests"""
    return {
        "1454_base": {
            "name": "Equilibrio de Poder",
            "year": 1454,
            "victory_conditions": {
                "cities": 15,
                "home_countries": 2,
            },
            "rules": {
                "fortress_active": False,
                "assassinations_active": True,
            },
            "home_countries": [
                {
                    "faction_id": "M",
                    "province_ids": ["milan", "pavia"]
                },
                {
                    "faction_id": "V",
                    "province_ids": ["venic", "padua"]
                }
            ],
            "powers": [
                {
                    "id": "M",
                    "garrisons": ["milan"]
                },
                {
                    "id": "V",
                    "extra_provinces": ["trevi"],
                    "armies": ["padua"],
                    "fleets": ["TS"]
                }
            ],
            "excluded_locations": ["messi", "paler"],
            "variable_income_home_countries": ["M", "V"],
            "variable_income_provinces": ["rome"]
        }
    }




def test_power_creation_autogenerates_name():
    """Comprueba que al pasar solo el ID se recupera el nombre correcto del PowerDict."""
    potencia = Power(id="M")

    assert potencia.id == "M"
    assert potencia.name == "Milan"
    # Comprobamos que las listas por defecto se inicializan limpias
    assert potencia.home_countries == []
    assert potencia.controlled_provinces == []
    assert potencia.armies == []
    assert potencia.fleets == []
    assert potencia.garrisons == []


def test_power_territory_mutation():
    """Comprueba que las listas de control territorial son independientes y mutables."""
    potencia = Power(id="V")  # Venice

    # Simulamos que conquista su propio país natal y un par de provincias
    potencia.home_countries.append("V")
    potencia.controlled_provinces.extend(["venic", "padua", "tivol"])

    # Simulamos un despliegue militar inicial
    potencia.armies.append("padua")
    potencia.fleets.extend(["TS", "venic"])
    potencia.garrisons.append("padua")

    assert potencia.name == "Venice"
    assert "V" in potencia.home_countries
    assert len(potencia.controlled_provinces) == 3
    assert potencia.controlled_provinces[2] == "tivol"

    assert len(potencia.armies) == 1
    assert "padua" in potencia.armies

    assert len(potencia.fleets) == 2
    assert "TS" in potencia.fleets
    assert "venic" in potencia.fleets

    assert "padua" in potencia.garrisons


def test_power_creation_with_invalid_id_raises_error():
    """Comprueba que si se intenta crear una potencia con un ID inexistente lanza un ValueError."""
    with pytest.raises(ValueError):
        Power(id="X")  # 'X' no existe en nuestro PowerDict



def test_scenario_creation():
    """Comprueba que un escenario se instancia correctamente con sus atributos y reglas por defecto."""
    # Creamos primero las condiciones de victoria obligatorias
    condiciones = VictoryConditions(cities=15, home_countries=2)

    home_milan = HomeCountry(faction_id="M", province_ids=["milan", "pavia", "cremo"])
    home_venice = HomeCountry(faction_id="V", province_ids=["venic", "padua", "trevi"])

    # Creamos las potencias
    power_milan = Power(id="M")
    power_venice = Power(id="V", controlled_provinces=["extra"])

    # Instanciamos el escenario pasándole el objeto de condiciones
    escenario = Scenario(
        name="Italia Clásica",
        year=1494,
        victory_conditions=condiciones,
        rules=Rules(fortress_active=False),
        home_countries=[home_milan, home_venice],
        powers=[power_milan, power_venice],
        variable_income_home_countries=["M", "V"],
        variable_income_provinces=["rome"],
    )

    # Chequeos
    assert escenario.name == "Italia Clásica"
    assert escenario.year == 1494

    assert escenario.victory_conditions.cities == 15
    assert escenario.victory_conditions.home_countries == 2

    # Valores de Rules (por defecto y especificados)
    assert escenario.rules.fortress_active is False
    assert escenario.rules.assassinations_active is True
    assert escenario.rules.famine_active is True
    assert escenario.rules.plague_active is True

    # HomeCountries
    assert len(escenario.home_countries) == 2
    assert escenario.home_countries[0].faction_id == "M"
    assert "pavia" in escenario.home_countries[0].province_ids
    assert escenario.home_countries[1].faction_id == "V"

    # Chequeo de Ingresos Variables (Variable Incomes)
    assert escenario.variable_income_home_countries == ["M", "V"]
    assert escenario.variable_income_provinces == ["rome"]

    # Potencias
    milan_en_juego = escenario.powers[0]
    assert milan_en_juego.home_countries == ["M"]
    assert len(milan_en_juego.controlled_provinces) == 3
    assert "pavia" in milan_en_juego.controlled_provinces

    venecia_en_juego = escenario.powers[1]
    assert venecia_en_juego.home_countries == ["V"]
    assert "extra" in venecia_en_juego.controlled_provinces
    assert "venic" in venecia_en_juego.controlled_provinces
    assert len(venecia_en_juego.controlled_provinces) == 4


def test_victory_conditions_creation():
    """Comprueba que las condiciones de victoria se instancian con los valores correctos."""
    # Simulamos, por ejemplo, necesitar 15 ciudades y 2 países natales para ganar
    condiciones = VictoryConditions(cities=15, home_countries=2)

    assert condiciones.cities == 15
    assert condiciones.home_countries == 2


def test_rules_default_values():
    """Comprueba que por defecto todas las reglas opcionales se inicializan como activas."""
    reglas = Rules()

    assert reglas.fortress_active is True
    assert reglas.assassinations_active is True
    assert reglas.famine_active is True
    assert reglas.plague_active is True


def test_rules_custom_values():
    """Comprueba que se pueden desactivar mecánicas específicas al instanciar las reglas."""
    # Simulamos una partida más "pacífica" sin pestes ni asesinatos
    reglas = Rules(fortress_active=True, assassinations_active=False, famine_active=True, plague_active=False)

    assert reglas.fortress_active is True
    assert reglas.assassinations_active is False
    assert reglas.famine_active is True
    assert reglas.plague_active is False

def test_load_scenarios_populates_all_objects_correctly(mock_scenario_data):
    """Comprueba que la factoría lee el JSON y devuelve el diccionario con los escenarios"""
    # Parcheamos json.load en el módulo destino y el builtins.open
    with patch("machiavelli.scenario.json.load", return_value=mock_scenario_data), patch(
        "builtins.open"
    ):
        scenarios = Scenario.load_scenarios()

    # Validamos que se haya indexado con el ID correcto del JSON
    assert "1454_base" in scenarios
    scenario = scenarios["1454_base"]

    # Validamos que sea de la clase adecuada (gracias a cls())
    assert isinstance(scenario, Scenario)
    assert scenario.name == "Equilibrio de Poder"
    assert scenario.year == 1454

    # Validamos que la desempaquetación de VictoryConditions y Rules haya funcionado
    assert isinstance(scenario.victory_conditions, VictoryConditions)
    assert scenario.victory_conditions.cities == 15
    assert scenario.victory_conditions.home_countries == 2

    assert isinstance(scenario.rules, Rules)
    assert scenario.rules.fortress_active is False
    assert scenario.rules.famine_active is True

    # Validamos las listas internas (HomeCountries y Powers)
    assert len(scenario.home_countries) == 2
    assert isinstance(scenario.home_countries[0], HomeCountry)
    assert scenario.home_countries[0].faction_id == "M"

    assert len(scenario.powers) == 2
    milan = scenario.powers[0]
    assert isinstance(milan, Power)
    assert milan.id == "M"
    assert "milan" in milan.controlled_provinces
    assert milan.garrisons == ["milan"]
    assert milan.armies == []
    assert milan.fleets == []

    # Validamos las locations excluidas
    assert len(scenario.excluded_locations) == 2
    assert "messi" in scenario.excluded_locations
    assert "paler" in scenario.excluded_locations

    # Validamos los ingresos variables opcionales
    assert scenario.variable_income_home_countries == ["M", "V"]
    assert scenario.variable_income_provinces == ["rome"]