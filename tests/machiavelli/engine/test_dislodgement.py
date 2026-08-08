# tests/machiavelli/engine/test_dislodgement.py

import unittest
from unittest.mock import Mock, patch

from machiavelli.engine.dislodgement import RetreatHandler
from machiavelli.engine.military import DislodgementDecision


class TestRetreatHandlerPreferredRetreat(unittest.TestCase):
    """Pruebas unitarias para el método _preferred_retreat de RetreatHandler."""

    def setUp(self):
        self.game = Mock()
        self.player = Mock()
        self.player.player_id = 1
        self.game.players = [self.player]

        self.mock_map = Mock()
        self.mock_map.provinces = dict()
        self.game.require_map.return_value = self.mock_map

        self.handler = RetreatHandler(self.game)

    def test_independent_garrison_returns_none(self):
        """Las guarniciones independientes (player_id es None) no se retiran."""
        outcome = Mock()
        outcome.unit.player_id = None

        result = self.handler._preferred_retreat(outcome, set())
        self.assertEqual(result, DislodgementDecision("disband", None))

    def test_invalid_unit_type_returns_none(self):
        """Las guarniciones no pueden retirarse."""
        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "G"
        outcome.unit.origin = "rome"

        self.mock_map.adjacent_locations.return_value = {"rome", "flore", "naple"}

        result = self.handler._preferred_retreat(outcome, set())
        self.assertEqual(result, DislodgementDecision("disband", None))

    def test_retreat_priority_controlled_and_home_country(self):
        """Prioridad 1: Se escoge un destino adyacente controlado y del país natal."""
        self.player.home_countries = ["P"]
        self.player.controlled_locations = ["rome"]
        self.game.scenario.home_countries_provinces.return_value = ["rome"]

        self.mock_map.adjacent_locations.return_value = {"naple", "rome"}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "flore"
        outcome.final_unit_type = "A"

        invalid_destinations = set()

        result = self.handler._preferred_retreat(outcome, invalid_destinations)

        self.assertEqual(result, DislodgementDecision("retreat", "rome"))

    def test_retreat_priority_controlled_only(self):
        """Prioridad 2: Si no hay de país natal, se escoge una controlada no natal."""
        self.player.home_countries = ["P"]
        self.player.controlled_locations = ["naple"]
        self.game.scenario.home_countries_provinces.return_value = ["rome"]

        self.mock_map.adjacent_locations.return_value = {"rome", "naple"}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "flore"
        outcome.final_unit_type = "A"

        invalid_destinations = set()

        result = self.handler._preferred_retreat(outcome, invalid_destinations)

        self.assertEqual(result, DislodgementDecision("retreat", "naple"))

    def test_retreat_priority_home_country_only(self):
        """Prioridad 3: Si no hay controladas, se escoge país natal no controlada."""
        self.player.home_countries = ["P"]
        self.player.controlled_locations = ["milan"]
        self.game.scenario.home_countries_provinces.return_value = ["rome"]

        self.mock_map.adjacent_locations.return_value = {"naple", "rome"}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "flore"
        outcome.final_unit_type = "A"

        invalid_destinations = set()

        result = self.handler._preferred_retreat(outcome, invalid_destinations)

        self.assertEqual(result, DislodgementDecision("retreat", "rome"))

    def test_retreat_priority_random_adjacent(self):
        """Prioridad 4: Si no cumple ninguna condición preferente, una adyacente."""
        self.player.home_countries = ["P"]
        self.player.controlled_locations = ["milan"]
        self.game.scenario.home_countries_provinces.return_value = ["rome"]

        self.mock_map.adjacent_locations.return_value = {"naple"}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "flore"
        outcome.final_unit_type = "A"

        invalid_destinations = set()

        result = self.handler._preferred_retreat(outcome, invalid_destinations)

        self.assertEqual(result, DislodgementDecision("retreat", "naple"))

    def test_retreat_filters_invalid_destinations(self):
        """Verifica que se descartan los destinos presentes en invalid_destinations."""
        self.player.home_countries = ["P"]
        self.player.controlled_locations = ["rome"]
        self.game.scenario.home_countries_provinces.return_value = ["venic"]

        self.mock_map.adjacent_locations.return_value = {"rome", "naple"}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "flore"
        outcome.final_unit_type = "A"

        # 'rome' es ideal pero está bloqueado/invalidado
        invalid_destinations = {"rome"}

        result = self.handler._preferred_retreat(outcome, invalid_destinations)

        self.assertEqual(result, DislodgementDecision("retreat", "naple"))

    @patch("machiavelli.engine.dislodgement.conflict_location")
    def test_retreat_transforms_to_garrison_in_fortified_city(
        self, mock_conflict_location
    ):
        """Si no hay otra opción, se retira a la ciudad fortificada."""
        mock_conflict_location.side_effect = lambda loc, utype: (
            f"G {loc}" if utype == "G" else loc.split()[0]
        )

        self.mock_map.adjacent_locations.return_value = []

        mock_province = Mock()
        mock_province.city = "fortified"
        self.mock_map.provinces = {"rome": mock_province}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "rome"
        outcome.final_unit_type = "A"

        invalid_destinations = set()

        result = self.handler._preferred_retreat(outcome, invalid_destinations)

        self.assertEqual(result, DislodgementDecision("garrison", "rome"))
        self.assertIn("G rome", invalid_destinations)

    @patch("machiavelli.engine.dislodgement.conflict_location")
    def test_fleet_transforms_to_garrison_requires_port(self, mock_conflict_location):
        """Una flota solo se convierte en guarnición si la provincia tiene puerto."""
        mock_conflict_location.side_effect = lambda loc, utype: (
            f"G {loc}" if utype == "G" else loc.split()[0]
        )

        self.mock_map.adjacent_locations.return_value = []

        mock_province = Mock()
        mock_province.city = "fortified"
        mock_province.has_port = False
        self.mock_map.provinces = {"naple": mock_province}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "F"
        outcome.unit.origin = "naple"
        outcome.final_unit_type = "F"

        result = self.handler._preferred_retreat(outcome, set())

        self.assertEqual(result, DislodgementDecision("disband", None))

    @patch("machiavelli.engine.dislodgement.conflict_location")
    def test_no_retreat_places_no_garrison_allowed(self, mock_conflict_location):
        """No hay rutas de huída y la ciudad no admite guarnición."""
        mock_conflict_location.side_effect = lambda loc, utype: (
            f"G {loc}" if utype == "G" else loc.split()[0]
        )

        self.mock_map.adjacent_locations.return_value = []

        mock_province = Mock()
        mock_province.city = "city"
        mock_province.has_port = False
        self.mock_map.provinces = {"naple": mock_province}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "naple"
        outcome.final_unit_type = "A"

        result = self.handler._preferred_retreat(outcome, set())

        self.assertEqual(result, DislodgementDecision("disband", None))

    @patch("machiavelli.engine.dislodgement.conflict_location")
    def test_no_retreat_places_no_garrison_city_full(self, mock_conflict_location):
        """No hay rutas de huída y ya hay una guarnición en la ciudad."""
        mock_conflict_location.side_effect = lambda loc, utype: (
            f"G {loc}" if utype == "G" else loc.split()[0]
        )

        self.mock_map.adjacent_locations.return_value = []

        mock_province = Mock()
        mock_province.city = "fortified"
        mock_province.has_port = False
        self.mock_map.provinces = {"naple": mock_province}

        outcome = Mock()
        outcome.unit.player_id = 1
        outcome.unit.unit_type = "A"
        outcome.unit.origin = "naple"
        outcome.final_unit_type = "A"

        result = self.handler._preferred_retreat(
            outcome,
            {
                "G naple",
            },
        )

        self.assertEqual(result, DislodgementDecision("disband", None))
