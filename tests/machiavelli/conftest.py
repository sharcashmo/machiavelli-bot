"""Fixtures compartidas para las pruebas de Machiavelli."""

from collections.abc import Mapping
from copy import deepcopy

import pytest

from machiavelli.events import EventType, JSONValue


@pytest.fixture
def valid_event_payloads() -> Mapping[EventType, dict[str, JSONValue]]:
    """Devuelve exactamente un `payload` válido para cada tipo de evento público de
    turno.
    """
    unit = ["Milan", "A", "mil"]
    return {
        EventType.START_GAME: {"scenario": "basic"},
        EventType.START_GAME_POWER_ASSIGNED: {
            "player_id": "player-1",
            "discord_id": 123,
            "power_id": "Milan",
        },
        EventType.START_SEASON: {"year": 1454, "season": 0},
        EventType.FAMINE_SPAWN: {"severity_roll": 4, "provinces": ["mil", "ven"]},
        EventType.FAMINE_RELIEF: {"player": "Milan", "province": "mil"},
        EventType.FAMINE_ATTRITION: {"player": "Milan", "units": ["A mil"]},
        EventType.FAMINE_END: {"provinces": ["mil"]},
        EventType.PLAGUE_SPAWN: {"severity_roll": 2, "provinces": ["ven"]},
        EventType.PLAGUE_DEATH: {"player": None, "units": ["G ven"]},
        EventType.REBELLION_PACIFY: {
            "player": "Milan",
            "province": "mil",
            "kind": "province",
        },
        EventType.REBELLION_PROVINCE: {"player": "Milan", "province": "mil"},
        EventType.REBELLION_CITY: {"player": "Venice", "province": "ven"},
        EventType.EXPENSE: {
            "player": "Milan",
            "expense": "A",
            "target": None,
            "amount": 3,
        },
        EventType.EXPENSE_NO_FUNDS: {
            "player": "Milan",
            "expense": "B",
            "target": "A ven",
            "amount": "all",
        },
        EventType.EXPENSE_SYNTAX_ERROR: {
            "player": "Milan",
            "expense": "?",
            "target": None,
            "amount": "bad",
        },
        EventType.BRIBE_EXECUTED: {
            "player": "Milan",
            "expense": "B",
            "target": "A ven",
            "amount": 6,
        },
        EventType.INCOME_COLLECTED: {
            "player": "Milan",
            "provinces": ["mil"],
            "province_income": 1,
            "cities": ["mil"],
            "city_income": 2,
            "variable_income": [
                {
                    "source_type": "home_country",
                    "source": "Milan",
                    "roll": 6,
                    "amount": 4,
                }
            ],
            "total_income": 7,
        },
        EventType.MAINTENANCE_ORDER_RESOLVED: {
            "player": "Milan",
            "actor": "A mil",
            "order": "M",
            "target": None,
            "result": "maintained",
            "cost": 3,
        },
        EventType.MAINTENANCE_SUMMARY: {
            "player": "Milan",
            "initial_ducats": 10,
            "expenses": 3,
            "remaining_ducats": 7,
        },
        EventType.GET_CONTROL: {"player": "Milan", "provinces": ["mil"]},
        EventType.LOSE_CONTROL: {"player": "Milan", "provinces": ["ven"]},
        EventType.GET_HOME_COUNTRY: {"player": "Milan", "home_country": "Milan"},
        EventType.LOSE_HOME_COUNTRY: {
            "player": "Milan",
            "home_country": "Venice",
        },
        EventType.PLAYER_ELIMINATED: {"player": "Milan"},
        EventType.PLAYER_WON: {
            "player": "Milan",
            "cities": 12,
            "home_countries": 3,
        },
        EventType.MILITARY_RESOLUTION: {
            "outcomes": [[unit, "A", "ven", False]],
            "cancelled_orders": [unit],
            "broken_convoys": [unit],
            "dislodgements": [unit],
            "rebellions": [["Milan", "province", "mil", "subdued"]],
            "sieges": [[unit, "mil", "started"]],
            "decisions": [[unit, "retreat", "ven"]],
        },
    }


@pytest.fixture
def copied_event_payloads(
    valid_event_payloads: Mapping[EventType, dict[str, JSONValue]],
) -> dict[EventType, dict[str, JSONValue]]:
    """Devuelve copias mutables para las pruebas que corrompen deliberadamente los
    `payloads`.
    """
    return {
        event_type: deepcopy(payload)
        for event_type, payload in valid_event_payloads.items()
    }
