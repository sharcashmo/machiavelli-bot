"""SQLite persistence for pending exchange proposals."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from machiavelli.game.trading import ExchangeProposal, TradeResource

if TYPE_CHECKING:
    from machiavelli.game.game import Game


class ExchangeRepository:
    """Persist the authoritative pending proposals of one game."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def _game_id(game: Game) -> int:
        if game.database_id is None:
            raise ValueError(
                "No se pueden persistir intercambios de una partida sin ID"
            )
        return game.database_id

    def _replace_for_game(self, game: Game) -> None:
        game_id = self._game_id(game)
        self.conn.execute(
            "DELETE FROM exchange_proposals WHERE game_id = ?", (game_id,)
        )
        for proposal in game.pending_exchanges:
            power_a, power_b = proposal.pair_key
            self.conn.execute(
                """
                INSERT INTO exchange_proposals (
                    game_id, power_a, power_b, proposer_power,
                    give_type, give_value, receive_type, receive_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    power_a,
                    power_b,
                    proposal.proposer_power,
                    proposal.give.kind,
                    str(proposal.give.value),
                    proposal.receive.kind,
                    str(proposal.receive.value),
                ),
            )

    def replace_for_game(self, game: Game) -> None:
        """Replace all persisted proposals without owning an outer transaction."""
        if self.conn.in_transaction:
            self._replace_for_game(game)
            return
        with self.conn:
            self._replace_for_game(game)

    def get_by_game(self, game: Game) -> list[ExchangeProposal]:
        """Load pending proposals ordered by their canonical pair."""
        game_id = self._game_id(game)
        rows = self.conn.execute(
            """
            SELECT power_a, power_b, proposer_power,
                give_type, give_value, receive_type, receive_value
            FROM exchange_proposals
            WHERE game_id = ?
            ORDER BY power_a, power_b
            """,
            (game_id,),
        ).fetchall()

        proposals: list[ExchangeProposal] = []
        for row in rows:
            power_a, power_b, proposer_power = row[:3]
            counterparty_power = power_b if proposer_power == power_a else power_a
            give_value = int(row[4]) if row[3] == "ducats" else row[4]
            receive_value = int(row[6]) if row[5] == "ducats" else row[6]
            proposals.append(
                ExchangeProposal(
                    proposer_power,
                    counterparty_power,
                    TradeResource(row[3], give_value),
                    TradeResource(row[5], receive_value),
                )
            )
        return proposals
