"""Fase de mantenimiento con un resultado auditable por cada orden intentada."""

from ..events import EventType, TurnEvent
from ..game.command import Command
from ..game.game import Game
from ..game.player import Player


class MaintenanceResolver:
    """Resuelve las órdenes de disolución, mantenimiento y reclutamiento."""

    def __init__(self, game: Game):
        self.game = game

    @staticmethod
    def _set_default_commands(player: Player) -> None:
        """Añade una orden efectiva de mantenimiento a cada unidad que no tenga una
        orden explícita.
        """
        actors = {command.actor for command in player.commands}
        for unit_type, locations in (
            ("A", player.armies),
            ("F", player.fleets),
            ("G", player.garrisons),
        ):
            for location in locations:
                actor = f"{unit_type} {location}"
                if actor not in actors:
                    player.commands.append(
                        Command(player.game, player, actor, "M", target=None)
                    )

    def _emit(
        self,
        player: Player,
        command: Command,
        result: str,
        cost: int,
    ) -> None:
        self.game.add_event(
            TurnEvent(
                EventType.MAINTENANCE_ORDER_RESOLVED,
                {
                    "player": player.player_id,
                    "actor": command.actor,
                    "order": command.command,
                    "target": command.target,
                    "result": result,
                    "cost": cost,
                },
            )
        )

    @staticmethod
    def _unit_collection(player: Player, unit_type: str) -> list[str]:
        try:
            return {
                "A": player.armies,
                "F": player.fleets,
                "G": player.garrisons,
            }[unit_type]
        except KeyError as error:
            raise ValueError(f"Tipo de unidad desconocido: {unit_type}") from error

    def run(self) -> None:
        """Resuelve los intentos de mantenimiento y emite un resumen por jugador."""
        game_map = self.game.require_map()
        scenario = self.game.require_scenario()

        for player in self.game.players:
            initial_ducats = player.ducats
            expenses = 0
            recruited_places = []
            disbanded_places = []
            self._set_default_commands(player)

            for command in [item for item in player.commands if item.command == "D"]:
                unit_type, unit_id = command.actor.split(maxsplit=1)
                units = self._unit_collection(player, unit_type)
                if unit_id in units:
                    units.remove(unit_id)
                    disbanded_places.append(unit_id)
                    self._emit(player, command, "disbanded", 0)
                else:
                    self._emit(player, command, "unit_not_found", 0)

            for command in [item for item in player.commands if item.command == "M"]:
                unit_type, unit_id = command.actor.split(maxsplit=1)
                units = self._unit_collection(player, unit_type)
                if unit_id not in units:
                    self._emit(player, command, "unit_not_found", 0)
                elif player.ducats - expenses >= 3:
                    expenses += 3
                    self._emit(player, command, "maintained", 3)
                else:
                    units.remove(unit_id)
                    self._emit(player, command, "disbanded_no_funds", 0)

            home_country_cities = {
                province
                for province in player.controlled_locations
                if scenario.province_home_country(province) in player.home_countries
                and game_map.provinces[province].city in ("city", "fortified")
            }
            for command in [item for item in player.commands if item.command == "R"]:
                unit_type, unit_id = command.actor.split(maxsplit=1)
                if player.ducats - expenses < 3:
                    self._emit(player, command, "recruitment_no_funds", 0)
                    continue
                if unit_id not in home_country_cities:
                    self._emit(player, command, "invalid_home_or_control", 0)
                    continue
                if unit_id in recruited_places:
                    self._emit(player, command, "already_recruited", 0)
                    continue
                if unit_id in disbanded_places:
                    self._emit(player, command, "disbanded_place", 0)
                    continue

                province = game_map.provinces[unit_id]
                occupied = unit_id in player.armies or any(
                    fleet.split()[0] == unit_id for fleet in player.fleets
                )
                if province.is_venice:
                    occupied = occupied or unit_id in player.garrisons

                if unit_type in ("A", "F"):
                    if occupied:
                        self._emit(player, command, "space_occupied", 0)
                    elif unit_type == "F" and not province.has_port:
                        self._emit(player, command, "port_required", 0)
                    else:
                        self._unit_collection(player, unit_type).append(unit_id)
                        recruited_places.append(unit_id)
                        expenses += 3
                        self._emit(player, command, "recruited", 3)
                elif unit_type == "G":
                    if unit_id in player.rebelled_cities:
                        self._emit(player, command, "rebelled_city", 0)
                    elif unit_id in player.garrisons or (
                        province.is_venice and occupied
                    ):
                        self._emit(player, command, "space_occupied", 0)
                    elif province.city != "fortified":
                        self._emit(player, command, "fortified_city_required", 0)
                    else:
                        player.garrisons.append(unit_id)
                        recruited_places.append(unit_id)
                        expenses += 3
                        self._emit(player, command, "recruited", 3)
                else:
                    self._emit(player, command, "invalid_home_or_control", 0)

            remaining_ducats = initial_ducats - expenses
            player.ducats = remaining_ducats
            self.game.add_event(
                TurnEvent(
                    EventType.MAINTENANCE_SUMMARY,
                    {
                        "player": player.player_id,
                        "initial_ducats": initial_ducats,
                        "expenses": expenses,
                        "remaining_ducats": remaining_ducats,
                    },
                )
            )
