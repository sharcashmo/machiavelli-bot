"""Pruebas de los ingresos de primavera auditables."""

from unittest.mock import Mock, patch

from machiavelli.engine.income import IncomeManager
from machiavelli.game.events import EventType, TurnEvent
from tests.machiavelli.engine.helpers import create_mock_game, create_mock_player


def _manager() -> tuple[IncomeManager, Mock, Mock, Mock]:
    game = create_mock_game()
    player = create_mock_player("player_1")
    player.ducats = 0
    scenario = Mock(
        variable_income_home_countries=["L", "N"],
        variable_income_provinces=["rome"],
    )
    game_map = Mock(
        provinces={
            "venic": Mock(city="fortified", major_city=2),
            "rome": Mock(city="fortified", major_city=2),
            "flore": Mock(city="fortified", major_city=1),
            "piomb": Mock(city="city", major_city=1),
            "keep": Mock(city="fortress", major_city=3),
            "sienn": Mock(city=None, major_city=None),
            "paler": Mock(city=None, major_city=None),
        }
    )
    game.scenario = scenario
    game.map = game_map
    game.require_scenario.return_value = scenario
    game.require_map.return_value = game_map
    game.besieges = ["flore"]
    return IncomeManager(game), game, player, scenario


def test_income_records_sorted_provinces_and_city_breakdown() -> None:
    assert not hasattr(IncomeManager, "player_income")
    manager, game, player, _ = _manager()
    player.controlled_locations = ["sienn", "paler", "flore"]
    player.armies = ["rome", "sienn"]
    player.fleets = ["UA", "paler"]
    player.garrisons = ["venic"]

    manager._collect_player_income(player)

    event = game.add_event.call_args.args[0]
    assert isinstance(event, TurnEvent)
    assert event.type is EventType.INCOME_COLLECTED
    assert event.data == {
        "player": "player_1",
        "provinces": ("UA", "flore", "paler", "rome", "sienn"),
        "province_income": 5,
        "cities": ("venic",),
        "city_income": 2,
        "variable_income": (),
        "total_income": 7,
    }
    assert player.ducats == 7


def test_fortress_has_province_income_but_no_city_bonus() -> None:
    manager, game, player, _ = _manager()
    player.controlled_locations = ["keep"]

    manager._collect_player_income(player)

    event = game.add_event.call_args.args[0]
    assert event.data["provinces"] == ("keep",)
    assert event.data["province_income"] == 1
    assert event.data["cities"] == ()
    assert event.data["city_income"] == 0
    assert event.data["total_income"] == 1
    assert player.ducats == 1


def test_income_excludes_famine_and_rebellions_but_keeps_garrison_city() -> None:
    manager, game, player, _ = _manager()
    player.controlled_locations = ["sienn", "paler", "flore"]
    player.armies = ["rome", "sienn"]
    player.fleets = ["UA", "paler"]
    player.garrisons = ["venic"]
    player.rebelled_provinces = ["venic"]
    player.rebelled_cities = ["flore"]
    game.famine = ["rome"]

    manager._collect_player_income(player)

    event = game.add_event.call_args.args[0]
    assert event.data["provinces"] == ("UA", "paler", "sienn")
    assert event.data["province_income"] == 3
    assert event.data["cities"] == ("venic",)
    assert event.data["city_income"] == 2
    assert event.data["total_income"] == 5
    assert player.ducats == 5


@patch("machiavelli.engine.income.GameTables")
def test_income_records_every_variable_roll_and_amount(mock_tables: Mock) -> None:
    manager, game, player, _ = _manager()
    rng = Mock()
    rng.randint.side_effect = [1, 6]
    manager.rng = rng
    mock_tables.variable_income = {
        "N": [1, 2, 3, 4, 5, 6],
        "rome": [11, 12, 13, 14, 15, 16],
    }
    player.controlled_locations = ["sienn", "paler", "rome"]
    player.armies = ["sienn"]
    player.fleets = ["UA", "paler"]
    player.garrisons = ["venic"]
    player.home_countries = ["N"]
    player.rebelled_provinces = ["venic"]
    game.famine = ["rome"]

    manager._collect_player_income(player)

    event = game.add_event.call_args.args[0]
    assert event.data["variable_income"] == (
        {
            "source_type": "home_country",
            "source": "N",
            "roll": 1,
            "amount": 1,
        },
        {
            "source_type": "province",
            "source": "rome",
            "roll": 6,
            "amount": 16,
        },
    )
    assert event.data["total_income"] == 22
    assert player.ducats == 22


def test_run_emits_one_income_event_per_player_in_player_order() -> None:
    manager, game, first, scenario = _manager()
    second = create_mock_player("player_2")
    second.ducats = 0
    game.players = [first, second]
    first.controlled_locations = ["sienn"]
    second.controlled_locations = ["paler"]
    scenario.variable_income_home_countries = []
    scenario.variable_income_provinces = []

    manager.run()

    events = [call.args[0] for call in game.add_event.call_args_list]
    assert [event.type for event in events] == [
        EventType.INCOME_COLLECTED,
        EventType.INCOME_COLLECTED,
    ]
    assert [event.data["player"] for event in events] == ["player_1", "player_2"]
