"""Pruebas de persistencia y reglas de Game relacionadas con la fase militar."""

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

import pytest

from machiavelli import database
from machiavelli.game.command import Command
from machiavelli.game.events import EventType, TurnEvent
from machiavelli.game.game import (
    DuplicatedGameException,
    Game,
    GameNotFoundException,
    Player,
)
from machiavelli.game.trading import ExchangeProposal, TradeResource
from machiavelli.repositories.game_repository import GameRepository


def test_player_constructor():
    """Test sobre el constructor de Player"""
    player_id = "username"
    discord_id = 10

    game = MagicMock(spec=Game)
    game.database_id = 111

    player = Player(game, player_id)

    assert player.game == game
    assert player.player_id == player_id
    assert player.discord_id is None

    player = Player(game, player_id, discord_id)

    assert player.game == game
    assert player.player_id == player_id
    assert player.discord_id == discord_id


def test_game_constructor():
    """Tests sobre el constructor de la clase"""
    name = "Test name"

    game = Game(name)

    assert game.name == name
    assert game.channel_id is None


def test_military_event_round_trip_preserves_seven_lists(tmp_path):
    """Comprueba que el evento militar completo sobrevive al ciclo SQLite."""
    db_path = tmp_path / "game.db"
    database.upgrade(str(db_path))
    event = TurnEvent.military_resolution(
        [[["P1", "A", "rome"], "A", "pisa", False, None]],
        [["P1", "A", "rome"]],
        [],
        [],
        [["P1", "province", "rome", "subdued"]],
        [],
        [[["P1", "A", "rome"], "retreat", "pisa"]],
    )
    with closing(sqlite3.connect(db_path)) as conn:
        game = Game("Evento militar")
        game.add_event(event)
        game.save(conn)
        loaded = Game.load_game(conn, game_id=game.database_id)

    loaded_event = loaded.turn_events[-1]
    assert loaded_event == event
    assert loaded_event.type is EventType.MILITARY_RESOLUTION
    assert loaded_event.to_json() == event.to_json()


def test_military_event_is_canonical_compact_and_keeps_previous_records():
    """Verifica orden, formato compacto y compatibilidad con eventos previos."""
    event = TurnEvent.military_resolution(
        [
            [["V", "A", "zeta"], "A", "ñ", False, None],
            [["M", "F", "alfa"], "F", "beta", False, None],
        ],
        [["V", "A", "zeta"], ["M", "F", "alfa"]],
        [["V", "A", "zeta"]],
        [["M", "F", "alfa"]],
        [["V", "city", "ñ", "liberated"], ["M", "province", "alfa", "subdued"]],
        [
            [["V", "A", "zeta"], "ñ", "started"],
            [["M", "F", "alfa"], "beta", "lifted"],
        ],
        [
            [["V", "A", "zeta"], "retreat", "alfa"],
            [["M", "F", "alfa"], "disband", None],
        ],
    )
    assert event.data["outcomes"][0][0] == ("M", "F", "alfa")
    assert event.data["cancelled_orders"] == (
        ("M", "F", "alfa"),
        ("V", "A", "zeta"),
    )
    assert event.to_json() == (
        '{"broken_convoys":[["V","A","zeta"]],'
        '"cancelled_orders":[["M","F","alfa"],["V","A","zeta"]],'
        '"decisions":[[["M","F","alfa"],"disband",null],'
        '[["V","A","zeta"],"retreat","alfa"]],'
        '"dislodgements":[["M","F","alfa"]],'
        '"outcomes":[[["M","F","alfa"],"F","beta",false,null],'
        '[["V","A","zeta"],"A","ñ",false,null]],'
        '"rebellions":[["M","province","alfa","subdued"],'
        '["V","city","ñ","liberated"]],'
        '"sieges":[[["M","F","alfa"],"beta","lifted"],'
        '[["V","A","zeta"],"ñ","started"]]}'
    )
    assert (
        TurnEvent.expense(EventType.EXPENSE, "M", "A", "a", 1).to_json()
        == '{"amount":1,"expense":"A","player":"M","target":"a"}'
    )


def test_military_event_rejects_non_primitive_or_malformed_lists():
    """Rechaza payloads que no respetan el contrato serializable."""
    malformed_lists = (
        ([[["P", "X", "a"], "A", "b", False]], [], [], [], [], [], []),
        ([[["P", "A", "a"], "X", "b", False]], [], [], [], [], [], []),
        ([[["P", "A", "a"], "A", None, False]], [], [], [], [], [], []),
        ([[["P", "A", "a"], "A", "b", True]], [], [], [], [], [], []),
        ([], [["P", "X", "a"]], [], [], [], [], []),
        ([], [], [["P", "A"]], [], [], [], []),
        ([], [], [], [["P", "A", "a", "extra"]], [], [], []),
        ([], [], [], [], [["P", "county", "a", "subdued"]], [], []),
        ([], [], [], [], [["P", "province", "a", "invalid"]], [], []),
        ([], [], [], [], [], [[["P", "X", "a"], "a", "started"]], []),
        ([], [], [], [], [], [[["P", "A", "a"], "a", ["started"]]], []),
        ([], [], [], [], [], [], [[["P", "X", "a"], "invalid", None]]),
    )
    for values in malformed_lists:
        with pytest.raises(ValueError):
            TurnEvent.military_resolution(*values)
    tuple_event = TurnEvent.military_resolution((), (), (), (), (), (), ())
    assert all(value == () for value in tuple_event.data.values())


# Tests on database functions


def test_load_commands_delegates_to_repository():
    """La fachada histórica debe usar el repositorio canónico."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    game = Game("Persistida", database_id=42)
    player = Player(game, "P1")

    with patch(
        "machiavelli.repositories.command_repository.CommandRepository.get_by_player",
        return_value=[],
    ) as get_by_player:
        assert Command.load_commands(mock_conn, game, player) == []

    get_by_player.assert_called_once_with(player)


def test_command_order_survives_repeated_loads_and_save_round_trip():
    """Conserva el orden relativo de un convoy tras cargas y guardados sucesivos."""

    def command_rows(game: Game) -> dict[str, tuple[tuple[str, str, str | None], ...]]:
        """Extrae las órdenes en la secuencia observada por cada jugador."""
        return {
            player.player_id: tuple(
                (command.actor, command.command, command.target)
                for command in player.commands
            )
            for player in game.players
        }

    expected = {
        "P1": (
            ("A rome", "A", "tyrrh"),
            ("A rome", "A", "westm"),
            ("A rome", "A", "pisa"),
        ),
        "P2": (
            ("A venic", "A", "ferrar"),
            ("A venic", "H", None),
        ),
    }

    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "commands.db"
        database.upgrade(str(db_path))

        with closing(sqlite3.connect(db_path)) as conn:
            game = Game("Orden persistido")
            game.players = [Player(game, "P1"), Player(game, "P2")]
            game.save(conn)
            conn.executemany(
                "INSERT INTO commands "
                "(game_id, player_id, actor, command, target) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (game.database_id, "P1", "A rome", "A", "tyrrh"),
                    (game.database_id, "P2", "A venic", "A", "ferrar"),
                    (game.database_id, "P1", "A rome", "A", "westm"),
                    (game.database_id, "P2", "A venic", "H", None),
                    (game.database_id, "P1", "A rome", "A", "pisa"),
                ),
            )
            conn.commit()

            first_load = Game.load_game(conn, game_id=game.database_id)
            second_load = Game.load_game(conn, game_id=game.database_id)
            assert command_rows(first_load) == expected
            assert command_rows(second_load) == expected

            first_load.save(conn)
            conn.commit()
            after_first_save = Game.load_game(conn, game_id=game.database_id)
            assert command_rows(after_first_save) == expected

            after_first_save.save(conn)
            conn.commit()
            after_second_save = Game.load_game(conn, game_id=game.database_id)
            assert command_rows(after_second_save) == expected
            assert conn.execute("PRAGMA user_version").fetchone() == (5,)


def test_pending_exchanges_are_independent_and_round_trip_without_game_columns():
    assert Game("A").pending_exchanges is not Game("B").pending_exchanges

    with closing(sqlite3.connect(":memory:")) as conn:
        database.upgrade_connection(conn)
        game = Game("Propuesta", scenario_id="Be")
        player = game.add_player("P1")
        player.home_countries = []
        game.add_event(TurnEvent(EventType.PLAYER_ELIMINATED, {"player": "P1"}))
        game.pending_exchanges = [
            ExchangeProposal(
                "X", "Y", TradeResource("ducats", 9), TradeResource("assassin", "V")
            )
        ]
        repository = GameRepository(conn)
        repository.save(game)
        conn.commit()

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()
        }
        assert "pending_exchanges" not in columns
        loaded = repository.get_by_id(game.database_id)
        assert loaded.pending_exchanges == game.pending_exchanges
        assert loaded.players[0].home_countries == []
        assert loaded.turn_events[0].type is EventType.PLAYER_ELIMINATED


def test_advance_turn_clears_pending_exchanges_after_player_commands():
    game = Game("Caducidad", turn_number=2, next_deadline="1454-01-01 00:00")
    player = Player(game, "P1")
    player.commands = ["command"]  # type: ignore[list-item]
    game.players = [player]
    game.pending_exchanges = [
        ExchangeProposal(
            "N", "L", TradeResource("ducats", 1), TradeResource("ducats", 2)
        )
    ]

    game.advance_turn()

    assert game.turn_number == 3
    assert game.next_deadline == "1454-01-08 00:00"
    assert player.commands == []
    assert game.pending_exchanges == []
    assert game.turn_events == []


def test_pending_exchange_persistence_does_not_change_game_events():
    with closing(sqlite3.connect(":memory:")) as conn:
        database.upgrade_connection(conn)
        game = Game("Eventos y propuestas", scenario_id="Be")
        game.add_player("P1")
        game.add_event(TurnEvent(EventType.PLAYER_ELIMINATED, {"player": "P1"}))
        repository = GameRepository(conn)
        repository.save(game)
        conn.commit()

        game_id = game.database_id
        assert game_id is not None
        event_rows_before = conn.execute(
            """
            SELECT event_type, data_json
            FROM game_events
            WHERE game_id = ?
            ORDER BY id ASC
            """,
            (game_id,),
        ).fetchall()

        loaded = repository.get_by_id(game_id)
        loaded.pending_exchanges = [
            ExchangeProposal(
                "M",
                "V",
                TradeResource("ducats", 987654),
                TradeResource("assassin", "N"),
            )
        ]
        repository.save(loaded)
        conn.commit()

        event_rows_after_proposal = conn.execute(
            """
            SELECT event_type, data_json
            FROM game_events
            WHERE game_id = ?
            ORDER BY id ASC
            """,
            (game_id,),
        ).fetchall()

        loaded = repository.get_by_id(game_id)
        loaded.advance_turn()
        repository.save(loaded)
        conn.commit()

        event_rows_after_expiry = conn.execute(
            """
            SELECT event_type, data_json
            FROM game_events
            WHERE game_id = ?
            ORDER BY id ASC
            """,
            (game_id,),
        ).fetchall()

        assert len(event_rows_after_proposal) == len(event_rows_before)
        assert event_rows_after_proposal == event_rows_before
        assert len(event_rows_after_expiry) == len(event_rows_before)
        assert event_rows_after_expiry == event_rows_before


# database on Player
def test_load_players_delegates_to_repository():
    """La fachada histórica debe usar el repositorio canónico."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    game = Game("Persistida", database_id=42)
    expected = [Player(game, "carlos_id", 1111), Player(game, "sofia_id")]

    with patch(
        "machiavelli.repositories.player_repository.PlayerRepository.get_by_game",
        return_value=expected,
    ) as get_by_game:
        players = Player.load_players(mock_conn, game)

    assert players is expected
    get_by_game.assert_called_once_with(game)


# database on Game
def test_create_game_success():
    """Comprueba que create_game inserta la partida correctamente en la BBDD
    y devuelve la instancia de Game con su id de base de datos asignado.
    """
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)

    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.lastrowid = 42

    name = "Guerra de Familias"
    channel_id = 123456789

    game = Game.create_game(name=name, channel_id=channel_id, conn=mock_conn)

    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO games (name, channel_id) VALUES (?, ?)", (name, channel_id)
    )

    assert isinstance(game, Game)
    assert game.name == name
    assert game.channel_id == channel_id
    assert game.database_id == 42


def test_create_game_raises_duplicated_exception():
    """Comprueba que si la base de datos lanza un IntegrityError (por nombre
    o canal duplicado), el método lo captura y lanza DuplicatedGameException.
    """
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.execute.side_effect = sqlite3.IntegrityError("UNIQUE constraint failed")

    name = "Partida Repetida"
    channel_id = 999999

    with pytest.raises(DuplicatedGameException) as exc_info:
        Game.create_game(name=name, channel_id=channel_id, conn=mock_conn)

    assert name in str(exc_info.value)
    assert str(channel_id) in str(exc_info.value)

    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO games (name, channel_id) VALUES (?, ?)", (name, channel_id)
    )


def test_load_game_success():
    """Comprueba que load_game recupera los datos de la partida de la BBDD"""

    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.return_value = (
        7,
        "Campaña de Milán",
        987654,
        None,
        0,
        None,
        None,
        '["venic", "bari"]',
        '["rome", "parma"]',
        '["turin"]',
    )

    with patch(
        "machiavelli.repositories.player_repository.PlayerRepository.get_by_game"
    ) as mock_get_players:
        mock_get_players.side_effect = lambda game: [
            Player(game, player_id="fake_carlos", discord_id=111),
            Player(game, player_id="fake_sofia", discord_id=222),
        ]

        game = Game.load_game(mock_conn, game_id=7)

        assert isinstance(game, Game)
        assert game.database_id == 7
        assert game.name == "Campaña de Milán"
        assert game.channel_id == 987654
        assert len(game.players) == 2
        assert game.players[0].player_id == "fake_carlos"
        assert game.players[1].discord_id == 222
        assert "venic" in game.famine
        assert "parma" in game.independent_garrisons
        assert "turin" in game.besieges

        mock_cursor.execute.assert_has_calls(
            [
                call(
                    "SELECT id, name, channel_id, scenario_id, turn_number, "
                    "weekly_deadline, next_deadline, famine, independent_garrisons, "
                    "besieges FROM games WHERE id = ?",
                    (7,),
                ),
                call(
                    """
            SELECT id, event_type, data_json
            FROM game_events
            WHERE game_id = ?
            ORDER BY id ASC
            """,
                    (7,),
                ),
            ]
        )

        mock_get_players.assert_called_once_with(game)


def test_load_game_raises_not_found_and_never_loads_players():
    """No carga jugadores cuando la partida solicitada no existe."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None

    with patch.object(Player, "load_players") as mock_load_players:
        with pytest.raises(GameNotFoundException):
            Game.load_game(mock_conn, name="Inexistente")

        mock_load_players.assert_not_called()


def test_game_save_inserts_new_game():
    """Comprueba que si database_id es None, save() hace un INSERT."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.lastrowid = 99

    # Partida sin ID (Nueva)
    game = Game(name="Nueva Partida", channel_id=111)

    game.save(mock_conn)

    # Verificamos que llamó al INSERT
    mock_cursor.execute.assert_any_call(
        "INSERT INTO games "
        "(name, channel_id, scenario_id, turn_number, weekly_deadline, "
        "next_deadline, famine, independent_garrisons, besieges) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Nueva Partida", 111, None, 0, None, None, "[]", "[]", "[]"),
    )
    # Verificamos que el objeto actualizó su ID en memoria
    assert game.database_id == 99


def test_game_save_updates_existing_game():
    """Comprueba que si database_id ya existe, save() hace un UPDATE."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)
    mock_conn.cursor.return_value = mock_cursor

    # Partida que YA existe en la BBDD (tiene ID 42)
    game = Game(name="Partida Vieja", channel_id=222, database_id=42)

    # Modificamos un dato en memoria (ej. el nombre)
    game.name = "Partida Renombrada"

    game.save(mock_conn)

    # Verificamos que ejecutó el UPDATE usando el ID como filtro
    mock_cursor.execute.assert_any_call(
        "UPDATE games SET name = ?, channel_id = ?, scenario_id = ?, "
        "turn_number = ?, weekly_deadline = ?, next_deadline = ?, famine = ?, "
        "independent_garrisons = ?, besieges = ? WHERE id = ?",
        ("Partida Renombrada", 222, None, 0, None, None, "[]", "[]", "[]", 42),
    )
    # El ID no debe haber cambiado
    assert game.database_id == 42
