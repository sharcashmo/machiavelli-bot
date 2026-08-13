# /tests/machiavelli/services/test_command_reporter.py

from unittest.mock import MagicMock

import pytest

from machiavelli.game.command import Command
from machiavelli.game.game import Game
from machiavelli.game.player import Player
from machiavelli.game.tables import GameTables
from machiavelli.services.command_reporter import CommandReporter


class FakeLocation:
    """Stub simple para simular provincias y mares del mapa."""

    def __init__(self, name: str):
        self.name = name


@pytest.fixture
def command_factory():
    """Construye órdenes con las relaciones canónicas de dominio."""
    game = Game("Informes", database_id=1)
    player = Player(game, "P1")

    def create(actor: str, command: str, target: str | None = None) -> Command:
        return Command(game, player, actor, command, target)

    return create


@pytest.fixture
def mock_game_map():
    """Mock ligero de GameMap para no requerir cargar el mapa real."""
    game_map = MagicMock()
    game_map.provinces = {
        "milan": FakeLocation("Milan"),
        "venic": FakeLocation("Venice"),
        "flore": FakeLocation("Florence"),
    }
    game_map.seas = {
        "UA": FakeLocation("Upper Adriatic"),
        "ETS": FakeLocation("Eastern Tyrrhenian Sea"),
    }
    return game_map


class TestCommandReporter:
    def test_unit_maintenance_turn(self, mock_game_map, monkeypatch, command_factory):
        """Mantenimiento de Primavera (turn_number % 4 == 1)."""
        monkeypatch.setattr(GameTables, "actors", {"A": "Ejército"})
        monkeypatch.setattr(
            GameTables,
            "maintenance_orders",
            {"M": {"text": "Mantener", "target_type": None}},
        )

        cmd = command_factory("A milan", "M")
        report = CommandReporter.format_report(cmd, mock_game_map, turn_number=1)

        assert report == "Ejército de Milan|Mantener"

    def test_unit_campaign_turn(self, mock_game_map, monkeypatch, command_factory):
        """Campaña Militar (turn_number % 4 != 1)."""
        monkeypatch.setattr(GameTables, "actors", {"A": "Ejército"})
        monkeypatch.setattr(
            GameTables,
            "military_orders",
            {"A": {"text": "Avanzar a Provincia o Mar", "target_type": "location"}},
        )

        cmd = command_factory("A milan", "A", "venic")
        report = CommandReporter.format_report(cmd, mock_game_map, turn_number=2)

        assert report == "Ejército de Milan|Avanzar a Provincia o Mar|Venice"

    def test_expense_report(self, mock_game_map, monkeypatch, command_factory):
        """Comando de Gasto/Soborno."""
        monkeypatch.setattr(
            GameTables,
            "expenses",
            {"B": {"text": "Pacificar rebelión", "target_type": "province"}},
        )

        cmd = command_factory("E B", "12", "flore")
        report = CommandReporter.format_report(cmd, mock_game_map, turn_number=2)

        assert report == "Pacificar rebelión|Florence|12 ducados"

    @pytest.mark.parametrize(
        "target_type, target_str, expected_target_report",
        [
            ("province", "flore", "Florence"),
            ("location", "UA", "Upper Adriatic"),
            ("power", "V", "Venice"),
            ("unit_type", "0", "Desbandar"),
            ("unit_type", "A", "Ejército"),
            ("unit", "A milan", "Ejército de Milan"),
            ("army_ext", "A milan V", "Ejército de Milan (Venice)"),
            ("army_ext", "A milan", "Ejército de Milan"),
            ("location_ext", "UA (V)", "Upper Adriatic (Venice)"),
            ("location_ext", "UA", "Upper Adriatic"),
        ],
    )
    def test_targets_parsing(
        self,
        mock_game_map,
        monkeypatch,
        target_type,
        target_str,
        expected_target_report,
        command_factory,
    ):
        """Verifica que todos los tipos de target_type se parsean correctamente."""
        monkeypatch.setattr(GameTables, "actors", {"A": "Ejército"})
        monkeypatch.setattr(GameTables, "powers", {"V": "Venice"})
        monkeypatch.setattr(
            GameTables,
            "military_orders",
            {"X": {"text": "Acción Especial", "target_type": target_type}},
        )

        cmd = command_factory("A milan", "X", target_str)
        report = CommandReporter.format_report(cmd, mock_game_map, turn_number=2)

        assert expected_target_report in report.split("|")

    def test_malformed_command_graceful_handling(self, mock_game_map, command_factory):
        """Comando mal formado devuelve 'Orden inválida (...)' sin romper ejecución."""
        cmd = command_factory("SIN_ESPACIOS", "M")
        report = CommandReporter.format_report(cmd, mock_game_map, turn_number=1)

        assert report.startswith("Orden inválida")
