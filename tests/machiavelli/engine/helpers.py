"""Constructores y snapshots reutilizables para pruebas militares deterministas."""

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from random import Random
from unittest.mock import Mock

from machiavelli.engine.core import GameEngine
from machiavelli.game.command import Command
from machiavelli.game.events import EventType, TurnEvent
from machiavelli.game.game import Game
from machiavelli.game.map import Map
from machiavelli.game.player import Player
from machiavelli.game.scenario import Scenario


@dataclass(frozen=True, slots=True)
class MilitaryOrdering:
    """Una permutación incidental acotada para escenarios militares."""

    reverse_players: bool = False
    reverse_collections: bool = False


MILITARY_ORDERINGS = (
    MilitaryOrdering(),
    MilitaryOrdering(reverse_players=True),
    MilitaryOrdering(reverse_collections=True),
    MilitaryOrdering(reverse_players=True, reverse_collections=True),
)


def create_mock_player(
    player_id: str,
    armies: list[str] | None = None,
    fleets: list[str] | None = None,
    garrisons: list[str] | None = None,
    controlled_locations: list[str] | None = None,
    home_countries: list[str] | None = None,
    rebelled_provinces: list[str] | None = None,
    rebelled_cities: list[str] | None = None,
    discord_id: int | None = 0,
    power: str | None = None,
) -> Mock:
    """Crea un Mock con la especificación de Player."""
    player = Mock(spec=Player)
    player.player_id = player_id
    player.armies = armies if armies is not None else []
    player.fleets = fleets if fleets is not None else []
    player.garrisons = garrisons if garrisons is not None else []
    player.controlled_locations = (
        controlled_locations if controlled_locations is not None else []
    )
    player.home_countries = home_countries if home_countries is not None else []
    player.rebelled_provinces = (
        rebelled_provinces if rebelled_provinces is not None else []
    )
    player.rebelled_cities = rebelled_cities if rebelled_cities is not None else []
    player.discord_id = discord_id
    player.power = power
    return player


def create_mock_game(
    players: list[Mock] | None = None,
    independent_garrisons: list[str] | None = None,
    famine: list[str] | None = None,
    provinces: set[str] | None = None,
    turn_number: int = 0,
    scenario: Mock | None = "default",
    scenario_id: str | None = "scenario_1",
) -> Mock:
    """Crea un Mock con la especificación de Game y atributos por defecto."""
    game = Mock(spec=Game)
    game.players = players if players is not None else []
    game.independent_garrisons = (
        independent_garrisons if independent_garrisons is not None else []
    )
    game.famine = famine if famine is not None else []
    game.turn_number = turn_number
    game.scenario_id = scenario_id

    if scenario == "default":
        game.scenario = Mock(powers={})
    else:
        game.scenario = scenario

    if provinces is not None:
        game.map.provinces = provinces

    return game


def create_test_engine(game: Mock | None = None, seed: int = 42) -> GameEngine:
    """Instancia un GameEngine con una semilla fija o un Game personalizado."""
    if game is None:
        game = create_mock_game()
    return GameEngine(game=game, rng=Random(seed))


def create_military_game(
    game_map: Map,
    players: Sequence[Mapping[str, object] | Player | str]
    | Mapping[str, Mapping[str, object] | Player]
    | None = None,
    *,
    orders: Mapping[str, Iterable[Command | Mapping[str, object] | Sequence[str]]]
    | Iterable[Command | Mapping[str, object] | Sequence[str]]
    | None = None,
    independent_garrisons: Iterable[str] = (),
    besieges: Iterable[str] = (),
    turn_events: Iterable[TurnEvent] = (),
    name: str = "military-test",
    channel_id: int | None = None,
    scenario: Scenario | None = None,
) -> Game:
    """Crea un ``Game`` fresco con jugadores, unidades y órdenes reales."""
    game = Game(
        name=name,
        channel_id=channel_id,
        scenario=scenario,
        map=game_map,
        independent_garrisons=list(independent_garrisons),
        besieges=list(besieges),
        turn_events=list(turn_events),
    )

    if players is None:
        player_specs = []
    elif isinstance(players, Mapping):
        player_specs = [
            {**value, "player_id": player_id} if isinstance(value, Mapping) else value
            for player_id, value in players.items()
        ]
    else:
        player_specs = list(players)

    game.players = [_create_military_player(game, spec) for spec in player_specs]
    explicit_orders = _orders_by_player(orders, game.players)

    for player, spec in zip(game.players, player_specs, strict=True):
        player_orders = explicit_orders.get(player.player_id)
        if player_orders is None:
            player_orders = _spec_value(
                spec, "orders", _spec_value(spec, "commands", ())
            )
        player.commands = [
            _create_command(game, player, order) for order in player_orders
        ]

    return game


def military_snapshot(game: Game) -> tuple[tuple[object, ...], ...]:
    """Devuelve el estado militar, sin los eventos de recepción de órdenes.

    Los resúmenes de órdenes son auditoría de entrada: se publican antes de que la
    resolución alcance una frontera de commit. No forman parte del estado que las
    pruebas de atomicidad deben comparar.
    """
    armies = _snapshot_units(game, "armies")
    fleets = _snapshot_units(game, "fleets")
    garrisons = _snapshot_units(game, "garrisons")
    rebellions = tuple(
        sorted(
            (player.player_id, kind, location)
            for player in game.players
            for kind, attr in (
                ("province", "rebelled_provinces"),
                ("city", "rebelled_cities"),
            )
            for location in getattr(player, attr)
        )
    )
    return (
        armies,
        fleets,
        garrisons,
        tuple(sorted(game.independent_garrisons)),
        tuple(sorted(game.besieges)),
        rebellions,
        tuple(
            event
            for event in game.turn_events
            if event.type is not EventType.MILITARY_ORDERS_SUMMARY
        ),
    )


def iter_military_orderings(factory: Callable[[], Game]) -> Iterator[Game]:
    """Crea un juego fresco por variante sin reordenar órdenes de un mismo actor."""
    for ordering in MILITARY_ORDERINGS:
        game = factory()
        if ordering.reverse_players:
            game.players.reverse()
        if ordering.reverse_collections:
            for player in game.players:
                for attr in ("armies", "fleets", "garrisons"):
                    setattr(player, attr, list(reversed(getattr(player, attr))))
            game.independent_garrisons.reverse()
            game.besieges.reverse()
        yield game


def _snapshot_units(game: Game, attr: str) -> tuple[tuple[str, str], ...]:
    """Normaliza unidades para comparar sin depender del orden."""
    return tuple(
        sorted(
            (player.player_id, location)
            for player in game.players
            for location in getattr(player, attr)
        )
    )


def _spec_value(spec: object, name: str, default: object) -> object:
    """Lee un campo tanto de una especificación mapping como de un objeto."""
    if isinstance(spec, Mapping):
        return spec.get(name, default)
    return getattr(spec, name, default)


def _create_military_player(
    game: Game, spec: Mapping[str, object] | Player | str
) -> Player:
    """Crea un jugador real y copia sus colecciones para aislar cada prueba."""
    if isinstance(spec, str):
        spec = {"player_id": spec}
    elif isinstance(spec, Player):
        spec = {
            name: getattr(spec, name)
            for name in (
                "player_id",
                "discord_id",
                "controlled_locations",
                "armies",
                "fleets",
                "garrisons",
                "ass_counters",
                "ducats",
                "rebelled_provinces",
                "rebelled_cities",
                "home_countries",
                "power",
            )
        }
    if not isinstance(spec, Mapping) or "player_id" not in spec:
        raise TypeError("Cada jugador militar necesita player_id")

    return Player(
        game=game,
        player_id=spec["player_id"],
        discord_id=spec.get("discord_id"),
        controlled_locations=list(spec.get("controlled_locations", ())),
        armies=list(spec.get("armies", ())),
        fleets=list(spec.get("fleets", ())),
        garrisons=list(spec.get("garrisons", ())),
        ass_counters=list(spec.get("ass_counters", ())),
        ducats=spec.get("ducats", 0),
        rebelled_provinces=list(spec.get("rebelled_provinces", ())),
        rebelled_cities=list(spec.get("rebelled_cities", ())),
        home_countries=list(spec.get("home_countries", ())),
        power=spec.get("power"),
    )


def _orders_by_player(orders: object, players: list[Player]) -> dict[str, list[object]]:
    """Normaliza órdenes globales o agrupadas por jugador."""
    if orders is None:
        return {}
    if isinstance(orders, Mapping):
        return {
            getattr(player_id, "player_id", player_id): list(player_orders)
            for player_id, player_orders in orders.items()
        }
    if len(players) != 1:
        raise ValueError("Las órdenes globales requieren un único jugador")
    return {players[0].player_id: list(orders)}


def _create_command(
    game: Game,
    player: Player,
    order: Command | Mapping[str, object] | Sequence[str],
) -> Command:
    """Convierte las formas aceptadas de orden en un Command ligado al juego."""
    if isinstance(order, Command):
        values = (order.actor, order.command, order.target)
    elif isinstance(order, Mapping):
        values = (order["actor"], order["command"], order.get("target"))
    else:
        if len(order) != 3:
            raise ValueError("Cada orden necesita actor, command y target")
        values = tuple(order)
    return Command(game, player, *values)
