# tests/machiavelli/engine/test_disaster.py

# tests/test_disasters.py

import unittest
from unittest.mock import Mock, patch

from machiavelli.engine.disasters import DisastersManager
from machiavelli.game.command import Command
from machiavelli.game.events import EventType, TurnEvent
from machiavelli.game.scenario import Rules
from tests.machiavelli.engine.helpers import create_mock_game


class TestFamineReliefExpenses(unittest.TestCase):
    """Pruebas para el procesamiento de gastos de alivio de hambruna."""

    def setUp(self):
        self.mock_game = create_mock_game()
        self.player1 = Mock()
        self.player1.player_id = "P1"
        self.player1.commands = []
        self.player2 = Mock()
        self.player2.player_id = "P2"
        self.player2.commands = []
        self.mock_game.players = [self.player1, self.player2]
        self.processor = DisastersManager(self.mock_game)
        self.mock_game.famine = ["flore", "pisa"]

    @patch("machiavelli.engine.disasters.GameTables")
    def test_famine_relief(self, mock_tables):
        """Un gasto 'E A' con importe suficiente elimina la hambruna del objetivo."""
        mock_tables.expenses = {"A": {"cost": 3}}

        cmd = Command(
            game=self.mock_game,
            player=self.player1,
            actor="E A",
            target="flore",
            command="3",
        )
        self.player1.commands = [cmd]

        self.processor.process_famine_relief_expenses()

        # Verifica la reducción real y el único hecho emitido.
        self.assertNotIn("flore", self.mock_game.famine)
        self.mock_game.add_event.assert_called_once()
        event = self.mock_game.add_event.call_args.args[0]
        self.assertEqual(event.type, EventType.FAMINE_RELIEF)
        self.assertEqual(event.data, {"player": "P1", "province": "flore"})

    @patch("machiavelli.engine.disasters.GameTables")
    def test_famine_relief_other_expense(self, mock_tables):
        """Los gastos que no son 'E A' son ignorados."""
        mock_tables.expenses = {"A": {"cost": 3}}

        cmd = Command(
            game=self.mock_game,
            player=self.player1,
            actor="E G",
            target="pisa",
            command="21",
        )
        self.player1.commands = [cmd]

        self.processor.process_famine_relief_expenses()

        # Verifica que no se eliminó el hambre en Pisa ni se emitió un hecho.
        self.assertIn("pisa", self.mock_game.famine)
        self.mock_game.add_event.assert_not_called()

    @patch("machiavelli.engine.disasters.GameTables")
    def test_famine_relief_short_amount(self, mock_tables):
        """Si el importe del gasto es menor que el coste de alivio, se ignora."""
        mock_tables.expenses = {"A": {"cost": 6}}

        cmd = Command(
            game=self.mock_game,
            player=self.player1,
            actor="E A",
            target="flore",
            command="3",
        )
        self.player1.commands = [cmd]

        self.processor.process_famine_relief_expenses()

        # Verifica que no se eliminó el hambre ni se emitió un hecho.
        self.assertIn("flore", self.mock_game.famine)
        self.mock_game.add_event.assert_not_called()

    @patch("machiavelli.engine.disasters.GameTables")
    def test_famine_relief_short_amount_multiple_players(self, mock_tables):
        """Filtra y procesa correctamente múltiples órdenes entre varios jugadores."""
        mock_tables.expenses = {"A": {"cost": 3}}

        cmd1 = Command(
            game=self.mock_game,
            player=self.player1,
            actor="E A",
            target="flore",
            command="3",
        )
        self.player1.commands = [cmd1]

        cmd2 = Command(
            game=self.mock_game,
            player=self.player2,
            actor="E A",
            target="flore",
            command="3",
        )
        cmd3 = Command(
            game=self.mock_game,
            player=self.player2,
            actor="E A",
            target="pisa",
            command="3",
        )
        self.player2.commands = [cmd2, cmd3]

        self.processor.process_famine_relief_expenses()

        # Verifica que cada reducción real emite una sola vez, sin duplicar Flore.
        self.assertNotIn("flore", self.mock_game.famine)
        self.assertNotIn("pisa", self.mock_game.famine)
        events = [item.args[0] for item in self.mock_game.add_event.call_args_list]
        self.assertEqual(
            [(event.type, event.data) for event in events],
            [
                (EventType.FAMINE_RELIEF, {"player": "P1", "province": "flore"}),
                (EventType.FAMINE_RELIEF, {"player": "P2", "province": "pisa"}),
            ],
        )


class TestApplyDisasterDeaths(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.mock_game.players = []
        self.mock_game.independent_garrisons = []
        self.manager = DisastersManager(game=self.mock_game)

    def test_apply_disaster_deaths_invalid_event(self):
        """Si el tipo de evento no es FAMINE_ATTRITION o PLAGUE_DEATH, no hace nada."""
        player = Mock(armies=["pisa"], fleets=[], garrisons=[])
        self.mock_game.players = [player]

        self.manager._apply_disaster_deaths(EventType.EXPENSE, ["pisa"])

        self.assertEqual(player.armies, ["pisa"])
        self.mock_game.add_event.assert_not_called()

    def test_apply_disaster_deaths(self):
        """Aplica a varias unidades del mismo jugador."""
        player = Mock(
            player_id="P1",
            armies=["flore", "pisa", "sienn"],
            fleets=[],
            garrisons=[],
        )
        self.mock_game.players = [player]

        self.manager._apply_disaster_deaths(
            EventType.FAMINE_ATTRITION, ["flore", "pisa"]
        )

        self.assertEqual(player.armies, ["sienn"])
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.FAMINE_ATTRITION)
        self.assertEqual(event.data["player"], "P1")
        self.assertEqual(set(event.data["units"]), {"A flore", "A pisa"})

    def test_apply_disaster_deaths_fleets_and_garrisons(self):
        """Prueba la eliminación de flotas (usando split()) y guarniciones."""
        player = Mock(
            player_id="P1",
            armies=[],
            fleets=["pisa S", "venic N"],
            garrisons=["pisa"],
        )
        self.mock_game.players = [player]

        self.manager._apply_disaster_deaths(EventType.PLAGUE_DEATH, ["pisa"])

        self.assertEqual(player.fleets, ["venic N"])
        self.assertEqual(player.garrisons, [])

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(set(event.data["units"]), {"F pisa S", "G pisa"})

    def test_apply_disaster_deaths_independent_garrisons(self):
        """Elimina guarniciones independientes y emite el evento con player=None."""
        self.mock_game.independent_garrisons = ["flore", "lucca", "pisa"]

        self.manager._apply_disaster_deaths(
            EventType.FAMINE_ATTRITION, ["flore", "pisa"]
        )

        self.assertEqual(self.mock_game.independent_garrisons, ["lucca"])
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.FAMINE_ATTRITION)
        self.assertIsNone(event.data["player"])
        self.assertEqual(event.data["units"], ("G flore", "G pisa"))

    def test_apply_disaster_deaths_no_units_affected(self):
        """No se registran eventos si ninguna unidad es afectada."""
        player = Mock(player_id="P1", armies=["rome"], fleets=[], garrisons=[])
        self.mock_game.players = [player]
        self.mock_game.independent_garrisons = ["naple"]

        self.manager._apply_disaster_deaths(EventType.FAMINE_ATTRITION, ["pisa"])

        self.mock_game.add_event.assert_not_called()


class TestResolveFamineAttrition(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = DisastersManager(game=self.mock_game)

    def test_resolve_famine_attrition_calls_apply_disaster_deaths_with_famine_list(
        self,
    ):
        """Llama _apply_disaster_deaths con FAMINE_ATTRITION y provincias con hambre."""
        famine_provinces = ["pisa", "flore"]
        self.mock_game.famine = famine_provinces

        with patch.object(self.manager, "_apply_disaster_deaths") as mock_apply:
            self.manager.resolve_famine_attrition()

            # Comprobar que se llamó exactamente una vez con los argumentos correctos
            mock_apply.assert_called_once_with(
                EventType.FAMINE_ATTRITION, famine_provinces
            )


class TestClearFamine(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = DisastersManager(game=self.mock_game)

    def test_clear_famine(self):
        """Emite el evento FAMINE_END con las provincias afectadas y vacía la lista."""
        self.mock_game.famine = ["pisa", "flore"]

        self.manager.clear_famine()

        # Comprobar que la lista del juego ha quedado vacía
        self.assertEqual(self.mock_game.famine, [])

        # Comprobar que se llamó a add_event exactamente una vez
        self.mock_game.add_event.assert_called_once()

        # Verificar los atributos del objeto TurnEvent enviado
        event = self.mock_game.add_event.call_args[0][0]
        self.assertIsInstance(event, TurnEvent)
        self.assertEqual(event.type, EventType.FAMINE_END)
        self.assertEqual(event.data, {"provinces": ("pisa", "flore")})

    def test_clear_famine_empty(self):
        """Si la lista de hambrunas está vacía, no debe emitir ningún evento."""
        self.mock_game.famine = []

        self.manager.clear_famine()

        self.assertEqual(self.mock_game.famine, [])
        self.mock_game.add_event.assert_not_called()


class TestSpawnDisaster(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.mock_map = Mock()
        # Provincias de prueba activas en el mapa
        self.mock_map.provinces = {
            "pisa": Mock(),
            "flore": Mock(),
            "rome": Mock(),
            "sienn": Mock(),
        }
        self.mock_game.map = self.mock_map
        self.mock_game.require_map.return_value = self.mock_map
        self.mock_rng = Mock()

        self.mock_game.map = self.mock_map
        self.manager = DisastersManager(game=self.mock_game, rng=self.mock_rng)

    def test_spawn_disaster_invalid_event_type(self):
        """Devuelve [] y no emite eventos si el event_type no es de desastre."""
        result = self.manager._spawn_disaster(EventType.EXPENSE)

        self.assertEqual(result, [])
        self.mock_game.add_event.assert_not_called()
        self.mock_rng.randint.assert_not_called()

    @patch("machiavelli.engine.disasters.GameTables")
    def test_spawn_disaster_row_only(self, mock_tables):
        """Prueba la generación de desastre afectando solo a una fila."""
        mock_tables.disasters = {0: ["row"]}
        # Fila 2 (índice 2) contiene pisa y flore
        mock_tables.famine = [
            [None, None],
            [None, None],
            ["pisa", "flore", None],
        ]

        # 1a llamada: severity_dice (0)
        # 2a y 3a llamada: dado fila (1 + 1 = 2)
        self.mock_rng.randint.side_effect = [0, 1, 1]

        result = self.manager._spawn_disaster(EventType.FAMINE_SPAWN)

        self.assertEqual(result, ["pisa", "flore"])
        self.mock_game.add_event.assert_called_once()

        event = self.mock_game.add_event.call_args[0][0]
        self.assertEqual(event.type, EventType.FAMINE_SPAWN)
        self.assertEqual(
            event.data,
            {"severity_roll": 0, "provinces": ("pisa", "flore")},
        )

    @patch("machiavelli.engine.disasters.GameTables")
    def test_spawn_disaster_both(self, mock_tables):
        """Si afecta a fila y columna ("both"), la intersección no se duplica."""
        mock_tables.disasters = {1: ["both"]}
        # Fila 0 tiene 'pisa'. Columna 0 de la Fila 1 también tiene 'pisa'.
        mock_tables.plague = [
            ["pisa", "flore"],
            ["pisa", "rome"],
        ]

        # 1a: severity_dice (1)
        # 2a, 3a: dado fila (0 + 0 = 0) -> Fila 0: ["pisa", "flore"]
        # 4a, 5a: dado columna (0 + 0 = 0) -> Columna 0: ["pisa", "pisa"]
        self.mock_rng.randint.side_effect = [1, 0, 0, 0, 0]

        result = self.manager._spawn_disaster(EventType.PLAGUE_SPAWN)

        # 'pisa' solo debe aparecer una vez y el evento conserva la tirada pública.
        self.assertEqual(result, ["pisa", "flore"])
        event = self.mock_game.add_event.call_args.args[0]
        self.assertEqual(event.type, EventType.PLAGUE_SPAWN)
        self.assertEqual(
            event.data,
            {"severity_roll": 1, "provinces": ("pisa", "flore")},
        )

    @patch("machiavelli.engine.disasters.GameTables")
    def test_spawn_disaster_filters_out_provinces_not_on_map(self, mock_tables):
        """Ignora provincias que no existen en self.map.provinces o son None."""
        mock_tables.disasters = {0: ["row"], 1: ["row"]}
        # "unkno" no está en self.mock_map.provinces
        mock_tables.famine = [["pisa", None, "unkno"]]

        self.mock_rng.randint.side_effect = [1, 0, 0]

        result = self.manager._spawn_disaster(EventType.FAMINE_SPAWN)

        self.assertEqual(result, ["pisa"])


class TestSpawnFamine(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = DisastersManager(game=self.mock_game)

    def test_spawn_famine_calls(self):
        """Llama a _spawn_disaster con FAMINE_SPAWN y actualiza game.famine."""
        expected_provinces = ["pisa", "flore"]

        with patch.object(
            self.manager, "_spawn_disaster", return_value=expected_provinces
        ) as mock_spawn:
            self.manager.spawn_famine()

            # Comprobar que llamó al helper con el tipo de evento correcto
            mock_spawn.assert_called_once_with(event_type=EventType.FAMINE_SPAWN)

            # Comprobar que el resultado se guardó en la lista de la partida
            self.assertEqual(self.mock_game.famine, expected_provinces)


class TestSpawnPlague(unittest.TestCase):
    def setUp(self):
        self.mock_game = Mock()
        self.manager = DisastersManager(game=self.mock_game)

    def test_spawn_plague(self):
        """Verifica que se genera la plaga y se aplican las muertes correspondientes."""
        plague_provinces = ["pisa", "flore"]

        with (
            patch.object(
                self.manager, "_spawn_disaster", return_value=plague_provinces
            ) as mock_spawn,
            patch.object(self.manager, "_apply_disaster_deaths") as mock_apply_deaths,
        ):
            self.manager.spawn_plague()

            # Comprobar que se llamó a _spawn_disaster con PLAGUE_SPAWN
            mock_spawn.assert_called_once_with(event_type=EventType.PLAGUE_SPAWN)

            # Comprobar que se llamó a _apply_disaster_deaths con el resultado
            # y PLAGUE_DEATH
            mock_apply_deaths.assert_called_once_with(
                event_type=EventType.PLAGUE_DEATH, provinces=plague_provinces
            )


class TestInactiveDisasterRules(unittest.TestCase):
    def setUp(self):
        self.game = Mock()
        self.game.scenario.rules = Rules(famine_active=False, plague_active=False)
        self.game.famine = ["pisa"]
        self.game.players = []
        self.game.independent_garrisons = ["pisa"]
        self.manager = DisastersManager(self.game)

    @patch("machiavelli.engine.disasters.GameTables")
    def test_inactive_rules_make_all_public_disaster_methods_no_ops(self, mock_tables):
        mock_tables.expenses = {"A": {"cost": 3}}

        with (
            patch.object(self.manager, "_spawn_disaster") as spawn,
            patch.object(self.manager, "_apply_disaster_deaths") as deaths,
        ):
            self.manager.process_famine_relief_expenses()
            self.manager.resolve_famine_attrition()
            self.manager.clear_famine()
            self.manager.spawn_famine()
            self.manager.spawn_plague()

        self.assertEqual(self.game.famine, ["pisa"])
        self.assertEqual(self.game.independent_garrisons, ["pisa"])
        self.game.add_event.assert_not_called()
        spawn.assert_not_called()
        deaths.assert_not_called()
