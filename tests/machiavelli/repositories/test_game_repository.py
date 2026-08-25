"""Pruebas de persistencia de agregados mediante `GameRepository`."""

import logging
import sqlite3
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

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

logger = logging.getLogger(__name__)


class TestGameRepository(unittest.TestCase):
    """Casos de prueba para GameRepository usando unittest."""

    def setUp(self) -> None:
        """Crea la conexión en memoria y aplica las migraciones antes de cada test."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        database.upgrade_connection(self.conn)

    def tearDown(self) -> None:
        """Cierra la conexión después de cada test."""
        self.conn.close()

    def test_save_and_load_complete_game(self) -> None:
        repo = GameRepository(self.conn)
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
            self.assertEqual(loaded.name, "Repositorio")
            self.assertEqual(loaded.famine, ["rome"])
            self.assertEqual(loaded.independent_garrisons, ["pisa"])
            self.assertEqual(loaded.besieges, ["flore"])
            self.assertEqual(loaded.turn_events, [event, event])
            self.assertEqual(len(loaded.players), 1)
            self.assertEqual(loaded.players[0].player_id, "P1")
            self.assertEqual(loaded.players[0].controlled_locations, ["rome"])
            self.assertIsNone(loaded.players[0].commands[0].target)

        rows = self.conn.execute(
            "SELECT event_type, data_json FROM game_events ORDER BY id ASC"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                (EventType.EXPENSE.value, event.to_json()),
                (EventType.EXPENSE.value, event.to_json()),
            ],
        )

    def test_save_rolls_back_complete_new_game_on_player_command_error(self) -> None:
        repo = GameRepository(self.conn)
        game = Game(name="Debe revertirse", channel_id=555)
        first = Player(game, "P1", ducats=10)
        first.commands = [Command(game, first, "A rome", "H", None)]
        second = Player(game, "P2", ducats=20)
        second.commands = [
            Command(game, second, None, "H", None)  # type: ignore[arg-type]
        ]
        game.players = [first, second]

        with self.assertRaises(sqlite3.IntegrityError):
            repo.save(game)

        self.assertIsNone(game.database_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM games").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 0
        )

    def test_save_rejects_strings_and_rolls_back_complete_aggregate(self) -> None:
        repo = GameRepository(self.conn)
        game = Game(
            name="Evento inválido",
            channel_id=556,
            turn_events=["texto"],  # type: ignore[list-item]
        )
        player = Player(game, "P1")
        player.commands = [Command(game, player, "A rome", "H", None)]
        game.players = [player]

        with self.assertRaises(AttributeError):
            repo.save(game)

        self.assertIsNone(game.database_id)
        for table in ("games", "players", "commands", "game_events"):
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0)

    def test_load_aborts_on_first_corrupt_event(self) -> None:
        """Aborta la actualización en el primer evento corrupto."""
        test_cases = [
            ("unknown", "{}"),
            (EventType.START_GAME.value, "not json"),
            (EventType.START_GAME.value, "[]"),
            (EventType.START_GAME.value, '{"scenario":""}'),
        ]

        for event_type, data_json in test_cases:
            with self.subTest(event_type=event_type, data_json=data_json):
                repo = GameRepository(self.conn)
                game = Game(name=f"Corrupta-{event_type}-{data_json}", channel_id=None)
                repo.save(game)
                cursor = self.conn.execute(
                    "INSERT INTO game_events (game_id, event_type, data_json) "
                    "VALUES (?, ?, ?)",
                    (game.database_id, event_type, data_json),
                )
                self.conn.commit()

                with self.assertRaises(InvalidTurnEventError) as caught:
                    repo.get_by_id(game.database_id)

                self.assertEqual(caught.exception.row_id, cursor.lastrowid)
                self.assertEqual(caught.exception.event_type, event_type)
                self.assertIsNotNone(caught.exception.__cause__)

                # Limpiamos la base de datos para el siguiente subTest
                self.conn.execute("DELETE FROM game_events")
                self.conn.execute("DELETE FROM games")
                self.conn.commit()

    def test_ten_save_load_cycles_preserve_all_types_and_replace_rows(self) -> None:
        repo = GameRepository(self.conn)

        # NOTA: En tu código original faltaba la definición de este fixture.
        # Aquí he puesto un diccionario de ejemplo para que el test compile.
        valid_event_payloads: Mapping[EventType, dict[str, JSONValue]] = {
            EventType.EXPENSE: {
                "player": "P1",
                "expense": "A",
                "target": None,
                "amount": "sí",
            },
            EventType.START_GAME: {"scenario": "Italia"},
        }

        events = [
            TurnEvent(event_type, payload)
            for event_type, payload in valid_event_payloads.items()
        ]
        events.extend([events[0], events[-1]])
        game = Game(name="Diez ciclos", channel_id=10, turn_events=events)

        for _ in range(10):
            repo.save(game)
            game = repo.get_by_id(game.database_id)
            self.assertEqual(game.turn_events, events)
            self.assertEqual(
                self.conn.execute(
                    "SELECT COUNT(*) FROM game_events WHERE game_id = ?",
                    (game.database_id,),
                ).fetchone(),
                (len(events),),
            )

    def test_second_event_insert_failure_rolls_back_complete_snapshot(self) -> None:
        # Usamos tempfile en lugar del fixture nativo tmp_path de pytest
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "rollback.db"
            database.upgrade(db_path)

            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            try:
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

                with self.assertRaisesRegex(sqlite3.IntegrityError, "fallo inyectado"):
                    repo.save(game)
            finally:
                conn.close()

            # Verificamos en una conexión nueva (fuera del bloque try para usar
            # otra conexión limpia)
            check = sqlite3.connect(db_path)
            try:
                persisted = GameRepository(check).get_by_id(game.database_id)
                self.assertEqual(persisted.name, "Rollback")
                self.assertEqual(persisted.players[0].ducats, 5)
                self.assertEqual(len(persisted.players[0].commands), 1)
                self.assertEqual(persisted.turn_events, [old_event])
            finally:
                check.close()
