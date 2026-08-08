"""Comprobaciones finales de integración en un estado limpio para la migración modular
completada.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from machiavelli import database
from machiavelli.db import database as canonical_database
from machiavelli.game.command import Command
from machiavelli.game.game import Game
from machiavelli.repositories.game_repository import GameRepository
from machiavelli.services import GameService


def _state(game: Game) -> dict[str, object]:
    """Devuelve el estado persistido relevante para el contrato final de ida y vuelta.
    """
    return {
        "database_id": game.database_id,
        "name": game.name,
        "channel_id": game.channel_id,
        "scenario_id": game.scenario_id,
        "turn_number": game.turn_number,
        "weekly_deadline": game.weekly_deadline,
        "next_deadline": game.next_deadline,
        "famine": tuple(game.famine),
        "independent_garrisons": tuple(game.independent_garrisons),
        "besieges": tuple(game.besieges),
        "turn_events": tuple(game.turn_events),
        "players": tuple(
            (
                player.player_id,
                player.discord_id,
                player.power,
                tuple(player.controlled_locations),
                tuple(player.armies),
                tuple(player.fleets),
                tuple(player.garrisons),
                tuple(player.ass_counters),
                player.ducats,
                tuple(player.rebelled_provinces),
                tuple(player.rebelled_cities),
                tuple(player.home_countries),
                tuple(
                    (command.actor, command.command, command.target)
                    for command in player.commands
                ),
            )
            for player in game.players
        ),
    }


def test_temporary_database_survives_orders_turn_and_repeated_reloads() -> None:
    """Prueba el ciclo de vida completo de la aplicación persistida en una base de datos
    aislada.
    """
    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "final-validation.db"
        database.upgrade(str(db_path))

        with closing(sqlite3.connect(db_path)) as conn:
            service = GameService(GameRepository(conn))
            created = service.create_game("Validación final", 10_010, "Be")
            for index in range(6):
                service.add_player(10_010, 20_000 + index, f"P{index + 1}")

            game = service.get_game(10_010)
            first_player = game.players[0]
            first_player.commands.append(
                Command(
                    game=game,
                    player=first_player,
                    actor="A milan",
                    command="H",
                    target=None,
                )
            )
            service.repo.save(game)
            initial_state = _state(service.get_game(10_010))

            assert created.database_id == game.database_id
            assert initial_state["turn_number"] == 0
            assert initial_state["players"]

        with closing(sqlite3.connect(db_path)) as conn:
            service = GameService(GameRepository(conn))
            restored = service.get_game(10_010)

            assert _state(restored) == initial_state
            assert restored.players[0].game is restored
            assert restored.players[0].commands[0].game is restored
            assert restored.players[0].commands[0].player is restored.players[0]

            report = service.run_turn(10_010)
            completed = service.get_game(10_010)
            completed_state = _state(completed)

            assert report
            assert completed.turn_number == 1
            assert all(player.power is not None for player in completed.players)
            assert all(player.commands == [] for player in completed.players)
            assert completed.turn_events

        with closing(sqlite3.connect(db_path)) as conn:
            service = GameService(GameRepository(conn))
            reloaded = service.get_game(10_010)

            assert _state(reloaded) == completed_state
            assert conn.execute("PRAGMA user_version").fetchone() == (
                canonical_database._SCHEMA_VERSION,
            )
