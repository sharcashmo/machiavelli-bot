"""Mantiene los datos de una partida."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .events import TurnEvent
from .exceptions import (
    DuplicatePlayerException,
    PlayerNotFoundException,
)
from .map import Map
from .player import Player
from .scenario import Scenario
from .trading import ExchangeProposal


@dataclass
class Game:
    """Representa un agregado completo de partida de Machiavelli."""

    name: str
    channel_id: int | None = None
    database_id: int | None = None
    scenario_id: str | None = None
    turn_number: int = 0
    weekly_deadline: str | None = None
    next_deadline: str | None = None
    players: list[Player] = field(default_factory=list)
    scenario: Scenario | None = None
    map: Map | None = None
    famine: list[str] = field(default_factory=list)
    independent_garrisons: list[str] = field(default_factory=list)
    besieges: list[str] = field(default_factory=list)
    turn_events: list[TurnEvent] = field(default_factory=list)
    pending_exchanges: list[ExchangeProposal] = field(default_factory=list)

    def require_map(self) -> Map:
        """Devuelve el mapa cargado o lanza un error inmediatamente si el estado de la
        partida no es válido.
        """
        game_map = self.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        return game_map

    def require_scenario(self) -> Scenario:
        """Devuelve el escenario cargado o lanza un error inmediatamente si el estado de
        la partida no es válido.
        """
        scenario = self.scenario
        if scenario is None:
            raise RuntimeError("La partida requiere un escenario cargado")
        return scenario

    def get_player(
        self, *, player_id: str = None, discord_id: int = None, power_id: str = None
    ) -> Player:
        """Devuelve un jugador por su id, su id de discord, o su power_id."""
        if sum(1 for p in (player_id, discord_id, power_id) if p is not None) != 1:
            return None
        player = None
        if player_id:
            player = next(
                (player for player in self.players if player.player_id == player_id),
                None,
            )
        elif discord_id:
            player = next(
                (player for player in self.players if player.discord_id == discord_id),
                None,
            )
        elif power_id:
            player = next(
                (player for player in self.players if player.power == power_id),
                None,
            )
        return player

    def add_player(self, player_id: str, discord_id: int | None = None) -> Player:
        """Crea y registra un jugador canónico en este agregado de partida."""
        if any(player.player_id == player_id for player in self.players):
            raise DuplicatePlayerException(
                f"El jugador '{player_id}' ya está inscrito en la partida."
            )
        if discord_id is not None and any(
            player.discord_id == discord_id for player in self.players
        ):
            raise DuplicatePlayerException(
                f"La cuenta de Discord '{discord_id}' ya está inscrita en la partida."
            )

        player = Player(game=self, player_id=player_id, discord_id=discord_id)
        self.players.append(player)
        return player

    def remove_player(self, discord_id: int) -> Player:
        """Elimina y devuelve el jugador vinculado a una cuenta de Discord."""
        player = next(
            (
                candidate
                for candidate in self.players
                if candidate.discord_id == discord_id
            ),
            None,
        )
        if player is None:
            raise PlayerNotFoundException(
                f"La cuenta de Discord '{discord_id}' no pertenece a la partida."
            )
        self.players.remove(player)
        return player

    def advance_turn(self) -> None:
        """Actualiza los datos con el avance de turno"""
        self.turn_number += 1
        if self.next_deadline:
            deadline = datetime.fromisoformat(self.next_deadline)
            self.next_deadline = (deadline + timedelta(weeks=1)).strftime(
                "%Y-%m-%d %H:%M"
            )
        for player in self.players:
            player.commands.clear()
        self.pending_exchanges.clear()

    def add_event(self, turn_event: TurnEvent) -> None:
        """Añade un evento."""
        self.turn_events.append(turn_event)

    def get_unit_owner(self, unit_id: str) -> Player | None:
        """Devuelve el propietario de una unidad o None para una guarnición
        independiente.
        """
        parts = unit_id.split(" ", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Formato de identificador de unidad inválido: '{unit_id}'"
            )
        unit_type, base_location = parts
        if unit_type not in ("A", "F", "G"):
            raise ValueError(f"Tipo de unidad desconocido: '{unit_type}'")

        for player in self.players:
            units = {
                "A": player.armies,
                "F": player.fleets,
                "G": player.garrisons,
            }[unit_type]
            if any(unit.split()[0] == base_location for unit in units):
                return player

        if unit_type == "G" and base_location in self.independent_garrisons:
            return None
        raise ValueError(f"No existe ninguna unidad '{unit_id}' en el juego.")
