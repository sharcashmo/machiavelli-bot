# tests/test_machiavelli/test_game.py
import sqlite3
from unittest.mock import MagicMock, patch, call
import pytest

from machiavelli.game import DuplicatedGameException, GameNotFoundException
from machiavelli.game import Game, Player

def test_player_constructor():
    """Test sobre el constructor de Player"""
    player_id = "username"
    discord_id = 10

    player = Player(player_id)

    assert player.player_id == player_id
    assert player.discord_id is None

    player = Player(player_id, discord_id)

    assert player.player_id == player_id
    assert player.discord_id == discord_id


def test_game_constructor():
    """Tests sobre el constructor de la clase"""
    name = "Test name"

    game = Game(name)

    assert game.name == name
    assert game.channel_id is None

# Tests on database functions

# database on Player
def test_load_players_success():
    """Comprueba que load_players ejecuta la query correcta y devuelve las instancias correspondientes de Player."""
    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock(spec=sqlite3.Cursor)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchall.return_value = [
        (
            "carlos_id", 1111, '["rome", "bari"]', '["veron", "messi"]',
            '["berga", "bolog"]', '["venic", "bosni"]', '["V", "L"]', 8, '["flore"]', '["pisa"]', '["M"]', "M"
        ),
        ("sofia_id", None, None, None, None, None, None, 0, None, None, None, None),
    ]

    players = Player.load_players(mock_conn, game_id=42)

    mock_cursor.execute.assert_called_once_with(
            """
            SELECT player_id, discord_id, controlled_locations, armies, fleets, garrisons,
                ass_counters, ducats, rebelled_provinces, rebelled_cities, home_countries, power
            FROM players WHERE game_id = ?
            """, (42,)
    )

    assert len(players) == 2
    assert isinstance(players[0], Player)
    assert players[0].player_id == "carlos_id"
    assert players[0].discord_id == 1111
    assert len(players[0].controlled_locations) == 2
    assert len(players[0].armies) == 2
    assert len(players[0].fleets) == 2
    assert len(players[0].garrisons) == 2
    assert len(players[0].ass_counters) == 2
    assert len(players[0].rebelled_provinces) == 1
    assert len(players[0].rebelled_cities) == 1
    assert len(players[0].home_countries) == 1
    assert "rome" in players[0].controlled_locations
    assert "messi" in players[0].armies
    assert "berga" in players[0].fleets
    assert "bosni" in players[0].garrisons
    assert "V" in players[0].ass_counters
    assert players[0].ducats == 8
    assert "flore" in players[0].rebelled_provinces
    assert "pisa" in players[0].rebelled_cities
    assert "M" in players[0].home_countries
    assert players[0].power == "M"

    assert isinstance(players[1], Player)
    assert players[1].player_id == "sofia_id"
    assert players[1].discord_id is None
    assert len(players[1].controlled_locations) == 0
    assert len(players[1].armies) == 0
    assert len(players[1].fleets) == 0
    assert len(players[1].garrisons) == 0
    assert len(players[1].ass_counters) == 0
    assert len(players[1].rebelled_provinces) == 0
    assert len(players[1].rebelled_cities) == 0
    assert len(players[1].home_countries) == 0
    assert players[1].ducats == 0
    assert players[1].power is None

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

    mock_cursor.execute.side_effect = sqlite3.IntegrityError(
        "UNIQUE constraint failed"
    )

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

    mock_cursor.fetchone.return_value = (7, "Campaña de Milán", 987654, None, 0, None, None, '["venic", "bari"]', '["rome", "parma"]')

    lista_jugadores_simulados = [
        Player(player_id="fake_carlos", discord_id=111),
        Player(player_id="fake_sofia", discord_id=222),
    ]

    with patch.object(
        Player, "load_players"
    ) as mock_load_players:
        mock_load_players.return_value = lista_jugadores_simulados

        game = Game.load_game(mock_conn, game_id=7)

        assert isinstance(game, Game)
        assert game.database_id == 7
        assert game.name == "Campaña de Milán"
        assert game.channel_id == 987654
        assert game.players == lista_jugadores_simulados
        assert "venic" in game.famine
        assert "parma" in game.independent_garrisons

        mock_cursor.execute.assert_has_calls([
            call(
                "SELECT id, name, channel_id, scenario_id, turn_number, "
                "weekly_deadline, next_deadline, famine, independent_garrisons FROM games WHERE id = ?", (7,)
            ),
            call("SELECT message FROM game_events WHERE game_id = ? ORDER BY id ASC", (7,))
        ])

        mock_load_players.assert_called_once_with(mock_conn, 7)


def test_load_game_raises_not_found_and_never_loads_players():
    """Comprueba que si la partida no existe, se lanza la excepción y no se intenta llamar a la carga de jugadores."""
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
        "INSERT INTO games (name, channel_id, scenario_id, turn_number, "
        "weekly_deadline, next_deadline, famine, independent_garrisons) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Nueva Partida", 111, None, 0, None, None, "[]", "[]")
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
        "UPDATE games SET name = ?, channel_id = ?, scenario_id = ?, turn_number = ?, "
        "weekly_deadline = ?, next_deadline = ?, famine = ?, independent_garrisons = ? WHERE id = ?", 
        ("Partida Renombrada", 222, None, 0, None, None, "[]", "[]", 42)
    )
    # El ID no debe haber cambiado
    assert game.database_id == 42