# tests/test_machiavelli/test_map.py

from machiavelli.map import Map, Province, Sea, Location, Route
from unittest.mock import patch
import pytest

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
            {"name": "Tivoli", "land_routes": [{"destination": "rome"}]},
            {"name": "Florence"},
            {
                "name": "Provence",
                "custom_id": "prove S",
                "sea_routes": [{"destination": "WGOL"},{"destination": "EGOL"},{"destination": "marse"}],
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
                "sea_routes": [{"destination": "EGOL"},{"destination": "prove S"}],
            },
            {
                "name": "Eastern Gulf of Lyons",
                "sea_routes": [{"destination": "WGOL"},{"destination": "prove S"}],
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
    """Comprueba que si el nombre tiene menos de 5 caracteres, el ID se genera sin problemas."""
    provincia = Province(name="Rome")

    assert provincia.name == "Rome"
    assert provincia.id == "rome"

def test_province_creation_with_city_and_economic_values():
    """Comprueba la correcta asignación de tipos de ciudad, puertos e ingresos dinámicos."""
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
    """Comprueba que el ID de un mar se genera usando las iniciales de cada palabra en mayúsculas."""
    mar_largo = Sea(name="Eastern Tyrrhenian Sea")
    mar_corto = Sea(name="Lagoon")

    assert mar_largo.name == "Eastern Tyrrhenian Sea"
    assert mar_largo.id == "ETS"

    assert mar_corto.name == "Lagoon"
    assert mar_corto.id == "L"


def test_map_loading_separates_land_and_sea(mock_json_data):
    """Comprueba que el mapa lee el JSON y clasifica correctamente la tierra de los mares."""
    with patch("machiavelli.map.json.load", return_value=mock_json_data), patch(
        "builtins.open"
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
    """Comprueba que el se purgan correctamente los IDs solicitados de ambos diccionarios."""
    # Le pedimos que excluya una provincia terrestre ('tivol') y un mar ('IS')
    with patch("machiavelli.map.json.load", return_value=mock_json_data), patch(
        "builtins.open"
    ):
        game_map = Map.load_map(exclude_ids=["tivol", "IS"])

    # Verificamos que hayan sido eliminados
    assert "tivol" not in game_map.provinces
    assert "IS" not in game_map.seas

    # Verificamos que el resto de la geografía siga intacta
    assert "rome" in game_map.provinces
    assert "flore" in game_map.provinces
    assert "WTS" in game_map.seas

def test_route_creation_default_values():
    """Comprueba que una ruta estándar se crea correctamente y su estrecho por defecto es None."""
    ruta_libre = Route(destination="rome")

    assert ruta_libre.destination == "rome"
    assert ruta_libre.strait is None


def test_route_creation_with_strait():
    """Comprueba que una ruta con estrecho almacena la provincia que controla el paso."""
    # Ejemplo: Conexión marítima controlada militarmente desde la provincia de Messina ('messi')
    ruta_estrecho = Route(destination="IS", strait="messi")

    assert ruta_estrecho.destination == "IS"
    assert ruta_estrecho.strait == "messi"


def test_location_routes_integration():
    """Comprueba que Province y Sea heredan la lista de rutas y permiten añadir conexiones."""
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

def test_location_exclude_routes():
    """Prueba la función que excluye rutas"""
    test_location = Location(name='Savoy',
        land_routes=[Route(destination='prove'),
            Route(destination='saluz'),
            Route(destination='turin'),
            Route(destination='montf'),
            Route(destination='genoa')],
        sea_routes=[Route(destination='prove S'),
            Route(destination='genoa'), Route(destination='EGOL')])
    
    test_location.exclude_routes(['prove'])

    destinations = [r.destination for r in test_location.land_routes + test_location.sea_routes]

    assert 'prove' not in destinations
    assert 'prove S' not in destinations
    assert 'turin' in destinations

def test_map_loading_separates_land_and_sea(mock_json_data):
    """Comprueba que el mapa lee el JSON, clasifica la geografía y carga sus rutas."""
    with patch("machiavelli.map.json.load", return_value=mock_json_data), patch(
        "builtins.open"
    ):
        game_map = Map.load_map()

    assert "rome" in game_map.provinces
    assert "ETS" in game_map.seas

    # Verificamos que Roma tenga conexiones cargadas desde el archivo
    roma = game_map.provinces["rome"]
    assert len(roma.land_routes) > 0
    assert roma.land_routes[0].destination == "tivol"

    # Verificamos que las rutas de mar también se hayan poblado
    mar_tirreno = game_map.seas["ETS"]
    assert len(mar_tirreno.sea_routes) > 0

def test_map_loading_excludes_routes_to_excluded_locations(mock_json_data):
    """Comprueba que si una localización se excluye, las rutas hacia ella también se eliminan."""
    with patch("machiavelli.map.json.load", return_value=mock_json_data), patch(
        "builtins.open"
    ):
        game_map = Map.load_map(exclude_ids=["tivol"])

    # Certificamos que Tivoli efectivamente no se ha procesado
    assert "tivol" not in game_map.provinces

    # Inspeccionamos Roma, que originalmente conectaba con 'tivol' y con 'ETS' (Mar Tirreno)
    roma = game_map.provinces["rome"]
    
    # La ruta hacia 'tivol' debe haber sido interceptada por el filtro
    destinos_de_roma = [route.destination for route in roma.land_routes + roma.sea_routes]
    assert "tivol" not in destinos_de_roma
    assert "ETS" in destinos_de_roma

def test_map_loading_excludes_double_coasts_by_base_id(mock_json_data):
    """Comprueba que excluir la raíz 'prove' elimina sus costas y limpia las rutas hacia ellas."""
    with patch("machiavelli.map.json.load", return_value=mock_json_data), patch(
        "builtins.open"
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

def test_map_exclude_locations(mock_json_data):
    """Excluimos 'prove' una vez ya creado el mapa."""
    with patch("machiavelli.map.json.load", return_value=mock_json_data), patch(
        "builtins.open"
    ):
        # Excluimos la provincia 'prove'
        game_map = Map.load_map()

    game_map.exclude_locations(["prove"])
    
    # 'prove S' ha sido eliminado de provinces
    assert "prove S" not in game_map.provinces

    # las rutas a 'prove S' se han eliminado de WGOL y EGOL
    wgol = game_map.seas["WGOL"]
    from_wgol = [route.destination for route in wgol.sea_routes]
    
    assert "prove S" not in from_wgol
    assert "EGOL" in from_wgol