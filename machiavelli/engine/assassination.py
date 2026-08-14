# machiavelli/engine/assassination.py

import logging
from random import Random

from ..events import EventType, TurnEvent
from ..game.command import Command
from ..game.game import Game
from ..game.player import Player
from ..game.tables import GameTables
from .rebellions import RebellionManager

logger = logging.getLogger(__name__)


class AssassinationResolver:
    """Responsable de la gestión de los asesinatos."""

    def __init__(self, game: Game, rng: Random | None = None):
        self.game = game
        self.rng = rng if rng is not None else Random()
        self.assassinations: list[str] = []

    def _execute_assassination(self, assassin: Player, target: Player) -> None:
        """Ejecuta un asesinato."""
        # En primer lugar, todas las órdenes de sus ejércitos se cancelan
        target.commands = [
            command for command in target.commands if command.actor[0] == "E"
        ]

        # Todas sus guarniciones asediadas se eliminan
        lost_garrisons = []

        for garrison in target.garrisons:
            if garrison in self.game.besieges:
                lost_garrisons.append(garrison)
                target.garrisons.remove(garrison)

        # Provocamos rebeliones
        manager = RebellionManager(self.game)
        rebellions = []
        army_locations = {
            *target.armies,
            *(province.split()[0] for province in target.fleets),
            *target.garrisons,
        }
        for province in sorted(target.controlled_locations):
            rebellion_index = 0
            home_country = self.game.scenario.province_home_country(province)
            is_home_country = home_country in target.home_countries
            has_army = province in army_locations

            if not is_home_country:
                rebellion_index += 2
            if not has_army:
                rebellion_index += 1

            # Tenemos el número en un dado que necesitamos para generar la rebelión
            rebellion_dificulty = GameTables.assassination_rebellions[rebellion_index]

            if self.rng.randint(1, 6) <= rebellion_dificulty:
                # Generamos una rebelión
                rebellions.append(province)
                manager.do_rebellion(target, province)

        # Enviamos el evento de resumen con toda la información
        self.game.add_event(
            TurnEvent(
                EventType.ASSASSINATION_ATTEMPT,
                data={
                    "assassin": assassin.player_id,
                    "target": target.player_id,
                    "result": "success",
                    "lost_garrisons": lost_garrisons,
                    "rebellions": rebellions,
                },
            )
        )

    def _do_assassination_attempt(self, player: Player, command: Command) -> None:
        """Intenta un asesinato."""
        # No es necesario comprobar el comando porque se comprueba en run()
        # Comprobamos el target
        if command.target is None:
            return

        # Recuperamos el objetivo
        target = self.game.get_player(power_id=command.target)
        if target is None:
            return

        # Vemos si player tiene en su poder la ficha de asesinato correspondiente
        if command.target in player.ass_counters:
            # Eliminamos la ficha, se realice el asesinato o no
            # el jugador puede tener más de una ficha (obteniéndolas de otros),
            # solo borramos la primera
            player.ass_counters.remove(command.target)
            if command.target not in self.assassinations:
                # Si el jugador ya fue asesinado no tiene más efectos
                chances = min(int(command.command) // 12, 3)
                roll = self.rng.randint(1, 6)
                if roll <= chances:
                    # Success
                    self._execute_assassination(assassin=player, target=target)
                else:
                    # Failed
                    self.game.add_event(
                        TurnEvent(
                            EventType.ASSASSINATION_ATTEMPT,
                            data={
                                "assassin": player.player_id,
                                "target": target.player_id,
                                "result": "failed",
                                "lost_garrisons": [],
                                "rebellions": [],
                            },
                        )
                    )
            else:
                # Alguien se le adelantó
                self.game.add_event(
                    TurnEvent(
                        EventType.ASSASSINATION_ATTEMPT,
                        data={
                            "assassin": player.player_id,
                            "target": target.player_id,
                            "result": "late",
                            "lost_garrisons": [],
                            "rebellions": [],
                        },
                    )
                )

    ASSASSINATION_EXPENSE_TYPES = {"E"}

    def run(self) -> None:
        """Ejecuta todas las órdenes de asesinato."""
        # Registramos todos los asesinatos
        for player in self.game.players:
            for command in player.commands:
                if command.is_valid_expense(self.ASSASSINATION_EXPENSE_TYPES):
                    self._do_assassination_attempt(player, command)
