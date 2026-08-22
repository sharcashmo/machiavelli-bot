# machiavelli/engine/setup.py


from random import Random

from ..game.events import EventType, TurnEvent
from ..game.game import Game
from .exceptions import (
    DuplicatePlayerError,
    GameAlreadyStartedError,
    GameInitializationError,
    InvalidPlayerCountError,
    ScenarioNotSelectedError,
)


class SetupManager:
    """Responsable de la realizar el setup inicial de la partida."""

    def __init__(self, game: Game, rng: Random | None = None):
        """Constructor del manager."""
        self.game = game
        self.rng = rng if rng is not None else Random()

    def run(self) -> None:
        """Realiza las operaciones del setup inicial de la partida según el escenario.

        Estas acciones son:
        - Reparte las facciones al azar entre los jugadores.
        - Asigna a cada jugador las provincias controladas y las unidades.
        - Reparte recursos a cada jugador (fichas de asesinato principalmente).
        - Coloca guarniciones independientes en las ciudades fortificadas sin dueño.

        Raises:
            ScenarioNotSelectedError si no se seleccionó escenario.
            DuplicatePlayerError si hay algún jugador duplicado.
            InvalidPlayerCountError si el número de jugadores no coincide con el del
                escenario.
        """
        # Ver si la partida ya está iniciada
        if self.game.turn_number > 0:
            raise GameAlreadyStartedError()

        # Comprobamos que se haya seleccionado escenario
        if self.game.scenario is None:
            raise ScenarioNotSelectedError()

        # Comprobamos que no haya jugadores duplicados
        player_ids = set()
        discord_ids = set()
        for player in self.game.players:
            if player.player_id in player_ids or (
                player.discord_id is not None and player.discord_id in discord_ids
            ):
                raise DuplicatePlayerError(player.player_id, player.discord_id)
            else:
                player_ids.add(player.player_id)
                discord_ids.add(player.discord_id)

        # Comprobamos que todas las plazas de jugadores están llenas
        players_number = len(self.game.players)
        powers_number = len(self.game.scenario.powers)
        if players_number != powers_number:
            raise InvalidPlayerCountError(players_number, powers_number)

        game_map = self.game.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        for power in self.game.scenario.powers.values():
            for location in power.garrisons:
                province = game_map.provinces.get(location)
                if province is None or not self.game.scenario.is_defensible_city(
                    province.city
                ):
                    raise GameInitializationError(
                        f"Guarnición inicial inválida en {location}: "
                        "la plaza no está defendible."
                    )

        self.game.add_event(
            TurnEvent(
                type=EventType.START_GAME,
                data={"scenario": self.game.scenario_id},
            ),
        )

        # Sorteamos las facciones entre los jugadores
        power_ids = list(self.game.scenario.powers.keys())
        self.rng.shuffle(power_ids)
        assassination_targets = (
            power_ids if self.game.scenario.rules.assassinations_active else []
        )

        # Coloca guarniciones independientes en todas las ciudades del mapa
        # Más fácil y limpio que buscar luego las ciudades no controladas por nadie
        garrisons = [
            key
            for key, province in game_map.provinces.items()
            if province.city == "fortified"
        ]

        for player, power_id in zip(self.game.players, power_ids, strict=True):
            self.game.add_event(
                TurnEvent(
                    type=EventType.START_GAME_POWER_ASSIGNED,
                    data={
                        "player_id": player.player_id,
                        "discord_id": player.discord_id,
                        "power_id": power_id,
                    },
                )
            )

            # Asigna la potencia al jugador, junto con sus provincias y unidades.
            power = self.game.scenario.powers[power_id]
            player.assign_power_from_scenario(power_id, power, assassination_targets)

            # Elimina las guarniciones independientes de sus provincias
            garrisons = [p for p in garrisons if p not in power.controlled_provinces]

        self.game.independent_garrisons = garrisons
