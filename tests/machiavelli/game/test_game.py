# tests/machiavelli/game/test_game.py

import unittest
from unittest.mock import Mock

from machiavelli.game.exceptions import (
    DuplicatePlayerException,
    PlayerNotFoundException,
)
from machiavelli.game.game import Game
from tests.machiavelli.engine.helpers import (
    create_mock_game,
    create_mock_player,
)


class TestRequireMap(unittest.TestCase):
    """Tests para el método require_map."""

    def test_require_map(self):
        """Recupera el mapa."""
        mock_map = Mock()
        game = Game("test", map=mock_map)

        map = game.require_map()
        self.assertEqual(map, mock_map)

    def test_require_map_failed(self):
        """El mapa no existe y lanza una excepción."""
        game = Game("test")

        with self.assertRaises(RuntimeError):
            game.require_map()


class TestRequireScenario(unittest.TestCase):
    """Tests para el método require_scenario."""

    def test_require_scenario(self):
        """Recupera el escenario."""
        mock_scenario = Mock()
        game = Game("test", scenario=mock_scenario)

        map = game.require_scenario()
        self.assertEqual(map, mock_scenario)

    def test_require_scenario_failed(self):
        """El escenario no existe y lanza una excepción."""
        game = Game("test")

        with self.assertRaises(RuntimeError):
            game.require_scenario()


class TestGetPlayer(unittest.TestCase):
    """Test unitarios para el método get_player."""

    def setUp(self):
        """Prepara el juego de pruebas."""
        self.game = Game("test")
        self.player1 = create_mock_player(player_id="P1", discord_id=123, power="L")
        self.player2 = create_mock_player(player_id="P2", discord_id=456, power="V")

        self.game.players = [self.player1, self.player2]

    def test_get_player_from_player_id(self):
        """Recupera un jugador de su player_id."""
        player = self.game.get_player(player_id="P1")
        self.assertEqual(player, self.player1)

        player = self.game.get_player(player_id="P3")
        self.assertIsNone(player)

    def test_get_player_from_discord_id(self):
        """Recupera un jugador de su id de discord."""
        player = self.game.get_player(discord_id=456)
        self.assertEqual(player, self.player2)

        player = self.game.get_player(discord_id=987)
        self.assertIsNone(player)

    def test_get_player_from_power_id(self):
        """Recupera un jugador de su id de potencia."""
        player = self.game.get_player(power_id="L")
        self.assertEqual(player, self.player1)

        player = self.game.get_player(power_id="N")
        self.assertIsNone(player)

    def test_get_player_invalid_parameters(self):
        """No se pasa ningún parámetro, o se pasan varios, para recuperarlo."""

        player = self.game.get_player()
        self.assertIsNone(player)

        player = self.game.get_player(player_id="P1", discord_id=123)
        self.assertIsNone(player)


class TestAddPlayer(unittest.TestCase):
    """Tests para el método add_player."""

    def setUp(self):
        """Prepara el juego de pruebas."""
        self.game = Game("test")
        self.player1 = create_mock_player(player_id="P1", discord_id=123, power="L")
        self.player2 = create_mock_player(player_id="P2", discord_id=456, power="V")

        self.game.players = [self.player1, self.player2]

    def test_add_player(self):
        """Añade un jugador a la partida."""
        self.game.add_player("P3", 789)
        self.assertEqual(len(self.game.players), 3)
        self.assertEqual(self.game.players[-1].player_id, "P3")
        self.assertEqual(self.game.players[-1].discord_id, 789)

    def test_add_player_duplicated(self):
        """Añade un jugador duplicado a la partida."""
        with self.assertRaises(DuplicatePlayerException):
            self.game.add_player("P1", 789)
        with self.assertRaises(DuplicatePlayerException):
            self.game.add_player("P3", 456)


class TestRemovePlayer(unittest.TestCase):
    """Tests para el método remove_player."""

    def setUp(self):
        """Prepara el juego de pruebas."""
        self.game = Game("test")
        self.player1 = create_mock_player(player_id="P1", discord_id=123, power="L")
        self.player2 = create_mock_player(player_id="P2", discord_id=456, power="V")

        self.game.players = [self.player1, self.player2]

    def test_remove_player(self):
        """Elimina un jugador a la partida."""
        self.game.remove_player(456)
        self.assertNotIn(self.player2, self.game.players)

    def test_remove_player_not_exists(self):
        """Añade un jugador duplicado a la partida."""
        with self.assertRaises(PlayerNotFoundException):
            self.game.remove_player(789)


class TestAdvanceTurn(unittest.TestCase):
    """Tests para el método advance_turn."""

    def test_advance_turn(self):
        """Actualiza Game con el avance de turno."""
        game = Game("test", turn_number=4, next_deadline="2020-10-10 22:00")
        game.pending_exchanges = [Mock(), Mock()]
        player1 = Mock()
        player2 = Mock()
        player1.commands = [Mock(), Mock()]
        player2.commands = [Mock(), Mock(), Mock(), Mock()]
        game.players = [player1, player2]

        game.advance_turn()
        self.assertEqual(game.turn_number, 5)
        self.assertEqual(game.next_deadline, "2020-10-17 22:00")
        self.assertEqual(player1.commands, [])
        self.assertEqual(player2.commands, [])


class TestAddEvent(unittest.TestCase):
    """Tests para el método add_event."""

    def test_add_event(self):
        """Añade eventos a la partida."""
        game = Game("Test")
        first = Mock()
        second = Mock()

        game.add_event(first)
        game.add_event(second)
        game.add_event(first)

        self.assertEqual(game.turn_events, [first, second, first])
        self.assertIs(game.turn_events[0], first)
        self.assertIs(game.turn_events[1], second)
        self.assertIs(game.turn_events[2], first)


class TestGetUnitOwner(unittest.TestCase):
    """Tests para el método get_unit_owner de Game."""

    def setUp(self):
        """Prepara la instancia de Game y los jugadores para las pruebas."""
        self.game = create_mock_game()
        # Enlazamos el método real de la clase Game a la instancia mockeada
        self.game.get_unit_owner = Game.get_unit_owner.__get__(self.game, Game)

        self.player1 = create_mock_player("P1")
        self.player2 = create_mock_player("P2")

        self.player1.armies = []
        self.player1.fleets = []
        self.player1.garrisons = []

        self.player2.armies = []
        self.player2.fleets = []
        self.player2.garrisons = []

        self.game.players = [self.player1, self.player2]
        self.game.independent_garrisons = []

    def test_get_unit_owner_player_army(self):
        """Devuelve el jugador correcto cuando se consulta un ejército existente."""
        self.player1.armies = ["tivol"]

        owner = self.game.get_unit_owner("A tivol")

        self.assertEqual(owner, self.player1)

    def test_get_unit_owner_player_fleet(self):
        """Devuelve el jugador correcto para una flota existente."""
        self.player1.fleets = ["prove S"]

        owner = self.game.get_unit_owner("F prove")

        self.assertEqual(owner, self.player1)

    def test_get_unit_owner_player_garrison(self):
        """Devuelve el jugador correcto para una guarnición."""
        self.player1.garrisons = ["rome"]

        owner = self.game.get_unit_owner("G rome")

        self.assertEqual(owner, self.player1)

    def test_get_unit_owner_multiple_players(self):
        """Verifica que asigna la unidad al jugador correcto cuando hay varios."""
        self.player1.armies = ["flore"]
        self.player2.armies = ["milan"]

        self.assertEqual(self.game.get_unit_owner("A flore"), self.player1)
        self.assertEqual(self.game.get_unit_owner("A milan"), self.player2)

    def test_get_unit_owner_independent_garrison(self):
        """Devuelve None si la guarnición existe pero es independiente."""
        self.game.independent_garrisons = ["pisa"]

        owner = self.game.get_unit_owner("G pisa")

        self.assertIsNone(owner)

    def test_get_unit_owner_unit_does_not_exist(self):
        """Lanza ValueError si la unidad no existe."""
        with self.assertRaises(ValueError):
            self.game.get_unit_owner("A prove")

    def test_get_unit_owner_invalid_format(self):
        """Lanza ValueError si el formato del identificador es incorrecto."""
        with self.assertRaises(ValueError):
            self.game.get_unit_owner("Aprove")
