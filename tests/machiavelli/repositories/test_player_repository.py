"""Pruebas de la persistencia de jugadores canónicos."""

import sqlite3

import pytest

from machiavelli.db.database import _UPGRADES, DatabaseManager
from machiavelli.game.command import Command
from machiavelli.game.game import Game
from machiavelli.game.player import Player
from machiavelli.repositories.player_repository import PlayerRepository


@pytest.fixture
def db_connection():
    manager = DatabaseManager(":memory:")
    conn = manager.get_connection()
    for version, script in enumerate(_UPGRADES, start=1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.execute(
        "INSERT INTO games (id, name, channel_id) VALUES (?, ?, ?)",
        (100, "Persistencia", 5000),
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def game() -> Game:
    return Game(name="Persistencia", channel_id=5000, database_id=100)


def test_save_and_get_player_preserves_state_and_commands(
    db_connection: sqlite3.Connection,
    game: Game,
) -> None:
    repo = PlayerRepository(db_connection)
    player = Player(
        game=game,
        player_id="p1",
        discord_id=987654321,
        controlled_locations=["paler", "messi"],
        armies=["messi"],
        fleets=["paler"],
        garrisons=["flore"],
        ass_counters=["V"],
        ducats=15,
        rebelled_provinces=["rome"],
        rebelled_cities=["pisa"],
        home_countries=["N"],
        power="N",
    )
    player.commands = [
        Command(game, player, "A messi", "A", "paler"),
        Command(game, player, "F paler", "H", None),
    ]

    repo.save(player)
    loaded = repo.get_by_game(game)

    assert len(loaded) == 1
    restored = loaded[0]
    assert restored.game is game
    assert restored.player_id == "p1"
    assert restored.discord_id == 987654321
    assert restored.controlled_locations == ["paler", "messi"]
    assert restored.armies == ["messi"]
    assert restored.fleets == ["paler"]
    assert restored.garrisons == ["flore"]
    assert restored.ass_counters == ["V"]
    assert restored.ducats == 15
    assert restored.rebelled_provinces == ["rome"]
    assert restored.rebelled_cities == ["pisa"]
    assert restored.home_countries == ["N"]
    assert restored.power == "N"
    assert [(command.actor, command.target) for command in restored.commands] == [
        ("A messi", "paler"),
        ("F paler", None),
    ]
    assert all(command.player is restored for command in restored.commands)


def test_update_existing_player_upserts_without_duplicates(
    db_connection: sqlite3.Connection,
    game: Game,
) -> None:
    repo = PlayerRepository(db_connection)
    player = Player(game=game, player_id="p1", ducats=10)
    repo.save(player)

    player.ducats = 25
    player.armies.append("naple")
    repo.save(player)

    loaded = repo.get_by_game(game)
    count = db_connection.execute(
        "SELECT COUNT(*) FROM players WHERE game_id = ? AND player_id = ?",
        (100, "p1"),
    ).fetchone()[0]
    assert count == 1
    assert len(loaded) == 1
    assert loaded[0].ducats == 25
    assert loaded[0].armies == ["naple"]


def test_save_commands_replaces_old_rows_in_list_order(
    db_connection: sqlite3.Connection,
    game: Game,
) -> None:
    repo = PlayerRepository(db_connection)
    player = Player(game=game, player_id="p1")
    player.commands = [Command(game, player, "A old", "H", None)]
    repo.save(player)

    player.commands = [
        Command(game, player, "A new", "A", "first"),
        Command(game, player, "F new", "A", "second"),
    ]
    repo.save_commands(player)

    assert [command.target for command in repo.get_by_game(game)[0].commands] == [
        "first",
        "second",
    ]


def test_save_rolls_back_player_and_commands_on_command_error(
    db_connection: sqlite3.Connection,
    game: Game,
) -> None:
    repo = PlayerRepository(db_connection)
    player = Player(game=game, player_id="p1", ducats=10)
    player.commands = [Command(game, player, "A old", "H", None)]
    repo.save(player)

    player.ducats = 99
    player.commands = [
        Command(game, player, "A new", "H", None),
        Command(game, player, None, "H", None),  # type: ignore[arg-type]
    ]

    with pytest.raises(sqlite3.IntegrityError):
        repo.save(player)

    restored = repo.get_by_game(game)[0]
    assert restored.ducats == 10
    assert [(command.actor, command.target) for command in restored.commands] == [
        ("A old", None)
    ]


def test_compatibility_facades_delegate_without_duplicate_sql(
    db_connection: sqlite3.Connection,
    game: Game,
) -> None:
    player = Player(game=game, player_id="facade", controlled_locations=["rome"])
    player.commands = [Command(game, player, "A rome", "H", None)]

    player.save(db_connection)
    loaded = Player.load_players(db_connection, game)

    assert len(loaded) == 1
    assert loaded[0].controlled_locations == ["rome"]
    assert loaded[0].commands[0].target is None


def test_rejects_unpersisted_game(db_connection: sqlite3.Connection) -> None:
    game = Game(name="Nueva")
    player = Player(game, "p1")
    repo = PlayerRepository(db_connection)

    with pytest.raises(ValueError, match="partida sin ID"):
        repo.save(player)
    with pytest.raises(ValueError, match="partida sin ID"):
        repo.get_by_game(game)
