"""Tests for the Discord adapter and its application-service boundary."""

from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
import threading
import unittest
from contextlib import contextmanager
from inspect import signature
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from machiavelli import database
from machiavelli.discord import (
    _add_player_record,
    _chunk_lines,
    _create_game_record,
    _exchange_resources_record,
    _execute_game_turn,
    _get_player_commands,
    _get_status_report,
    _get_trade_assassin_targets,
    _get_trade_counterparties,
    _get_trade_resource_types,
    _get_turn_report,
    _give_resource_record,
    _set_scenario_record,
    _submit_command_record,
    _submit_expense_record,
    add_player,
    admin_group,
    cmd,
    exchange,
    expense,
    game_group,
    game_report,
    game_status,
    give,
    run_game,
    trade_exchange_give_value_autocomplete,
    trade_exchange_receive_value_autocomplete,
    trade_give_to_autocomplete,
    trade_give_type_autocomplete,
    trade_give_value_autocomplete,
)
from machiavelli.engine.exceptions import TooManyExpenses
from machiavelli.engine.military import (
    CycleDiagnostic,
    DislodgementResolverRequired,
    InvalidMilitaryState,
    MilitaryResolutionError,
    UnresolvedMilitaryConflict,
)
from machiavelli.events import InvalidTurnEventError
from machiavelli.game import (
    DuplicatePlayerException,
    GameNotFoundException,
    PlayerNotFoundException,
    TradeRuleException,
)
from machiavelli.game.scenario import Scenario
from machiavelli.services import game_service_session


def make_interaction(*, channel_id: int = 321, discord_id: int = 654) -> Mock:
    """Build a network-free interaction mock with all response surfaces."""
    interaction = Mock(name="interaction")
    interaction.channel_id = channel_id
    interaction.user = Mock(id=discord_id)
    interaction.namespace = Mock(power=None)
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.delete_original_response = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


class TestServiceWorkers(unittest.TestCase):
    """Verify that synchronous workers stay behind the service boundary."""

    def test_all_synchronous_database_helpers_use_one_service_session(self) -> None:
        module = ast.parse(Path("machiavelli/discord.py").read_text(encoding="utf-8"))
        helpers = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("_")
            and [*node.args.posonlyargs, *node.args.args]
            and [*node.args.posonlyargs, *node.args.args][0].arg == "db_path"
        ]

        self.assertTrue(helpers)
        for helper in helpers:
            sessions = [
                node
                for node in ast.walk(helper)
                if isinstance(node, ast.With)
                and len(node.items) == 1
                and isinstance(node.items[0].context_expr, ast.Call)
                and isinstance(node.items[0].context_expr.func, ast.Name)
                and node.items[0].context_expr.func.id == "game_service_session"
                and len(node.items[0].context_expr.args) == 1
                and isinstance(node.items[0].context_expr.args[0], ast.Name)
                and node.items[0].context_expr.args[0].id == "db_path"
                and not node.items[0].context_expr.keywords
                and isinstance(node.items[0].optional_vars, ast.Name)
                and node.items[0].optional_vars.id == "service"
            ]
            self.assertEqual(len(sessions), 1, helper.name)

    def test_run_game_worker_has_no_dislodgement_resolver_parameter(self) -> None:
        self.assertNotIn(
            "dislodgement_resolver",
            signature(_execute_game_turn).parameters,
        )

    def test_run_game_worker_delegates_to_service(self) -> None:
        service = Mock(name="service")
        service.run_turn.return_value = ["line one", "line two"]

        @contextmanager
        def fake_session(db_path: str):
            self.assertEqual(db_path, "game.db")
            yield service

        with patch("machiavelli.discord.game_service_session", fake_session):
            report = _execute_game_turn("game.db", 123)

        self.assertEqual(report, ("line one", "line two"))
        service.run_turn.assert_called_once_with(123)

    def test_run_game_worker_propagates_atomic_failure(self) -> None:
        service = Mock(name="service")
        failure = InvalidMilitaryState("duplicate occupation")
        service.run_turn.side_effect = failure

        @contextmanager
        def fake_session(_db_path: str):
            yield service

        with (
            patch("machiavelli.discord.game_service_session", fake_session),
            self.assertRaises(InvalidMilitaryState) as caught,
        ):
            _execute_game_turn("game.db", 123)

        self.assertIs(caught.exception, failure)

    def test_workers_integrate_with_temporary_sqlite(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "discord-phase8.db")
            database.upgrade(db_path)

            game_name, database_id = _create_game_record(db_path, "Adapter", 8080)
            scenario_game_name, scenario_name = _set_scenario_record(
                db_path,
                8080,
                "Be",
            )
            persisted_name, players = _add_player_record(
                db_path,
                8080,
                4242,
                "Florencia",
            )

            with game_service_session(db_path) as service:
                game = service.get_game(8080)
                game.turn_number = 2
                game.players[0].armies = ["milan"]
                service.repo.save(game)

            report = _submit_command_record(
                db_path,
                8080,
                4242,
                "A milan",
                "H",
                None,
            )
            player_id, commands = _get_player_commands(db_path, 8080, 4242)
            status = _get_status_report(db_path, 8080)

            self.assertEqual(game_name, "Adapter")
            self.assertGreater(database_id, 0)
            self.assertEqual(scenario_game_name, "Adapter")
            self.assertIn("balance of power", scenario_name.casefold())
            self.assertEqual(persisted_name, "Adapter")
            self.assertEqual(players, [("Florencia", 4242)])
            self.assertTrue(report[0].startswith("Orden `"))
            self.assertEqual(player_id, "Florencia")
            self.assertEqual(len(commands), 1)
            self.assertIn("Mantener", commands[0])
            self.assertTrue(any("Adapter" in line for line in status))


class TestPlayerCommands(unittest.IsolatedAsyncioTestCase):
    """Exercise player registration and order submission without Discord network I/O."""

    async def test_add_player_uses_service_and_keeps_public_response(self) -> None:
        interaction = make_interaction()
        member = Mock(id=777, mention="<@777>")

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=("Diplomacia", [("Florencia", 777)]),
        ) as mock_to_thread:
            await add_player.callback(interaction, member, "Florencia")

        interaction.response.defer.assert_awaited_once_with(ephemeral=False)
        mock_to_thread.assert_awaited_once_with(
            _add_player_record,
            admin_group.db_path,
            interaction.channel_id,
            member.id,
            "Florencia",
        )
        sent_message = interaction.followup.send.await_args.args[0]
        self.assertIn("Florencia", sent_message)
        self.assertIn("<@777>", sent_message)
        self.assertNotIn("ephemeral", interaction.followup.send.await_args.kwargs)

    async def test_add_player_reports_missing_game(self) -> None:
        interaction = make_interaction()
        member = Mock(id=777, mention="<@777>")

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=GameNotFoundException,
        ):
            await add_player.callback(interaction, member, "Florencia")

        message = interaction.followup.send.await_args.args[0]
        self.assertIn("No hay ninguna partida activa", message)

    async def test_add_player_reports_duplicate_without_leaking_details(self) -> None:
        interaction = make_interaction()
        member = Mock(id=777, mention="<@777>")

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=DuplicatePlayerException("internal duplicate row"),
        ):
            await add_player.callback(interaction, member, "Florencia")

        message = interaction.followup.send.await_args.args[0]
        self.assertIn("ya está inscrito", message)
        self.assertNotIn("internal duplicate row", message)

    async def test_submit_command_is_private_and_uses_service(self) -> None:
        interaction = make_interaction(discord_id=900)
        report = ("Orden enviada.", "**Órdenes recibidas hasta ahora:**")

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=report,
        ) as mock_to_thread:
            await cmd.callback(interaction, "A milan", "H", None)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        mock_to_thread.assert_awaited_once_with(
            _submit_command_record,
            game_group.db_path,
            interaction.channel_id,
            interaction.user.id,
            "A milan",
            "H",
            None,
        )
        interaction.followup.send.assert_awaited_once_with(
            "\n".join(report),
            ephemeral=True,
        )

    async def test_submit_command_reports_unknown_player_privately(self) -> None:
        interaction = make_interaction()

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=PlayerNotFoundException,
        ):
            await cmd.callback(interaction, "A milan", "H", None)

        interaction.followup.send.assert_awaited_once_with(
            "**Error:** No se identificó al jugador.",
            ephemeral=True,
        )

    async def test_excessive_expense_is_private_and_not_reported_as_saved(self) -> None:
        interaction = make_interaction(discord_id=901)

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=TooManyExpenses,
        ) as mock_to_thread:
            await expense.callback(interaction, "E F", "milan", "3")

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        mock_to_thread.assert_awaited_once_with(
            _submit_expense_record,
            game_group.db_path,
            interaction.channel_id,
            interaction.user.id,
            "E F",
            "milan",
            "3",
        )
        message = interaction.followup.send.await_args.args[0]
        self.assertIn("Superado el límite de gastos", message)
        self.assertIn("no se ha guardado", message)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])


class TestReports(unittest.IsolatedAsyncioTestCase):
    """Verify public/private response semantics and safe message partitioning."""

    async def test_game_status_is_public(self) -> None:
        interaction = make_interaction()

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=("status one", "status two"),
        ) as mock_to_thread:
            await game_status.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=False)
        mock_to_thread.assert_awaited_once_with(
            _get_status_report,
            game_group.db_path,
            interaction.channel_id,
        )
        interaction.followup.send.assert_awaited_once_with(
            "status one\nstatus two",
            ephemeral=False,
        )

    async def test_game_report_is_private(self) -> None:
        interaction = make_interaction()

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=("report one", "report two"),
        ) as mock_to_thread:
            await game_report.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        mock_to_thread.assert_awaited_once_with(
            _get_turn_report,
            game_group.db_path,
            interaction.channel_id,
        )
        interaction.followup.send.assert_awaited_once_with(
            "report one\nreport two",
            ephemeral=True,
        )

    async def test_game_report_translates_invalid_history_privately(self) -> None:
        interaction = make_interaction()
        failure = InvalidTurnEventError(row_id=41, event_type="secret_type")

        with (
            patch(
                "machiavelli.discord.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=failure,
            ),
            patch("machiavelli.discord.logger.error") as mock_log,
        ):
            await game_report.callback(interaction)

        mock_log.assert_called_once_with(
            "Historial de turno inválido",
            extra={"row_id": 41, "event_type": "secret_type"},
        )
        interaction.followup.send.assert_awaited_once_with(
            "No se pudo generar el informe porque el historial del turno no es "
            "válido.\nComunícaselo al administrador para que revise los eventos "
            "guardados.",
            ephemeral=True,
        )
        message = interaction.followup.send.await_args.args[0]
        for forbidden in (
            "InvalidTurnEventError",
            "secret_type",
            "41",
            "Traceback",
            "{",
        ):
            self.assertNotIn(forbidden, message)

    async def test_game_report_logs_unexpected_error_without_leaking_details(
        self,
    ) -> None:
        interaction = make_interaction()

        with (
            patch(
                "machiavelli.discord.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=RuntimeError("dato interno"),
            ),
            patch("machiavelli.discord.logger.exception") as mock_log,
        ):
            await game_report.callback(interaction)

        mock_log.assert_called_once()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        message = interaction.followup.send.await_args.args[0]
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        for forbidden in (
            "RuntimeError",
            "dato interno",
            "Traceback",
            "discord.py",
            "test_discord.py",
            "line ",
        ):
            self.assertNotIn(forbidden, message)

    def test_chunk_lines_preserves_order_and_never_exceeds_limit(self) -> None:
        lines = ("a" * 1200, "b" * 1200, "c" * 2100)

        chunks = _chunk_lines(lines, limit=1950)

        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(all(0 < len(chunk) <= 1950 for chunk in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), "".join(lines))


class TestRunGame(unittest.IsolatedAsyncioTestCase):
    """Verify successful publication and safe atomic military failures."""

    async def test_run_game_success_publishes_public_report(self) -> None:
        interaction = make_interaction()

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=("line one", "line two"),
        ) as mock_to_thread:
            await run_game.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        mock_to_thread.assert_awaited_once_with(
            _execute_game_turn,
            admin_group.db_path,
            interaction.channel_id,
        )
        interaction.delete_original_response.assert_awaited_once_with()
        interaction.followup.send.assert_awaited_once_with(
            "line one\nline two",
            ephemeral=False,
        )
        interaction.edit_original_response.assert_not_awaited()

    async def test_run_game_worker_leaves_the_event_loop_available(self) -> None:
        interaction = make_interaction()
        worker_started = threading.Event()
        release_worker = threading.Event()
        witness_completed = asyncio.Event()

        def blocked_worker(_db_path: str, _channel_id: int) -> tuple[str, ...]:
            worker_started.set()
            if not release_worker.wait(timeout=2):
                raise AssertionError("worker was not released")
            return ("done",)

        async def witness() -> None:
            while not worker_started.is_set():
                await asyncio.sleep(0)
            witness_completed.set()

        with patch(
            "machiavelli.discord._execute_game_turn",
            side_effect=blocked_worker,
        ):
            run_task = asyncio.create_task(run_game.callback(interaction))
            try:
                await asyncio.wait_for(witness(), timeout=1)
                self.assertTrue(witness_completed.is_set())
                self.assertFalse(run_task.done())
            finally:
                release_worker.set()
            await asyncio.wait_for(run_task, timeout=1)

        interaction.followup.send.assert_awaited_once_with("done", ephemeral=False)

    async def test_run_game_not_found_edits_deferred_response(self) -> None:
        interaction = make_interaction()

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=GameNotFoundException,
        ):
            await run_game.callback(interaction)

        message = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("No hay ninguna partida activa", message)
        interaction.delete_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_run_game_translates_invalid_history_in_deferred_response(
        self,
    ) -> None:
        interaction = make_interaction()
        failure = InvalidTurnEventError(row_id=73, event_type="hidden_type")

        with (
            patch(
                "machiavelli.discord.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=failure,
            ),
            patch("machiavelli.discord.logger.error") as mock_log,
        ):
            await run_game.callback(interaction)

        mock_log.assert_called_once_with(
            "Historial de turno inválido",
            extra={"row_id": 73, "event_type": "hidden_type"},
        )
        message = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertEqual(
            message,
            "No se pudo generar el informe porque el historial del turno no es "
            "válido.\nComunícaselo al administrador para que revise los eventos "
            "guardados.",
        )
        for forbidden in (
            "InvalidTurnEventError",
            "hidden_type",
            "73",
            "Traceback",
            "{",
        ):
            self.assertNotIn(forbidden, message)
        interaction.delete_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_run_game_logs_unexpected_error_without_leaking_details(
        self,
    ) -> None:
        interaction = make_interaction()

        with (
            patch(
                "machiavelli.discord.asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=RuntimeError("dato interno"),
            ),
            patch("machiavelli.discord.logger.exception") as mock_log,
        ):
            await run_game.callback(interaction)

        mock_log.assert_called_once()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        message = interaction.edit_original_response.await_args.kwargs["content"]
        for forbidden in (
            "RuntimeError",
            "dato interno",
            "Traceback",
            "discord.py",
            "test_discord.py",
            "line ",
        ):
            self.assertNotIn(forbidden, message)
        interaction.delete_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    async def test_military_errors_are_logged_and_translated_atomically(self) -> None:
        diagnostic = CycleDiagnostic(
            stage="all-support-cancellation-exhausted",
            first_seen_iteration=1,
            repeated_iteration=2,
            pending_conflicts=("secret-place",),
            state_signature=(("secret",),),
        )
        cases = (
            (InvalidMilitaryState("duplicate at secret-place"), "ocupaciones"),
            (UnresolvedMilitaryConflict(diagnostic), "Revisa las órdenes"),
            (DislodgementResolverRequired("missing resolver"), "retiradas"),
            (MilitaryResolutionError("discord.py:999"), "Reintenta"),
        )

        for error, guidance in cases:
            with self.subTest(error=type(error).__name__):
                interaction = make_interaction()
                with (
                    patch(
                        "machiavelli.discord.asyncio.to_thread",
                        new_callable=AsyncMock,
                        side_effect=error,
                    ),
                    patch("machiavelli.discord.logger.exception") as mock_log,
                ):
                    await run_game.callback(interaction)

                mock_log.assert_called_once()
                message = interaction.edit_original_response.await_args.kwargs[
                    "content"
                ]
                self.assertTrue(
                    message.startswith(
                        "No se pudo resolver la fase militar; "
                        "no se aplicó ningún cambio."
                    )
                )
                self.assertIn(guidance, message)
                for forbidden in (
                    type(error).__name__,
                    "secret-place",
                    "discord.py",
                    "999",
                    "Traceback",
                ):
                    self.assertNotIn(forbidden, message)
                interaction.delete_original_response.assert_not_awaited()
                interaction.followup.send.assert_not_awaited()


class TestImportSafety(unittest.TestCase):
    """Ensure importing adapters does not require a token or create a database."""

    def test_imports_have_no_database_or_network_side_effects(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "must-not-exist.db"
            env = os.environ.copy()
            env.pop("DISCORD_TOKEN", None)
            env["DATABASE_PATH"] = str(database_path)
            env["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    (str(project_root), env.get("PYTHONPATH", "")),
                )
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import bot; import machiavelli.discord; print('ok')",
                ],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "ok")
            self.assertFalse(database_path.exists())

    def test_public_command_groups_keep_their_names(self) -> None:
        self.assertEqual(game_group.name, "mach")
        self.assertEqual(admin_group.name, "shar")
        self.assertIn("cmd", {command.name for command in game_group.commands})
        self.assertIn("exchange", {command.name for command in game_group.commands})
        self.assertIn("run_game", {command.name for command in admin_group.commands})


class TestGiveCommand(unittest.IsolatedAsyncioTestCase):
    """Verify the private direct-transfer Discord boundary."""

    def test_metadata_and_signature(self) -> None:
        self.assertEqual(
            list(signature(give.callback).parameters),
            ["interaction", "give_to", "give_type", "give_value"],
        )
        self.assertEqual(
            give.description,
            "Da ducados o una ficha de asesinato a otra facción.",
        )
        parameters = {parameter.name: parameter for parameter in give.parameters}
        self.assertEqual(
            parameters["give_to"].description, "Facción que recibirá el recurso"
        )
        self.assertEqual(parameters["give_type"].description, "Recurso que quieres dar")
        self.assertEqual(
            parameters["give_value"].description,
            "Cantidad de ducados o facción objetivo de la ficha",
        )

    async def test_callback_defers_and_uses_one_private_worker(self) -> None:
        interaction = make_interaction()

        async def worker(*_args: object) -> str:
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            return "done"

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(side_effect=worker),
        ) as worker:
            await give.callback(interaction, "L", "ducats", "9")

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        worker.assert_awaited_once_with(
            _give_resource_record,
            game_group.db_path,
            interaction.channel_id,
            interaction.user.id,
            "L",
            "ducats",
            "9",
        )
        interaction.followup.send.assert_awaited_once_with("done", ephemeral=True)

    async def test_callback_maps_expected_errors_to_ephemeral_messages(self) -> None:
        cases = [
            (
                GameNotFoundException(),
                "**Error:** No hay ninguna partida activa en este canal.",
            ),
            (
                PlayerNotFoundException(),
                "**Error:** No se identificó al jugador o no tiene una facción "
                "asignada.",
            ),
            (
                TradeRuleException("recurso inválido"),
                "**Error:** recurso inválido",
            ),
        ]

        for failure, expected in cases:
            interaction = make_interaction()
            with patch(
                "machiavelli.discord.asyncio.to_thread",
                new=AsyncMock(side_effect=failure),
            ):
                await give.callback(interaction, "L", "ducats", "9")

            interaction.followup.send.assert_awaited_once_with(
                expected,
                ephemeral=True,
            )

    async def test_callback_logs_unexpected_error_without_leaking_details(self) -> None:
        interaction = make_interaction()

        with (
            patch(
                "machiavelli.discord.asyncio.to_thread",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("machiavelli.discord.logger.exception") as log,
        ):
            await give.callback(interaction, "L", "ducats", "9")

        log.assert_called_once_with(
            "Fallo inesperado en /mach give",
            extra={"operation": "trade_give", "channel_id": interaction.channel_id},
        )
        interaction.followup.send.assert_awaited_once_with(
            "**Error inesperado:** No se pudo completar la operación. "
            "Inténtalo de nuevo.",
            ephemeral=True,
        )

    async def test_trade_give_autocompletes_filter_and_cap_results(self) -> None:
        interaction = make_interaction()
        counterparties = tuple((f"P{index}", f"Power {index}") for index in range(30))

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(return_value=counterparties),
        ) as worker:
            choices = await trade_give_to_autocomplete(interaction, "power")

        self.assertEqual(len(choices), 25)
        self.assertEqual(choices[0].value, "P0")
        worker.assert_awaited_once_with(
            _get_trade_counterparties,
            game_group.db_path,
            interaction.channel_id,
            interaction.user.id,
        )

    async def test_trade_give_type_autocomplete_uses_one_worker(self) -> None:
        interaction = make_interaction()

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(return_value=(("ducats", "Ducados"),)),
        ) as worker:
            choices = await trade_give_type_autocomplete(interaction, "DUCATS")

        self.assertEqual(
            [(choice.value, choice.name) for choice in choices], [("ducats", "Ducados")]
        )
        worker.assert_awaited_once_with(
            _get_trade_resource_types,
            game_group.db_path,
            interaction.channel_id,
        )

    async def test_trade_give_value_autocomplete_only_loads_assassin_targets(
        self,
    ) -> None:
        interaction = make_interaction()
        interaction.namespace.give_type = "ducats"
        with patch("machiavelli.discord.asyncio.to_thread", new=AsyncMock()) as worker:
            self.assertEqual(await trade_give_value_autocomplete(interaction, ""), [])
        worker.assert_not_awaited()

        interaction.namespace.give_type = "assassin"
        targets = (("M", "Milan"), ("V", "Venice"))
        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(return_value=targets),
        ) as worker:
            choices = await trade_give_value_autocomplete(interaction, "ven")

        self.assertEqual(
            [(choice.value, choice.name) for choice in choices], [("V", "Venice")]
        )
        worker.assert_awaited_once_with(
            _get_trade_assassin_targets,
            game_group.db_path,
            interaction.channel_id,
        )

    async def test_trade_autocompletes_use_assigned_powers_and_scenario_rules(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "trading.db")
            database.upgrade(db_path)
            _create_game_record(db_path, "Trading", 321)
            _set_scenario_record(db_path, 321, "Be")
            _add_player_record(db_path, 321, 654, "P1")
            _add_player_record(db_path, 321, 655, "P2")
            _add_player_record(db_path, 321, 656, "P3")

            with game_service_session(db_path) as service:
                game = service.get_game(321)
                game.players[0].power = "N"
                game.players[1].power = "L"
                service.repo.save(game)
                expected_targets = set(game.require_scenario().powers)

            interaction = make_interaction()
            interaction.namespace.give_type = "assassin"
            with patch.object(game_group, "db_path", db_path):
                counterparties = await trade_give_to_autocomplete(interaction, "")
                resource_types = await trade_give_type_autocomplete(interaction, "")
                targets = await trade_give_value_autocomplete(interaction, "")

            self.assertEqual(
                [(choice.value, choice.name) for choice in counterparties],
                [("L", "Florence")],
            )
            self.assertEqual(
                {choice.value for choice in resource_types}, {"ducats", "assassin"}
            )
            self.assertEqual({choice.value for choice in targets}, expected_targets)
            self.assertNotIn("0", {choice.value for choice in targets})

            disabled_scenario = Scenario.load_scenarios()["Be"]
            disabled_scenario.rules.assassinations_active = False
            with (
                patch(
                    "machiavelli.services.game_service.Scenario.load_scenarios",
                    return_value={"Be": disabled_scenario},
                ),
                patch.object(game_group, "db_path", db_path),
            ):
                disabled_types = await trade_give_type_autocomplete(interaction, "")

            self.assertEqual({choice.value for choice in disabled_types}, {"ducats"})


class TestExchangeCommand(unittest.IsolatedAsyncioTestCase):
    """Verify the complete private exchange Discord boundary."""

    def test_metadata_and_signature(self) -> None:
        self.assertEqual(
            list(signature(exchange.callback).parameters),
            [
                "interaction",
                "give_to",
                "give_type",
                "give_value",
                "receive_type",
                "receive_value",
            ],
        )
        self.assertEqual(
            [
                parameter.annotation
                for parameter in list(signature(exchange.callback).parameters.values())[
                    1:
                ]
            ],
            [str] * 5,
        )
        self.assertEqual(
            exchange.description,
            "Propón, cancela o completa un intercambio con otra facción.",
        )
        parameters = {parameter.name: parameter for parameter in exchange.parameters}
        self.assertEqual(
            parameters["give_to"].description,
            "Facción con la que quieres intercambiar",
        )
        self.assertEqual(parameters["give_type"].description, "Recurso que ofreces")
        self.assertEqual(
            parameters["give_value"].description,
            "Cantidad u objetivo que ofreces; 0 cancela",
        )
        self.assertEqual(
            parameters["receive_type"].description, "Recurso que solicitas"
        )
        self.assertEqual(
            parameters["receive_value"].description,
            "Cantidad u objetivo que solicitas; 0 cancela",
        )
        autocomplete = {
            name: parameter.autocomplete for name, parameter in exchange._params.items()
        }
        self.assertIs(autocomplete["give_to"], trade_give_to_autocomplete)
        self.assertIs(autocomplete["give_type"], trade_give_type_autocomplete)
        self.assertIs(
            autocomplete["give_value"], trade_exchange_give_value_autocomplete
        )
        self.assertIs(autocomplete["receive_type"], trade_give_type_autocomplete)
        self.assertIs(
            autocomplete["receive_value"], trade_exchange_receive_value_autocomplete
        )

    async def test_callback_defers_and_uses_one_private_worker(self) -> None:
        interaction = make_interaction()

        async def worker(*_args: object) -> str:
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            return "done"

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(side_effect=worker),
        ) as worker:
            await exchange.callback(
                interaction,
                "L",
                "ducats",
                "9",
                "assassin",
                "V",
            )

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        worker.assert_awaited_once_with(
            _exchange_resources_record,
            game_group.db_path,
            interaction.channel_id,
            interaction.user.id,
            "L",
            "ducats",
            "9",
            "assassin",
            "V",
        )
        interaction.followup.send.assert_awaited_once_with("done", ephemeral=True)

    async def test_callback_maps_expected_and_unexpected_errors(self) -> None:
        cases = [
            (
                GameNotFoundException(),
                "**Error:** No hay ninguna partida activa en este canal.",
            ),
            (
                PlayerNotFoundException(),
                "**Error:** No se identificó al jugador o no tiene una facción "
                "asignada.",
            ),
            (TradeRuleException("recurso inválido"), "**Error:** recurso inválido"),
        ]

        for failure, expected in cases:
            interaction = make_interaction()
            with patch(
                "machiavelli.discord.asyncio.to_thread",
                new=AsyncMock(side_effect=failure),
            ):
                await exchange.callback(
                    interaction,
                    "L",
                    "ducats",
                    "9",
                    "assassin",
                    "V",
                )
            interaction.followup.send.assert_awaited_once_with(expected, ephemeral=True)

        interaction = make_interaction()
        with (
            patch(
                "machiavelli.discord.asyncio.to_thread",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("machiavelli.discord.logger.exception") as log,
        ):
            await exchange.callback(
                interaction,
                "L",
                "ducats",
                "9",
                "assassin",
                "V",
            )

        log.assert_called_once_with(
            "Fallo inesperado en /mach exchange",
            extra={"operation": "exchange", "channel_id": interaction.channel_id},
        )
        interaction.followup.send.assert_awaited_once_with(
            "**Error inesperado:** No se pudo completar la operación. "
            "Inténtalo de nuevo.",
            ephemeral=True,
        )
        message = interaction.followup.send.await_args.args[0]
        for forbidden in (
            "RuntimeError",
            "boom",
            "Traceback",
            "SQL",
            "discord.py",
            "test_discord.py",
            "line ",
            "9",
            "V",
            str(interaction.user.id),
        ):
            self.assertNotIn(forbidden, message)

    async def test_exchange_value_autocompletes_cancel_without_ducat_io(self) -> None:
        interaction = make_interaction()
        interaction.namespace.give_type = "ducats"

        with patch("machiavelli.discord.asyncio.to_thread", new=AsyncMock()) as worker:
            choices = await trade_exchange_give_value_autocomplete(
                interaction, "cancel"
            )

        self.assertEqual(
            [(choice.name, choice.value) for choice in choices],
            [("0 — Cancelar intercambio", "0")],
        )
        worker.assert_not_awaited()

        interaction.namespace.receive_type = "unknown"
        with patch("machiavelli.discord.asyncio.to_thread", new=AsyncMock()) as worker:
            choices = await trade_exchange_receive_value_autocomplete(interaction, "0")

        self.assertEqual([choice.value for choice in choices], ["0"])
        worker.assert_not_awaited()

        interaction.namespace.give_type = "ASSASSIN"
        with patch("machiavelli.discord.asyncio.to_thread", new=AsyncMock()) as worker:
            choices = await trade_exchange_give_value_autocomplete(interaction, "0")

        self.assertEqual([choice.value for choice in choices], ["0"])
        worker.assert_not_awaited()

    async def test_exchange_value_autocomplete_loads_assassin_targets_once(
        self,
    ) -> None:
        interaction = make_interaction()
        interaction.namespace.give_type = "assassin"
        targets = (("M", "Milan"), ("V", "Venice"))

        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(return_value=targets),
        ) as worker:
            choices = await trade_exchange_give_value_autocomplete(interaction, "")

        self.assertEqual(
            [(choice.name, choice.value) for choice in choices],
            [
                ("0 — Cancelar intercambio", "0"),
                ("Milan", "M"),
                ("Venice", "V"),
            ],
        )
        worker.assert_awaited_once_with(
            _get_trade_assassin_targets,
            game_group.db_path,
            interaction.channel_id,
        )

        many_targets = tuple((f"P{index}", f"Power {index}") for index in range(30))
        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(return_value=many_targets),
        ) as worker:
            choices = await trade_exchange_give_value_autocomplete(interaction, "")

        self.assertEqual(len(choices), 25)
        self.assertEqual(
            (choices[0].name, choices[0].value),
            ("0 — Cancelar intercambio", "0"),
        )
        worker.assert_awaited_once_with(
            _get_trade_assassin_targets,
            game_group.db_path,
            interaction.channel_id,
        )

        interaction.namespace.receive_type = "assassin"
        with patch(
            "machiavelli.discord.asyncio.to_thread",
            new=AsyncMock(return_value=targets),
        ) as worker:
            choices = await trade_exchange_receive_value_autocomplete(
                interaction, "VEN"
            )

        self.assertEqual([choice.value for choice in choices], ["V"])
        worker.assert_awaited_once_with(
            _get_trade_assassin_targets,
            game_group.db_path,
            interaction.channel_id,
        )

    async def test_exchange_value_autocomplete_hides_unexpected_errors(self) -> None:
        for autocomplete, resource_attribute in (
            (trade_exchange_give_value_autocomplete, "give_type"),
            (trade_exchange_receive_value_autocomplete, "receive_type"),
        ):
            with self.subTest(autocomplete=autocomplete.__name__):
                interaction = make_interaction()
                setattr(interaction.namespace, resource_attribute, "assassin")
                with patch(
                    "machiavelli.discord.asyncio.to_thread",
                    new=AsyncMock(side_effect=RuntimeError("boom")),
                ):
                    choices = await autocomplete(interaction, "")

                self.assertEqual(choices, [])
                interaction.response.send_message.assert_not_awaited()
                interaction.followup.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
