# tests/machiavelli/services/test_player_interaction_service.py

from unittest.mock import MagicMock

import pytest

from machiavelli.services.player_interaction_service import PlayerInteractionService


@pytest.fixture
def mock_game():
    game = MagicMock()
    game.turn_number = 1  # Por defecto turno de mantenimiento de primavera (1 % 4 == 1)
    game.famine = []
    game.independent_garrisons = []
    game.besieges = []
    game.players = []
    return game


@pytest.fixture
def mock_player(mock_game):
    player = MagicMock()
    player.game = mock_game
    player.armies = ["naple"]
    player.fleets = []
    player.garrisons = []
    player.ducats = 15
    player.home_countries = ["N"]
    player.controlled_locations = ["naple"]
    player.commands = []
    player.rebelled_provinces = []
    player.rebelled_cities = []
    player.power = "N"
    player.ass_counters = []
    return player


@pytest.fixture
def service(mock_player):
    return PlayerInteractionService(mock_player)


def test_cmd_available_actors_maintenance(service, mock_game):
    mock_game.turn_number = 1
    mock_province = MagicMock()
    mock_province.name = "Naples"
    mock_province.city = "city"
    mock_province.has_port = True

    mock_game.map.provinces = {"naple": mock_province}
    mock_game.map.seas = {}

    choices = service.cmd_available_actors()
    assert ("A naple", "Ejército en Naples") in choices


def test_cmd_available_actors_campaign(service, mock_game):
    mock_game.turn_number = 2  # Turno de campaña
    mock_province = MagicMock()
    mock_province.name = "Naples"
    mock_province.land_routes = []
    mock_province.sea_routes = []

    mock_game.map.provinces = {"naple": mock_province}
    mock_game.map.seas = {}

    choices = service.cmd_available_actors()
    assert ("A naple", "Ejército en Naples") in choices


def test_cmd_available_commands_maintenance(service, mock_game):
    mock_game.turn_number = 1
    service.player.armies = ["naple"]
    service.player.fleets = []
    service.player.garrisons = []

    with pytest.MonkeyPatch.context() as mp:
        from machiavelli.services import player_interaction_service

        mock_gt = MagicMock()
        mock_gt.maintenance_orders = {
            "M": {"text": "Mantener"},
            "D": {"text": "Desbandar"},
        }
        mp.setattr(player_interaction_service, "GameTables", mock_gt)

        choices = service.cmd_available_commands("A naple")
        assert ("M", "Mantener") in choices
        assert ("D", "Desbandar") in choices


def test_cmd_available_commands_campaign(service, mock_game):
    mock_game.turn_number = 2  # Turno de campaña
    mock_province = MagicMock()
    mock_province.name = "Naples"
    mock_province.has_port = False
    mock_province.city = "fortress"
    mock_game.map.provinces.get.return_value = mock_province

    with pytest.MonkeyPatch.context() as mp:
        from machiavelli.services import player_interaction_service

        mock_gt = MagicMock()
        mock_gt.military_orders = {
            "A": {"text": "Avanzar a Provincia o Mar"},
            "H": {"text": "Mantener"},
            "S": {"text": "Apoyar Provincia o Mar"},
            "C": {"text": "Convertir o desbandar"},
        }
        mp.setattr(player_interaction_service, "GameTables", mock_gt)

        choices = service.cmd_available_commands("A naple")
        commands = [c[0] for c in choices]
        assert "A" in commands
        assert "H" in commands
        assert "S" in commands
        assert "C" in commands


def test_exp_available_expenses(service, mock_game):
    service.player.ducats = 10

    with pytest.MonkeyPatch.context() as mp:
        from machiavelli.services import player_interaction_service

        mock_gt = MagicMock()
        mock_gt.expenses = {
            "A": {"cost": 5, "text": "Paliar hambruna"},
            "F": {"cost": 2, "text": "Contra-soborno"},
        }
        mp.setattr(player_interaction_service, "GameTables", mock_gt)

        mock_game.famine = ["naple"]
        mock_province = MagicMock()
        mock_province.land_routes = []
        mock_province.sea_routes = []
        mock_game.map.provinces = {"naple": mock_province}
        mock_game.map.seas = {}

        choices = service.exp_available_expenses()
        assert ("E A", "Paliar hambruna") in choices
        assert ("E F", "Contra-soborno") in choices


def test_exp_available_amounts_fixed_cost(service):
    service.player.ducats = 20
    service.game.famine = ["naple"]
    service.game.map.provinces = {
        "naple": MagicMock(
            id="naple",
            name="Naples",
            land_routes=[],
            sea_routes=[],
        ),
    }
    service.game.map.seas = {}

    with pytest.MonkeyPatch.context() as mp:
        from machiavelli.services import player_interaction_service

        mock_gt = MagicMock()
        mock_gt.expenses = {"A": {"cost": 5, "text": "Paliar hambruna"}}
        mp.setattr(player_interaction_service, "GameTables", mock_gt)

        choices = service.exp_available_amounts("E A", "naple")
        assert ("0", "Cancelar gasto") in choices
        assert ("5", "5 ducados") in choices


def test_inactive_rules_hide_famine_and_assassination_expenses(service, mock_game):
    service.player.ass_counters = ["M"]
    mock_game.players = [
        service.player,
        MagicMock(power="M", home_countries=["M"], armies=[], fleets=[], garrisons=[]),
    ]
    mock_game.famine = ["naple"]
    mock_game.scenario.rules.famine_active = False
    mock_game.scenario.rules.assassinations_active = False
    mock_game.map.provinces = {
        "naple": MagicMock(land_routes=[], sea_routes=[]),
    }
    mock_game.map.seas = {}

    with pytest.MonkeyPatch.context() as mp:
        from machiavelli.services import player_interaction_service

        mock_gt = MagicMock()
        mock_gt.expenses = {
            "A": {"cost": 5, "text": "Paliar hambruna"},
            "E": {"cost": 5, "text": "Asesinar"},
            "F": {"cost": 2, "text": "Contra-soborno"},
        }
        mp.setattr(player_interaction_service, "GameTables", mock_gt)

        choices = service.exp_available_expenses()
        famine_targets = service.exp_available_targets("E A")
        assassination_targets = service.exp_available_targets("E E")
        famine_amounts = service.exp_available_amounts("E A", "naple")
        assassination_amounts = service.exp_available_amounts("E E", "M")

    assert ("E A", "Paliar hambruna") not in choices
    assert ("E E", "Asesinar") not in choices
    assert ("E F", "Contra-soborno") in choices
    assert famine_targets == []
    assert assassination_targets == []
    assert famine_amounts == []
    assert assassination_amounts == []


def test_active_fortress_shows_existing_defensible_actions(service, mock_game):
    mock_game.turn_number = 2
    service.player.armies = ["keep"]
    service.player.garrisons = ["keep"]
    mock_game.independent_garrisons = ["keep"]
    province = MagicMock(
        city="fortress",
        has_port=True,
        land_routes=[],
        sea_routes=[],
    )
    province.name = "Keep"
    mock_game.map.provinces = {"keep": province}
    mock_game.map.seas = {}

    with pytest.MonkeyPatch.context() as mp:
        from machiavelli.services import player_interaction_service

        mock_gt = MagicMock()
        mock_gt.military_orders = {
            "A": {"text": "Avanzar"},
            "B": {"text": "Asediar"},
            "H": {"text": "Mantener"},
            "S": {"text": "Apoyar"},
            "C": {"text": "Convertir"},
        }
        mp.setattr(player_interaction_service, "GameTables", mock_gt)

        actors = service.cmd_available_actors()
        commands = dict(service.cmd_available_commands("A keep"))

    assert ("G keep", "Guarnición en Keep") in actors
    assert "B" in commands
    assert "C" in commands


def test_fortress_is_not_offered_for_recruitment(service, mock_game):
    mock_game.turn_number = 1
    service.player.armies = []
    service.player.fleets = []
    service.player.garrisons = []
    service.player.home_countries = ["M"]
    service.player.controlled_locations = ["keep"]
    mock_game.scenario.home_countries = {
        "M": MagicMock(provinces=["keep"]),
    }
    mock_game.map.provinces = {
        "keep": MagicMock(name="Keep", city="fortress", has_port=True),
    }
    mock_game.map.seas = {}

    assert service.cmd_available_actors() == []
