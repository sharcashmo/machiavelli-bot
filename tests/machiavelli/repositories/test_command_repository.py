"""Pruebas de la persistencia de comandos canónicos."""

import sqlite3
from unittest.mock import MagicMock

import pytest

from machiavelli.db.database import _UPGRADES, DatabaseManager
from machiavelli.game.command import Command
from machiavelli.game.game import Game
from machiavelli.game.player import Player
from machiavelli.repositories.command_repository import CommandRepository


@pytest.fixture
def db_conn():
    manager = DatabaseManager(":memory:")
    conn = manager.get_connection()
    for version, script in enumerate(_UPGRADES, start=1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def domain(db_conn: sqlite3.Connection) -> tuple[Game, Player, Player]:
    db_conn.execute(
        "INSERT INTO games (id, name, channel_id) VALUES (?, ?, ?)",
        (1, "Partida Test", 1001),
    )
    db_conn.executemany(
        "INSERT INTO players (game_id, player_id, discord_id) VALUES (?, ?, ?)",
        ((1, "p1", 2001), (1, "p2", 2002)),
    )
    db_conn.commit()

    game = Game(name="Partida Test", channel_id=1001, database_id=1)
    return game, Player(game, "p1", 2001), Player(game, "p2", 2002)


@pytest.fixture
def repo(db_conn: sqlite3.Connection) -> CommandRepository:
    return CommandRepository(db_conn)


def test_get_by_player_orders_query_by_command_id() -> None:
    """Fija el contrato de ordenación persistida a la clave primaria de comando
    existente.
    """
    conn = MagicMock(spec=sqlite3.Connection)
    cursor = MagicMock(spec=sqlite3.Cursor)
    cursor.fetchall.return_value = []
    conn.execute.return_value = cursor
    game = Game(name="Partida Test", database_id=1)
    player = Player(game, "p1")

    assert CommandRepository(conn).get_by_player(player) == []

    sql, parameters = conn.execute.call_args.args
    assert "ORDER BY commands.id ASC" in " ".join(sql.split())
    assert parameters == (1, "p1")


def test_save_and_get_by_player(
    repo: CommandRepository,
    domain: tuple[Game, Player, Player],
) -> None:
    game, player, _ = domain
    command = Command(game, player, "A milan", "A", "venic")

    repo.save(command)
    retrieved = repo.get_by_player(player)

    assert len(retrieved) == 1
    assert retrieved[0].game is game
    assert retrieved[0].player is player
    assert retrieved[0].game_id == 1
    assert retrieved[0].player_id == "p1"
    assert retrieved[0].actor == "A milan"
    assert retrieved[0].command == "A"
    assert retrieved[0].target == "venic"


def test_save_many_preserves_order_and_none_target(
    repo: CommandRepository,
    domain: tuple[Game, Player, Player],
) -> None:
    game, player, _ = domain
    commands = (
        Command(game, player, "A milan", "A", "venic"),
        Command(game, player, "F UA", "H", None),
        Command(game, player, "E B", "12", "flore"),
    )

    repo.save_many(command for command in commands)
    retrieved = repo.get_by_player(player)

    assert [(item.actor, item.target) for item in retrieved] == [
        ("A milan", "venic"),
        ("F UA", None),
        ("E B", "flore"),
    ]


def test_interleaved_players_keep_their_relative_order(
    repo: CommandRepository,
    domain: tuple[Game, Player, Player],
) -> None:
    game, player_one, player_two = domain
    repo.save_many(
        [
            Command(game, player_one, "A one", "A", "first"),
            Command(game, player_two, "A two", "A", "other"),
            Command(game, player_one, "A one", "A", "second"),
            Command(game, player_two, "A two", "H", None),
            Command(game, player_one, "A one", "A", "third"),
        ]
    )

    first_load = repo.get_by_player(player_one)
    second_load = repo.get_by_player(player_one)

    assert [command.target for command in first_load] == ["first", "second", "third"]
    assert [command.target for command in second_load] == ["first", "second", "third"]
    assert [command.target for command in repo.get_by_player(player_two)] == [
        "other",
        None,
    ]


def test_delete_by_player_is_isolated(
    repo: CommandRepository,
    domain: tuple[Game, Player, Player],
) -> None:
    game, player_one, player_two = domain
    repo.save_many(
        [
            Command(game, player_one, "A milan", "A", "venic"),
            Command(game, player_two, "F UA", "H", None),
        ]
    )

    repo.delete_by_player(player_one)

    assert repo.get_by_player(player_one) == []
    assert len(repo.get_by_player(player_two)) == 1


def test_foreign_key_constraint(repo: CommandRepository) -> None:
    game = Game(name="Ausente", database_id=999)
    player = Player(game, "non-existent")
    command = Command(game, player, "A milan", "A", "venic")

    with pytest.raises(sqlite3.IntegrityError):
        repo.save(command)


def test_save_many_rolls_back_every_row_on_error(
    repo: CommandRepository,
    db_conn: sqlite3.Connection,
    domain: tuple[Game, Player, Player],
) -> None:
    game, player, _ = domain
    valid = Command(game, player, "A milan", "H", None)
    invalid = Command(game, player, None, "H", None)  # type: ignore[arg-type]

    with pytest.raises(sqlite3.IntegrityError):
        repo.save_many([valid, invalid])

    count = db_conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
    assert count == 0


def test_rejects_command_bound_to_a_different_game(
    repo: CommandRepository,
    domain: tuple[Game, Player, Player],
) -> None:
    game, player, _ = domain
    other_game = Game(name="Otra", database_id=2)
    command = Command(other_game, player, "A milan", "H", None)

    with pytest.raises(ValueError, match="partidas distintas"):
        repo.save(command)
