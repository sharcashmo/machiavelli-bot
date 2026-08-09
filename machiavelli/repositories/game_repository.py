"""Fachada de repositorio para agregados :class:`Game` persistidos."""

from __future__ import annotations

import sqlite3

from machiavelli.game.game import Game


class GameRepository:
    """Guarda y carga partidas manteniendo explícita la propiedad de la transacción."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, game: Game) -> None:
        """Guarda una partida completa atómicamente, salvo que el llamador sea el
        propietario de la transacción.
        """
        original_database_id = game.database_id
        try:
            if self.conn.in_transaction:
                game.save(self.conn)
                return
            with self.conn:
                game.save(self.conn)
        except Exception:
            game.database_id = original_database_id
            raise

    def get_by_id(self, game_id: int) -> Game:
        """Carga una partida mediante su identificador de SQLite."""
        return Game.load_game(self.conn, game_id=game_id)

    def get_by_name(self, name: str) -> Game:
        """Carga una partida mediante su nombre único."""
        return Game.load_game(self.conn, name=name)

    def get_by_channel(self, channel_id: int) -> Game:
        """Carga una partida mediante el identificador de su canal de Discord."""
        return Game.load_game(self.conn, channel_id=channel_id)
