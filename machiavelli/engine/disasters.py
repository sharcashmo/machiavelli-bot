# machiavelli/engine/disasters.py

from random import Random

from ..events import EventType, TurnEvent
from ..game.game import Game
from ..game.map import Map
from ..game.tables import GameTables


class DisastersManager:
    """Responsable de las reglas de desastres: hambre y plagas."""

    def __init__(self, game: Game, rng: Random | None = None):
        """Constructor del manager."""
        self.game = game
        self.rng = rng if rng is not None else Random()

    def _map(self) -> Map:
        """Devuelve el mapa activo conservando la interfaz histórica de Game."""
        game_map = self.game.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        return game_map

    def process_famine_relief_expenses(self) -> None:
        """Procesa los gastos de Paliar hambruna."""
        if (
            self.game.scenario is not None
            and not self.game.scenario.rules.famine_active
        ):
            return
        cost = GameTables.expenses["A"]["cost"]
        famine_relief_expenses = [
            cmd
            for player in self.game.players
            for cmd in player.commands
            if cmd.actor == "E A" and int(cmd.command) >= cost
        ]

        for exp in famine_relief_expenses:
            if exp.target in self.game.famine:
                self.game.famine.remove(exp.target)
                self.game.add_event(
                    TurnEvent(
                        EventType.FAMINE_RELIEF,
                        {"player": exp.player.player_id, "province": exp.target},
                    )
                )

    def _apply_disaster_deaths(
        self, event_type: EventType, provinces: list[str]
    ) -> None:
        """Elimina las unidades afectadas por algún desastre.

        Args:
            event_type (EventType): EventType.FAMINE_ATTRITION o EventType.PLAGUE_DEATH.
            provinces (list[str]): Lista de provincias afectadas por el desastre.
        """
        if event_type not in (EventType.FAMINE_ATTRITION, EventType.PLAGUE_DEATH):
            return

        if not provinces:
            return

        for player in self.game.players:
            player_units = []
            for army in player.armies[:]:
                if army in provinces:
                    player.armies.remove(army)
                    player_units.append(f"A {army}")
            for fleet in player.fleets[:]:
                if fleet.split()[0] in provinces:
                    player.fleets.remove(fleet)
                    player_units.append(f"F {fleet}")
            for garrison in player.garrisons[:]:
                if garrison in provinces:
                    player.garrisons.remove(garrison)
                    player_units.append(f"G {garrison}")

            # Envía el evento
            if player_units:
                self.game.add_event(
                    TurnEvent(
                        type=event_type,
                        data={"player": player.player_id, "units": player_units},
                    )
                )

        # Afecta también a las guarniciones independientes
        garrisons = [g for g in self.game.independent_garrisons if g in provinces]
        for g in garrisons:
            self.game.independent_garrisons.remove(g)

        # Envía el evento
        if garrisons:
            self.game.add_event(
                TurnEvent(
                    type=event_type,
                    data={"player": None, "units": [f"G {g}" for g in garrisons]},
                )
            )

    def resolve_famine_attrition(self) -> None:
        """Elimina las unidades reducidas por inanición al final de la primavera."""
        if (
            self.game.scenario is not None
            and not self.game.scenario.rules.famine_active
        ):
            return
        self._apply_disaster_deaths(EventType.FAMINE_ATTRITION, self.game.famine)

    def clear_famine(self) -> None:
        """Elimina el hambre al inicio del verano."""
        if (
            self.game.scenario is not None
            and not self.game.scenario.rules.famine_active
        ):
            return
        if self.game.famine:
            self.game.add_event(
                TurnEvent(EventType.FAMINE_END, data={"provinces": self.game.famine})
            )
        self.game.famine = []

    def _spawn_disaster(self, event_type: EventType) -> list[str]:
        """Genera el listado de provincias con desastre y emite el evento."""
        if event_type not in (EventType.FAMINE_SPAWN, EventType.PLAGUE_SPAWN):
            return []

        severity_roll = self.rng.randint(0, 5)
        severity = GameTables.disasters[severity_roll]
        provinces_table = (
            GameTables.famine
            if event_type == EventType.FAMINE_SPAWN
            else GameTables.plague
        )
        affected_provinces: list[str] = []
        game_map = self._map()

        # Fila
        if severity[0] in ["both", "row"]:
            dice = self.rng.randint(0, 5) + self.rng.randint(0, 5)
            row = provinces_table[dice]
            for p in row:
                if (
                    p is not None
                    and p in game_map.provinces
                    and p not in affected_provinces
                ):
                    affected_provinces.append(p)

        # Columna
        if severity[0] in ["both", "column"]:
            dice = self.rng.randint(0, 5) + self.rng.randint(0, 5)
            column = [r[dice] for r in provinces_table]
            for p in column:
                if (
                    p is not None
                    and p in game_map.provinces
                    and p not in affected_provinces
                ):
                    affected_provinces.append(p)

        if affected_provinces:
            self.game.add_event(
                TurnEvent(
                    type=event_type,
                    data={
                        "severity_roll": severity_roll,
                        "provinces": affected_provinces,
                    },
                )
            )

        return affected_provinces

    def spawn_famine(self) -> None:
        """Genera hambre en nuevas provincias al inicio de la primavera."""
        if (
            self.game.scenario is not None
            and not self.game.scenario.rules.famine_active
        ):
            return
        self.game.famine = self._spawn_disaster(event_type=EventType.FAMINE_SPAWN)

    def spawn_plague(self) -> None:
        """Genera plagas en nuevas provincias al inicio del verano.

        Las plagas afectan inmediatamente, así que el spawn viene acompañado de las
        muertes.
        """
        if (
            self.game.scenario is not None
            and not self.game.scenario.rules.plague_active
        ):
            return
        plague_provinces = self._spawn_disaster(event_type=EventType.PLAGUE_SPAWN)
        self._apply_disaster_deaths(
            event_type=EventType.PLAGUE_DEATH, provinces=plague_provinces
        )
