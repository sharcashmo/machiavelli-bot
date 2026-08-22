# test/machiavelli/engine/test_bribes.py

import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import Mock, patch

from machiavelli.engine.bribes import Bribe, BribeResolver
from machiavelli.game.events import EventType
from machiavelli.game.map import MovementMode
from tests.machiavelli.engine.helpers import create_mock_game, create_mock_player


class TestCheckAdjacent(unittest.TestCase):
    """Tests para el método privado _check_adjacent de GameEngine."""

    def setUp(self):
        """Prepara el GameEngine y el jugador para las pruebas."""
        self.mock_game = create_mock_game()
        self.resolver = BribeResolver(self.mock_game)
        self.player = create_mock_player("P1")

        # Mockeamos el comportamiento de map.adjacent_locations
        self.mock_adj_locations = {"tivol", "ETS", "prove"}
        self.mock_game.map.adjacent_locations = Mock(
            return_value=self.mock_adj_locations
        )

        # Estado inicial limpio del jugador
        self.player.armies = []
        self.player.fleets = []
        self.player.garrisons = []

    def test_check_adjacent_adjacent_army(self):
        """Devuelve True si el jugador tiene un ejército adyacente."""
        self.player.armies = ["tivol"]

        result = self.resolver._check_adjacent(self.player, "rome")

        self.assertTrue(result)
        self.mock_game.map.adjacent_locations.assert_called_once_with(
            origin="rome", mode=MovementMode.BOTH
        )

    def test_check_adjacent_adjacent_fleet(self):
        """Devuelve True si el jugador tiene una flota adyacente."""
        # 'prove S' debe normalizarse a 'prove', que está en mock_adj_locations
        self.player.fleets = ["prove S"]

        result = self.resolver._check_adjacent(self.player, "rome")

        self.assertTrue(result)

    def test_check_adjacent_adjacent_garrison(self):
        """Devuelve True si el jugador tiene una guarnición adyacente."""
        self.player.garrisons = ["tivol"]

        result = self.resolver._check_adjacent(self.player, "rome")

        self.assertTrue(result)

    def test_check_adjacent_no_units_adjacent(self):
        """Devuelve False si el jugador tiene unidades pero ninguna está adyacente."""
        self.player.armies = ["milan", "venic"]
        self.player.fleets = ["WTS"]
        self.player.garrisons = ["flore"]

        result = self.resolver._check_adjacent(self.player, "rome")

        self.assertFalse(result)

    def test_check_adjacent_no_units(self):
        """Devuelve False si el jugador no tiene ninguna unidad."""
        result = self.resolver._check_adjacent(self.player, "rome")

        self.assertFalse(result)


class TestExpenseCounterbribe(unittest.TestCase):
    """Tests para el método expense_counterbribe de GameEngine."""

    def setUp(self):
        """Prepara el GameEngine, jugador y diccionario de contrasobornos."""
        self.mock_game = create_mock_game()
        self.resolver = BribeResolver(self.mock_game)
        self.player = create_mock_player("P1")

    def test_expense_counterbribe_new_entry(self):
        """Registra un contrasoborno para un objetivo que no tenía ninguno previo."""
        command = Mock(target="A prove", command="10")

        self.resolver.expense_counterbribe(self.player, command)

        self.assertEqual(self.resolver.counterbribes.get("A prove"), 10)

    def test_expense_counterbribe_updated(self):
        """Actualiza la cifra del contrasoborno si la nueva puja es mayor."""
        self.resolver.counterbribes["A prove"] = 10
        command = Mock(target="A prove", command="15")

        self.resolver.expense_counterbribe(self.player, command)

        self.assertEqual(self.resolver.counterbribes["A prove"], 15)

    def test_expense_counterbribe_ignored(self):
        """Mantiene el contrasoborno original si la nueva puja es inferior."""
        self.resolver.counterbribes["A prove"] = 20
        command = Mock(target="A prove", command="10")

        self.resolver.expense_counterbribe(self.player, command)

        self.assertEqual(self.resolver.counterbribes["A prove"], 20)


class TestExpenseBribe(unittest.TestCase):
    """Tests para el método expense_bribe de GameEngine."""

    def setUp(self):
        """Prepara el GameEngine, jugadores y estructura de sobornos."""
        self.mock_game = create_mock_game()
        self.resolver = BribeResolver(self.mock_game)

        self.player1 = create_mock_player("P1")
        self.player2 = create_mock_player("P2")

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_not_adjacent(self, mock_check_adjacent):
        """Descarta el soborno si el jugador no está adyacente al objetivo."""
        mock_check_adjacent.return_value = False
        command = Mock(target="G pisa", actor="E G", command="9")

        self.resolver.expense_bribe(self.player1, command)

        self.assertEqual(len(self.resolver.bribes), 0)
        mock_check_adjacent.assert_called_once_with(self.player1, "pisa")

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_unit_does_not_exist(self, mock_check_adjacent):
        """Descarta el soborno si get_unit_owner lanza ValueError."""
        mock_check_adjacent.return_value = True
        self.mock_game.get_unit_owner.side_effect = ValueError("Unidad no encontrada")
        command = Mock(target="A prove", actor="E K", command="15")

        self.resolver.expense_bribe(self.player1, command)

        self.assertEqual(len(self.resolver.bribes), 0)

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_independent_garrison(self, mock_check_adjacent):
        """Soborno válido de tipo G u H hacia una guarnición independiente."""
        mock_check_adjacent.return_value = True
        self.mock_game.get_unit_owner.return_value = None  # Guarnición independiente
        command = Mock(target="G pisa", actor="E G", command="18")

        self.resolver.expense_bribe(self.player1, command)

        bribes_list = self.resolver.bribes["G pisa"]
        self.assertEqual(len(bribes_list), 1)

        bribe = bribes_list[0]
        self.assertEqual(bribe.target, "G pisa")
        self.assertIsNone(bribe.owner)
        self.assertEqual(bribe.actor, self.player1)
        self.assertEqual(bribe.amount, 18)
        self.assertEqual(bribe.command, "G")

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_enemy_garrison(self, mock_check_adjacent):
        """Soborno tipo I válido para una guarnición del rival."""
        mock_check_adjacent.return_value = True
        self.mock_game.get_unit_owner.return_value = self.player2
        command = Mock(target="G rome", actor="E I", command="30")

        self.resolver.expense_bribe(self.player1, command)

        self.assertEqual(len(self.resolver.bribes["G rome"]), 1)
        self.assertEqual(self.resolver.bribes["G rome"][0].owner, self.player2)

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_self_bribe(self, mock_check_adjacent):
        """Descarta soborno tipo I si el objetivo pertenece al propio jugador."""
        mock_check_adjacent.return_value = True
        self.mock_game.get_unit_owner.return_value = self.player1  # Unidad propia
        command = Mock(target="G rome", actor="E I", command="30")

        self.resolver.expense_bribe(self.player1, command)

        self.assertEqual(len(self.resolver.bribes), 0)

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_enemy_unit(self, mock_check_adjacent):
        """Soborno tipo K sobre un ejército (A) o flota (F) rival."""
        mock_check_adjacent.return_value = True
        self.mock_game.get_unit_owner.return_value = self.player2
        # 'F prove S' debe normalizarse a 'F prove'
        command = Mock(target="F prove S", actor="E K", command="24")

        self.resolver.expense_bribe(self.player1, command)

        self.assertIn("F prove", self.resolver.bribes)
        self.assertEqual(len(self.resolver.bribes["F prove"]), 1)
        self.assertEqual(self.resolver.bribes["F prove"][0].target, "F prove")

    @patch.object(BribeResolver, "_check_adjacent")
    def test_expense_bribe_invalid(self, mock_check_adjacent):
        """Descarta tipos de soborno aplicados a unidades incompatibles."""
        mock_check_adjacent.return_value = True
        self.mock_game.get_unit_owner.return_value = self.player2
        command = Mock(target="G rome", actor="E G", command="9")

        self.resolver.expense_bribe(self.player1, command)

        self.assertEqual(len(self.resolver.bribes), 0)


class TestExecuteBribe(unittest.TestCase):
    """Tests para la ejecución física de sobornos en GameEngine."""

    def setUp(self):
        """Prepara el GameEngine y los jugadores para las pruebas."""
        self.mock_game = create_mock_game()
        if not hasattr(self.mock_game, "add_event"):
            self.mock_game.add_event = Mock()

        self.resolver = BribeResolver(self.mock_game)

        self.actor = create_mock_player("Actor")
        self.owner = create_mock_player("Owner")

        self.mock_game.independent_garrisons = ["pisa"]
        self.owner.garrisons = ["rome"]
        self.owner.armies = ["flore"]
        self.owner.fleets = ["prove S"]

    def test_execute_bribe_emits_turn_event(self):
        """execute_bribe debe registrar un TurnEvent de gasto en el juego."""
        bribe = Bribe(
            target="G pisa", owner=None, actor=self.actor, amount=9, command="G"
        )
        self.resolver.execute_bribe(bribe)

        self.mock_game.add_event.assert_called_once()

        event_call = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event_call.type, EventType.BRIBE_EXECUTED)
        self.assertEqual(
            event_call.data,
            {
                "player": "Actor",
                "expense": "G",
                "target": "G pisa",
                "amount": 9,
            },
        )

    def test_execute_bribe_remove_independent_garrison(self):
        """Elimina una guarnición autónoma de la lista del juego."""
        bribe = Bribe(
            target="G pisa", owner=None, actor=self.actor, amount=9, command="G"
        )
        self.resolver.execute_bribe(bribe)

        self.assertNotIn("pisa", self.mock_game.independent_garrisons)

    def test_execute_bribe_buy_independent_garrison(self):
        """Elimina la guarnición autónoma del juego y se la asigna al comprador."""
        bribe = Bribe(
            target="G pisa", owner=None, actor=self.actor, amount=9, command="H"
        )
        self.resolver.execute_bribe(bribe)
        self.assertNotIn("pisa", self.mock_game.independent_garrisons)
        self.assertIn("pisa", self.actor.garrisons)

    def test_execute_bribe_make_garrison_independent(self):
        """Retira la guarnición al propietario y la añade a independientes."""
        bribe = Bribe(
            target="G rome", owner=self.owner, actor=self.actor, amount=12, command="I"
        )
        self.resolver.execute_bribe(bribe)
        self.assertNotIn("rome", self.owner.garrisons)
        self.assertIn("rome", self.mock_game.independent_garrisons)

    def test_execute_bribe_disbands_enemy_army(self):
        """Desbanda un ejército enemigo."""
        self.owner.armies = ["flore"]
        bribe = Bribe(
            target="A flore", owner=self.owner, actor=self.actor, amount=21, command="J"
        )

        self.resolver.execute_bribe(bribe)

        self.assertNotIn("flore", self.owner.armies)

    def test_execute_bribe_enemy_fleet_with_coast(self):
        """Desbanda una flota enemiga registrada con especificación de costa."""
        bribe = Bribe(
            target="F prove", owner=self.owner, actor=self.actor, amount=21, command="J"
        )

        self.resolver.execute_bribe(bribe)

        self.assertEqual(len(self.owner.fleets), 0)

    def test_execute_bribe_buy_army(self):
        """Transfiere un ejército de la lista del propietario a la del comprador."""
        self.owner.armies = ["flore"]
        bribe = Bribe(
            target="A flore", owner=self.owner, actor=self.actor, amount=30, command="K"
        )

        self.resolver.execute_bribe(bribe)

        self.assertNotIn("flore", self.owner.armies)
        self.assertIn("flore", self.actor.armies)

    def test_execute_bribe_buys_fleet(self):
        """Transfiere una flota manteniendo su designación de costa original."""
        self.owner.fleets = ["prove S"]
        bribe = Bribe(
            target="F prove", owner=self.owner, actor=self.actor, amount=30, command="K"
        )

        self.resolver.execute_bribe(bribe)

        self.assertNotIn("prove S", self.owner.fleets)
        self.assertIn("prove S", self.actor.fleets)


class TestResolveBribes(unittest.TestCase):
    """Tests para la orquestación y resolución global de sobornos en GameEngine."""

    def setUp(self):
        """Prepara el GameEngine, jugadores y datos de prueba."""
        self.mock_game = create_mock_game()
        self.resolver = BribeResolver(self.mock_game)

        self.player1 = create_mock_player("P1")
        self.player2 = create_mock_player("P2")

        # Mock básico del mapa para evitar KeyError en las provincias
        self.mock_province = Mock(major_city=1)
        self.mock_game.map.provinces = defaultdict(lambda: self.mock_province)

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_one_successful_bribe(self, mock_tables, mock_execute):
        """Un soborno válido que supera el coste se ejecuta correctamente."""
        mock_tables.expenses = {"K": {"cost": 9}}
        target = "A milan"

        bribe = Bribe(
            target=target,
            owner=self.player2,
            actor=self.player1,
            amount=20,
            command="K",
        )
        self.resolver.bribes[target] = [bribe]

        self.resolver.resolve_bribes()

        mock_execute.assert_called_once_with(bribe)

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_tied_purchases(self, mock_tables, mock_execute):
        """Si la puja máxima incluye órdenes de compra empatadas, se cancelan."""
        mock_tables.expenses = {"K": {"cost": 9}, "H": {"cost": 9}}
        target = "A milan"

        bribe1 = Bribe(
            target=target,
            owner=self.player2,
            actor=self.player1,
            amount=30,
            command="K",
        )
        bribe2 = Bribe(
            target=target,
            owner=self.player2,
            actor=self.player2,
            amount=30,
            command="K",
        )
        self.resolver.bribes[target] = [bribe1, bribe2]

        self.resolver.resolve_bribes()

        mock_execute.assert_not_called()

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_different_commands(self, mock_tables, mock_execute):
        """Si empatan en puja sobornos de no-compra pero distinto tipo, se cancelan."""
        mock_tables.expenses = {"I": {"cost": 9}, "J": {"cost": 9}}
        target = "G pisa"

        bribe1 = Bribe(
            target=target, owner=None, actor=self.player1, amount=24, command="I"
        )
        bribe2 = Bribe(
            target=target, owner=None, actor=self.player2, amount=24, command="J"
        )
        self.resolver.bribes[target] = [bribe1, bribe2]

        self.resolver.resolve_bribes()

        mock_execute.assert_not_called()

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_same_non_purchase(self, mock_tables, mock_execute):
        """Si empatan sobornos del MISMO tipo de no-compra, se ejecuta uno de ellos."""
        mock_tables.expenses = {"G": {"cost": 12}}
        target = "G pisa"

        bribe1 = Bribe(
            target=target, owner=None, actor=self.player1, amount=24, command="G"
        )
        bribe2 = Bribe(
            target=target, owner=None, actor=self.player2, amount=24, command="G"
        )
        self.resolver.bribes[target] = [bribe1, bribe2]

        self.resolver.resolve_bribes()

        mock_execute.assert_called_once()
        self.assertIn(mock_execute.call_args[0][0], [bribe1, bribe2])

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_garrisons_in_major_cities(self, mock_tables, mock_execute):
        """El coste se duplica si el objetivo es una guarnición en ciudad mayor (>1)."""
        mock_tables.expenses = {"G": {"cost": 15}}  # Coste base 15 -> Duplicado = 30
        target = "G rome"

        # Rome es ciudad mayor
        rome_province = Mock(major_city=2)
        self.mock_game.map.provinces["rome"] = rome_province

        # La puja es 24, insuficiente para los 30 exigidos
        bribe = Bribe(
            target=target, owner=None, actor=self.player1, amount=24, command="G"
        )
        self.resolver.bribes[target] = [bribe]

        self.resolver.resolve_bribes()

        mock_execute.assert_not_called()

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_succeed_major_city(self, mock_tables, mock_execute):
        """Si la puja alcanza el coste duplicado de la ciudad mayor, se ejecuta."""
        mock_tables.expenses = {"G": {"cost": 15}}  # Coste base 15 -> Duplicado = 30
        target = "G rome"

        rome_province = Mock(major_city=2)
        self.mock_game.map.provinces["rome"] = rome_province

        bribe = Bribe(
            target=target, owner=None, actor=self.player1, amount=36, command="G"
        )
        self.resolver.bribes[target] = [bribe]

        self.resolver.resolve_bribes()

        mock_execute.assert_called_once_with(bribe)

    @patch.object(BribeResolver, "execute_bribe")
    @patch("machiavelli.engine.bribes.GameTables")
    def test_resolve_bribes_counterbribe(self, mock_tables, mock_execute):
        """Resta el contrasoborno del importe."""
        mock_tables.expenses = {"K": {"cost": 21}}
        target = "A milan"

        bribe = Bribe(
            target=target,
            owner=self.player2,
            actor=self.player1,
            amount=30,
            command="K",
        )
        self.resolver.bribes[target] = [bribe]
        self.resolver.counterbribes[target] = 15

        self.resolver.resolve_bribes()

        mock_execute.assert_not_called()


def test_bribe_set_is_not_a_public_or_emitted_event() -> None:
    assert "bribe_set" not in {event_type.value for event_type in EventType}
    production_source = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("machiavelli").rglob("*.py")
    )
    assert "bribe_set" not in production_source


class TestBribeResolverRun(unittest.TestCase):
    """Tests para el método orquestador run() de BribeResolver."""

    def setUp(self):
        self.mock_game = create_mock_game()
        self.resolver = BribeResolver(self.mock_game)
        self.player1 = create_mock_player("P1")
        self.player2 = create_mock_player("P2")
        self.mock_game.players = [self.player1, self.player2]

    @patch.object(BribeResolver, "expense_counterbribe")
    @patch.object(BribeResolver, "expense_bribe")
    @patch.object(BribeResolver, "resolve_bribes")
    def test_run_classifies_commands_and_resolves(
        self, mock_resolve, mock_expense_bribe, mock_expense_counterbribe
    ):
        """Clasifica contrasobornos y sobornos, e invoca a resolve_bribes."""
        # Preparar comandos mockeados con respuestas a is_valid_expense
        cmd_counterbribe = Mock()
        cmd_counterbribe.is_valid_expense.side_effect = lambda types: (
            types == BribeResolver.COUNTERBRIBE_EXPENSE_TYPES
        )

        cmd_bribe = Mock()
        cmd_bribe.is_valid_expense.side_effect = lambda types: (
            types == BribeResolver.BRIBE_EXPENSE_TYPES
        )

        cmd_other = Mock()
        cmd_other.is_valid_expense.return_value = False

        self.player1.commands = [cmd_counterbribe, cmd_other]
        self.player2.commands = [cmd_bribe]

        # Ejecutar run()
        self.resolver.run()

        # Verificaciones
        mock_expense_counterbribe.assert_called_once_with(
            self.player1, cmd_counterbribe
        )
        mock_expense_bribe.assert_called_once_with(self.player2, cmd_bribe)
        mock_resolve.assert_called_once()

    @patch.object(BribeResolver, "resolve_bribes")
    def test_run_clears_previous_state(self, mock_resolve):
        """Debe limpiar las estructuras internas antes de registrar órdenes."""
        # Estado sucio de una ejecución previa
        self.resolver.counterbribes["A prove"] = 50
        self.resolver.bribes["A prove"] = [Mock()]

        self.player1.commands = []
        self.player2.commands = []

        self.resolver.run()

        # El estado interno debe haber quedado reseteado a vacío
        self.assertEqual(len(self.resolver.counterbribes), 0)
        self.assertEqual(len(self.resolver.bribes), 0)
        mock_resolve.assert_called_once()
