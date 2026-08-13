# machiavelli/engine/rebellions.py


from ..events import EventType, TurnEvent
from ..game.game import Command, Game, Player
from ..game.map import Map
from ..game.scenario import Scenario


class RebellionManager:
    """Responsable de la gestión de las rebeliones."""

    def __init__(self, game: Game):
        self.game = game

    def _map(self) -> Map:
        """Devuelve el mapa activo conservando la interfaz histórica de Game."""
        game_map = self.game.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        return game_map

    def _scenario(self) -> Scenario:
        """Devuelve el escenario activo conservando la interfaz histórica de Game."""
        scenario = self.game.scenario
        if scenario is None:
            raise RuntimeError("La partida requiere un escenario cargado")
        return scenario

    def _expense_rebellion_pacify(self, command: Command) -> None:
        """Pacifica una rebelión activa en una provincia o ciudad."""
        target = command.target
        if target is None:
            return

        for player in self.game.players:
            for kind, rebel_list in (
                ("province", player.rebelled_provinces),
                ("city", player.rebelled_cities),
            ):
                if target in rebel_list:
                    if kind == "city":
                        province = self._map().provinces.get(target)
                        if province is None or not self._scenario().is_defensible_city(
                            province.city
                        ):
                            return
                    rebel_list.remove(target)
                    self.game.add_event(
                        TurnEvent(
                            EventType.REBELLION_PACIFY,
                            {
                                "player": player.player_id,
                                "province": target,
                                "kind": kind,
                            },
                        )
                    )
                    return  # Solo hay una rebelión por provincia

    def _expense_rebellion_non_home_country(self, command: Command) -> None:
        """Comenzar rebelión en una provincia no natal."""
        # Primer comprobamos si la provincia la controla alguien
        # (si no hay control, no hay rebeliones)
        target = command.target
        if target is None:
            return

        player_owner = next(
            (p for p in self.game.players if target in p.controlled_locations),
            None,
        )

        if not player_owner:
            return

        # Comprobamos que no sea una provincia natal del jugador. Si es natal de otro
        # jugador no importa
        hc = self._scenario().province_home_country(target)
        if hc in player_owner.home_countries:
            return

        # Realizamos la rebelión
        self.do_rebellion(owner=player_owner, target=target)

    def _expense_rebellion_home_country(self, command: Command) -> None:
        """Comenzar una rebelión en una provincia natal."""
        # Primer comprobamos si la provincia la controla alguien
        # (si no hay control, no hay rebeliones)
        target = command.target
        if target is None:
            return

        player_owner = next(
            (p for p in self.game.players if target in p.controlled_locations),
            None,
        )

        if not player_owner:
            return

        # Comprobamos que sea una provincia natal del jugador
        hc = self._scenario().province_home_country(target)
        if hc not in player_owner.home_countries:
            return

        # Realizamos la rebelión
        self.do_rebellion(owner=player_owner, target=target)

    REBELLION_EXPENSE_PACIFY = {"B"}
    REBELLION_EXPENSE_NON_HOME_COUNTRY = {"D"}
    REBELLION_EXPENSE_HOME_COUNTRY = {"D"}

    def rebellion_expenses(self) -> None:
        """Ejecuta las gastos de rebeliones."""
        for player in self.game.players:
            for command in player.commands:
                if command.is_valid_expense(self.REBELLION_EXPENSE_PACIFY):
                    self._expense_rebellion_pacify(command)
                elif command.is_valid_expense(self.REBELLION_EXPENSE_NON_HOME_COUNTRY):
                    self._expense_rebellion_non_home_country(command)
                elif command.is_valid_expense(self.REBELLION_EXPENSE_HOME_COUNTRY):
                    self._expense_rebellion_home_country(command)

        return

    def do_rebellion(self, owner: Player, target: str) -> None:
        """Realiza una rebelión.

        Al llamar a _do_rebellion, owner es ya el controlador de target. Recogimos
        el dato antes y para no buscarlo otro vez lo guardamos.
        """

        # Comprobamos que no haya rebeliones ya, pues no puede haber más de una
        if target in owner.rebelled_provinces or target in owner.rebelled_cities:
            return

        # Determinamos si la ciudad es defendible según las reglas del escenario
        scenario = self._scenario()
        game_map = self._map()

        if (
            scenario.is_defensible_city(game_map.provinces[target].city)
            and target not in owner.garrisons
        ):
            # Si hay ciudad fortificada (o fuerte) sin guarnición, rebelión en la ciudad
            owner.rebelled_cities.append(target)
            self.game.add_event(
                TurnEvent(
                    EventType.REBELLION_CITY,
                    {"player": owner.player_id, "province": target},
                )
            )
        else:
            # Si no, la rebelión se sitúa en la provincia
            owner.rebelled_provinces.append(target)
            self.game.add_event(
                TurnEvent(
                    EventType.REBELLION_PROVINCE,
                    {"player": owner.player_id, "province": target},
                )
            )
