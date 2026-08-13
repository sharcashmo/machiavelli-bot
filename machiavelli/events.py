"""Eventos de turno tipados, validados e inmutables."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Self, TypeGuard

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)
type FrozenJSONValue = (
    None
    | bool
    | int
    | float
    | str
    | tuple[FrozenJSONValue, ...]
    | Mapping[str, FrozenJSONValue]
)


class EventType(StrEnum):
    """Catálogo cerrado de hechos emitidos durante un turno."""

    START_GAME = "start_game"
    START_GAME_POWER_ASSIGNED = "start_game_power_assigned"
    START_SEASON = "start_season"
    FAMINE_SPAWN = "famine_spawn"
    FAMINE_RELIEF = "famine_relief"
    FAMINE_ATTRITION = "famine_attrition"
    FAMINE_END = "famine_end"
    PLAGUE_SPAWN = "plague_spawn"
    PLAGUE_DEATH = "plague_death"
    REBELLION_PACIFY = "rebellion_pacify"
    REBELLION_PROVINCE = "rebellion_province"
    REBELLION_CITY = "rebellion_city"
    EXPENSE = "expense"
    EXPENSE_NO_FUNDS = "expense_no_funds"
    EXPENSE_SYNTAX_ERROR = "expense_syntax_error"
    BRIBE_EXECUTED = "bribe_executed"
    INCOME_COLLECTED = "income_collected"
    MAINTENANCE_ORDER_RESOLVED = "maintenance_order_resolved"
    MAINTENANCE_SUMMARY = "maintenance_summary"
    GET_CONTROL = "get_control"
    LOSE_CONTROL = "lose_control"
    GET_HOME_COUNTRY = "get_home_country"
    LOSE_HOME_COUNTRY = "lose_home_country"
    PLAYER_ELIMINATED = "player_eliminated"
    PLAYER_WON = "player_won"
    MILITARY_ORDERS_SUMMARY = "military_orders_summary"
    MILITARY_RESOLUTION = "military_resolution"
    ASSASSINATION_ATTEMPT = "assassination_attempt"


class InvalidTurnEventError(ValueError):
    """Un evento de turno no cumple el contrato de eventos públicos."""

    def __init__(
        self,
        message: str = "Evento de turno inválido",
        *,
        row_id: int | None = None,
        event_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.row_id = row_id
        self.event_type = event_type


_MAINTENANCE_RESULTS = {
    "disbanded",
    "unit_not_found",
    "maintained",
    "disbanded_no_funds",
    "recruited",
    "recruitment_no_funds",
    "invalid_home_or_control",
    "space_occupied",
    "port_required",
    "rebelled_city",
    "fortified_city_required",
}


@dataclass(frozen=True, slots=True, init=False)
class TurnEvent:
    """Representa a un evento del turno actual que deba aparecer en el reporte."""

    type: EventType
    data: Mapping[str, FrozenJSONValue]

    def __init__(self, type: EventType, data: Mapping[str, object]) -> None:
        raw_type = type.value if isinstance(type, EventType) else str(type)
        try:
            if not isinstance(type, EventType):
                raise TypeError("El tipo debe pertenecer a EventType")
            if not isinstance(data, Mapping):
                raise TypeError("El payload debe ser un objeto")
            normalized = _VALIDATORS[type](data)
            frozen = _freeze(normalized)
            if not isinstance(frozen, Mapping):
                raise TypeError("El payload debe ser un objeto")
        except (TypeError, ValueError, KeyError) as error:
            raise InvalidTurnEventError(event_type=raw_type) from error
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "data", frozen)

    @classmethod
    def expense(
        cls,
        event_type: EventType,
        actor: str,
        expense_type: str,
        target: str | None,
        amount: int | str,
    ) -> Self:
        """Construye un evento de gasto o de soborno."""
        allowed = {
            EventType.EXPENSE,
            EventType.EXPENSE_NO_FUNDS,
            EventType.EXPENSE_SYNTAX_ERROR,
            EventType.BRIBE_EXECUTED,
        }
        if event_type not in allowed:
            raise InvalidTurnEventError(event_type=str(event_type))
        normalized_amount: int | str = (
            int(amount) if isinstance(amount, str) and amount.isdigit() else amount
        )
        return cls(
            event_type,
            {
                "player": actor,
                "expense": expense_type,
                "target": target,
                "amount": normalized_amount,
            },
        )

    @classmethod
    def military_resolution(
        cls,
        outcomes: Sequence[Sequence[object]],
        cancelled_orders: Sequence[Sequence[object]],
        broken_convoys: Sequence[Sequence[object]],
        dislodgements: Sequence[Sequence[object]],
        rebellions: Sequence[Sequence[object]],
        sieges: Sequence[Sequence[object]],
        decisions: Sequence[Sequence[object]],
    ) -> Self:
        """Construye un evento de sumario de la fase de resolución militar."""
        return cls(
            EventType.MILITARY_RESOLUTION,
            {
                "outcomes": list(outcomes),
                "cancelled_orders": list(cancelled_orders),
                "broken_convoys": list(broken_convoys),
                "dislodgements": list(dislodgements),
                "rebellions": list(rebellions),
                "sieges": list(sieges),
                "decisions": list(decisions),
            },
        )

    @classmethod
    def military_orders_summary(
        cls,
        orders: Sequence[Sequence[object]],
        invalid_orders: Sequence[Sequence[object]],
    ) -> Self:
        """Construye el evento de sumario de órdenes militares recibidas."""
        return cls(
            EventType.MILITARY_ORDERS_SUMMARY,
            {"orders": list(orders), "invalid_orders": list(invalid_orders)},
        )

    def to_json(self) -> str:
        """Serializa de forma determinista un árbol JSON nativo nuevo."""
        return json.dumps(
            _thaw(self.data),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_persisted(
        cls,
        *,
        row_id: int,
        event_type: str,
        data_json: str,
    ) -> Self:
        """Reconstruye y valida una fila persistida, incluyendo el contexto de la fila
        si falla.
        """
        try:
            parsed_type = EventType(event_type)
            payload = json.loads(data_json)
            if not isinstance(payload, dict):
                raise TypeError("El JSON persistido debe ser un objeto")
            return cls(parsed_type, payload)
        except (InvalidTurnEventError, TypeError, ValueError) as error:
            raise InvalidTurnEventError(
                f"Evento persistido inválido en fila {row_id}, tipo {event_type!r}",
                row_id=row_id,
                event_type=event_type,
            ) from error


def _freeze(value: object) -> FrozenJSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if _is_sequence(value):
        return tuple(_freeze(item) for item in value)
    raise TypeError("Valor no serializable como JSON")


def _thaw(value: FrozenJSONValue) -> JSONValue:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _keys(data: Mapping[str, object], expected: set[str]) -> None:
    if set(data) != expected:
        raise ValueError("Claves de payload inválidas")


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("Se esperaba un string no vacío")
    return value


def _nullable_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _integer(value: object, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Se esperaba un entero")
    if minimum is not None and value < minimum:
        raise ValueError("Entero fuera de rango")
    return value


def _nullable_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _choice(value: object, choices: set[str]) -> str:
    string = _string(value)
    if string not in choices:
        raise ValueError("Valor fuera del catálogo")
    return string


def _string_list(value: object, *, non_empty: bool = False) -> list[JSONValue]:
    if not _is_sequence(value):
        raise TypeError("Se esperaba una lista")
    result: list[JSONValue] = [_string(item) for item in value]
    if non_empty and not result:
        raise ValueError("La lista no puede estar vacía")
    return result


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _simple_strings(
    *fields: str,
) -> Callable[[Mapping[str, object]], dict[str, JSONValue]]:
    expected = set(fields)

    def validate(data: Mapping[str, object]) -> dict[str, JSONValue]:
        _keys(data, expected)
        return {field: _string(data[field]) for field in fields}

    return validate


def _start_game_power_assigned(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player_id", "discord_id", "power_id"})
    return {
        "player_id": _string(data["player_id"]),
        "discord_id": _nullable_integer(data["discord_id"]),
        "power_id": _string(data["power_id"]),
    }


def _start_season(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"year", "season"})
    season = _integer(data["season"], minimum=0)
    if season > 3:
        raise ValueError("Estación fuera de rango")
    return {"year": _integer(data["year"]), "season": season}


def _spawn(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"severity_roll", "provinces"})
    roll = _integer(data["severity_roll"])
    if not 0 <= roll <= 5:
        raise ValueError("Tirada fuera de rango")
    return {
        "severity_roll": roll,
        "provinces": _string_list(data["provinces"], non_empty=True),
    }


def _loss(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "units"})
    return {
        "player": _nullable_string(data["player"]),
        "units": _string_list(data["units"], non_empty=True),
    }


def _province_list(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"provinces"})
    return {"provinces": _string_list(data["provinces"], non_empty=True)}


def _rebellion_pacify(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "province", "kind"})
    return {
        "player": _string(data["player"]),
        "province": _string(data["province"]),
        "kind": _choice(data["kind"], {"province", "city"}),
    }


def _assassination_attempt(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"assassin", "target", "result", "lost_garrisons", "rebellions"})
    return {
        "assassin": _string(data["assassin"]),
        "target": _string(data["target"]),
        "result": _choice(data["result"], {"success", "failed", "late"}),
        "lost_garrisons": _string_list(data["lost_garrisons"], non_empty=False),
        "rebellions": _string_list(data["rebellions"], non_empty=False),
    }


def _expense(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "expense", "target", "amount"})
    amount = data["amount"]
    if isinstance(amount, bool) or not isinstance(amount, (int, str)):
        raise TypeError("Importe de gasto inválido")
    if isinstance(amount, str):
        amount = _string(amount)
    return {
        "player": _string(data["player"]),
        "expense": _string(data["expense"]),
        "target": _nullable_string(data["target"]),
        "amount": amount,
    }


def _bribe_executed(data: Mapping[str, object]) -> dict[str, JSONValue]:
    result = _expense(data)
    result["target"] = _string(data["target"])
    result["amount"] = _integer(data["amount"], minimum=0)
    return result


def _variable_income(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise TypeError("Ingreso variable inválido")
    _keys(value, {"source_type", "source", "roll", "amount"})
    roll = _integer(value["roll"])
    if not 1 <= roll <= 6:
        raise ValueError("Tirada fuera de rango")
    return {
        "source_type": _choice(value["source_type"], {"home_country", "province"}),
        "source": _string(value["source"]),
        "roll": roll,
        "amount": _integer(value["amount"], minimum=0),
    }


def _income_collected(data: Mapping[str, object]) -> dict[str, JSONValue]:
    expected = {
        "player",
        "provinces",
        "province_income",
        "cities",
        "city_income",
        "variable_income",
        "total_income",
    }
    _keys(data, expected)
    variable_income = data["variable_income"]
    if not _is_sequence(variable_income):
        raise TypeError("Ingresos variables inválidos")
    return {
        "player": _string(data["player"]),
        "provinces": _string_list(data["provinces"]),
        "province_income": _integer(data["province_income"], minimum=0),
        "cities": _string_list(data["cities"]),
        "city_income": _integer(data["city_income"], minimum=0),
        "variable_income": [_variable_income(item) for item in variable_income],
        "total_income": _integer(data["total_income"], minimum=0),
    }


def _maintenance_order(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "actor", "order", "target", "result", "cost"})
    return {
        "player": _string(data["player"]),
        "actor": _string(data["actor"]),
        "order": _choice(data["order"], {"D", "M", "R"}),
        "target": _nullable_string(data["target"]),
        "result": _choice(data["result"], _MAINTENANCE_RESULTS),
        "cost": _integer(data["cost"], minimum=0),
    }


def _maintenance_summary(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "initial_ducats", "expenses", "remaining_ducats"})
    return {
        "player": _string(data["player"]),
        "initial_ducats": _integer(data["initial_ducats"], minimum=0),
        "expenses": _integer(data["expenses"], minimum=0),
        "remaining_ducats": _integer(data["remaining_ducats"], minimum=0),
    }


def _control(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "provinces"})
    return {
        "player": _string(data["player"]),
        "provinces": _string_list(data["provinces"], non_empty=True),
    }


def _player_won(data: Mapping[str, object]) -> dict[str, JSONValue]:
    _keys(data, {"player", "cities", "home_countries"})
    return {
        "player": _string(data["player"]),
        "cities": _integer(data["cities"], minimum=0),
        "home_countries": _integer(data["home_countries"], minimum=0),
    }


def _sequence(value: object, length: int) -> list[object]:
    if not _is_sequence(value) or len(value) != length:
        raise TypeError("Registro militar inválido")
    return list(value)


def _unit_key(value: object) -> list[JSONValue]:
    item = _sequence(value, 3)
    return [
        _nullable_string(item[0]),
        _choice(item[1], {"A", "F", "G"}),
        _string(item[2]),
    ]


def _outcome(value: object) -> list[JSONValue]:
    item = _sequence(value, 5)
    unit = _unit_key(item[0])
    final_type = _choice(item[1], {"A", "F", "G"})
    final_location = _nullable_string(item[2])
    if not isinstance(item[3], bool):
        raise TypeError("Estado de desalojo inválido")
    dislodged = item[3]
    if dislodged != (final_location is None):
        raise ValueError("Destino militar incoherente")
    attack_origin = _nullable_string(item[4])
    if attack_origin is not None and not dislodged:
        raise ValueError("Origen de ataque sin desalojo")
    return [unit, final_type, final_location, dislodged, attack_origin]


def _rebellion(value: object) -> list[JSONValue]:
    item = _sequence(value, 4)
    return [
        _nullable_string(item[0]),
        _choice(item[1], {"province", "city"}),
        _string(item[2]),
        _choice(item[3], {"subdued", "liberated"}),
    ]


def _siege(value: object) -> list[JSONValue]:
    item = _sequence(value, 3)
    return [
        _unit_key(item[0]),
        _string(item[1]),
        _choice(item[2], {"started", "completed", "lifted"}),
    ]


def _decisions(value: object) -> list[JSONValue]:
    item = _sequence(value, 3)
    unit = _unit_key(item[0])
    result_type = _choice(item[1], {"retreat", "garrison", "disband"})
    destination = _nullable_string(item[2])
    return [unit, result_type, destination]


def _orders(value: object) -> list[JSONValue]:
    item = _sequence(value, 7)
    unit = _unit_key(item[0])
    order_type = _choice(item[1], {"A", "B", "H", "L", "S", "T", "C"})
    target_location = _nullable_string(item[2])
    if item[3]:
        path = _thaw(item[3])
    else:
        path = None
    if item[4]:
        transported_army = _unit_key(item[4])
    else:
        transported_army = None
    supported_faction = _nullable_string(item[5])
    is_convoy = item[6]

    return [
        unit,
        order_type,
        target_location,
        path,
        transported_army,
        supported_faction,
        is_convoy,
    ]


def _invalid_orders(value: object) -> list[JSONValue]:
    item = _sequence(value, 2)
    unit = _unit_key(item[0])
    reason = item[1]
    return [unit, reason]


def _canonicalize(
    value: object,
    validator: Callable[[object], list[JSONValue]],
) -> list[JSONValue]:
    if not _is_sequence(value):
        raise TypeError("Colección militar inválida")
    validated = [validator(item) for item in value]
    return sorted(validated, key=_record_sort_key)


def _record_sort_key(value: JSONValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _military_resolution(data: Mapping[str, object]) -> dict[str, JSONValue]:
    expected = {
        "outcomes",
        "cancelled_orders",
        "broken_convoys",
        "dislodgements",
        "rebellions",
        "sieges",
        "decisions",
    }
    _keys(data, expected)
    return {
        "outcomes": _canonicalize(data["outcomes"], _outcome),
        "cancelled_orders": _canonicalize(data["cancelled_orders"], _unit_key),
        "broken_convoys": _canonicalize(data["broken_convoys"], _unit_key),
        "dislodgements": _canonicalize(data["dislodgements"], _unit_key),
        "rebellions": _canonicalize(data["rebellions"], _rebellion),
        "sieges": _canonicalize(data["sieges"], _siege),
        "decisions": _canonicalize(data["decisions"], _decisions),
    }


def _military_orders_summary(data: Mapping[str, object]) -> dict[str, JSONValue]:
    expected = {"orders", "invalid_orders"}
    _keys(data, expected)
    return {
        "orders": _canonicalize(data["orders"], _orders),
        "invalid_orders": _canonicalize(data["invalid_orders"], _invalid_orders),
    }


_VALIDATORS = {
    EventType.START_GAME: _simple_strings("scenario"),
    EventType.START_GAME_POWER_ASSIGNED: _start_game_power_assigned,
    EventType.START_SEASON: _start_season,
    EventType.FAMINE_SPAWN: _spawn,
    EventType.FAMINE_RELIEF: _simple_strings("player", "province"),
    EventType.FAMINE_ATTRITION: _loss,
    EventType.FAMINE_END: _province_list,
    EventType.PLAGUE_SPAWN: _spawn,
    EventType.PLAGUE_DEATH: _loss,
    EventType.REBELLION_PACIFY: _rebellion_pacify,
    EventType.REBELLION_PROVINCE: _simple_strings("player", "province"),
    EventType.REBELLION_CITY: _simple_strings("player", "province"),
    EventType.EXPENSE: _expense,
    EventType.EXPENSE_NO_FUNDS: _expense,
    EventType.EXPENSE_SYNTAX_ERROR: _expense,
    EventType.BRIBE_EXECUTED: _bribe_executed,
    EventType.INCOME_COLLECTED: _income_collected,
    EventType.MAINTENANCE_ORDER_RESOLVED: _maintenance_order,
    EventType.MAINTENANCE_SUMMARY: _maintenance_summary,
    EventType.GET_CONTROL: _control,
    EventType.LOSE_CONTROL: _control,
    EventType.GET_HOME_COUNTRY: _simple_strings("player", "home_country"),
    EventType.LOSE_HOME_COUNTRY: _simple_strings("player", "home_country"),
    EventType.PLAYER_ELIMINATED: _simple_strings("player"),
    EventType.PLAYER_WON: _player_won,
    EventType.MILITARY_RESOLUTION: _military_resolution,
    EventType.MILITARY_ORDERS_SUMMARY: _military_orders_summary,
    EventType.ASSASSINATION_ATTEMPT: _assassination_attempt,
}


__all__ = [
    "EventType",
    "FrozenJSONValue",
    "InvalidTurnEventError",
    "JSONValue",
    "TurnEvent",
]
