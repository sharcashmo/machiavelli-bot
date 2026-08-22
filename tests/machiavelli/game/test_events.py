"""Pruebas de contrato para eventos de turno tipados e inmutables."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from machiavelli.game.events import (
    EventType,
    InvalidTurnEventError,
    JSONValue,
    TurnEvent,
)

EXPECTED_EVENT_TYPES = {
    "start_game",
    "start_game_power_assigned",
    "start_season",
    "famine_spawn",
    "famine_relief",
    "famine_attrition",
    "famine_end",
    "plague_spawn",
    "plague_death",
    "rebellion_pacify",
    "rebellion_province",
    "rebellion_city",
    "expense",
    "expense_no_funds",
    "expense_syntax_error",
    "bribe_executed",
    "income_collected",
    "maintenance_order_resolved",
    "maintenance_summary",
    "get_control",
    "lose_control",
    "get_home_country",
    "lose_home_country",
    "player_eliminated",
    "player_won",
    "military_resolution",
    "military_orders_summary",
    "assassination_attempt",
}


def test_catalog_is_exact() -> None:
    assert {event_type.value for event_type in EventType} == EXPECTED_EVENT_TYPES
    assert len(EventType) == 28


def test_every_valid_payload_round_trips(
    valid_event_payloads: Mapping[EventType, dict[str, JSONValue]],
) -> None:
    assert set(valid_event_payloads) == set(EventType)

    for event_type, payload in valid_event_payloads.items():
        event = TurnEvent(event_type, payload)
        assert event.type is event_type
        assert json.loads(event.to_json()) == payload
        assert (
            TurnEvent.from_persisted(
                row_id=7,
                event_type=event_type.value,
                data_json=event.to_json(),
            )
            == event
        )


@pytest.mark.parametrize("event_type", list(EventType))
def test_payload_keys_are_exact(
    event_type: EventType,
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[event_type]
    first_key = next(iter(payload))
    payload.pop(first_key)
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(event_type, payload)

    payload = copied_event_payloads[event_type]
    payload["extra"] = "value"
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(event_type, payload)


@pytest.mark.parametrize(
    ("event_type", "field"),
    [
        (EventType.START_GAME, "scenario"),
        (EventType.FAMINE_RELIEF, "player"),
        (EventType.FAMINE_RELIEF, "province"),
        (EventType.PLAYER_ELIMINATED, "player"),
    ],
)
def test_identifiers_must_be_non_empty_strings(
    event_type: EventType,
    field: str,
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[event_type]
    payload[field] = ""
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(event_type, payload)


@pytest.mark.parametrize(
    ("event_type", "field"),
    [
        (EventType.START_SEASON, "year"),
        (EventType.START_GAME_POWER_ASSIGNED, "discord_id"),
        (EventType.INCOME_COLLECTED, "total_income"),
        (EventType.MAINTENANCE_SUMMARY, "expenses"),
        (EventType.PLAYER_WON, "cities"),
    ],
)
def test_integer_fields_reject_bool(
    event_type: EventType,
    field: str,
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[event_type]
    payload[field] = True
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(event_type, payload)


@pytest.mark.parametrize("event_type", [EventType.FAMINE_SPAWN, EventType.PLAGUE_SPAWN])
@pytest.mark.parametrize("roll", [-1, 6, True])
def test_severity_roll_is_zero_to_five(
    event_type: EventType,
    roll: int | bool,
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[event_type]
    payload["severity_roll"] = roll
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(event_type, payload)


@pytest.mark.parametrize("roll", [0, 7, True])
def test_variable_income_roll_is_one_to_six(
    roll: int | bool,
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[EventType.INCOME_COLLECTED]
    variable_income = payload["variable_income"]
    assert isinstance(variable_income, list)
    item = variable_income[0]
    assert isinstance(item, dict)
    item["roll"] = roll
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(EventType.INCOME_COLLECTED, payload)


@pytest.mark.parametrize(
    ("event_type", "field", "value"),
    [
        (EventType.REBELLION_PACIFY, "kind", "fortress"),
        (EventType.MAINTENANCE_ORDER_RESOLVED, "order", "X"),
        (EventType.MAINTENANCE_ORDER_RESOLVED, "result", "unknown"),
    ],
)
def test_closed_values_are_rejected(
    event_type: EventType,
    field: str,
    value: str,
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[event_type]
    payload[field] = value
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(event_type, payload)


def test_military_resolution_accepts_seven_empty_collections() -> None:
    event = TurnEvent.military_resolution([], [], [], [], [], [], [])
    assert event.type is EventType.MILITARY_RESOLUTION
    assert all(event.data[key] == () for key in event.data)


def test_military_resolution_accepts_non_empty_tuples() -> None:
    unit = ("Milan", "A", "mil")
    event = TurnEvent.military_resolution(
        ((unit, "A", "ven", False, None),),
        (unit,),
        (unit,),
        (unit,),
        (("Milan", "province", "mil", "subdued"),),
        ((unit, "mil", "started"),),
        ((unit, "retreat", "ven"),),
    )

    assert event.type is EventType.MILITARY_RESOLUTION
    assert all(isinstance(collection, tuple) for collection in event.data.values())
    assert isinstance(json.loads(event.to_json())["outcomes"], list)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("outcomes", [[[["Milan", "A", "mil"], "A", None, False]]]),
        ("cancelled_orders", [["Milan", "X", "mil"]]),
        ("broken_convoys", [["Milan", "A", ""]]),
        ("dislodgements", [["Milan", "A"]]),
        ("rebellions", [["Milan", "fortress", "mil", "subdued"]]),
        ("sieges", [["Milan", "mil", "started"]]),
        ("decisiones", [[["Milan", "A", "mil"], "retreat", "mil"]]),
    ],
)
def test_military_collections_are_validated(
    field: str,
    invalid: list[JSONValue],
    copied_event_payloads: dict[EventType, dict[str, JSONValue]],
) -> None:
    payload = copied_event_payloads[EventType.MILITARY_RESOLUTION]
    payload[field] = invalid
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(EventType.MILITARY_RESOLUTION, payload)


@pytest.mark.parametrize("payload", [[], None, "text", 1, True])
def test_payload_must_be_an_object(payload: JSONValue) -> None:
    with pytest.raises(InvalidTurnEventError):
        TurnEvent(EventType.START_GAME, payload)  # type: ignore[arg-type]


def test_event_is_deeply_immutable_and_defensively_copied() -> None:
    payload: dict[str, JSONValue] = {
        "player": "Milan",
        "provinces": ["mil"],
        "province_income": 1,
        "cities": ["mil"],
        "city_income": 2,
        "variable_income": [
            {
                "source_type": "province",
                "source": "mil",
                "roll": 4,
                "amount": 2,
            }
        ],
        "total_income": 5,
    }
    original = deepcopy(payload)
    event = TurnEvent(EventType.INCOME_COLLECTED, payload)

    payload["player"] = "changed"
    provinces = payload["provinces"]
    assert isinstance(provinces, list)
    provinces.append("ven")

    assert json.loads(event.to_json()) == original
    with pytest.raises(FrozenInstanceError):
        event.type = EventType.START_GAME
    with pytest.raises(FrozenInstanceError):
        event.data = {}
    with pytest.raises(TypeError):
        event.data["player"] = "changed"  # type: ignore[index]
    frozen_variable_income = event.data["variable_income"]
    assert isinstance(frozen_variable_income, tuple)
    frozen_item = frozen_variable_income[0]
    assert isinstance(frozen_item, Mapping)
    with pytest.raises(TypeError):
        frozen_item["amount"] = 99  # type: ignore[index]


def test_json_is_compact_deterministic_and_native() -> None:
    event = TurnEvent(
        EventType.EXPENSE,
        {
            "target": None,
            "amount": "sí",
            "expense": "A",
            "player": "Milan",
        },
    )
    assert event.to_json() == (
        '{"amount":"sí","expense":"A","player":"Milan","target":null}'
    )

    unit = ("Milan", "A", "mil")
    military = TurnEvent.military_resolution(
        ((unit, "A", "ven", False, None),),
        (unit,),
        (unit,),
        (unit,),
        (("Milan", "province", "mil", "subdued"),),
        ((unit, "mil", "started"),),
        ((unit, "retreat", "mil"),),
    )
    assert military.to_json() == (
        '{"broken_convoys":[["Milan","A","mil"]],'
        '"cancelled_orders":[["Milan","A","mil"]],'
        '"decisions":[[["Milan","A","mil"],"retreat","mil"]],'
        '"dislodgements":[["Milan","A","mil"]],'
        '"outcomes":[[["Milan","A","mil"],"A","ven",false,null]],'
        '"rebellions":[["Milan","province","mil","subdued"]],'
        '"sieges":[[["Milan","A","mil"],"mil","started"]]}'
    )


def test_persisted_errors_include_row_and_raw_type_without_payload() -> None:
    with pytest.raises(InvalidTurnEventError) as caught:
        TurnEvent.from_persisted(
            row_id=42,
            event_type="unknown",
            data_json='{"secret":"do not expose"}',
        )

    error = caught.value
    assert error.row_id == 42
    assert error.event_type == "unknown"
    assert "42" in str(error)
    assert "unknown" in str(error)
    assert "secret" not in str(error)
    assert "do not expose" not in str(error)
    assert error.__cause__ is not None

    with pytest.raises(InvalidTurnEventError) as malformed:
        TurnEvent.from_persisted(
            row_id=43,
            event_type=EventType.START_GAME.value,
            data_json="not json",
        )
    assert malformed.value.row_id == 43
    assert malformed.value.event_type == EventType.START_GAME.value
    assert "43" in str(malformed.value)
    assert EventType.START_GAME.value in str(malformed.value)
    assert "not json" not in str(malformed.value)
    assert malformed.value.__cause__ is not None
