import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from machiavelli.game.scenario import (
    HomeCountry,
    Power,
    Rules,
    Scenario,
    VictoryConditions,
)


class TestScenario(unittest.TestCase):
    """Pruebas unitarias para la clase Scenario y la carga de datos."""

    def setUp(self):
        """Prepara datos de ejemplo para instanciar escenarios manualmente."""
        self.home_countries = {
            "M": HomeCountry(provinces=["milan", "pavia"]),
            "V": HomeCountry(provinces=["venic", "padua"]),
            "P": HomeCountry(provinces=["prove"]),
        }
        self.powers = {
            "M": Power(
                home_countries=["M"],
                armies=["milan"],
                extra_provinces=["genoa", "pavia"],  # 'pavia' duplicada a propósito
            ),
            "V": Power(
                home_countries=["V"],
                fleets=["venic"],
            ),
        }
        self.vc = VictoryConditions(cities=15, home_countries=2)
        self.rules = Rules(fortress_active=False)

    def test_power_instantiation_has_default_name(self):
        """Verifica que instanciar Power no lanza AttributeError al leer name."""
        p = Power(home_countries=["M"])
        self.assertEqual(p.name, "")
        self.assertEqual(p.controlled_provinces, [])

    @patch.dict(
        "machiavelli.game.scenario.GameTables.powers",
        {"M": "Milan", "V": "Venice"},
        clear=True,
    )
    def test_scenario_post_init(self):
        """Verifica asignación de nombres y deduplicación de controlled_provinces."""
        scenario = Scenario(
            name="Test Scenario",
            year=1454,
            victory_conditions=self.vc,
            rules=self.rules,
            home_countries=self.home_countries,
            powers=self.powers,
        )

        # Nombre asignado desde GameTables
        self.assertEqual(scenario.powers["M"].name, "Milan")
        self.assertEqual(scenario.powers["V"].name, "Venice")

        # Asignación y deduplicación de controlled_provinces
        # 'pavia' solo aparece una vez estando en home_country y extra_provinces
        self.assertEqual(
            scenario.powers["M"].controlled_provinces, ["milan", "pavia", "genoa"]
        )
        self.assertEqual(scenario.powers["V"].controlled_provinces, ["venic", "padua"])

    @patch.dict("machiavelli.game.scenario.GameTables.powers", {}, clear=True)
    def test_scenario_post_init_unknown_power_fallback(self):
        """Verifica el nombre generado si la potencia no existe en GameTables."""
        powers = {"FRA": Power(home_countries=[])}
        scenario = Scenario(
            name="Fallback Scenario",
            year=1500,
            victory_conditions=self.vc,
            powers=powers,
        )
        self.assertEqual(scenario.powers["FRA"].name, "Fra")

    def test_province_home_country(self):
        """Verifica resolución de país natal para IDs base y de costa."""
        scenario = Scenario(
            name="Test Scenario",
            year=1454,
            victory_conditions=self.vc,
            home_countries=self.home_countries,
            powers={},
        )

        # ID de provincia estándar
        self.assertEqual(scenario.province_home_country("milan"), "M")
        self.assertEqual(scenario.province_home_country("padua"), "V")
        self.assertIsNone(scenario.province_home_country("rome"))

        # ID con calificador de costa
        self.assertEqual(scenario.province_home_country("prove S"), "P")
        self.assertEqual(scenario.province_home_country("prove N"), "P")

    @patch.dict(
        "machiavelli.game.scenario.GameTables.powers",
        {"M": "Milan", "V": "Venice"},
        clear=True,
    )
    def test_load_scenarios_with_custom_file(self):
        """Verifica que load_scenarios parsea un archivo JSON correctamente."""
        sample_json_data = {
            "Be": {
                "name": "The balance of power",
                "year": 1454,
                "victory_conditions": {"cities": 15, "home_countries": 2},
                "rules": {"fortress_active": False},
                "home_countries": {
                    "M": ["pavia", "milan"],
                    "V": ["padua", "venic"],
                },
                "powers": {
                    "M": {
                        "home_countries": ["M"],
                        "armies": ["pavia", "milan"],
                        "extra_provinces": ["genoa"],
                    },
                    "V": {
                        "home_countries": ["V"],
                        "armies": ["padua"],
                        "fleets": ["venic"],
                    },
                },
                "excluded_locations": ["hunga"],
                "variable_income_home_countries": ["M", "V"],
                "variable_income_provinces": ["milan"],
            }
        }

        # Uso de TemporaryDirectory para evitar bloqueos de archivo en Windows
        with tempfile.TemporaryDirectory() as tmp_dir:
            json_path = Path(tmp_dir) / "test_scenarios.json"
            json_path.write_text(json.dumps(sample_json_data), encoding="utf-8")

            scenarios = Scenario.load_scenarios(json_path=json_path)

        self.assertIn("Be", scenarios)
        sc = scenarios["Be"]

        self.assertEqual(sc.name, "The balance of power")
        self.assertEqual(sc.year, 1454)
        self.assertFalse(sc.rules.fortress_active)
        self.assertTrue(sc.rules.assassinations_active)  # Valor por defecto

        self.assertIn("M", sc.home_countries)
        self.assertEqual(sc.home_countries["M"].provinces, ["pavia", "milan"])

        self.assertIn("M", sc.powers)
        self.assertEqual(sc.powers["M"].extra_provinces, ["genoa"])
        self.assertEqual(
            sc.powers["M"].controlled_provinces, ["pavia", "milan", "genoa"]
        )

    def test_home_countries_provinces(self):
        """Comprueba que devuelve las provincias de las home countries indicadas."""

        scenario = Scenario(
            name="Test Scenario",
            year=1454,
            victory_conditions=self.vc,
            rules=self.rules,
            home_countries=self.home_countries,
            powers=self.powers,
        )
        provinces = scenario.home_countries_provinces(["M", "P"])
        self.assertEqual(provinces, ["milan", "pavia", "prove"])

    def test_rules_default_to_all_mechanics_active(self):
        rules = Rules()

        self.assertTrue(rules.fortress_active)
        self.assertTrue(rules.assassinations_active)
        self.assertTrue(rules.famine_active)
        self.assertTrue(rules.first_turn_famine)
        self.assertTrue(rules.plague_active)
