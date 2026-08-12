"""Pruebas del adjudicador militar, sus reglas y su atomicidad."""

import json
import logging
import os
import platform
import sqlite3
import unittest
from collections.abc import Mapping
from functools import partial
from tempfile import TemporaryDirectory
from time import perf_counter
from types import MappingProxyType
from unittest.mock import Mock, patch

from machiavelli.database import upgrade
from machiavelli.engine.military import (
    DislodgementDecision,
    DislodgementResolverRequired,
    InvalidMilitaryState,
    MilitaryOrder,
    MilitaryResolution,
    MilitaryResolutionError,
    MilitaryResolver,
    ResolutionState,
    UnitKey,
    UnitOutcome,
    UnresolvedMilitaryConflict,
    conflict_location,
)
from machiavelli.events import EventType, TurnEvent
from machiavelli.game.game import Game
from machiavelli.game.map import Map, Province, Route, Sea
from machiavelli.game.scenario import Rules, Scenario, VictoryConditions
from tests.machiavelli.engine.helpers import (
    create_military_game,
    iter_military_orderings,
    military_snapshot,
)

BEFORE_EVENT = TurnEvent(EventType.START_GAME, {"scenario": "before"})
EMPTY_ORDERS_EVENT = TurnEvent(
    EventType.MILITARY_ORDERS_SUMMARY, {"orders": [], "invalid_orders": []}
)

logger = logging.getLogger(__name__)


def _event_payload(game: Game) -> dict[str, list[object]]:
    """Devuelve un `payload` nativo nuevo para el último evento militar tipado."""
    event = game.turn_events[-1]
    assert event.type is EventType.MILITARY_RESOLUTION
    return json.loads(event.to_json())


def military_map() -> Map:
    """Mapa pequeño con tierra, mar, costa y una ciudad fortificada."""
    provinces = {
        name: Province(name, custom_id=name, city=city, has_port=port)
        for name, city, port in (
            ("a", None, False),
            ("b", None, False),
            ("c", None, False),
            ("coast", None, True),
            ("fort", "fortified", True),
        )
    }
    seas = {"SEA": Sea("Small Sea")}
    seas["SEA"].id = "SEA"
    provinces["coast S"] = provinces["coast"]
    for origin, destination in (
        ("a", "b"),
        ("b", "a"),
        ("b", "c"),
        ("c", "b"),
        ("c", "fort"),
        ("fort", "c"),
    ):
        provinces[origin].land_routes.append(Route(destination))
    for origin, destination in (("coast S", "SEA"), ("SEA", "coast S")):
        location = seas["SEA"] if origin == "SEA" else provinces[origin.split()[0]]
        location.sea_routes.append(Route(destination))
    seas["SEA"].sea_routes.extend((Route("a"), Route("b"), Route("fort")))
    return Map(provinces=provinces, seas=seas)


def fortress_map() -> Map:
    """Mapa mínimo con una ciudad de tipo fortress."""
    return Map(
        provinces={
            "keep": Province(
                "keep",
                custom_id="keep",
                city="fortress",
                has_port=True,
            )
        },
        seas={},
    )


def fortress_scenario(*, active: bool) -> Scenario:
    """Escenario real mínimo que controla la regla fortress_active."""
    return Scenario(
        name="fortress-test",
        year=1454,
        victory_conditions=VictoryConditions(cities=1, home_countries=1),
        rules=Rules(fortress_active=active),
    )


def convoy_map() -> Map:
    """Mapa mínimo para rutas de convoy, incluidos ataques a transportadoras."""
    provinces = {name: Province(name, custom_id=name) for name in ("a", "b", "c", "d")}
    seas = {name: Sea(name) for name in ("S1", "S2", "S3")}
    for name, sea in seas.items():
        sea.id = name
    for origin, destination in (
        ("a", "S1"),
        ("S1", "a"),
        ("S1", "S2"),
        ("S2", "S1"),
        ("S2", "b"),
        ("b", "S2"),
        ("S1", "b"),
        ("b", "S1"),
        ("S1", "S3"),
        ("S3", "S1"),
        ("b", "a"),
        ("a", "b"),
    ):
        location = seas[origin] if origin in seas else provinces[origin]
        location.sea_routes.append(Route(destination))
    provinces["a"].land_routes.append(Route("b"))
    provinces["b"].land_routes.append(Route("a"))
    return Map(provinces=provinces, seas=seas)


class TestMilitaryModelsAndIndex(unittest.TestCase):
    """Verifica identidades, snapshots e índices militares iniciales."""

    def test_unit_key_is_immutable_hashable_and_keeps_coast(self):
        coast = UnitKey("P1", "F", "coast S")
        self.assertEqual(coast, UnitKey("P1", "F", "coast S"))
        self.assertEqual({coast: "fleet"}[coast], "fleet")
        self.assertEqual(conflict_location("coast S", "F"), "coast")
        self.assertEqual(conflict_location("fort", "G"), "G fort")

    def test_index_separates_city_and_province_and_preserves_coast(self):
        game = create_military_game(
            military_map(),
            players=[{"player_id": "P1", "armies": ["a"], "fleets": ["coast S"]}],
            independent_garrisons=["fort"],
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()

        army = UnitKey("P1", "A", "a")
        fleet = UnitKey("P1", "F", "coast S")
        independent = UnitKey(None, "G", "fort")
        self.assertEqual(resolver.army_by_origin["a"], army)
        self.assertEqual(resolver.fleet_by_conflict_location["coast"], fleet)
        self.assertEqual(resolver.actor_to_unit[("P1", "F coast S")], fleet)
        self.assertIn(independent, resolver.units_by_key)
        self.assertNotIn("G fort", resolver.fleet_by_conflict_location)

    def test_invalid_snapshot_aborts_before_orders(self):
        cases = (
            ("duplicate key", [{"player_id": "P1", "armies": ["a", "a"]}]),
            (
                "duplicate province",
                [
                    {"player_id": "P1", "armies": ["a"]},
                    {"player_id": "P2", "armies": ["a"]},
                ],
            ),
            (
                "duplicate normalized fleet province",
                [
                    {"player_id": "P1", "fleets": ["coast S"]},
                    {"player_id": "P2", "fleets": ["coast N"]},
                ],
            ),
            (
                "two city garrisons",
                [
                    {"player_id": "P1", "garrisons": ["fort"]},
                    {"player_id": "P2", "garrisons": ["fort"]},
                ],
            ),
        )
        for label, players in cases:
            with self.subTest(label):
                resolver = MilitaryResolver(
                    create_military_game(military_map(), players)
                )
                with self.assertRaises(InvalidMilitaryState):
                    resolver._build_unit_index()


class TestOrderCompilation(unittest.TestCase):
    """Comprueba la compilación y validación estática de órdenes."""

    def _compile(self, players, orders, **game_kwargs):
        resolver = MilitaryResolver(
            create_military_game(military_map(), players, orders=orders, **game_kwargs)
        )
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        return resolver

    def test_all_order_codes_have_a_logical_order(self):
        cases = (
            ("A", {"armies": ["a"]}, ("A a", "A", "b"), {}),
            (
                "B",
                {"armies": ["fort"]},
                ("A fort", "B", None),
                {"independent_garrisons": ["fort"]},
            ),
            ("H", {"armies": ["a"]}, ("A a", "H", ""), {}),
            (
                "L",
                {"armies": ["fort"]},
                ("A fort", "L", None),
                {
                    "independent_garrisons": ["fort"],
                    "besieges": ["fort"],
                },
            ),
            ("S", {"fleets": ["SEA"]}, ("F SEA", "S", "a"), {}),
            (
                "T",
                {"fleets": ["SEA"], "armies": ["a"]},
                ("F SEA", "T", "A a"),
                {},
            ),
            ("C", {"garrisons": ["fort"]}, ("G fort", "C", "A"), {}),
        )
        for code, unit_spec, command, game_kwargs in cases:
            with self.subTest(code):
                resolver = self._compile(
                    [{"player_id": "P1", **unit_spec}],
                    [command],
                    **game_kwargs,
                )
                self.assertIn(
                    code,
                    {order.order_type for order in resolver.orders_by_unit.values()},
                )

    def test_support_and_transport_grammar(self):
        resolver = self._compile(
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a"],
                    "fleets": ["SEA"],
                },
                {"player_id": "P2", "power": "V", "armies": ["b"]},
            ],
            {"P1": [("F SEA", "S", "a"), ("A a", "H", "")]},
        )
        fleet = UnitKey("P1", "F", "SEA")
        self.assertEqual(resolver.orders_by_unit[fleet].supported_faction, "M")

        resolver = self._compile(
            [
                {"player_id": "P1", "power": "M", "fleets": ["SEA"]},
                {"player_id": "P2", "power": "V", "armies": ["a"]},
            ],
            {"P1": [("F SEA", "S", "a (V)")]},
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "F", "SEA")].supported_faction,
            "V",
        )

        resolver = self._compile(
            [{"player_id": "P1", "power": "M", "fleets": ["SEA"]}],
            [("F SEA", "S", "coast S (M)")],
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "F", "SEA")].target_location,
            "coast S",
        )

        resolver = self._compile(
            [
                {"player_id": "P1", "fleets": ["SEA"]},
                {"player_id": "P2", "armies": ["a"]},
            ],
            {"P1": [("F SEA", "T", "A a")], "P2": [("A a", "H", "")]},
        )
        transport = resolver.orders_by_unit[UnitKey("P1", "F", "SEA")]
        self.assertEqual(transport.transported_army, UnitKey("P2", "A", "a"))

        invalid_targets = ("a(P1)", "a (P1) extra", "a (missing", "A a P1")
        for target in invalid_targets:
            with self.subTest(target):
                resolver = self._compile(
                    [{"player_id": "P1", "fleets": ["SEA"]}],
                    [("F SEA", "S" if target.startswith("a") else "T", target)],
                )
                key = UnitKey("P1", "F", "SEA")
                self.assertEqual(resolver.orders_by_unit[key].order_type, "H")
                self.assertIn(key, resolver.invalid_orders)

    def test_missing_invalid_and_orphan_rows_only_affect_existing_unit(self):
        resolver = self._compile(
            [
                {"player_id": "P1", "armies": ["a"]},
                {"player_id": "P2", "armies": ["b"]},
            ],
            {
                "P1": [("A ghost", "A", "b"), ("A a", "X", "b")],
                "P2": [("A b", "A", "a")],
            },
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "A", "a")].order_type, "H"
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P2", "A", "b")].order_type, "A"
        )
        self.assertEqual(set(resolver.invalid_orders), {UnitKey("P1", "A", "a")})

    def test_orphan_rows_from_purchase_disband_and_ownership_change_are_ignored(self):
        cases = (
            (
                "purchase changes the saved actor",
                [{"player_id": "P1", "armies": ["b"]}],
                UnitKey("P1", "A", "b"),
            ),
            (
                "disband leaves no current unit for the saved key",
                [{"player_id": "P1"}, {"player_id": "P2", "armies": ["b"]}],
                UnitKey("P2", "A", "b"),
            ),
            (
                "ownership transfer leaves the unit under another player",
                [
                    {"player_id": "P1", "armies": ["b"]},
                    {"player_id": "P2", "armies": ["a"]},
                ],
                UnitKey("P2", "A", "a"),
            ),
        )
        for label, players, current_key in cases:
            with self.subTest(label):
                resolver = self._compile(
                    players,
                    {"P1": [("A a", "A", "b")]},
                )
                self.assertEqual(resolver.orders_by_unit[current_key].order_type, "H")
                self.assertFalse(resolver.invalid_orders)
                self.assertNotIn(UnitKey("P1", "A", "a"), resolver.orders_by_unit)
                self.assertFalse(
                    any(
                        order.order_type == "T"
                        for order in resolver.orders_by_unit.values()
                    )
                )

    def test_invalid_target_and_missing_order_become_hold(self):
        resolver = self._compile(
            [{"player_id": "P1", "armies": ["a", "b"]}],
            [("A a", "A", "SEA")],
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "A", "a")].order_type, "H"
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "A", "b")].order_type, "H"
        )

    def test_compilation_does_not_mutate_game(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "armies": ["a"]}],
            orders=[("A a", "A", "b")],
        )
        before = military_snapshot(game)
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        self.assertEqual(military_snapshot(game), before)

    def test_fleet_advance_keeps_exact_coast(self):
        resolver = self._compile(
            [{"player_id": "P1", "fleets": ["coast S"]}],
            [("F coast S", "A", "SEA")],
        )
        key = UnitKey("P1", "F", "coast S")
        self.assertEqual(resolver.orders_by_unit[key].target_location, "SEA")

    def test_invalid_exact_coast_aborts_before_compilation(self):
        game = create_military_game(
            military_map(), [{"player_id": "P1", "fleets": ["coast N"]}]
        )
        with self.assertRaises(InvalidMilitaryState):
            MilitaryResolver(game)._build_unit_index()

    def test_garrison_can_support_its_own_province(self):
        resolver = self._compile(
            [{"player_id": "P1", "garrisons": ["fort"]}],
            [("G fort", "S", "fort")],
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "G", "fort")].order_type,
            "S",
        )


class TestAtomicResolution(unittest.TestCase):
    """Garantiza resultados completos y rollback ante cualquier fallo."""

    def _game(self):
        return create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a"],
                    "fleets": ["SEA"],
                },
                {"player_id": "P2", "power": "V", "armies": ["c"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("F SEA", "S", "b")],
                "P2": [("A c", "A", "b")],
            },
            turn_events=[BEFORE_EVENT],
        )

    def test_victory_tie_support_and_permutations_are_deterministic(self):
        outcomes = []
        for game in iter_military_orderings(self._game):
            resolver = MilitaryResolver(game)
            resolution = resolver.run()
            outcomes.append((resolution, military_snapshot(game)))
            armies = {player.player_id: player.armies for player in game.players}
            self.assertEqual(armies, {"P1": ["b"], "P2": ["c"]})
            self.assertEqual(len(resolution.outcomes), len(resolver.units_by_key))
            self.assertEqual(
                {outcome.unit for outcome in resolution.outcomes},
                set(resolver.units_by_key),
            )
            self.assertEqual(
                _event_payload(game)["outcomes"],
                [
                    [["P1", "A", "a"], "A", "b", False, None],
                    [["P1", "F", "SEA"], "F", "SEA", False, None],
                    [["P2", "A", "c"], "A", "c", False, None],
                ],
            )
        self.assertTrue(all(outcome == outcomes[0] for outcome in outcomes))

    def test_unopposed_victory_and_tie_are_resolved_from_the_snapshot(self):
        victory = create_military_game(
            military_map(),
            [{"player_id": "P1", "armies": ["a"]}],
            orders=[("A a", "A", "b")],
        )
        MilitaryResolver(victory).run()
        self.assertEqual(victory.players[0].armies, ["b"])

        tie = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "armies": ["a"]},
                {"player_id": "P2", "armies": ["c"]},
            ],
            orders={"P1": [("A a", "A", "b")], "P2": [("A c", "A", "b")]},
        )
        resolution = MilitaryResolver(tie).run()
        self.assertEqual([player.armies for player in tie.players], [["a"], ["c"]])
        self.assertEqual(resolution.contested_locations, frozenset({"b"}))

    def test_conversion_wins_and_loses_without_partial_type_changes(self):
        winner = create_military_game(
            military_map(),
            [{"player_id": "P1", "garrisons": ["fort"]}],
            orders=[("G fort", "C", "A")],
        )
        MilitaryResolver(winner).run()
        self.assertEqual(winner.players[0].armies, ["fort"])

        loser = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "power": "M", "garrisons": ["fort"]},
                {
                    "player_id": "P2",
                    "power": "V",
                    "armies": ["c"],
                    "fleets": ["SEA"],
                },
            ],
            orders={
                "P1": [("G fort", "C", "A")],
                "P2": [("A c", "A", "fort"), ("F SEA", "S", "fort")],
            },
        )
        MilitaryResolver(loser).run()
        self.assertEqual(loser.players[0].garrisons, ["fort"])
        self.assertEqual(loser.players[1].armies, ["fort"])

    def test_conversion_tie_keeps_garrison_and_attacker_at_origin(self):
        game = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "garrisons": ["fort"]},
                {"player_id": "P2", "armies": ["c"]},
            ],
            orders={"P1": [("G fort", "C", "A")], "P2": [("A c", "A", "fort")]},
        )
        resolution = MilitaryResolver(game).run()
        self.assertEqual(game.players[0].garrisons, ["fort"])
        self.assertEqual(game.players[1].armies, ["c"])
        self.assertEqual(resolution.contested_locations, frozenset({"fort"}))
        event = _event_payload(game)
        self.assertEqual(
            event["cancelled_orders"],
            [["P1", "G", "fort"], ["P2", "A", "c"]],
        )

    def test_orders_summary_precedes_resolution_and_serializes_compiled_orders(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["a", "b"]}],
            orders=[("A a", "A", "b"), ("A b", "X", "")],
            turn_events=[BEFORE_EVENT],
        )

        MilitaryResolver(game).run()

        summary = game.turn_events[-2]
        self.assertIs(summary.type, EventType.MILITARY_ORDERS_SUMMARY)
        self.assertIs(game.turn_events[-1].type, EventType.MILITARY_RESOLUTION)
        self.assertEqual(
            json.loads(summary.to_json()),
            {
                "orders": [
                    [["P1", "A", "a"], "A", "b", ["a", "b"], None, None, False],
                    [["P1", "A", "b"], "H", None, None, None, None, False],
                ],
                "invalid_orders": [
                    [["P1", "A", "b"], "código de orden inválido"]
                ],
            },
        )

    def test_corrupt_snapshot_and_event_failures_are_atomic(self):
        game = create_military_game(
            military_map(), [{"player_id": "P1", "armies": ["a", "a"]}]
        )
        before = military_snapshot(game)
        with self.assertRaises(InvalidMilitaryState):
            MilitaryResolver(game).run()
        self.assertEqual(military_snapshot(game), before)

        game = self._game()
        before = military_snapshot(game)
        with patch(
            "machiavelli.engine.military.TurnEvent.military_resolution",
            side_effect=ValueError,
        ):
            with self.assertRaises(MilitaryResolutionError):
                MilitaryResolver(game).run()
        self.assertEqual(military_snapshot(game), before)

    def test_incomplete_resolution_stops_before_event_or_commit(self):
        game = self._game()
        before = military_snapshot(game)
        incomplete = ResolutionState(
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            (),
            frozenset(),
        )
        resolver = MilitaryResolver(game)
        dislodgement_resolver = Mock()
        with (
            patch.object(resolver, "_resolve_conflicts", return_value=incomplete),
            patch.object(resolver, "_build_resolution") as build_resolution,
            patch.object(resolver, "_build_final_collections") as collections,
            patch("machiavelli.engine.military.TurnEvent.military_resolution") as event,
            patch.object(resolver, "_apply_final_collections") as apply,
        ):
            with self.assertRaises(MilitaryResolutionError):
                resolver.run(dislodgement_resolver)
        dislodgement_resolver.assert_not_called()
        build_resolution.assert_not_called()
        collections.assert_not_called()
        event.assert_not_called()
        apply.assert_not_called()
        self.assertEqual(military_snapshot(game), before)

    def test_partial_convoy_is_rejected_before_final_collections(self):
        game = create_military_game(
            military_map(), [{"player_id": "P1", "armies": ["a"]}]
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        key = UnitKey("P1", "A", "a")
        resolver.orders_by_unit[key] = MilitaryOrder(
            key, "A", "b", path=("a", "SEA", "b"), is_convoy=True
        )
        resolution = MilitaryResolution(
            (UnitOutcome(key, "A", "b", False),), frozenset()
        )
        with self.assertRaises(MilitaryResolutionError):
            resolver._build_final_collections(resolution)

        resolver.orders_by_unit[key] = MilitaryOrder(
            key,
            "A",
            "b",
            path=("a", "SEA", "SEA2", "b"),
            is_convoy=True,
            transporters=(UnitKey("P1", "F", "SEA"),),
        )
        with self.assertRaises(MilitaryResolutionError):
            resolver._build_final_collections(resolution)

    def test_commit_failure_restores_all_collections_and_events(self):
        class FailingPlayer:
            """Fuerza un fallo puntual durante la frontera de commit."""

            def __init__(self, source):
                self.player_id = source.player_id
                self.power = source.power
                self.armies = source.armies
                self.fleets = source.fleets
                self.garrisons = source.garrisons
                self.commands = source.commands
                self.rebelled_provinces = source.rebelled_provinces
                self.rebelled_cities = source.rebelled_cities
                self.fail_on_fleets = True

            def __setattr__(self, name, value):
                if name == "fleets" and getattr(self, "fail_on_fleets", False):
                    object.__setattr__(self, "fail_on_fleets", False)
                    raise RuntimeError("commit failure")
                object.__setattr__(self, name, value)

        game = self._game()
        game.players[0] = FailingPlayer(game.players[0])
        before = military_snapshot(game)
        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(game).run()
        self.assertEqual(military_snapshot(game), before)

    def test_persistent_rollback_failure_preserves_other_collections(self):
        class PersistentFailingPlayer:
            """Simula un atributo que falla al aplicar y al restaurar."""

            def __init__(self, source):
                self.player_id = source.player_id
                self.power = source.power
                self.armies = source.armies
                self.fleets = source.fleets
                self.garrisons = source.garrisons
                self.commands = source.commands
                self.rebelled_provinces = source.rebelled_provinces
                self.rebelled_cities = source.rebelled_cities
                self.fail_on_fleets = True

            def __setattr__(self, name, value):
                if name == "fleets" and getattr(self, "fail_on_fleets", False):
                    raise RuntimeError("persistent commit failure")
                object.__setattr__(self, name, value)

        game = self._game()
        game.players[0] = PersistentFailingPlayer(game.players[0])
        before = military_snapshot(game)
        with self.assertRaises(MilitaryResolutionError) as raised:
            MilitaryResolver(game).run()
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(game.players[0].armies, ["a"])
        self.assertEqual(game.players[0].fleets, ["SEA"])
        self.assertEqual(game.players[0].garrisons, [])
        self.assertEqual(game.players[1].armies, ["c"])
        self.assertEqual(game.players[1].fleets, [])
        self.assertEqual(game.players[1].garrisons, [])
        self.assertEqual(game.independent_garrisons, list(before[3]))
        self.assertEqual(game.besieges, list(before[4]))
        self.assertEqual(game.turn_events[:-1], list(before[6]))
        self.assertIs(game.turn_events[-1].type, EventType.MILITARY_ORDERS_SUMMARY)


class TestConvoyCompilationAndResolution(unittest.TestCase):
    """Cubre rutas encadenadas, transportes y rotura de convoyes."""

    def _game(
        self,
        *,
        army_orders,
        fleet_orders,
        enemy_orders=(),
        enemy_fleets=(),
        enemy_armies=(),
        transport_fleets=("S1", "S2"),
    ):
        return create_military_game(
            convoy_map(),
            [
                {"player_id": "P1", "power": "M", "armies": ["a", "c"]},
                {
                    "player_id": "P2",
                    "power": "V",
                    "armies": enemy_armies,
                    "fleets": enemy_fleets,
                },
                {"player_id": "P3", "power": "G", "fleets": transport_fleets},
            ],
            orders={
                "P1": army_orders,
                "P2": enemy_orders,
                "P3": fleet_orders,
            },
        )

    def _compile(self, **kwargs):
        resolver = MilitaryResolver(self._game(**kwargs))
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        return resolver

    def test_convoy_compilation_and_validation_matrix(self):
        valid_cases = (
            (
                "one fleet",
                [("A a", "A", "S1"), ("A a", "A", "b")],
                [("F S1", "T", "A a")],
                ("a", "S1", "b"),
                (UnitKey("P3", "F", "S1"),),
            ),
            (
                "T inversa correcta, two foreign fleets interspersed",
                [
                    ("A a", "A", "S1"),
                    ("A c", "H", ""),
                    ("A a", "A", "S2"),
                    ("A a", "A", "b"),
                ],
                [("F S1", "T", "A a"), ("F S2", "T", "A a")],
                ("a", "S1", "S2", "b"),
                (UnitKey("P3", "F", "S1"), UnitKey("P3", "F", "S2")),
            ),
            (
                "finite repeated route deduplicates only dependencies",
                [
                    ("A a", "A", "S1"),
                    ("A a", "A", "S2"),
                    ("A a", "A", "S1"),
                    ("A a", "A", "b"),
                ],
                [("F S1", "T", "A a"), ("F S2", "T", "A a")],
                ("a", "S1", "S2", "S1", "b"),
                (UnitKey("P3", "F", "S1"), UnitKey("P3", "F", "S2")),
            ),
        )
        for label, army_orders, fleet_orders, path, transporters in valid_cases:
            with self.subTest(label):
                resolver = self._compile(
                    army_orders=army_orders, fleet_orders=fleet_orders
                )
                order = resolver.orders_by_unit[UnitKey("P1", "A", "a")]
                self.assertEqual((order.is_convoy, order.path), (True, path))
                self.assertEqual(order.transporters, transporters)
                self.assertTrue(
                    all(
                        resolver.orders_by_unit[transporter].transported_army
                        == UnitKey("P1", "A", "a")
                        for transporter in transporters
                    )
                )
                self.assertFalse(resolver.invalid_orders)

        invalid_cases = (
            ("transport absent", [], [("A a", "A", "S1"), ("A a", "A", "b")]),
            (
                "transport for different army",
                [("F S1", "T", "A c")],
                [("A a", "A", "S1"), ("A a", "A", "b")],
            ),
            (
                "duplicate transport rows",
                [("F S1", "T", "A a"), ("F S1", "T", "A a")],
                [("A a", "A", "S1"), ("A a", "A", "b")],
            ),
            (
                "non adjacent leg",
                [("F S1", "T", "A a")],
                [("A a", "A", "S1"), ("A a", "A", "d")],
            ),
            (
                "sea destination",
                [("F S1", "T", "A a")],
                [("A a", "A", "S1"), ("A a", "A", "S2")],
            ),
        )
        for label, fleet_orders, army_orders in invalid_cases:
            with self.subTest(label):
                resolver = self._compile(
                    army_orders=army_orders, fleet_orders=fleet_orders
                )
                self.assertEqual(
                    resolver.orders_by_unit[UnitKey("P1", "A", "a")].order_type,
                    "H",
                )

        resolver = self._compile(army_orders=[("A a", "A", "S1")], fleet_orders=[])
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "A", "a")].order_type, "H"
        )

        resolver = self._compile(
            army_orders=[("A a", "A", "S1"), ("A a", "A", "b")],
            fleet_orders=[("F b", "T", "A a")],
            transport_fleets=("b",),
        )
        self.assertEqual(
            resolver.orders_by_unit[UnitKey("P1", "A", "a")].order_type, "H"
        )

        incompatible_cases = (
            ("Advance plus Hold", [("A a", "A", "S1"), ("A a", "H", "")]),
            ("Advance plus Support", [("A a", "A", "S1"), ("A a", "S", "b")]),
        )
        for label, army_orders in incompatible_cases:
            with self.subTest(label):
                resolver = self._compile(army_orders=army_orders, fleet_orders=[])
                army = UnitKey("P1", "A", "a")
                order = resolver.orders_by_unit[army]
                self.assertEqual(order, MilitaryOrder(army, "H"))
                self.assertIn(army, resolver.invalid_orders)
                state = resolver._resolve_conflicts()
                self.assertNotIn(army, state.successful_moves)
                outcome = next(
                    outcome
                    for outcome in resolver._build_resolution(state).outcomes
                    if outcome.unit == army
                )
                self.assertEqual(outcome.final_location, "a")

    def test_convoy_resolves_only_at_destination_without_crossing(self):
        game = self._game(
            army_orders=[("A a", "A", "S1"), ("A a", "A", "S2"), ("A a", "A", "b")],
            fleet_orders=[("F S1", "T", "A a"), ("F S2", "T", "A a")],
            enemy_fleets=["S3"],
            enemy_orders=[("F S3", "H", "")],
        )
        resolution = MilitaryResolver(game).run()
        self.assertEqual(game.players[0].armies, ["b", "c"])
        self.assertEqual(game.players[2].fleets, ["S1", "S2"])
        self.assertEqual(
            next(
                outcome.final_location
                for outcome in resolution.outcomes
                if outcome.unit.origin == "a"
            ),
            "b",
        )
        self.assertNotIn("S1", resolution.contested_locations)
        self.assertNotIn("S2", resolution.contested_locations)

        opposed = self._game(
            army_orders=[("A a", "A", "S1"), ("A a", "A", "b")],
            fleet_orders=[("F S1", "T", "A a")],
            enemy_armies=["b"],
            enemy_orders=[("A b", "A", "a")],
            transport_fleets=("S1",),
        )
        MilitaryResolver(opposed).run()
        self.assertEqual(opposed.players[0].armies, ["b", "c"])
        self.assertEqual(opposed.players[1].armies, ["a"])

    def test_convoy_dislodgement_records_its_last_scale_as_attack_origin(self):
        game = self._game(
            army_orders=[("A a", "A", "S1"), ("A a", "A", "S2"), ("A a", "A", "b")],
            fleet_orders=[("F S1", "T", "A a"), ("F S2", "T", "A a")],
            enemy_armies=["b"],
            enemy_orders=[("A b", "H", "")],
        )
        game.players[1].rebelled_provinces = ["b"]
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()

        resolution = resolver._build_resolution(resolver._resolve_conflicts())
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}

        self.assertTrue(outcomes[UnitKey("P2", "A", "b")].dislodged)
        self.assertEqual(outcomes[UnitKey("P2", "A", "b")].attack_origin, "S2")

    def test_convoy_against_opposed_direct_move_and_transporter_attacks(self):
        cases = (
            ("tie", [], [], False),
            (
                "failed attack",
                [("F S2", "A", "S1"), ("F S3", "A", "S1")],
                ["S2", "S3"],
                False,
            ),
            ("defensive victory", [("F S2", "A", "S1")], ["S2"], False),
            (
                "dislodged",
                [("F S2", "A", "S1"), ("F S3", "S", "S1")],
                ["S2", "S3"],
                True,
            ),
        )
        for label, enemy_orders, enemy_fleets, broken in cases:
            with self.subTest(label):
                fleet_orders = [("F S1", "T", "A a")]
                transport_fleets = ("S1",)
                if label == "tie":
                    enemy_orders, enemy_fleets = [("F S2", "A", "S1")], ["S2"]
                    transport_fleets = ("S1",)
                if label == "defensive victory":
                    fleet_orders.append(("F S3", "S", "S1"))
                    transport_fleets = ("S1", "S3")
                resolver = MilitaryResolver(
                    self._game(
                        army_orders=[("A a", "A", "S1"), ("A a", "A", "b")],
                        fleet_orders=fleet_orders,
                        enemy_orders=enemy_orders,
                        enemy_fleets=enemy_fleets,
                        transport_fleets=transport_fleets,
                    )
                )
                resolver._build_unit_index()
                resolver._compile_orders()
                resolver._link_and_validate_orders()
                state = resolver._resolve_conflicts()
                army = UnitKey("P1", "A", "a")
                transporter = UnitKey("P3", "F", "S1")
                self.assertEqual(army in state.cancelled_orders, broken)
                self.assertEqual(army in state.successful_moves, not broken)
                self.assertEqual(transporter in state.dislodged_units, broken)
                self.assertEqual(
                    army in state.available_convoys,
                    not broken,
                )
                event = resolver._event_from_resolution(
                    resolver._build_resolution(state), state
                )
                self.assertEqual(
                    event.data["broken_convoys"],
                    (("P1", "A", "a"),) if broken else (),
                )
                outcome = next(
                    outcome
                    for outcome in resolver._build_resolution(state).outcomes
                    if outcome.unit == army
                )
                self.assertEqual(outcome.final_location, "a" if broken else "b")


class TestConflictConstructionAndSupport(unittest.TestCase):
    """Valida la construcción de conflictos, cruces y fuerza de apoyo."""

    def test_support_faction_forms_and_garrison_support_province(self):
        supporter = UnitKey("P3", "A", "c")
        for target, faction in (("b", "G"), ("b (M)", "M")):
            with self.subTest(target=target):
                game = create_military_game(
                    military_map(),
                    [
                        {"player_id": "P1", "power": "M", "armies": ["a"]},
                        {"player_id": "P2", "power": "V", "armies": ["b"]},
                        {"player_id": "P3", "power": "G", "armies": ["c"]},
                    ],
                    orders={"P3": [("A c", "S", target)]},
                )
                resolver = MilitaryResolver(game)
                resolver._build_unit_index()
                resolver._compile_orders()
                resolver._link_and_validate_orders()
                self.assertEqual(
                    resolver.orders_by_unit[supporter].supported_faction, faction
                )

        support_map = military_map()
        support_map.provinces["fort"].land_routes.append(Route("b"))
        game = create_military_game(
            support_map,
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a"],
                    "garrisons": ["fort"],
                },
                {"player_id": "P2", "power": "V", "armies": ["b"]},
            ],
            orders={"P1": [("A a", "A", "b"), ("G fort", "S", "b")]},
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()

        self.assertIn(UnitKey("P1", "G", "fort"), state.active_supports)
        self.assertIn(UnitKey("P1", "A", "a"), state.successful_moves)

    def test_province_and_city_conflicts_can_resolve_together(self):
        game = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "power": "M", "armies": ["c"]},
                {"player_id": "P2", "power": "V", "armies": ["fort"]},
            ],
            orders={"P1": [("A c", "A", "fort")], "P2": [("A fort", "C", "G")]},
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()

        self.assertEqual(state.resolved_conflicts, frozenset({"fort", "G fort"}))
        resolution = resolver._build_resolution(state)
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
        self.assertEqual(outcomes[UnitKey("P1", "A", "c")].final_location, "fort")
        self.assertEqual(outcomes[UnitKey("P2", "A", "fort")].final_unit_type, "G")

    def test_own_exchange_and_province_city_remain_distinct(self):
        exchange = create_military_game(
            military_map(),
            [{"player_id": "P1", "armies": ["a", "b"]}],
            orders={"P1": [("A a", "A", "b"), ("A b", "A", "a")]},
        )
        MilitaryResolver(exchange).run()
        self.assertEqual(exchange.players[0].armies, ["a", "b"])

        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "armies": ["fort"], "garrisons": ["fort"]}],
        )
        resolution = MilitaryResolver(game).run()
        self.assertEqual(len(resolution.outcomes), 2)
        self.assertFalse(resolution.contested_locations)

    def test_enemy_crossing_uses_supports_at_its_own_endpoint(self):
        game = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "power": "M", "armies": ["a"], "fleets": ["SEA"]},
                {"player_id": "P2", "power": "V", "armies": ["b"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("F SEA", "S", "b")],
                "P2": [("A b", "A", "a")],
            },
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        resolution = resolver._build_resolution(resolver._resolve_conflicts())
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
        self.assertEqual(outcomes[UnitKey("P1", "A", "a")].final_location, "b")
        self.assertTrue(outcomes[UnitKey("P2", "A", "b")].dislodged)
        self.assertEqual(outcomes[UnitKey("P2", "A", "b")].attack_origin, "a")
        self.assertEqual(resolution.contested_locations, frozenset({"a", "b"}))

    def test_support_is_cut_by_a_tie_but_not_a_lost_attack_or_origin_exception(self):
        lost = create_military_game(
            convoy_map(),
            [
                {"player_id": "P1", "power": "M", "fleets": ["S1", "S3"]},
                {"player_id": "P2", "power": "V", "fleets": ["S2"]},
            ],
            orders={
                "P1": [("F S1", "S", "b"), ("F S3", "S", "S1")],
                "P2": [("F S2", "A", "S1")],
            },
        )
        resolver = MilitaryResolver(lost)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()
        self.assertIn(UnitKey("P1", "F", "S1"), state.active_supports)

        tie = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "power": "M", "fleets": ["SEA"]},
                {"player_id": "P2", "power": "V", "fleets": ["coast S"]},
            ],
            orders={"P1": [("F SEA", "S", "b")], "P2": [("F coast S", "A", "SEA")]},
        )
        resolver = MilitaryResolver(tie)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()
        self.assertNotIn(UnitKey("P1", "F", "SEA"), state.active_supports)

        exception = create_military_game(
            convoy_map(),
            [
                {"player_id": "P1", "power": "M", "fleets": ["S1"]},
                {"player_id": "P2", "power": "V", "fleets": ["S2"]},
            ],
            orders={"P1": [("F S1", "S", "S2")], "P2": [("F S2", "A", "S1")]},
        )
        resolver = MilitaryResolver(exception)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()
        self.assertIn(UnitKey("P1", "F", "S1"), state.active_supports)

    def test_convert_into_own_campaign_unit_is_a_self_conflict(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "armies": ["fort"], "garrisons": ["fort"]}],
            orders={"P1": [("G fort", "C", "A")]},
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()

        converted = UnitKey("P1", "G", "fort")
        self.assertIn(converted, state.cancelled_by_self_conflict)
        self.assertNotIn(converted, state.successful_conversions)
        self.assertEqual(
            resolver._build_resolution(state).contested_locations, frozenset()
        )

    def test_dislodged_support_is_removed_before_dependent_conflict(self):
        provinces = {
            name: Province(name, custom_id=name)
            for name in ("a", "b", "c", "d", "e", "f")
        }
        for origin, destination in (
            ("a", "b"),
            ("b", "a"),
            ("c", "b"),
            ("b", "c"),
            ("d", "c"),
            ("c", "d"),
            ("e", "c"),
            ("c", "e"),
            ("f", "b"),
            ("b", "f"),
        ):
            provinces[origin].land_routes.append(Route(destination))

        game = create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["a", "c"]},
                {"player_id": "P2", "power": "V", "armies": ["d", "e", "f"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("A c", "S", "b")],
                "P2": [
                    ("A d", "A", "c"),
                    ("A e", "S", "c"),
                    ("A f", "A", "b"),
                ],
            },
        )

        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()

        supporter = UnitKey("P1", "A", "c")
        self.assertIn(supporter, state.dislodged_units)
        self.assertIn(supporter, state.cancelled_orders)
        self.assertNotIn(supporter, state.active_supports)
        self.assertNotIn(UnitKey("P1", "A", "a"), state.successful_moves)
        self.assertNotIn(UnitKey("P2", "A", "f"), state.successful_moves)
        resolution = resolver._build_resolution(state)
        event = resolver._event_from_resolution(resolution, state)
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
        self.assertTrue(outcomes[supporter].dislodged)
        self.assertEqual(outcomes[UnitKey("P1", "A", "a")].final_location, "a")
        self.assertEqual(outcomes[UnitKey("P2", "A", "f")].final_location, "f")
        self.assertEqual(event.data["dislodgements"], (("P1", "A", "c"),))
        self.assertEqual(event.data["cancelled_orders"].count(("P1", "A", "c")), 1)


class TestDependencyResolution(unittest.TestCase):
    """Comprueba el orden estable entre grupos de conflicto dependientes."""

    def _support_dependency_game(self):
        provinces = {
            name: Province(name, custom_id=name)
            for name in ("a", "b", "c", "d", "e", "f")
        }
        for origin, destination in (
            ("a", "b"),
            ("b", "a"),
            ("c", "b"),
            ("b", "c"),
            ("d", "c"),
            ("c", "d"),
            ("e", "c"),
            ("c", "e"),
            ("f", "b"),
            ("b", "f"),
        ):
            provinces[origin].land_routes.append(Route(destination))
        return create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["a", "c"]},
                {"player_id": "P2", "power": "V", "armies": ["d", "e", "f"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("A c", "S", "b")],
                "P2": [
                    ("A d", "A", "c"),
                    ("A e", "S", "c"),
                    ("A f", "A", "b"),
                ],
            },
        )

    def test_support_dependency_is_resolved_only_after_its_independent_group(self):
        game = self._support_dependency_game()
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        initial = resolver._initial_resolution_state()
        groups, moving = resolver._conflict_groups(initial)

        self.assertEqual(resolver._dependencies("b", groups, moving, initial), {"c"})
        self.assertEqual(resolver._dependencies("c", groups, moving, initial), set())

        state = resolver._resolve_conflicts()
        supporter = UnitKey("P1", "A", "c")
        self.assertIn(supporter, state.dislodged_units)
        self.assertNotIn(supporter, state.active_supports)
        self.assertNotIn(UnitKey("P1", "A", "a"), state.successful_moves)

    def test_support_dependency_is_invariant_under_incidental_orderings(self):
        results = []
        for game in iter_military_orderings(self._support_dependency_game):
            before = military_snapshot(game)
            resolver = MilitaryResolver(game)
            resolver._build_unit_index()
            resolver._compile_orders()
            resolver._link_and_validate_orders()
            state = resolver._resolve_conflicts()
            resolution = resolver._build_resolution(state)
            event = resolver._event_from_resolution(resolution, state)
            results.append(
                (
                    resolution,
                    state.cancelled_orders,
                    event.data,
                    military_snapshot(game),
                )
            )
            self.assertEqual(military_snapshot(game), before)
        self.assertTrue(all(result == results[0] for result in results))

    def test_incidental_order_permutations_keep_dependency_result(self):
        def build_game():
            return TestConvoyCompilationAndResolution()._game(
                army_orders=[("A a", "A", "S1"), ("A a", "A", "b")],
                fleet_orders=[("F S1", "T", "A a")],
                enemy_orders=[("F S2", "A", "S1"), ("F S3", "S", "S1")],
                enemy_fleets=["S2", "S3"],
                transport_fleets=("S1",),
            )

        results = []
        for game in iter_military_orderings(build_game):
            before = military_snapshot(game)
            resolver = MilitaryResolver(game)
            resolver._build_unit_index()
            resolver._compile_orders()
            resolver._link_and_validate_orders()
            state = resolver._resolve_conflicts()
            resolution = resolver._build_resolution(state)
            event = resolver._event_from_resolution(resolution, state)
            results.append(
                (
                    resolution,
                    state.cancelled_orders,
                    event.data,
                    military_snapshot(game),
                )
            )
            self.assertEqual(military_snapshot(game), before)
        self.assertTrue(all(result == results[0] for result in results))

    def test_broken_convoy_rebuilds_the_destination_conflict(self):
        game = TestConvoyCompilationAndResolution()._game(
            army_orders=[("A a", "A", "S1"), ("A a", "A", "b")],
            fleet_orders=[("F S1", "T", "A a")],
            enemy_orders=[("F S2", "A", "S1"), ("F S3", "S", "S1")],
            enemy_fleets=["S2", "S3"],
            transport_fleets=("S1",),
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()
        army = UnitKey("P1", "A", "a")
        self.assertIn(army, state.cancelled_orders)
        self.assertNotIn(army, state.available_convoys)
        self.assertNotIn(army, state.successful_moves)
        self.assertNotIn("b", state.resolved_conflicts)
        self.assertNotIn("b", resolver._build_resolution(state).contested_locations)


class TestCyclesAndCancellationSemantics(unittest.TestCase):
    """Cubre ciclos, diagnósticos y efectos exactos de las cancelaciones."""

    def _double_convoy_game(self, *, include_q=False):
        provinces = {
            name: Province(name, custom_id=name)
            for name in ("o1", "p1", "p2", "o2", "q")
        }
        for origin, target in (
            ("o1", "p1"),
            ("p1", "o1"),
            ("p1", "p2"),
            ("p2", "p1"),
            ("p2", "o2"),
            ("o2", "p2"),
            ("q", "p1"),
            ("p1", "q"),
        ):
            provinces[origin].sea_routes.append(Route(target))
        if include_q:
            provinces["q"].land_routes.append(Route("p1"))
        players = [
            {"player_id": "P1", "power": "M", "armies": ["o1"], "fleets": ["p1"]},
            {"player_id": "P2", "power": "V", "armies": ["o2"], "fleets": ["p2"]},
        ]
        orders = {
            "P1": [
                ("A o1", "A", "p1"),
                ("A o1", "A", "p2"),
                ("F p1", "T", "A o1"),
            ],
            "P2": [
                ("A o2", "A", "p2"),
                ("A o2", "A", "p1"),
                ("F p2", "T", "A o2"),
            ],
        }
        if include_q:
            players.append({"player_id": "P3", "power": "G", "armies": ["q"]})
            orders["P3"] = [("A q", "S", "p1 (M)")]
        return create_military_game(
            Map(provinces=provinces, seas={}),
            players,
            orders=orders,
            turn_events=[BEFORE_EVENT],
        )

    def _cancelled_order_case(self, order_type):
        if order_type == "T":
            game = create_military_game(
                convoy_map(),
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "armies": ["a"],
                        "fleets": ["S1"],
                        "rebelled_provinces": ["a"],
                    },
                    {
                        "player_id": "P2",
                        "power": "V",
                        "fleets": ["S2", "S3"],
                    },
                ],
                orders={
                    "P1": [("F S1", "T", "A a")],
                    "P2": [("F S2", "A", "S1"), ("F S3", "S", "S1")],
                },
                besieges=["a"],
            )
            return game, UnitKey("P1", "F", "S1")

        if order_type == "C":
            game = create_military_game(
                military_map(),
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "armies": ["fort"],
                        "garrisons": ["fort"],
                    }
                ],
                orders={"P1": [("G fort", "C", "A")]},
            )
            return game, UnitKey("P1", "G", "fort")

        if order_type == "A":
            game = create_military_game(
                military_map(),
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "armies": ["b"],
                        "rebelled_provinces": ["b"],
                        "rebelled_cities": ["fort"],
                    },
                    {"player_id": "P2", "power": "V", "armies": ["a", "c"]},
                ],
                orders={
                    "P1": [("A b", "A", "a")],
                    "P2": [("A a", "S", "b"), ("A c", "A", "b")],
                },
                besieges=["fort"],
            )
            return game, UnitKey("P1", "A", "b")
        elif order_type == "S":
            command = ("A b", "S", "a")
        else:
            command = (
                "A b",
                order_type,
                "fort" if order_type in {"B", "L"} else "",
            )
        player_one_orders = [command]
        enemy_armies = ["a", "c"]
        enemy_orders = [
            ("A a", "A", "b"),
            ("A c", "S", "b"),
        ]
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["b"],
                    "rebelled_provinces": ["b"],
                    "rebelled_cities": ["fort"],
                },
                {"player_id": "P2", "power": "V", "armies": enemy_armies},
            ],
            orders={
                "P1": player_one_orders,
                "P2": enemy_orders,
            },
            besieges=["fort"],
        )
        victim = UnitKey("P1", "A", "b")
        return game, victim

    def test_cancelled_orders_have_no_us4_side_effects(self):
        for order_type in ("A", "B", "H", "L", "S", "T", "C"):
            with self.subTest(order_type=order_type):
                game, victim = self._cancelled_order_case(order_type)
                before = military_snapshot(game)
                rebellions_before = [
                    (tuple(player.rebelled_provinces), tuple(player.rebelled_cities))
                    for player in game.players
                ]
                resolver = MilitaryResolver(game)
                resolver._build_unit_index()
                resolver._compile_orders()
                resolver._link_and_validate_orders()
                state = resolver._resolve_conflicts()
                resolution = resolver._build_resolution(state)
                outcome = next(
                    outcome for outcome in resolution.outcomes if outcome.unit == victim
                )
                event = resolver._event_from_resolution(resolution, state)

                self.assertIn(victim, state.cancelled_orders)
                self.assertNotIn(victim, state.active_supports)
                self.assertNotIn(victim, state.available_convoys)
                if order_type == "C":
                    self.assertIn(victim, state.cancelled_by_self_conflict)
                    self.assertNotIn(victim, state.successful_conversions)
                    self.assertFalse(outcome.dislodged)
                    self.assertEqual(outcome.final_location, victim.origin)
                else:
                    self.assertTrue(outcome.dislodged)
                self.assertEqual(outcome.final_unit_type, victim.unit_type)
                self.assertEqual(game.besieges, list(before[4]))
                self.assertEqual(
                    [
                        (
                            tuple(player.rebelled_provinces),
                            tuple(player.rebelled_cities),
                        )
                        for player in game.players
                    ],
                    rebellions_before,
                )
                self.assertEqual(event.data["rebellions"], ())
                self.assertEqual(event.data["sieges"], ())
                self.assertIn(
                    (victim.player_id, victim.unit_type, victim.origin),
                    event.data["cancelled_orders"],
                )
                if outcome.dislodged:
                    self.assertIsNone(outcome.final_location)

    def test_cycle_diagnostic_is_primitive_and_stable_across_fresh_orderings(self):
        def build_game():
            provinces = {
                name: Province(name, custom_id=name)
                for name in ("o1", "p1", "p2", "o2", "q")
            }
            for origin, target in (
                ("o1", "p1"),
                ("p1", "o1"),
                ("p1", "p2"),
                ("p2", "p1"),
                ("p2", "o2"),
                ("o2", "p2"),
                ("q", "p1"),
                ("p1", "q"),
            ):
                provinces[origin].sea_routes.append(Route(target))
            return create_military_game(
                Map(provinces=provinces, seas={}),
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "armies": ["o1"],
                        "fleets": ["p1"],
                    },
                    {
                        "player_id": "P2",
                        "power": "V",
                        "armies": ["o2"],
                        "fleets": ["p2"],
                    },
                ],
                orders={
                    "P1": [
                        ("A o1", "A", "p1"),
                        ("A o1", "A", "p2"),
                        ("F p1", "T", "A o1"),
                    ],
                    "P2": [
                        ("A o2", "A", "p2"),
                        ("A o2", "A", "p1"),
                        ("F p2", "T", "A o2"),
                    ],
                },
                turn_events=[BEFORE_EVENT],
            )

        diagnostics = []
        for _ in range(2):
            for game in iter_military_orderings(build_game):
                before = military_snapshot(game)
                with self.assertRaises(UnresolvedMilitaryConflict) as raised:
                    MilitaryResolver(game).run()
                diagnostics.append(raised.exception.diagnostic)
                self.assertEqual(military_snapshot(game), before)

        self.assertTrue(all(diagnostic == diagnostics[0] for diagnostic in diagnostics))
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic.pending_conflicts, ("p1", "p2"))
        self.assertLess(diagnostic.first_seen_iteration, diagnostic.repeated_iteration)

        def assert_primitives(value):
            if isinstance(value, tuple):
                for item in value:
                    assert_primitives(item)
            else:
                self.assertIsInstance(value, (str, int, bool, type(None)))

        assert_primitives(diagnostic.state_signature)

    def test_double_convoy_cycle_survives_sqlite_round_trips(self):
        with TemporaryDirectory() as directory:
            db_path = f"{directory}/cycle.db"
            upgrade(db_path)
            game = self._double_convoy_game()
            diagnostics = []
            conn = sqlite3.connect(db_path)
            try:
                game.save(conn)
                conn.commit()
                for _ in range(2):
                    loaded = Game.load_game(conn, game_id=game.database_id)
                    loaded.map = game.map
                    before = military_snapshot(loaded)
                    with self.assertRaises(UnresolvedMilitaryConflict) as raised:
                        MilitaryResolver(loaded).run()
                    diagnostics.append(raised.exception.diagnostic)
                    self.assertEqual(military_snapshot(loaded), before)
                    loaded.save(conn)
                    conn.commit()
            finally:
                conn.close()

        self.assertEqual(diagnostics, [diagnostics[0], diagnostics[0]])
        self.assertEqual(
            diagnostics[0].first_seen_iteration,
            0,
        )
        self.assertEqual(diagnostics[0].repeated_iteration, 2)

    def test_targeted_origin_exception_in_dependency_cycle(self):
        provinces = {
            name: Province(
                name,
                custom_id=name,
                city="fortified" if name in {"x", "y"} else None,
            )
            for name in ("o1", "p1", "p2", "o2", "a", "x", "q", "r", "y")
        }
        for origin, target in (
            ("o1", "p1"),
            ("p1", "o1"),
            ("p1", "p2"),
            ("p2", "p1"),
            ("p2", "o2"),
            ("o2", "p2"),
            ("a", "q"),
            ("q", "a"),
            ("q", "x"),
            ("x", "q"),
        ):
            provinces[origin].sea_routes.append(Route(target))
        for origin, target in (
            ("r", "y"),
            ("y", "r"),
            ("y", "p1"),
            ("p1", "y"),
            ("a", "x"),
            ("x", "a"),
        ):
            provinces[origin].land_routes.append(Route(target))
        game = create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["o1"], "fleets": ["p1"]},
                {"player_id": "P2", "power": "V", "armies": ["o2"], "fleets": ["p2"]},
                {
                    "player_id": "P3",
                    "power": "G",
                    "armies": ["a"],
                    "fleets": ["q"],
                    "garrisons": ["x"],
                },
                {"player_id": "P4", "power": "N", "garrisons": ["y"]},
                {"player_id": "P5", "power": "F", "armies": ["r"]},
            ],
            orders={
                "P1": [
                    ("A o1", "A", "p1"),
                    ("A o1", "A", "p2"),
                    ("F p1", "T", "A o1"),
                ],
                "P2": [
                    ("A o2", "A", "p2"),
                    ("A o2", "A", "p1"),
                    ("F p2", "T", "A o2"),
                ],
                "P3": [
                    ("A a", "A", "q"),
                    ("A a", "A", "x"),
                    ("F q", "T", "A a"),
                    ("G x", "S", "a (G)"),
                ],
                "P4": [("G y", "S", "p1 (M)")],
                "P5": [("A r", "A", "y")],
            },
        )

        protected = UnitKey("P3", "G", "x")
        cancelled = UnitKey("P4", "G", "y")

        class ObservingResolver(MilitaryResolver):
            """Expone los apoyos elegidos durante la ruptura del ciclo."""

            targeted_observations = []

            def _targeted_supports(self, state):
                targeted = super()._targeted_supports(state)
                self.targeted_observations.append(
                    (state.active_supports, state.cancelled_orders, frozenset(targeted))
                )
                return targeted

        before = military_snapshot(game)
        resolver = ObservingResolver(game)
        with self.assertRaises(UnresolvedMilitaryConflict) as raised:
            resolver.run()

        self.assertEqual(
            raised.exception.diagnostic.stage,
            "all-support-cancellation-exhausted",
        )
        self.assertEqual(
            resolver.targeted_observations,
            [(frozenset({protected, cancelled}), frozenset(), frozenset({cancelled}))],
        )
        self.assertEqual(military_snapshot(game), before)

    def test_two_convoys_deadlock_with_real_pending_locations(self):
        provinces = {
            name: Province(name, custom_id=name)
            for name in ("o1", "p1", "p2", "o2", "q")
        }
        for origin, target in (
            ("o1", "p1"),
            ("p1", "o1"),
            ("p1", "p2"),
            ("p2", "p1"),
            ("p2", "o2"),
            ("o2", "p2"),
            ("q", "p1"),
            ("p1", "q"),
        ):
            provinces[origin].sea_routes.append(Route(target))
        game = create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["o1"], "fleets": ["p1"]},
                {"player_id": "P2", "power": "V", "armies": ["o2"], "fleets": ["p2"]},
            ],
            orders={
                "P1": [("A o1", "A", "p1"), ("A o1", "A", "p2"), ("F p1", "T", "A o1")],
                "P2": [("A o2", "A", "p2"), ("A o2", "A", "p1"), ("F p2", "T", "A o2")],
            },
            turn_events=[BEFORE_EVENT],
        )
        before = military_snapshot(game)
        with self.assertRaises(UnresolvedMilitaryConflict) as raised:
            MilitaryResolver(game).run()
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic.stage, "all-support-cancellation-exhausted")
        self.assertEqual(diagnostic.pending_conflicts, ("p1", "p2"))
        self.assertEqual(diagnostic.first_seen_iteration, 0)
        self.assertEqual(diagnostic.repeated_iteration, 2)
        self.assertEqual(military_snapshot(game), before)

    def test_all_cancellation_leaves_the_two_convoys_deadlocked(self):
        provinces = {
            name: Province(name, custom_id=name)
            for name in ("o1", "p1", "p2", "o2", "q")
        }
        for origin, target in (
            ("o1", "p1"),
            ("p1", "o1"),
            ("p1", "p2"),
            ("p2", "p1"),
            ("p2", "o2"),
            ("o2", "p2"),
        ):
            provinces[origin].sea_routes.append(Route(target))
        provinces["q"].land_routes.append(Route("p1"))
        game = create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["o1"], "fleets": ["p1"]},
                {"player_id": "P2", "power": "V", "armies": ["o2"], "fleets": ["p2"]},
                {"player_id": "P3", "power": "G", "armies": ["q"]},
            ],
            orders={
                "P1": [("A o1", "A", "p1"), ("A o1", "A", "p2"), ("F p1", "T", "A o1")],
                "P2": [("A o2", "A", "p2"), ("A o2", "A", "p1"), ("F p2", "T", "A o2")],
                "P3": [("A q", "S", "p1 (M)")],
            },
        )

        class RecordingResolver(MilitaryResolver):
            """Registra cada cancelación de apoyos aplicada por el resolver."""

            def __init__(self, game):
                super().__init__(game)
                self.cancelled_support_groups = []

            def _cancel_supports(self, state, supporters):
                self.cancelled_support_groups.append(frozenset(supporters))
                return super()._cancel_supports(state, supporters)

        resolver = RecordingResolver(game)
        with self.assertRaises(UnresolvedMilitaryConflict) as raised:
            resolver.run()
        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic.stage, "all-support-cancellation-exhausted")
        self.assertEqual(diagnostic.pending_conflicts, ("p1", "p2"))
        self.assertEqual(diagnostic.first_seen_iteration, 2)
        self.assertEqual(diagnostic.repeated_iteration, 3)
        self.assertEqual(
            resolver.cancelled_support_groups,
            [frozenset({UnitKey("P3", "A", "q")})],
        )

    def test_targeted_cancellation_breaks_a_real_support_cycle(self):
        provinces = {
            name: Province(name, custom_id=name) for name in ("a", "b", "c", "d")
        }
        for origin, target in (
            ("a", "b"),
            ("b", "a"),
            ("b", "c"),
            ("c", "b"),
            ("d", "c"),
            ("c", "d"),
        ):
            provinces[origin].land_routes.append(Route(target))
        game = create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["c"]},
                {"player_id": "P2", "power": "V", "armies": ["b"]},
                {"player_id": "P3", "power": "G", "armies": ["a"]},
                {"player_id": "P4", "power": "F", "armies": ["d"]},
            ],
            orders={
                "P1": [("A c", "S", "b (V)")],
                "P2": [("A b", "S", "c (M)")],
                "P3": [("A a", "A", "b")],
                "P4": [("A d", "A", "c")],
            },
        )
        resolution = MilitaryResolver(game).run()
        cancelled = _event_payload(game)["cancelled_orders"]
        self.assertIn(["P1", "A", "c"], cancelled)
        self.assertIn(["P2", "A", "b"], cancelled)
        self.assertFalse(any(outcome.dislodged for outcome in resolution.outcomes))

    def test_consecutive_stable_state_needs_no_cycle_diagnostic(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "fleets": ["SEA"]}],
            orders=[("F SEA", "S", "a")],
        )
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        state = resolver._resolve_conflicts()
        self.assertEqual(state.resolved_conflicts, frozenset())

    def test_cancelled_advance_defends_without_subduing_a_rebellion(self):
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a", "b"],
                    "rebelled_provinces": ["a"],
                },
                {"player_id": "P2", "power": "V", "armies": ["c"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("A b", "H", "")],
                "P2": [("A c", "A", "a")],
            },
        )

        resolution = MilitaryResolver(game).run()
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
        self.assertEqual(outcomes[UnitKey("P1", "A", "a")].final_location, "a")
        self.assertEqual(game.players[0].rebelled_provinces, ["a"])
        event = _event_payload(game)
        self.assertIn(["P1", "A", "a"], event["cancelled_orders"])
        self.assertEqual(event["rebellions"], [])


class TestRebellions(unittest.TestCase):
    """Verifica la fuerza rebelde y sus transiciones finales."""

    @staticmethod
    def _remove_all_dislodged(resolution):
        return {
            outcome.unit: DislodgementDecision("disband", None)
            for outcome in resolution.outcomes
            if outcome.dislodged
        }

    @staticmethod
    def _event(game):
        return _event_payload(game)

    def _run(self, game):
        resolution = MilitaryResolver(game).run(self._remove_all_dislodged)
        event = self._event(game)
        expected_outcomes = [
            [
                [
                    outcome.unit.player_id,
                    outcome.unit.unit_type,
                    outcome.unit.origin,
                ],
                outcome.final_unit_type,
                outcome.final_location,
                outcome.dislodged,
                outcome.attack_origin,
            ]
            for outcome in resolution.outcomes
        ]
        expected_dislodgements = [
            [
                outcome.unit.player_id,
                outcome.unit.unit_type,
                outcome.unit.origin,
            ]
            for outcome in resolution.outcomes
            if outcome.dislodged
        ]

        def canonical(value):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        self.assertEqual(
            event["outcomes"],
            sorted(expected_outcomes, key=canonical),
        )
        self.assertEqual(
            event["dislodgements"],
            sorted(expected_dislodgements, key=canonical),
        )
        return resolution, event

    def test_provincial_and_city_rebellions_change_the_public_provincial_outcome(self):
        cases = (
            ("province", "b", {"rebelled_provinces": ["b"]}, "a"),
            ("city", "fort", {"rebelled_cities": ["fort"]}, "c"),
        )
        for label, target, rebellion, origin in cases:
            with self.subTest(label=label):
                players = [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "armies": [target],
                        **rebellion,
                    },
                    {"player_id": "P2", "power": "V", "armies": [origin]},
                ]
                orders = {
                    "P1": [(f"A {target}", "H", "")],
                    "P2": [(f"A {origin}", "A", target)],
                }
                game = create_military_game(military_map(), players, orders=orders)
                resolution, event = self._run(game)
                outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
                attacker = UnitKey("P2", "A", origin)
                controller = UnitKey("P1", "A", target)

                self.assertEqual(outcomes[attacker].final_location, target)
                self.assertFalse(outcomes[attacker].dislodged)
                self.assertIsNone(outcomes[controller].final_location)
                self.assertTrue(outcomes[controller].dislodged)
                self.assertEqual(resolution.contested_locations, frozenset({target}))
                self.assertEqual(game.players[0].rebelled_provinces, [])
                self.assertEqual(
                    game.players[0].rebelled_cities,
                    ["fort"] if label == "city" else [],
                )
                self.assertEqual(
                    event["rebellions"],
                    [["P1", "province", "b", "liberated"]]
                    if label == "province"
                    else [],
                )
                self.assertEqual(
                    military_snapshot(game)[0],
                    (("P2", target),),
                )

                neutral_game = create_military_game(
                    military_map(),
                    [
                        {"player_id": "P1", "power": "M", "armies": [target]},
                        {"player_id": "P2", "power": "V", "armies": [origin]},
                    ],
                    orders=orders,
                )
                neutral_resolution, neutral_event = self._run(neutral_game)
                neutral_outcomes = {
                    outcome.unit: outcome for outcome in neutral_resolution.outcomes
                }
                self.assertEqual(neutral_outcomes[attacker].final_location, origin)
                self.assertEqual(neutral_outcomes[controller].final_location, target)
                self.assertFalse(neutral_outcomes[attacker].dislodged)
                self.assertFalse(neutral_outcomes[controller].dislodged)
                self.assertEqual(neutral_event["dislodgements"], [])
                self.assertEqual(
                    military_snapshot(neutral_game)[0],
                    (("P1", target), ("P2", origin)),
                )

    def test_city_rebellion_does_not_create_a_city_participant(self):
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["fort"],
                    "rebelled_cities": ["fort"],
                }
            ],
            orders=[("A fort", "C", "G")],
        )

        resolution, event = self._run(game)
        outcome = resolution.outcomes[0]

        self.assertEqual(outcome.final_unit_type, "A")
        self.assertEqual(outcome.final_location, "fort")
        self.assertFalse(outcome.dislodged)
        self.assertEqual(resolution.contested_locations, frozenset())
        self.assertEqual(game.players[0].armies, ["fort"])
        self.assertEqual(game.players[0].garrisons, [])
        self.assertEqual(game.players[0].rebelled_provinces, [])
        self.assertEqual(game.players[0].rebelled_cities, ["fort"])
        self.assertEqual(event["rebellions"], [])
        self.assertEqual(event["sieges"], [])

    def test_crossing_uses_the_rebellion_at_each_units_destination(self):
        game = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "power": "M", "armies": ["a"]},
                {
                    "player_id": "P2",
                    "power": "V",
                    "armies": ["b"],
                    "rebelled_provinces": ["b"],
                },
            ],
            orders={
                "P1": [("A a", "A", "b")],
                "P2": [("A b", "A", "a")],
            },
        )

        resolution, event = self._run(game)
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}

        self.assertEqual(outcomes[UnitKey("P1", "A", "a")].final_location, "b")
        self.assertTrue(outcomes[UnitKey("P2", "A", "b")].dislodged)
        self.assertEqual(game.players[1].rebelled_provinces, [])
        self.assertEqual(game.players[1].rebelled_cities, [])
        self.assertEqual(
            event["rebellions"],
            [["P2", "province", "b", "liberated"]],
        )

    def test_rebellion_alone_does_not_create_a_conflict(self):
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "rebelled_provinces": ["b"],
                },
                {"player_id": "P2", "power": "V", "armies": ["a"]},
            ],
        )

        resolution, event = self._run(game)

        self.assertEqual(resolution.contested_locations, frozenset())
        self.assertEqual(game.players[0].rebelled_provinces, ["b"])
        self.assertEqual(game.players[0].rebelled_cities, [])
        self.assertEqual(event["rebellions"], [])

    def test_effective_holds_subdue_a_provincial_rebellion(self):
        cases = (
            ("explicit", [("A b", "H", "")]),
            ("missing", []),
            ("invalid", [("A b", "invalid", "")]),
        )
        for label, orders in cases:
            with self.subTest(label=label):
                game = create_military_game(
                    military_map(),
                    [
                        {
                            "player_id": "P1",
                            "power": "M",
                            "armies": ["b"],
                            "rebelled_provinces": ["b"],
                        }
                    ],
                    orders=orders,
                )

                _resolution, event = self._run(game)

                self.assertEqual(game.players[0].rebelled_provinces, [])
                self.assertEqual(game.players[0].rebelled_cities, [])
                self.assertEqual(
                    event["rebellions"],
                    [["P1", "province", "b", "subdued"]],
                )

    def test_successful_foreign_advance_liberates_provincial_rebellion(self):
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "rebelled_provinces": ["b"],
                },
                {"player_id": "P2", "power": "V", "armies": ["a"]},
            ],
            orders={"P2": [("A a", "A", "b")]},
        )

        _resolution, event = self._run(game)

        self.assertEqual(game.players[0].rebelled_provinces, [])
        self.assertEqual(game.players[0].rebelled_cities, [])
        self.assertEqual(
            event["rebellions"],
            [["P1", "province", "b", "liberated"]],
        )

    def test_cancelled_order_does_not_become_a_subduing_hold(self):
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a", "b"],
                    "rebelled_provinces": ["a"],
                },
                {"player_id": "P2", "power": "V", "armies": ["c"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("A b", "H", "")],
                "P2": [("A c", "A", "a")],
            },
        )

        _resolution, event = self._run(game)

        self.assertEqual(game.players[0].rebelled_provinces, ["a"])
        self.assertEqual(game.players[0].rebelled_cities, [])
        self.assertEqual(event["rebellions"], [])

    def test_already_pacified_rebellion_is_not_recreated_or_reported(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["b"]}],
        )

        _resolution, event = self._run(game)

        self.assertEqual(game.players[0].rebelled_provinces, [])
        self.assertEqual(game.players[0].rebelled_cities, [])
        self.assertEqual(event["rebellions"], [])

    def test_city_rebellion_cannot_coexist_with_any_garrison(self):
        cases = (
            (
                "owned",
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "garrisons": ["fort"],
                        "rebelled_cities": ["fort"],
                    }
                ],
                [],
            ),
            (
                "independent",
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "rebelled_cities": ["fort"],
                    }
                ],
                ["fort"],
            ),
        )
        for label, players, independent in cases:
            with self.subTest(label=label):
                game = create_military_game(
                    military_map(),
                    players,
                    independent_garrisons=independent,
                    turn_events=[BEFORE_EVENT],
                )
                before = military_snapshot(game)

                with self.assertRaises(InvalidMilitaryState):
                    MilitaryResolver(game).run(self._remove_all_dislodged)

                self.assertEqual(military_snapshot(game), before)
                self.assertEqual(game.turn_events, [BEFORE_EVENT])

    def test_fortress_rebellion_respects_the_scenario_rule(self):
        invalid = create_military_game(
            fortress_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "rebelled_cities": ["keep"],
                }
            ],
            scenario=fortress_scenario(active=False),
            turn_events=[BEFORE_EVENT],
        )
        before = military_snapshot(invalid)

        with self.assertRaises(InvalidMilitaryState):
            MilitaryResolver(invalid).run(self._remove_all_dislodged)

        self.assertEqual(military_snapshot(invalid), before)
        self.assertEqual(invalid.turn_events, [BEFORE_EVENT])

        provincial = create_military_game(
            fortress_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "rebelled_provinces": ["keep"],
                }
            ],
            scenario=fortress_scenario(active=False),
        )

        resolution, event = self._run(provincial)

        self.assertEqual(resolution.outcomes, ())
        self.assertEqual(provincial.players[0].rebelled_provinces, ["keep"])
        self.assertEqual(provincial.players[0].rebelled_cities, [])
        self.assertEqual(event["cancelled_orders"], [])
        self.assertEqual(event["broken_convoys"], [])
        self.assertEqual(event["dislodgements"], [])
        self.assertEqual(event["rebellions"], [])
        self.assertEqual(event["sieges"], [])

    def test_final_validation_rejects_inactive_fortress_city_rebellion(self):
        game = create_military_game(
            fortress_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "rebelled_cities": ["keep"],
                }
            ],
            scenario=fortress_scenario(active=False),
            turn_events=[BEFORE_EVENT],
        )

        with (
            patch.object(MilitaryResolver, "_build_rebellion_index"),
            self.assertRaises(MilitaryResolutionError),
        ):
            MilitaryResolver(game).run(self._remove_all_dislodged)

        self.assertEqual(game.turn_events, [BEFORE_EVENT, EMPTY_ORDERS_EVENT])


class TestSiegesAndRestrictedConversions(unittest.TestCase):
    """Cubre asedios y restricciones de conversión asociadas."""

    @staticmethod
    def _compiled(game):
        resolver = MilitaryResolver(game)
        resolver._build_unit_index()
        resolver._compile_orders()
        resolver._link_and_validate_orders()
        return resolver

    @staticmethod
    def _remove_all_dislodged(resolution):
        return {
            outcome.unit: DislodgementDecision("disband", None)
            for outcome in resolution.outcomes
            if outcome.dislodged
        }

    @staticmethod
    def _event(game):
        return _event_payload(game)

    def _run(self, game, dislodgement_resolver=None):
        if dislodgement_resolver is None:
            dislodgement_resolver = self._remove_all_dislodged
        resolution = MilitaryResolver(game).run(dislodgement_resolver)
        event = self._event(game)
        expected_outcomes = [
            [
                [
                    outcome.unit.player_id,
                    outcome.unit.unit_type,
                    outcome.unit.origin,
                ],
                outcome.final_unit_type,
                outcome.final_location,
                outcome.dislodged,
                outcome.attack_origin,
            ]
            for outcome in resolution.outcomes
        ]
        expected_dislodgements = [
            [
                outcome.unit.player_id,
                outcome.unit.unit_type,
                outcome.unit.origin,
            ]
            for outcome in resolution.outcomes
            if outcome.dislodged
        ]

        def canonical(value):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        self.assertEqual(
            event["outcomes"],
            sorted(expected_outcomes, key=canonical),
        )
        self.assertEqual(
            event["dislodgements"],
            sorted(expected_dislodgements, key=canonical),
        )
        return resolution, event

    def test_inactive_fortress_rejects_initial_garrison(self):
        game = create_military_game(
            fortress_map(),
            [{"player_id": "P1", "power": "M", "garrisons": ["keep"]}],
            scenario=fortress_scenario(active=False),
            turn_events=[BEFORE_EVENT],
        )
        before = military_snapshot(game)

        with self.assertRaises(InvalidMilitaryState):
            MilitaryResolver(game).run(self._remove_all_dislodged)

        self.assertEqual(military_snapshot(game), before)
        self.assertEqual(game.turn_events, [BEFORE_EVENT])

    def test_active_fortress_accepts_initial_garrison(self):
        game = create_military_game(
            fortress_map(),
            [{"player_id": "P1", "power": "M", "garrisons": ["keep"]}],
            scenario=fortress_scenario(active=True),
        )

        resolution, event = self._run(game)

        outcome = next(
            item
            for item in resolution.outcomes
            if item.unit == UnitKey("P1", "G", "keep")
        )
        self.assertEqual(outcome.final_unit_type, "G")
        self.assertEqual(outcome.final_location, "keep")
        self.assertEqual(game.players[0].garrisons, ["keep"])
        self.assertEqual(event["cancelled_orders"], [])

    def test_fortress_conversion_respects_the_scenario_rule(self):
        inactive = create_military_game(
            fortress_map(),
            [{"player_id": "P1", "power": "M", "armies": ["keep"]}],
            orders=[("A keep", "C", "G")],
            scenario=fortress_scenario(active=False),
        )
        inactive_resolver = self._compiled(inactive)
        key = UnitKey("P1", "A", "keep")
        self.assertIn(key, inactive_resolver.invalid_orders)

        active = create_military_game(
            fortress_map(),
            [{"player_id": "P1", "power": "M", "armies": ["keep"]}],
            orders=[("A keep", "C", "G")],
            scenario=fortress_scenario(active=True),
        )
        active_resolver = self._compiled(active)
        self.assertNotIn(key, active_resolver.invalid_orders)
        self.assertEqual(active_resolver.orders_by_unit[key].order_type, "C")

    def test_first_and_second_besiege_start_and_complete_against_garrison(self):
        first = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
            orders=[("A fort", "B", None)],
            independent_garrisons=["fort"],
        )
        first_resolution, first_event = self._run(first)
        self.assertEqual(first.besieges, ["fort"])
        self.assertEqual(first.independent_garrisons, ["fort"])
        self.assertFalse(
            any(outcome.dislodged for outcome in first_resolution.outcomes)
        )
        self.assertEqual(first_event["cancelled_orders"], [])
        self.assertEqual(first_event["broken_convoys"], [])
        self.assertEqual(first_event["dislodgements"], [])
        self.assertEqual(first_event["rebellions"], [])
        self.assertEqual(
            first_event["sieges"],
            [[["P1", "A", "fort"], "fort", "started"]],
        )

        second = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
            orders=[("A fort", "B", None)],
            independent_garrisons=["fort"],
            besieges=["fort"],
        )
        second_resolution, second_event = self._run(second)
        destroyed = next(
            outcome
            for outcome in second_resolution.outcomes
            if outcome.unit == UnitKey(None, "G", "fort")
        )
        self.assertTrue(destroyed.dislodged)
        self.assertIsNone(destroyed.final_location)
        self.assertEqual(second.besieges, [])
        self.assertEqual(second.independent_garrisons, [])
        self.assertEqual(second_event["cancelled_orders"], [])
        self.assertEqual(second_event["broken_convoys"], [])
        self.assertEqual(second_event["dislodgements"], [[None, "G", "fort"]])
        self.assertEqual(second_event["rebellions"], [])
        self.assertEqual(
            second_event["sieges"],
            [[["P1", "A", "fort"], "fort", "completed"]],
        )
        self.assertEqual(
            military_snapshot(second),
            (
                (("P1", "fort"),),
                (),
                (),
                (),
                (),
                (),
                (second.turn_events[-1],),
            ),
        )

    def test_completed_siege_without_manager_is_atomic(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
            orders=[("A fort", "B", None)],
            independent_garrisons=["fort"],
            besieges=["fort"],
            turn_events=[BEFORE_EVENT],
        )
        before = military_snapshot(game)

        with self.assertRaises(DislodgementResolverRequired):
            MilitaryResolver(game).run()

        self.assertEqual(military_snapshot(game), before)
        self.assertEqual(game.turn_events[:-1], [BEFORE_EVENT])
        self.assertIs(game.turn_events[-1].type, EventType.MILITARY_ORDERS_SUMMARY)

    def test_destroyed_garrison_hold_does_not_subdue_provincial_rebellion(self):
        cases = (
            ("explicit", [("G fort", "H", "")]),
            ("missing", []),
            ("invalid", [("G fort", "invalid", "")]),
        )
        for label, garrison_orders in cases:
            with self.subTest(label=label):
                game = create_military_game(
                    military_map(),
                    [
                        {
                            "player_id": "P1",
                            "power": "M",
                            "garrisons": ["fort"],
                            "rebelled_provinces": ["fort"],
                        },
                        {"player_id": "P2", "power": "V", "armies": ["fort"]},
                    ],
                    orders={
                        "P1": garrison_orders,
                        "P2": [("A fort", "B", None)],
                    },
                    besieges=["fort"],
                )

                resolution, event = self._run(game)
                destroyed = next(
                    outcome
                    for outcome in resolution.outcomes
                    if outcome.unit == UnitKey("P1", "G", "fort")
                )

                self.assertIsNone(destroyed.final_location)
                self.assertTrue(destroyed.dislodged)
                self.assertEqual(event["cancelled_orders"], [])
                self.assertEqual(event["broken_convoys"], [])
                self.assertEqual(event["dislodgements"], [["P1", "G", "fort"]])
                self.assertEqual(event["rebellions"], [])
                self.assertEqual(
                    event["sieges"],
                    [[["P2", "A", "fort"], "fort", "completed"]],
                )
                self.assertEqual(
                    military_snapshot(game),
                    (
                        (("P2", "fort"),),
                        (),
                        (),
                        (),
                        (),
                        (("P1", "province", "fort"),),
                        (game.turn_events[-1],),
                    ),
                )

    def test_completed_siege_removes_owned_garrison_consistently(self):
        game = create_military_game(
            military_map(),
            [
                {"player_id": "P1", "power": "M", "armies": ["fort"]},
                {"player_id": "P2", "power": "V", "garrisons": ["fort"]},
            ],
            orders={"P1": [("A fort", "B", None)]},
            besieges=["fort"],
        )

        resolution, event = self._run(game)
        destroyed = next(
            outcome
            for outcome in resolution.outcomes
            if outcome.unit == UnitKey("P2", "G", "fort")
        )

        self.assertTrue(destroyed.dislodged)
        self.assertIsNone(destroyed.final_location)
        self.assertEqual(game.players[1].garrisons, [])
        self.assertEqual(game.independent_garrisons, [])
        self.assertEqual(game.besieges, [])
        self.assertEqual(event["cancelled_orders"], [])
        self.assertEqual(event["broken_convoys"], [])
        self.assertEqual(event["dislodgements"], [["P2", "G", "fort"]])
        self.assertEqual(event["rebellions"], [])
        self.assertEqual(
            event["sieges"],
            [[["P1", "A", "fort"], "fort", "completed"]],
        )
        self.assertEqual(
            military_snapshot(game),
            (
                (("P1", "fort"),),
                (),
                (),
                (),
                (),
                (),
                (game.turn_events[-1],),
            ),
        )

    def test_completed_siege_subdues_city_rebellion(self):
        game = create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["fort"],
                    "rebelled_cities": ["fort"],
                }
            ],
            orders=[("A fort", "B", None)],
            besieges=["fort"],
        )

        resolution, event = self._run(game)

        self.assertFalse(any(outcome.dislodged for outcome in resolution.outcomes))
        self.assertEqual(game.players[0].rebelled_provinces, [])
        self.assertEqual(game.players[0].rebelled_cities, [])
        self.assertEqual(event["dislodgements"], [])
        self.assertEqual(
            event["rebellions"],
            [["P1", "city", "fort", "subdued"]],
        )
        self.assertEqual(
            event["sieges"],
            [[["P1", "A", "fort"], "fort", "completed"]],
        )

    def test_lift_siege_keeps_target(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
            orders=[("A fort", "L", None)],
            independent_garrisons=["fort"],
            besieges=["fort"],
        )

        resolution, event = self._run(game)

        self.assertFalse(any(outcome.dislodged for outcome in resolution.outcomes))
        self.assertEqual(game.besieges, [])
        self.assertEqual(game.independent_garrisons, ["fort"])
        self.assertEqual(event["dislodgements"], [])
        self.assertEqual(
            event["sieges"],
            [[["P1", "A", "fort"], "fort", "lifted"]],
        )

    def test_dislodged_besieger_lifts_siege_without_removing_target(self):
        provinces = {
            "fort": Province("fort", custom_id="fort", city="fortified"),
            "att": Province("att", custom_id="att"),
            "sup": Province("sup", custom_id="sup"),
        }
        for origin, target in (
            ("fort", "att"),
            ("att", "fort"),
            ("fort", "sup"),
            ("sup", "fort"),
        ):
            provinces[origin].land_routes.append(Route(target))
        game = create_military_game(
            Map(provinces=provinces, seas={}),
            [
                {"player_id": "P1", "power": "M", "armies": ["fort"]},
                {"player_id": "P2", "power": "V", "armies": ["att", "sup"]},
            ],
            orders={
                "P1": [("A fort", "H", "")],
                "P2": [("A att", "A", "fort"), ("A sup", "S", "fort")],
            },
            independent_garrisons=["fort"],
            besieges=["fort"],
        )
        before = military_snapshot(game)
        dislodgement_resolver = Mock(side_effect=self._remove_all_dislodged)
        resolution, event = self._run(game, dislodgement_resolver)
        dislodgement_resolver.assert_called_once_with(resolution)
        besieger = next(
            outcome
            for outcome in resolution.outcomes
            if outcome.unit == UnitKey("P1", "A", "fort")
        )

        self.assertTrue(besieger.dislodged)
        self.assertIsNone(besieger.final_location)
        self.assertEqual(game.besieges, [])
        self.assertEqual(game.independent_garrisons, ["fort"])
        self.assertEqual(game.players[0].armies, [])
        self.assertEqual(game.players[1].armies, ["fort", "sup"])
        self.assertEqual(event["dislodgements"], [["P1", "A", "fort"]])
        self.assertEqual(
            event["sieges"],
            [[["P1", "A", "fort"], "fort", "lifted"]],
        )
        self.assertNotEqual(military_snapshot(game), before)

    def test_fortress_besiege_respects_the_scenario_rule(self):
        invalid = create_military_game(
            fortress_map(),
            [{"player_id": "P1", "power": "M", "armies": ["keep"]}],
            orders=[("A keep", "B", None)],
            scenario=fortress_scenario(active=False),
        )
        resolver = self._compiled(invalid)
        key = UnitKey("P1", "A", "keep")

        self.assertEqual(resolver.orders_by_unit[key].order_type, "H")
        self.assertIn(key, resolver.invalid_orders)

        persistent = create_military_game(
            fortress_map(),
            [{"player_id": "P1", "power": "M", "armies": ["keep"]}],
            orders=[("A keep", "H", "")],
            besieges=["keep"],
            scenario=fortress_scenario(active=False),
            turn_events=[BEFORE_EVENT],
        )
        before = military_snapshot(persistent)

        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(persistent).run()

        self.assertEqual(military_snapshot(persistent), before)
        self.assertEqual(persistent.turn_events[:-1], [BEFORE_EVENT])
        self.assertIs(
            persistent.turn_events[-1].type, EventType.MILITARY_ORDERS_SUMMARY
        )

    def test_active_fortress_allows_exact_city_rebellion_siege_cycle(self):
        first = create_military_game(
            fortress_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["keep"],
                    "rebelled_cities": ["keep"],
                }
            ],
            orders=[("A keep", "B", None)],
            scenario=fortress_scenario(active=True),
        )
        first_compiled = self._compiled(first)
        key = UnitKey("P1", "A", "keep")
        self.assertEqual(first_compiled.orders_by_unit[key].order_type, "B")
        self.assertNotIn(key, first_compiled.invalid_orders)

        first_resolution, first_event = self._run(first)
        first_outcome = next(
            outcome for outcome in first_resolution.outcomes if outcome.unit == key
        )

        self.assertEqual(first_outcome.final_location, "keep")
        self.assertFalse(first_outcome.dislodged)
        self.assertEqual(first_event["cancelled_orders"], [])
        self.assertEqual(first_event["broken_convoys"], [])
        self.assertEqual(first_event["dislodgements"], [])
        self.assertEqual(first_event["rebellions"], [])
        self.assertEqual(
            first_event["sieges"],
            [[["P1", "A", "keep"], "keep", "started"]],
        )
        self.assertEqual(
            military_snapshot(first),
            (
                (("P1", "keep"),),
                (),
                (),
                (),
                ("keep",),
                (("P1", "city", "keep"),),
                (first.turn_events[-1],),
            ),
        )

        second = create_military_game(
            fortress_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["keep"],
                    "rebelled_cities": ["keep"],
                }
            ],
            orders=[("A keep", "B", None)],
            besieges=["keep"],
            scenario=fortress_scenario(active=True),
        )
        second_compiled = self._compiled(second)
        self.assertEqual(second_compiled.orders_by_unit[key].order_type, "B")
        self.assertNotIn(key, second_compiled.invalid_orders)

        second_resolution, second_event = self._run(second)
        second_outcome = next(
            outcome for outcome in second_resolution.outcomes if outcome.unit == key
        )

        self.assertEqual(second_outcome.final_location, "keep")
        self.assertFalse(second_outcome.dislodged)
        self.assertEqual(second_event["cancelled_orders"], [])
        self.assertEqual(second_event["broken_convoys"], [])
        self.assertEqual(second_event["dislodgements"], [])
        self.assertEqual(
            second_event["rebellions"],
            [["P1", "city", "keep", "subdued"]],
        )
        self.assertEqual(
            second_event["sieges"],
            [[["P1", "A", "keep"], "keep", "completed"]],
        )
        self.assertEqual(
            military_snapshot(second),
            (
                (("P1", "keep"),),
                (),
                (),
                (),
                (),
                (),
                (second.turn_events[-1],),
            ),
        )

    def test_fleet_requires_port_to_besiege(self):
        port_game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "fleets": ["fort"]}],
            orders=[("F fort", "B", None)],
            independent_garrisons=["fort"],
        )
        port_resolver = self._compiled(port_game)
        self.assertEqual(
            port_resolver.orders_by_unit[UnitKey("P1", "F", "fort")].order_type,
            "B",
        )

        inland = Map(
            provinces={
                "fort": Province(
                    "fort", custom_id="fort", city="fortified", has_port=False
                )
            },
            seas={},
        )
        inland_game = create_military_game(
            inland,
            [{"player_id": "P1", "power": "M", "fleets": ["fort"]}],
            orders=[("F fort", "B", None)],
            independent_garrisons=["fort"],
        )
        inland_resolver = self._compiled(inland_game)
        key = UnitKey("P1", "F", "fort")
        self.assertEqual(inland_resolver.orders_by_unit[key].order_type, "H")
        self.assertIn(key, inland_resolver.invalid_orders)

    def test_siege_restricts_besieger_and_garrison_orders(self):
        besieger_cases = (
            ("B", None, "B"),
            ("H", "", "H"),
            ("L", None, "L"),
            ("A", "c", "H"),
            ("S", "fort", "H"),
            ("C", "G", "H"),
        )
        for command, target, expected in besieger_cases:
            with self.subTest(actor="besieger", command=command):
                game = create_military_game(
                    military_map(),
                    [
                        {"player_id": "P1", "power": "M", "armies": ["fort"]},
                        {"player_id": "P2", "power": "V", "garrisons": ["fort"]},
                    ],
                    orders={"P1": [("A fort", command, target)]},
                    besieges=["fort"],
                )
                resolver = self._compiled(game)
                self.assertEqual(
                    resolver.orders_by_unit[UnitKey("P1", "A", "fort")].order_type,
                    expected,
                )

        garrison_cases = (
            ("H", "", "H"),
            ("S", "fort", "S"),
            ("C", "A", "H"),
            ("B", None, "H"),
            ("L", None, "H"),
        )
        for command, target, expected in garrison_cases:
            with self.subTest(actor="garrison", command=command):
                game = create_military_game(
                    military_map(),
                    [
                        {"player_id": "P1", "power": "M", "armies": ["fort"]},
                        {"player_id": "P2", "power": "V", "garrisons": ["fort"]},
                    ],
                    orders={"P2": [("G fort", command, target)]},
                    besieges=["fort"],
                )
                resolver = self._compiled(game)
                self.assertEqual(
                    resolver.orders_by_unit[UnitKey("P2", "G", "fort")].order_type,
                    expected,
                )

    def test_convert_is_invalid_under_siege_or_into_rebelled_city(self):
        cases = (
            (
                "campaign under siege",
                [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
                {"P1": [("A fort", "C", "G")]},
                ["fort"],
                UnitKey("P1", "A", "fort"),
            ),
            (
                "garrison under siege",
                [
                    {"player_id": "P1", "power": "M", "armies": ["fort"]},
                    {"player_id": "P2", "power": "V", "garrisons": ["fort"]},
                ],
                {"P2": [("G fort", "C", "A")]},
                ["fort"],
                UnitKey("P2", "G", "fort"),
            ),
            (
                "rebelled city",
                [
                    {
                        "player_id": "P1",
                        "power": "M",
                        "armies": ["fort"],
                        "rebelled_cities": ["fort"],
                    }
                ],
                {"P1": [("A fort", "C", "G")]},
                [],
                UnitKey("P1", "A", "fort"),
            ),
        )
        for label, players, orders, besieges, key in cases:
            with self.subTest(label=label):
                resolver = self._compiled(
                    create_military_game(
                        military_map(), players, orders=orders, besieges=besieges
                    )
                )
                self.assertEqual(resolver.orders_by_unit[key].order_type, "H")
                self.assertIn(key, resolver.invalid_orders)

        open_game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
            orders=[("A fort", "C", "G")],
        )
        open_resolver = self._compiled(open_game)
        self.assertEqual(
            open_resolver.orders_by_unit[UnitKey("P1", "A", "fort")].order_type,
            "C",
        )


class TestDislodgementContract(unittest.TestCase):
    """Valida el contrato externo de retiradas y eliminaciones."""

    @staticmethod
    def _single_dislodgement_game(*, turn_events=(BEFORE_EVENT,)):
        return create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a"],
                    "fleets": ["SEA"],
                },
                {"player_id": "P2", "power": "V", "armies": ["b"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("F SEA", "S", "b")],
                "P2": [("A b", "H", "")],
            },
            turn_events=turn_events,
        )

    @staticmethod
    def _two_dislodgement_game(*, turn_events=(BEFORE_EVENT,)):
        return create_military_game(
            military_map(),
            [
                {
                    "player_id": "P1",
                    "power": "M",
                    "armies": ["a"],
                    "fleets": ["SEA"],
                },
                {"player_id": "P2", "power": "V", "armies": ["b"]},
                {
                    "player_id": "P3",
                    "power": "F",
                    "armies": ["c"],
                    "garrisons": ["fort"],
                },
                {"player_id": "P4", "power": "T", "armies": ["fort"]},
            ],
            orders={
                "P1": [("A a", "A", "b"), ("F SEA", "S", "b")],
                "P2": [("A b", "H", "")],
                "P3": [("A c", "A", "fort"), ("G fort", "S", "fort")],
                "P4": [("A fort", "H", "")],
            },
            turn_events=turn_events,
        )

    @staticmethod
    def _completed_independent_siege(*, turn_events=(BEFORE_EVENT,)):
        return create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["fort"]}],
            orders=[("A fort", "B", None)],
            independent_garrisons=["fort"],
            besieges=["fort"],
            turn_events=turn_events,
        )

    def test_manager_is_not_called_without_dislodgements(self):
        game = create_military_game(
            military_map(),
            [{"player_id": "P1", "power": "M", "armies": ["a"]}],
            turn_events=[BEFORE_EVENT],
        )
        manager = Mock()

        resolution = MilitaryResolver(game).run(manager)

        manager.assert_not_called()
        self.assertFalse(any(outcome.dislodged for outcome in resolution.outcomes))
        self.assertEqual(game.players[0].armies, ["a"])
        self.assertEqual(len(game.turn_events), 3)
        self.assertIs(game.turn_events[-2].type, EventType.MILITARY_ORDERS_SUMMARY)

    def test_missing_manager_aborts_without_pending_collection(self):
        game = self._single_dislodgement_game()
        before = military_snapshot(game)

        with self.assertRaises(DislodgementResolverRequired):
            MilitaryResolver(game).run()

        self.assertEqual(military_snapshot(game), before)
        self.assertFalse(hasattr(game, "pending_dislodgements"))

    def test_manager_errors_and_non_exact_mappings_are_atomic(self):
        dislodged = UnitKey("P2", "A", "b")
        attacker = UnitKey("P1", "A", "a")
        external_error = RuntimeError("external failure")
        military_error = MilitaryResolutionError("typed failure")
        cases = (
            ("raises", Mock(side_effect=external_error), MilitaryResolutionError),
            ("incomplete", Mock(return_value={}), MilitaryResolutionError),
            (
                "extra key",
                Mock(return_value={dislodged: None, attacker: None}),
                MilitaryResolutionError,
            ),
            ("typed error", Mock(side_effect=military_error), MilitaryResolutionError),
        )
        for label, manager, expected_error in cases:
            with self.subTest(label=label):
                game = self._single_dislodgement_game()
                before = military_snapshot(game)

                with (
                    patch.object(
                        MilitaryResolver,
                        "_event_from_military_orders",
                        return_value=EMPTY_ORDERS_EVENT,
                    ),
                    self.assertRaises(expected_error) as caught,
                ):
                    MilitaryResolver(game).run(manager)

                manager.assert_called_once()
                self.assertEqual(military_snapshot(game), before)
                if label == "raises":
                    self.assertIs(caught.exception.__cause__, external_error)
                if label == "typed error":
                    self.assertIs(caught.exception, military_error)

    def test_mapping_materialization_error_is_wrapped_atomically(self):
        game = self._single_dislodgement_game()
        before = military_snapshot(game)
        external_error = RuntimeError("mapping iteration failed")

        class ExplodingMapping(Mapping):
            """Simula un mapping que falla al materializar sus decisiones."""

            def __getitem__(self, key):
                raise external_error

            def __iter__(self):
                raise external_error

            def __len__(self):
                return 1

        manager = Mock(return_value=ExplodingMapping())

        with self.assertRaises(MilitaryResolutionError) as caught:
            MilitaryResolver(game).run(manager)

        manager.assert_called_once()
        self.assertIs(caught.exception.__cause__, external_error)
        self.assertEqual(military_snapshot(game), before)
        self.assertEqual(game.turn_events[:-1], [BEFORE_EVENT])
        self.assertIs(game.turn_events[-1].type, EventType.MILITARY_ORDERS_SUMMARY)

    def test_contested_destination_aborts_atomically(self):
        game = self._single_dislodgement_game()
        before = military_snapshot(game)
        dislodged = UnitKey("P2", "A", "b")
        manager = Mock(return_value={dislodged: DislodgementDecision("retreat", "b")})

        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(game).run(manager)

        manager.assert_called_once()
        resolution = manager.call_args.args[0]
        self.assertEqual(resolution.contested_locations, frozenset({"b"}))
        self.assertEqual(military_snapshot(game), before)

    def test_attack_origin_cannot_be_selected_as_a_retreat(self):
        game = self._single_dislodgement_game()
        before = military_snapshot(game)
        dislodged = UnitKey("P2", "A", "b")
        manager = Mock(return_value={dislodged: DislodgementDecision("retreat", "a")})

        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(game).run(manager)

        resolution = manager.call_args.args[0]
        outcome = next(item for item in resolution.outcomes if item.unit == dislodged)
        self.assertEqual(outcome.attack_origin, "a")
        self.assertEqual(military_snapshot(game), before)

    def test_two_retreats_cannot_share_a_destination(self):
        game = self._two_dislodgement_game()
        before = military_snapshot(game)
        decisions = {
            UnitKey("P2", "A", "b"): DislodgementDecision("retreat", "c"),
            UnitKey("P4", "A", "fort"): DislodgementDecision("retreat", "c"),
        }
        manager = Mock(return_value=decisions)

        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(game).run(manager)

        manager.assert_called_once()
        self.assertEqual(military_snapshot(game), before)

    def test_elimination_and_valid_retreat_commit_after_one_manager_call(self):
        cases = (
            ("elimination", DislodgementDecision("disband", None), []),
            ("retreat", DislodgementDecision("retreat", "c"), ["c"]),
        )
        for label, destination, expected_armies in cases:
            with self.subTest(label=label):
                game = self._single_dislodgement_game()
                before = military_snapshot(game)
                dislodged = UnitKey("P2", "A", "b")
                manager = Mock(return_value={dislodged: destination})

                with patch.object(
                    MilitaryResolver,
                    "_event_from_military_orders",
                    return_value=EMPTY_ORDERS_EVENT,
                ):
                    resolution = MilitaryResolver(game).run(manager)

                manager.assert_called_once_with(resolution)
                outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
                self.assertTrue(outcomes[dislodged].dislodged)
                self.assertIsNone(outcomes[dislodged].final_location)
                self.assertEqual(resolution.contested_locations, frozenset({"b"}))
                self.assertEqual(game.players[0].armies, ["b"])
                self.assertEqual(game.players[1].armies, expected_armies)
                self.assertNotEqual(military_snapshot(game), before)
                event = _event_payload(game)
                self.assertEqual(event["dislodgements"], [["P2", "A", "b"]])

    def test_fleet_retreat_to_inland_province_is_rejected_atomically(self):
        provinces = {
            "inland": Province("inland", custom_id="inland", has_port=False),
        }
        seas = {name: Sea(name) for name in ("X", "Y", "Z")}
        for name, sea in seas.items():
            sea.id = name
        for origin, destination in (
            ("X", "Y"),
            ("Y", "X"),
            ("Z", "Y"),
            ("Y", "Z"),
        ):
            seas[origin].sea_routes.append(Route(destination))
        game = create_military_game(
            Map(provinces=provinces, seas=seas),
            [
                {"player_id": "P1", "power": "M", "fleets": ["X", "Z"]},
                {"player_id": "P2", "power": "V", "fleets": ["Y"]},
            ],
            orders={
                "P1": [("F X", "A", "Y"), ("F Z", "S", "Y")],
                "P2": [("F Y", "H", "")],
            },
            turn_events=[BEFORE_EVENT],
        )
        before = military_snapshot(game)
        dislodged = UnitKey("P2", "F", "Y")
        manager = Mock(
            return_value={dislodged: DislodgementDecision("retreat", "inland")}
        )

        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(game).run(manager)

        manager.assert_called_once()
        self.assertEqual(military_snapshot(game), before)
        self.assertEqual(game.turn_events[:-1], [BEFORE_EVENT])
        self.assertIs(game.turn_events[-1].type, EventType.MILITARY_ORDERS_SUMMARY)

    def test_independent_garrison_requires_an_explicit_decision(self):
        independent = UnitKey(None, "G", "fort")

        no_manager = self._completed_independent_siege()
        no_manager_before = military_snapshot(no_manager)
        with self.assertRaises(DislodgementResolverRequired):
            MilitaryResolver(no_manager).run()
        self.assertEqual(military_snapshot(no_manager), no_manager_before)
        self.assertFalse(hasattr(no_manager, "pending_dislodgements"))

        incomplete = self._completed_independent_siege()
        incomplete_before = military_snapshot(incomplete)
        incomplete_manager = Mock(return_value={})
        with self.assertRaises(MilitaryResolutionError):
            MilitaryResolver(incomplete).run(incomplete_manager)
        incomplete_manager.assert_called_once()
        self.assertEqual(military_snapshot(incomplete), incomplete_before)

        completed = self._completed_independent_siege()
        manager = Mock(
            return_value={independent: DislodgementDecision("disband", None)}
        )
        resolution = MilitaryResolver(completed).run(manager)
        manager.assert_called_once_with(resolution)
        self.assertEqual(completed.independent_garrisons, [])
        self.assertEqual(completed.besieges, [])
        event = _event_payload(completed)
        self.assertEqual(event["dislodgements"], [[None, "G", "fort"]])


REPRESENTATIVE_DISLODGEMENT_DECISIONS: Mapping[UnitKey, DislodgementDecision] = (
    MappingProxyType(
        {
            UnitKey("P2", "A", "cross00b"): DislodgementDecision("retreat", "retreat"),
            UnitKey("P2", "A", "cross01b"): DislodgementDecision("disband", None),
        }
    )
)


def representative_dislodgement_resolver(
    resolution: MilitaryResolution,
) -> Mapping[UnitKey, DislodgementDecision]:
    """Devuelve las dos decisiones fijas de la carga representativa."""
    dislodged = frozenset(
        outcome.unit for outcome in resolution.outcomes if outcome.dislodged
    )
    expected = frozenset(REPRESENTATIVE_DISLODGEMENT_DECISIONS)
    if dislodged != expected:
        raise AssertionError(
            f"Desalojos representativos inesperados: {sorted(dislodged, key=str)}"
        )
    return REPRESENTATIVE_DISLODGEMENT_DECISIONS


def build_representative_game() -> Game:
    """Construye la carga normativa de 30 unidades y 60 filas de orden."""
    crossing_pairs = tuple(
        (f"cross{index:02d}a", f"cross{index:02d}b") for index in range(10)
    )
    province_names = {location for pair in crossing_pairs for location in pair} | {
        "convoy-origin",
        "convoy-destination",
        "hold0",
        "hold1",
        "retreat",
        "support0",
        "support1",
    }
    provinces = {
        name: Province(name, custom_id=name, has_port=name.startswith("convoy-"))
        for name in province_names
    }
    for origin, destination in crossing_pairs:
        provinces[origin].land_routes.append(Route(destination))
        provinces[destination].land_routes.append(Route(origin))
    provinces["support0"].land_routes.append(Route("cross00b"))
    provinces["support1"].land_routes.append(Route("cross01b"))

    seas = {name: Sea(name) for name in ("C1", "C2", "C3", "C4", "C5")}
    for name, sea in seas.items():
        sea.id = name
    provinces["convoy-origin"].sea_routes.append(Route("C1"))
    for origin, destination in (
        ("C1", "C2"),
        ("C2", "C3"),
        ("C3", "C4"),
        ("C4", "C5"),
        ("C5", "C1"),
        ("C5", "convoy-destination"),
    ):
        seas[origin].sea_routes.append(Route(destination))

    convoy_targets = tuple(f"C{index % 5 + 1}" for index in range(30)) + (
        "convoy-destination",
    )
    p1_armies = [origin for origin, _destination in crossing_pairs] + [
        "support0",
        "support1",
    ]
    p2_armies = [destination for _origin, destination in crossing_pairs]
    players = [
        {"player_id": "P1", "power": "M", "armies": p1_armies},
        {"player_id": "P2", "power": "V", "armies": p2_armies},
        {
            "player_id": "P3",
            "power": "F",
            "armies": ["convoy-origin"],
            "fleets": list(seas),
        },
        {"player_id": "P4", "power": "T", "armies": ["hold0", "hold1"]},
    ]
    orders = {
        "P1": [
            (f"A {origin}", "A", destination) for origin, destination in crossing_pairs
        ]
        + [
            ("A support0", "S", "cross00b"),
            ("A support1", "S", "cross01b"),
        ],
        "P2": [
            (f"A {destination}", "A", origin) for origin, destination in crossing_pairs
        ],
        "P3": [("A convoy-origin", "A", target) for target in convoy_targets]
        + [(f"F {sea}", "T", "A convoy-origin") for sea in seas],
        "P4": [("A hold0", "H", ""), ("A hold1", "H", "")],
    }
    return create_military_game(
        Map(provinces=provinces, seas=seas),
        players,
        orders=orders,
        turn_events=[BEFORE_EVENT],
        name="representative-military-performance",
    )


def _military_unit_count(game: Game) -> int:
    """Cuenta todas las unidades controladas e independientes del snapshot."""
    return sum(
        len(player.armies) + len(player.fleets) + len(player.garrisons)
        for player in game.players
    ) + len(game.independent_garrisons)


def _military_event_payload(game: Game) -> dict[str, list[object]]:
    """Extrae las seis listas primitivas del último evento militar."""
    return _event_payload(game)


def _resolve_and_capture(original_resolve, resolved_states):
    """Ejecuta la resolución interna y conserva el estado para su firma."""
    state = original_resolve()
    resolved_states.append(state)
    return state


def _record_manager_snapshot(
    resolution,
    *,
    game,
    observations,
    decisions,
):
    """Registra el snapshot visible al gestor y devuelve decisiones inmutables."""
    observations.append((resolution, military_snapshot(game)))
    return decisions


class TestRepresentativeMilitaryResolution(unittest.TestCase):
    """Valida determinismo y presupuesto de la carga normativa."""

    def test_representative_resolution_determinism(self):
        observations = []
        expected_conflicts = frozenset(
            f"cross{index:02d}{side}" for index in range(10) for side in ("a", "b")
        )

        for _iteration in range(5):
            game = build_representative_game()
            self.assertEqual(_military_unit_count(game), 30)
            self.assertEqual(sum(len(player.commands) for player in game.players), 60)

            resolver = MilitaryResolver(game)
            resolved_states = []
            capture_state = partial(
                _resolve_and_capture,
                resolver._resolve_conflicts,
                resolved_states,
            )

            manager = Mock(side_effect=representative_dislodgement_resolver)
            with patch.object(
                resolver,
                "_resolve_conflicts",
                side_effect=capture_state,
            ):
                resolution = resolver.run(manager)

            manager.assert_called_once_with(resolution)
            self.assertEqual(len(resolved_states), 1)
            signature = resolver._state_signature(resolved_states[0])
            dislodged = {
                outcome.unit for outcome in resolution.outcomes if outcome.dislodged
            }
            convoy = resolver.orders_by_unit[UnitKey("P3", "A", "convoy-origin")]

            self.assertEqual(dislodged, set(REPRESENTATIVE_DISLODGEMENT_DECISIONS))
            self.assertEqual(resolution.contested_locations, expected_conflicts)
            self.assertEqual(len(convoy.transporters), 5)
            self.assertEqual(convoy.path[-1], "convoy-destination")
            self.assertEqual(len(convoy.path), 32)
            p2 = next(player for player in game.players if player.player_id == "P2")
            self.assertIn("retreat", p2.armies)
            self.assertNotIn("cross01b", p2.armies)

            observations.append(
                (
                    signature,
                    resolution,
                    game.turn_events[-1],
                    military_snapshot(game),
                )
            )

        self.assertTrue(
            all(observation == observations[0] for observation in observations)
        )

    @unittest.skipUnless(
        os.getenv("MACHIAVELLI_REFERENCE_PERF") == "1",
        "Solo se ejecuta en el job de rendimiento de referencia",
    )
    def test_representative_resolution_budget(self):
        runs = [
            (
                MilitaryResolver(build_representative_game()),
                representative_dislodgement_resolver,
            )
            for _iteration in range(5)
        ]
        durations = []

        for resolver, manager in runs:
            started = perf_counter()
            resolver.run(manager)
            duration = perf_counter() - started
            durations.append(duration)
            details = (
                f"duration={duration:.6f}s; max={max(durations):.6f}s; "
                f"python={platform.python_version()}; "
                f"platform={platform.platform()}; "
                f"machine={platform.machine()}; cpu_count={os.cpu_count()}"
            )
            self.assertLess(duration, 1.0, details)


def _build_integrated_acceptance_game() -> Game:
    """Crea una campaña compacta que atraviesa todas las reglas integradas."""
    provinces = {
        name: Province(
            name,
            custom_id=name,
            city="fortified" if name in {"city", "fort"} else None,
            has_port=name in {"convoy-origin", "convoy-destination"},
        )
        for name in (
            "a",
            "b",
            "c",
            "city",
            "convoy-destination",
            "convoy-origin",
            "d",
            "e",
            "fort",
            "r",
            "reb",
        )
    }
    for origin, destination in (
        ("a", "b"),
        ("c", "b"),
        ("d", "c"),
        ("e", "c"),
    ):
        provinces[origin].land_routes.append(Route(destination))
    sea = Sea("S")
    sea.id = "S"
    provinces["convoy-origin"].sea_routes.append(Route("S"))
    sea.sea_routes.append(Route("convoy-destination"))

    return create_military_game(
        Map(provinces=provinces, seas={"S": sea}),
        [
            {
                "player_id": "P1",
                "power": "M",
                "armies": ["a", "c", "reb"],
                "rebelled_provinces": ["reb"],
            },
            {"player_id": "P2", "power": "V", "armies": ["b", "d", "e"]},
            {
                "player_id": "P3",
                "power": "F",
                "armies": ["city", "convoy-origin"],
                "fleets": ["S"],
            },
            {"player_id": "P4", "power": "T", "armies": ["fort"]},
        ],
        orders={
            "P1": [
                ("A a", "A", "b"),
                ("A c", "S", "b"),
                ("A reb", "H", ""),
            ],
            "P2": [
                ("A b", "H", ""),
                ("A d", "A", "c"),
                ("A e", "S", "c"),
            ],
            "P3": [
                ("A convoy-origin", "A", "S"),
                ("A convoy-origin", "A", "convoy-destination"),
                ("F S", "T", "A convoy-origin"),
                ("A city", "C", "G"),
            ],
            "P4": [("A fort", "B", None)],
        },
        independent_garrisons=["fort"],
        turn_events=[BEFORE_EVENT],
        name="integrated-military-acceptance",
    )


class TestIntegratedMilitaryAcceptance(unittest.TestCase):
    """Cierra la aceptación integrada y la invariancia de orden incidental."""

    def test_integrated_military_acceptance_is_invariant_under_incidental_order(self):
        event_keys = (
            "outcomes",
            "cancelled_orders",
            "broken_convoys",
            "dislodgements",
            "rebellions",
            "sieges",
        )
        decisions: Mapping[UnitKey, DislodgementDecision] = MappingProxyType(
            {UnitKey("P1", "A", "c"): DislodgementDecision("retreat", "r")}
        )
        observations = []

        for game in iter_military_orderings(_build_integrated_acceptance_game):
            before = military_snapshot(game)
            manager_observations = []
            manager = partial(
                _record_manager_snapshot,
                game=game,
                observations=manager_observations,
                decisions=decisions,
            )

            resolver = MilitaryResolver(game)
            with patch.object(
                resolver,
                "_apply_final_collections",
                wraps=resolver._apply_final_collections,
            ) as apply_final:
                resolution = resolver.run(manager)

            apply_final.assert_called_once()
            self.assertEqual(len(manager_observations), 1)
            self.assertIs(manager_observations[0][0], resolution)
            self.assertEqual(manager_observations[0][1], before)
            self.assertEqual(len(resolution.outcomes), len(resolver.units_by_key))
            self.assertEqual(
                len({outcome.unit for outcome in resolution.outcomes}),
                len(resolution.outcomes),
            )
            self.assertEqual(resolution.contested_locations, frozenset({"b", "c"}))

            outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
            dislodged = UnitKey("P1", "A", "c")
            convoyed = UnitKey("P3", "A", "convoy-origin")
            converted = UnitKey("P3", "A", "city")
            self.assertTrue(outcomes[dislodged].dislodged)
            self.assertIsNone(outcomes[dislodged].final_location)
            self.assertEqual(outcomes[convoyed].final_location, "convoy-destination")
            self.assertEqual(outcomes[converted].final_unit_type, "G")
            self.assertEqual(outcomes[converted].final_location, "city")

            p1 = next(player for player in game.players if player.player_id == "P1")
            p3 = next(player for player in game.players if player.player_id == "P3")
            self.assertIn("r", p1.armies)
            self.assertEqual(p1.rebelled_provinces, [])
            self.assertIn("city", p3.garrisons)
            self.assertEqual(game.besieges, ["fort"])
            self.assertEqual(game.independent_garrisons, ["fort"])

            payload = _military_event_payload(game)
            self.assertIn(["P1", "A", "a"], payload["cancelled_orders"])
            self.assertIn(["P1", "A", "c"], payload["cancelled_orders"])
            self.assertEqual(payload["broken_convoys"], [])
            self.assertEqual(payload["dislodgements"], [["P1", "A", "c"]])
            self.assertEqual(
                payload["rebellions"],
                [["P1", "province", "reb", "subdued"]],
            )
            self.assertEqual(
                payload["sieges"],
                [[["P4", "A", "fort"], "fort", "started"]],
            )

            observations.append(
                (
                    resolution,
                    tuple(payload[key] for key in event_keys),
                    military_snapshot(game),
                )
            )

        self.assertTrue(
            all(observation == observations[0] for observation in observations)
        )
