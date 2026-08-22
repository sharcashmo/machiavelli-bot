"""Pruebas de persistencia de agregados mediante `GameRepository`."""

import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest

from machiavelli import database
from machiavelli.game.command import Command
from machiavelli.game.events import (
    EventType,
    InvalidTurnEventError,
    JSONValue,
    TurnEvent,
)
from machiavelli.game.game import Game
from machiavelli.game.player import Player
from machiavelli.repositories.game_repository import GameRepository


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    database.upgrade_connection(conn)
    yield conn
    conn.close()


def test_save_and_load_complete_game(connection: sqlite3.Connection) -> None:
    repo = GameRepository(connection)
    event = TurnEvent(
        EventType.EXPENSE,
        {
            "player": "P1",
            "expense": "A",
            "target": None,
            "amount": "sí",
        },
    )
    game = Game(
        name="Repositorio",
        channel_id=1234,
        famine=["rome"],
        independent_garrisons=["pisa"],
        besieges=["flore"],
        turn_events=[event, event],
    )
    player = Player(
        game,
        "P1",
        discord_id=9876,
        controlled_locations=["rome"],
        armies=["rome"],
        ducats=12,
        power="M",
    )
    player.commands = [Command(game, player, "A rome", "H", None)]
    game.players = [player]

    repo.save(game)

    by_id = repo.get_by_id(game.database_id)
    by_name = repo.get_by_name("Repositorio")
    by_channel = repo.get_by_channel(1234)

    for loaded in (by_id, by_name, by_channel):
        assert loaded.name == "Repositorio"
        assert loaded.famine == ["rome"]
        assert loaded.independent_garrisons == ["pisa"]
        assert loaded.besieges == ["flore"]
        assert loaded.turn_events == [event, event]
        assert len(loaded.players) == 1
        assert loaded.players[0].player_id == "P1"
        assert loaded.players[0].controlled_locations == ["rome"]
        assert loaded.players[0].commands[0].target is None

    rows = connection.execute(
        "SELECT event_type, data_json FROM game_events ORDER BY id ASC"
    ).fetchall()
    assert rows == [
        (EventType.EXPENSE.value, event.to_json()),
        (EventType.EXPENSE.value, event.to_json()),
    ]


def test_save_rolls_back_complete_new_game_on_player_command_error(
    connection: sqlite3.Connection,
) -> None:
    repo = GameRepository(connection)
    game = Game(name="Debe revertirse", channel_id=555)
    first = Player(game, "P1", ducats=10)
    first.commands = [Command(game, first, "A rome", "H", None)]
    second = Player(game, "P2", ducats=20)
    second.commands = [
        Command(game, second, None, "H", None)  # type: ignore[arg-type]
    ]
    game.players = [first, second]

    with pytest.raises(sqlite3.IntegrityError):
        repo.save(game)

    assert game.database_id is None
    assert connection.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0


def test_save_rejects_strings_and_rolls_back_complete_aggregate(
    connection: sqlite3.Connection,
) -> None:
    repo = GameRepository(connection)
    game = Game(
        name="Evento inválido",
        channel_id=556,
        turn_events=["texto"],  # type: ignore[list-item]
    )
    player = Player(game, "P1")
    player.commands = [Command(game, player, "A rome", "H", None)]
    game.players = [player]

    with pytest.raises(TypeError, match="TurnEvent"):
        repo.save(game)

    assert game.database_id is None
    for table in ("games", "players", "commands", "game_events"):
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0


@pytest.mark.parametrize(
    ("event_type", "data_json"),
    [
        ("unknown", "{}"),
        (EventType.START_GAME.value, "not json"),
        (EventType.START_GAME.value, "[]"),
        (EventType.START_GAME.value, '{"scenario":""}'),
    ],
)
def test_load_aborts_on_first_corrupt_event(
    connection: sqlite3.Connection,
    event_type: str,
    data_json: str,
) -> None:
    repo = GameRepository(connection)
    game = Game(name=f"Corrupta-{event_type}-{data_json}", channel_id=None)
    repo.save(game)
    cursor = connection.execute(
        "INSERT INTO game_events (game_id, event_type, data_json) VALUES (?, ?, ?)",
        (game.database_id, event_type, data_json),
    )
    connection.commit()

    with pytest.raises(InvalidTurnEventError) as caught:
        repo.get_by_id(game.database_id)

    assert caught.value.row_id == cursor.lastrowid
    assert caught.value.event_type == event_type
    assert caught.value.__cause__ is not None


def test_ten_save_load_cycles_preserve_all_types_and_replace_rows(
    connection: sqlite3.Connection,
    valid_event_payloads: Mapping[EventType, dict[str, JSONValue]],
) -> None:
    repo = GameRepository(connection)
    events = [
        TurnEvent(event_type, payload)
        for event_type, payload in valid_event_payloads.items()
    ]
    events.extend([events[0], events[-1]])
    game = Game(name="Diez ciclos", channel_id=10, turn_events=events)

    for _ in range(10):
        repo.save(game)
        game = repo.get_by_id(game.database_id)
        assert game.turn_events == events
        assert connection.execute(
            "SELECT COUNT(*) FROM game_events WHERE game_id = ?", (game.database_id,)
        ).fetchone() == (len(events),)


def test_second_event_insert_failure_rolls_back_complete_snapshot(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rollback.db"
    database.upgrade(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    repo = GameRepository(conn)
    old_event = TurnEvent(EventType.START_GAME, {"scenario": "old"})
    game = Game(name="Rollback", channel_id=88, turn_events=[old_event])
    player = Player(game, "P1", ducats=5)
    player.commands = [Command(game, player, "A mil", "H", None)]
    game.players = [player]
    repo.save(game)

    conn.execute(
        """
        CREATE TRIGGER fail_second_event
        BEFORE INSERT ON game_events
        WHEN NEW.event_type = 'start_season'
        BEGIN
            SELECT RAISE(ABORT, 'fallo inyectado');
        END
        """
    )
    conn.commit()

    game.name = "Mutada"
    game.players[0].ducats = 99
    game.players[0].commands = []
    game.turn_events = [
        TurnEvent(EventType.START_GAME, {"scenario": "new"}),
        TurnEvent(EventType.START_SEASON, {"year": 1454, "season": 0}),
    ]
    with pytest.raises(sqlite3.IntegrityError, match="fallo inyectado"):
        repo.save(game)
    conn.close()

    check = sqlite3.connect(db_path)
    try:
        persisted = GameRepository(check).get_by_id(game.database_id)
        assert persisted.name == "Rollback"
        assert persisted.players[0].ducats == 5
        assert len(persisted.players[0].commands) == 1
        assert persisted.turn_events == [old_event]
    finally:
        check.close()
