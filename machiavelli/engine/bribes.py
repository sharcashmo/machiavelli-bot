# machiavelli/engine/bribes.property

import logging
from collections import defaultdict
from dataclasses import dataclass

from ..game.events import EventType, TurnEvent
from ..game.game import Command, Game, Player
from ..game.map import Map, MovementMode
from ..game.tables import GameTables

logger = logging.getLogger(__name__)


@dataclass
class Bribe:
    """Define un soborno (o contrasoborno) para una unidad.

    Attributes:
        target (str): Identificador de la unidad sobornada, con el formato '<type> <id>'
        owner (Player): Jugador propietario de la unidad, o None si es independiente
        actor (Player): Jugador que realizó el soborno.
        amount (int): Importe del soborno.
        command (str): Comando utilizado para el soborno.
    """

    target: str
    owner: Player | None
    actor: Player
    amount: int
    command: str


class BribeResolver:
    """Responsable de la gestión de los sobornos."""

    def __init__(self, game: Game):
        self.game = game
        self.bribes: dict[str, list[Bribe]] = defaultdict(list)
        self.counterbribes: dict[str, int] = {}

    # Funciones auxiliares

    def _map(self) -> Map:
        """Devuelve el mapa activo conservando la interfaz histórica de Game."""
        game_map = self.game.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        return game_map

    def _check_adjacent(self, player: Player, target: str) -> bool:
        """Comprueba si el jugador tiene alguna unidad adyacente al objetivo.

        Args:
            player (Player): Jugador que realiza la acción.
            target (str): Código de la provincia donde se dirige la acción.

        Returns:
            bool: True si player tiene alguna unidad adyacente, False en caso contrario.
        """
        adj_locations = self._map().adjacent_locations(
            origin=target, mode=MovementMode.BOTH
        )

        if any(a in adj_locations for a in player.armies):
            return True
        if any(f.split()[0] in adj_locations for f in player.fleets):
            return True
        return any(g in adj_locations for g in player.garrisons)

    # Funciones principales
    def expense_counterbribe(self, player: Player, command: Command) -> None:
        """Contra-soborno."""
        # El contrasoborno no exige adyacencia
        target = command.target
        if target is None:
            return
        value = self.counterbribes.get(target, 0)
        if int(command.command) > value:
            self.counterbribes[target] = int(command.command)

    def expense_bribe(self, player: Player, command: Command) -> None:
        """Procesa la orden de soborno de un jugador."""
        # Parseo del objetivo
        if command.target is None:
            return
        try:
            parts = command.target.split(maxsplit=1)
            target_type = parts[0]
            target_id = parts[1].split()[0]
            target_key = f"{target_type} {target_id}"
            exp_type = command.actor.split()[1]
        except (IndexError, AttributeError) as e:
            logger.warning(
                "Sintaxis de comando de soborno inválida del jugador '%s'. "
                "Comando: '%s' (Error: %s)",
                getattr(player, "player_id", player),
                command,
                e,
            )
            return

        # Comprobación de adyacencia
        if not self._check_adjacent(player, target_id):
            logger.info(
                "Soborno de '%s' descartado: objetivo '%s' no adyacente.",
                player.player_id,
                target_id,
            )
            return

        # Consulta de propietario y captura de errores de dominio
        try:
            target_owner = self.game.get_unit_owner(target_key)
        except ValueError as e:
            logger.warning(
                "Soborno inválido de '%s': el objetivo '%s' no existe en el mapa (%s).",
                player.player_id,
                target_key,
                e,
            )
            return

        # Ahora tenemos que comprobar que el objetivo es válido
        is_valid = (
            (exp_type in ("G", "H") and target_owner is None)
            or (
                exp_type == "I"
                and target_type == "G"
                and target_owner not in (None, player)
            )
            or (exp_type == "J" and target_owner not in (None, player))
            or (
                exp_type == "K" and target_type in ("A", "F") and target_owner != player
            )
        )
        if is_valid:
            self.bribes[target_key].append(
                Bribe(
                    target=target_key,
                    owner=target_owner,
                    actor=player,
                    amount=int(command.command),
                    command=exp_type,
                )
            )

    def execute_bribe(self, bribe: Bribe) -> None:
        """Aplica los efectos de un soborno"""
        target_type, target_location = bribe.target.split(maxsplit=1)
        target_key = target_location.split()[0]

        self.game.add_event(
            TurnEvent.expense(
                EventType.BRIBE_EXECUTED,
                actor=bribe.actor.player_id,
                expense_type=bribe.command,
                target=bribe.target,
                amount=bribe.amount,
            ),
        )

        owner = bribe.owner
        if bribe.command in {"I", "J", "K"} and owner is None:
            raise ValueError("El soborno requiere una unidad con propietario")

        if bribe.command == "G":
            # Desbandar guarnición autónoma
            self.game.independent_garrisons.remove(target_key)
        elif bribe.command == "H":
            # Comprar guarnición autónoma
            self.game.independent_garrisons.remove(target_key)
            bribe.actor.garrisons.append(target_key)
        elif bribe.command == "I":
            # Convertir guarnición en autónoma
            if owner is None:
                raise ValueError("La guarnición sobornada no tiene propietario")
            owner.garrisons.remove(target_key)
            self.game.independent_garrisons.append(target_key)
        elif bribe.command == "J":
            # Desbandar unidad
            if owner is None:
                raise ValueError("La unidad sobornada no tiene propietario")
            if target_type == "G":
                owner.garrisons.remove(target_key)
            elif target_type == "A":
                owner.armies.remove(target_key)
            elif target_type == "F":
                owner.fleets = [
                    fleet for fleet in owner.fleets if fleet.split()[0] != target_key
                ]
        elif bribe.command == "K":
            # Comprar ejército o flota
            if owner is None:
                raise ValueError("La unidad sobornada no tiene propietario")
            if target_type == "A":
                owner.armies.remove(target_key)
                bribe.actor.armies.append(target_key)
            elif target_type == "F":
                fleet = [
                    fleet for fleet in owner.fleets if fleet.split()[0] == target_key
                ][0]
                owner.fleets.remove(fleet)
                bribe.actor.fleets.append(fleet)

    def resolve_bribes(self) -> None:
        """Resuelve las sobornos de los jugadores."""
        # Recorremos todos los sobornos
        for target, bribes in self.bribes.items():
            if not bribes:
                continue
            # Sacamos el mayor importe
            max_amount = max(b.amount for b in bribes)
            # y nos quedamos solo con ellos
            top_bribes = [b for b in bribes if b.amount == max_amount]
            if len(top_bribes) > 1:
                # Si hay algún soborno para comprar la unidad, se cancelan
                if any(b.command in ("H", "K") for b in top_bribes):
                    continue
                # Si ninguno es de compra, necesito que sean del mismo tipo
                first_cmd = top_bribes[0].command
                if any(b.command != first_cmd for b in top_bribes):
                    continue
                # Si son todos iguales, cojo cualquiera
                final_bribe = top_bribes[0]
            else:
                final_bribe = top_bribes[0]

            # Comprobamos ahora si el importe neto es suficiente para el soborno
            counterbribe = self.counterbribes.get(target, 0)
            effective_amount = final_bribe.amount - counterbribe
            cost = GameTables.expenses[final_bribe.command]["cost"]

            # Los sobornos que afectan a guarniciones de ciudades mayores se doblan
            target_type, target_location = target.split(maxsplit=1)
            target_id = target_location.split()[0]
            is_major = (self._map().provinces[target_id].major_city or 0) > 1

            if target_type == "G" and is_major:
                cost *= 2

            # Si no llegamos al coste, continuamos
            if effective_amount < cost:
                continue

            # Ahora, aplicamos los efectos del soborno
            self.execute_bribe(final_bribe)

    COUNTERBRIBE_EXPENSE_TYPES = {"F"}
    BRIBE_EXPENSE_TYPES = {"G", "H", "I", "J", "K"}

    def run(self) -> None:
        """Ejecuta todas las órdenes de soborno."""
        # Primero, registramos todos los sobornos
        self.bribes.clear()
        self.counterbribes.clear()

        for player in self.game.players:
            for command in player.commands:
                if command.is_valid_expense(self.COUNTERBRIBE_EXPENSE_TYPES):
                    self.expense_counterbribe(player, command)
                elif command.is_valid_expense(self.BRIBE_EXPENSE_TYPES):
                    self.expense_bribe(player, command)

        # Y los resolvemos
        self.resolve_bribes()

        return
