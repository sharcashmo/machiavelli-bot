# tests/machiavelli/game/test_map.py

from unittest.mock import patch

import pytest

from machiavelli.game.map import Map, MovementMode, Province, Route, Sea


@pytest.fixture
def mock_json_data():
    return {
        "provinces": [
            {
                "name": "Rome",
                "city": "city",
                "land_routes": [{"destination": "tivol"}],
                "sea_routes": [{"destination": "ETS"}],
            },
            {
                "name": "Tivoli",
                "city": "fortress",
                "land_routes": [{"destination": "rome"}],
            },
            {"name": "Florence"},
            {
                "name": "Provence",
                "city": "fortified",
                "land_routes": [{"destination": "avign"}],
            },
            {
                "name": "Provence South Coast",
                "custom_id": "prove S",
                "sea_routes": [
                    {"destination": "WGOL"},
                    {"destination": "EGOL"},
                ],
            },
            {
                "name": "Provence East Coast",
                "custom_id": "prove E",
                "sea_routes": [
                    {"destination": "EGOL"},
                ],
            },
        ],
        "seas": [
            {"name": "Western Tyrrhenian Sea", "sea_routes": []},
            {"name": "Ionian Sea", "sea_routes": []},
            {
                "name": "Eastern Tyrrhenian Sea",
                "sea_routes": [{"destination": "rome"}],
            },
            {
                "name": "Western Gulf of Lyons",
                "sea_routes": [{"destination": "EGOL"}, {"destination": "prove S"}],
            },
            {
                "name": "Eastern Gulf of Lyons",
                "sea_routes": [
                    {"destination": "WGOL"},
                    {"destination": "prove S"},
                    {"destination": "prove E"},
                ],
            },
        ],
    }


def test_province_creation_generates_id_from_long_name():
    """Comprueba que el ID se genera en minúsculas y se recorta a 5 caracteres."""
    provincia = Province(name="Florence")

    assert provincia.name == "Florence"
    assert provincia.id == "flore"
    assert provincia.city is None


def test_province_creation_generates_id_from_short_name():
    """Si el nombre tiene menos de 5 caracteres, el ID se genera sin problemas."""
    provincia = Province(name="Rome")

    assert provincia.name == "Rome"
    assert provincia.id == "rome"


def test_province_creation_with_city_and_economic_values():
    """Correcta asignación de tipos de ciudad, puertos e ingresos dinámicos."""
    # Roma tiene ciudad, por lo que ahora sus ingresos por defecto deben pasar a ser 1
    roma = Province(name="Rome", city="city")
    assert roma.city == "city"
    assert roma.has_port is False
    assert roma.major_city == 1
    assert roma.is_venice is False

    # Una provincia sin ciudad debe seguir reportando 0 patacos de ingresos
    florencia_rural = Province(name="Florence")
    assert florencia_rural.city is None
    assert florencia_rural.major_city == 0

    # Venecia sobreescribe el valor por defecto a 3 de forma explícita
    venecia = Province(
        name="Venice",
        city="fortified",
        has_port=True,
        major_city=3,
        is_venice=True,
    )
    assert venecia.city == "fortified"
    assert venecia.major_city == 3


def test_sea_creation_generates_id_from_initials():
    """El ID de un mar se genera usando las iniciales de cada palabra en mayúsculas."""
    mar_largo = Sea(name="Eastern Tyrrhenian Sea")
    mar_corto = Sea(name="Lagoon")

    assert mar_largo.name == "Eastern Tyrrhenian Sea"
    assert mar_largo.id == "ETS"

    assert mar_corto.name == "Lagoon"
    assert mar_corto.id == "L"


def test_map_loading_separates_land_and_sea(mock_json_data):
    """El mapa lee el JSON y clasifica correctamente la tierra de los mares."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    # Comprobamos la carga de Provinces en el diccionario
    assert "rome" in game_map.provinces
    assert "tivol" in game_map.provinces
    assert "flore" in game_map.provinces
    assert game_map.provinces["rome"].name == "Rome"

    # Comprobamos la carga de Seas en el diccionario
    assert "WTS" in game_map.seas
    assert "IS" in game_map.seas
    assert game_map.seas["WTS"].name == "Western Tyrrhenian Sea"


def test_map_loading_applies_exclusions(mock_json_data):
    """Se purgan correctamente los IDs solicitados de ambos diccionarios."""
    # Le pedimos que excluya una provincia terrestre ('tivol') y un mar ('IS')
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map(exclude_ids=["tivol", "IS"])

    # Verificamos que hayan sido eliminados
    assert "tivol" not in game_map.provinces
    assert "IS" not in game_map.seas

    # Verificamos que el resto de la geografía siga intacta
    assert "rome" in game_map.provinces
    assert "flore" in game_map.provinces
    assert "WTS" in game_map.seas


def test_map_loading_without_fortress(mock_json_data):
    """Se eliminan los fortress del mapa."""
    # Le pedimos que excluya una provincia terrestre ('tivol') y un mar ('IS')
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map(fortress_active=False)

    # Verificamos que el fortress de "tivol" no existe
    assert game_map.provinces["tivol"].city is None


def test_route_creation_default_values():
    """Una ruta estándar se crea correctamente y su estrecho por defecto es None."""
    ruta_libre = Route(destination="rome")

    assert ruta_libre.destination == "rome"
    assert ruta_libre.strait is None


def test_route_creation_with_strait():
    """Una ruta con estrecho almacena la provincia que controla el paso."""
    # Ejemplo: Conexión marítima controlada militarmente desde la provincia de Messina
    ruta_estrecho = Route(destination="IS", strait="messi")

    assert ruta_estrecho.destination == "IS"
    assert ruta_estrecho.strait == "messi"


def test_location_routes_integration():
    """Province y Sea heredan la lista de rutas y permiten añadir conexiones."""
    roma = Province(name="Rome", city="city")
    mar_tirreno = Sea(name="Eastern Tyrrhenian Sea")

    # Al crearse, las rutas deben estar vacías
    assert roma.land_routes == []
    assert mar_tirreno.sea_routes == []

    # Simulamos conexiones: Roma conecta con Tivoli por tierra y con el Tirreno por mar
    roma.land_routes.append(Route(destination="tivol"))
    roma.sea_routes.append(Route(destination="ETS"))

    # El Mar Tirreno conecta de vuelta con Roma
    mar_tirreno.sea_routes.append(Route(destination="rome"))

    # Verificaciones del grafo
    assert len(roma.land_routes) == 1
    assert len(roma.sea_routes) == 1
    assert roma.land_routes[0].destination == "tivol"
    assert roma.sea_routes[0].destination == "ETS"

    assert len(mar_tirreno.sea_routes) == 1
    assert mar_tirreno.sea_routes[0].destination == "rome"


def test_map_loading_excludes_routes_to_excluded_locations(mock_json_data):
    """Comprueba que si una localización se excluye, las rutas hacia ella también."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map(exclude_ids=["tivol"])

    # Certificamos que Tivoli efectivamente no se ha procesado
    assert "tivol" not in game_map.provinces

    # Inspeccionamos Roma, que originalmente conectaba con 'tivol' y con 'ETS'
    roma = game_map.provinces["rome"]

    # La ruta hacia 'tivol' debe haber sido interceptada por el filtro
    destinos_de_roma = [
        route.destination for route in roma.land_routes + roma.sea_routes
    ]
    assert "tivol" not in destinos_de_roma
    assert "ETS" in destinos_de_roma


def test_map_loading_excludes_double_coasts_by_base_id(mock_json_data):
    """Excluir 'prove' elimina sus costas y limpia las rutas hacia ellas."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        # Excluimos la provincia 'prove'
        game_map = Map.load_map(exclude_ids=["prove"])

    # 'prove S' ha sido eliminado de provinces
    assert "prove S" not in game_map.provinces

    # las rutas a 'prove S' se han eliminado de WGOL y EGOL
    wgol = game_map.seas["WGOL"]
    from_wgol = [route.destination for route in wgol.sea_routes]

    assert "prove S" not in from_wgol
    assert "EGOL" in from_wgol


def test_adjacent_locations_default_both_modes(mock_json_data):
    """Comprueba que por defecto (MovementMode.BOTH) devuelve rutas de tierra y mar."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    adjacent = game_map.adjacent_locations("rome")

    assert "tivol" in adjacent
    assert "ETS" in adjacent


def test_adjacent_locations_land_mode_only(mock_json_data):
    """Comprueba que MovementMode.LAND solo devuelve las rutas terrestres."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    adjacent = game_map.adjacent_locations("rome", mode=MovementMode.LAND)

    assert "tivol" in adjacent
    assert "ETS" not in adjacent


def test_adjacent_locations_sea_mode_only(mock_json_data):
    """Comprueba que MovementMode.SEA solo devuelve las rutas marítimas."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    adjacent = game_map.adjacent_locations("rome", mode=MovementMode.SEA)

    assert "ETS" in adjacent
    assert "tivol" not in adjacent


def test_adjacent_locations_double_coast_base_in_both_mode(mock_json_data):
    """Comprueba que los destinos de las costas se añaden al listado."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    adjacent = game_map.adjacent_locations("prove", mode=MovementMode.BOTH)

    assert "avign" in adjacent
    assert "WGOL" in adjacent
    assert "EGOL" in adjacent


def test_adjacent_locations_double_coast_destination_normalizes_to_base(mock_json_data):
    """Comprueba que si una ruta marítima llega a una costa se incluye la base."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    # WGOL tiene ruta hacia 'prove S'
    adjacent = game_map.adjacent_locations("WGOL", mode=MovementMode.BOTH)

    assert "prove S" in adjacent
    assert "prove" in adjacent


def test_adjacent_locations_invalid_origin_raises_keyerror(mock_json_data):
    """Comprueba que si el origen no existe en el mapa se lanza KeyError."""
    with (
        patch("machiavelli.game.map.json.load", return_value=mock_json_data),
        patch("builtins.open"),
    ):
        game_map = Map.load_map()

    with pytest.raises(KeyError):
        game_map.adjacent_locations("invalid_id")
