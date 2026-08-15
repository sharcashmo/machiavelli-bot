"""Reglas de envío y sustitución de órdenes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from machiavelli.engine.exceptions import TooManyExpenses
from machiavelli.game.map import MovementMode, Province
from machiavelli.game.player import TurnType

if TYPE_CHECKING:
    from machiavelli.game.command import Command
    from machiavelli.game.game import Game
    from machiavelli.game.player import Player


class OrderProcessor:
    """Valida y registra las órdenes según el tipo de turno activo."""

    def __init__(self, game: Game) -> None:
        self.game = game

    def process_command(
        self,
        player: Player,
        turn_type: TurnType,
        command: Command,
    ) -> list[str]:
        """Registra una orden y devuelve su informe visible para el usuario en un orden
        estable.
        """
        report = [f"Orden `{command}` enviada."]

        if turn_type == TurnType.MAINTENANCE:
            report.extend(self._handle_maintenance_command(player, command))
        else:
            report.extend(self._handle_campaign_command(player, command))

        report.append("**Órdenes recibidas hasta ahora:**")
        report.extend(f"`{registered}`" for registered in player.commands)
        return report

    def _handle_maintenance_command(
        self,
        player: Player,
        command: Command,
    ) -> list[str]:
        """Registra una orden de mantenimiento, conservando una orden por actor."""
        current_commands = [
            current for current in player.commands if current.actor == command.actor
        ]
        if len(current_commands) > 1:
            raise ValueError(
                f"Se encontraron múltiples comandos para el actor '{command.actor}'"
            )

        if not current_commands:
            player.add_command(command)
            return []

        current = current_commands[0]
        messages = [f"Sustituye la orden anterior `{current}`."]
        current.command = command.command
        current.target = command.target

        actor_type, actor_id = command.actor.split(maxsplit=1)
        is_new_unit = (
            (actor_type == "A" and actor_id not in player.armies)
            or (actor_type == "F" and actor_id not in player.fleets)
            or (actor_type == "G" and actor_id not in player.garrisons)
        )
        if is_new_unit and command.command == "D":
            player.remove_command(current)

        return messages

    def _handle_campaign_command(
        self,
        player: Player,
        command: Command,
    ) -> list[str]:
        """Registra una orden, actualiza un gasto o añade un tramo de convoy."""
        actor_type, _actor_id = command.actor.split(maxsplit=1)
        if actor_type == "E":
            return self._handle_expense_command(player, command)

        current_commands = [
            current for current in player.commands if current.actor == command.actor
        ]
        if not current_commands:
            player.add_command(command)
            return []

        if self._validate_convoy(player, command, actor_type, current_commands):
            player.add_command(command)
            return []

        messages = [
            f"Sustituye la orden anterior `{current}`." for current in current_commands
        ]
        for current in current_commands:
            player.remove_command(current)
        player.add_command(command)
        return messages

    def _handle_expense_command(
        self,
        player: Player,
        command: Command,
    ) -> list[str]:
        """Crea, actualiza o elimina un gasto de campaña."""
        expense = next(
            (
                current
                for current in player.commands
                if current.actor == command.actor and current.target == command.target
            ),
            None,
        )
        if expense is not None:
            if int(command.command) == 0:
                message = f"Elimina el gasto anterior `{expense}`."
                player.remove_command(expense)
                return [message]

            message = f"Sustituye la orden anterior `{expense}`."
            expense.command = command.command
            return [message]

        expense_count = sum(
            current.actor.startswith("E ") for current in player.commands
        )
        if expense_count >= 4:
            raise TooManyExpenses()

        player.add_command(command)
        return []

    def _validate_convoy(
        self,
        player: Player,
        command: Command,
        actor_type: str,
        current_commands: list[Command],
    ) -> bool:
        """Devuelve si ``command`` amplía una ruta de convoy sintácticamente válida."""
        if actor_type != "A" or command.command != "A" or command.target is None:
            return False

        game_map = self.game.require_map()
        locations = game_map.provinces | game_map.seas
        fleets = [fleet for owner in self.game.players for fleet in owner.fleets]
        convoy = [
            current.target
            for current in player.commands
            if current.actor == command.actor and current.command == "A"
        ]

        if len(convoy) != len(current_commands):
            return False
        if not all(location in fleets for location in convoy):
            return False

        last_place = convoy[-1]
        if last_place is None:
            return False

        destination = locations.get(command.target)
        return (
            last_place in fleets
            and command.target
            in game_map.adjacent_locations(last_place, MovementMode.BOTH)
            and (command.target in fleets or isinstance(destination, Province))
        )
