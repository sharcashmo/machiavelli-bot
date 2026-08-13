# tests/machiavelli/engine/test_assassination.py

import unittest

from machiavelli.engine.assassination import AssassinationResolver

from .helpers import create_mock_game, create_mock_player


class TestExecuteAssassination(unittest.TestCase):
    """Tests para el método privado _execute_assassination de AssassinationResolver."""

    def setUp(self):
        """Prepara el juego de pruebas para los tests sobre _excute_assassionation."""
        self.game = create_mock_game()
        self.player1 = create_mock_player("P1")
        self.player2 = create_mock_player("P2")
        self.resolver = AssassinationResolver(self.game)

    def test_do_execute_assassionation_success(self):
        """Ejecuta un asesinato con éxito."""
        pass
