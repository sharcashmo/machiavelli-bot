"""Persistencia de objetos de clase Event."""

from __future__ import annotations

import sqlite3


class TurnEventsRepository:
    """Guarda y carga eventos."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Crea el repositorio a partir de una conexión de sqlite."""
        self.conn = conn

    def replace_for_game(self, game) -> None:
        """Actualiza los datos de los eventos de una partida."""
        cursor = self.conn.cursor()

        # Los eventos se sustituyen completamente
        cursor.execute("DELETE FROM game_events WHERE game_id = ?", (game.database_id,))
        if game.turn_events:
            cursor.executemany(
                """
                INSERT INTO game_events (game_id, event_type, data_json)
                VALUES (?, ?, ?)
                """,
                [
                    (game.database_id, event.type.value, event.to_json())
                    for event in game.turn_events
                ],
            )
