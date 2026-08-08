import sqlite3
from pathlib import Path

import pytest

from machiavelli.db import database as database_module
from machiavelli.db.database import (
    _SCHEMA_VERSION,
    _UPGRADES,
    DatabaseManager,
    upgrade,
    upgrade_connection,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Proporciona una ruta válida para una BBDD temporal."""
    return tmp_path / "test_machiavelli.db"


@pytest.fixture
def repo(db_path: Path) -> DatabaseManager:
    """Instancia de GameRepository lista para usar."""
    return DatabaseManager(db_path)


def test_init(tmp_path: Path) -> None:
    """Verifica que el constructor acepte tanto cadenas como objetos Path."""
    str_path = str(tmp_path / "str_db.db")
    path_obj = tmp_path / "path_db.db"

    repo_str = DatabaseManager(str_path)
    repo_path = DatabaseManager(path_obj)

    assert repo_str.db_path == Path(str_path)
    assert repo_path.db_path == path_obj


def test_get_connection_creates_parent_directories(tmp_path: Path) -> None:
    """Debe crear los directorios padres si no existen al intentar conectar."""
    deep_path = tmp_path / "nested" / "subfolder" / "game.db"
    repo = DatabaseManager(deep_path)

    assert not deep_path.parent.exists()

    conn = repo.get_connection()
    conn.close()

    assert deep_path.parent.exists()


def test_get_connection_configures_pragmas_and_row_factory(
    repo: DatabaseManager,
) -> None:
    """Verifica que la conexión configure el row_factory y los PRAGMAs requeridos."""
    conn = repo.get_connection()

    # 1. Row factory
    assert conn.row_factory == sqlite3.Row

    # 2. Foreign keys activadas
    cursor = conn.cursor()
    fk_status = cursor.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk_status == 1

    # 3. Journal mode en WAL
    journal_mode = cursor.execute("PRAGMA journal_mode;").fetchone()[0]
    assert journal_mode.lower() == "wal"

    conn.close()


def test_get_connection_foreign_keys_are_enforced(repo: DatabaseManager) -> None:
    """Garantiza que la BBDD realmente rechace violaciones de clave foránea."""
    repo.init_db()

    conn = repo.get_connection()
    cursor = conn.cursor()

    # Intentar insertar un jugador asignado a un game_id inexistente (999)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO players (game_id, player_id, discord_id) VALUES (?, ?, ?)",
            (999, "FRANCE", 123456789),
        )
    conn.close()


def test_init_db_creates_schema_from_scratch(repo: DatabaseManager) -> None:
    """Crea la base de datos desde cero hasta la última versión del esquema."""
    repo.init_db()

    conn = repo.get_connection()
    cursor = conn.cursor()

    # 1. Comprobar que el PRAGMA user_version es el objetivo
    version = cursor.execute("PRAGMA user_version;").fetchone()[0]
    assert version == _SCHEMA_VERSION

    # 2. Comprobar existencia de tablas principales
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row["name"] for row in cursor.fetchall()}

    expected_tables = {
        "games",
        "players",
        "game_events",
        "commands",
        "exchange_proposals",
    }
    assert expected_tables.issubset(tables)

    # 3. Comprobar las columnas de games y el historial tipado v4.
    cursor.execute("PRAGMA table_info(games);")
    columns = {row["name"] for row in cursor.fetchall()}
    assert "besieges" in columns
    cursor.execute("PRAGMA table_info(game_events);")
    event_columns = [row["name"] for row in cursor.fetchall()]
    assert event_columns == ["id", "game_id", "event_type", "data_json"]

    conn.close()


def test_init_db_is_idempotent(repo: DatabaseManager) -> None:
    """Ejecutar init_db múltiples veces en una BBDD no falla ni altera la versión."""
    repo.init_db()
    repo.init_db()  # Segunda llamada debe ser un no-op

    conn = repo.get_connection()
    version = conn.execute("PRAGMA user_version;").fetchone()[0]
    conn.close()

    assert version == _SCHEMA_VERSION


def test_init_db_incremental_migration(db_path: Path) -> None:
    """Prueba que una BBDD canónica en versión 1 se actualice correctamente."""
    conn = sqlite3.connect(db_path)
    conn.executescript(_UPGRADES[0])
    conn.execute("PRAGMA user_version = 1;")
    conn.commit()
    conn.close()

    # Ejecutar init_db a través del repositorio
    repo = DatabaseManager(db_path)
    repo.init_db()

    # Verificar que saltó de v1 a v4
    conn = repo.get_connection()
    version = conn.execute("PRAGMA user_version;").fetchone()[0]

    # Comprobar que la tabla `commands` (añadida en v3) existe ahora
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='commands';"
    )
    command_table = cursor.fetchone()

    conn.close()

    assert version == _SCHEMA_VERSION
    assert command_table is not None


def test_init_db_rolls_back_on_migration_failure(
    repo: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si script de migración falla, debe hacer rollback y no avanzar user_version."""
    import machiavelli.db.database as repo_module

    # Inyectar una migración corrupta en la versión 1
    bad_upgrades = ("CREATE TABLE games (id INTEGER);", "SINTAXIS_SQL_INVALIDA;")
    monkeypatch.setattr(repo_module, "_UPGRADES", bad_upgrades)
    monkeypatch.setattr(repo_module, "_SCHEMA_VERSION", 2)

    with pytest.raises(sqlite3.OperationalError):
        repo.init_db()

    # La primera migración debió aplicarse (v1), pero la v2 debió fallar y mantener v1
    conn = repo.get_connection()
    version = conn.execute("PRAGMA user_version;").fetchone()[0]
    conn.close()

    assert version == 1


def test_upgrade_connection_migrates_version_two(db_path: Path) -> None:
    """Una base en versión 2 recibe únicamente la tabla de comandos."""
    conn = sqlite3.connect(db_path)
    try:
        for script in _UPGRADES[:2]:
            conn.executescript(script)
        conn.execute("PRAGMA user_version = 2;")
        conn.commit()

        upgrade_connection(conn)

        assert conn.execute("PRAGMA user_version;").fetchone()[0] == _SCHEMA_VERSION
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='commands';"
        ).fetchone() == ("commands",)
    finally:
        conn.close()


def test_upgrade_connection_does_not_close_caller_connection(db_path: Path) -> None:
    """La función canónica no toma propiedad de la conexión recibida."""
    conn = sqlite3.connect(db_path)
    try:
        upgrade_connection(conn)
        assert conn.execute("SELECT 1;").fetchone() == (1,)
    finally:
        conn.close()


@pytest.mark.parametrize("source_version", [1, 2, 3])
def test_upgrade_preserves_state_and_resets_events(
    db_path: Path, source_version: int
) -> None:
    """La v4 conserva el agregado y reinicia únicamente el historial efímero."""
    conn = sqlite3.connect(db_path)
    try:
        for script in _UPGRADES[:source_version]:
            conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {source_version};")
        conn.execute(
            "INSERT INTO games "
            "(name, channel_id, scenario_id, turn_number, famine, "
            "independent_garrisons) VALUES (?, ?, ?, ?, ?, ?)",
            ("Histórica", 123, "Be", 7, '["milan"]', '["pisa"]'),
        )
        game_id = conn.execute(
            "SELECT id FROM games WHERE name = ?", ("Histórica",)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO players "
            "(game_id, player_id, discord_id, controlled_locations, armies, "
            "fleets, garrisons, ass_counters, ducats, rebelled_provinces, "
            "rebelled_cities, home_countries, power) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                game_id,
                "Florencia",
                456,
                '["florence"]',
                '["florence"]',
                "[]",
                "[]",
                "[]",
                12,
                "[]",
                "[]",
                '["Florencia"]',
                "Florencia",
            ),
        )
        conn.execute(
            "INSERT INTO game_events (game_id, message) VALUES (?, ?)",
            (game_id, "Evento histórico"),
        )
        if source_version == 3:
            conn.execute(
                "INSERT INTO commands "
                "(game_id, player_id, actor, command, target) "
                "VALUES (?, ?, ?, ?, ?)",
                (game_id, "Florencia", "A florence", "H", None),
            )
        conn.commit()

        upgrade_connection(conn)

        assert conn.execute("PRAGMA user_version;").fetchone()[0] == _SCHEMA_VERSION
        assert conn.execute(
            "SELECT name, channel_id, turn_number FROM games WHERE id = ?",
            (game_id,),
        ).fetchone() == ("Histórica", 123, 7)
        assert conn.execute(
            "SELECT player_id, discord_id, ducats FROM players WHERE game_id = ?",
            (game_id,),
        ).fetchone() == ("Florencia", 456, 12)
        assert conn.execute(
            "SELECT COUNT(*) FROM game_events WHERE game_id = ?", (game_id,)
        ).fetchone() == (0,)
        assert [
            row[1] for row in conn.execute("PRAGMA table_info(game_events)").fetchall()
        ] == ["id", "game_id", "event_type", "data_json"]
        if source_version == 3:
            assert conn.execute(
                "SELECT actor, command, target FROM commands WHERE game_id = ?",
                (game_id,),
            ).fetchone() == ("A florence", "H", None)
    finally:
        conn.close()


def test_database_manager_and_upgrade_create_equivalent_schemas(
    tmp_path: Path,
) -> None:
    """Las dos entradas públicas producen el mismo esquema y versión."""
    upgrade_path = tmp_path / "upgrade.db"
    manager_path = tmp_path / "manager.db"

    upgrade(upgrade_path)
    DatabaseManager(manager_path).init_db()

    def schema_snapshot(path: Path) -> tuple[int, tuple[tuple[str, str | None], ...]]:
        conn = sqlite3.connect(path)
        try:
            version = conn.execute("PRAGMA user_version;").fetchone()[0]
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name;"
            ).fetchall()
            return version, tuple(rows)
        finally:
            conn.close()

    assert schema_snapshot(upgrade_path) == schema_snapshot(manager_path)


def test_database_manager_delegates_to_upgrade_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DatabaseManager no mantiene un segundo bucle de migración."""

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()
    manager = DatabaseManager("ignored.db")
    calls: list[FakeConnection] = []
    monkeypatch.setattr(manager, "get_connection", lambda: connection)
    monkeypatch.setattr(
        database_module,
        "upgrade_connection",
        lambda conn: calls.append(conn),
    )

    manager.init_db()

    assert calls == [connection]
    assert connection.closed


class _FailingMigrationCursor(sqlite3.Cursor):
    def execute(self, sql: str, parameters=()):  # type: ignore[no-untyped-def]
        result = super().execute(sql, parameters)
        normalized = " ".join(sql.split()).upper()
        connection = self.connection
        if not isinstance(connection, _FailingMigrationConnection):
            return result
        if connection.fail_after == "drop" and normalized.startswith(
            "DROP TABLE GAME_EVENTS"
        ):
            raise sqlite3.OperationalError("fallo tras drop")
        if connection.fail_after == "create" and normalized.startswith(
            "CREATE TABLE GAME_EVENTS"
        ):
            raise sqlite3.OperationalError("fallo tras create")
        if connection.fail_after == "version" and normalized.startswith(
            "PRAGMA USER_VERSION = 4"
        ):
            raise sqlite3.OperationalError("fallo tras version")
        if connection.fail_after == "v5_create" and normalized.startswith(
            "CREATE TABLE EXCHANGE_PROPOSALS"
        ):
            raise sqlite3.OperationalError("fallo tras create v5")
        if connection.fail_after == "v5_version" and normalized.startswith(
            "PRAGMA USER_VERSION = 5"
        ):
            raise sqlite3.OperationalError("fallo tras version v5")
        return result


class _FailingMigrationConnection(sqlite3.Connection):
    fail_after: str | None = None

    def cursor(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("factory", _FailingMigrationCursor)
        return super().cursor(*args, **kwargs)

    def commit(self) -> None:
        if (
            self.fail_after == "commit"
            and self.execute("PRAGMA user_version").fetchone()[0] == 4
        ):
            raise sqlite3.OperationalError("fallo antes de commit")
        super().commit()


def _seed_v3(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        for script in _UPGRADES:
            conn.executescript(script)
        conn.execute("PRAGMA user_version = 3")
        conn.execute("INSERT INTO games (name, channel_id) VALUES (?, ?)", ("v3", 9))
        game_id = conn.execute("SELECT id FROM games").fetchone()[0]
        conn.execute(
            "INSERT INTO game_events (game_id, message) VALUES (?, ?)",
            (game_id, "histórico"),
        )
        conn.commit()
        return game_id
    finally:
        conn.close()


@pytest.mark.parametrize("fail_after", ["drop", "create", "version", "commit"])
def test_v4_migration_rolls_back_table_rows_and_version(
    db_path: Path, fail_after: str
) -> None:
    game_id = _seed_v3(db_path)
    conn = sqlite3.connect(db_path, factory=_FailingMigrationConnection)
    conn.fail_after = fail_after
    try:
        with pytest.raises(sqlite3.OperationalError):
            upgrade_connection(conn)
    finally:
        conn.close()

    check = sqlite3.connect(db_path)
    try:
        assert check.execute("PRAGMA user_version").fetchone() == (3,)
        assert [
            row[1] for row in check.execute("PRAGMA table_info(game_events)").fetchall()
        ] == ["id", "game_id", "message"]
        assert check.execute(
            "SELECT message FROM game_events WHERE game_id = ?", (game_id,)
        ).fetchone() == ("histórico",)
    finally:
        check.close()


def _seed_v4(
    path: Path,
) -> tuple[int, dict[str, tuple[tuple[object, ...], ...]]]:
    conn = sqlite3.connect(path)
    try:
        for script in _UPGRADES:
            conn.executescript(script)
        conn.execute("DROP TABLE game_events")
        conn.execute(
            """
            CREATE TABLE game_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("PRAGMA user_version = 4")
        conn.execute(
            "INSERT INTO games (name, channel_id, scenario_id, turn_number) "
            "VALUES (?, ?, ?, ?)",
            ("v4", 14, "Be", 3),
        )
        game_id = conn.execute("SELECT id FROM games").fetchone()[0]
        conn.execute(
            "INSERT INTO players (game_id, player_id, discord_id, ducats) "
            "VALUES (?, ?, ?, ?)",
            (game_id, "P1", 41, 9),
        )
        conn.execute(
            "INSERT INTO commands (game_id, player_id, actor, command, target) "
            "VALUES (?, ?, ?, ?, ?)",
            (game_id, "P1", "A rome", "H", None),
        )
        conn.execute(
            "INSERT INTO game_events (game_id, event_type, data_json) VALUES (?, ?, ?)",
            (game_id, "expense", '{"amount":1}'),
        )
        conn.commit()
        snapshot = {
            table: tuple(
                tuple(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
            )
            for table in ("games", "players", "commands", "game_events")
        }
        return game_id, snapshot
    finally:
        conn.close()


def test_v4_to_v5_preserves_existing_rows_and_creates_exact_table(
    db_path: Path,
) -> None:
    _game_id, snapshot_before = _seed_v4(db_path)
    conn = sqlite3.connect(db_path)
    try:
        upgrade_connection(conn)

        assert conn.execute("PRAGMA user_version").fetchone() == (5,)
        for table, expected_rows in snapshot_before.items():
            actual_rows = tuple(
                tuple(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
            )
            assert actual_rows == expected_rows

        assert [
            (row[1], row[5])
            for row in conn.execute("PRAGMA table_info(exchange_proposals)")
        ] == [
            ("game_id", 1),
            ("power_a", 2),
            ("power_b", 3),
            ("proposer_power", 0),
            ("give_type", 0),
            ("give_value", 0),
            ("receive_type", 0),
            ("receive_value", 0),
        ]
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'exchange_proposals'"
        ).fetchone()[0]
        assert "CHECK (power_a < power_b)" in sql
        assert "CHECK (proposer_power = power_a OR proposer_power = power_b)" in sql
        assert "CHECK (give_type IN ('ducats', 'assassin'))" in sql
        assert "CHECK (receive_type IN ('ducats', 'assassin'))" in sql
        assert conn.execute("PRAGMA foreign_key_list(exchange_proposals)").fetchone()[
            2:5
        ] == ("games", "game_id", "id")
    finally:
        conn.close()


@pytest.mark.parametrize("fail_after", ["v5_create", "v5_version"])
def test_v5_migration_rolls_back_table_and_version(
    db_path: Path, fail_after: str
) -> None:
    game_id, snapshot_before = _seed_v4(db_path)
    conn = sqlite3.connect(db_path, factory=_FailingMigrationConnection)
    conn.fail_after = fail_after
    try:
        with pytest.raises(sqlite3.OperationalError):
            upgrade_connection(conn)
    finally:
        conn.close()

    check = sqlite3.connect(db_path)
    try:
        assert check.execute("PRAGMA user_version").fetchone() == (4,)
        assert (
            check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'exchange_proposals'"
            ).fetchone()
            is None
        )
        for table, expected_rows in snapshot_before.items():
            actual_rows = tuple(
                tuple(row)
                for row in check.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
            )
            assert actual_rows == expected_rows
        assert check.execute(
            "SELECT name, turn_number FROM games WHERE id = ?", (game_id,)
        ).fetchone() == ("v4", 3)
    finally:
        check.close()
