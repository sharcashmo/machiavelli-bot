"""Pruebas de contrato para el procesamiento centralizado de órdenes."""

from __future__ import annotations

import pytest

from machiavelli.engine.exceptions import TooManyExpenses
from machiavelli.engine.orders import OrderProcessor
from machiavelli.game import (
    DuplicatedGameException,
    FailedToStartError,
    GameNotFoundException,
)
from machiavelli.game.command import Command
from machiavelli.game.exceptions import (
    DuplicatedGameException as DomainDuplicatedGameException,
)
from machiavelli.game.exceptions import FailedToStartError as DomainFailedToStartError
from machiavelli.game.exceptions import (
    GameNotFoundException as DomainGameNotFoundException,
)
from machiavelli.game.game import (
    DuplicatedGameException as CompatibilityDuplicatedGameException,
)
from machiavelli.game.game import FailedToStartError as CompatibilityFailedToStartError
from machiavelli.game.game import Game
from machiavelli.game.game import (
    GameNotFoundException as CompatibilityGameNotFoundException,
)
from machiavelli.game.game import TooManyExpenses as CompatibilityTooManyExpenses
from machiavelli.game.map import Map, Province, Route, Sea
from machiavelli.game.player import Player, TurnType


def make_command(
    game: Game,
    player: Player,
    actor: str,
    command: str,
    target: str | None = None,
) -> Command:
    """Construye un comando utilizando las relaciones canónicas del dominio."""
    return Command(
        game=game,
        player=player,
        actor=actor,
        command=command,
        target=target,
    )


@pytest.fixture
def game() -> Game:
    origin = Province("Origin", custom_id="origin", has_port=True)
    destination = Province("Destination", custom_id="destination", has_port=True)
    fort = Province("Fort", custom_id="fort", city="Fort")
    sea_one = Sea("Sea One", custom_id="sea-one")
    sea_two = Sea("Sea Two", custom_id="sea-two")

    sea_one.sea_routes = [Route("sea-two"), Route("destination")]
    sea_two.sea_routes = [Route("destination")]

    return Game(
        name="orders-test",
        map=Map(
            provinces={
                origin.id: origin,
                destination.id: destination,
                fort.id: fort,
            },
            seas={sea_one.id: sea_one, sea_two.id: sea_two},
        ),
    )


@pytest.fixture
def player(game: Game) -> Player:
    current = Player(
        game=game,
        player_id="player_1",
        armies=["origin"],
        fleets=["sea-one", "sea-two"],
        garrisons=["fort"],
    )
    game.players = [current]
    return current


@pytest.fixture
def processor(game: Game) -> OrderProcessor:
    return OrderProcessor(game)


def test_game_exceptions_and_expense_exception_have_single_identities() -> None:
    assert FailedToStartError is DomainFailedToStartError
    assert FailedToStartError is CompatibilityFailedToStartError
    assert DuplicatedGameException is DomainDuplicatedGameException
    assert DuplicatedGameException is CompatibilityDuplicatedGameException
    assert GameNotFoundException is DomainGameNotFoundException
    assert GameNotFoundException is CompatibilityGameNotFoundException
    assert TooManyExpenses is CompatibilityTooManyExpenses


class TestMaintenanceTurn:
    def test_add_new_command_through_player_facade(
        self,
        game: Game,
        player: Player,
    ) -> None:
        game.turn_number = 1
        command = make_command(game, player, "A origin", "M")

        report = player.cmd_add_command(TurnType.MAINTENANCE, command)

        assert player.commands == [command]
        assert report == [
            f"Orden `{command}` enviada.",
            "**Órdenes recibidas hasta ahora:**",
            f"`{command}`",
        ]

    def test_replace_existing_command(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 1
        current = make_command(game, player, "A origin", "M")
        player.add_command(current)
        previous_text = str(current)
        replacement = make_command(game, player, "A origin", "D")

        report = processor.process_command(player, TurnType.MAINTENANCE, replacement)

        assert player.commands == [current]
        assert current.command == "D"
        assert current.target is None
        assert report[1] == f"Sustituye la orden anterior `{previous_text}`."

    def test_disband_removes_order_for_new_unit(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 1
        creation = make_command(game, player, "A reserve", "C", "origin")
        player.add_command(creation)
        disband = make_command(game, player, "A reserve", "D")

        processor.process_command(player, TurnType.MAINTENANCE, disband)

        assert player.commands == []

    def test_duplicate_rows_for_actor_raise_value_error(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 1
        first = make_command(game, player, "A origin", "M")
        second = make_command(game, player, "A origin", "D")
        player.commands = [first, second]
        replacement = make_command(game, player, "A origin", "M")

        with pytest.raises(ValueError, match="Se encontraron múltiples comandos"):
            processor.process_command(player, TurnType.MAINTENANCE, replacement)

        assert player.commands == [first, second]


class TestCampaignExpenses:
    def test_add_new_expense(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        expense = make_command(game, player, "E 1", "5", "origin")

        processor.process_command(player, TurnType.CAMPAIGN, expense)

        assert player.commands == [expense]

    def test_update_existing_expense(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        expense = make_command(game, player, "E 1", "5", "origin")
        player.add_command(expense)
        previous_text = str(expense)
        update = make_command(game, player, "E 1", "3", "origin")

        report = processor.process_command(player, TurnType.CAMPAIGN, update)

        assert player.commands == [expense]
        assert expense.command == "3"
        assert report[1] == f"Sustituye la orden anterior `{previous_text}`."

    def test_zero_cost_removes_existing_expense(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        expense = make_command(game, player, "E 1", "5", "origin")
        player.add_command(expense)
        previous_text = str(expense)
        removal = make_command(game, player, "E 1", "0", "origin")

        report = processor.process_command(player, TurnType.CAMPAIGN, removal)

        assert player.commands == []
        assert report[1] == f"Elimina el gasto anterior `{previous_text}`."

    def test_more_than_four_expenses_raises_single_exception(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        player.commands = [
            make_command(game, player, f"E {index}", "2", "origin")
            for index in range(4)
        ]
        fifth = make_command(game, player, "E 5", "1", "origin")

        with pytest.raises(
            TooManyExpenses,
            match="Solo se permiten hasta cuatro gastos",
        ):
            processor.process_command(player, TurnType.CAMPAIGN, fifth)

        assert len(player.commands) == 4


class TestCampaignOrders:
    def test_standard_order_replaces_previous_order(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        current = make_command(game, player, "A origin", "H")
        player.add_command(current)
        previous_text = str(current)
        replacement = make_command(game, player, "A origin", "A", "destination")

        report = processor.process_command(player, TurnType.CAMPAIGN, replacement)

        assert player.commands == [replacement]
        assert report[1] == f"Sustituye la orden anterior `{previous_text}`."

    def test_valid_convoy_appends_segment(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        first_segment = make_command(game, player, "A origin", "A", "sea-one")
        player.add_command(first_segment)
        destination = make_command(game, player, "A origin", "A", "destination")

        processor.process_command(player, TurnType.CAMPAIGN, destination)

        assert player.commands == [first_segment, destination]

    def test_invalid_convoy_replaces_previous_segments(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        first_segment = make_command(game, player, "A origin", "A", "sea-one")
        player.add_command(first_segment)
        invalid_destination = make_command(game, player, "A origin", "A", "fort")

        processor.process_command(player, TurnType.CAMPAIGN, invalid_destination)

        assert player.commands == [invalid_destination]

    def test_order_without_target_is_preserved(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        hold = make_command(game, player, "F sea-two", "H")

        processor.process_command(player, TurnType.CAMPAIGN, hold)

        assert player.commands == [hold]
        assert player.commands[0].target is None

    def test_report_messages_and_commands_keep_stable_order(
        self,
        game: Game,
        player: Player,
        processor: OrderProcessor,
    ) -> None:
        game.turn_number = 2
        army = make_command(game, player, "A origin", "H")
        fleet = make_command(game, player, "F sea-one", "H")
        garrison = make_command(game, player, "G fort", "H")
        player.commands = [army, fleet]

        report = processor.process_command(player, TurnType.CAMPAIGN, garrison)

        assert report == [
            f"Orden `{garrison}` enviada.",
            "**Órdenes recibidas hasta ahora:**",
            f"`{army}`",
            f"`{fleet}`",
            f"`{garrison}`",
        ]
        assert player.commands == [army, fleet, garrison]
