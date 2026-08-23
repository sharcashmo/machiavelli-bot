"""Pruebas de informes de turnos legibles y con contexto."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import patch

import pytest

from machiavelli.game.events import EventType, JSONValue, TurnEvent
from machiavelli.game.game import Game
from machiavelli.game.map import Map
from machiavelli.game.scenario import Scenario
from machiavelli.game.trading import ExchangeProposal, TradeResource
from machiavelli.services.turn_reporter import TurnReporter


def make_report_game() -> Game:
    """Construye una partida cargada con identificadores públicos conocidos."""
    scenario = Scenario.load_scenarios()["Be"]
    game = Game(
        name="Partida de prueba",
        channel_id=123,
        scenario_id="Be",
        scenario=scenario,
        map=Map.load_map(exclude_ids=scenario.excluded_locations),
        turn_number=2,
    )
    first = game.add_player("player-1", 123)
    first.power = "M"
    second = game.add_player("player-2", 456)
    second.power = "V"
    return game


def event_lines(report: list[str]) -> list[str]:
    """Devuelve únicamente las líneas generadas a partir de los eventos del turno."""
    situation_index = report.index("## 🗺️ REPORTE DE SITUACIÓN")
    events_index = report.index("⚠️ **EVENTOS DEL TURNO ANTERIOR**")
    return report[events_index + 1 : situation_index]


@pytest.mark.parametrize("event_type", list(EventType))
def test_every_event_type_has_a_non_empty_spanish_representation(
    event_type: EventType,
    valid_event_payloads: Mapping[EventType, dict[str, JSONValue]],
) -> None:
    game = make_report_game()
    event = TurnEvent(event_type, valid_event_payloads[event_type])
    game.turn_events = [event]

    report = TurnReporter.generate(game)
    rendered = event_lines(report)

    assert rendered
    assert all(line.strip() for line in report)
    assert event_type.value not in "\n".join(rendered)
    assert event.to_json() not in "\n".join(report)
    assert "TurnEvent" not in "\n".join(report)
    assert "mappingproxy" not in "\n".join(report)


def test_report_preserves_general_order_event_order_and_repetitions() -> None:
    game = make_report_game()
    repeated = TurnEvent(EventType.START_GAME, {"scenario": "Be"})
    eliminated = TurnEvent(EventType.PLAYER_ELIMINATED, {"player": "player-2"})
    game.turn_events = [repeated, eliminated, repeated]

    report = TurnReporter.generate(game)
    rendered = event_lines(report)

    assert report[:3] == [
        "## 📜 Partida de prueba, turno 1",
        "### 🗓️ Primavera (mantenimiento) de 1454",
        "⚠️ **EVENTOS DEL TURNO ANTERIOR**",
    ]
    assert rendered == [
        "> Se inició la partida con el escenario The balance of power (six players).",
        "> Venice <@456> fue eliminado de la partida.",
        "> Se inició la partida con el escenario The balance of power (six players).",
    ]


def test_report_resolves_known_player_power_province_and_unit_identifiers() -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.START_GAME_POWER_ASSIGNED,
            {"player_id": "player-1", "discord_id": 123, "power_id": "M"},
        ),
        TurnEvent(
            EventType.FAMINE_RELIEF,
            {"player": "player-1", "province": "milan"},
        ),
        TurnEvent(
            EventType.FAMINE_ATTRITION,
            {"player": "player-2", "units": ["F venic S"]},
        ),
    ]

    rendered = "\n".join(event_lines(TurnReporter.generate(game)))

    assert "Milan <@123>" in rendered
    assert "Venice <@456>" in rendered
    assert "Milan" in rendered
    assert "Flota" in rendered
    assert "Venice (S)" in rendered


@pytest.mark.parametrize(
    ("persisted_discord_id", "expected_player"),
    [(999, "<@999>"), (123, "<@123>")],
)
def test_power_assignment_prefers_persisted_discord_id_with_safe_fallback(
    persisted_discord_id: int | None,
    expected_player: str,
) -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.START_GAME_POWER_ASSIGNED,
            {
                "player_id": "player-1",
                "discord_id": persisted_discord_id,
                "power_id": "M",
            },
        )
    ]

    assert event_lines(TurnReporter.generate(game)) == [
        f"> {expected_player} recibió la potencia Milan."
    ]


def test_power_expense_resolves_its_target_as_a_power() -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.EXPENSE,
            {"player": "player-1", "expense": "E", "target": "V", "amount": 12},
        )
    ]

    assert event_lines(TurnReporter.generate(game)) == [
        "> Milan <@123> registró Ordenar asesinato sobre Venice por 12 ducados."
    ]


def test_unknown_identifiers_escape_markdown_then_mentions() -> None:
    game = make_report_game()
    unknown_player = "@everyone_*`|\\"
    unknown_province = "<@123>_**"
    game.turn_events = [
        TurnEvent(
            EventType.FAMINE_RELIEF,
            {"player": unknown_player, "province": unknown_province},
        )
    ]
    calls: list[tuple[object, ...]] = []

    def fake_markdown(value: str, *, as_needed: bool) -> str:
        calls.append(("markdown", value, as_needed))
        return f"md-{len(calls)}"

    def fake_mentions(value: str) -> str:
        calls.append(("mentions", value))
        return f"safe-{len(calls)}"

    with (
        patch(
            "machiavelli.services.turn_reporter.escape_markdown",
            side_effect=fake_markdown,
        ),
        patch(
            "machiavelli.services.turn_reporter.escape_mentions",
            side_effect=fake_mentions,
        ),
    ):
        rendered = TurnReporter._render_event(game, game.turn_events[0])

    assert calls == [
        ("markdown", unknown_player, False),
        ("mentions", "md-1"),
        ("markdown", unknown_province, False),
        ("mentions", "md-3"),
    ]
    assert rendered == ["safe-2 alivió el hambre en safe-4."]


def test_generate_does_not_mutate_game_or_events() -> None:
    game = make_report_game()
    game.famine = ["milan"]
    game.turn_events = [
        TurnEvent(EventType.FAMINE_END, {"provinces": ["milan"]}),
        TurnEvent(EventType.FAMINE_END, {"provinces": ["venic"]}),
    ]
    event_snapshot = [(id(event), event.to_json()) for event in game.turn_events]
    player_snapshot = [
        (
            player.player_id,
            player.power,
            tuple(player.controlled_locations),
            tuple(player.commands),
        )
        for player in game.players
    ]
    famine_snapshot = tuple(game.famine)

    TurnReporter.generate(game)

    current_events = [(id(event), event.to_json()) for event in game.turn_events]
    assert current_events == event_snapshot
    assert [
        (
            player.player_id,
            player.power,
            tuple(player.controlled_locations),
            tuple(player.commands),
        )
        for player in game.players
    ] == player_snapshot
    assert tuple(game.famine) == famine_snapshot


def test_pending_exchange_does_not_change_public_turn_report() -> None:
    game = make_report_game()
    game.turn_events = [TurnEvent(EventType.START_GAME, {"scenario": "Be"})]
    first, second = game.players
    first.home_countries = ["M"]
    first.ducats = 37
    first.ass_counters = ["V"]
    second.home_countries = ["V"]
    second.ducats = 11
    second.ass_counters = ["M"]

    before = TurnReporter.generate(game)

    game.pending_exchanges = [
        ExchangeProposal(
            "M",
            "V",
            TradeResource("ducats", 987654),
            TradeResource("assassin", "N"),
        )
    ]

    after = TurnReporter.generate(game)
    rendered = "\n".join(after)

    assert after == before
    assert all(
        message not in rendered
        for message in (
            "Intercambio propuesto",
            "Intercambio completado",
            "Has dado",
        )
    )


def test_military_orders_are_grouped_by_player_with_their_presentation() -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.MILITARY_ORDERS_SUMMARY,
            {
                "orders": [
                    [["player-2", "F", "venic S"], "H", None, None, None, None, False],
                    [["player-1", "A", "milan"], "B", None, None, None, None, False],
                ],
                "invalid_orders": [
                    [["player-1", "A", "milan"], "Orden inválida"],
                ],
            },
        )
    ]

    assert event_lines(TurnReporter.generate(game)) == [
        "### :scroll: **Órdenes recibidas:**",
        "🏰 __**Milan <@123>**__",
        "> Ejército de Milan asediar",
        "> :exclamation: Ejército de Milan de Milan <@123>, Orden inválida",
        "🏰 __**Venice <@456>**__",
        "> Flota de Venice (S) mantener",
    ]


def test_military_resolution_renders_every_item_in_group_order() -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.MILITARY_RESOLUTION,
            {
                "outcomes": [
                    [["player-1", "F", "venic S"], "G", "milan", False, None],
                    [["player-2", "A", "pavia"], "A", None, True, "milan"],
                ],
                "cancelled_orders": [["player-2", "A", "pavia"]],
                "broken_convoys": [["player-1", "G", "milan"]],
                "decisions": [
                    [["player-1", "F", "venic S"], "retreat", "milan"],
                    [["player-2", "A", "pavia"], "disband", None],
                ],
                "dislodgements": [["player-2", "F", "venic S"]],
                "rebellions": [["player-1", "city", "venic", "liberated"]],
                "sieges": [[["player-2", "A", "pavia"], "milan", "completed"]],
            },
        )
    ]

    rendered = event_lines(TurnReporter.generate(game))

    assert rendered == [
        "### :crossed_swords: **Resultados militares:**",
        "### :fire: **Rebeliones:**",
        "> Rebelión urbana de Venice para Milan <@123>: liberada.",
        "🏰 __**Milan <@123>**__",
        "> :crossed_swords: **Resultados**",
        "> Flota de Venice (S) ➔ Guarnición en Milan. Desalojada: no.",
        "> :broken_chain: **Transportes rotos:**",
        "> Guarnición de Milan.",
        "> ### :dash: **Retiradas:**",
        "> Flota de Venice (S) de Milan <@123> se retiró a Milan.",
        "🏰 __**Venice <@456>**__",
        "> :crossed_swords: **Resultados**",
        "> Ejército de Pavia ➔ Ejército sin destino. Desalojada: desde Milan.",
        "> :exclamation: **Órdenes canceladas:**",
        "> Ejército de Pavia.",
        "> ### :flag_white: **Desalojos:**",
        "> Flota de Venice (S) de Venice <@456>.",
        "> ### :shield: **Asedios:**",
        "> Ejército de Pavia en Milan: completado.",
        "> ### :dash: **Retiradas:**",
        "> Ejército de Pavia de Venice <@456> no pudo retirarse y se desbandó.",
        "### FIN DEL REPORTE MILITAR",
    ]


def test_military_resolution_omits_only_empty_groups() -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.MILITARY_RESOLUTION,
            {
                "outcomes": [[["player-1", "A", "milan"], "A", "pavia", False, None]],
                "cancelled_orders": [],
                "broken_convoys": [],
                "dislodgements": [],
                "rebellions": [],
                "sieges": [[["player-2", "F", "venic S"], "milan", "started"]],
                "decisions": [],
            },
        )
    ]

    rendered = event_lines(TurnReporter.generate(game))

    assert rendered == [
        "### :crossed_swords: **Resultados militares:**",
        "🏰 __**Milan <@123>**__",
        "> :crossed_swords: **Resultados**",
        "> Ejército de Milan ➔ Ejército en Pavia. Desalojada: no.",
        "🏰 __**Venice <@456>**__",
        "> ### :shield: **Asedios:**",
        "> Flota de Venice (S) en Milan: iniciado.",
        "### FIN DEL REPORTE MILITAR",
    ]
    assert not {
        "> :exclamation: **Órdenes canceladas:**",
        "> :broken_chain: **Transportes rotos:**",
        "> ### :flag_white: **Desalojos:**",
        "### :fire: **Rebeliones:**",
        "> ### :dash: **Retiradas:**",
    }.intersection(rendered)


def test_empty_military_resolution_has_exactly_one_line() -> None:
    game = make_report_game()
    game.turn_events = [
        TurnEvent(
            EventType.MILITARY_RESOLUTION,
            {
                "outcomes": [],
                "cancelled_orders": [],
                "broken_convoys": [],
                "decisions": [],
                "dislodgements": [],
                "rebellions": [],
                "sieges": [],
            },
        )
    ]

    assert event_lines(TurnReporter.generate(game)) == ["Sin cambios militares."]
