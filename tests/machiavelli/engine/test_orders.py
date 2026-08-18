"""Pruebas de contrato para el procesamiento centralizado de órdenes."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from machiavelli.engine.exceptions import TooManyExpenses
from machiavelli.engine.orders import OrderProcessor
from machiavelli.game import (
    DuplicatedGameException,
    FailedToStartError,
    GameNotFoundException,
)
from machiavelli.game.command import Command
from machiavelli.game.exceptions import (
    DuplicatedGameException as DomainDuplicatedGameException,
)
from machiavelli.game.exceptions import FailedToStartError as DomainFailedToStartError
from machiavelli.game.exceptions import (
    GameNotFoundException as DomainGameNotFoundException,
)
from machiavelli.game.game import (
    DuplicatedGameException as CompatibilityDuplicatedGameException,
)
from machiavelli.game.game import FailedToStartError as CompatibilityFailedToStartError
from machiavelli.game.game import (
    GameNotFoundException as CompatibilityGameNotFoundException,
)
from machiavelli.game.game import TooManyExpenses as CompatibilityTooManyExpenses
from machiavelli.game.map import Map, Province, Route, Sea
from machiavelli.game.player import TurnType

from .helpers import create_military_game


def create_test_map() -> Map:
    """Construye el mapa base utilizado para las pruebas de órdenes."""
    origin = Province("Origin", custom_id="origin", has_port=True)
    destination = Province("Destination", custom_id="destination", has_port=True)
    fort = Province("Fort", custom_id="fort", city="Fort")
    sea_one = Sea("Sea One", custom_id="sea-one")
    sea_two = Sea("Sea Two", custom_id="sea-two")

    sea_one.sea_routes = [Route("sea-two"), Route("destination")]
    sea_two.sea_routes = [Route("destination")]

    return Map(
        provinces={
            origin.id: origin,
            destination.id: destination,
            fort.id: fort,
        },
        seas={sea_one.id: sea_one, sea_two.id: sea_two},
    )


class TestGameExceptions(unittest.TestCase):
    def test_game_exceptions_and_expense_exception_have_single_identities(
        self,
    ) -> None:
        self.assertIs(FailedToStartError, DomainFailedToStartError)
        self.assertIs(FailedToStartError, CompatibilityFailedToStartError)
        self.assertIs(DuplicatedGameException, DomainDuplicatedGameException)
        self.assertIs(DuplicatedGameException, CompatibilityDuplicatedGameException)
        self.assertIs(GameNotFoundException, DomainGameNotFoundException)
        self.assertIs(GameNotFoundException, CompatibilityGameNotFoundException)
        self.assertIs(TooManyExpenses, CompatibilityTooManyExpenses)


class BaseOrdersTestCase(unittest.TestCase):
    """Fixture base usando ``create_military_game`` de helpers.py."""

    def setUp(self) -> None:
        self.game_map = create_test_map()
        self.game = create_military_game(
            game_map=self.game_map,
            players=[
                {
                    "player_id": "player_1",
                    "armies": ["origin"],
                    "fleets": ["sea-one", "sea-two"],
                    "garrisons": ["fort"],
                }
            ],
            name="orders-test",
        )
        self.player = self.game.players[0]
        self.processor = OrderProcessor(self.game)


class TestMaintenanceTurn(BaseOrdersTestCase):
    @patch("machiavelli.engine.orders.CommandReporter.format_report")
    def test_add_new_command_through_player_facade(
        self,
        mock_format_report: MagicMock,
    ) -> None:
        self.game.turn_number = 1
        command = Command(self.game, self.player, "A origin", "M")
        texto_mockeado = "A origin | M"
        mock_format_report.return_value = texto_mockeado

        report = self.player.cmd_add_command(TurnType.MAINTENANCE, command)

        # Verificación de las 2 llamadas esperadas
        self.assertEqual(mock_format_report.call_count, 2)
        expected_call = call(command, self.game.map, self.game.turn_number)
        mock_format_report.assert_has_calls([expected_call, expected_call])

        self.assertEqual(self.player.commands, [command])
        self.assertEqual(
            report,
            [
                f"Orden `{texto_mockeado}` enviada.",
                "**Órdenes recibidas hasta ahora:**",
                f"`{texto_mockeado}`",
            ],
        )

    def test_replace_existing_command(self) -> None:
        self.game.turn_number = 1
        current = Command(self.game, self.player, "A origin", "M")
        self.player.add_command(current)
        previous_text = str(current)
        replacement = Command(self.game, self.player, "A origin", "D")

        report = self.processor.process_command(
            self.player, TurnType.MAINTENANCE, replacement
        )

        self.assertEqual(self.player.commands, [current])
        self.assertEqual(current.command, "D")
        self.assertIsNone(current.target)
        self.assertEqual(report[1], f"Sustituye la orden anterior `{previous_text}`.")

    def test_disband_removes_order_for_new_unit(self) -> None:
        self.game.turn_number = 1
        creation = Command(self.game, self.player, "A reserve", "C", "origin")
        self.player.add_command(creation)
        disband = Command(self.game, self.player, "A reserve", "D")

        self.processor.process_command(self.player, TurnType.MAINTENANCE, disband)

        self.assertEqual(self.player.commands, [])

    def test_duplicate_rows_for_actor_raise_value_error(self) -> None:
        self.game.turn_number = 1
        first = Command(self.game, self.player, "A origin", "M")
        second = Command(self.game, self.player, "A origin", "D")
        self.player.commands = [first, second]
        replacement = Command(self.game, self.player, "A origin", "M")

        with self.assertRaisesRegex(ValueError, "Se encontraron múltiples comandos"):
            self.processor.process_command(
                self.player, TurnType.MAINTENANCE, replacement
            )

        self.assertEqual(self.player.commands, [first, second])


class TestCampaignExpenses(BaseOrdersTestCase):
    def test_add_new_expense(self) -> None:
        self.game.turn_number = 2
        expense = Command(self.game, self.player, "E 1", "5", "origin")

        self.processor.process_command(self.player, TurnType.CAMPAIGN, expense)

        self.assertEqual(self.player.commands, [expense])

    @patch("machiavelli.engine.orders.CommandReporter.format_report")
    def test_update_existing_expense(self, mock_format_report: MagicMock) -> None:
        formated_text = "A|6 ducados"
        mock_format_report.return_value = formated_text

        self.game.turn_number = 2
        expense = Command(self.game, self.player, "E A", "6", "origin")
        self.player.add_command(expense)

        update = Command(self.game, self.player, "E A", "3", "origin")

        report = self.processor.process_command(self.player, TurnType.CAMPAIGN, update)

        self.assertEqual(self.player.commands, [expense])
        self.assertEqual(expense.command, "3")
        self.assertEqual(report[1], f"Sustituye el gasto anterior `{formated_text}`.")

    @patch("machiavelli.engine.orders.CommandReporter.format_report")
    def test_zero_cost_removes_existing_expense(
        self, mock_format_report: MagicMock
    ) -> None:
        formated_text = "A|3 ducados"
        mock_format_report.return_value = formated_text

        self.game.turn_number = 2
        expense = Command(self.game, self.player, "E A", "6", "origin")

        self.player.add_command(expense)
        removal = Command(self.game, self.player, "E A", "0", "origin")

        report = self.processor.process_command(self.player, TurnType.CAMPAIGN, removal)

        self.assertEqual(self.player.commands, [])
        self.assertEqual(report[1], f"Elimina el gasto anterior `{formated_text}`.")

    def test_more_than_four_expenses_raises_single_exception(self) -> None:
        self.game.turn_number = 2
        self.player.commands = [
            Command(self.game, self.player, f"E {index}", "2", "origin")
            for index in range(4)
        ]
        fifth = Command(self.game, self.player, "E 5", "1", "origin")

        with self.assertRaisesRegex(
            TooManyExpenses, "Solo se permiten hasta cuatro gastos"
        ):
            self.processor.process_command(self.player, TurnType.CAMPAIGN, fifth)

        self.assertEqual(len(self.player.commands), 4)


class TestCampaignOrders(BaseOrdersTestCase):
    @patch("machiavelli.engine.orders.CommandReporter.format_report")
    def test_standard_order_replaces_previous_order(
        self, mock_format_report: MagicMock
    ) -> None:
        formated_text = "command_representation"
        mock_format_report.return_value = formated_text

        self.game.turn_number = 2
        current = Command(self.game, self.player, "A origin", "H")

        self.player.add_command(current)
        replacement = Command(self.game, self.player, "A origin", "A", "destination")

        report = self.processor.process_command(
            self.player, TurnType.CAMPAIGN, replacement
        )

        self.assertEqual(self.player.commands, [replacement])
        self.assertEqual(report[1], "Sustituye la orden anterior.")
        self.assertEqual(report[2], f"`{formated_text}`")

    def test_valid_convoy_appends_segment(self) -> None:
        self.game.turn_number = 2
        first_segment = Command(self.game, self.player, "A origin", "A", "sea-one")
        self.player.add_command(first_segment)
        destination = Command(self.game, self.player, "A origin", "A", "destination")

        self.processor.process_command(self.player, TurnType.CAMPAIGN, destination)

        self.assertEqual(self.player.commands, [first_segment, destination])

    def test_invalid_convoy_replaces_previous_segments(self) -> None:
        self.game.turn_number = 2
        first_segment = Command(self.game, self.player, "A origin", "A", "sea-one")
        self.player.add_command(first_segment)
        invalid_destination = Command(self.game, self.player, "A origin", "A", "fort")

        self.processor.process_command(
            self.player, TurnType.CAMPAIGN, invalid_destination
        )

        self.assertEqual(self.player.commands, [invalid_destination])

    def test_order_without_target_is_preserved(self) -> None:
        self.game.turn_number = 2
        hold = Command(self.game, self.player, "F sea-two", "H")

        self.processor.process_command(self.player, TurnType.CAMPAIGN, hold)

        self.assertEqual(self.player.commands, [hold])
        self.assertIsNone(self.player.commands[0].target)

    @patch("machiavelli.engine.orders.CommandReporter.format_report")
    def test_report_messages_and_commands_keep_stable_order(
        self,
        mock_format_report: MagicMock,
    ) -> None:
        self.game.turn_number = 2
        army = Command(self.game, self.player, "A origin", "H")
        fleet = Command(self.game, self.player, "F sea-one", "H")
        garrison = Command(self.game, self.player, "G fort", "H")
        self.player.commands = [army, fleet]

        # Devuelve un texto legible dinámico basado en la orden recibida
        mock_format_report.side_effect = lambda cmd, map, turn: (
            f"FORMATTED({cmd.actor})"
        )

        report = self.processor.process_command(
            self.player, TurnType.CAMPAIGN, garrison
        )

        # 1. Verificar las 4 llamadas exactas en orden
        self.assertEqual(mock_format_report.call_count, 4)
        expected_calls = [
            call(garrison, self.game.map, self.game.turn_number),  # Confirmación
            call(army, self.game.map, self.game.turn_number),  # Resumen orden 1
            call(fleet, self.game.map, self.game.turn_number),  # Resumen orden 2
            call(garrison, self.game.map, self.game.turn_number),  # Resumen orden 3
        ]
        mock_format_report.assert_has_calls(expected_calls, any_order=False)

        # 2. Verificar la estructura del reporte ensamblado
        self.assertEqual(
            report,
            [
                "Orden `FORMATTED(G fort)` enviada.",
                "**Órdenes recibidas hasta ahora:**",
                "`FORMATTED(A origin)`",
                "`FORMATTED(F sea-one)`",
                "`FORMATTED(G fort)`",
            ],
        )
        self.assertEqual(self.player.commands, [army, fleet, garrison])


if __name__ == "__main__":
    unittest.main()
