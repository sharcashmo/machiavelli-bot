# machiavelli/engine/control.py


from ..game.events import EventType, TurnEvent
from ..game.game import Game, Player


class ControlManager:
    """Responsable de la gestión del control de provincias y países."""

    def __init__(self, game: Game):
        self.game = game

    def _provinces_with_own_units(self, player: Player) -> set[str]:
        """Devuelve un set con las provincias en las que hay unidades de player."""
        own_provinces = set()

        own_provinces.update(p for p in player.armies)
        own_provinces.update(
            p.split()[0] for p in player.fleets if p in self.game.map.provinces
        )
        own_provinces.update(p for p in player.garrisons)

        return own_provinces

    def _provinces_with_others_units(self, player: Player) -> set[str]:
        """Devuelve un set con las provincias con unidades de otro jugador."""
        others_provinces = set()

        others_provinces.update(
            p for other in self.game.players for p in other.armies if other != player
        )
        others_provinces.update(
            p.split()[0]
            for other in self.game.players
            for p in other.fleets
            if other != player
            if p in self.game.map.provinces
        )
        others_provinces.update(
            p for other in self.game.players for p in other.garrisons if other != player
        )
        others_provinces.update(p for p in self.game.independent_garrisons)

        return others_provinces

    def control_changes(self, player: Player) -> None:
        """Actualiza los cambios de control de las provincias de un jugador."""

        own_provinces = self._provinces_with_own_units(player)
        others_provinces = self._provinces_with_others_units(player)

        new_controlled_provinces = [
            p
            for p in own_provinces
            if p not in others_provinces
            if p not in player.controlled_locations
        ]
        lost_controlled_provinces = [
            p for p in player.controlled_locations if p in others_provinces
        ]

        if new_controlled_provinces:
            self.game.add_event(
                TurnEvent(
                    type=EventType.GET_CONTROL,
                    data={
                        "player": player.player_id,
                        "provinces": new_controlled_provinces,
                    },
                )
            )
            player.controlled_locations.extend(new_controlled_provinces)

        if lost_controlled_provinces:
            self.game.add_event(
                TurnEvent(
                    type=EventType.LOSE_CONTROL,
                    data={
                        "player": player.player_id,
                        "provinces": lost_controlled_provinces,
                    },
                )
            )
            player.controlled_locations = [
                p
                for p in player.controlled_locations
                if p not in lost_controlled_provinces
            ]

    def home_country_control_loses(self, player: Player) -> None:
        """Comprueba si un jugador piede el control sobre algún país natal."""

        # Se pierde el control de un país natal si se pierde el control de todas las
        # ciudades de éste. Una guarnición permite controlar la ciudad
        for home_country in player.home_countries[:]:
            target_hc = self.game.scenario.home_countries.get(home_country)
            if target_hc:
                controls_any_city = any(
                    (p in player.controlled_locations or p in player.garrisons)
                    and self.game.map.provinces[p].city in ("city", "fortified")
                    for p in target_hc.provinces
                )
            else:
                controls_any_city = False

            if not controls_any_city:
                self.game.add_event(
                    TurnEvent(
                        type=EventType.LOSE_HOME_COUNTRY,
                        data={"player": player.player_id, "home_country": home_country},
                    )
                )
                player.home_countries.remove(home_country)

    def home_country_control_gains(self, player: Player) -> None:
        # Se gana el control de un país natal si se controlan
        # todas las provincias y ciudades de éste
        for home_country in self.game.scenario.home_countries:
            if home_country not in player.home_countries:
                missing_province = any(
                    p not in player.controlled_locations
                    for p in self.game.scenario.home_countries[home_country].provinces
                )
                if not missing_province:
                    self.game.add_event(
                        TurnEvent(
                            type=EventType.GET_HOME_COUNTRY,
                            data={
                                "player": player.player_id,
                                "home_country": home_country,
                            },
                        )
                    )
                    player.home_countries.append(home_country)

    def check_player_status(self, player: Player) -> None:
        """Comprueba si el jugador es eliminado o cumple las condiciones de victoria."""
        if not player.home_countries:
            player.eliminate()
            self.game.add_event(
                TurnEvent(
                    type=EventType.PLAYER_ELIMINATED, data={"player": player.player_id}
                )
            )
        else:
            cities = sum(
                self.game.map.provinces[p].city in ("city", "fortified")
                for p in player.controlled_locations
            )
            hc = len(player.home_countries)
            victory_conditions = self.game.scenario.victory_conditions
            if (
                cities >= victory_conditions.cities
                and hc >= victory_conditions.home_countries
            ):
                self.game.add_event(
                    TurnEvent(
                        type=EventType.PLAYER_WON,
                        data={
                            "player": player.player_id,
                            "cities": cities,
                            "home_countries": hc,
                        },
                    )
                )

    def run(self) -> None:
        """Ajusta el control, comprueba condiciones de victoria y finaliza campaña."""

        # Para cada jugador, comprobamos su control sobre provincias y países natales
        for player in self.game.players:
            self.control_changes(player)
            self.home_country_control_loses(player)
            self.home_country_control_gains(player)
            self.check_player_status(player)

        # Y cambiamos de estación
        year = self.game.scenario.year + self.game.turn_number // 4
        season = self.game.turn_number % 4

        self.game.add_event(
            TurnEvent(
                type=EventType.START_SEASON, data={"year": year, "season": season}
            )
        )
