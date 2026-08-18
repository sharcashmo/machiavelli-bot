# machiavelli/engine/dislodgement.py

import logging
import random
from collections.abc import Mapping
from enum import IntEnum

from ..game.game import Game
from ..game.map import Map, MovementMode
from ..game.player import Player
from .military import (
    DecisionType,
    DislodgementDecision,
    MilitaryResolution,
    UnitKey,
    UnitOutcome,
    conflict_location,
)

logger = logging.getLogger(__name__)


class RetreatStep(IntEnum):
    CONTROLLED_HOME_COUNTRY = 0
    CONTROLLED = 1
    HOME_COUNTRY = 2
    ADJACENT = 3
    GARRISON = 4


class RetreatHandler:
    def __init__(self, game: Game):
        self.game = game
        self.players = {player.player_id: player for player in game.players}

    @property
    def map(self) -> Map:
        """Devuelve el mapa de la partida."""
        return self.game.require_map()

    def _preferred_retreat(
        self,
        retreat_step: RetreatStep,
        outcome: UnitOutcome,
        invalid_destinations: set[str],
    ) -> DislodgementDecision | None:
        """Devuelve la retirada preferida por la unidad."""
        unit: UnitKey = outcome.unit

        if outcome.attack_origin:
            my_invalid_destinations = set(invalid_destinations)
            my_invalid_destinations.add(outcome.attack_origin)

        # Las guarniciones no se retiran
        if unit.player_id is None or unit.unit_type == "G":
            return DislodgementDecision(DecisionType.DISBAND, None)

        # Resolvemos según la fase
        if retreat_step == RetreatStep.GARRISON:
            # Quizá podamos retirarnos a la ciudad
            province = self.map.provinces[unit.origin]
            temptative_destination = conflict_location(unit.origin, "G")
            if (
                # No hay una guarnición en nuestra provincia
                temptative_destination not in my_invalid_destinations
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
                    invalid_destinations.add(conflict_location(destination, "G"))
                    return DislodgementDecision(DecisionType.GARRISON, destination)

            return DislodgementDecision(DecisionType.DISBAND, None)
        else:
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
                adjacent_locations = {}

            adjacent_locations: list[str] = [
                location
                for location in adjacent_locations
                if location not in my_invalid_destinations
            ]

            if adjacent_locations:
                # Tenemos lugares de retirada
                random.shuffle(adjacent_locations)

                hc_provinces = self.game.scenario.home_countries_provinces(
                    player.home_countries
                )
                controlled_provinces = player.controlled_locations

                # Busco lugares apropiados según la fase
                destination = next(
                    (
                        d
                        for d in adjacent_locations
                        if (
                            retreat_step
                            in (RetreatStep.CONTROLLED, RetreatStep.ADJACENT)
                            or d.split()[0] in hc_provinces
                        )
                        and (
                            retreat_step
                            in (RetreatStep.HOME_COUNTRY, RetreatStep.ADJACENT)
                            or d.split()[0] in controlled_provinces
                        )
                    ),
                    None,
                )

                if destination:
                    invalid_destinations.add(
                        conflict_location(destination, unit.unit_type)
                    )
                    return DislodgementDecision(DecisionType.RETREAT, destination)
                else:
                    return None
            else:
                # No hay lugares de retirada; quizá podrá convertirse en garrison
                return None

    def __call__(self, resolution: MilitaryResolution) -> Mapping[UnitKey, str | None]:
        """Resuelve las retiradas del combate."""
        retreats: dict[UnitKey, DislodgementDecision] = {}

        # Comenzamos haciendo la lista de localizaciones no válidas para retiradas
        invalid_destinations = {
            conflict_location(outcome.final_location, outcome.final_unit_type)
            for outcome in resolution.outcomes
            if not outcome.dislodged
        }
        invalid_destinations |= resolution.contested_locations

        # Recorremos la tupla de unidades en retirada en orden aleatorio
        for retreat_step in RetreatStep:
            # Ahora calculamos todas las retiradas pendientes de calcular
            dislodges = [
                outcome
                for outcome in resolution.outcomes
                if outcome.dislodged and outcome.unit not in retreats
            ]
            for outcome in random.sample(dislodges, len(dislodges)):
                retreat = self._preferred_retreat(
                    retreat_step, outcome, invalid_destinations
                )
                if retreat:
                    retreats[outcome.unit] = retreat

        return retreats
