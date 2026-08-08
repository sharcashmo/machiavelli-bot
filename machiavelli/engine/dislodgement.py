# machiavelli/engine/dislodgement.py

import logging
import random
from collections.abc import Mapping

from ..game.game import Game
from ..game.map import Map, MovementMode
from ..game.player import Player
from .military import (
    DislodgementDecision,
    MilitaryResolution,
    UnitKey,
    UnitOutcome,
    conflict_location,
)

logger = logging.getLogger(__name__)


class RetreatHandler:
    def __init__(self, game: Game):
        self.game = game
        self.players = {player.player_id: player for player in game.players}

    @property
    def map(self) -> Map:
        """Devuelve el mapa de la partida."""
        return self.game.require_map()

    def _preferred_retreat(
        self, outcome: UnitOutcome, invalid_destinations: set[str]
    ) -> DislodgementDecision:
        """Devuelve la retirada preferida por la unidad."""
        unit: UnitKey = outcome.unit

        decision: DislodgementDecision = DislodgementDecision("disband", None)
        destination = None

        logger.debug(
            "Preferred retreat. Outcome: %s. Invalid destionations: %s",
            outcome,
            invalid_destinations,
        )

        # Las guarniciones independientes no se retiran
        if unit.player_id is None:
            return decision

        player: Player = self.players[unit.player_id]

        if unit.unit_type == "A":
            adjacent_locations = self.map.adjacent_locations(
                origin=unit.origin, mode=MovementMode.LAND
            )
        elif unit.unit_type == "F":
            adjacent_locations = self.map.adjacent_locations(
                origin=unit.origin, mode=MovementMode.SEA
            )
        else:
            adjacent_locations = set()

        adjacent_locations: list[str] = [
            location
            for location in adjacent_locations
            if location not in invalid_destinations
        ]

        if adjacent_locations:
            # Tenemos lugares de retirada
            # Ordenamos los lugares de retirada por
            # 1. Está controlado
            # 2. Es del país natal del jugador
            random.shuffle(adjacent_locations)

            hc_provinces = self.game.scenario.home_countries_provinces(
                player.home_countries
            )
            controlled_provinces = player.controlled_locations

            # Busco que esté controlada y sea del país natal
            destination = next(
                (
                    d
                    for d in adjacent_locations
                    if d.split()[0] in hc_provinces
                    and d.split()[0] in controlled_provinces
                ),
                None,
            )

            if not destination:
                # Busco una controlada
                destination = next(
                    (
                        d
                        for d in adjacent_locations
                        if d.split()[0] in controlled_provinces
                    ),
                    None,
                )

            if not destination:
                # Busco una del país natal
                destination = next(
                    (d for d in adjacent_locations if d.split()[0] in hc_provinces),
                    None,
                )
            if not destination:
                # Una cualquiera adyacente
                destination = adjacent_locations[0]

            decision = DislodgementDecision("retreat", destination)
            invalid_destinations.add(conflict_location(destination, unit.unit_type))

        elif unit.origin in self.map.provinces:
            # No tenemos, pero quizá podamos retirarnos a la ciudad
            province = self.map.provinces[unit.origin]
            temptative_destination = conflict_location(unit.origin, "G")
            if (
                # No hay una guarnición en nuestra provincia
                temptative_destination not in invalid_destinations
                and
                # Tiene una ciudad fortificada o un fuerte
                (
                    province.city == "fortified"
                    or province.city == "fortress"
                    and self.game.scenario.rules.fortress_active
                )
            ):
                if unit.unit_type == "A" or unit.unit_type == "F" and province.has_port:
                    # Nos retiramos al fuerte
                    destination = conflict_location(unit.origin, unit.unit_type)
                    decision = DislodgementDecision("garrison", destination)
                    invalid_destinations.add(conflict_location(destination, "G"))

        logger.debug(
            "Output. Outcome: %s. Invalid destionations: %s. Destination: %s",
            outcome,
            invalid_destinations,
            decision,
        )

        if destination:
            invalid_destinations.add(
                conflict_location(destination, outcome.final_unit_type)
            )

        return decision

    def __call__(self, resolution: MilitaryResolution) -> Mapping[UnitKey, str | None]:
        """Resuelve las retiradas del combate."""
        retreats: dict[UnitKey, DislodgementDecision] = {}

        # Comenzamos haciendo la lista de localizaciones no válidas para retiradas
        logger.debug("Dislodgement handler. resolution: %s", resolution)
        invalid_destinations = {
            conflict_location(outcome.final_location, outcome.final_unit_type)
            for outcome in resolution.outcomes
            if not outcome.dislodged
        }
        invalid_destinations |= resolution.contested_locations

        # Ahora calculamos todas las retiradas
        dislodges = [outcome for outcome in resolution.outcomes if outcome.dislodged]

        # Recorremos la tupla de unidades en retirada en orden aleatorio
        for outcome in random.sample(dislodges, len(dislodges)):
            retreats[outcome.unit] = self._preferred_retreat(
                outcome, invalid_destinations
            )

        return retreats
