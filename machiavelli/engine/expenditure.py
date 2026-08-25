# machiavelli/engine/expenditure.py

from ..game.command import Command
from ..game.events import EventType, TurnEvent
from ..game.game import Game
from ..game.player import Player


class ExpenditureProcessor:
    """
    Procesa la Fase de Gastos (Expenditure Phase).
    Recorre las órdenes en orden FIFO, descuenta los ducados de la tesorería
    y elimina las órdenes sintácticamente inválidas o sin fondos suficientes.
    """

    def __init__(self, game: Game):
        self.game = game

    def _process_player_expenses(self, player: Player) -> list[Command]:
        """Procesa los gastos del jugador."""
        funded_commands = []
        rules = self.game.scenario.rules if self.game.scenario is not None else None
        for cmd in player.commands:
            if rules is not None and (
                (cmd.actor == "E A" and not rules.famine_active)
                or (cmd.actor == "E E" and not rules.assassinations_active)
            ):
                continue

            # Si no es una orden de gasto se conserva intacta silenciosamente
            if not cmd.is_valid_expense():
                funded_commands.append(cmd)
                continue

            # Validar el importe
            try:
                cost = int(cmd.command)
                if cost <= 0:
                    self.game.add_event(
                        TurnEvent.expense(
                            EventType.EXPENSE_SYNTAX_ERROR,
                            actor=player.player_id,
                            expense_type=cmd.actor.split()[1],
                            target=cmd.target,
                            amount=cmd.command,
                        ),
                    )
                    continue
            except (ValueError, TypeError):
                # El valor no es numérico
                self.game.add_event(
                    TurnEvent.expense(
                        EventType.EXPENSE_SYNTAX_ERROR,
                        actor=player.player_id,
                        expense_type=cmd.actor.split()[1],
                        target=cmd.target,
                        amount=cmd.command,
                    ),
                )
                continue

            # Comprobamos el saldo

            # Fondos suficientes -> se cobra SIEMPRE (sea legal o no) y se conserva
            if player.ducats >= cost:
                player.ducats -= cost
                funded_commands.append(cmd)
                self.game.add_event(
                    TurnEvent.expense(
                        EventType.EXPENSE,
                        actor=player.player_id,
                        expense_type=cmd.actor.split()[1],
                        target=cmd.target,
                        amount=cmd.command,
                    ),
                )

            # Fondos insuficientes -> se descarta la orden
            else:
                self.game.add_event(
                    TurnEvent.expense(
                        EventType.EXPENSE_NO_FUNDS,
                        actor=player.player_id,
                        expense_type=cmd.actor.split()[1],
                        target=cmd.target,
                        amount=cmd.command,
                    ),
                )
        return funded_commands

    def run(self) -> None:
        """Filtra y cobra las órdenes de gasto de todos los jugadores."""
        for player in self.game.players:
            player.commands = self._process_player_expenses(player)
