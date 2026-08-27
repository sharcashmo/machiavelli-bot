import unittest
from unittest.mock import Mock, patch

from machiavelli.game.command import Command
from machiavelli.game.game import Game
from machiavelli.game.player import Player
from machiavelli.game.tables import GameTables


def create_command(
    *,
    actor: str,
    command: str,
    target: str | None = None,
) -> Command:
    game = Game(name="command-test", database_id=1)
    player = Player(game=game, player_id="P1")
    return Command(
        game=game,
        player=player,
        actor=actor,
        command=command,
        target=target,
    )


class TestCommandProperties(unittest.TestCase):
    """Comprobar el acceso a player_id y game_id por sus propiedades."""

    def setUp(self) -> None:
        """Creamos un set de pruebas."""
        self.game = Mock(name="test", database_id=1)
        self.player = Mock(game=self.game, player_id="P1")
        self.command = Command(
            game=self.game,
            player=self.player,
            actor="A prove",
            command="A",
            target="marse",
        )

    def TestCommandPlayerId(self) -> None:
        """Comprueba el acceso a player_id."""
        self.assertEqual(self.command.player_id, self.player.player_id)

    def TestCommandGameId(self) -> None:
        """Comprueba el acceso a game_id."""
        self.assertEqual(self.command.game_id, self.game.database_id)


class TestIsValidExpense(unittest.TestCase):
    @patch.object(GameTables, "expenses", new={"B": {"text": "Pacificar rebelión"}})
    def test_valid_expense_default_tables(self) -> None:

        cmd = create_command(actor="E B", command="12")
        self.assertTrue(cmd.is_valid_expense())

    def test_valid_expense_with_allowed_types(self) -> None:
        cmd = create_command(actor="E B", command="12")

        self.assertTrue(cmd.is_valid_expense(allowed_types={"B", "S"}))
        self.assertFalse(cmd.is_valid_expense(allowed_types={"S"}))

    def test_invalid_actor_format(self) -> None:
        invalid_actors = ["A milan", "E", "E B 12", "E_B"]
        for actor in invalid_actors:
            cmd = create_command(actor=actor, command="12")
            self.assertFalse(cmd.is_valid_expense())
