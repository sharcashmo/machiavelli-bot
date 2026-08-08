"""Modelo de dominio para los comandos de los jugadores."""

from __future__ import annotations

import sqlite3
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

    def save(self, conn: sqlite3.Connection) -> None:
        """Guarda este comando mediante la fachada de compatibilidad del repositorio."""
        from machiavelli.repositories.command_repository import CommandRepository

        CommandRepository(conn).save(self)

    @classmethod
    def load_commands(
        cls,
        conn: sqlite3.Connection,
        game: Game,
        player: Player,
    ) -> list[Command]:
        """Carga los comandos de un jugador."""
        from machiavelli.repositories.command_repository import CommandRepository

        if player.game is not game:
            raise ValueError("El jugador no pertenece a la partida indicada")
        return CommandRepository(conn).get_by_player(player)

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

    def __repr__(self) -> str:
        return (
            f"Command(actor={self.actor!r}, command={self.command!r}, "
            f"target={self.target!r})"
        )

    def __str__(self) -> str:
        """Devuelve una representación legible del comando."""
        game_map = self.game.require_map()
        provinces = game_map.provinces
        seas = game_map.seas
        locations = provinces | seas

        try:
            report: list[str] = []
            target_type: str | None = None
            actor_type, actor_id = self.actor.split(maxsplit=1)

            if actor_type in ("A", "F", "G"):
                report.append(
                    f"{GameTables.actors[actor_type]} de {locations[actor_id].name}"
                )
            elif actor_type == "E":
                report.append(GameTables.expenses[actor_id]["text"])
                target_type = GameTables.expenses[actor_id]["target_type"]

            if actor_type in ("A", "F", "G"):
                if self.game.turn_number % 4 == 1:
                    order = GameTables.maintenance_orders[self.command]
                else:
                    order = GameTables.military_orders[self.command]
                report.append(order["text"])
                target_type = order["target_type"]

            if target_type:
                assert self.target is not None
                if target_type == "army_ext":
                    army_ext = self.target.split()
                    if len(army_ext) > 2:
                        report.append(
                            f"{GameTables.actors[army_ext[0]]} "
                            f"de {provinces[army_ext[1]].name} "
                            f"({GameTables.powers[army_ext[2]]})"
                        )
                    else:
                        report.append(
                            f"{GameTables.actors[army_ext[0]]} "
                            f"de {provinces[army_ext[1]].name}"
                        )
                elif target_type == "location":
                    report.append(locations[self.target].name)
                elif target_type == "location_ext":
                    location_ext = self.target.split()
                    if len(location_ext) > 1:
                        report.append(
                            f"{locations[location_ext[0]].name} "
                            f"({GameTables.powers[location_ext[1]]})"
                        )
                    else:
                        report.append(locations[location_ext[0]].name)
                elif target_type == "province":
                    report.append(provinces[self.target].name)
                elif target_type == "power":
                    report.append(GameTables.powers[self.target])
                elif target_type == "unit":
                    unit_ext = self.target.split()
                    report.append(
                        f"{GameTables.actors[unit_ext[0]]} "
                        f"de {provinces[unit_ext[1]].name}"
                    )
                elif target_type == "unit_type":
                    if self.target == "0":
                        report.append("Desbandar")
                    else:
                        report.append(GameTables.actors[self.target])

            if actor_type == "E":
                report.append(f"{self.command} ducados")

            return "|".join(report)
        except Exception:
            return "Orden inválida"
