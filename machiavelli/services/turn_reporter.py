"""Informes legibles de eventos de turnos validados."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from discord.utils import escape_markdown, escape_mentions

from machiavelli.events import (
    EventType,
    FrozenJSONValue,
    InvalidTurnEventError,
    TurnEvent,
)
from machiavelli.game.tables import GameTables

if TYPE_CHECKING:
    from machiavelli.game.game import Game

logger = logging.getLogger(__name__)

type UnitKeyRecord = tuple[str | None, str, str]
type OutcomeRecord = tuple[UnitKeyRecord, str, str | None, bool]
type RebellionRecord = tuple[str | None, str, str, str]
type SiegeRecord = tuple[UnitKeyRecord, str, str]
type DislodgementRecord = tuple[UnitKeyRecord, str, str | None]

type MilitaryOrderRecord = tuple[
    UnitKeyRecord,
    str,
    str | None,
    tuple[str] | None,
    UnitKeyRecord | None,
    str | None,
    bool,
]
type InvalidOrderRecord = tuple[UnitKeyRecord, str]

_SEASONS = (
    "Primavera (mantenimiento)",
    "Primavera (campaña)",
    "Verano",
    "Otoño",
)
_MAINTENANCE_RESULTS = {
    "disbanded": "desbandada",
    "unit_not_found": "unidad no encontrada",
    "maintained": "mantenida",
    "disbanded_no_funds": "desbandada por falta de fondos",
    "recruited": "reclutada",
    "recruitment_no_funds": "reclutamiento rechazado por falta de fondos",
    "invalid_home_or_control": "reclutamiento fuera de territorio válido",
    "space_occupied": "espacio ocupado",
    "port_required": "puerto necesario",
    "rebelled_city": "ciudad rebelde",
    "fortified_city_required": "ciudad fortificada necesaria",
}
_REBELLION_KINDS = {"province": "provincial", "city": "urbana"}


class TurnReporter:
    """Genera el informe público del historial validado de un turno."""

    @staticmethod
    def generate(game: Game) -> list[str]:
        """Renderiza las cabeceras, los eventos y la situación actual sin mutar el
        estado.
        """
        scenario = game.require_scenario()
        game.require_map()
        year = scenario.year + (game.turn_number - 1) // 4
        previous_season = _SEASONS[(game.turn_number - 2) % 4]
        report = [
            f"## 📜 {TurnReporter._safe(game.name)}, turno {game.turn_number - 1}",
            f"### 🗓️ {previous_season} de {year}",
            "⚠️ **EVENTOS DEL TURNO ANTERIOR**",
        ]
        if game.turn_number > 1 and ((game.turn_number - 2) % 4) > 0:
            report.append("### 💰 **Gastos**")

        for event in game.turn_events:
            logger.debug("Events %s", game.turn_events)
            report.extend(TurnReporter._render_event(game, event))
        report.append("## 🗺️ REPORTE DE SITUACIÓN")

        if game.famine:
            names = [
                province.name
                for key, province in game.map.provinces.items()
                if key in game.famine
            ]
            famine = " y ".join([", ".join(names[:-1]), names[-1]])
            report.append(f"🌾 **Hambre:** {famine}")

        if game.independent_garrisons:
            names = [
                province.name
                for key, province in game.map.provinces.items()
                if key in game.independent_garrisons
            ]
            garrisons = (
                " y ".join([", ".join(names[:-1]), names[-1]])
                if len(names) > 1
                else names[0]
            )
            report.append(f"🛡️ **Guarniciones independientes:** {garrisons}")

        for player in game.players:
            report.extend(player.player_report())

        return report

    @staticmethod
    def _render_event(game: Game, event: TurnEvent) -> list[str]:
        data = event.data
        match event.type:
            case EventType.START_GAME:
                scenario = TurnReporter._safe(cast(str, data["scenario"]))
                return [f"Se inició la partida con el escenario {scenario}."]
            case EventType.START_GAME_POWER_ASSIGNED:
                logger.debug("data is %s", data)
                discord_id = cast(int | None, data["discord_id"])
                player = (
                    f"<@{discord_id}>"
                    if discord_id is not None
                    else TurnReporter._player(game, cast(str, data["player_id"]))
                )
                power = TurnReporter._power(game, cast(str, data["power_id"]))
                logger.debug(
                    "Se devolverá %s - %s",
                    player,
                    f"{player} recibió la potencia {power}.",
                )
                return [f"{player} recibió la potencia {power}."]
            case EventType.START_SEASON:
                season = _SEASONS[cast(int, data["season"])]
                return [f"### 🗓️ Comenzó {season} de {cast(int, data['year'])}."]
            case EventType.FAMINE_SPAWN:
                provinces = TurnReporter._locations(
                    game, cast(tuple[str, ...], data["provinces"])
                )
                severity = GameTables.disasters[cast(int, data["severity_roll"])][1]
                return [f"### 🌾 Hambre: {severity}.", f"> Apareció en {provinces}."]
            case EventType.FAMINE_RELIEF:
                player = TurnReporter._player(game, cast(str, data["player"]))
                province = TurnReporter._location(game, cast(str, data["province"]))
                return [f"{player} alivió el hambre en {province}."]
            case EventType.FAMINE_ATTRITION:
                player = TurnReporter._nullable_player(game, data["player"])
                units = TurnReporter._units(game, cast(tuple[str, ...], data["units"]))
                return [f"El hambre eliminó unidades de {player}: {units}."]
            case EventType.FAMINE_END:
                provinces = TurnReporter._locations(
                    game, cast(tuple[str, ...], data["provinces"])
                )
                return [f"Terminó el hambre en {provinces}."]
            case EventType.PLAGUE_SPAWN:
                provinces = TurnReporter._locations(
                    game, cast(tuple[str, ...], data["provinces"])
                )
                return [
                    f"La plaga apareció tras una tirada de "
                    f"{cast(int, data['severity_roll'])} en {provinces}."
                ]
            case EventType.PLAGUE_DEATH:
                player = TurnReporter._nullable_player(game, data["player"])
                units = TurnReporter._units(game, cast(tuple[str, ...], data["units"]))
                return [f"La plaga eliminó unidades de {player}: {units}."]
            case EventType.REBELLION_PACIFY:
                player = TurnReporter._player(game, cast(str, data["player"]))
                province = TurnReporter._location(game, cast(str, data["province"]))
                kind = _REBELLION_KINDS[cast(str, data["kind"])]
                return [f"{player} pacificó la rebelión {kind} de {province}."]
            case EventType.REBELLION_PROVINCE:
                return [TurnReporter._rebellion_line(game, data, "provincial")]
            case EventType.REBELLION_CITY:
                return [TurnReporter._rebellion_line(game, data, "urbana")]
            case EventType.EXPENSE:
                return [TurnReporter._expense_line(game, data, "registró")]
            case EventType.EXPENSE_NO_FUNDS:
                return [
                    TurnReporter._expense_line(
                        game, data, "no pudo pagar", include_amount=False
                    )
                ]
            case EventType.EXPENSE_SYNTAX_ERROR:
                return [
                    TurnReporter._expense_line(game, data, "presentó incorrectamente")
                ]
            case EventType.BRIBE_EXECUTED:
                return [TurnReporter._expense_line(game, data, "ejecutó")]
            case EventType.INCOME_COLLECTED:
                return TurnReporter._income_line(game, data)
            case EventType.MAINTENANCE_ORDER_RESOLVED:
                return [TurnReporter._maintenance_order_line(game, data)]
            case EventType.MAINTENANCE_SUMMARY:
                player = TurnReporter._player(game, cast(str, data["player"]))
                return [
                    f"Mantenimiento de {player}: "
                    f"{cast(int, data['initial_ducats'])} ducados iniciales, "
                    f"{cast(int, data['expenses'])} gastados y "
                    f"{cast(int, data['remaining_ducats'])} restantes."
                ]
            case EventType.GET_CONTROL:
                return [TurnReporter._control_line(game, data, gained=True)]
            case EventType.LOSE_CONTROL:
                return [TurnReporter._control_line(game, data, gained=False)]
            case EventType.GET_HOME_COUNTRY:
                return [TurnReporter._home_country_line(game, data, gained=True)]
            case EventType.LOSE_HOME_COUNTRY:
                return [TurnReporter._home_country_line(game, data, gained=False)]
            case EventType.PLAYER_ELIMINATED:
                player = TurnReporter._player(game, cast(str, data["player"]))
                return [f"{player} fue eliminado de la partida."]
            case EventType.PLAYER_WON:
                player = TurnReporter._player(game, cast(str, data["player"]))
                return [
                    f"{player} ganó con {cast(int, data['cities'])} ciudades y "
                    f"{cast(int, data['home_countries'])} países natales."
                ]
            case EventType.MILITARY_RESOLUTION:
                return TurnReporter._render_military(game, data)
            case EventType.MILITARY_ORDERS_SUMMARY:
                return TurnReporter._render_orders_summary(game, data)
            case _:
                raise InvalidTurnEventError(event_type=str(event.type))

    @staticmethod
    def _safe(value: str) -> str:
        return escape_mentions(escape_markdown(value, as_needed=False))

    @staticmethod
    def _player(game: Game, value: str) -> str:
        for player in game.players:
            power_name = GameTables.powers.get(player.power or "", player.power)
            if value in {player.player_id, player.power, power_name}:
                if player.discord_id is not None:
                    return " ".join((power_name, f"<@{player.discord_id}>"))
                if player.power is not None:
                    return TurnReporter._power(game, player.power)
                return TurnReporter._safe(player.player_id)
        return TurnReporter._safe(value)

    @staticmethod
    def _nullable_player(game: Game, value: FrozenJSONValue) -> str:
        if value is None:
            return "una guarnición independiente"
        return TurnReporter._player(game, cast(str, value))

    @staticmethod
    def _power(game: Game, value: str) -> str:
        scenario = game.require_scenario()
        if value in scenario.powers:
            return scenario.powers[value].name
        if value in GameTables.powers:
            return GameTables.powers[value]
        for power in scenario.powers.values():
            if power.name == value:
                return power.name
        return TurnReporter._safe(value)

    @staticmethod
    def _location(game: Game, value: str) -> str:
        base, separator, coast = value.partition(" ")
        game_map = game.require_map()
        location = game_map.provinces.get(base) or game_map.seas.get(base)
        if location is None:
            return TurnReporter._safe(value)
        if not separator:
            return location.name
        return f"{location.name} ({TurnReporter._safe(coast)})"

    @staticmethod
    def _locations(game: Game, values: tuple[str, ...]) -> str:
        locations = [TurnReporter._location(game, value) for value in values]
        return TurnReporter._join(locations)

    @staticmethod
    def _unit(game: Game, value: str) -> str:
        unit_type, separator, location = value.partition(" ")
        if not separator or unit_type not in {"A", "F", "G"}:
            return TurnReporter._safe(value)
        actor = GameTables.actors[unit_type]
        return f"{actor} de {TurnReporter._location(game, location)}"

    @staticmethod
    def _units(game: Game, values: tuple[str, ...]) -> str:
        return TurnReporter._join([TurnReporter._unit(game, value) for value in values])

    @staticmethod
    def _join(values: list[str]) -> str:
        if not values:
            return "ninguno"
        if len(values) == 1:
            return values[0]
        return " y ".join([", ".join(values[:-1]), values[-1]])

    @staticmethod
    def _rebellion_line(
        game: Game,
        data: Mapping[str, FrozenJSONValue],
        kind: str,
    ) -> str:
        player = TurnReporter._player(game, cast(str, data["player"]))
        province = TurnReporter._location(game, cast(str, data["province"]))
        return f"Comenzó una rebelión {kind} de {province} contra {player}."

    @staticmethod
    def _expense_name(value: str) -> str:
        expense = GameTables.expenses.get(value)
        return expense["text"] if expense is not None else TurnReporter._safe(value)

    @staticmethod
    def _expense_target(
        game: Game,
        expense_code: str,
        value: FrozenJSONValue,
    ) -> str:
        if value is None:
            return ""
        target = cast(str, value)
        expense = GameTables.expenses.get(expense_code)
        target_type = expense["target_type"] if expense is not None else None
        if target_type == "power":
            rendered = TurnReporter._power(game, target)
        elif target_type == "unit":
            rendered = TurnReporter._unit(game, target)
        elif target_type == "province":
            rendered = TurnReporter._location(game, target)
        elif target.partition(" ")[0] in {"A", "F", "G"}:
            rendered = TurnReporter._unit(game, target)
        else:
            rendered = TurnReporter._location(game, target)
        return f" sobre {rendered}"

    @staticmethod
    def _expense_line(
        game: Game,
        data: Mapping[str, FrozenJSONValue],
        action: str,
        *,
        include_amount: bool = True,
    ) -> str:
        player = TurnReporter._player(game, cast(str, data["player"]))
        expense_code = cast(str, data["expense"])
        expense = TurnReporter._expense_name(expense_code)
        target = TurnReporter._expense_target(game, expense_code, data["target"])
        raw_amount = data["amount"]
        rendered_amount = (
            str(raw_amount)
            if isinstance(raw_amount, int)
            else TurnReporter._safe(cast(str, raw_amount))
        )
        amount = f" por {rendered_amount} ducados" if include_amount else ""
        return f"> {player} {action} {expense}{target}{amount}."

    @staticmethod
    def _income_line(game: Game, data: Mapping[str, FrozenJSONValue]) -> list[str]:
        player = TurnReporter._player(game, cast(str, data["player"]))
        provinces = TurnReporter._locations(
            game, cast(tuple[str, ...], data["provinces"])
        )
        cities = TurnReporter._locations(game, cast(tuple[str, ...], data["cities"]))
        variable_values = cast(
            tuple[Mapping[str, FrozenJSONValue], ...], data["variable_income"]
        )
        variable = TurnReporter._join(
            [TurnReporter._variable_income(game, item) for item in variable_values]
        )
        amount = sum(var["amount"] for var in variable_values)
        return [
            f"💰 __**{player}**__",
            f"> **Provincias ({cast(int, data['province_income'])}):** {provinces}",
            f"> **Ciudades ({cast(int, data['city_income'])}):** {cities}",
            f"> **Variable ({amount}):** {variable}",
            f"> **Total:** {cast(int, data['total_income'])} ducados",
        ]

    @staticmethod
    def _variable_income(game: Game, data: Mapping[str, FrozenJSONValue]) -> str:
        source = cast(str, data["source"])
        if data["source_type"] == "home_country":
            source_name, source_type = TurnReporter._power(game, source), "país"
        else:
            source_name, source_type = TurnReporter._location(game, source), "provincia"
        return f"{source_name} ({source_type}), {cast(int, data['amount'])} ducados"

    @staticmethod
    def _maintenance_order_line(game: Game, data: Mapping[str, FrozenJSONValue]) -> str:
        player = TurnReporter._player(game, cast(str, data["player"]))
        actor = TurnReporter._unit(game, cast(str, data["actor"]))
        order_code = cast(str, data["order"])
        order = GameTables.maintenance_orders[order_code]["text"]
        target = TurnReporter._expense_target(game, order_code, data["target"])
        result_code = cast(str, data["result"])
        result = _MAINTENANCE_RESULTS[result_code]
        return (
            f"Mantenimiento de {player}: {actor}, orden {order}{target}, "
            f"resultado {result}, coste {cast(int, data['cost'])} ducados."
        )

    @staticmethod
    def _control_line(
        game: Game,
        data: Mapping[str, FrozenJSONValue],
        *,
        gained: bool,
    ) -> str:
        player = TurnReporter._player(game, cast(str, data["player"]))
        provinces = TurnReporter._locations(
            game, cast(tuple[str, ...], data["provinces"])
        )
        action = "obtuvo el control de" if gained else "perdió el control de"
        return f"> {player} {action} {provinces}."

    @staticmethod
    def _home_country_line(
        game: Game,
        data: Mapping[str, FrozenJSONValue],
        *,
        gained: bool,
    ) -> str:
        player = TurnReporter._player(game, cast(str, data["player"]))
        home_country = TurnReporter._power(game, cast(str, data["home_country"]))
        action = "obtuvo" if gained else "perdió"
        return f"> {player} {action} el país natal {home_country}."

    @staticmethod
    def _render_orders_summary(
        game: Game, data: Mapping[str, FrozenJSONValue]
    ) -> list[str]:
        orders = cast(tuple[MilitaryOrderRecord, ...], data["orders"])
        invalid_orders = cast(tuple[InvalidOrderRecord, ...], data["invalid_orders"])
        logger.debug(data)
        if not any((orders, invalid_orders)):
            return "Sin órdenes militares."

        lines: list[str] = []
        lines.append("### :scroll: **Órdenes recibidas:**")

        for player in game.players:
            player_orders = [
                order for order in orders if order[0][0] == player.player_id
            ]
            player_invalid_orders = [
                invalid_order
                for invalid_order in invalid_orders
                if invalid_order[0][0] == player.player_id
            ]
            if player_orders or player_invalid_orders:
                player_txt = TurnReporter._player(game, player.player_id)
                lines.append(f"🏰 __**{player_txt}**__")
                if player_orders:
                    lines.extend(
                        TurnReporter._military_order_line(game, order)
                        for order in player_orders
                    )
                if invalid_orders:
                    lines.extend(
                        TurnReporter._invalid_order_line(game, invalid_order)
                        for invalid_order in player_invalid_orders
                    )
        return lines

    @staticmethod
    def _render_military(
        game: Game,
        data: Mapping[str, FrozenJSONValue],
    ) -> list[str]:
        outcomes = cast(tuple[OutcomeRecord, ...], data["outcomes"])
        cancelled = cast(tuple[UnitKeyRecord, ...], data["cancelled_orders"])
        broken_convoys = cast(tuple[UnitKeyRecord, ...], data["broken_convoys"])
        dislodgements = cast(tuple[UnitKeyRecord, ...], data["dislodgements"])
        rebellions = cast(tuple[RebellionRecord, ...], data["rebellions"])
        sieges = cast(tuple[SiegeRecord, ...], data["sieges"])
        decisions = cast(tuple[DislodgementRecord, ...], data["decisions"])
        if not any(
            (
                outcomes,
                cancelled,
                broken_convoys,
                dislodgements,
                rebellions,
                sieges,
                decisions,
            )
        ):
            return ["Sin cambios militares."]

        lines: list[str] = []

        lines.append("### :crossed_swords: **Resultados militares:**")

        if rebellions:
            lines.append("### :fire: **Rebeliones:**")
            lines.extend(
                TurnReporter._military_rebellion_line(game, rebellion)
                for rebellion in rebellions
            )

        for player in game.players:
            player_outcomes = [
                outcome for outcome in outcomes if outcome[0][0] == player.player_id
            ]
            player_cancelled = [c for c in cancelled if c[0] == player.player_id]
            player_broken_convoys = [
                convoy for convoy in broken_convoys if convoy[0] == player.player_id
            ]
            player_dislodgements = [
                dislodgement
                for dislodgement in dislodgements
                if dislodgement[0] == player.player_id
            ]
            player_sieges = [
                siege for siege in sieges if siege[0][0] == player.player_id
            ]
            player_decisions = [
                decision for decision in decisions if decision[0][0] == player.player_id
            ]
            if not any(
                (
                    player_outcomes,
                    player_cancelled,
                    player_broken_convoys,
                    player_dislodgements,
                    player_sieges,
                    player_decisions,
                )
            ):
                continue

            player_txt = TurnReporter._player(game, player.player_id)
            lines.append(f"🏰 __**{player_txt}**__")
            if player_outcomes:
                lines.append("> :crossed_swords: **Resultados**")
                lines.extend(
                    TurnReporter._military_outcome_line(game, outcome)
                    for outcome in player_outcomes
                )
            if player_cancelled:
                lines.append("> :exclamation: **Órdenes canceladas:**")
                lines.extend(
                    f"> {TurnReporter._military_unit(game, unit, False)}."
                    for unit in player_cancelled
                )
            if player_broken_convoys:
                lines.append("> :broken_chain: **Transportes rotos:**")
                lines.extend(
                    f"> {TurnReporter._military_unit(game, unit, False)}."
                    for unit in player_broken_convoys
                )
            if player_dislodgements:
                lines.append("> ### :flag_white: **Desalojos:**")
                lines.extend(
                    f"> {TurnReporter._military_unit(game, unit)}."
                    for unit in player_dislodgements
                )
            if player_sieges:
                lines.append("> ### :shield: **Asedios:**")
                lines.extend(
                    TurnReporter._military_siege_line(game, siege)
                    for siege in player_sieges
                )
            if player_decisions:
                lines.append("> ### :dash: **Retiradas:**")
                lines.extend(
                    TurnReporter._military_dislodgement_line(game, decision)
                    for decision in player_decisions
                )

        lines.append("### FIN DEL REPORTE MILITAR")

        return lines

    @staticmethod
    def _military_unit(game: Game, unit: UnitKeyRecord, show_owner: bool = True) -> str:
        owner, unit_type, origin = unit
        actor = GameTables.actors[unit_type]
        location = TurnReporter._location(game, origin)
        if owner is None:
            return f"{actor} independiente de {location}"
        player = TurnReporter._player(game, owner)
        if show_owner:
            return f"{actor} de {location} de {player}"
        else:
            return f"{actor} de {location}"

    @staticmethod
    def _military_order_line(game: Game, order: MilitaryOrderRecord) -> str:
        (
            unit,
            order_type,
            target_location,
            path,
            transported_unit,
            supported_faction,
            is_convoy,
        ) = order
        unit_text = TurnReporter._military_unit(game, unit, False)
        order_type_texts = {
            "A": "avanzar",
            "B": "asediar",
            "H": "mantener",
            "L": "levantar asedio",
            "S": "apoyar",
            "T": "transportar",
            "C": "convertir",
        }
        order_type_text = (
            order_type_texts[order_type] if order_type in order_type_texts else None
        )

        if target_location and target_location in game.map.locations:
            target_location_name = game.map.locations[target_location].name
        else:
            target_location_name = None

        if is_convoy and path and target_location_name:
            path_names = [
                game.map.locations[location].name
                for location in path
                if location in game.map.locations
            ]
            path_names_text = TurnReporter._join(path_names)

        if supported_faction and supported_faction in GameTables.powers:
            supported_faction_text = GameTables.powers[supported_faction]
        else:
            supported_faction_text = None

        if transported_unit:
            transported_unit_text = TurnReporter._military_unit(
                game, transported_unit, False
            )
        else:
            transported_unit_text = None

        order_description = None
        if order_type == "A":
            if is_convoy and path and target_location_name:
                order_description = (
                    f"{order_type_text} a {target_location_name} "
                    f"(via {path_names_text})"
                )
            else:
                order_description = f"{order_type_text} a {target_location_name}"
        elif order_type in ("B", "H", "L"):
            order_description = f"{order_type_text}"
        elif order_type == "S":
            if supported_faction_text:
                order_description = (
                    f"{order_type_text} a {target_location_name} "
                    f"({supported_faction_text})"
                )
            else:
                order_description = f"{order_type_text} a {target_location_name}"
        elif order_type == "T" and transported_unit_text:
            order_description = f"{order_type_text} a {transported_unit_text}"
        elif order_type == "C":
            target_unit_txt = (
                GameTables.actors[target_location]
                if target_location in GameTables.actors
                else None
            )
            order_description = f"{order_type_text} a {target_unit_txt}"

        if unit_text and order_description:
            return f"> {unit_text} {order_description}"

    @staticmethod
    def _invalid_order_line(game, invalid_order: InvalidOrderRecord) -> str:
        unit, reason = invalid_order
        unit_txt = TurnReporter._military_unit(game, unit)
        return f"> :exclamation: {unit_txt}, {reason}"

    @staticmethod
    def _military_outcome_line(game: Game, outcome: OutcomeRecord) -> str:
        unit, final_type, final_location, dislodged, attack_origin = outcome
        original = TurnReporter._military_unit(game, unit, False)
        final_actor = GameTables.actors[final_type]
        destination = (
            f"en {TurnReporter._location(game, final_location)}"
            if final_location is not None
            else "sin destino"
        )
        attack_origin_text = (
            f"desde {TurnReporter._location(game, attack_origin)}"
            if attack_origin
            else None
        )
        dislodged_text = (
            attack_origin_text if attack_origin_text else "sí" if dislodged else "no"
        )
        return (
            f"> {original} ➔ {final_actor} {destination}. Desalojada: {dislodged_text}."
        )

    @staticmethod
    def _military_dislodgement_line(game: Game, decision: DislodgementRecord) -> str:
        unit, result_type, destination = decision
        original = TurnReporter._military_unit(game, unit)
        final_destination = (
            TurnReporter._location(game, destination) if destination else None
        )
        if result_type == "retreat":
            return f"> {original} se retiró a {final_destination}."
        elif result_type == "garrison":
            return f"> {original} se refugió en la fortaleza de {final_destination}."
        else:  # disbanded
            return f"> {original} no pudo retirarse y se desbandó."

    @staticmethod
    def _military_rebellion_line(game: Game, rebellion: RebellionRecord) -> str:
        player_id, kind, province_id, state = rebellion
        player = TurnReporter._nullable_player(game, player_id)
        kind_name = _REBELLION_KINDS[kind]
        province = TurnReporter._location(game, province_id)
        state_name = {"subdued": "sometida", "liberated": "liberada"}[state]
        return f"> Rebelión {kind_name} de {province} para {player}: {state_name}."

    @staticmethod
    def _military_siege_line(game: Game, siege: SiegeRecord) -> str:
        unit, province_id, state = siege
        rendered_unit = TurnReporter._military_unit(game, unit, False)
        province = TurnReporter._location(game, province_id)
        state_name = {
            "started": "iniciado",
            "completed": "completado",
            "lifted": "levantado",
        }[state]
        return f"> {rendered_unit} en {province}: {state_name}."


__all__ = ["TurnReporter"]
