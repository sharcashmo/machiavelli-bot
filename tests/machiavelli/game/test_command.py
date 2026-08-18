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


class TestIsValidExpense:
    def test_valid_expense_default_tables(self, monkeypatch) -> None:
        monkeypatch.setattr(
            GameTables,
            "expenses",
            {"B": {"text": "Pacificar rebelión"}},
        )

        cmd = create_command(actor="E B", command="12")
        assert cmd.is_valid_expense() is True

    def test_valid_expense_with_allowed_types(self) -> None:
        cmd = create_command(actor="E B", command="12")

        assert cmd.is_valid_expense(allowed_types={"B", "S"}) is True
        assert cmd.is_valid_expense(allowed_types={"S"}) is False

    def test_invalid_actor_format(self) -> None:
        invalid_actors = ["A milan", "E", "E B 12", "E_B"]
        for actor in invalid_actors:
            cmd = create_command(actor=actor, command="12")
            assert cmd.is_valid_expense() is False
