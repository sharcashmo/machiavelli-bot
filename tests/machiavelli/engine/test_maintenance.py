"""Pruebas de eventos de mantenimiento por orden."""

from unittest.mock import MagicMock

import pytest

from machiavelli.engine.maintenance import MaintenanceResolver
from machiavelli.events import EventType
from machiavelli.game.command import Command


class DummyProvince:
    def __init__(self, city="city", is_venice=False, has_port=True):
        self.city = city
        self.is_venice = is_venice
        self.has_port = has_port


def _state(ducats: int = 20):
    game = MagicMock()
    player = MagicMock()
    player.player_id = "player_1"
    player.game = game
    player.ducats = ducats
    player.home_countries = ["Italy"]
    player.controlled_locations = ["rome", "flore", "venic"]
    player.armies = []
    player.fleets = []
    player.garrisons = []
    player.commands = []
    player.rebelled_cities = set()
    game.players = [player]
    scenario = MagicMock()
    scenario.province_home_country.side_effect = lambda province: (
        "Italy" if province in {"rome", "flore", "venic"} else "Other"
    )
    game_map = MagicMock()
    game_map.provinces = {
        "rome": DummyProvince(city="city", has_port=False),
        "flore": DummyProvince(city="fortified"),
        "venic": DummyProvince(city="fortified", has_port=True, is_venice=True),
    }
    game.require_scenario.return_value = scenario
    game.require_map.return_value = game_map
    return game, player


def _events(game: MagicMock):
    return [call.args[0] for call in game.add_event.call_args_list]


def test_set_default_commands_adds_missing_maintenance_orders() -> None:
    game, player = _state()
    player.armies = ["rome"]
    player.commands = []

    MaintenanceResolver(game).run()

    events = _events(game)
    assert player.armies == ["rome"]
    assert player.ducats == 17
    assert [event.type for event in events] == [
        EventType.MAINTENANCE_ORDER_RESOLVED,
        EventType.MAINTENANCE_SUMMARY,
    ]
    assert events[0].data["actor"] == "A rome"
    assert events[0].data["order"] == "M"
    assert events[0].data["result"] == "maintained"
    assert events[0].data["cost"] == 3


@pytest.mark.parametrize(
    (
        "ducats",
        "actor",
        "order",
        "armies",
        "fleets",
        "garrisons",
        "rebelled_cities",
        "expected_result",
        "expected_cost",
        "expected_armies",
        "expected_fleets",
        "expected_garrisons",
        "expected_ducats",
    ),
    [
        (20, "A rome", "D", ["rome"], [], [], set(), "disbanded", 0, [], [], [], 20),
        (20, "A rome", "D", [], [], [], set(), "unit_not_found", 0, [], [], [], 20),
        (
            20,
            "A rome",
            "M",
            ["rome"],
            [],
            [],
            set(),
            "maintained",
            3,
            ["rome"],
            [],
            [],
            17,
        ),
        (
            2,
            "A rome",
            "M",
            ["rome"],
            [],
            [],
            set(),
            "disbanded_no_funds",
            0,
            [],
            [],
            [],
            2,
        ),
        (20, "A rome", "R", [], [], [], set(), "recruited", 3, ["rome"], [], [], 17),
        (2, "A rome", "R", [], [], [], set(), "recruitment_no_funds", 0, [], [], [], 2),
        (
            20,
            "A nowhere",
            "R",
            [],
            [],
            [],
            set(),
            "invalid_home_or_control",
            0,
            [],
            [],
            [],
            20,
        ),
        (
            20,
            "A venic",
            "R",
            ["venic"],
            [],
            [],
            set(),
            "space_occupied",
            0,
            ["venic"],
            [],
            [],
            20,
        ),
        (20, "F rome", "R", [], [], [], set(), "port_required", 0, [], [], [], 20),
        (20, "G flore", "R", [], [], [], {"flore"}, "rebelled_city", 0, [], [], [], 20),
        (
            20,
            "G rome",
            "R",
            [],
            [],
            [],
            set(),
            "fortified_city_required",
            0,
            [],
            [],
            [],
            20,
        ),
    ],
)
def test_maintenance_results_are_parametrized(
    ducats: int,
    actor: str,
    order: str,
    armies: list[str],
    fleets: list[str],
    garrisons: list[str],
    rebelled_cities: set[str],
    expected_result: str,
    expected_cost: int,
    expected_armies: list[str],
    expected_fleets: list[str],
    expected_garrisons: list[str],
    expected_ducats: int,
) -> None:
    game, player = _state(ducats)
    player.armies = armies
    player.fleets = fleets
    player.garrisons = garrisons
    player.rebelled_cities = rebelled_cities
    player.commands = [Command(game, player, actor, order)]

    MaintenanceResolver(game).run()

    event = _events(game)[0]
    assert event.type is EventType.MAINTENANCE_ORDER_RESOLVED
    assert event.data["result"] == expected_result
    assert event.data["cost"] == expected_cost
    assert player.armies == expected_armies
    assert player.fleets == expected_fleets
    assert player.garrisons == expected_garrisons
    assert player.ducats == expected_ducats


def test_fortress_is_not_a_recruitment_location() -> None:
    game, player = _state()
    player.home_countries.append("Other")
    player.controlled_locations.append("keep")
    game.require_map.return_value.provinces["keep"] = DummyProvince(city="fortress")
    player.commands = [Command(game, player, "A keep", "R")]

    MaintenanceResolver(game).run()

    event = _events(game)[0]
    assert event.data["result"] == "invalid_home_or_control"
    assert player.armies == []
    assert player.ducats == 20


def test_same_phase_results_preserve_command_order() -> None:
    game, player = _state()
    player.armies = ["rome", "flore"]
    player.commands = [
        Command(game, player, "A flore", "D"),
        Command(game, player, "A rome", "D"),
    ]

    MaintenanceResolver(game).run()

    assert [event.data["actor"] for event in _events(game)[:-1]] == [
        "A flore",
        "A rome",
    ]


def test_run_emits_disband_maintain_recruit_then_summary() -> None:
    game, player = _state(ducats=9)
    player.armies = ["rome", "flore"]
    player.commands = [
        Command(game, player, "G flore", "R"),
        Command(game, player, "A rome", "M"),
        Command(game, player, "A flore", "D"),
    ]

    MaintenanceResolver(game).run()

    events = _events(game)
    assert [event.type for event in events] == [
        EventType.MAINTENANCE_ORDER_RESOLVED,
        EventType.MAINTENANCE_ORDER_RESOLVED,
        EventType.MAINTENANCE_ORDER_RESOLVED,
        EventType.MAINTENANCE_SUMMARY,
    ]
    assert [event.data["result"] for event in events[:-1]] == [
        "disbanded",
        "maintained",
        "recruited",
    ]


def test_summary_records_final_balance() -> None:
    game, player = _state(ducats=9)
    player.armies = ["rome"]
    player.commands = [Command(game, player, "A rome", "M")]

    MaintenanceResolver(game).run()

    assert _events(game)[-1].data == {
        "player": "player_1",
        "initial_ducats": 9,
        "expenses": 3,
        "remaining_ducats": 6,
    }


@pytest.mark.parametrize(
    ("location", "occupying_type", "expected_result"),
    [
        ("flore", "A", "recruited"),
        ("venic", "F", "space_occupied"),
    ],
)
def test_garrison_recruitment_only_shares_venice_space(
    location: str,
    occupying_type: str,
    expected_result: str,
) -> None:
    game, player = _state()
    if occupying_type == "A":
        player.armies = [location]
    else:
        player.fleets = [location]
    player.commands = [Command(game, player, f"G {location}", "R")]

    MaintenanceResolver(game).run()

    event = next(
        event for event in _events(game) if event.data.get("actor") == f"G {location}"
    )
    assert event.data["result"] == expected_result
    if expected_result == "recruited":
        assert player.garrisons == [location]
    else:
        assert player.garrisons == []
