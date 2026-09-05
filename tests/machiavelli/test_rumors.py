"""Rumores: SQLite real y Discord simulado, sin credenciales ni acceso a red."""

import asyncio
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from machiavelli import discord as adapter
from machiavelli.db import database as database_module
from machiavelli.db.database import _UPGRADES, DatabaseManager, upgrade_connection
from machiavelli.engine import GameEngine
from machiavelli.game import Command
from machiavelli.services import game_service_session


@pytest.fixture(autouse=True)
def no_discord_network(monkeypatch):
    attempted = []

    async def blocked(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Las pruebas no pueden acceder a Discord")

    monkeypatch.setattr(discord.http.HTTPClient, "request", blocked)
    monkeypatch.setattr(discord.http.HTTPClient, "ws_connect", blocked)
    yield
    assert not attempted, "Se intentó acceder a Discord, incluso si se capturó el error"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "rumors.db")
    DatabaseManager(path).init_db()
    with game_service_session(path) as service:
        service.create_game("Partida", 100)
        service.add_player(100, 11, "Emisor")
        service.add_player(100, 22, "Receptor")
        service.set_rumor_channel(100, 200)
    monkeypatch.setattr(adapter.game_group, "db_path", path)
    monkeypatch.setattr(adapter.admin_group, "db_path", path)
    return path


def quota(path, discord_id=11, channel_id=100):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            "SELECT rumors_sent FROM players JOIN games ON games.id = players.game_id "
            "WHERE channel_id = ? AND discord_id = ?",
            (channel_id, discord_id),
        ).fetchone()[0]


def reserve(path, discord_id=11, recipient_id=22, channel_id=100):
    with game_service_session(path) as service:
        game_id, turn, _name, board = service.prepare_rumor(
            channel_id, discord_id, recipient_id
        )
        return service.reserve_rumor(game_id, turn, discord_id, recipient_id, board)


def make_interaction():
    interaction = Mock()
    interaction.channel_id = 100
    interaction.user.id = 11
    interaction.permissions = discord.Permissions(administrator=True)
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = True
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    guild = interaction.guild
    guild.id = 10
    recipient = Mock(spec=discord.Member)
    recipient.id = 22
    recipient.guild = guild
    recipient.send = AsyncMock()
    guild.fetch_member = AsyncMock(return_value=recipient)
    board = Mock(spec=discord.TextChannel)
    board.id = 200
    board.guild = guild
    board.permissions_for.return_value = discord.Permissions(
        view_channel=True, send_messages=True
    )
    board.send = AsyncMock()
    guild.fetch_channel = AsyncMock(return_value=board)
    return interaction, recipient, board


def test_migration_v5_preserves_all_rows_and_only_adds_minimum_columns(tmp_path):
    with closing(sqlite3.connect(tmp_path / "v5.db")) as conn:
        for script in _UPGRADES[:5]:
            conn.executescript(script)
        conn.execute("PRAGMA user_version = 5")
        conn.execute("INSERT INTO games (name, channel_id) VALUES ('old', 100)")
        conn.execute(
            "INSERT INTO players (game_id, player_id, discord_id) VALUES (1, 'P1', 11)"
        )
        conn.commit()
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
        before = {
            table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables
        }
        upgrade_connection(conn)
        upgrade_connection(conn)
        assert conn.execute("PRAGMA user_version").fetchone() == (6,)
        for table in tables:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if table in ("games", "players"):
                assert [row[:-1] for row in rows] == before[table]
            else:
                assert rows == before[table]
        assert conn.execute("SELECT rumor_channel_id FROM games").fetchone() == (None,)
        assert conn.execute("SELECT rumors_sent FROM players").fetchone() == (0,)
        for value in (-1, 4):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE players SET rumors_sent = ?", (value,))


def test_quota_persists_is_shared_and_isolated(db_path):
    assert reserve(db_path) == 2
    assert reserve(db_path, recipient_id=None) == 1
    assert reserve(db_path) == 0
    with pytest.raises(ValueError, match="límite"):
        reserve(db_path, recipient_id=None)
    assert quota(db_path) == 3
    assert reserve(db_path, discord_id=22, recipient_id=11) == 2
    with game_service_session(db_path) as service:
        service.create_game("Otra", 300)
        service.add_player(300, 11, "P1")
        service.add_player(300, 22, "P2")
    assert reserve(db_path, channel_id=300) == 2
    assert quota(db_path) == 3


def test_failed_migration_six_rolls_back_added_columns(tmp_path, monkeypatch):
    with closing(sqlite3.connect(tmp_path / "failed-migration.db")) as conn:
        for script in _UPGRADES[:5]:
            conn.executescript(script)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        monkeypatch.setattr(
            database_module,
            "_UPGRADES",
            _UPGRADES[:5] + (_UPGRADES[5] + "INVALID SQL;",),
        )
        with pytest.raises(sqlite3.OperationalError):
            upgrade_connection(conn)
        assert conn.execute("PRAGMA user_version").fetchone() == (5,)
        for table, column in (
            ("games", "rumor_channel_id"),
            ("players", "rumors_sent"),
        ):
            assert column not in {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }


def test_concurrent_reservations_never_exceed_three(db_path):
    def attempt(index):
        try:
            return reserve(db_path, recipient_id=22 if index % 2 else None)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(12)))
    assert sorted(value for value in results if value is not None) == [0, 1, 2]
    assert quota(db_path) == 3


def test_old_aggregate_and_command_saves_preserve_counter(db_path):
    with game_service_session(db_path) as service:
        game = service.get_game(100)
        assert reserve(db_path) == 2
        player = game.players[0]
        player.commands.append(Command(game, player, "A rome", "H", None))
        service.repo.save(game)
        player.save_commands(service.repo.conn)
        player.save(service.repo.conn)
    assert quota(db_path) == 1


@pytest.mark.parametrize("turn", [0, 1, 2, 3, 4, 5])
def test_each_successful_turn_renews_quota_and_late_refund_is_ignored(db_path, turn):
    with game_service_session(db_path) as service:
        service.set_scenario(100, "Be")
        game = service.get_game(100)
        game.turn_number = turn
        service.repo.save(game)
        reserve(db_path)
        # Lifecycle real; solo sustituimos las reglas de cada fase.
        with (
            patch.object(GameEngine, "run_startup"),
            patch.object(GameEngine, "run_maintenance"),
            patch.object(GameEngine, "run_campaign"),
        ):
            service.run_turn(100)
        assert quota(db_path) == 0
        assert reserve(db_path) == 2
        service.refund_rumor(game.database_id, turn, 11)
        assert quota(db_path) == 1
        with pytest.raises(ValueError, match="cambiado"):
            service.repo.save(game)
        assert service.get_game(100).turn_number == turn + 1
        assert quota(db_path) == 1


def test_failed_engine_and_failed_save_preserve_quota(db_path):
    reserve(db_path)
    with game_service_session(db_path) as service:
        with patch.object(GameEngine, "run_startup", side_effect=RuntimeError):
            with pytest.raises(RuntimeError):
                service.run_turn(100)
        game = service.get_game(100)
        game.advance_turn()
        with patch(
            "machiavelli.repositories.game_repository.PlayerRepository.replace_for_game",
            side_effect=RuntimeError,
        ):
            with pytest.raises(RuntimeError):
                service.repo.save(game)
        assert service.get_game(100).turn_number == 0
    assert quota(db_path) == 1


@pytest.mark.parametrize(
    "change", ["turn", "remove_sender", "remove_recipient", "board"]
)
def test_reservation_revalidates_after_preparation(db_path, change):
    with game_service_session(db_path) as service:
        game_id, turn, _name, board = service.prepare_rumor(100, 11, 22)
        if change == "turn":
            game = service.get_game(100)
            game.advance_turn()
            service.repo.save(game)
        elif change.startswith("remove"):
            service.remove_player(100, 11 if change == "remove_sender" else 22)
        else:
            service.set_rumor_channel(100, 201)
        with pytest.raises(ValueError):
            service.reserve_rumor(
                game_id, turn, 11, None if change == "board" else 22, board
            )


def test_adapter_private_and_board_share_quota_without_exposing_author(db_path, caplog):
    interaction, recipient, board = make_interaction()
    text = "un rumor secreto @everyone"
    with closing(sqlite3.connect(db_path)) as conn:
        before = list(conn.iterdump())
    for target in (recipient, None, recipient, None):
        asyncio.run(adapter.rumor.callback(interaction, text, target))
    assert recipient.send.await_count == 2
    board.send.assert_awaited_once_with(f"Rumor anónimo · Partida\n\n{text}")
    for call in recipient.send.await_args_list:
        assert call.args == (f"Rumor anónimo · Partida\n\n{text}",)
        assert call.kwargs == {}
    assert quota(db_path) == 3
    assert "límite" in interaction.followup.send.call_args.args[0]
    for call in interaction.followup.send.call_args_list:
        assert call.kwargs == {"ephemeral": True}
        assert text not in call.args[0]
    interaction.response.defer.assert_called_with(ephemeral=True)
    with closing(sqlite3.connect(db_path)) as conn:
        after = list(conn.iterdump())
        assert conn.execute("SELECT * FROM game_events").fetchall() == []
    changed = [line for line in after if line not in before]
    assert len(changed) == 1
    assert changed[0].startswith('INSERT INTO "players"')
    assert text not in "\n".join(after)
    assert text not in caplog.text


@pytest.mark.parametrize(
    "problem", ["empty", "long", "self", "sender", "recipient", "guild", "channel"]
)
def test_invalid_input_does_not_consume_quota(db_path, problem):
    interaction, recipient, board = make_interaction()
    text = "Rumor"
    if problem == "empty":
        text = " \n "
    elif problem == "long":
        text = "x" * 1901
    elif problem == "self":
        recipient.id = 11
    elif problem == "sender":
        interaction.user.id = 99
    elif problem == "recipient":
        with game_service_session(db_path) as service:
            service.create_game("Otra", 300)
            service.add_player(300, 99, "Solo en otra partida")
        recipient.id = 99
    elif problem == "guild":
        interaction.guild = None
    else:
        interaction.channel_id = 999
    asyncio.run(adapter.rumor.callback(interaction, text, recipient))
    recipient.send.assert_not_awaited()
    board.send.assert_not_awaited()
    assert quota(db_path) == 0


def test_eliminated_players_and_maximum_text_without_board(db_path):
    with game_service_session(db_path) as service:
        game = service.get_game(100)
        for player in game.players:
            player.eliminate()
        game.name = "a" * 200
        game.rumor_channel_id = None
        service.repo.save(game)
    interaction, recipient, board = make_interaction()
    asyncio.run(adapter.rumor.callback(interaction, "x" * 1900, recipient))
    assert len(recipient.send.call_args.args[0]) <= 2000
    assert recipient.send.call_args.args[0].endswith("x" * 1900)
    assert quota(db_path) == 1
    asyncio.run(adapter.rumor.callback(interaction, "Rumor"))
    board.send.assert_not_awaited()
    assert quota(db_path) == 1


def http_error(status):
    return discord.HTTPException(Mock(status=status, reason="failure"), "secret")


@pytest.mark.parametrize(
    "problem", ["deleted", "permissions", "other_guild", "thread", "member"]
)
def test_unavailable_destination_does_not_consume_quota(db_path, problem):
    interaction, _recipient, board = make_interaction()
    if problem == "deleted":
        interaction.guild.fetch_channel.side_effect = http_error(404)
    elif problem == "permissions":
        board.permissions_for.return_value.send_messages = False
    elif problem == "other_guild":
        board.guild = Mock(id=999)
    elif problem == "thread":
        interaction.guild.fetch_channel.return_value = Mock(spec=discord.Thread)
    else:
        interaction.guild.fetch_member.side_effect = http_error(404)
    asyncio.run(adapter.rumor.callback(interaction, "Rumor"))
    assert quota(db_path) == 0
    board.send.assert_not_awaited()


@pytest.mark.parametrize(
    "status,expected", [(400, 0), (403, 0), (404, 0), (429, 1), (500, 1)]
)
@pytest.mark.parametrize("private", [True, False])
def test_delivery_failures_refund_only_clear_rejections(
    db_path, status, expected, private, caplog
):
    interaction, recipient, board = make_interaction()
    destination = recipient if private else board
    destination.send.side_effect = http_error(status)
    asyncio.run(
        adapter.rumor.callback(interaction, "secret", recipient if private else None)
    )
    assert quota(db_path) == expected
    destination.send.assert_awaited_once()
    assert "secret" not in caplog.text
    assert "secret" not in interaction.followup.send.call_args.args[0]


def test_timeout_and_failed_confirmation_do_not_retry_or_refund(db_path):
    interaction, recipient, _board = make_interaction()
    recipient.send.side_effect = TimeoutError("secret")
    asyncio.run(adapter.rumor.callback(interaction, "secret", recipient))
    assert quota(db_path) == 1
    recipient.send.assert_awaited_once()
    recipient.send.reset_mock(side_effect=True)
    interaction.followup.send.side_effect = RuntimeError("secret")
    asyncio.run(adapter.rumor.callback(interaction, "secret", recipient))
    assert quota(db_path) == 2
    recipient.send.assert_awaited_once()


@pytest.mark.parametrize("admin", [True, False])
def test_configure_board_checks_actual_admin_permission(db_path, admin):
    interaction, _recipient, board = make_interaction()
    interaction.permissions.administrator = admin
    board.id = 201
    asyncio.run(adapter.set_rumor_channel.callback(interaction, board))
    with game_service_session(db_path) as service:
        assert service.get_game(100).rumor_channel_id == (201 if admin else 200)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs == {"ephemeral": True}


def test_transport_logs_and_command_errors_do_not_expose_payload(caplog):
    with caplog.at_level(logging.DEBUG):
        for name in ("discord.http", "discord.gateway", "discord.webhook.async_"):
            logging.getLogger(name).debug("secret payload")
            logging.getLogger(name).warning("secret destination")
        interaction, _recipient, _board = make_interaction()
        interaction.response.is_done.return_value = False
        asyncio.run(
            adapter.rumor_command_error(
                interaction, discord.app_commands.AppCommandError("secret author")
            )
        )
    assert "secret" not in caplog.text
    interaction.response.send_message.assert_awaited_once()
    assert "secret" not in interaction.response.send_message.call_args.args[0]
    assert interaction.response.send_message.call_args.kwargs == {"ephemeral": True}
