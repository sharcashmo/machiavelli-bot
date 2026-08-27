"""Modelo de dominio para los comandos de los jugadores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .tables import GameTables

if TYPE_CHECKING:
    from .game import Game
    from .player import Player


@dataclass(slots=True)
class Command:
    """Representa una orden emitida por un jugador en una partida."""

    game: Game
    player: Player
    actor: str
    command: str
    target: str | None = None

    @property
    def game_id(self) -> int | None:
        """Devuelve el identificador persistido de la partida, derivado del objeto de
        dominio.
        """
        return self.game.database_id

    @property
    def player_id(self) -> str:
        """Devuelve el identificador del jugador, derivado del objeto de dominio."""
        return self.player.player_id

    def is_valid_expense(
        self,
        allowed_types: set[str] | list[str] | None = None,
    ) -> bool:
        """Valida sintácticamente el comando como gasto."""
        actor = self.actor.split()
        if len(actor) != 2 or actor[0] != "E":
            return False
        if allowed_types is None:
            return actor[1] in GameTables.expenses
        return actor[1] in allowed_types
