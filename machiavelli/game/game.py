"""Canonical game aggregate and persistence compatibility facade."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from typing import Self

from machiavelli.events import TurnEvent

from .command import Command
from .exceptions import (
    DuplicatedGameException,
    DuplicatePlayerException,
    FailedToStartError,
    GameNotFoundException,
    PlayerNotFoundException,
)
from .map import Map
from .player import Player
from .scenario import Scenario
from .trading import ExchangeProposal


@dataclass
class Game:
    """Represent a complete Machiavelli game aggregate."""

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
        """Return the loaded map or fail fast for an invalid game state."""
        game_map = self.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        return game_map

    def require_scenario(self) -> Scenario:
        """Return the loaded scenario or fail fast for an invalid game state."""
        scenario = self.scenario
        if scenario is None:
            raise RuntimeError("La partida requiere un escenario cargado")
        return scenario

    def add_player(self, player_id: str, discord_id: int | None = None) -> Player:
        """Create and register one canonical player in this game aggregate."""
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
        """Remove and return the player linked to a Discord account."""
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
        """Advance lifecycle metadata after a successfully completed engine run."""
        self.turn_number += 1
        if self.next_deadline:
            deadline = datetime.fromisoformat(self.next_deadline)
            self.next_deadline = (deadline + timedelta(weeks=1)).strftime(
                "%Y-%m-%d %H:%M"
            )
        for player in self.players:
            player.commands.clear()
        self.pending_exchanges.clear()

    def save(self, conn: sqlite3.Connection) -> None:
        """Persist the complete aggregate using the caller's transaction."""
        cursor = conn.cursor()
        columns = [
            item.name
            for item in fields(self)
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
        values = [getattr(self, column) for column in columns]

        for column, value in (
            ("famine", self.famine),
            ("independent_garrisons", self.independent_garrisons),
            ("besieges", self.besieges),
        ):
            columns.append(column)
            values.append(json.dumps(value))

        if self.database_id is None:
            try:
                placeholders = ", ".join(["?"] * len(columns))
                query = (
                    f"INSERT INTO games ({', '.join(columns)}) VALUES ({placeholders})"
                )
                cursor.execute(query, tuple(values))
                self.database_id = cursor.lastrowid
            except sqlite3.IntegrityError as error:
                raise DuplicatedGameException(
                    "No se pudo crear la partida. "
                    f"El nombre '{self.name}' o el canal "
                    f"'{self.channel_id}' ya están en uso."
                ) from error
        else:
            set_clause = ", ".join([f"{column} = ?" for column in columns])
            query = f"UPDATE games SET {set_clause} WHERE id = ?"
            cursor.execute(query, tuple(values) + (self.database_id,))

        from machiavelli.repositories.exchange_repository import ExchangeRepository
        from machiavelli.repositories.player_repository import PlayerRepository

        PlayerRepository(conn).replace_for_game(self)
        ExchangeRepository(conn).replace_for_game(self)

        cursor.execute("DELETE FROM game_events WHERE game_id = ?", (self.database_id,))
        if self.turn_events:
            if not all(isinstance(event, TurnEvent) for event in self.turn_events):
                raise TypeError("El historial solo admite TurnEvent")
            cursor.executemany(
                """
                INSERT INTO game_events (game_id, event_type, data_json)
                VALUES (?, ?, ?)
                """,
                [
                    (self.database_id, event.type.value, event.to_json())
                    for event in self.turn_events
                ],
            )

    def report_status(
        self,
        safe_text: Callable[[str], str] = str,
    ) -> list[str]:
        """Return the current public game status as report lines."""
        report = [f"## __**Partida**: {safe_text(self.name)}__"]
        report.append(
            f"**Escenario:** {self.scenario.name if self.scenario else 'Por definir'}."
        )
        report.append(
            f"**Horario de los turnos:** {self.weekly_deadline or 'Por definir'}."
        )

        if self.turn_number == 0:
            report.append("### __**Estado:** Por comenzar.__")
            if self.players:
                players = ", ".join(
                    f"<@{player.discord_id}> ({safe_text(player.player_id)})"
                    for player in self.players
                )
                if self.scenario:
                    report.append(
                        f"**Jugadores {len(self.players)}/"
                        f"{len(self.scenario.powers)}:** {players}"
                    )
                else:
                    report.append(f"**Jugadores {len(self.players)}:** {players}")
            else:
                report.append("**Jugadores:** Ninguno")
        else:
            if self.scenario is None:
                raise ValueError("Una partida iniciada debe tener escenario")
            year = (self.turn_number - 1) // 4 + self.scenario.year
            season = (
                "Primavera (mantenimiento)",
                "Primavera (campaña)",
                "Verano",
                "Otoño",
            )[(self.turn_number - 1) % 4]
            report.append(f"### __**Estado:** {season} de {year}__")
            report.append("**Han enviado sus órdenes:**")
            ordered_players = [player for player in self.players if player.commands]
            if ordered_players:
                report.extend(
                    f"- <@{player.discord_id}> ({safe_text(player.player_id)})"
                    for player in ordered_players
                )
            else:
                report.append("- Nadie :wink:.")

        report.append(f"**Próximo turno:** {self.next_deadline or 'Por definir'}.")
        return report

    @classmethod
    def create_game(cls, name: str, channel_id: int, conn: sqlite3.Connection) -> Self:
        """Create and insert a game through the historical persistence facade."""
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO games (name, channel_id) VALUES (?, ?)",
                (name, channel_id),
            )
        except sqlite3.IntegrityError as error:
            raise DuplicatedGameException(
                f"No se pudo crear la partida. El nombre '{name}' o el canal "
                f"'{channel_id}' ya están en uso."
            ) from error
        return cls(name=name, channel_id=channel_id, database_id=cursor.lastrowid)

    @classmethod
    def load_game(
        cls,
        conn: sqlite3.Connection,
        *,
        game_id: int | None = None,
        name: str | None = None,
        channel_id: int | None = None,
    ) -> Self:
        """Load a complete and internally consistent aggregate from SQLite."""
        cursor = conn.cursor()
        columns = [
            item.name
            for item in fields(cls)
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

        game = cls(**init_kwargs)
        game.database_id = game_row[0]

        from machiavelli.repositories.exchange_repository import ExchangeRepository
        from machiavelli.repositories.player_repository import PlayerRepository

        game.players = PlayerRepository(conn).get_by_game(game)
        game.pending_exchanges = ExchangeRepository(conn).get_by_game(game)
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
        else:
            game.scenario = None
            excluded_locations = None
        game.map = Map.load_map(exclude_ids=excluded_locations)
        return game

    def add_event(self, turn_event: TurnEvent) -> None:
        """Append one validated event without serializing or rendering it."""
        if not isinstance(turn_event, TurnEvent):
            raise TypeError("El historial solo admite TurnEvent")
        self.turn_events.append(turn_event)

    def get_unit_owner(self, unit_id: str) -> Player | None:
        """Return the owner of a unit, or None for an independent garrison."""
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


def __getattr__(name: str) -> object:
    """Resolve temporary compatibility exports without creating import cycles."""
    if name == "TooManyExpenses":
        from machiavelli.engine.exceptions import TooManyExpenses

        return TooManyExpenses
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Command",
    "DuplicatePlayerException",
    "DuplicatedGameException",
    "FailedToStartError",
    "Game",
    "GameNotFoundException",
    "Player",
    "PlayerNotFoundException",
]
