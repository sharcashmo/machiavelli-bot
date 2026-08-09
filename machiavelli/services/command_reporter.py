"""Informes legibles de comandos."""

from __future__ import annotations

from typing import TYPE_CHECKING

from machiavelli.game.tables import GameTables

if TYPE_CHECKING:
    from machiavelli.game.command import Command
    from machiavelli.game.map import Location, Map, Province


class CommandReporter:
    """Genera representaciones legibles de los comandos de los jugadores."""

    @staticmethod
    def format_report(command: Command, game_map: Map, turn_number: int) -> str:
        """Devuelve una descripción legible del comando, separada por ``|``."""
        locations: dict[str, Location] = dict(game_map.provinces)
        locations.update(game_map.seas)
        provinces = game_map.provinces

        try:
            report: list[str] = []
            target_type: str | None = None

            actor_type, actor_id = command.actor.split(maxsplit=1)

            if actor_type in ("A", "F", "G"):
                actor_name = GameTables.actors.get(actor_type, actor_type)
                loc_name = (
                    locations[actor_id].name if actor_id in locations else actor_id
                )
                report.append(f"{actor_name} de {loc_name}")
            elif actor_type == "E":
                expense_info = GameTables.expenses.get(actor_id)
                if expense_info is None:
                    report.append(actor_id)
                else:
                    report.append(expense_info["text"])
                    target_type = expense_info["target_type"]

            if actor_type in ("A", "F", "G"):
                orders_table = (
                    GameTables.maintenance_orders
                    if turn_number % 4 == 1
                    else GameTables.military_orders
                )
                command_info = orders_table.get(command.command)
                if command_info is None:
                    report.append(command.command)
                else:
                    report.append(command_info["text"])
                    target_type = command_info["target_type"]

            if target_type:
                CommandReporter._append_target_report(
                    report,
                    command,
                    target_type,
                    locations,
                    provinces,
                )

            if actor_type == "E":
                report.append(f"{command.command} ducados")

            return "|".join(report)
        except (KeyError, ValueError, IndexError) as error:
            return f"Orden inválida ({error})"

    @staticmethod
    def _append_target_report(
        report: list[str],
        command: Command,
        target_type: str,
        locations: dict[str, Location],
        provinces: dict[str, Province],
    ) -> None:
        """Añade el objetivo formateado del comando cuando está presente."""
        target = command.target
        if target is None:
            return

        if target_type == "army_ext":
            parts = target.split()
            actor_name = GameTables.actors.get(parts[0], parts[0])
            prov_name = provinces[parts[1]].name if parts[1] in provinces else parts[1]
            if len(parts) > 2:
                power_name = GameTables.powers.get(parts[2], parts[2])
                report.append(f"{actor_name} de {prov_name} ({power_name})")
            else:
                report.append(f"{actor_name} de {prov_name}")

        elif target_type == "location":
            report.append(locations[target].name if target in locations else target)

        elif target_type == "location_ext":
            parts = target.split()
            loc_name = locations[parts[0]].name if parts[0] in locations else parts[0]
            if len(parts) > 1:
                power_name = GameTables.powers.get(parts[1], parts[1])
                report.append(f"{loc_name} ({power_name})")
            else:
                report.append(loc_name)

        elif target_type == "province":
            report.append(provinces[target].name if target in provinces else target)

        elif target_type == "power":
            report.append(GameTables.powers.get(target, target))

        elif target_type == "unit":
            parts = target.split()
            actor_name = GameTables.actors.get(parts[0], parts[0])
            prov_name = provinces[parts[1]].name if parts[1] in provinces else parts[1]
            report.append(f"{actor_name} de {prov_name}")

        elif target_type == "unit_type":
            if target == "0":
                report.append("Desbandar")
            else:
                report.append(GameTables.actors.get(target, target))
