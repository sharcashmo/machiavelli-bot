"""Fase de ingresos y sus eventos de turno auditables."""

from random import Random

from ..game.events import EventType, JSONValue, TurnEvent
from ..game.game import Game
from ..game.player import Player
from ..game.tables import GameTables


class IncomeManager:
    """Calcula y aplica los ingresos de primavera de cada jugador."""

    def __init__(self, game: Game, rng: Random | None = None):
        self.game = game
        self.rng = rng if rng is not None else Random()

    def _collect_player_income(self, player: Player) -> None:
        """Aplica el cálculo determinista completo de los ingresos de un jugador."""
        game_map = self.game.require_map()
        scenario = self.game.require_scenario()

        maybe_provinces = (
            set(player.controlled_locations)
            | set(player.armies)
            | {fleet.split()[0] for fleet in player.fleets}
        )
        provinces = sorted(
            province
            for province in maybe_provinces
            if province not in self.game.famine
            and province not in player.rebelled_provinces
            and province not in player.rebelled_cities
        )
        province_income = len(provinces)

        maybe_cities = {
            province
            for province in player.controlled_locations
            if province not in self.game.famine
            and province not in player.rebelled_cities
            and province not in player.rebelled_provinces
            and province not in self.game.besieges
        } | set(player.garrisons)
        cities = sorted(
            city
            for city in maybe_cities
            if game_map.provinces[city].city in ("city", "fortified")
        )
        city_income = sum(game_map.provinces[city].major_city or 0 for city in cities)

        variable_income: list[dict[str, JSONValue]] = []
        variable_total = 0
        for home_country in scenario.variable_income_home_countries:
            if home_country not in player.home_countries:
                continue
            roll = self.rng.randint(1, 6)
            amount = GameTables.variable_income[home_country][roll - 1]
            variable_income.append(
                {
                    "source_type": "home_country",
                    "source": home_country,
                    "roll": roll,
                    "amount": amount,
                }
            )
            variable_total += amount

        for province in scenario.variable_income_provinces:
            if province not in player.controlled_locations:
                continue
            roll = self.rng.randint(1, 6)
            amount = GameTables.variable_income[province][roll - 1]
            variable_income.append(
                {
                    "source_type": "province",
                    "source": province,
                    "roll": roll,
                    "amount": amount,
                }
            )
            variable_total += amount

        total_income = province_income + city_income + variable_total
        player.ducats += total_income
        self.game.add_event(
            TurnEvent(
                EventType.INCOME_COLLECTED,
                {
                    "player": player.player_id,
                    "provinces": provinces,
                    "province_income": province_income,
                    "cities": cities,
                    "city_income": city_income,
                    "variable_income": variable_income,
                    "total_income": total_income,
                },
            )
        )

    def run(self) -> None:
        """Aplica los ingresos a cada jugador en el orden de asignación."""
        for player in self.game.players:
            self._collect_player_income(player)
