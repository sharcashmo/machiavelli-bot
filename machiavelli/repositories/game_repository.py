"""Persistencia de objetos de clase Game."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import fields

from ..game.events import TurnEvent
from ..game.exceptions import DuplicatedGameException, GameNotFoundException
from ..game.game import Game
from ..game.map import Map
from ..game.scenario import Scenario
from .events_repository import TurnEventsRepository
from .exchange_repository import ExchangeRepository
from .player_repository import PlayerRepository

logger = logging.getLogger(__name__)


class GameRepository:
    """Guarda y carga partidas."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, game: Game) -> None:
        """Guarda o crea una partida en la base de datos en una única transacción."""
        with self.conn:
            # Creamos dinámicamente la lista de columnas y sus valores
            cursor = self.conn.cursor()

            # Primero, las columnas con valores simples
            columns = [
                item.name
                for item in fields(game)
                if item.name
                not in (
                    "database_id",
                    "players",
                    "scenario",
                    "map",
                    "famine",
                    "independent_garrisons",
                    "besieges",
                    "turn_events",
                    "pending_exchanges",
                )
            ]
            values = [getattr(game, column) for column in columns]

            # Añadimos las colecciones (sets, lists) de valores simples (str)
            for column, value in (
                ("famine", game.famine),
                ("independent_garrisons", game.independent_garrisons),
                ("besieges", game.besieges),
            ):
                columns.append(column)
                values.append(json.dumps(list(value)))

            # Guardamos el ID original por si hay que revertir el cambio
            previous_id = game.database_id

            try:
                # Creamos el registro en la base de datos si no existe
                if game.database_id is None:
                    try:
                        placeholders = ", ".join(["?"] * len(columns))
                        query = (
                            f"INSERT INTO games ({', '.join(columns)}) "
                            f"VALUES ({placeholders})"
                        )
                        cursor.execute(query, tuple(values))
                        game.database_id = cursor.lastrowid
                    except sqlite3.IntegrityError as error:
                        game.database_id = None
                        raise DuplicatedGameException(
                            "No se pudo crear la partida. "
                            f"El nombre '{game.name}' o el canal "
                            f"'{game.channel_id}' ya están en uso."
                        ) from error
                else:
                    set_clause = ", ".join([f"{column} = ?" for column in columns])
                    query = f"UPDATE games SET {set_clause} WHERE id = ?"
                    cursor.execute(query, tuple(values) + (game.database_id,))

                # Por último, guardamos las listas de objetos complejos: jugadores,
                # eventos e intercambios pendientes
                PlayerRepository(self.conn).replace_for_game(game)
                ExchangeRepository(self.conn).replace_for_game(game)
                TurnEventsRepository(self.conn).replace_for_game(game)
            except Exception as error:
                logger.debug("Error is %s", error)
                logger.debug("Previous id is %s", previous_id)
                game.database_id = previous_id
                raise

    def delete(self, game: Game) -> None:
        """Elimina una partida de la base de datos."""
        with self.conn:
            cursor = self.conn.cursor()
            if game.database_id is not None:
                cursor.execute("DELETE FROM games WHERE id = ?", (game.database_id,))
                game.database_id = None

    def load_game(
        self,
        *,
        game_id: int | None = None,
        name: str | None = None,
        channel_id: int | None = None,
    ) -> Game:
        """Carga una partida de la base de datos."""
        cursor = self.conn.cursor()
        columns = [
            item.name
            for item in fields(Game)
            if item.name
            not in (
                "database_id",
                "players",
                "scenario",
                "map",
                "turn_events",
                "pending_exchanges",
            )
        ]
        select_clause = ", ".join(["id"] + columns)

        if game_id is not None:
            cursor.execute(
                f"SELECT {select_clause} FROM games WHERE id = ?", (game_id,)
            )
        elif name is not None:
            cursor.execute(f"SELECT {select_clause} FROM games WHERE name = ?", (name,))
        elif channel_id is not None:
            cursor.execute(
                f"SELECT {select_clause} FROM games WHERE channel_id = ?", (channel_id,)
            )
        else:
            raise ValueError("Debes proporcionar al menos un criterio de búsqueda.")

        game_row = cursor.fetchone()
        if not game_row:
            raise GameNotFoundException("No se encontró ninguna partida.")

        init_kwargs = {
            columns[index]: game_row[index + 1] for index in range(len(columns))
        }
        for column in ("famine", "independent_garrisons", "besieges"):
            init_kwargs[column] = (
                json.loads(init_kwargs[column]) if init_kwargs[column] else []
            )

        game = Game(**init_kwargs)
        game.database_id = game_row[0]

        from machiavelli.repositories.exchange_repository import ExchangeRepository
        from machiavelli.repositories.player_repository import PlayerRepository

        game.players = PlayerRepository(self.conn).get_by_game(game)
        game.pending_exchanges = ExchangeRepository(self.conn).get_by_game(game)
        cursor.execute(
            """
            SELECT id, event_type, data_json
            FROM game_events
            WHERE game_id = ?
            ORDER BY id ASC
            """,
            (game.database_id,),
        )
        game.turn_events = [
            TurnEvent.from_persisted(
                row_id=row[0],
                event_type=row[1],
                data_json=row[2],
            )
            for row in cursor.fetchall()
        ]

        if game.scenario_id:
            scenarios = Scenario.load_scenarios()
            try:
                game.scenario = scenarios[game.scenario_id]
            except KeyError as error:
                raise ValueError(
                    f"Escenario persistido desconocido: {game.scenario_id}"
                ) from error
            excluded_locations = game.scenario.excluded_locations
            fortress_active = game.scenario.rules.fortress_active
        else:
            game.scenario = None
            excluded_locations = None
            fortress_active = True
        game.map = Map.load_map(
            exclude_ids=excluded_locations,
            fortress_active=fortress_active,
        )
        return game

    def get_by_id(self, game_id: int) -> Game:
        """Carga una partida mediante su identificador de SQLite."""
        return self.load_game(game_id=game_id)

    def get_by_name(self, name: str) -> Game:
        """Carga una partida mediante su nombre único."""
        return self.load_game(name=name)

    def get_by_channel(self, channel_id: int) -> Game:
        """Carga una partida mediante el identificador de su canal de Discord."""
        return self.load_game(channel_id=channel_id)
