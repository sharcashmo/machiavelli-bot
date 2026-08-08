"""Persistencia SQLite de objetos de dominio canónicos :class:`Player`."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from machiavelli.game.player import Player

from .command_repository import CommandRepository

if TYPE_CHECKING:
    from machiavelli.game.game import Game


class PlayerRepository:
    """Traduce entre jugadores canónicos y filas de SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.command_repo = CommandRepository(conn)

    @staticmethod
    def _game_id(player: Player) -> int:
        game_id = player.game.database_id
        if game_id is None:
            raise ValueError("No se puede persistir un jugador de una partida sin ID")
        return game_id

    @staticmethod
    def _decode_list(value: Any) -> list[str]:
        if value is None or value == "":
            return []
        decoded = json.loads(value)
        if not isinstance(decoded, list) or not all(
            isinstance(item, str) for item in decoded
        ):
            raise ValueError(
                "El estado JSON del jugador no contiene una lista de texto"
            )
        return decoded

    def _upsert(self, player: Player) -> None:
        game_id = self._game_id(player)
        self.conn.execute(
            """
            INSERT INTO players (
                game_id, player_id, discord_id, controlled_locations,
                armies, fleets, garrisons, ass_counters, ducats,
                rebelled_provinces, rebelled_cities, home_countries, power
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, player_id) DO UPDATE SET
                discord_id = excluded.discord_id,
                controlled_locations = excluded.controlled_locations,
                armies = excluded.armies,
                fleets = excluded.fleets,
                garrisons = excluded.garrisons,
                ass_counters = excluded.ass_counters,
                ducats = excluded.ducats,
                rebelled_provinces = excluded.rebelled_provinces,
                rebelled_cities = excluded.rebelled_cities,
                home_countries = excluded.home_countries,
                power = excluded.power
            """,
            (
                game_id,
                player.player_id,
                player.discord_id,
                json.dumps(player.controlled_locations),
                json.dumps(player.armies),
                json.dumps(player.fleets),
                json.dumps(player.garrisons),
                json.dumps(player.ass_counters),
                player.ducats,
                json.dumps(player.rebelled_provinces),
                json.dumps(player.rebelled_cities),
                json.dumps(player.home_countries),
                player.power,
            ),
        )

    def _replace_commands(self, player: Player) -> None:
        self.command_repo._delete_by_player(player)
        self.command_repo._save_many(player.commands)

    def _replace_for_game(self, game: Game) -> None:
        """Sincroniza la colección completa de jugadores persistida para una partida."""
        if game.database_id is None:
            raise ValueError("No se puede persistir una partida sin ID")

        player_ids: list[str] = []
        seen_player_ids: set[str] = set()
        discord_ids: set[int] = set()
        for player in game.players:
            if player.game is not game:
                raise ValueError("Todos los jugadores deben pertenecer a la partida")
            if player.player_id in seen_player_ids:
                raise ValueError(
                    f"Identificador de jugador duplicado: {player.player_id}"
                )
            if player.discord_id is not None and player.discord_id in discord_ids:
                raise ValueError(f"Cuenta de Discord duplicada: {player.discord_id}")
            player_ids.append(player.player_id)
            seen_player_ids.add(player.player_id)
            if player.discord_id is not None:
                discord_ids.add(player.discord_id)

        if player_ids:
            placeholders = ", ".join("?" for _ in player_ids)
            parameters = (game.database_id, *player_ids)
            self.conn.execute(
                f"DELETE FROM commands WHERE game_id = ? "
                f"AND player_id NOT IN ({placeholders})",
                parameters,
            )
            self.conn.execute(
                f"DELETE FROM players WHERE game_id = ? "
                f"AND player_id NOT IN ({placeholders})",
                parameters,
            )
        else:
            self.conn.execute(
                "DELETE FROM commands WHERE game_id = ?",
                (game.database_id,),
            )
            self.conn.execute(
                "DELETE FROM players WHERE game_id = ?",
                (game.database_id,),
            )

        for player in game.players:
            self._upsert(player)
            self._replace_commands(player)

    def replace_for_game(self, game: Game) -> None:
        """Guarda la colección canónica de jugadores sin realizar confirmaciones
        parciales.
        """
        if self.conn.in_transaction:
            self._replace_for_game(game)
            return
        with self.conn:
            self._replace_for_game(game)

    def save(self, player: Player) -> None:
        """Actualiza o inserta un jugador y sus comandos sin confirmar una transacción
        externa.
        """
        if self.conn.in_transaction:
            self._upsert(player)
            self._replace_commands(player)
            return
        with self.conn:
            self._upsert(player)
            self._replace_commands(player)

    def save_commands(self, player: Player) -> None:
        """Sustituye los comandos sin confirmar una transacción externa."""
        if self.conn.in_transaction:
            self._replace_commands(player)
            return
        with self.conn:
            self._replace_commands(player)

    def get_by_game(self, game: Game) -> list[Player]:
        """Carga todos los jugadores y sus comandos de una partida persistida."""
        if game.database_id is None:
            raise ValueError("No se pueden cargar jugadores de una partida sin ID")

        rows = self.conn.execute(
            """
            SELECT player_id, discord_id, controlled_locations, armies, fleets,
                garrisons, ass_counters, ducats, rebelled_provinces,
                rebelled_cities, home_countries, power
            FROM players
            WHERE game_id = ?
            ORDER BY rowid ASC
            """,
            (game.database_id,),
        ).fetchall()

        players: list[Player] = []
        for row in rows:
            player = Player(
                game=game,
                player_id=row[0],
                discord_id=row[1],
                controlled_locations=self._decode_list(row[2]),
                armies=self._decode_list(row[3]),
                fleets=self._decode_list(row[4]),
                garrisons=self._decode_list(row[5]),
                ass_counters=self._decode_list(row[6]),
                ducats=row[7],
                rebelled_provinces=self._decode_list(row[8]),
                rebelled_cities=self._decode_list(row[9]),
                home_countries=self._decode_list(row[10]),
                power=row[11],
            )
            player.commands = self.command_repo.get_by_player(player)
            players.append(player)

        return players
