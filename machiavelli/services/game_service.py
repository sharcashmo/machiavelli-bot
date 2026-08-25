"""Servicio de aplicación para casos de uso completos de partidas."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any

from machiavelli.db.database import DatabaseManager
from machiavelli.engine import GameEngine
from machiavelli.game import (
    Command,
    Game,
    Player,
    PlayerNotFoundException,
    TradeRuleException,
    TurnType,
)
from machiavelli.game.map import Map
from machiavelli.game.scenario import Scenario
from machiavelli.game.tables import GameTables
from machiavelli.game.trading import (
    ExchangeProposal,
    TradeResource,
    find_exchange_proposal_index,
    parse_trade_resource,
    player_has_trade_resource,
    transfer_trade_resource,
)
from machiavelli.repositories.game_repository import (
    GameNotFoundException,
    GameRepository,
)
from machiavelli.services.status_reporter import StatusReporter

from .command_reporter import CommandReporter
from .turn_reporter import TurnReporter

type PlayerInfo = tuple[str, int | None]
type ActorOption = tuple[str, str]

logger = logging.getLogger(__name__)

_trade_mutation_lock = Lock()
# ponytail: lock global por instancia; locks por partida si la contención lo exige
type GameStatusDict = dict[str, Any]


@contextmanager
def game_service_session(db_path: str | Path) -> Iterator[GameService]:
    """Proporciona un servicio sobre una conexión SQLite y la cierra siempre."""
    connection = DatabaseManager(db_path).get_connection()
    try:
        yield GameService(GameRepository(connection))
    finally:
        connection.close()


class GameService:
    """Orquesta las operaciones del dominio de la partida, el motor y el repositorio."""

    def __init__(self, repository: GameRepository) -> None:
        self.repo = repository

    @staticmethod
    def _resolve_scenario(scenario_name: str) -> tuple[str, Scenario]:
        scenarios = Scenario.load_scenarios()
        if scenario_name in scenarios:
            return scenario_name, scenarios[scenario_name]

        matches = [
            (scenario_id, scenario)
            for scenario_id, scenario in scenarios.items()
            if scenario.name.casefold() == scenario_name.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"Escenario desconocido: {scenario_name}")

    @staticmethod
    def _command_from_payload(
        game: Game,
        player: Player,
        payload: dict[str, Any],
    ) -> Command:
        actor = payload.get("actor")
        action = payload.get("command")
        target = payload.get("target")

        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("La orden requiere un actor de texto no vacío")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("La orden requiere un comando de texto no vacío")
        if target is not None and not isinstance(target, str):
            raise ValueError("El objetivo de la orden debe ser texto o None")

        return Command(
            game=game,
            player=player,
            actor=actor,
            command=action,
            target=target,
        )

    def create_game(
        self,
        name: str,
        channel_id: int,
        scenario_name: str | None = None,
    ) -> Game:
        """Crea y guarda una partida, inicializando opcionalmente su escenario."""
        if scenario_name is None:
            game = Game(name=name, channel_id=channel_id)
        else:
            scenario_id, scenario = self._resolve_scenario(scenario_name)
            game = Game(
                name=name,
                channel_id=channel_id,
                scenario_id=scenario_id,
                scenario=scenario,
                map=Map.load_map(exclude_ids=scenario.excluded_locations),
            )
        self.repo.save(game)
        return game

    def delete_game(self, channel_id: int) -> str:
        """Elimina la partida del canal, si existe."""
        game = self.repo.get_by_channel(channel_id)
        self.repo.delete(game)
        return game.name

    def get_game(self, channel_id: int) -> Game:
        """Carga un agregado completo de partida mediante el canal de Discord."""
        return self.repo.get_by_channel(channel_id)

    def get_game_status(self, channel_id: int) -> GameStatusDict:
        """Devuelve un resumen estructurado utilizando los atributos canónicos de la
        partida.
        """
        game = self.get_game(channel_id)
        return {
            "id": game.database_id,
            "name": game.name,
            "turn": game.turn_number,
            "scenario": game.scenario.name if game.scenario else None,
            "scenario_id": game.scenario_id,
            "players_count": len(game.players),
            "players": [
                (player.player_id, player.discord_id) for player in game.players
            ],
        }

    def add_player(
        self,
        channel_id: int,
        discord_id: int,
        player_id: str,
    ) -> list[PlayerInfo]:
        """Registra un jugador en el agregado y guarda la partida completa."""
        game = self.get_game(channel_id)
        game.add_player(player_id=player_id, discord_id=discord_id)
        self.repo.save(game)
        return [(player.player_id, player.discord_id) for player in game.players]

    def remove_player(
        self,
        channel_id: int,
        discord_id: int,
    ) -> tuple[str, list[PlayerInfo]]:
        """Elimina un jugador y sincroniza los jugadores y comandos persistidos."""
        game = self.get_game(channel_id)
        removed_player = game.remove_player(discord_id=discord_id)
        self.repo.save(game)
        remaining = [(player.player_id, player.discord_id) for player in game.players]
        return removed_player.player_id, remaining

    def set_scenario(self, channel_id: int, scenario_name: str) -> str:
        """Asigna un escenario conocido y actualiza el mapa antes de guardar."""
        scenario_id, scenario = self._resolve_scenario(scenario_name)
        game = self.get_game(channel_id)
        game.scenario_id = scenario_id
        game.scenario = scenario
        game.map = Map.load_map(exclude_ids=scenario.excluded_locations)
        self.repo.save(game)
        return scenario.name

    def update_deadlines(
        self,
        channel_id: int,
        *,
        weekly_deadline: str | None = None,
        next_deadline: str | None = None,
    ) -> str:
        """Guarda valores de fecha límite ya validados y devuelve el nombre de la
        partida.
        """
        game = self.get_game(channel_id)
        if weekly_deadline is not None:
            game.weekly_deadline = weekly_deadline
        if next_deadline is not None:
            game.next_deadline = next_deadline
        self.repo.save(game)
        return game.name

    def get_admin_status(self, channel_id: int) -> list[str]:
        """Devuelve el informe de estado de la partida para el administrador."""
        lines = StatusReporter.generate(self.get_game(channel_id))
        return lines

    def get_status_report(
        self,
        channel_id: int,
        discord_id: int,
    ) -> list[str]:
        """Devuelve el informe público de estado de la partida sin exponer la
        persistencia.
        """
        game = self.get_game(channel_id)
        player = self.resolve_player(game, discord_id)
        lines = StatusReporter.generate(game)
        lines.append(
            f"### :scroll: **Comandos actuales para "
            f"{GameTables.powers[player.power]}:**"
        )
        lines.extend(
            [
                CommandReporter.format_report(command, game.map, game.turn_number)
                for command in player.commands
            ]
        )
        return lines

    def get_turn_report(self, channel_id: int) -> list[str]:
        """Devuelve el informe persistido del último turno."""
        return TurnReporter.generate(self.get_game(channel_id))

    def run_turn(self, channel_id: int) -> list[str]:
        """Ejecuta un turno y después guarda atómicamente el agregado resultante."""
        game = self.get_game(channel_id)
        GameEngine(game).run()
        report_lines = TurnReporter.generate(game)
        self.repo.save(game)
        return report_lines

    def submit_command(
        self,
        channel_id: int,
        discord_id: int,
        command_payload: dict[str, Any],
        selected_power: str | None = None,
    ) -> list[str]:
        """Valida, registra y guarda una orden mediante los servicios canónicos."""
        game = self.get_game(channel_id)
        player = self.resolve_player(game, discord_id, selected_power)
        command = self._command_from_payload(game, player, command_payload)

        valid_actors = {code for code, _label in player.cmd_available_actors()}
        if command.actor not in valid_actors:
            raise ValueError(f"`{command.actor}` no es un actor válido.")

        valid_commands = {
            code for code, _label in player.cmd_available_commands(command.actor)
        }
        if command.command not in valid_commands:
            raise ValueError(f"`{command.command}` no es una orden válida.")

        valid_targets = [
            code
            for code, _label in player.cmd_available_targets(
                command.actor,
                command.command,
            )
        ]
        if (
            valid_targets
            and valid_targets[0] != ""
            and command.target not in valid_targets
        ):
            raise ValueError(f"`{command.target}` no es un objetivo válido.")

        turn_type = (
            TurnType.MAINTENANCE if game.turn_number % 4 == 1 else TurnType.CAMPAIGN
        )
        report = player.cmd_add_command(turn_type, command)
        self.repo.save(game)
        return report

    def submit_expense(
        self,
        channel_id: int,
        discord_id: int,
        *,
        expense: str,
        target: str,
        amount: str,
        selected_power: str | None = None,
    ) -> list[str]:
        """Valida, registra y guarda un gasto de campaña."""
        game = self.get_game(channel_id)
        player = self.resolve_player(game, discord_id, selected_power)

        valid_expenses = {code for code, _label in player.exp_available_expenses()}
        if expense not in valid_expenses:
            raise ValueError(f"`{expense}` no es un gasto válido.")

        valid_targets = {code for code, _label in player.exp_available_targets(expense)}
        if target not in valid_targets:
            raise ValueError(f"`{target}` no es un objetivo válido.")

        valid_amounts = {
            code for code, _label in player.exp_available_amounts(expense, target)
        }
        if amount not in valid_amounts:
            raise ValueError(f"`{amount}` no es una cantidad válida.")

        command = Command(
            game=game,
            player=player,
            actor=expense,
            command=amount,
            target=target,
        )
        report = player.cmd_add_command(TurnType.CAMPAIGN, command)
        self.repo.save(game)
        return report

    def resolve_player(
        self,
        game: Game,
        discord_id: int,
        selected_power: str | None = None,
    ) -> Player:
        """Resuelve un jugador mediante la potencia o el identificador de jugador
        seleccionados, o mediante su cuenta de Discord.
        """
        if selected_power:
            selected = selected_power.casefold()
            player = next(
                (
                    candidate
                    for candidate in game.players
                    if candidate.player_id.casefold() == selected
                    or (
                        candidate.power is not None
                        and candidate.power.casefold() == selected
                    )
                ),
                None,
            )
            if player is None:
                raise PlayerNotFoundException(
                    f"No existe la potencia o jugador '{selected_power}'."
                )
            return player

        player = next(
            (
                candidate
                for candidate in game.players
                if candidate.discord_id == discord_id
            ),
            None,
        )
        if player is None:
            raise PlayerNotFoundException(
                "Tu cuenta no está vinculada a ningún jugador en esta partida."
            )
        return player

    def get_available_actors(
        self,
        channel_id: int,
        discord_id: int,
        selected_power: str | None = None,
    ) -> list[ActorOption]:
        """Devuelve opciones de actores sin exponer errores de búsqueda al
        autocompletado.
        """
        try:
            game = self.get_game(channel_id)
            player = self.resolve_player(game, discord_id, selected_power)
            return player.cmd_available_actors()
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_available_commands(
        self,
        channel_id: int,
        discord_id: int,
        actor: str,
        selected_power: str | None = None,
    ) -> list[ActorOption]:
        """Devuelve opciones de comandos para un actor, o ninguna opción si falla la
        búsqueda.
        """
        try:
            game = self.get_game(channel_id)
            player = self.resolve_player(game, discord_id, selected_power)
            return player.cmd_available_commands(actor)
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_available_targets(
        self,
        channel_id: int,
        discord_id: int,
        actor: str,
        command: str,
        selected_power: str | None = None,
    ) -> list[ActorOption]:
        """Devuelve opciones de objetivos para una orden, o ninguna opción si falla la
        búsqueda.
        """
        try:
            game = self.get_game(channel_id)
            player = self.resolve_player(game, discord_id, selected_power)
            return player.cmd_available_targets(actor, command)
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_available_expenses(
        self,
        channel_id: int,
        discord_id: int,
        selected_power: str | None = None,
    ) -> list[ActorOption]:
        """Devuelve opciones de gastos, o ninguna opción si falla la búsqueda."""
        try:
            game = self.get_game(channel_id)
            player = self.resolve_player(game, discord_id, selected_power)
            return player.exp_available_expenses()
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_expense_targets(
        self,
        channel_id: int,
        discord_id: int,
        expense: str,
        selected_power: str | None = None,
    ) -> list[ActorOption]:
        """Devuelve opciones de objetivos para un gasto, o ninguna opción si falla la
        búsqueda.
        """
        try:
            game = self.get_game(channel_id)
            player = self.resolve_player(game, discord_id, selected_power)
            return player.exp_available_targets(expense)
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_expense_amounts(
        self,
        channel_id: int,
        discord_id: int,
        expense: str,
        target: str,
        selected_power: str | None = None,
    ) -> list[ActorOption]:
        """Devuelve opciones de importes para un gasto, o ninguna opción si falla la
        búsqueda.
        """
        try:
            game = self.get_game(channel_id)
            player = self.resolve_player(game, discord_id, selected_power)
            return player.exp_available_amounts(expense, target)
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_active_powers(self, channel_id: int) -> list[str]:
        """Devuelve los identificadores de las potencias asignadas en el orden canónico
        de los jugadores.
        """
        return [
            player.power
            for player in self.get_game(channel_id).players
            if player.power is not None
        ]

    def _resolve_trade_parties(
        self,
        game: Game,
        discord_id: int,
        give_to: str,
    ) -> tuple[Player, str, Player, str]:
        """Resuelve el actor asignado y otra potencia asignada."""
        actor = self.resolve_player(game, discord_id)
        actor_power = actor.power
        if actor_power is None:
            raise PlayerNotFoundException(
                "Tu cuenta no tiene una facción asignada en esta partida."
            )

        target = give_to.casefold()
        counterparty = next(
            (
                player
                for player in game.players
                if player is not actor
                and player.power is not None
                and player.power.casefold() == target
            ),
            None,
        )
        if counterparty is None or counterparty.power is None:
            raise TradeRuleException(
                "La facción de destino no está asignada a otro jugador de esta partida."
            )
        return actor, actor_power, counterparty, counterparty.power

    @staticmethod
    def _trade_resource_text(resource: TradeResource) -> str:
        """Formatea un recurso para una respuesta privada del servicio."""
        if resource.kind == "ducats":
            assert isinstance(resource.value, int)
            unit = "ducado" if resource.value == 1 else "ducados"
            return f"{resource.value} {unit}"
        assert isinstance(resource.value, str)
        return f"una ficha de asesinato contra {GameTables.powers[resource.value]}"

    def give_resource(
        self,
        channel_id: int,
        discord_id: int,
        *,
        give_to: str,
        give_type: str,
        give_value: str,
    ) -> str:
        """Transfiere directamente un recurso entre dos potencias asignadas."""
        with _trade_mutation_lock:
            game = self.get_game(channel_id)
            actor, actor_power, receiver, receiver_power = self._resolve_trade_parties(
                game, discord_id, give_to
            )
            resource = parse_trade_resource(
                game.require_scenario(), give_type, give_value
            )
            transfer_trade_resource(actor, receiver, resource)
            self.repo.save(game)

            power_a, power_b = sorted((actor_power, receiver_power))
            logger.info(
                "Operación privada de trading",
                extra={
                    "game_id": game.database_id,
                    "operation": "trade_give",
                    "power_a": power_a,
                    "power_b": power_b,
                },
            )

            resource_text = self._trade_resource_text(resource)
            counterparty = GameTables.powers[receiver_power]
            if resource.kind == "assassin":
                return f"Has dado a {counterparty} {resource_text}."
            return f"Has dado {resource_text} a {counterparty}."

    def exchange_resources(
        self,
        channel_id: int,
        discord_id: int,
        *,
        give_to: str,
        give_type: str,
        give_value: str,
        receive_type: str,
        receive_value: str,
    ) -> str:
        """Crea, cancela, sustituye o ejecuta una propuesta de intercambio."""
        with _trade_mutation_lock:
            game = self.get_game(channel_id)
            actor, actor_power, _counterparty, counterparty_power = (
                self._resolve_trade_parties(game, discord_id, give_to)
            )
            if give_value == "0" or receive_value == "0":
                return self._cancel_pending_exchange(
                    game, actor_power, counterparty_power
                )

            scenario = game.require_scenario()
            give = parse_trade_resource(scenario, give_type, give_value)
            receive = parse_trade_resource(scenario, receive_type, receive_value)
            new = ExchangeProposal(actor_power, counterparty_power, give, receive)
            existing_index = find_exchange_proposal_index(
                game.pending_exchanges, new.pair_key
            )
            if existing_index is None:
                return self._store_pending_exchange(game, actor, new, None)

            old = game.pending_exchanges[existing_index]
            if not old.is_exact_inverse(new):
                return self._store_pending_exchange(game, actor, new, existing_index)

            old_proposer = next(
                (
                    player
                    for player in game.players
                    if player.power == old.proposer_power
                ),
                None,
            )
            if old_proposer is None:
                raise TradeRuleException(
                    "La facción de destino no está asignada a otro jugador de esta "
                    "partida."
                )
            if not player_has_trade_resource(old_proposer, old.give):
                proposer_name = GameTables.powers[old.proposer_power]
                raise TradeRuleException(
                    f"{proposer_name} ya no dispone de "
                    f"{self._trade_resource_text(old.give)} para completar el "
                    "intercambio."
                )
            if not player_has_trade_resource(actor, new.give):
                actor_name = GameTables.powers[actor_power]
                raise TradeRuleException(
                    f"{actor_name} ya no dispone de "
                    f"{self._trade_resource_text(new.give)} para completar el "
                    "intercambio."
                )

            transfer_trade_resource(old_proposer, actor, old.give)
            transfer_trade_resource(actor, old_proposer, new.give)
            game.pending_exchanges.pop(existing_index)
            self.repo.save(game)
            power_a, power_b = new.pair_key
            logger.info(
                "Operación privada de trading",
                extra={
                    "game_id": game.database_id,
                    "operation": "exchange_completed",
                    "power_a": power_a,
                    "power_b": power_b,
                },
            )
            counterparty = GameTables.powers[counterparty_power]
            return (
                f"Intercambio completado con {counterparty}: has dado "
                f"{self._trade_resource_text(new.give)} y has recibido "
                f"{self._trade_resource_text(new.receive)}."
            )

    def _cancel_pending_exchange(
        self,
        game: Game,
        actor_power: str,
        counterparty_power: str,
    ) -> str:
        """Cancela el intercambio pendiente para un par sin orden."""
        power_a, power_b = sorted((actor_power, counterparty_power))
        pair_key = (power_a, power_b)
        index = find_exchange_proposal_index(game.pending_exchanges, pair_key)
        counterparty = GameTables.powers[counterparty_power]
        if index is None:
            logger.info(
                "Operación privada de trading",
                extra={
                    "game_id": game.database_id,
                    "operation": "exchange_cancel_noop",
                    "power_a": power_a,
                    "power_b": power_b,
                },
            )
            return f"No había ningún intercambio pendiente con {counterparty}."

        game.pending_exchanges.pop(index)
        self.repo.save(game)
        logger.info(
            "Operación privada de trading",
            extra={
                "game_id": game.database_id,
                "operation": "exchange_cancelled",
                "power_a": power_a,
                "power_b": power_b,
            },
        )
        return f"Intercambio pendiente con {counterparty} cancelado."

    def _store_pending_exchange(
        self,
        game: Game,
        actor: Player,
        proposal: ExchangeProposal,
        existing_index: int | None,
    ) -> str:
        """Guarda o sustituye una propuesta después de comprobar la oferta de su
        propietario.
        """
        if not player_has_trade_resource(actor, proposal.give):
            if proposal.give.kind == "ducats":
                raise TradeRuleException("No tienes suficientes ducados.")
            assert isinstance(proposal.give.value, str)
            target = GameTables.powers[proposal.give.value]
            raise TradeRuleException(
                f"No tienes una ficha de asesinato contra {target}."
            )

        operation = "exchange_proposed"
        if existing_index is None:
            game.pending_exchanges.append(proposal)
        else:
            operation = "exchange_replaced"
            game.pending_exchanges[existing_index] = proposal

        self.repo.save(game)
        power_a, power_b = proposal.pair_key
        logger.info(
            "Operación privada de trading",
            extra={
                "game_id": game.database_id,
                "operation": operation,
                "power_a": power_a,
                "power_b": power_b,
            },
        )
        counterparty = GameTables.powers[proposal.counterparty_power]
        give_text = self._trade_resource_text(proposal.give)
        receive_text = self._trade_resource_text(proposal.receive)
        if existing_index is None:
            return (
                f"Intercambio propuesto a {counterparty}: das {give_text} y "
                f"pides {receive_text}."
            )
        return (
            f"Has sustituido el intercambio pendiente con {counterparty}: das "
            f"{give_text} y pides {receive_text}."
        )

    def get_trade_counterparties(
        self,
        channel_id: int,
        discord_id: int,
    ) -> list[ActorOption]:
        """Devuelve las potencias asignadas distintas del actor solicitante."""
        try:
            game = self.get_game(channel_id)
            actor = self.resolve_player(game, discord_id)
            if actor.power is None:
                return []
            return [
                (player.power, GameTables.powers[player.power])
                for player in game.players
                if player is not actor and player.power is not None
            ]
        except (GameNotFoundException, PlayerNotFoundException):
            return []

    def get_trade_resource_types(self, channel_id: int) -> list[ActorOption]:
        """Devuelve los tipos de recursos habilitados por el escenario activo."""
        try:
            scenario = self.get_game(channel_id).require_scenario()
            types: list[ActorOption] = [("ducats", "Ducados")]
            if scenario.rules.assassinations_active:
                types.append(("assassin", "Ficha de asesinato"))
            return types
        except GameNotFoundException:
            return []

    def get_trade_assassin_targets(
        self,
        channel_id: int,
    ) -> list[ActorOption]:
        """Devuelve todas las potencias del escenario como posibles objetivos de
        asesinato.
        """
        try:
            scenario = self.get_game(channel_id).require_scenario()
            if not scenario.rules.assassinations_active:
                return []
            return [
                (power_code, GameTables.powers[power_code])
                for power_code in scenario.powers
            ]
        except GameNotFoundException:
            return []
