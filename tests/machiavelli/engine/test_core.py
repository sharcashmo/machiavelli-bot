"""Pruebas de coordinación y barreras de error del motor de turnos."""

import logging
import unittest
from random import Random
from unittest.mock import Mock, call, patch

import pytest

from machiavelli.engine.core import GameEngine
from machiavelli.game.command import Command
from machiavelli.game.events import EventType, TurnEvent
from machiavelli.game.game import Game
from machiavelli.game.map import Map, Province
from machiavelli.game.scenario import (
    HomeCountry,
    Power,
    Rules,
    Scenario,
    VictoryConditions,
)
from machiavelli.game.tables import GameTables
from tests.machiavelli.engine.helpers import create_military_game, military_snapshot

logger = logging.getLogger(__name__)


class TrackingGame:
    """Doble mínimo de partida que cuenta las sustituciones de la lista de historial."""

    def __init__(self, turn_number: int, events: list[TurnEvent]):
        self.turn_number = turn_number
        self._turn_events = events
        self.history_replacements = 0
        self.advance_calls = 0

    @property
    def turn_events(self) -> list[TurnEvent]:
        return self._turn_events

    @turn_events.setter
    def turn_events(self, events: list[TurnEvent]) -> None:
        self.history_replacements += 1
        self._turn_events = events

    def advance_turn(self) -> None:
        self.advance_calls += 1


class TestGameEngineRunStartup(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.engine = GameEngine(game=self.mock_game)

    @patch("machiavelli.engine.core.SetupManager")
    @patch("machiavelli.engine.core.DisastersManager")
    @patch("machiavelli.engine.core.IncomeManager")
    def test_run_startup(
        self, mock_income_manager_cls, mock_disaster_manager_cls, mock_setup_manager_cls
    ):
        """Ejecuta correctamente el setup cuando estamos en el turno 0."""
        self.mock_game.turn_number = 0

        self.engine.run_startup()

        # Verifica que se instancia el SetupManager pasándole el game y el rng del motor
        mock_setup_manager_cls.assert_called_once_with(self.mock_game, self.engine.rng)

        # Instanciación correcta con game
        mock_disaster_manager_cls.assert_called_once_with(self.mock_game)
        mock_income_manager_cls.assert_called_once_with(self.mock_game)

        # Ejecución de los métodos correctos según core.py
        mock_disaster_manager_cls.return_value.spawn_famine.assert_called_once()
        mock_income_manager_cls.return_value.run.assert_called_once()

    @patch("machiavelli.engine.core.SetupManager")
    @patch("machiavelli.engine.core.DisastersManager")
    @patch("machiavelli.engine.core.IncomeManager")
    def test_run_startup_exception(
        self, mock_income_manager_cls, mock_disaster_manager_cls, mock_setup_manager_cls
    ):
        """Propaga el error específico y detiene las fases posteriores."""
        from machiavelli.engine.exceptions import DuplicatePlayerError

        self.mock_game.turn_number = 0
        error_raised = DuplicatePlayerError(player_id="p1", discord_id=None)
        mock_setup_manager_cls.return_value.run.side_effect = error_raised

        with self.assertRaises(DuplicatePlayerError) as caught:
            self.engine.run()

        self.assertIs(caught.exception, error_raised)
        mock_setup_manager_cls.return_value.run.assert_called_once()
        mock_disaster_manager_cls.return_value.spawn_famine.assert_not_called()
        mock_income_manager_cls.return_value.run.assert_not_called()
        self.mock_game.advance_turn.assert_not_called()

    def test_startup_famine_requires_both_rules(self):
        cases = (
            (False, False, False),
            (False, True, False),
            (True, False, False),
            (True, True, True),
        )
        for famine_active, first_turn_famine, expected in cases:
            with self.subTest(
                famine_active=famine_active,
                first_turn_famine=first_turn_famine,
            ):
                game = Mock(turn_number=0)
                game.scenario.rules = Rules(
                    famine_active=famine_active,
                    first_turn_famine=first_turn_famine,
                )
                engine = GameEngine(game)
                with (
                    patch("machiavelli.engine.core.SetupManager"),
                    patch("machiavelli.engine.core.DisastersManager") as disasters,
                    patch("machiavelli.engine.core.IncomeManager"),
                ):
                    engine.run_startup()

                if expected:
                    disasters.assert_called_once_with(game)
                    disasters.return_value.spawn_famine.assert_called_once_with()
                else:
                    disasters.assert_not_called()


class TestGameEngineRunCampaign(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.mock_game.players = []
        self.engine = GameEngine(game=self.mock_game)

    @patch("machiavelli.engine.core.ControlManager")
    @patch("machiavelli.engine.core.MilitaryResolver")
    @patch("machiavelli.engine.core.AssassinationResolver")
    @patch("machiavelli.engine.core.BribeResolver")
    @patch("machiavelli.engine.core.RebellionManager")
    @patch("machiavelli.engine.core.DisastersManager")
    @patch("machiavelli.engine.core.ExpenditureProcessor")
    def test_run_campaign_standard_season(
        self,
        mock_expenditure_cls,
        mock_disasters_cls,
        mock_rebellion_cls,
        mock_bribe_cls,
        mock_assassination_cls,
        mock_military_cls,
        mock_control_cls,
    ):
        """Para season != 2 (ej. season 1), omite los eventos de verano."""
        self.mock_game.turn_number = 1  # 1 % 4 = 1 (season 1)

        mock_disasters_inst = mock_disasters_cls.return_value

        self.engine.run_campaign()

        # Instanciación correcta con game
        mock_expenditure_cls.assert_called_once_with(self.mock_game)
        mock_disasters_cls.assert_called_once_with(self.mock_game)
        mock_rebellion_cls.assert_called_once_with(self.mock_game)
        mock_bribe_cls.assert_called_once_with(self.mock_game)
        mock_assassination_cls.assert_called_once_with(self.mock_game)
        mock_military_cls.assert_called_once_with(self.mock_game)
        mock_control_cls.assert_called_once_with(self.mock_game)

        # Ejecución de procesadores principales
        mock_expenditure_cls.return_value.run.assert_called_once()
        mock_disasters_inst.process_famine_relief_expenses.assert_called_once()
        mock_rebellion_cls.return_value.rebellion_expenses.assert_called_once()
        mock_bribe_cls.return_value.run.assert_called_once()
        mock_assassination_cls.return_value.run.assert_called_once()
        mock_military_cls.return_value.run.assert_called_once()
        mock_control_cls.return_value.run.assert_called_once()

        # Métodos exclusivos de season == 2 NO deben llamarse
        mock_disasters_inst.resolve_famine_attrition.assert_not_called()
        mock_disasters_inst.clear_famine.assert_not_called()
        mock_disasters_inst.spawn_plague.assert_not_called()

    @patch("machiavelli.engine.core.ControlManager")
    @patch("machiavelli.engine.core.MilitaryResolver")
    @patch("machiavelli.engine.core.AssassinationResolver")
    @patch("machiavelli.engine.core.BribeResolver")
    @patch("machiavelli.engine.core.RebellionManager")
    @patch("machiavelli.engine.core.DisastersManager")
    @patch("machiavelli.engine.core.ExpenditureProcessor")
    def test_run_campaign_season_2(
        self,
        mock_expenditure_cls,
        mock_disasters_cls,
        mock_rebellion_cls,
        mock_bribe_cls,
        mock_assassination_cls,
        mock_military_cls,
        mock_control_cls,
    ):
        """Para season == 2 ejecuta attrition, limpia hambre y genera de plaga."""
        self.mock_game.turn_number = 2  # 2 % 4 = 2 (season 2)

        mock_disasters_inst = mock_disasters_cls.return_value

        self.engine.run_campaign()

        # Métodos de season == 2 SÍ deben ejecutarse
        mock_disasters_inst.resolve_famine_attrition.assert_called_once()
        mock_disasters_inst.clear_famine.assert_called_once()
        mock_disasters_inst.spawn_plague.assert_called_once()

    @patch("machiavelli.engine.core.ControlManager")
    @patch("machiavelli.engine.core.MilitaryResolver")
    @patch("machiavelli.engine.core.AssassinationResolver")
    @patch("machiavelli.engine.core.BribeResolver")
    @patch("machiavelli.engine.core.RebellionManager")
    @patch("machiavelli.engine.core.DisastersManager")
    @patch("machiavelli.engine.core.ExpenditureProcessor")
    @patch("machiavelli.engine.core.RetreatHandler")
    def test_run_campaign_season_2_execution_order(
        self,
        mock_retreat_handler_cls,
        mock_expenditure_cls,
        mock_disasters_cls,
        mock_rebellion_cls,
        mock_bribe_cls,
        mock_assassination_cls,
        mock_military_cls,
        mock_control_cls,
    ):
        """Verifica el orden secuencial exacto de ejecución durante en season == 2."""
        self.mock_game.turn_number = 2

        # Rastreador centralizado de orden de llamadas
        manager = Mock()
        manager.attach_mock(mock_expenditure_cls.return_value.run, "expenditure_run")
        manager.attach_mock(
            mock_disasters_cls.return_value.process_famine_relief_expenses,
            "famine_relief",
        )
        manager.attach_mock(
            mock_rebellion_cls.return_value.rebellion_expenses, "rebellion_expenses"
        )
        manager.attach_mock(mock_bribe_cls.return_value.run, "bribe_run")
        manager.attach_mock(
            mock_assassination_cls.return_value.run, "assassination_run"
        )
        manager.attach_mock(mock_military_cls.return_value.run, "military_run")
        manager.attach_mock(
            mock_disasters_cls.return_value.resolve_famine_attrition, "attrition"
        )
        manager.attach_mock(mock_control_cls.return_value.run, "control_run")
        manager.attach_mock(
            mock_disasters_cls.return_value.clear_famine, "clear_famine"
        )
        manager.attach_mock(
            mock_disasters_cls.return_value.spawn_plague, "spawn_plague"
        )

        self.engine.run_campaign()

        expected_calls = [
            call.expenditure_run(),
            call.famine_relief(),
            call.rebellion_expenses(),
            call.bribe_run(),
            call.assassination_run(),
            call.military_run(
                dislodgement_resolver=mock_retreat_handler_cls.return_value
            ),
            call.attrition(),
            call.control_run(),
            call.clear_famine(),
            call.spawn_plague(),
        ]

        self.assertEqual(manager.mock_calls, expected_calls)

    @patch("machiavelli.engine.core.ControlManager")
    @patch("machiavelli.engine.core.MilitaryResolver")
    @patch("machiavelli.engine.core.AssassinationResolver")
    @patch("machiavelli.engine.core.BribeResolver")
    @patch("machiavelli.engine.core.RebellionManager")
    @patch("machiavelli.engine.core.DisastersManager")
    @patch("machiavelli.engine.core.ExpenditureProcessor")
    @patch("machiavelli.engine.core.IncomeManager")
    @patch("machiavelli.engine.core.RetreatHandler")
    def test_run_campaign_season_0_execution_order(
        self,
        mock_retreat_handler_cls,
        mock_income_cls,
        mock_expenditure_cls,
        mock_disasters_cls,
        mock_rebellion_cls,
        mock_bribe_cls,
        mock_assassination_cls,
        mock_military_cls,
        mock_control_cls,
    ):
        """Verifica el orden secuencial exacto de ejecución durante en season == 0."""
        self.mock_game.turn_number = 4

        # Rastreador centralizado de orden de llamadas
        manager = Mock()
        manager.attach_mock(mock_expenditure_cls.return_value.run, "expenditure_run")
        manager.attach_mock(
            mock_disasters_cls.return_value.process_famine_relief_expenses,
            "famine_relief",
        )
        manager.attach_mock(
            mock_rebellion_cls.return_value.rebellion_expenses, "rebellion_expenses"
        )
        manager.attach_mock(mock_bribe_cls.return_value.run, "bribe_run")
        manager.attach_mock(
            mock_assassination_cls.return_value.run, "assassination_run"
        )
        manager.attach_mock(mock_military_cls.return_value.run, "military_run")
        manager.attach_mock(mock_control_cls.return_value.run, "control_run")
        manager.attach_mock(
            mock_disasters_cls.return_value.spawn_famine, "spawn_famine"
        )
        manager.attach_mock(mock_income_cls.return_value.run, "income_run")

        self.engine.run_campaign()

        expected_calls = [
            call.expenditure_run(),
            call.famine_relief(),
            call.rebellion_expenses(),
            call.bribe_run(),
            call.assassination_run(),
            call.military_run(
                dislodgement_resolver=mock_retreat_handler_cls.return_value
            ),
            call.control_run(),
            call.spawn_famine(),
            call.income_run(),
        ]

        self.assertEqual(manager.mock_calls, expected_calls)

    def test_campaign_gates_optional_rules_before_resolvers(self):
        self.mock_game.turn_number = 2
        self.mock_game.scenario.rules = Rules(
            assassinations_active=False,
            famine_active=False,
            first_turn_famine=True,
            plague_active=False,
        )

        with (
            patch("machiavelli.engine.core.ExpenditureProcessor"),
            patch("machiavelli.engine.core.DisastersManager") as disasters,
            patch("machiavelli.engine.core.RebellionManager"),
            patch("machiavelli.engine.core.BribeResolver"),
            patch("machiavelli.engine.core.AssassinationResolver") as assassinations,
            patch("machiavelli.engine.core.MilitaryResolver"),
            patch("machiavelli.engine.core.RetreatHandler"),
            patch("machiavelli.engine.core.ControlManager"),
        ):
            self.engine.run_campaign()

        disasters.assert_not_called()
        assassinations.assert_not_called()

    def test_campaign_spring_famine_ignores_first_turn_famine(self):
        self.mock_game.turn_number = 4
        self.mock_game.scenario.rules = Rules(
            famine_active=True,
            first_turn_famine=False,
        )

        with (
            patch("machiavelli.engine.core.ExpenditureProcessor"),
            patch("machiavelli.engine.core.DisastersManager") as disasters,
            patch("machiavelli.engine.core.RebellionManager"),
            patch("machiavelli.engine.core.BribeResolver"),
            patch("machiavelli.engine.core.AssassinationResolver"),
            patch("machiavelli.engine.core.MilitaryResolver"),
            patch("machiavelli.engine.core.RetreatHandler"),
            patch("machiavelli.engine.core.ControlManager"),
            patch("machiavelli.engine.core.IncomeManager"),
        ):
            self.engine.run_campaign()

        disasters.return_value.spawn_famine.assert_called_once_with()


class TestGameEngineRun(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.engine = GameEngine(game=self.mock_game)

    def test_run_do_startup(self):
        """Llama exclusivamente a run_startup() cuando turn_number es 0."""
        self.mock_game.turn_number = 0

        with (
            patch.object(self.engine, "run_startup") as mock_startup,
            patch.object(self.engine, "run_maintenance") as mock_maintenance,
            patch.object(self.engine, "run_campaign") as mock_campaign,
        ):
            self.engine.run()

            mock_startup.assert_called_once()
            mock_maintenance.assert_not_called()
            mock_campaign.assert_not_called()

    def test_run_do_maintenance(self):
        """Llama exclusivamente a run_maintenance() en el primer turno de primavera."""
        spring_turns = [1, 5, 9, 13]

        for turn in spring_turns:
            with self.subTest(turn_number=turn):
                self.mock_game.turn_number = turn

                with (
                    patch.object(self.engine, "run_startup") as mock_startup,
                    patch.object(self.engine, "run_maintenance") as mock_maintenance,
                    patch.object(self.engine, "run_campaign") as mock_campaign,
                ):
                    self.engine.run()

                    mock_maintenance.assert_called_once()
                    mock_startup.assert_not_called()
                    mock_campaign.assert_not_called()

    def test_run_do_campaign(self):
        """Llama exclusivamente a run_campaign() en el resto de estaciones."""
        campaign_turns = [2, 3, 4, 6, 7, 8, 10, 11, 12]

        for turn in campaign_turns:
            with self.subTest(turn_number=turn):
                self.mock_game.turn_number = turn

                with (
                    patch.object(self.engine, "run_startup") as mock_startup,
                    patch.object(self.engine, "run_maintenance") as mock_maintenance,
                    patch.object(self.engine, "run_campaign") as mock_campaign,
                ):
                    self.engine.run()

                    mock_campaign.assert_called_once()
                    mock_startup.assert_not_called()
                    mock_maintenance.assert_not_called()

    def test_run_replaces_history_once_before_each_turn_kind(self):
        """El inicio, el mantenimiento y la campaña comparten un único reinicio del
        historial al entrar.
        """
        previous = TurnEvent(EventType.START_GAME, {"scenario": "previous"})
        current = TurnEvent(EventType.START_SEASON, {"year": 1454, "season": 1})
        cases = (
            (0, "run_startup"),
            (1, "run_maintenance"),
            (2, "run_campaign"),
        )

        for turn_number, phase_name in cases:
            with self.subTest(phase=phase_name):
                game = TrackingGame(turn_number, [previous])
                engine = GameEngine(game)  # type: ignore[arg-type]

                def emit_current(active_game: TrackingGame = game) -> None:
                    self.assertEqual(active_game.history_replacements, 1)
                    self.assertEqual(active_game.turn_events, [])
                    active_game.turn_events.append(current)

                with (
                    patch.object(engine, "run_startup") as startup,
                    patch.object(engine, "run_maintenance") as maintenance,
                    patch.object(engine, "run_campaign") as campaign,
                ):
                    selected = {
                        "run_startup": startup,
                        "run_maintenance": maintenance,
                        "run_campaign": campaign,
                    }[phase_name]
                    selected.side_effect = emit_current
                    engine.run()

                self.assertEqual(game.history_replacements, 1)
                self.assertEqual(game.turn_events, [current])
                self.assertEqual(game.advance_calls, 1)

    def test_run_resets_before_failure_and_does_not_advance(self):
        """Una fase que falla ve el historial nuevo, pero no puede avanzar el ciclo de
        vida.
        """
        previous = TurnEvent(EventType.START_GAME, {"scenario": "previous"})
        game = TrackingGame(2, [previous])
        engine = GameEngine(game)  # type: ignore[arg-type]

        def fail_campaign() -> None:
            self.assertEqual(game.history_replacements, 1)
            self.assertEqual(game.turn_events, [])
            raise RuntimeError("phase failed")

        with (
            patch.object(engine, "run_campaign", side_effect=fail_campaign),
            self.assertRaisesRegex(RuntimeError, "phase failed"),
        ):
            engine.run()

        self.assertEqual(game.history_replacements, 1)
        self.assertEqual(game.turn_events, [])
        self.assertEqual(game.advance_calls, 0)

    def test_run_advances_lifecycle_only_after_success(self):
        """No avanza ni limpia órdenes cuando la fase activa falla."""
        self.mock_game.turn_number = 2

        with patch.object(self.engine, "run_campaign"):
            self.engine.run()
        self.mock_game.advance_turn.assert_called_once_with()

        self.mock_game.advance_turn.reset_mock()
        with (
            patch.object(
                self.engine,
                "run_campaign",
                side_effect=RuntimeError("phase failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.engine.run()
        self.mock_game.advance_turn.assert_not_called()

    # def test_run_maintenance_uses_the_game_domain_rules(self):
    #     """La integración no duplica todavía el algoritmo de mantenimiento."""
    #     self.engine.run_maintenance()
    #     self.mock_game.spring_maintenance.assert_called_once_with()


def _turn_snapshot(game: Game) -> dict[str, object]:
    """Devuelve el estado primitivo completo utilizado por las caracterizaciones de la
    fase 5.
    """
    return {
        "turn_number": game.turn_number,
        "famine": tuple(game.famine),
        "military": military_snapshot(game)[:-1],
        "players": tuple(
            sorted(
                (
                    player.player_id,
                    player.discord_id,
                    player.power,
                    tuple(player.controlled_locations),
                    tuple(player.home_countries),
                    player.ducats,
                    tuple(player.ass_counters),
                    tuple(
                        (command.actor, command.command, command.target)
                        for command in player.commands
                    ),
                )
                for player in game.players
            )
        ),
        "events": tuple(
            (event.type.value, event.to_json())
            for event in game.turn_events
            if event.type is not EventType.MILITARY_ORDERS_SUMMARY
        ),
    }


def _domain_event_types(events: list[TurnEvent]) -> tuple[EventType, ...]:
    """Filtra el evento técnico de recepción al comprobar el orden del dominio."""
    return tuple(
        event.type
        for event in events
        if event.type is not EventType.MILITARY_ORDERS_SUMMARY
    )


ACTIVE_STARTUP_SNAPSHOT_V1 = {
    "turn_number": 1,
    "famine": ("free",),
    "military": (
        (("P0", "fort"), ("P1", "other")),
        (),
        (),
        ("free",),
        (),
        (),
    ),
    "players": (
        ("P0", 1000, "M", ("fort",), ("M",), 3, ("V",), ()),
        ("P1", 1001, "V", ("other",), ("V",), 3, ("M",), ()),
    ),
    "events": (
        ("start_game", '{"scenario":"active-rules-v1"}'),
        (
            "start_game_power_assigned",
            '{"discord_id":1000,"player_id":"P0","power_id":"M"}',
        ),
        (
            "start_game_power_assigned",
            '{"discord_id":1001,"player_id":"P1","power_id":"V"}',
        ),
        (
            "famine_spawn",
            '{"provinces":["free"],"severity_roll":1}',
        ),
        (
            "income_collected",
            '{"cities":["fort"],"city_income":2,"player":"P0",'
            '"province_income":1,"provinces":["fort"],"total_income":3,'
            '"variable_income":[]}',
        ),
        (
            "income_collected",
            '{"cities":["other"],"city_income":2,"player":"P1",'
            '"province_income":1,"provinces":["other"],"total_income":3,'
            '"variable_income":[]}',
        ),
    ),
}

ACTIVE_MAINTENANCE_SNAPSHOT_V1 = {
    "turn_number": 2,
    "famine": ("free",),
    "military": (
        (("P0", "fort"), ("P1", "other")),
        (),
        (),
        ("free",),
        (),
        (),
    ),
    "players": (
        ("P0", 1000, "M", ("fort",), ("M",), 0, ("V",), ()),
        ("P1", 1001, "V", ("other",), ("V",), 0, ("M",), ()),
    ),
    "events": (
        (
            "maintenance_order_resolved",
            '{"actor":"A fort","cost":3,"order":"M","player":"P0",'
            '"result":"maintained","target":null}',
        ),
        (
            "maintenance_summary",
            '{"expenses":3,"initial_ducats":3,"player":"P0","remaining_ducats":0}',
        ),
        (
            "maintenance_order_resolved",
            '{"actor":"A other","cost":3,"order":"M","player":"P1",'
            '"result":"maintained","target":null}',
        ),
        (
            "maintenance_summary",
            '{"expenses":3,"initial_ducats":3,"player":"P1","remaining_ducats":0}',
        ),
    ),
}

ACTIVE_CAMPAIGN_SNAPSHOT_V1 = {
    "turn_number": 3,
    "famine": (),
    "military": (
        (),
        (),
        (),
        ("fort",),
        ("fort",),
        (),
    ),
    "players": (
        ("P0", 1000, "M", (), (), 0, (), ()),
        ("P1", 1001, "V", ("other",), ("V",), 0, ("M",), ()),
    ),
    "events": (
        (
            "military_resolution",
            '{"broken_convoys":[],"cancelled_orders":[],"decisions":[],'
            '"dislodgements":[],'
            '"outcomes":[[["P0","A","fort"],"A","fort",false,null],'
            '[["P1","A","other"],"A","other",false,null],'
            '[[null,"G","fort"],"G","fort",false,null],'
            '[[null,"G","free"],"G","free",false,null]],"rebellions":[],'
            '"sieges":[[["P0","A","fort"],"fort","started"]]}',
        ),
        (
            "famine_attrition",
            '{"player":null,"units":["G free"]}',
        ),
        (
            "lose_control",
            '{"player":"P0","provinces":["fort"]}',
        ),
        (
            "lose_home_country",
            '{"home_country":"M","player":"P0"}',
        ),
        ("player_eliminated", '{"player":"P0"}'),
        ("start_season", '{"season":2,"year":1454}'),
        ("famine_end", '{"provinces":["free"]}'),
        (
            "plague_spawn",
            '{"provinces":["other"],"severity_roll":1}',
        ),
        ("plague_death", '{"player":"P1","units":["A other"]}'),
    ),
}


def test_active_rules_versioned_snapshots_v1() -> None:
    game_map = Map(
        provinces={
            "fort": Province(
                "Fort",
                custom_id="fort",
                city="fortified",
                major_city=2,
            ),
            "other": Province(
                "Other",
                custom_id="other",
                city="city",
                major_city=2,
            ),
            "free": Province("Free", custom_id="free", city="fortified"),
        },
        seas={},
    )
    scenario = Scenario(
        name="active-rules-v1",
        year=1454,
        victory_conditions=VictoryConditions(cities=99, home_countries=99),
        rules=Rules(),
        home_countries={
            "M": HomeCountry(provinces=["fort"]),
            "V": HomeCountry(provinces=["other"]),
        },
        powers={
            "M": Power(home_countries=["M"], armies=["fort"]),
            "V": Power(home_countries=["V"], armies=["other"]),
        },
    )
    game = Game(
        name="active-rules-v1",
        scenario_id="active-rules-v1",
        scenario=scenario,
        map=game_map,
    )
    game.add_player("P0", discord_id=1000)
    game.add_player("P1", discord_id=1001)
    engine = GameEngine(game, Random(7))
    disaster_rng = Mock()
    disaster_rng.randint.side_effect = [1, 0, 0, 1, 0, 0]

    with (
        patch("machiavelli.engine.disasters.Random", return_value=disaster_rng),
        patch.object(GameTables, "disasters", {0: ["row"], 1: ["row"]}),
        patch.object(GameTables, "famine", [["free"]]),
        patch.object(GameTables, "plague", [["other"]]),
    ):
        engine.run()
        assert _turn_snapshot(game) == ACTIVE_STARTUP_SNAPSHOT_V1

        engine.run()
        assert _turn_snapshot(game) == ACTIVE_MAINTENANCE_SNAPSHOT_V1

        owner = next(player for player in game.players if "fort" in player.armies)
        game.independent_garrisons.append("fort")
        owner.commands.append(Command(game, owner, "A fort", "B", None))
        engine.run()
        assert _turn_snapshot(game) == ACTIVE_CAMPAIGN_SNAPSHOT_V1


@pytest.mark.parametrize(
    ("runs", "ordered_anchors", "repeated_type"),
    [
        (
            1,
            (
                EventType.START_GAME,
                EventType.START_GAME_POWER_ASSIGNED,
                EventType.INCOME_COLLECTED,
            ),
            EventType.START_GAME_POWER_ASSIGNED,
        ),
        (
            2,
            (
                EventType.MAINTENANCE_ORDER_RESOLVED,
                EventType.MAINTENANCE_SUMMARY,
            ),
            EventType.MAINTENANCE_ORDER_RESOLVED,
        ),
        (
            3,
            (
                EventType.MILITARY_RESOLUTION,
                EventType.START_SEASON,
            ),
            None,
        ),
    ],
)
def test_real_turns_emit_only_ordered_reconstructible_events(
    runs: int,
    ordered_anchors: tuple[EventType, ...],
    repeated_type: EventType | None,
) -> None:
    """El inicio, el mantenimiento y la campaña reales conservan únicamente hechos de
    dominio tipados.
    """
    scenario = Scenario.load_scenarios()["Be"]
    scenario.rules = Rules()
    assert scenario.rules == Rules()
    game = Game(
        name=f"integrated-events-{runs}",
        scenario_id="Be",
        scenario=scenario,
        map=Map.load_map(),
    )
    for index in range(len(scenario.powers)):
        game.add_player(f"P{index}", discord_id=1000 + index)

    engine = GameEngine(game, Random(7))
    for _ in range(runs):
        engine.run()

    assert game.turn_number == runs
    assert all(player.power is not None for player in game.players)
    assert all(player.commands == [] for player in game.players)

    events = game.turn_events
    event_types = _domain_event_types(events)
    assert event_types[0] is ordered_anchors[0]
    anchor_positions = [event_types.index(anchor) for anchor in ordered_anchors]
    assert anchor_positions == sorted(anchor_positions)
    if repeated_type is not None:
        assert event_types.count(repeated_type) > 1
    assert all(isinstance(event, TurnEvent) for event in events)
    assert all(isinstance(event.type, EventType) for event in events)
    assert [TurnEvent(type=event.type, data=event.data) for event in events] == events
    assert all(
        marker not in event.to_json()
        for event in events
        for marker in ("**", "<@", "###", "```", "\n")
    )


def _rule_campaign_game(
    rules: Rules,
    *,
    turn_number: int,
    players: list[dict[str, object]],
    orders: object = None,
    famine: tuple[str, ...] = (),
) -> Game:
    game_map = Map(
        provinces={
            "fort": Province("fort", custom_id="fort", city="fortified"),
            "keep": Province("keep", custom_id="keep", city="fortress"),
            "other": Province("other", custom_id="other", city="city"),
        },
        seas={},
    )
    scenario = Scenario(
        name="rule-integration",
        year=1454,
        victory_conditions=VictoryConditions(cities=99, home_countries=99),
        rules=rules,
        home_countries={
            "M": HomeCountry(provinces=["fort"]),
            "V": HomeCountry(provinces=["other"]),
        },
    )
    game = create_military_game(
        game_map,
        players,
        orders=orders,
        scenario=scenario,
    )
    game.turn_number = turn_number
    game.famine = list(famine)
    return game


def test_first_turn_famine_inactive_integrated_snapshot() -> None:
    games: dict[bool, Game] = {}
    for first_turn_famine in (True, False):
        scenario = Scenario(
            name="startup-rule-integration",
            year=1454,
            victory_conditions=VictoryConditions(cities=99, home_countries=99),
            rules=Rules(first_turn_famine=first_turn_famine),
            home_countries={"M": HomeCountry(provinces=["fort"])},
            powers={"M": Power(home_countries=["M"], armies=["fort"])},
        )
        game = Game(
            name="startup-rule-integration",
            scenario_id="startup-rule-integration",
            scenario=scenario,
            map=Map(
                provinces={
                    "fort": Province("Fort", custom_id="fort", city="fortified"),
                    "free": Province("Free", custom_id="free", city="fortified"),
                },
                seas={},
            ),
        )
        game.add_player("P1", discord_id=1001)
        disaster_rng = Mock()
        disaster_rng.randint.side_effect = [1, 0, 0]
        with (
            patch("machiavelli.engine.disasters.Random", return_value=disaster_rng),
            patch.object(GameTables, "disasters", {0: ["row"], 1: ["row"]}),
            patch.object(GameTables, "famine", [["free"]]),
        ):
            GameEngine(game, Random(7)).run_startup()
        games[first_turn_famine] = game

    active = _turn_snapshot(games[True])
    inactive = _turn_snapshot(games[False])
    assert inactive == {**active, "famine": (), "events": inactive["events"]}
    assert active["famine"] == ("free",)
    assert inactive["famine"] == ()
    active_types = _domain_event_types(games[True].turn_events)
    inactive_types = _domain_event_types(games[False].turn_events)
    assert active_types == (
        EventType.START_GAME,
        EventType.START_GAME_POWER_ASSIGNED,
        EventType.FAMINE_SPAWN,
        EventType.INCOME_COLLECTED,
    )
    assert inactive_types == (
        EventType.START_GAME,
        EventType.START_GAME_POWER_ASSIGNED,
        EventType.INCOME_COLLECTED,
    )
    assert EventType.FAMINE_SPAWN not in inactive_types


def test_famine_inactive_integrated_snapshot_and_event_order() -> None:
    games = {
        active: _rule_campaign_game(
            Rules(famine_active=active),
            turn_number=2,
            players=[
                {
                    "player_id": "P1",
                    "power": "M",
                    "home_countries": ["M"],
                    "controlled_locations": ["fort"],
                    "armies": ["fort"],
                }
            ],
            famine=("fort",),
        )
        for active in (True, False)
    }
    for game in games.values():
        disaster_rng = Mock()
        disaster_rng.randint.side_effect = [1, 0, 0]
        with (
            patch("machiavelli.engine.disasters.Random", return_value=disaster_rng),
            patch.object(GameTables, "disasters", {0: ["row"], 1: ["row"]}),
            patch.object(GameTables, "plague", [["other"]]),
        ):
            GameEngine(game, Random(7)).run_campaign()

    active = _turn_snapshot(games[True])
    inactive = _turn_snapshot(games[False])
    assert inactive == {
        **active,
        "famine": ("fort",),
        "military": (
            (("P1", "fort"),),
            (),
            (),
            (),
            (),
            (),
        ),
        "events": inactive["events"],
    }
    active_types = _domain_event_types(games[True].turn_events)
    inactive_types = _domain_event_types(games[False].turn_events)
    assert active_types == (
        EventType.MILITARY_RESOLUTION,
        EventType.FAMINE_ATTRITION,
        EventType.START_SEASON,
        EventType.FAMINE_END,
        EventType.PLAGUE_SPAWN,
    )
    assert inactive_types == (
        EventType.MILITARY_RESOLUTION,
        EventType.START_SEASON,
        EventType.PLAGUE_SPAWN,
    )
    forbidden = {
        EventType.FAMINE_SPAWN,
        EventType.FAMINE_RELIEF,
        EventType.FAMINE_ATTRITION,
        EventType.FAMINE_END,
    }
    assert forbidden.isdisjoint(inactive_types)
    assert (
        tuple(event for event in active_types if event not in forbidden)
        == inactive_types
    )


def test_assassinations_inactive_integrated_snapshot_and_event_order() -> None:
    games = {
        active: _rule_campaign_game(
            Rules(assassinations_active=active),
            turn_number=3,
            players=[
                {
                    "player_id": "P1",
                    "power": "M",
                    "home_countries": ["M"],
                    "controlled_locations": ["fort"],
                    "armies": ["fort"],
                    "ass_counters": ["V"],
                    "ducats": 20,
                },
                {
                    "player_id": "P2",
                    "power": "V",
                    "home_countries": ["V"],
                    "controlled_locations": ["other"],
                },
            ],
            orders={"P1": [("E E", "5", "V")]},
        )
        for active in (True, False)
    }
    for game in games.values():
        GameEngine(game, Random(7)).run_campaign()

    active = _turn_snapshot(games[True])
    inactive = _turn_snapshot(games[False])
    assert inactive == {
        **active,
        "players": (
            ("P1", None, "M", ("fort",), ("M",), 20, ("V",), ()),
            ("P2", None, "V", ("other",), ("V",), 0, (), ()),
        ),
        "events": inactive["events"],
    }
    active_types = _domain_event_types(games[True].turn_events)
    inactive_types = _domain_event_types(games[False].turn_events)
    assert active_types == (
        EventType.EXPENSE,
        EventType.ASSASSINATION_ATTEMPT,
        EventType.MILITARY_RESOLUTION,
        EventType.START_SEASON,
    )
    assert inactive_types == (
        EventType.MILITARY_RESOLUTION,
        EventType.START_SEASON,
    )
    assert EventType.EXPENSE not in inactive_types
    assert tuple(
        event
        for event in active_types
        if event not in (EventType.EXPENSE, EventType.ASSASSINATION_ATTEMPT)
    ) == (inactive_types)


def test_plague_inactive_integrated_snapshot_and_event_order() -> None:
    games = {
        active: _rule_campaign_game(
            Rules(plague_active=active),
            turn_number=2,
            players=[
                {
                    "player_id": "P1",
                    "power": "M",
                    "home_countries": ["M"],
                    "controlled_locations": ["fort"],
                    "armies": ["fort"],
                },
                {
                    "player_id": "P2",
                    "power": "V",
                    "home_countries": ["V"],
                    "controlled_locations": ["other"],
                    "armies": ["other"],
                },
            ],
            famine=("fort",),
        )
        for active in (True, False)
    }
    for game in games.values():
        disaster_rng = Mock()
        disaster_rng.randint.side_effect = [1, 0, 0]
        with (
            patch("machiavelli.engine.disasters.Random", return_value=disaster_rng),
            patch.object(GameTables, "disasters", {0: ["row"], 1: ["row"]}),
            patch.object(GameTables, "plague", [["other"]]),
        ):
            GameEngine(game, Random(7)).run_campaign()

    active = _turn_snapshot(games[True])
    inactive = _turn_snapshot(games[False])
    assert inactive == {
        **active,
        "military": (
            (("P2", "other"),),
            (),
            (),
            (),
            (),
            (),
        ),
        "events": inactive["events"],
    }
    active_types = _domain_event_types(games[True].turn_events)
    inactive_types = _domain_event_types(games[False].turn_events)
    assert active_types == (
        EventType.MILITARY_RESOLUTION,
        EventType.FAMINE_ATTRITION,
        EventType.START_SEASON,
        EventType.FAMINE_END,
        EventType.PLAGUE_SPAWN,
        EventType.PLAGUE_DEATH,
    )
    assert inactive_types == (
        EventType.MILITARY_RESOLUTION,
        EventType.FAMINE_ATTRITION,
        EventType.START_SEASON,
        EventType.FAMINE_END,
    )
    forbidden = {EventType.PLAGUE_SPAWN, EventType.PLAGUE_DEATH}
    assert forbidden.isdisjoint(inactive_types)
    assert (
        tuple(event for event in active_types if event not in forbidden)
        == inactive_types
    )
