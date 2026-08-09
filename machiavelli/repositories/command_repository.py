"""Persistencia SQLite de objetos de dominio canónicos :class:`Command`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from machiavelli.game.command import Command
from machiavelli.game.player import Player


class CommandRepository:
    """Traduce entre comandos canónicos y filas de SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def _player_identity(player: Player) -> tuple[int, str]:
        game_id = player.game.database_id
        if game_id is None:
            raise ValueError("No se pueden persistir órdenes de una partida sin ID")
        return game_id, player.player_id

    @classmethod
    def _command_row(
        cls,
        command: Command,
    ) -> tuple[int, str, str, str, str | None]:
        if command.game is not command.player.game:
            raise ValueError("La orden y el jugador pertenecen a partidas distintas")
        game_id, player_id = cls._player_identity(command.player)
        return (
            game_id,
            player_id,
            command.actor,
            command.command,
            command.target,
        )

    def _save(self, command: Command) -> None:
        self.conn.execute(
            """
            INSERT INTO commands (game_id, player_id, actor, command, target)
            VALUES (?, ?, ?, ?, ?)
            """,
            self._command_row(command),
        )

    def _save_many(self, commands: Iterable[Command]) -> None:
        rows = [self._command_row(command) for command in commands]
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT INTO commands (game_id, player_id, actor, command, target)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _delete_by_player(self, player: Player) -> None:
        game_id, player_id = self._player_identity(player)
        self.conn.execute(
            "DELETE FROM commands WHERE game_id = ? AND player_id = ?",
            (game_id, player_id),
        )

    def save(self, command: Command) -> None:
        """Guarda un comando sin confirmar una transacción envolvente."""
        if self.conn.in_transaction:
            self._save(command)
            return
        with self.conn:
            self._save(command)

    def save_many(self, commands: Iterable[Command]) -> None:
        """Guarda los comandos atómicamente y conserva el orden del iterable."""
        if self.conn.in_transaction:
            self._save_many(commands)
            return
        with self.conn:
            self._save_many(commands)

    def get_by_player(self, player: Player) -> list[Command]:
        """Carga los comandos de un jugador en su orden relativo persistido."""
        game_id, player_id = self._player_identity(player)
        rows = self.conn.execute(
            """
            SELECT actor, command, target
            FROM commands
            WHERE game_id = ? AND player_id = ?
            ORDER BY commands.id ASC
            """,
            (game_id, player_id),
        ).fetchall()
        return [
            Command(
                game=player.game,
                player=player,
                actor=row[0],
                command=row[1],
                target=row[2],
            )
            for row in rows
        ]

    def delete_by_player(self, player: Player) -> None:
        """Elimina comandos sin confirmar una transacción envolvente."""
        if self.conn.in_transaction:
            self._delete_by_player(player)
            return
        with self.conn:
            self._delete_by_player(player)
