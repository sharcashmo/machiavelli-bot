# tests/machiavelli/services/test_player_reporter.py
from unittest.mock import MagicMock

import pytest

from machiavelli.game.tables import GameTables
from machiavelli.services.player_reporter import PlayerReporter


class FakeLocation:
    """Stub simple para simular provincias y mares del mapa."""

    def __init__(self, name: str, city: str):
        self.name = name
        self.city = city


@pytest.fixture
def mock_game_map():
    """Mock ligero de GameMap con provincias y mares."""
    game_map = MagicMock()
    game_map.provinces = {
        "milan": FakeLocation("Milan", "fortified"),
        "venic": FakeLocation("Venice", "fortified"),
        "flore": FakeLocation("Florence", "city"),
        "naple": FakeLocation("Naples", None),
    }
    game_map.seas = {
        "UA": FakeLocation("Upper Adriatic", None),
    }
    return game_map


@pytest.fixture
def mock_game(mock_game_map):
    """Mock de Game con mapa y conjunto de asedios por defecto."""
    game = MagicMock()
    game.map = mock_game_map
    game.besieges = set()
    return game


class TestPlayerReporter:
    def test_active_player_full_report(self, mock_game, monkeypatch):
        """Verifica el informe completo de un jugador activo con todos sus atributos."""
        monkeypatch.setattr(
            GameTables,
            "powers",
            {
                "V": "Venice",
                "L": "Florence",
                "M": "Milan",
                "N": "Naples",
            },
        )

        player = MagicMock()
        player.game = mock_game
        player.power = "V"
        player.discord_id = 123456789
        player.home_countries = ["V", "L"]
        player.ducats = 75
        player.ass_counters = ["M"]
        player.controlled_locations = ["milan", "venic"]
        player.rebelled_provinces = ["naple"]
        player.rebelled_cities = []
        player.armies = ["milan"]
        player.fleets = ["UA"]
        player.garrisons = ["venic"]

        mock_game.besieges = {"milan"}

        report = PlayerReporter.generate_report(player)

        assert "### 🏰 __**Venice (<@123456789>)**__" in report
        assert "> 👑 **Naciones controladas (2):** Venice y Florence" in report
        assert "> 💰 **Recursos:** 75 ducados." in report
        assert "> 🗡️ **Fichas de asesinato (1):** Milan" in report
        assert (
            "> 🗺️ **Provincias controladas (2 provincias, 2 ciudades):** Milan y Venice"
            in report
        )
        assert "> 🔥 **Rebeliones:** Naples" in report
        assert "> ⚔️ **Ejércitos:** Milan (asediando)" in report
        assert "> ⚓ **Flotas:** Upper Adriatic" in report
        assert "> 🛡️ **Guarniciones:** Venice" in report

    def test_eliminated_player_report(self, mock_game, monkeypatch):
        """Verifica que un jugador sin países natales sea reportado como eliminado."""
        monkeypatch.setattr(GameTables, "powers", {"V": "Venice"})

        player = MagicMock()
        player.game = mock_game
        player.power = "V"
        player.discord_id = 123456789
        player.home_countries = []

        report = PlayerReporter.generate_report(player)

        assert len(report) == 2
        assert report[0] == "### 🏰 __**Venice (<@123456789>)**__"
        assert report[1] == "> ❌ **Eliminado**"

    @pytest.mark.parametrize(
        "names, default, expected",
        [
            ([], "Ninguno", "Ninguno"),
            (["Milan"], "Ninguno", "Milan"),
            (["Milan", "Venice"], "Ninguno", "Milan y Venice"),
            (["Milan", "Venice", "Florence"], "Ninguno", "Milan, Venice y Florence"),
        ],
    )
    def test_format_joined_names(self, names, default, expected):
        """Prueba la correcta gramática española en las listas unidas por 'y'."""
        result = PlayerReporter._format_joined_names(names, default)
        assert result == expected
