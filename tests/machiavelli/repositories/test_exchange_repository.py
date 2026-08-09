import sqlite3

import pytest

from machiavelli.db.database import upgrade_connection
from machiavelli.game.game import Game
from machiavelli.game.trading import ExchangeProposal, TradeResource
from machiavelli.repositories.exchange_repository import ExchangeRepository


def make_game(conn: sqlite3.Connection) -> Game:
    upgrade_connection(conn)
    game = Game.create_game("Intercambios", 8400, conn)
    conn.commit()
    return game


def test_exchange_repository_round_trips_and_replaces_authoritatively() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        game = make_game(conn)
        repository = ExchangeRepository(conn)
        proposals = [
            ExchangeProposal(
                "M", "V", TradeResource("assassin", "L"), TradeResource("ducats", 4)
            ),
            ExchangeProposal(
                "N", "L", TradeResource("ducats", 9), TradeResource("assassin", "V")
            ),
        ]
        game.pending_exchanges = proposals
        repository.replace_for_game(game)
        conn.commit()

        assert repository.get_by_game(game) == [proposals[1], proposals[0]]
        assert conn.execute(
            "SELECT power_a, power_b, proposer_power, give_type, give_value, "
            "receive_type, receive_value FROM exchange_proposals "
            "ORDER BY power_a, power_b"
        ).fetchall() == [
            ("L", "N", "N", "ducats", "9", "assassin", "V"),
            ("M", "V", "M", "assassin", "L", "ducats", "4"),
        ]

        replacement = ExchangeProposal(
            "L", "N", TradeResource("ducats", 2), TradeResource("ducats", 1)
        )
        game.pending_exchanges = [replacement]
        repository.replace_for_game(game)
        conn.commit()

        assert repository.get_by_game(game) == [replacement]
    finally:
        conn.close()


def test_exchange_repository_requires_persisted_game_id() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        upgrade_connection(conn)
        repository = ExchangeRepository(conn)
        for operation in (repository.replace_for_game, repository.get_by_game):
            with pytest.raises(
                ValueError,
                match="^No se pueden persistir intercambios de una partida sin ID$",
            ):
                operation(Game("Nueva"))
    finally:
        conn.close()


def test_exchange_repository_does_not_commit_outer_transaction() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        game = make_game(conn)
        repository = ExchangeRepository(conn)
        proposal = ExchangeProposal(
            "N", "L", TradeResource("ducats", 9), TradeResource("ducats", 4)
        )
        conn.execute("BEGIN")
        game.pending_exchanges = [proposal]
        repository.replace_for_game(game)
        assert conn.in_transaction
        conn.rollback()
        assert repository.get_by_game(game) == []
    finally:
        conn.close()
