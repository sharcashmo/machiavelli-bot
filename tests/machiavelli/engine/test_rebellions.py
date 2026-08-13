# test/machiavelli/engine/test_rebellions.py

import unittest
from unittest.mock import Mock, patch

from machiavelli.engine.rebellions import RebellionManager
from machiavelli.events import EventType, TurnEvent


class TestExpenseRebellionPacify(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = RebellionManager(game=self.mock_game)

        # Mock del propietario de la provincia
        self.owner_player = Mock()
        self.owner_player.player_id = "FLORENCE"
        self.owner_player.rebelled_provinces = ["pisa"]
        self.owner_player.rebelled_cities = ["flore"]

        self.mock_game.players = [self.owner_player]
        self.mock_game.map.provinces = {"flore": Mock(city="fortified")}
        self.mock_game.scenario.is_defensible_city.return_value = True
        self.mock_command = Mock()

    def test_expense_rebellion_pacify_province(self):
        """Pacifica exitosamente una provincia en rebelión."""
        self.mock_command.target = "pisa"

        self.manager._expense_rebellion_pacify(self.mock_command)

        self.assertNotIn("pisa", self.owner_player.rebelled_provinces)
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertIsInstance(event, TurnEvent)
        self.assertEqual(event.type, EventType.REBELLION_PACIFY)
        self.assertEqual(
            event.data,
            {"player": "FLORENCE", "province": "pisa", "kind": "province"},
        )

    def test_expense_rebellion_pacify_city(self):
        """Pacifica exitosamente una ciudad en rebelión."""
        self.mock_command.target = "flore"

        self.manager._expense_rebellion_pacify(self.mock_command)

        self.assertNotIn("flore", self.owner_player.rebelled_cities)
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.REBELLION_PACIFY)
        self.assertEqual(
            event.data,
            {"player": "FLORENCE", "province": "flore", "kind": "city"},
        )

    def test_expense_rebellion_pacify_non_rebelled(self):
        """Si el objetivo no tiene ninguna rebelión activa, no hace nada."""
        self.mock_command.target = "rome"

        self.manager._expense_rebellion_pacify(self.mock_command)

        self.assertEqual(self.owner_player.rebelled_provinces, ["pisa"])
        self.assertEqual(self.owner_player.rebelled_cities, ["flore"])
        self.mock_game.add_event.assert_not_called()

    def test_inactive_fortress_city_rebellion_cannot_be_pacified(self):
        self.mock_command.target = "flore"
        self.mock_game.map.provinces["flore"].city = "fortress"
        self.mock_game.scenario.is_defensible_city.return_value = False

        self.manager._expense_rebellion_pacify(self.mock_command)

        self.assertEqual(self.owner_player.rebelled_cities, ["flore"])
        self.mock_game.add_event.assert_not_called()


class TestDoRebellion(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = RebellionManager(game=self.mock_game)

        # Mock del jugador
        self.owner = Mock()
        self.owner.player_id = "FLORENCE"
        self.owner.rebelled_provinces = []
        self.owner.rebelled_cities = []
        self.owner.garrisons = []

        # Mock del escenario y mapa
        self.mock_game.scenario.rules.fortress_active = True
        self.mock_game.scenario.is_defensible_city.side_effect = lambda city: (
            city == "fortified"
            or (city == "fortress" and self.mock_game.scenario.rules.fortress_active)
        )
        self.mock_province = Mock()
        self.mock_game.map.provinces = {"pisa": self.mock_province}

    def test_do_rebellion_already_rebelled_province(self):
        """No añade nada ni emite eventos si la provincia ya está en rebelión."""
        self.owner.rebelled_provinces = ["pisa"]

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_provinces, ["pisa"])
        self.assertEqual(self.owner.rebelled_cities, [])
        self.mock_game.add_event.assert_not_called()

    def test_do_rebellion_already_rebelled_city(self):
        """No añade nada ni emite eventos si la ciudad ya está en rebelión."""
        self.owner.rebelled_cities = ["pisa"]

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_cities, ["pisa"])
        self.assertEqual(self.owner.rebelled_provinces, [])
        self.mock_game.add_event.assert_not_called()

    def test_do_rebellion_city(self):
        """Si la ciudad está fortificada y no hay guarnición, rebelión en la ciudad."""
        self.mock_province.city = "fortified"

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_cities, ["pisa"])
        self.assertEqual(self.owner.rebelled_provinces, [])

        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args[0][0]
        self.assertIsInstance(event, TurnEvent)
        self.assertEqual(event.type, EventType.REBELLION_CITY)
        self.assertEqual(event.data, {"player": "FLORENCE", "province": "pisa"})

    def test_do_rebellion_fortified_with_garrison(self):
        """Si hay guarnición defendiendo, la rebelión va a la provincia."""
        self.mock_province.city = "fortified"
        self.owner.garrisons = ["pisa"]

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_provinces, ["pisa"])
        self.assertEqual(self.owner.rebelled_cities, [])

        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.REBELLION_PROVINCE)
        self.assertEqual(event.data, {"player": "FLORENCE", "province": "pisa"})

    def test_do_rebellion_unfortified(self):
        """Si la provincia no tiene fortificación, emite REBELLION_PROVINCE."""
        self.mock_province.city = "unfortified"

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_provinces, ["pisa"])
        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.REBELLION_PROVINCE)
        self.assertEqual(event.data, {"player": "FLORENCE", "province": "pisa"})

    def test_do_rebellion_fortress_active(self):
        """Si la ciudad es de tipo 'fortress' y fortress_active=True."""
        self.mock_province.city = "fortress"
        self.mock_game.scenario.rules.fortress_active = True

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_cities, ["pisa"])
        self.assertEqual(self.owner.rebelled_provinces, [])

        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.REBELLION_CITY)
        self.assertEqual(event.data, {"player": "FLORENCE", "province": "pisa"})

    def test_do_rebellion_fortress_inactive(self):
        """Si la ciudad es 'fortress' pero fortress_active=False."""
        self.mock_province.city = "fortress"
        self.mock_game.scenario.rules.fortress_active = False

        self.manager.do_rebellion(self.owner, "pisa")

        self.assertEqual(self.owner.rebelled_provinces, ["pisa"])
        self.assertEqual(self.owner.rebelled_cities, [])

        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.REBELLION_PROVINCE)
        self.assertEqual(event.data, {"player": "FLORENCE", "province": "pisa"})


class TestExpenseRebellionNonHomeCountry(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = RebellionManager(game=self.mock_game)

        # Mock del dueño actual de la provincia (ej. Florencia)
        self.owner_player = Mock()
        self.owner_player.player_id = "FLORENCE"
        self.owner_player.controlled_locations = ["pisa", "flore"]
        self.owner_player.home_countries = ["L"]

        self.mock_game.players = [self.owner_player]

        # Mock del comando
        self.mock_command = Mock()

    def test_expense_rebellion_non_home_country(self):
        """Dispara do_rebellion si la provincia está controlada y no es país natal."""
        self.mock_command.target = "pisa"
        # La provincia pertenece al territorio natal de Milán, no de Florencia
        self.mock_game.scenario.province_home_country.return_value = "M"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_non_home_country(self.mock_command)

            mock_do_rebellion.assert_called_once_with(
                owner=self.owner_player, target="pisa"
            )

    def test_expense_rebellion_non_home_country_not_controlled(self):
        """No hace nada si la provincia no la controla ningún jugador."""
        self.mock_command.target = "rome"
        self.mock_game.scenario.province_home_country.return_value = "P"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_non_home_country(self.mock_command)

            mock_do_rebellion.assert_not_called()

    def test_expense_rebellion_non_home_country_is_home_country(self):
        """No hace nada si la provincia pertenece al país natal."""
        self.mock_command.target = "flore"
        # Pertenece al territorio natal del propio jugador
        self.mock_game.scenario.province_home_country.return_value = "L"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_non_home_country(self.mock_command)

            mock_do_rebellion.assert_not_called()

    def test_expense_rebellion_non_home_country_others_home_country(self):
        """La provincia es natal de OTRO jugador distinto al controlador."""
        another_player = Mock()
        another_player.player_id = "M"
        another_player.home_countries = ["M"]

        # Florencia controla 'pisa', pero 'pisa' es natal de Milán
        self.mock_command.target = "pisa"
        self.mock_game.players = [self.owner_player, another_player]
        self.mock_game.scenario.province_home_country.return_value = "M"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_non_home_country(self.mock_command)

            # Debe llamar a do_rebellion con Florencia (owner_player) como afectado
            mock_do_rebellion.assert_called_once_with(
                owner=self.owner_player, target="pisa"
            )


class TestExpenseRebellionHomeCountry(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = RebellionManager(game=self.mock_game)

        # Mock del dueño controlador (ej. Florencia)
        self.owner_player = Mock()
        self.owner_player.player_id = "FLORENCE"
        self.owner_player.controlled_locations = ["flore", "pisa"]
        self.owner_player.home_countries = ["L"]

        self.mock_game.players = [self.owner_player]
        self.mock_command = Mock()

    def test_expense_rebellion_home_country(self):
        """Dispara do_rebellion si la provincia está controlada y es país natal."""
        self.mock_command.target = "flore"
        # Es provincia natal de Florencia
        self.mock_game.scenario.province_home_country.return_value = "L"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_home_country(self.mock_command)

            mock_do_rebellion.assert_called_once_with(
                owner=self.owner_player, target="flore"
            )

    def test_expense_rebellion_home_country_not_controlled(self):
        """No hace nada si la provincia no la controla ningún jugador."""
        self.mock_command.target = "rome"
        self.mock_game.scenario.province_home_country.return_value = "P"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_home_country(self.mock_command)

            mock_do_rebellion.assert_not_called()

    def test_expense_rebellion_home_country_not_home_country(self):
        """No hace nada si la provincia NO pertenece al país natal del controlador."""
        self.mock_command.target = "pisa"
        # 'pisa' está controlada por Florencia, pero pertenece al país natal de Milán
        self.mock_game.scenario.province_home_country.return_value = "M"

        with patch.object(self.manager, "do_rebellion") as mock_do_rebellion:
            self.manager._expense_rebellion_home_country(self.mock_command)

            mock_do_rebellion.assert_not_called()
