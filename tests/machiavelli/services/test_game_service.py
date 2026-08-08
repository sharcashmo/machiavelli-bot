"""Integration tests for the phase-7 game application service."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from inspect import signature
from itertools import combinations
from os import getenv
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import Mock, patch

import pytest

from machiavelli import database
from machiavelli.engine import GameEngine
from machiavelli.engine.military import DislodgementResolverRequired
from machiavelli.events import InvalidTurnEventError
from machiavelli.game import Command as PublicCommand
from machiavelli.game import (
    DuplicatePlayerException,
    PlayerNotFoundException,
    TradeRuleException,
)
from machiavelli.game import Game as PublicGame
from machiavelli.game import Player as PublicPlayer
from machiavelli.game.command import Command
from machiavelli.game.game import Game
from machiavelli.game.player import Player
from machiavelli.game.scenario import Power, Rules, Scenario, VictoryConditions
from machiavelli.game.trading import ExchangeProposal, TradeResource
from machiavelli.repositories.game_repository import GameRepository
from machiavelli.services import GameService, game_service_session


def make_service(conn: sqlite3.Connection) -> GameService:
    database.upgrade_connection(conn)
    return GameService(GameRepository(conn))


def make_trade_service(
    channel_id: int = 7100,
) -> tuple[sqlite3.Connection, GameService]:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    service = make_service(conn)
    service.create_game("Trading", channel_id, "Be")
    service.add_player(channel_id, 1, "P1")
    service.add_player(channel_id, 2, "P2")
    game = service.get_game(channel_id)
    game.players[0].power = "N"
    game.players[0].ducats = 20
    game.players[0].ass_counters = ["V"]
    game.players[1].power = "L"
    service.repo.save(game)
    return conn, service


def test_game_service_session_builds_once_and_closes_on_success() -> None:
    connection = Mock(name="connection")
    manager = Mock(name="manager")
    manager.get_connection.return_value = connection
    repository = Mock(name="repository")
    service = Mock(name="service")
    db_path = Path("game.db")

    with (
        patch(
            "machiavelli.services.game_service.DatabaseManager",
            return_value=manager,
        ) as manager_class,
        patch(
            "machiavelli.services.game_service.GameRepository",
            return_value=repository,
        ) as repository_class,
        patch(
            "machiavelli.services.game_service.GameService",
            return_value=service,
        ) as service_class,
    ):
        with game_service_session(db_path) as yielded:
            assert yielded is service

    manager_class.assert_called_once_with(db_path)
    manager.get_connection.assert_called_once_with()
    repository_class.assert_called_once_with(connection)
    service_class.assert_called_once_with(repository)
    connection.close.assert_called_once_with()


def test_game_service_session_closes_on_exception() -> None:
    connection = Mock(name="connection")
    manager = Mock(name="manager")
    manager.get_connection.return_value = connection
    failure = RuntimeError("boom")

    with (
        patch(
            "machiavelli.services.game_service.DatabaseManager",
            return_value=manager,
        ),
        pytest.raises(RuntimeError) as caught,
    ):
        with game_service_session("game.db"):
            raise failure

    assert caught.value is failure
    connection.close.assert_called_once_with()


def test_create_load_and_status_use_the_canonical_domain() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        service = make_service(conn)

        created = service.create_game("Integración", 7001, "Be")
        loaded = service.get_game(7001)
        status = service.get_game_status(7001)

        assert PublicGame is Game
        assert PublicPlayer is Player
        assert PublicCommand is Command
        assert isinstance(created, Game)
        assert isinstance(loaded, Game)
        assert created.database_id == loaded.database_id
        assert loaded.scenario_id == "Be"
        assert loaded.scenario is not None
        assert loaded.map is not None
        assert status == {
            "id": created.database_id,
            "name": "Integración",
            "turn": 0,
            "scenario": "The balance of power (six players)",
            "scenario_id": "Be",
            "players_count": 0,
            "players": [],
        }


def test_add_remove_and_resolve_player_persist_the_authoritative_collection() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        service = make_service(conn)
        service.create_game("Jugadores", 7002, "Be")

        assert service.add_player(7002, 101, "P1") == [("P1", 101)]
        assert service.add_player(7002, 202, "P2") == [("P1", 101), ("P2", 202)]

        game = service.get_game(7002)
        game.players[0].power = "M"
        game.players[0].commands = [
            Command(game, game.players[0], "A milan", "H", None)
        ]
        service.repo.save(game)

        assert service.resolve_player(game, 101).player_id == "P1"
        assert service.resolve_player(game, 0, "M").player_id == "P1"
        assert service.resolve_player(game, 0, "p2").discord_id == 202

        removed, remaining = service.remove_player(7002, 101)
        assert removed == "P1"
        assert remaining == [("P2", 202)]
        assert [player.player_id for player in service.get_game(7002).players] == ["P2"]
        assert conn.execute(
            "SELECT COUNT(*) FROM players WHERE player_id = 'P1'"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM commands WHERE player_id = 'P1'"
        ).fetchone() == (0,)

        with pytest.raises(DuplicatePlayerException):
            service.add_player(7002, 202, "P3")
        with pytest.raises(PlayerNotFoundException):
            service.remove_player(7002, 999)


def test_submit_and_replace_command_survive_reload() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        service = make_service(conn)
        service.create_game("Órdenes", 7003, "Be")
        service.add_player(7003, 303, "P1")

        game = service.get_game(7003)
        game.turn_number = 2
        game.players[0].armies = ["milan"]
        service.repo.save(game)

        first_report = service.submit_command(
            7003,
            303,
            {"actor": "A milan", "command": "H", "target": None},
        )
        replacement_report = service.submit_command(
            7003,
            303,
            {"actor": "A milan", "command": "A", "target": "pavia"},
        )

        loaded = service.get_game(7003)
        commands = loaded.players[0].commands
        assert first_report[0].startswith("Orden `")
        assert any("Sustituye la orden anterior" in line for line in replacement_report)
        assert len(commands) == 1
        assert commands[0].game is loaded
        assert commands[0].player is loaded.players[0]
        assert (commands[0].actor, commands[0].command, commands[0].target) == (
            "A milan",
            "A",
            "pavia",
        )


def test_turn_boundaries_do_not_accept_a_dislodgement_resolver() -> None:
    assert "dislodgement_resolver" not in signature(GameService.run_turn).parameters
    assert "dislodgement_resolver" not in signature(GameEngine).parameters


def test_get_turn_report_uses_the_reporter_and_returns_its_lines() -> None:
    repository = Mock(name="repository")
    game = Mock(name="game")
    repository.get_by_channel.return_value = game
    service = GameService(repository)

    with patch(
        "machiavelli.services.game_service.TurnReporter.generate",
        return_value=["report one", "report two"],
    ) as generate:
        report = service.get_turn_report(7004)

    assert report == ["report one", "report two"]
    generate.assert_called_once_with(game)
    repository.save.assert_not_called()


def test_run_turn_uses_strict_load_engine_reporter_save_order() -> None:
    calls: list[str] = []
    repository = Mock(name="repository")
    game = Mock(name="game")
    repository.get_by_channel.side_effect = lambda _channel_id: (
        calls.append("load") or game
    )
    repository.save.side_effect = lambda _game: calls.append("save")
    service = GameService(repository)

    with (
        patch("machiavelli.services.game_service.GameEngine") as engine_class,
        patch("machiavelli.services.game_service.TurnReporter.generate") as generate,
    ):
        engine_class.return_value.run.side_effect = lambda: calls.append("engine")
        generate.side_effect = lambda _game: calls.append("reporter") or ["turn report"]
        report = service.run_turn(7005)

    assert report == ["turn report"]
    assert calls == ["load", "engine", "reporter", "save"]
    engine_class.assert_called_once_with(game)
    engine_class.return_value.run.assert_called_once_with()
    generate.assert_called_once_with(game)
    repository.save.assert_called_once_with(game)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("engine failed"),
        InvalidTurnEventError(row_id=4, event_type="broken"),
        DislodgementResolverRequired("retreat required"),
    ],
)
def test_run_turn_does_not_save_when_engine_fails(failure: Exception) -> None:
    repository = Mock(name="repository")
    repository.get_by_channel.return_value = Mock(name="game")
    service = GameService(repository)

    with (
        patch("machiavelli.services.game_service.GameEngine") as engine_class,
        pytest.raises(type(failure)) as caught,
    ):
        engine_class.return_value.run.side_effect = failure
        service.run_turn(7006)

    assert caught.value is failure
    repository.save.assert_not_called()


def test_run_turn_rolls_back_persistence_when_save_fails() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        service = make_service(conn)
        service.create_game("Rollback", 7007, "Be")
        before = conn.execute(
            "SELECT name, channel_id, scenario_id, turn_number FROM games"
        ).fetchall()
        conn.execute(
            """
            CREATE TRIGGER fail_game_update
            BEFORE UPDATE ON games
            BEGIN
                SELECT RAISE(ABORT, 'forced save failure');
            END
            """
        )

        with (
            patch("machiavelli.services.game_service.GameEngine") as engine_class,
            patch(
                "machiavelli.services.game_service.TurnReporter.generate",
                return_value=["turn report"],
            ),
            pytest.raises(sqlite3.IntegrityError),
        ):
            engine_class.return_value.run.side_effect = lambda: setattr(
                engine_class.call_args.args[0],
                "turn_number",
                1,
            )
            service.run_turn(7007)

        after = conn.execute(
            "SELECT name, channel_id, scenario_id, turn_number FROM games"
        ).fetchall()
        assert after == before


def test_run_turn_does_not_save_when_reporting_fails() -> None:
    repository = Mock(name="repository")
    game = Mock(name="game")
    repository.get_by_channel.return_value = game
    service = GameService(repository)
    failure = InvalidTurnEventError(row_id=9, event_type="broken")

    with (
        patch("machiavelli.services.game_service.GameEngine"),
        patch(
            "machiavelli.services.game_service.TurnReporter.generate",
            side_effect=failure,
        ),
        pytest.raises(InvalidTurnEventError) as caught,
    ):
        service.run_turn(7006)

    assert caught.value is failure
    repository.save.assert_not_called()


def test_run_turn_persists_and_can_continue_after_reopening_connection() -> None:
    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "phase7.db"
        database.upgrade(str(db_path))

        with closing(sqlite3.connect(db_path)) as conn:
            service = GameService(GameRepository(conn))
            service.create_game("Reinicio", 7004, "Be")
            for index in range(6):
                service.add_player(7004, 400 + index, f"P{index + 1}")

            report = service.run_turn(7004)
            persisted = service.get_game(7004)

            assert report
            assert persisted.turn_number == 1
            assert all(player.power is not None for player in persisted.players)
            assert all(player.commands == [] for player in persisted.players)
            assert persisted.turn_events

        with closing(sqlite3.connect(db_path)) as reopened:
            continued_service = GameService(GameRepository(reopened))
            restored = continued_service.get_game(7004)

            assert restored.turn_number == 1
            assert len(restored.players) == 6
            assert all(player.game is restored for player in restored.players)
            assert all(
                command.game is restored and command.player is player
                for player in restored.players
                for command in player.commands
            )
            assert restored.scenario is not None
            assert restored.map is not None


def test_give_resource_persists_ducats_and_saves_once() -> None:
    conn, service = make_trade_service()
    try:
        with patch.object(service.repo, "save", wraps=service.repo.save) as save:
            result = service.give_resource(
                7100,
                1,
                give_to="L",
                give_type="ducats",
                give_value="9",
            )

        assert result == "Has dado 9 ducados a Florence."
        save.assert_called_once()
        loaded = service.get_game(7100)
        assert loaded.players[0].ducats == 11
        assert loaded.players[1].ducats == 9
        assert loaded.turn_events == []
    finally:
        conn.close()


def test_give_resource_transfers_one_assassin_counter() -> None:
    conn, service = make_trade_service()
    try:
        result = service.give_resource(
            7100,
            1,
            give_to="L",
            give_type="assassin",
            give_value="V",
        )

        assert result == "Has dado a Florence una ficha de asesinato contra Venice."
        loaded = service.get_game(7100)
        assert loaded.players[0].ass_counters == []
        assert loaded.players[1].ass_counters == ["V"]
    finally:
        conn.close()


@pytest.mark.parametrize("turn_number", [1, 2])
def test_give_resource_has_no_phase_gate(turn_number: int) -> None:
    conn, service = make_trade_service(channel_id=7101 + turn_number)
    try:
        game = service.get_game(7101 + turn_number)
        game.turn_number = turn_number
        service.repo.save(game)
        assert (
            service.give_resource(
                7101 + turn_number,
                1,
                give_to="L",
                give_type="ducats",
                give_value="1",
            )
            == "Has dado 1 ducado a Florence."
        )
    finally:
        conn.close()


def test_give_resource_save_failure_is_rolled_back_after_reload() -> None:
    conn, service = make_trade_service(channel_id=7104)
    try:
        before = [
            (player.ducats, player.ass_counters)
            for player in service.get_game(7104).players
        ]
        conn.execute(
            """
            CREATE TRIGGER fail_trade_save
            BEFORE UPDATE ON players
            BEGIN
                SELECT RAISE(ABORT, 'forced trade save failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            service.give_resource(
                7104,
                1,
                give_to="L",
                give_type="ducats",
                give_value="9",
            )

        reloaded = service.get_game(7104)
        assert [
            (player.ducats, player.ass_counters) for player in reloaded.players
        ] == before
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("give_to", "give_type", "give_value", "message"),
    [
        (
            "N",
            "ducats",
            "1",
            "La facción de destino no está asignada a otro jugador de esta partida.",
        ),
        (
            "P2",
            "ducats",
            "1",
            "La facción de destino no está asignada a otro jugador de esta partida.",
        ),
        (
            "ZZ",
            "ducats",
            "1",
            "La facción de destino no está asignada a otro jugador de esta partida.",
        ),
        (
            "M",
            "ducats",
            "1",
            "La facción de destino no está asignada a otro jugador de esta partida.",
        ),
        (
            "L",
            "ducats",
            "0",
            "La cantidad de ducados debe ser un entero mayor que cero.",
        ),
        (
            "L",
            "assassin",
            "M",
            "No tienes una ficha de asesinato contra Milan.",
        ),
    ],
)
def test_give_resource_invalid_requests_do_not_save(
    give_to: str, give_type: str, give_value: str, message: str
) -> None:
    conn, service = make_trade_service(channel_id=7105)
    try:
        with patch.object(service.repo, "save") as save:
            with pytest.raises(TradeRuleException, match=f"^{message}$"):
                service.give_resource(
                    7105,
                    1,
                    give_to=give_to,
                    give_type=give_type,
                    give_value=give_value,
                )
        save.assert_not_called()
    finally:
        conn.close()


def test_give_resource_requires_assigned_actor_power() -> None:
    conn, service = make_trade_service(channel_id=7106)
    try:
        game = service.get_game(7106)
        game.players[0].power = None
        service.repo.save(game)

        with patch.object(service.repo, "save") as save:
            with pytest.raises(
                PlayerNotFoundException,
                match="^Tu cuenta no tiene una facción asignada en esta partida\\.$",
            ):
                service.give_resource(
                    7106,
                    1,
                    give_to="L",
                    give_type="ducats",
                    give_value="1",
                )
            with pytest.raises(PlayerNotFoundException):
                service.give_resource(
                    7106,
                    999,
                    give_to="L",
                    give_type="ducats",
                    give_value="1",
                )
        save.assert_not_called()
    finally:
        conn.close()


def test_give_resource_rejects_disabled_assassinations_without_save() -> None:
    conn, service = make_trade_service(channel_id=7109)
    try:
        game = service.get_game(7109)
        scenario = game.require_scenario()
        scenario.rules.assassinations_active = False

        with (
            patch(
                "machiavelli.services.game_service.Scenario.load_scenarios",
                return_value={"Be": scenario},
            ),
            patch.object(service.repo, "save") as save,
        ):
            with pytest.raises(
                TradeRuleException,
                match=(
                    "^Las fichas de asesinato no están disponibles en este "
                    "escenario\\.$"
                ),
            ):
                service.give_resource(
                    7109,
                    1,
                    give_to="L",
                    give_type="assassin",
                    give_value="V",
                )
        save.assert_not_called()
    finally:
        conn.close()


def test_give_resource_logs_only_private_pair_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn, service = make_trade_service(channel_id=7107)
    try:
        with caplog.at_level(logging.INFO, logger="machiavelli.services.game_service"):
            service.give_resource(
                7107,
                1,
                give_to="L",
                give_type="assassin",
                give_value="V",
            )

        record = next(
            record
            for record in caplog.records
            if getattr(record, "operation", None) == "trade_give"
        )
        assert record.game_id == service.get_game(7107).database_id
        assert record.operation == "trade_give"
        assert record.power_a == "L"
        assert record.power_b == "N"
        assert {
            key: getattr(record, key)
            for key in ("game_id", "operation", "power_a", "power_b")
        } == {
            "game_id": record.game_id,
            "operation": "trade_give",
            "power_a": "L",
            "power_b": "N",
        }
        for private_field in (
            "discord_id",
            "amount",
            "value",
            "give_value",
            "resource",
            "target",
            "assassin_target",
        ):
            assert not hasattr(record, private_field)
        assert "V" not in record.getMessage()
        assert "1" not in record.getMessage()
    finally:
        conn.close()


def test_concurrent_gives_load_sequentially_before_second_operation() -> None:
    conn, service = make_trade_service(channel_id=7108)
    try:
        service_two = GameService(service.repo)
        first_entered = threading.Event()
        release_first = threading.Event()
        second_loaded = threading.Event()
        first_resolve = service._resolve_trade_parties
        second_get = service_two.get_game

        def block_first(game: Game, discord_id: int, give_to: str):
            first_entered.set()
            if not release_first.wait(timeout=2):
                raise AssertionError("first give was not released")
            return first_resolve(game, discord_id, give_to)

        def observe_second_load(channel_id: int) -> Game:
            second_loaded.set()
            return second_get(channel_id)

        results: list[str] = []

        with (
            patch.object(service, "_resolve_trade_parties", side_effect=block_first),
            patch.object(service_two, "get_game", side_effect=observe_second_load),
        ):
            first = threading.Thread(
                target=lambda: results.append(
                    service.give_resource(
                        7108,
                        1,
                        give_to="L",
                        give_type="ducats",
                        give_value="1",
                    )
                )
            )
            second = threading.Thread(
                target=lambda: results.append(
                    service_two.give_resource(
                        7108,
                        1,
                        give_to="L",
                        give_type="ducats",
                        give_value="1",
                    )
                )
            )
            first.start()
            assert first_entered.wait(timeout=2)
            second.start()
            assert not second_loaded.wait(timeout=0.1)
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

        assert sorted(results) == [
            "Has dado 1 ducado a Florence.",
            "Has dado 1 ducado a Florence.",
        ]
        loaded = service.get_game(7108)
        assert loaded.players[0].ducats == 18
        assert loaded.players[1].ducats == 2
    finally:
        release_first.set() if "release_first" in locals() else None
        conn.close()


def make_proposal(
    proposer: str = "N",
    counterparty: str = "L",
    give: TradeResource | None = None,
    receive: TradeResource | None = None,
) -> ExchangeProposal:
    return ExchangeProposal(
        proposer,
        counterparty,
        give or TradeResource("ducats", 9),
        receive or TradeResource("assassin", "V"),
    )


def test_exchange_helpers_store_replace_and_cancel_without_moving_resources(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn, service = make_trade_service(channel_id=7110)
    try:
        with caplog.at_level(logging.INFO, logger="machiavelli.services.game_service"):
            game = service.get_game(7110)
            actor, _actor_power, _counterparty, _counterparty_power = (
                service._resolve_trade_parties(game, 1, "L")
            )
            proposal = make_proposal()
            before = [
                (player.ducats, player.ass_counters[:]) for player in game.players
            ]
            assert service._store_pending_exchange(game, actor, proposal, None) == (
                "Intercambio propuesto a Florence: das 9 ducados y pides "
                "una ficha de asesinato contra Venice."
            )

            stored = service.get_game(7110)
            assert stored.pending_exchanges == [proposal]
            assert [
                (player.ducats, player.ass_counters) for player in stored.players
            ] == before

            other = make_proposal(
                "N", "M", TradeResource("ducats", 2), TradeResource("ducats", 1)
            )
            game = service.get_game(7110)
            actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
            game.pending_exchanges.append(other)
            service.repo.save(game)

            replacement = make_proposal(
                "N", "L", TradeResource("ducats", 3), TradeResource("ducats", 4)
            )
            game = service.get_game(7110)
            actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
            assert service._store_pending_exchange(game, actor, replacement, 0) == (
                "Has sustituido el intercambio pendiente con Florence: das 3 ducados "
                "y pides 4 ducados."
            )
            assert service.get_game(7110).pending_exchanges == [replacement, other]

            cancel_game = service.get_game(7110)
            _, cancel_actor_power, _, cancel_counterparty_power = (
                service._resolve_trade_parties(cancel_game, 1, "L")
            )
            with patch.object(service.repo, "save", wraps=service.repo.save) as save:
                assert (
                    service._cancel_pending_exchange(
                        cancel_game, cancel_actor_power, cancel_counterparty_power
                    )
                    == "Intercambio pendiente con Florence cancelado."
                )
            save.assert_called_once_with(cancel_game)
            assert service.get_game(7110).pending_exchanges == [other]

            noop_game = service.get_game(7110)
            _, noop_actor_power, _, noop_counterparty_power = (
                service._resolve_trade_parties(noop_game, 1, "L")
            )
            with patch.object(service.repo, "save") as save:
                assert (
                    service._cancel_pending_exchange(
                        noop_game, noop_actor_power, noop_counterparty_power
                    )
                    == "No había ningún intercambio pendiente con Florence."
                )
            save.assert_not_called()

        operations = {
            record.operation: record
            for record in caplog.records
            if getattr(record, "operation", None)
            in {
                "exchange_proposed",
                "exchange_replaced",
                "exchange_cancelled",
                "exchange_cancel_noop",
            }
        }
        for operation in (
            "exchange_proposed",
            "exchange_replaced",
            "exchange_cancelled",
            "exchange_cancel_noop",
        ):
            record = operations[operation]
            assert {
                key: getattr(record, key)
                for key in ("game_id", "operation", "power_a", "power_b")
            } == {
                "game_id": stored.database_id,
                "operation": operation,
                "power_a": "L",
                "power_b": "N",
            }
    finally:
        conn.close()


def test_store_exchange_requires_actor_ownership_and_keeps_existing_row() -> None:
    conn, service = make_trade_service(channel_id=7111)
    try:
        game = service.get_game(7111)
        actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
        existing = make_proposal()
        service._store_pending_exchange(game, actor, existing, None)
        game = service.get_game(7111)
        actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
        actor.ducats = 0
        replacement = make_proposal(
            "N", "L", TradeResource("ducats", 3), TradeResource("ducats", 4)
        )

        with patch.object(service.repo, "save") as save:
            with pytest.raises(
                TradeRuleException,
                match="^No tienes suficientes ducados\\.$",
            ):
                service._store_pending_exchange(game, actor, replacement, 0)
        save.assert_not_called()
        assert service.get_game(7111).pending_exchanges == [existing]
    finally:
        conn.close()


@pytest.mark.parametrize("operation", ["create", "replace", "cancel"])
def test_exchange_helper_save_failures_preserve_reloaded_state(operation: str) -> None:
    conn, service = make_trade_service(channel_id=7112)
    try:
        before_resources = [
            (player.ducats, player.ass_counters[:])
            for player in service.get_game(7112).players
        ]
        if operation == "create":
            conn.execute(
                """
                CREATE TRIGGER fail_exchange_insert
                BEFORE INSERT ON exchange_proposals
                BEGIN SELECT RAISE(ABORT, 'forced exchange failure'); END
                """
            )
            game = service.get_game(7112)
            actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
            with pytest.raises(sqlite3.IntegrityError):
                service._store_pending_exchange(game, actor, make_proposal(), None)
        else:
            game = service.get_game(7112)
            actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
            existing = make_proposal()
            service._store_pending_exchange(game, actor, existing, None)
            if operation == "replace":
                conn.execute(
                    """
                    CREATE TRIGGER fail_exchange_insert
                    BEFORE INSERT ON exchange_proposals
                    BEGIN SELECT RAISE(ABORT, 'forced exchange failure'); END
                    """
                )
                replacement = make_proposal(
                    "N", "L", TradeResource("ducats", 3), TradeResource("ducats", 4)
                )
                game = service.get_game(7112)
                actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
                with pytest.raises(sqlite3.IntegrityError):
                    service._store_pending_exchange(game, actor, replacement, 0)
            else:
                conn.execute(
                    """
                    CREATE TRIGGER fail_exchange_delete
                    BEFORE DELETE ON exchange_proposals
                    BEGIN SELECT RAISE(ABORT, 'forced exchange failure'); END
                    """
                )
                game = service.get_game(7112)
                _, actor_power, _, counterparty_power = service._resolve_trade_parties(
                    game, 1, "L"
                )
                with pytest.raises(sqlite3.IntegrityError):
                    service._cancel_pending_exchange(
                        game, actor_power, counterparty_power
                    )
        restored = service.get_game(7112)
        expected_proposals = [] if operation == "create" else [existing]
        assert restored.pending_exchanges == expected_proposals
        assert [
            (player.ducats, player.ass_counters) for player in restored.players
        ] == before_resources
    finally:
        conn.close()


def test_run_turn_expires_exchanges_and_save_failure_preserves_previous_state() -> None:
    conn, service = make_trade_service(channel_id=7113)
    try:
        game = service.get_game(7113)
        actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
        service._store_pending_exchange(game, actor, make_proposal(), None)

        with (
            patch("machiavelli.services.game_service.GameEngine") as engine,
            patch(
                "machiavelli.services.game_service.TurnReporter.generate",
                return_value=[],
            ),
        ):
            engine.return_value.run.side_effect = lambda: engine.call_args.args[
                0
            ].advance_turn()
            assert service.run_turn(7113) == []
        assert service.get_game(7113).pending_exchanges == []

        game = service.get_game(7113)
        actor, _, _, _ = service._resolve_trade_parties(game, 1, "L")
        service._store_pending_exchange(game, actor, make_proposal(), None)
        conn.execute(
            """
            CREATE TRIGGER fail_turn_update
            BEFORE UPDATE ON games
            BEGIN SELECT RAISE(ABORT, 'forced turn failure'); END
            """
        )
        with (
            patch("machiavelli.services.game_service.GameEngine") as engine,
            patch(
                "machiavelli.services.game_service.TurnReporter.generate",
                return_value=[],
            ),
            pytest.raises(sqlite3.IntegrityError),
        ):
            engine.return_value.run.side_effect = lambda: engine.call_args.args[
                0
            ].advance_turn()
            service.run_turn(7113)

        restored = service.get_game(7113)
        assert restored.turn_number == 1
        assert restored.pending_exchanges == [make_proposal()]
    finally:
        conn.close()


@pytest.mark.parametrize(
    (
        "old_give_type",
        "old_give_value",
        "old_receive_type",
        "old_receive_value",
        "new_give_type",
        "new_give_value",
        "new_receive_type",
        "new_receive_value",
        "expected",
    ),
    [
        (
            "ducats",
            "9",
            "assassin",
            "V",
            "assassin",
            "V",
            "ducats",
            "9",
            "Intercambio completado con Naples: has dado una ficha de asesinato "
            "contra Venice y has recibido 9 ducados.",
        ),
        (
            "ducats",
            "9",
            "ducats",
            "4",
            "ducats",
            "4",
            "ducats",
            "9",
            "Intercambio completado con Naples: has dado 4 ducados y has recibido "
            "9 ducados.",
        ),
        (
            "assassin",
            "V",
            "assassin",
            "M",
            "assassin",
            "M",
            "assassin",
            "V",
            "Intercambio completado con Naples: has dado una ficha de asesinato "
            "contra Milan y has recibido una ficha de asesinato contra Venice.",
        ),
    ],
)
def test_exchange_resources_executes_exact_inverse_for_all_resource_pairs(
    old_give_type: str,
    old_give_value: str,
    old_receive_type: str,
    old_receive_value: str,
    new_give_type: str,
    new_give_value: str,
    new_receive_type: str,
    new_receive_value: str,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn, service = make_trade_service(channel_id=7200)
    try:
        game = service.get_game(7200)
        game.players[1].ass_counters = ["V", "M"]
        if old_receive_type == "ducats":
            game.players[1].ducats = 20
        service.repo.save(game)

        assert service.exchange_resources(
            7200,
            1,
            give_to="L",
            give_type=old_give_type,
            give_value=old_give_value,
            receive_type=old_receive_type,
            receive_value=old_receive_value,
        ).startswith("Intercambio propuesto")

        with (
            patch.object(service.repo, "save", wraps=service.repo.save) as save,
            caplog.at_level(logging.INFO, logger="machiavelli.services.game_service"),
        ):
            result = service.exchange_resources(
                7200,
                2,
                give_to="N",
                give_type=new_give_type,
                give_value=new_give_value,
                receive_type=new_receive_type,
                receive_value=new_receive_value,
            )

        assert result == expected
        save.assert_called_once()
        record = next(
            record
            for record in caplog.records
            if getattr(record, "operation", None) == "exchange_completed"
        )
        assert {
            key: getattr(record, key)
            for key in ("game_id", "operation", "power_a", "power_b")
        } == {
            "game_id": 1,
            "operation": "exchange_completed",
            "power_a": "L",
            "power_b": "N",
        }
        assert record.getMessage() == "Operación privada de trading"
        for private_field in (
            "discord_id",
            "amount",
            "value",
            "give_value",
            "resource",
            "target",
            "assassin_target",
        ):
            assert not hasattr(record, private_field)
        loaded = service.get_game(7200)
        assert loaded.pending_exchanges == []
        assert loaded.turn_events == []
        if old_give_type == "ducats" and old_receive_type == "assassin":
            assert (loaded.players[0].ducats, loaded.players[1].ducats) == (11, 9)
            assert loaded.players[0].ass_counters == ["V", "V"]
            assert loaded.players[1].ass_counters == ["M"]
        elif old_give_type == "ducats":
            assert (loaded.players[0].ducats, loaded.players[1].ducats) == (15, 25)
        else:
            assert (loaded.players[0].ducats, loaded.players[1].ducats) == (20, 0)
            assert loaded.players[0].ass_counters == ["M"]
            assert loaded.players[1].ass_counters == ["V", "V"]
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("give_value", "receive_value"),
    [("0", "also-invalid"), ("also-invalid", "0")],
)
def test_exchange_cancellation_precedes_resource_parsing(
    give_value: str, receive_value: str
) -> None:
    conn, service = make_trade_service(channel_id=7201)
    try:
        service.exchange_resources(
            7201,
            1,
            give_to="L",
            give_type="ducats",
            give_value="9",
            receive_type="assassin",
            receive_value="V",
        )

        with (
            patch("machiavelli.services.game_service.parse_trade_resource") as parse,
            patch.object(service, "_cancel_pending_exchange") as cancel,
        ):
            cancel.return_value = "cancelled"
            result = service.exchange_resources(
                7201,
                1,
                give_to="L",
                give_type="not-a-resource",
                give_value=give_value,
                receive_type="not-a-resource",
                receive_value=receive_value,
            )

        parse.assert_not_called()
        cancel.assert_called_once()

        assert result == "cancelled"

        with (
            patch("machiavelli.services.game_service.parse_trade_resource") as parse,
            patch.object(service, "_cancel_pending_exchange") as cancel,
            pytest.raises(
                TradeRuleException,
                match=(
                    "^La facción de destino no está asignada a otro jugador de "
                    "esta partida\\.$"
                ),
            ),
        ):
            service.exchange_resources(
                7201,
                1,
                give_to="ZZ",
                give_type="not-a-resource",
                give_value="0",
                receive_type="not-a-resource",
                receive_value="0",
            )

        parse.assert_not_called()
        cancel.assert_not_called()
    finally:
        conn.close()


@pytest.mark.parametrize("non_cancellation_value", ["00", "+0", " 0 "])
def test_exchange_nonzero_sentinels_parse_as_invalid_ducats_and_keep_proposal(
    non_cancellation_value: str,
) -> None:
    conn, service = make_trade_service(channel_id=7206)
    try:
        service.exchange_resources(
            7206,
            1,
            give_to="L",
            give_type="ducats",
            give_value="9",
            receive_type="assassin",
            receive_value="V",
        )
        before = service.get_game(7206).pending_exchanges.copy()

        with pytest.raises(
            TradeRuleException,
            match=("^La cantidad de ducados debe ser un entero mayor que cero\\.$"),
        ):
            service.exchange_resources(
                7206,
                1,
                give_to="L",
                give_type="ducats",
                give_value=non_cancellation_value,
                receive_type="ducats",
                receive_value="1",
            )

        assert service.get_game(7206).pending_exchanges == before
    finally:
        conn.close()


def test_exchange_cancellation_ignores_disabled_assassin_value() -> None:
    conn, service = make_trade_service(channel_id=7208)
    try:
        service.exchange_resources(
            7208,
            1,
            give_to="L",
            give_type="ducats",
            give_value="9",
            receive_type="assassin",
            receive_value="V",
        )
        scenario = service.get_game(7208).require_scenario()
        scenario.rules.assassinations_active = False

        with patch(
            "machiavelli.services.game_service.Scenario.load_scenarios",
            return_value={"Be": scenario},
        ):
            result = service.exchange_resources(
                7208,
                1,
                give_to="L",
                give_type="assassin",
                give_value="V",
                receive_type="ducats",
                receive_value="0",
            )

        assert result == "Intercambio pendiente con Florence cancelado."
        assert service.get_game(7208).pending_exchanges == []
    finally:
        conn.close()


def test_exchange_resources_delegates_non_inverse_replacement() -> None:
    conn, service = make_trade_service(channel_id=7207)
    try:
        service.exchange_resources(
            7207,
            1,
            give_to="L",
            give_type="ducats",
            give_value="9",
            receive_type="assassin",
            receive_value="V",
        )

        with patch.object(
            service, "_store_pending_exchange", return_value="stored"
        ) as store:
            result = service.exchange_resources(
                7207,
                1,
                give_to="L",
                give_type="ducats",
                give_value="3",
                receive_type="ducats",
                receive_value="4",
            )

        assert result == "stored"
        store.assert_called_once()
        proposal = store.call_args.args[2]
        assert proposal == ExchangeProposal(
            "N", "L", TradeResource("ducats", 3), TradeResource("ducats", 4)
        )
        assert store.call_args.args[3] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("discord_id", "expected"),
    [
        (
            1,
            "Naples ya no dispone de 9 ducados para completar el intercambio.",
        ),
        (
            2,
            "Florence ya no dispone de una ficha de asesinato contra Venice para "
            "completar el intercambio.",
        ),
    ],
)
def test_exchange_inverse_rechecks_both_current_offers(
    discord_id: int, expected: str
) -> None:
    conn, service = make_trade_service(channel_id=7202 + discord_id)
    try:
        channel_id = 7202 + discord_id
        service.exchange_resources(
            channel_id,
            1,
            give_to="L",
            give_type="ducats",
            give_value="9",
            receive_type="assassin",
            receive_value="V",
        )
        game = service.get_game(channel_id)
        if discord_id == 1:
            game.players[0].ducats = 0
        else:
            game.players[1].ass_counters = []
        service.repo.save(game)
        before = service.get_game(channel_id)
        before_state = [
            (player.ducats, player.ass_counters[:]) for player in before.players
        ]

        with patch.object(service, "get_game", return_value=before):
            with pytest.raises(TradeRuleException, match=f"^{expected}$"):
                service.exchange_resources(
                    channel_id,
                    2,
                    give_to="N",
                    give_type="assassin",
                    give_value="V",
                    receive_type="ducats",
                    receive_value="9",
                )

        assert before.pending_exchanges == [make_proposal()]
        assert [
            (player.ducats, player.ass_counters) for player in before.players
        ] == before_state
        restored = service.get_game(channel_id)
        assert restored.pending_exchanges == [make_proposal()]
        assert [
            (player.ducats, player.ass_counters) for player in restored.players
        ] == before_state
    finally:
        conn.close()


def test_exchange_save_failure_preserves_reloaded_state() -> None:
    conn, service = make_trade_service(channel_id=7205)
    try:
        service.exchange_resources(
            7205,
            1,
            give_to="L",
            give_type="ducats",
            give_value="9",
            receive_type="assassin",
            receive_value="V",
        )
        game = service.get_game(7205)
        game.players[1].ass_counters = ["V"]
        service.repo.save(game)
        before = service.get_game(7205)
        before_state = [
            (player.ducats, player.ass_counters[:]) for player in before.players
        ]
        conn.execute(
            """
            CREATE TRIGGER fail_exchange_execution
            BEFORE UPDATE ON players
            BEGIN SELECT RAISE(ABORT, 'forced exchange execution failure'); END
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            service.exchange_resources(
                7205,
                2,
                give_to="N",
                give_type="assassin",
                give_value="V",
                receive_type="ducats",
                receive_value="9",
            )

        restored = service.get_game(7205)
        assert restored.pending_exchanges == [make_proposal()]
        assert [
            (player.ducats, player.ass_counters) for player in restored.players
        ] == before_state
    finally:
        conn.close()


def test_exchange_waits_for_a_concurrent_give_to_commit_first() -> None:
    conn, service = make_trade_service(channel_id=7206)
    try:
        service_two = GameService(service.repo)
        first_entered = threading.Event()
        release_first = threading.Event()
        second_loaded = threading.Event()
        original_resolve = service._resolve_trade_parties
        original_get = service_two.get_game
        results: list[str] = []

        def block_first(game: Game, discord_id: int, give_to: str):
            first_entered.set()
            if not release_first.wait(timeout=2):
                raise AssertionError("first give was not released")
            return original_resolve(game, discord_id, give_to)

        def observe_second_load(channel_id: int) -> Game:
            second_loaded.set()
            return original_get(channel_id)

        with (
            patch.object(service, "_resolve_trade_parties", side_effect=block_first),
            patch.object(service_two, "get_game", side_effect=observe_second_load),
        ):
            first = threading.Thread(
                target=lambda: results.append(
                    service.give_resource(
                        7206,
                        1,
                        give_to="L",
                        give_type="ducats",
                        give_value="1",
                    )
                )
            )
            second = threading.Thread(
                target=lambda: results.append(
                    service_two.exchange_resources(
                        7206,
                        1,
                        give_to="L",
                        give_type="ducats",
                        give_value="3",
                        receive_type="ducats",
                        receive_value="4",
                    )
                )
            )
            first.start()
            assert first_entered.wait(timeout=2)
            second.start()
            assert not second_loaded.wait(timeout=0.1)
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

        assert results == [
            "Has dado 1 ducado a Florence.",
            "Intercambio propuesto a Florence: das 3 ducados y pides 4 ducados.",
        ]
        loaded = service.get_game(7206)
        assert (loaded.players[0].ducats, loaded.players[1].ducats) == (19, 1)
        assert loaded.pending_exchanges == [
            ExchangeProposal(
                "N", "L", TradeResource("ducats", 3), TradeResource("ducats", 4)
            )
        ]
    finally:
        release_first.set() if "release_first" in locals() else None
        conn.close()


@pytest.mark.skipif(
    getenv("MACHIAVELLI_REFERENCE_PERF") != "1",
    reason="reference performance gate",
)
def test_trade_operations_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    perf_power_codes = ("M", "V", "L", "N", "P", "F", "T")
    perf_scenario = Scenario(
        name="Performance 7",
        year=1454,
        victory_conditions=VictoryConditions(cities=15, home_countries=2),
        rules=Rules(assassinations_active=True),
        powers={code: Power() for code in perf_power_codes},
    )
    monkeypatch.setattr(
        Scenario,
        "load_scenarios",
        classmethod(lambda cls, json_path=None: {"Perf7": perf_scenario}),
    )

    elapsed: dict[str, float] = {}
    cases = (
        (
            "direct_give",
            False,
            None,
            lambda service: service.give_resource(
                7300,
                9003,
                give_to="L",
                give_type="ducats",
                give_value="1",
            ),
        ),
        (
            "proposal_create",
            True,
            None,
            lambda service: service.exchange_resources(
                7301,
                9003,
                give_to="L",
                give_type="ducats",
                give_value="3",
                receive_type="ducats",
                receive_value="4",
            ),
        ),
        (
            "replacement",
            True,
            ExchangeProposal(
                "L", "N", TradeResource("ducats", 1), TradeResource("ducats", 2)
            ),
            lambda service: service.exchange_resources(
                7302,
                9003,
                give_to="L",
                give_type="ducats",
                give_value="3",
                receive_type="ducats",
                receive_value="4",
            ),
        ),
        (
            "cancellation",
            False,
            None,
            lambda service: service.exchange_resources(
                7303,
                9003,
                give_to="L",
                give_type="ducats",
                give_value="0",
                receive_type="ducats",
                receive_value="1",
            ),
        ),
        (
            "exact_inverse",
            True,
            ExchangeProposal(
                "L", "N", TradeResource("ducats", 9), TradeResource("assassin", "V")
            ),
            lambda service: service.exchange_resources(
                7304,
                9003,
                give_to="L",
                give_type="assassin",
                give_value="V",
                receive_type="ducats",
                receive_value="9",
            ),
        ),
    )

    for index, (operation, skip_measured_pair, special, call) in enumerate(cases):
        channel_id = 7300 + index
        with closing(sqlite3.connect(":memory:")) as conn:
            service = make_service(conn)
            game = service.create_game(operation, channel_id, "Perf7")
            for player_index, code in enumerate(perf_power_codes):
                player = game.add_player(
                    player_id=f"perf-{code}", discord_id=9000 + player_index
                )
                player.power = code
                player.ducats = 10_000
                player.ass_counters = list(perf_power_codes)

            game.pending_exchanges = [
                ExchangeProposal(
                    power_a,
                    power_b,
                    TradeResource("ducats", 1),
                    TradeResource("ducats", 1),
                )
                for power_a, power_b in combinations(perf_power_codes, 2)
                if not (skip_measured_pair and power_a == "L" and power_b == "N")
            ]
            if special is not None:
                game.pending_exchanges.append(special)
            service.repo.save(game)

            started = perf_counter()
            call(service)
            elapsed[operation] = perf_counter() - started

    assert all(duration < 0.5 for duration in elapsed.values())
