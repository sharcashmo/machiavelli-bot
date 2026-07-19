# tests/test_machiavelli/test_versions.py
import sqlite3
from unittest.mock import MagicMock, call, patch

import pytest

from machiavelli.database import upgrade


@patch("machiavelli.database.sqlite3")
@patch("machiavelli.database._SCHEMA_VERSION", 3)
@patch("machiavelli.database._UPGRADES", ["SQL_STEP_1", "SQL_STEP_2", "SQL_STEP_3"])
def test_upgrade_database_from_scratch(mock_sqlite3):
    """Prueba que si la base de datos es nueva (v0), se ejecutan las migraciones."""
    # Creamos los wrappers para la conexión y el cursor
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    # Configuramos la cadena de llamadas:
    # sqlite3.connect() devuelve la conexión falsa
    mock_sqlite3.connect.return_value = mock_conn
    # conn.cursor() devuelve el cursor falso
    mock_conn.cursor.return_value = mock_cursor

    # Simula que 'PRAGMA user_version;' devuelve un 0 (Base de datos nueva)
    mock_cursor.fetchone.return_value = [0]

    # Ejecutamos el upgrade
    upgrade("fake.db")

    # ¿Se abrió la BBDD con la ruta correcta?
    mock_sqlite3.connect.assert_called_once_with("fake.db")

    # ¿Se llegó a ejecutar el script SQL de actualización?
    assert mock_cursor.executescript.call_count == 3

    expected_sql_calls = [call("SQL_STEP_1"), call("SQL_STEP_2"), call("SQL_STEP_3")]
    mock_cursor.executescript.assert_has_calls(expected_sql_calls, any_order=False)

    expected_pragma_calls = [
        call("PRAGMA user_version;"),
        call("PRAGMA user_version = 1;"),
        call("PRAGMA user_version = 2;"),
        call("PRAGMA user_version = 3;"),
    ]
    mock_cursor.execute.assert_has_calls(expected_pragma_calls, any_order=False)

    # ¿Se guardaron los cambios y se cerró la conexión?
    mock_conn.close.assert_called_once()


@patch("machiavelli.database.sqlite3")
@patch("machiavelli.database._SCHEMA_VERSION", 3)
@patch("machiavelli.database._UPGRADES", ["SQL_STEP_1", "SQL_STEP_2", "SQL_STEP_3"])
def test_upgrade_database_partial_migration(mock_sqlite3):
    """Prueba que si la DB ya estaba en v1, solo se aplican los pasos 2 y 3."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_sqlite3.connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    # La base de datos responde que ya está en la versión 1
    mock_cursor.fetchone.return_value = [1]

    upgrade("fake.db")

    # Debería llamarse solo 2 veces (para el paso 2 y el paso 3)
    assert mock_cursor.executescript.call_count == 2

    # Comprobamos que el SQL_PASO_1 fue ignorado olímpicamente
    expected_sql_calls = [call("SQL_STEP_2"), call("SQL_STEP_3")]
    mock_cursor.executescript.assert_has_calls(expected_sql_calls, any_order=False)


@patch("machiavelli.database.sqlite3")
@patch("machiavelli.database._SCHEMA_VERSION", 3)
@patch("machiavelli.database._UPGRADES", ["SQL_STEP_1", "SQL_STEP_2", "SQL_STEP_3"])
def test_upgrade_database_fails_at_second_step(mock_sqlite3):
    """Prueba que si el segundo script falla, se hace rollback y la DB queda en v1."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_sqlite3.connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.return_value = [0]

    # Configurar el comportamiento sucesivo de executescript:
    # Primera llamada (Paso 1): Todo va bien (devuelve None)
    # Segunda llamada (Paso 2): Falla con un error de SQLite
    mock_cursor.executescript.side_effect = [None, sqlite3.OperationalError("Syntax error in Step 2")]

    # El test pasará SOLÓ si la función 'upgrade' relanza este error exacto.
    with pytest.raises(sqlite3.OperationalError, match="Syntax error in Step 2"):
        upgrade("fake.db")

    # Se intentó ejecutar el paso 1 y el paso 2, pero NUNCA el 3
    assert mock_cursor.executescript.call_count == 2
    mock_cursor.executescript.assert_has_calls([call("SQL_STEP_1"), call("SQL_STEP_2")])

    # La versión se actualizó a la 1
    mock_cursor.execute.assert_any_call("PRAGMA user_version = 1;")

    # pero nunca a la versión 2 ni a la 3
    for args, _ in mock_cursor.execute.call_args_list:
        assert "user_version = 2" not in args[0]
        assert "user_version = 3" not in args[0]

    # verificamos los rollback y commits
    mock_conn.rollback.assert_called_once()  # Se deshacen los cambios del paso 2 que falló
    mock_conn.commit.assert_not_called()  # Nunca se llega al commit definitivo del final del bucle
    mock_conn.close.assert_called_once()  # La conexión se cierra obligatoriamente pese al error
