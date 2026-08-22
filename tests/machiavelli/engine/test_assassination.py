# tests/machiavelli/engine/test_assassination.py

import unittest
from unittest.mock import Mock, call, patch

from machiavelli.engine.assassination import AssassinationResolver
from machiavelli.game.command import Command
from machiavelli.game.events import EventType, TurnEvent

from .helpers import create_mock_game, create_mock_player


class TestExecuteAssassination(unittest.TestCase):
    """Tests para el método _execute_assassination de AssassinationResolver."""

    def setUp(self):
        """Prepara el juego de pruebas para los tests sobre _excute_assassionation."""
        self.game = create_mock_game()

        self.player1 = create_mock_player("Naples", power="N", home_countries=["N"])
        self.player2 = create_mock_player("Florence", power="L", home_countries=["L"])
        self.player1.ass_counters = ["L", "V"]
        self.player2.ass_counters = ["N", "V"]

        # Preparamos la situación de player2 para ser asesinado
        self.player2.armies = ["flore", "sienn"]
        self.player2.fleets = ["pisa", "piomb"]
        self.player2.garrisons = ["lucca", "flore", "moden"]
        self.game.besieges = ["lucca"]

        self.player2.commands = [
            Command(self.game, self.player2, "A flore", "A", "romag"),
            Command(self.game, self.player2, "A sienn", "A", "perug"),
            Command(self.game, self.player2, "F pisa", "A", "EGOL"),
            Command(self.game, self.player2, "F piomb", "C", None),
            Command(self.game, self.player2, "G lucca", "S", "lucca"),
            Command(self.game, self.player2, "G flore", "H"),
            Command(self.game, self.player2, "E A", "3", "piomb"),
        ]

        self.player2.controlled_locations = [
            "arezz",
            "flore",
            "lucca",
            "moden",
            "piomb",
            "pisa",
            "pisto",
            "romag",
            "sienn",
        ]

        self.game.players = [self.player1, self.player2]

        self.mock_rng = Mock()

        self.resolver = AssassinationResolver(self.game, self.mock_rng)

        # Preparo los home countries por provincia
        home_countries = {
            "flore": "L",
            "sienn": None,
            "pisto": "L",
            "pisa": "L",
            "arezz": "L",
            "piomb": None,
            "lucca": None,
            "moden": None,
            "romag": "P",
        }
        self.game.scenario.province_home_country.side_effect = lambda prov: (
            home_countries.get(prov, None)
        )

    @patch("machiavelli.engine.assassination.RebellionManager")
    def test_do_execute_assassionation_success(self, mock_rebellion_manager_class):
        """Ejecuta un asesinato con éxito."""

        mock_manager_instance = mock_rebellion_manager_class.return_value

        self.mock_rng.randint.side_effect = [
            2,  # arezz, home country, no army, rebellion
            2,  # flore, home country, army, no rebellion
            4,  # lucca, no home country, sieged garrison (lost), rebellion
            4,  # moden, no home country, garrison counts as army, no rebellion
            3,  # piomb, no home country, fleet, rebellion
            1,  # pisa, home country, fleet, rebellion
            5,  # pisto, home country, no army, no rebellion
            4,  # romag, no home country, no army, rebellion
            4,  # sienn, no home country, army, no rebellion
        ]
        self.resolver._execute_assassination(self.player1, self.player2)

        # Comprueba las llamadas a RebellionManager
        mock_rebellion_manager_class.assert_called_once_with(self.game)
        expected_calls = [
            call(self.player2, "arezz"),
            call(self.player2, "lucca"),
            call(self.player2, "piomb"),
            call(self.player2, "pisa"),
            call(self.player2, "romag"),
        ]
        mock_manager_instance.do_rebellion.assert_has_calls(
            expected_calls, any_order=False
        )
        self.assertEqual(
            mock_manager_instance.do_rebellion.call_count, len(expected_calls)
        )

        # Comprueba que se han eliminado todas las órdenes menos el gasto
        self.assertEqual(
            self.player2.commands,
            [Command(self.game, self.player2, "E A", "3", "piomb")],
        )

        # Comprueba que la guarnición asediada se ha eliminado
        self.assertNotIn("lucca", self.player2.garrisons)

        # Comprueba el evento
        self.game.add_event.assert_called_once_with(
            TurnEvent(
                EventType.ASSASSINATION_ATTEMPT,
                data={
                    "assassin": self.player1.player_id,
                    "target": self.player2.player_id,
                    "result": "success",
                    "lost_garrisons": ["lucca"],
                    "rebellions": [
                        "arezz",
                        "lucca",
                        "piomb",
                        "pisa",
                        "romag",
                    ],
                },
            )
        )


class TestDoAssassinationAttempt(unittest.TestCase):
    """Tests para el método _do_assassination_attempt de AssassinationResolver."""

    def setUp(self):
        """Prepara juego de pruebas para los tests sobre _do_assassination_attempt."""
        self.game = create_mock_game()

        self.player1 = create_mock_player("Naples", power="N", home_countries=["N"])
        self.player2 = create_mock_player("Florence", power="L", home_countries=["L"])
        self.player1.ass_counters = ["L", "V"]
        self.player2.ass_counters = ["N", "V"]

        self.game.players = [self.player1, self.player2]

        self.game.get_player.return_value = self.player2

        self.mock_rng = Mock()

        self.resolver = AssassinationResolver(self.game, self.mock_rng)

    def test_do_assassination_attempt(self):
        """Realiza un intento de asesinato con éxito."""

        self.mock_rng.randint.return_value = 2

        # Comprueba que se llama a _execute_assassination
        with patch.object(
            AssassinationResolver, "_execute_assassination"
        ) as mock_execute:
            self.resolver._do_assassination_attempt(
                self.player1, Command(self.game, self.player1, "E E", "24", "L")
            )
            mock_execute.assert_called_once_with(
                assassin=self.player1, target=self.player2
            )

        # Comprueba que se ha gastado la ficha de asesinato
        self.assertNotIn("L", self.player1.ass_counters)

    def test_do_assassination_attempt_failed(self):
        """Realiza un intento de asesinato, pero falla."""

        self.mock_rng.randint.return_value = 2

        self.resolver._do_assassination_attempt(
            self.player1, Command(self.game, self.player1, "E E", "12", "L")
        )

        # Comprueba que se ha gastado la ficha de asesinato
        self.assertNotIn("L", self.player1.ass_counters)

        # Comprueba el evento
        self.game.add_event.assert_called_once_with(
            TurnEvent(
                EventType.ASSASSINATION_ATTEMPT,
                data={
                    "assassin": self.player1.player_id,
                    "target": self.player2.player_id,
                    "result": "failed",
                    "lost_garrisons": [],
                    "rebellions": [],
                },
            )
        )

    def test_do_assassination_attempt_max_amount_reached(self):
        """Realiza un intento de asesinato gastando más del límite."""

        self.mock_rng.randint.return_value = 4

        # Debería fallar, porque a pesar de gastar 48 ducados, el máximo valor es 3
        self.resolver._do_assassination_attempt(
            self.player1, Command(self.game, self.player1, "E E", "48", "L")
        )

        # Comprueba que se ha gastado la ficha de asesinato
        self.assertNotIn("L", self.player1.ass_counters)

        # Comprueba el evento
        self.game.add_event.assert_called_once_with(
            TurnEvent(
                EventType.ASSASSINATION_ATTEMPT,
                data={
                    "assassin": self.player1.player_id,
                    "target": self.player2.player_id,
                    "result": "failed",
                    "lost_garrisons": [],
                    "rebellions": [],
                },
            )
        )

    def test_do_assassination_attempt_missing_counter(self):
        """Realiza un intento de asesinato, pero no tiene ficha."""
        self.player1.ass_counters.remove("L")

        self.resolver._do_assassination_attempt(
            self.player1, Command(self.game, self.player1, "E E", "24", "L")
        )

        # Comprueba que no hay evento
        self.game.add_event.assert_not_called()

    def test_do_assassination_attempt_late(self):
        """Realiza un intento de asesinato a alguien que ya ha sido asesinado."""

        self.resolver.assassinations = ["L"]
        self.resolver._do_assassination_attempt(
            self.player1, Command(self.game, self.player1, "E E", "24", "L")
        )

        # Comprueba que se ha gastado la ficha de asesinato
        self.assertNotIn("L", self.player1.ass_counters)

        # Comprueba el evento
        self.game.add_event.assert_called_once_with(
            TurnEvent(
                EventType.ASSASSINATION_ATTEMPT,
                data={
                    "assassin": self.player1.player_id,
                    "target": self.player2.player_id,
                    "result": "late",
                    "lost_garrisons": [],
                    "rebellions": [],
                },
            )
        )


class TestRun(unittest.TestCase):
    """Tests para el método run de AssassinationResolver."""

    def setUp(self):
        """Prepara juego de pruebas para los tests sobre run."""
        self.game = create_mock_game()

        self.player1 = create_mock_player("Naples", power="N", home_countries=["N"])
        self.player2 = create_mock_player("Florence", power="L", home_countries=["L"])
        self.player1.ass_counters = ["L", "V"]
        self.player2.ass_counters = ["N", "V"]
        self.command1 = Command(self.game, self.player1, "E E", "24", "L")
        self.command2 = Command(self.game, self.player2, "E E", "24", "N")
        self.player1.commands = [self.command1]
        self.player2.commands = [self.command2]

        self.game.players = [self.player1, self.player2]

        self.mock_rng = Mock()

        self.resolver = AssassinationResolver(self.game, self.mock_rng)

    def test_run(self):
        """Comprueba que se ejecuta correctamente run."""

        with patch.object(
            AssassinationResolver, "_do_assassination_attempt"
        ) as mock_assassinate:
            self.resolver.run()

            expected_calls = [
                call(self.player1, self.command1),
                call(self.player2, self.command2),
            ]
            mock_assassinate.assert_has_calls(expected_calls, any_order=False)
            self.assertEqual(mock_assassinate.call_count, len(expected_calls))
