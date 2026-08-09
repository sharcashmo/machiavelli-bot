"""Modelo de dominio de los jugadores de Machiavelli."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from .command import Command
from .scenario import Power, Scenario

if TYPE_CHECKING:
    from .game import Game


class TurnType(Enum):
    """Representa el tipo de fase en la que se envía una orden."""

    MAINTENANCE = "maintenance"
    CAMPAIGN = "campaign"


@dataclass
class Player:
    """Representa un jugador y todo su estado en la partida."""

    game: Game
    player_id: str
    discord_id: int | None = None
    controlled_locations: list[str] = field(default_factory=list)
    armies: list[str] = field(default_factory=list)
    fleets: list[str] = field(default_factory=list)
    garrisons: list[str] = field(default_factory=list)
    ass_counters: list[str] = field(default_factory=list)
    ducats: int = 0
    rebelled_provinces: list[str] = field(default_factory=list)
    rebelled_cities: list[str] = field(default_factory=list)
    home_countries: list[str] = field(default_factory=list)
    power: str | None = None
    commands: list[Command] = field(default_factory=list)

    @property
    def game_id(self) -> int | None:
        """Devuelve el identificador persistido de la partida, derivado de ``game``."""
        return self.game.database_id

    @property
    def power_id(self) -> str | None:
        """Alias de compatibilidad para el estado canónico ``power``."""
        return self.power

    def add_command(self, command: Command) -> None:
        """Añade exactamente el objeto de comando recibido."""
        self.commands.append(command)

    def remove_command(self, command: Command) -> None:
        """Elimina exactamente el objeto de comando recibido."""
        self.commands.remove(command)

    def assign_power(self, power: Power) -> None:
        """Asigna una potencia de dominio e inicializa el estado inicial del jugador."""
        power_id = getattr(power, "id", None)
        if not power_id and self.game.scenario is not None:
            power_id = next(
                (
                    candidate_id
                    for candidate_id, candidate in self.game.scenario.powers.items()
                    if candidate is power
                ),
                None,
            )

        self.power = power_id
        self.home_countries = list(power.home_countries)
        self.controlled_locations = list(power.controlled_provinces)
        self.armies = list(power.armies)
        self.fleets = list(power.fleets)
        self.garrisons = list(power.garrisons)

    def assign_power_from_scenario(
        self,
        power_id: str,
        power: Power,
        available_power_ids: Iterable[str],
    ) -> None:
        """Asigna una potencia cuando ya se conoce su identificador de escenario."""
        self.assign_power(power)
        self.power = power_id
        self.ass_counters = [
            candidate for candidate in available_power_ids if candidate != power_id
        ]

    def hc_provinces(self, scenario: Scenario | None = None) -> list[str]:
        """Devuelve las provincias controladas que pertenecen a los países natales del
        jugador.
        """
        active_scenario = scenario or self.game.scenario
        if active_scenario is None:
            raise ValueError("El jugador no tiene un escenario activo")
        provinces = active_scenario.home_countries_provinces(self.home_countries) or []
        return [
            province for province in self.controlled_locations if province in provinces
        ]

    def nonhc_provinces(self, scenario: Scenario | None = None) -> list[str]:
        """Devuelve las provincias controladas que están fuera de los países natales del
        jugador.
        """
        active_scenario = scenario or self.game.scenario
        if active_scenario is None:
            raise ValueError("El jugador no tiene un escenario activo")
        provinces = active_scenario.home_countries_provinces(self.home_countries) or []
        return [
            province
            for province in self.controlled_locations
            if province not in provinces
        ]

    def save(self, conn: sqlite3.Connection) -> None:
        """Guarda el jugador mediante la fachada de compatibilidad del repositorio."""
        from machiavelli.repositories.player_repository import PlayerRepository

        PlayerRepository(conn).save(self)

    def save_commands(self, conn: sqlite3.Connection) -> None:
        """Sustituye los comandos mediante la fachada de compatibilidad del repositorio.
        """
        from machiavelli.repositories.player_repository import PlayerRepository

        PlayerRepository(conn).save_commands(self)

    @classmethod
    def load_players(cls, conn: sqlite3.Connection, game: Game) -> list[Player]:
        """Carga los jugadores mediante la fachada de compatibilidad del repositorio."""
        from machiavelli.repositories.player_repository import PlayerRepository

        return PlayerRepository(conn).get_by_game(game)

    def player_report(self) -> list[str]:
        """Genera el informe público actual del jugador."""
        from machiavelli.services.player_reporter import PlayerReporter

        return PlayerReporter.generate_report(self)

    def cmd_available_actors(self) -> list[tuple[str, str]]:
        from machiavelli.services.player_interaction_service import (
            PlayerInteractionService,
        )

        return PlayerInteractionService(self).cmd_available_actors()

    def cmd_available_commands(self, actor: str) -> list[tuple[str, str]]:
        from machiavelli.services.player_interaction_service import (
            PlayerInteractionService,
        )

        return PlayerInteractionService(self).cmd_available_commands(actor)

    def cmd_available_targets(
        self,
        actor: str,
        command: str,
    ) -> list[tuple[str, str]]:
        from machiavelli.services.player_interaction_service import (
            PlayerInteractionService,
        )

        return PlayerInteractionService(self).cmd_available_targets(actor, command)

    def exp_available_expenses(self) -> list[tuple[str, str]]:
        from machiavelli.services.player_interaction_service import (
            PlayerInteractionService,
        )

        return PlayerInteractionService(self).exp_available_expenses()

    def exp_available_targets(self, expense: str) -> list[tuple[str, str]]:
        from machiavelli.services.player_interaction_service import (
            PlayerInteractionService,
        )

        return PlayerInteractionService(self).exp_available_targets(expense)

    def exp_available_amounts(
        self,
        expense: str,
        target: str,
    ) -> list[tuple[str, str]]:
        from machiavelli.services.player_interaction_service import (
            PlayerInteractionService,
        )

        return PlayerInteractionService(self).exp_available_amounts(expense, target)

    def cmd_add_command(
        self,
        turn_type: TurnType,
        command: Command,
    ) -> list[str]:
        """Fachada de compatibilidad sobre el procesador central de órdenes."""
        from machiavelli.engine.orders import OrderProcessor

        return OrderProcessor(self.game).process_command(self, turn_type, command)
