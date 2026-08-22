# tests/machiavelli/engine/test_setup.py
from random import Random
from unittest.mock import Mock

import pytest

from machiavelli.engine.exceptions import (
    DuplicatePlayerError,
    GameAlreadyStartedError,
    GameInitializationError,
    InvalidPlayerCountError,
    ScenarioNotSelectedError,
)
from machiavelli.engine.setup import SetupManager
from machiavelli.game.events import EventType, TurnEvent
from machiavelli.game.scenario import Power, Rules, Scenario, VictoryConditions

from .helpers import create_mock_game, create_mock_player


def test_setup_raises_scenario_not_selected():
    game = create_mock_game(turn_number=0, scenario_id=None, scenario=None)
    manager = SetupManager(game)

    with pytest.raises(ScenarioNotSelectedError):
        manager.run()


def test_setup_raises_duplicate_player_id():
    p1 = create_mock_player("p1", discord_id=100)
    p2 = create_mock_player("p1", discord_id=200)
    game = create_mock_game(turn_number=0, players=[p1, p2])

    manager = SetupManager(game)

    with pytest.raises(DuplicatePlayerError) as exc_info:
        manager.run()

    assert exc_info.value.player_id == "p1"


def test_setup_raises_duplicate_discord_id():
    p1 = create_mock_player("p1", discord_id=100)
    p2 = create_mock_player("p2", discord_id=100)
    game = create_mock_game(turn_number=0, players=[p1, p2])

    manager = SetupManager(game)

    with pytest.raises(DuplicatePlayerError) as exc_info:
        manager.run()

    assert exc_info.value.discord_id == 100


def test_setup_raises_invalid_player_count():
    p1 = create_mock_player("p1", discord_id=100)
    p2 = create_mock_player("p2", discord_id=200)
    power = Mock()
    scenario = Mock(powers={"M": power, "V": power, "L": power})
    game = create_mock_game(turn_number=0, players=[p1, p2], scenario=scenario)

    manager = SetupManager(game)

    with pytest.raises(InvalidPlayerCountError) as exc_info:
        manager.run()

    assert exc_info.value.current == 2
    assert exc_info.value.scenario_players == 3


def test_setup_raises_already_started():
    game = create_mock_game(turn_number=1)

    manager = SetupManager(game)

    with pytest.raises(GameAlreadyStartedError):
        manager.run()


def test_setup_successful_run():
    p1 = create_mock_player("p1", discord_id=100)
    p2 = create_mock_player("p2", discord_id=200)

    power_venice = Mock(controlled_provinces=["venic"], garrisons=[])
    power_milan = Mock(controlled_provinces=["milan"], garrisons=[])

    scenario = Mock(powers={"V": power_venice, "M": power_milan})

    prov_venice = Mock(city="fortified")
    prov_milan = Mock(city="fortified")
    prov_florence = Mock(city="fortified")

    map_obj = Mock(
        provinces={
            "venic": prov_venice,
            "milan": prov_milan,
            "flore": prov_florence,
        }
    )

    game = create_mock_game(
        turn_number=0,
        players=[p1, p2],
        scenario=scenario,
        scenario_id="scenario_1",
    )
    game.map = map_obj

    manager = SetupManager(game, rng=Random(42))
    manager.run()

    events = [item.args[0] for item in game.add_event.call_args_list]
    assigned_powers = [
        p1.assign_power_from_scenario.call_args.args[0],
        p2.assign_power_from_scenario.call_args.args[0],
    ]
    assert all(isinstance(event, TurnEvent) for event in events)
    assert [event.type for event in events] == [
        EventType.START_GAME,
        EventType.START_GAME_POWER_ASSIGNED,
        EventType.START_GAME_POWER_ASSIGNED,
    ]
    assert events[0].data == {"scenario": "scenario_1"}
    assert [event.data for event in events[1:]] == [
        {"player_id": "p1", "discord_id": 100, "power_id": assigned_powers[0]},
        {"player_id": "p2", "discord_id": 200, "power_id": assigned_powers[1]},
    ]
    assert game.independent_garrisons == ["flore"]
    p1.assign_power_from_scenario.assert_called_once()
    p2.assign_power_from_scenario.assert_called_once()


def test_setup_assignment_events_are_reproducible_with_injected_random():
    def assigned_power_ids(seed: int) -> tuple[str, ...]:
        players = [
            create_mock_player("p1", discord_id=100),
            create_mock_player("p2", discord_id=200),
            create_mock_player("p3", discord_id=None),
        ]
        powers = {
            power_id: Mock(controlled_provinces=[], garrisons=[])
            for power_id in ("M", "V", "L")
        }
        game = create_mock_game(
            turn_number=0,
            players=players,
            scenario=Mock(powers=powers),
            scenario_id="scenario_1",
        )
        game.map = Mock(provinces={})

        SetupManager(game, rng=Random(seed)).run()

        events = [item.args[0] for item in game.add_event.call_args_list]
        assert events[0].type is EventType.START_GAME
        return tuple(event.data["power_id"] for event in events[1:])

    assert assigned_power_ids(713) == assigned_power_ids(713)


def _scenario_with_rules(
    rules: Rules, *, garrisons: list[str] | None = None
) -> Scenario:
    return Scenario(
        name="rules-test",
        year=1454,
        victory_conditions=VictoryConditions(cities=1, home_countries=1),
        rules=rules,
        powers={"M": Power(garrisons=garrisons or [])},
    )


def test_setup_rejects_inactive_fortress_garrison_before_first_event():
    player = create_mock_player("p1")
    scenario = _scenario_with_rules(Rules(fortress_active=False), garrisons=["keep"])
    game = create_mock_game(players=[player], scenario=scenario)
    game.map = Mock(provinces={"keep": Mock(city="fortress")})

    with pytest.raises(GameInitializationError, match="keep"):
        SetupManager(game).run()

    game.add_event.assert_not_called()
    player.assign_power_from_scenario.assert_not_called()


def test_setup_disables_assassination_counters_and_keeps_automatic_fortified_only():
    player = create_mock_player("p1")
    scenario = _scenario_with_rules(
        Rules(fortress_active=True, assassinations_active=False),
        garrisons=["keep"],
    )
    game = create_mock_game(players=[player], scenario=scenario)
    game.map = Mock(
        provinces={
            "keep": Mock(city="fortress"),
            "fort": Mock(city="fortified"),
        }
    )

    SetupManager(game).run()

    assert player.assign_power_from_scenario.call_args.args[2] == []
    assert game.independent_garrisons == ["fort"]
