# test/machiavelli/engine/test_expenditure.py
import unittest
from unittest.mock import Mock

from machiavelli.engine.expenditure import ExpenditureProcessor
from machiavelli.game.events import EventType


class TestExpenditureProcessor(unittest.TestCase):
    """Tests unitarios para ExpenditureProcessor."""

    def setUp(self):
        self.mock_game = Mock()
        self.mock_game.add_event = Mock()
        self.processor = ExpenditureProcessor(self.mock_game)

        # Estado inicial del jugador de prueba
        self.player = Mock()
        self.player.player_id = "Milan"
        self.player.ducats = 50
        self.player.commands = []
        self.mock_game.players = [self.player]

    def _make_cmd(self, is_expense: bool, command_val: str, actor="E G", target="pisa"):
        """Helper para construir mocks de Command con el formato esperado."""
        cmd = Mock()
        cmd.is_valid_expense.return_value = is_expense
        cmd.command = command_val
        cmd.actor = actor
        cmd.target = target
        return cmd

    def test_process_player_expenses_no_expense(self):
        """Las órdenes que no son de gasto se conservan silenciosamente."""
        cmd = self._make_cmd(is_expense=False, command_val="A flore")
        self.player.commands = [cmd]

        self.processor.run()

        self.assertEqual(self.player.commands, [cmd])
        self.assertEqual(self.player.ducats, 50)
        self.mock_game.add_event.assert_not_called()

    def test_process_player_expenses_no_numeric_amount(self):
        """Si el importe no es un entero, la descarta y emite EXPENSE_SYNTAX_ERROR."""
        cmd = self._make_cmd(is_expense=True, command_val="diez", target=None)
        self.player.commands = [cmd]

        self.processor.run()

        self.assertEqual(self.player.commands, [])
        self.assertEqual(self.player.ducats, 50)
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.EXPENSE_SYNTAX_ERROR)
        self.assertEqual(
            event.data,
            {
                "player": "Milan",
                "expense": "G",
                "target": None,
                "amount": "diez",
            },
        )

    def test_process_player_expenses_zero_or_negative_amount(self):
        """Si el importe es <= 0, descarta las órdenes y emite EXPENSE_SYNTAX_ERROR."""
        cmd_zero = self._make_cmd(is_expense=True, command_val="0")
        cmd_neg = self._make_cmd(is_expense=True, command_val="-10")
        self.player.commands = [cmd_zero, cmd_neg]

        self.processor.run()

        self.assertEqual(self.player.commands, [])
        self.assertEqual(self.player.ducats, 50)
        self.assertEqual(self.mock_game.add_event.call_count, 2)

    def test_process_player_expenses(self):
        """Con saldo suficiente, descuenta ducados, la conserva y emite EXPENSE."""
        cmd = self._make_cmd(is_expense=True, command_val="20")
        self.player.commands = [cmd]

        self.processor.run()

        self.assertEqual(self.player.commands, [cmd])
        self.assertEqual(self.player.ducats, 30)
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.EXPENSE)
        self.assertEqual(
            event.data,
            {
                "player": "Milan",
                "expense": "G",
                "target": "pisa",
                "amount": 20,
            },
        )

    def test_process_player_expenses_no_funds(self):
        """Sin saldo suficiente, la descarta, no descuenta y emite EXPENSE_NO_FUNDS."""
        cmd = self._make_cmd(is_expense=True, command_val="100")
        self.player.commands = [cmd]

        self.processor.run()

        self.assertEqual(self.player.commands, [])
        self.assertEqual(self.player.ducats, 50)
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.EXPENSE_NO_FUNDS)
        self.assertEqual(
            event.data,
            {
                "player": "Milan",
                "expense": "G",
                "target": "pisa",
                "amount": 100,
            },
        )

    def test_run_sequential(self):
        """Procesa órdenes en orden FIFO recalculando el saldo tras cada cobro."""
        cmd1 = self._make_cmd(is_expense=True, command_val="30")  # Pasa (50-30 = 20)
        cmd2 = self._make_cmd(is_expense=True, command_val="25")  # Falla (20 < 25)
        cmd3 = self._make_cmd(is_expense=True, command_val="15")  # Pasa (20-15 = 5)

        self.player.commands = [cmd1, cmd2, cmd3]

        self.processor.run()

        # Únicamente cmd1 y cmd3 quedan financiados y conservados en orden
        self.assertEqual(self.player.commands, [cmd1, cmd3])
        self.assertEqual(self.player.ducats, 5)
        self.assertEqual(self.mock_game.add_event.call_count, 3)

        # Verificar tipos de evento emitidos secuencialmente
        calls = self.mock_game.add_event.call_args_list
        self.assertEqual(calls[0][0][0].type, EventType.EXPENSE)
        self.assertEqual(calls[1][0][0].type, EventType.EXPENSE_NO_FUNDS)
        self.assertEqual(calls[2][0][0].type, EventType.EXPENSE)

    def test_disabled_expenses_are_dropped_before_validation_or_payment(self):
        self.mock_game.scenario.rules.famine_active = False
        self.mock_game.scenario.rules.assassinations_active = False
        famine = self._make_cmd(True, "5", actor="E A")
        allowed = self._make_cmd(True, "10", actor="E G")
        assassination = self._make_cmd(True, "5", actor="E E")
        self.player.commands = [famine, allowed, assassination]

        self.processor.run()

        self.assertEqual(self.player.commands, [allowed])
        self.assertEqual(self.player.ducats, 40)
        famine.is_valid_expense.assert_not_called()
        assassination.is_valid_expense.assert_not_called()
        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args.args[0]
        self.assertEqual(event.type, EventType.EXPENSE)
        self.assertEqual(event.data["expense"], "G")
