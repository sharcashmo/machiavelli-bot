# machiavelli/services/player_interaction_service.py

from __future__ import annotations

from typing import TYPE_CHECKING

from ..game.map import MovementMode, Sea
from ..game.tables import GameTables

if TYPE_CHECKING:
    pass


class PlayerInteractionService:
    def __init__(self, player):
        self.player = player
        self.game = player.game

    def _is_defensible_location(self, location: str) -> bool:
        province = self.game.map.provinces.get(location.split()[0])
        return province is not None and self.game.scenario.is_defensible_city(
            province.city
        )

    # ---------------------------------------------------------
    # Funciones para la precarga de órdenes disponibles
    # ---------------------------------------------------------

    def cmd_available_actors(self) -> list[tuple[str, str]]:
        """Devuelve la lista de actores disponibles para una orden de un jugador."""
        choices = []

        map = self.game.map
        provinces = self.game.map.provinces
        locations = self.game.map.provinces | self.game.map.seas

        if self.game.turn_number % 4 == 1:
            # Primer turno de la primavera, mantenimiento
            for a in self.player.armies:
                choices.append((f"A {a}", f"Ejército en {provinces[a].name}"))
            for a in self.player.fleets:
                choices.append((f"F {a}", f"Flota en {locations[a].name}"))
            for a in self.player.garrisons:
                if self._is_defensible_location(a):
                    choices.append((f"G {a}", f"Guarnición en {provinces[a].name}"))

            home_countries_cities = [
                p
                for hc_id in self.player.home_countries
                if hc_id in self.game.scenario.home_countries
                for p in self.game.scenario.home_countries[hc_id].provinces
                if p in self.player.controlled_locations
                and map.provinces[p].city in ("city", "fortified")
            ]

            for p in home_countries_cities:
                if (
                    p not in self.player.armies
                    and p not in self.player.fleets
                    and (p not in self.player.garrisons)
                ):
                    choices.append(
                        (f"A {p}", f"Ejército en {provinces[p].name} (reclutar)")
                    )
                    if map.provinces[p].has_port:
                        choices.append(
                            (f"F {p}", f"Flota en {provinces[p].name} (reclutar)")
                        )
                if (
                    p not in self.player.garrisons
                    and map.provinces[p].city == "fortified"
                ):
                    choices.append(
                        (f"G {p}", f"Guarnición en {provinces[p].name} (reclutar)")
                    )
        else:
            # Resto de turnos, campaña
            for a in self.player.armies:
                choices.append((f"A {a}", f"Ejército en {provinces[a].name}"))
            for a in self.player.fleets:
                choices.append((f"F {a}", f"Flota en {locations[a].name}"))
            for a in self.player.garrisons:
                if self._is_defensible_location(a):
                    choices.append((f"G {a}", f"Guarnición en {provinces[a].name}"))

            unit_provinces = {p for p in self.player.armies}
            unit_provinces |= {p.split()[0] for p in self.player.fleets}
            unit_provinces |= {p for p in self.player.garrisons}
            unit_provinces |= {
                p for p in locations.keys() if p.split()[0] in unit_provinces
            }

            adjacent = {
                r.destination for a in unit_provinces for r in locations[a].land_routes
            }
            adjacent |= {
                r.destination.split()[0]
                for a in unit_provinces
                for r in locations[a].sea_routes
            }

            bribe_armies = [
                a
                for p in self.game.players
                for a in p.armies
                if p != self.player and a in adjacent
            ]
            bribe_fleets = [
                f
                for p in self.game.players
                for f in p.fleets
                if p != self.player and f.split()[0] in adjacent
            ]
            bribe_garrisons = [
                g
                for p in self.game.players
                for g in p.garrisons
                if p != self.player
                and g in adjacent
                and self._is_defensible_location(g)
            ]
            bribe_independent = [
                g
                for g in self.game.independent_garrisons
                if g in adjacent and self._is_defensible_location(g)
            ]

            for a in bribe_armies:
                choices.append((f"A {a}", f"Ejército en {provinces[a].name}"))
            for a in bribe_fleets:
                choices.append((f"F {a}", f"Flota en {locations[a].name}"))
            for a in bribe_garrisons:
                choices.append((f"G {a}", f"Guarnición en {provinces[a].name}"))
            for a in bribe_independent:
                choices.append((f"G {a}", f"Guarnición en {provinces[a].name}"))

        return choices

    def cmd_available_commands(self, actor: str) -> list[tuple[str, str]]:
        """Devuelve lista de comandos para una orden de un jugador y un actor."""
        if actor not in dict(self.cmd_available_actors()):
            return []
        choices = []

        if self.game.turn_number % 4 == 1:
            actor_type, actor_id = actor.split(maxsplit=1)

            if (
                (actor_type == "A" and actor_id in self.player.armies)
                or (actor_type == "F" and actor_id in self.player.fleets)
                or (actor_type == "G" and actor_id in self.player.garrisons)
            ):
                for c in ("M", "D"):
                    choices.append((c, GameTables.maintenance_orders[c]["text"]))
            else:
                for c in ("R", "D"):
                    choices.append((c, GameTables.maintenance_orders[c]["text"]))
        else:
            actor_type, actor_location = actor.split(maxsplit=1)
            actor_id = actor_location.split()[0]

            is_besieging = actor_id in self.game.besieges
            garrisons = [
                g for p in self.game.players for g in p.garrisons
            ] + self.game.independent_garrisons
            has_garrison = actor_id in garrisons
            province = self.game.map.provinces.get(actor_id)
            has_port = province.has_port if province else False
            is_defensible = self._is_defensible_location(actor_id)

            if actor_type in ("A", "F") and not is_besieging:
                choices.append(("A", f"{GameTables.military_orders['A']['text']}"))
            if actor_type == "A" and has_garrison and is_defensible:
                choices.append(("B", f"{GameTables.military_orders['B']['text']}"))
            if actor_type == "F" and has_garrison and has_port and is_defensible:
                choices.append(("B", f"{GameTables.military_orders['B']['text']}"))
            choices.append(("H", f"{GameTables.military_orders['H']['text']}"))
            if actor_type in ("A", "F") and is_besieging:
                choices.append(("L", f"{GameTables.military_orders['L']['text']}"))
            if not is_besieging:
                choices.append(("S", f"{GameTables.military_orders['S']['text']}"))
            if actor_type == "F" and not is_besieging:
                choices.append(("T", f"{GameTables.military_orders['T']['text']}"))
            if is_defensible and not is_besieging:
                choices.append(("C", f"{GameTables.military_orders['C']['text']}"))

        return choices

    def cmd_available_targets(self, actor: str, command: str) -> list[tuple[str, str]]:
        """Devuelve la lista de objetivos disponibles para un comando."""
        if command not in dict(self.cmd_available_commands(actor)):
            return []
        choices = []

        if self.game.turn_number % 4 == 1:
            choices.append(("", "Ninguno"))
        else:
            map = self.game.map
            locations = map.provinces | map.seas

            actor_type, actor_location = actor.split(maxsplit=1)
            actor_id = actor_location.split()[0]

            if command in ("B", "H", "L"):
                choices.append(("", "Ninguno"))
            elif command == "A":
                assert actor_type in ("A", "F")
                if actor_type == "A":
                    for r in locations[actor_location].land_routes:
                        choices.append(
                            (r.destination, f"{locations[r.destination].name}")
                        )

                    fleets = [f for p in self.game.players for f in p.fleets]

                    convoy = [
                        c.target
                        for c in self.player.commands
                        if c.actor == actor and c.command == "A"
                    ]

                    if convoy:
                        for s in convoy:
                            if s not in fleets:
                                break
                        else:
                            convoy_end = convoy[-1]
                            for r in map.adjacent_locations(
                                convoy_end, mode=MovementMode.BOTH
                            ):
                                if isinstance(locations[r], Sea):
                                    if r in fleets:
                                        choices.append((r, f"{locations[r].name}"))
                                else:
                                    choices.append((r, f"{locations[r].name}"))
                            choices = list(dict.fromkeys(choices))
                    else:
                        for r in map.adjacent_locations(
                            actor_location, mode=MovementMode.BOTH
                        ):
                            if r in fleets:
                                choices.append((r, f"{locations[r].name}"))

                elif actor_type == "F":
                    for r in locations[actor_location].sea_routes:
                        choices.append(
                            (r.destination, f"{locations[r.destination].name}")
                        )
            elif command == "S":
                if actor_type == "A":
                    for r in locations[actor_location].land_routes:
                        choices.append(
                            (r.destination, f"{locations[r.destination].name}")
                        )
                    for r in locations[actor_location].land_routes:
                        for p in self.game.players:
                            if p != self.player:
                                choices.append(
                                    (
                                        f"{r.destination} ({p.power})",
                                        f"{locations[r.destination].name} "
                                        f"({GameTables.powers[p.power]})",
                                    )
                                )
                elif actor_type == "F":
                    for r in locations[actor_location].sea_routes:
                        choices.append(
                            (r.destination, f"{locations[r.destination].name}")
                        )
                    for r in locations[actor_location].sea_routes:
                        for p in self.game.players:
                            if p != self.player:
                                choices.append(
                                    (
                                        f"{r.destination} ({p.power})",
                                        f"{locations[r.destination].name} "
                                        f"({GameTables.powers[p.power]})",
                                    )
                                )
                elif actor_type == "G":
                    choices.append(
                        (actor_location, f"{locations[actor_location].name}")
                    )
                    for p in self.game.players:
                        if p != self.player:
                            choices.append(
                                (
                                    f"{actor_location} ({p.power})",
                                    f"{locations[actor_location].name} "
                                    f"({GameTables.powers[p.power]})",
                                )
                            )
            elif command == "T":
                assert actor_type in ("F")
                armies = [
                    a
                    for p in self.game.players
                    for a in p.armies
                    if locations[a].sea_routes
                ]
                for a in armies:
                    choices.append((f"A {a}", f"Ejército en {locations[a].name}"))
            elif command == "C":
                choices.append(("0", "Desbandar"))
                if self._is_defensible_location(actor_id):
                    if actor_type == "G":
                        choices.append(("A", f"{GameTables.actors['A']}"))
                        if locations[actor_id].has_port:
                            choices.append(("F", f"{GameTables.actors['F']}"))
                    elif actor_type == "A" or locations[actor_id].has_port:
                        choices.append(("G", f"{GameTables.actors['G']}"))

        return choices

    # ---------------------------------------------------------
    # Funciones para la precarga de gastos disponibles
    # ---------------------------------------------------------

    def exp_available_expenses(self) -> list[tuple[str, str]]:
        """Devuelve la lista de gastos disponibles para un jugador."""
        choices = []
        rules = self.game.scenario.rules

        expenses = {
            k: e
            for k, e in GameTables.expenses.items()
            if e["cost"] <= self.player.ducats
            and (k != "A" or rules.famine_active)
            and (k != "E" or rules.assassinations_active)
        }

        locations = self.game.map.provinces | self.game.map.seas
        unit_provinces = {p for p in self.player.armies}
        unit_provinces |= {p.split()[0] for p in self.player.fleets}
        unit_provinces |= {p for p in self.player.garrisons}
        unit_provinces |= {
            p for p in locations.keys() if p.split()[0] in unit_provinces
        }

        adjacent = {
            r.destination for a in unit_provinces for r in locations[a].land_routes
        }
        adjacent |= {
            r.destination.split()[0]
            for a in unit_provinces
            for r in locations[a].sea_routes
        }

        bribe_armies = [
            a
            for p in self.game.players
            for a in p.armies
            if p != self.player and a in adjacent
        ]
        bribe_fleets = [
            f
            for p in self.game.players
            for f in p.fleets
            if p != self.player and f.split()[0] in adjacent
        ]
        bribe_garrisons = [
            g
            for p in self.game.players
            for g in p.garrisons
            if p != self.player and g in adjacent and self._is_defensible_location(g)
        ]

        for key, expense in expenses.items():
            if key == "A" and self.game.famine:
                choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "B":
                rebellions = [
                    r for p in self.game.players for r in p.rebelled_provinces
                ] + [
                    r
                    for p in self.game.players
                    for r in p.rebelled_cities
                    if self._is_defensible_location(r)
                ]
                if rebellions:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "C":
                no_hc = [
                    pr
                    for p in self.game.players
                    for pr in p.nonhc_provinces()
                    if p != self.player
                    if pr not in p.rebelled_provinces
                    if pr not in p.rebelled_cities
                ]
                if no_hc:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "D":
                hc = [
                    pr
                    for p in self.game.players
                    for pr in p.hc_provinces()
                    if p != self.player
                    if pr not in p.rebelled_provinces
                    if pr not in p.rebelled_cities
                ]
                if hc:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "E":
                ass = [
                    p.power
                    for p in self.game.players
                    if p.home_countries
                    if p.power in self.player.ass_counters
                ]
                if ass:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "F":
                choices.append((f"E {key}", f"{expense['text']}"))
            elif key in ("G", "H"):
                garrisons = [
                    g
                    for g in self.game.independent_garrisons
                    if g in adjacent and self._is_defensible_location(g)
                ]
                if garrisons:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "I":
                if bribe_garrisons:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "J":
                if bribe_armies or bribe_fleets or bribe_garrisons:
                    choices.append((f"E {key}", f"{expense['text']}"))
            elif key == "K":
                if bribe_armies or bribe_fleets:
                    choices.append((f"E {key}", f"{expense['text']}"))

        return choices

    def exp_available_targets(self, expense: str) -> list[tuple[str, str]]:
        """Devuelve la lista de objetivos disponibles para un gasto."""
        if expense not in dict(self.exp_available_expenses()):
            return []
        choices = []

        _, key = expense.split()
        map = self.game.map

        locations = self.game.map.provinces | self.game.map.seas
        unit_provinces = {p for p in self.player.armies}
        unit_provinces |= {p.split()[0] for p in self.player.fleets}
        unit_provinces |= {p for p in self.player.garrisons}
        unit_provinces |= {
            p for p in locations.keys() if p.split()[0] in unit_provinces
        }

        adjacent = {
            r.destination for a in unit_provinces for r in locations[a].land_routes
        }
        adjacent |= {
            r.destination.split()[0]
            for a in unit_provinces
            for r in locations[a].sea_routes
        }

        bribe_armies = [
            a
            for p in self.game.players
            for a in p.armies
            if p != self.player and a in adjacent
        ]
        bribe_fleets = [
            f
            for p in self.game.players
            for f in p.fleets
            if p != self.player and f.split()[0] in adjacent
        ]
        bribe_garrisons = [
            g
            for p in self.game.players
            for g in p.garrisons
            if p != self.player and g in adjacent and self._is_defensible_location(g)
        ]

        if key == "A":
            for f in self.game.famine:
                choices.append((f"{map.provinces[f].id}", f"{map.provinces[f].name}"))
        elif key == "B":
            rebellions = [
                r for p in self.game.players for r in p.rebelled_provinces
            ] + [
                r
                for p in self.game.players
                for r in p.rebelled_cities
                if self._is_defensible_location(r)
            ]
            for r in rebellions:
                choices.append((f"{map.provinces[r].id}", f"{map.provinces[r].name}"))
        elif key == "C":
            no_hc = [
                pr
                for p in self.game.players
                for pr in p.nonhc_provinces()
                if p != self.player
                if pr not in p.rebelled_provinces
                if pr not in p.rebelled_cities
            ]
            for r in no_hc:
                choices.append((f"{map.provinces[r].id}", f"{map.provinces[r].name}"))
        elif key == "D":
            hc = [
                pr
                for p in self.game.players
                for pr in p.hc_provinces()
                if p != self.player
                if pr not in p.rebelled_provinces
                if pr not in p.rebelled_cities
            ]
            for r in hc:
                choices.append((f"{map.provinces[r].id}", f"{map.provinces[r].name}"))
        elif key == "E":
            ass = [
                p.power
                for p in self.game.players
                if p.home_countries
                if p.power in self.player.ass_counters
            ]
            for a in ass:
                choices.append((f"{a}", f"{GameTables.powers[a]}"))
        elif key == "F":
            armies = [u for p in self.game.players for u in p.armies]
            for a in armies:
                choices.append((f"A {a}", f"Ejército en {locations[a].name}"))
            fleets = [u for p in self.game.players for u in p.fleets]
            for f in fleets:
                choices.append((f"F {f}", f"Flota en {locations[f].name}"))
            garrisons = [
                u
                for p in self.game.players
                for u in p.garrisons
                if self._is_defensible_location(u)
            ] + [
                g
                for g in self.game.independent_garrisons
                if self._is_defensible_location(g)
            ]
            for g in garrisons:
                choices.append((f"G {g}", f"Guarnición en {locations[g].name}"))
        elif key in ("G", "H"):
            garrisons = [
                g
                for g in self.game.independent_garrisons
                if g in adjacent and self._is_defensible_location(g)
            ]
            for g in garrisons:
                choices.append((f"G {g}", f"Guarnición en {locations[g].name}"))
        elif key == "I":
            for g in bribe_garrisons:
                choices.append((f"G {g}", f"Guarnición en {locations[g].name}"))
        elif key == "J":
            for a in bribe_armies:
                choices.append((f"A {a}", f"Ejército en {locations[a].name}"))
            for f in bribe_fleets:
                choices.append((f"F {f}", f"Flota en {locations[f].name}"))
            for g in bribe_garrisons:
                choices.append((f"F {g}", f"Guarnición en {locations[g].name}"))
        elif key == "K":
            for a in bribe_armies:
                choices.append((f"A {a}", f"Ejército en {locations[a].name}"))
            for f in bribe_fleets:
                choices.append((f"F {f}", f"Flota en {locations[f].name}"))

        return choices

    def exp_available_amounts(self, expense: str, target: str) -> list[tuple[str, str]]:
        """Devuelve la lista de cantidades disponibles para un gasto."""
        if target not in dict(self.exp_available_targets(expense)):
            return []
        choices = [("0", "Cancelar gasto")]

        _, key = expense.split()
        exp = GameTables.expenses[key]
        cost = exp["cost"]
        map = self.game.map

        if key in ("A", "B", "C", "D"):
            choices.append((str(cost), f"{cost} ducados"))
        elif key == "E":
            for c in range(cost, cost * 3 + 1, cost):
                choices.append((str(c), f"{c} ducados"))
        elif key == "F":
            for c in range(cost, self.player.ducats + 1, 3):
                choices.append((str(c), f"{c} ducados"))
        elif key in ("G", "H", "I", "J", "K"):
            target_type, target_id = target.split()
            if target_type == "G" and map.provinces[target_id].major_city > 1:
                cost *= 2
            for c in range(cost, self.player.ducats + 1, 3):
                choices.append((str(c), f"{c} ducados"))

        return choices
