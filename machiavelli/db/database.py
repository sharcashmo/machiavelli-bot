"""Canonical SQLite schema, migration, and connection management."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 5

_UPGRADES: tuple[str, ...] = (
    # SCHEMA 1
    """\
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        channel_id INTEGER UNIQUE,
        scenario_id TEXT,
        turn_number INTEGER DEFAULT 0,
        weekly_deadline TEXT,
        next_deadline TEXT,
        famine TEXT,
        independent_garrisons TEXT
    );

    CREATE TABLE IF NOT EXISTS players (
        game_id INTEGER,
        player_id TEXT,
        discord_id INTEGER,
        controlled_locations TEXT,
        armies TEXT,
        fleets TEXT,
        garrisons TEXT,
        ass_counters TEXT,
        ducats INTEGER,
        rebelled_provinces TEXT,
        rebelled_cities TEXT,
        home_countries TEXT,
        power TEXT,
        PRIMARY KEY (game_id, player_id),
        FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE,
        UNIQUE(game_id, discord_id)
    );

    CREATE TABLE IF NOT EXISTS game_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
    );
    """,
    # SCHEMA 2
    """\
    ALTER TABLE games ADD COLUMN besieges TEXT;
    """,
    # SCHEMA 3
    """\
    CREATE TABLE IF NOT EXISTS commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        player_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        command TEXT NOT NULL,
        target TEXT,
        FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
        FOREIGN KEY (game_id, player_id)
            REFERENCES players(game_id, player_id) ON DELETE CASCADE
    );
    """,
)

_GAME_EVENTS_V4_SQL = """\
CREATE TABLE game_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
)
"""

_EXCHANGE_PROPOSALS_V5_SQL = """\
CREATE TABLE exchange_proposals (
    game_id INTEGER NOT NULL,
    power_a TEXT NOT NULL,
    power_b TEXT NOT NULL,
    proposer_power TEXT NOT NULL,
    give_type TEXT NOT NULL,
    give_value TEXT NOT NULL,
    receive_type TEXT NOT NULL,
    receive_value TEXT NOT NULL,
    PRIMARY KEY (game_id, power_a, power_b),
    CHECK (power_a < power_b),
    CHECK (proposer_power = power_a OR proposer_power = power_b),
    CHECK (give_type IN ('ducats', 'assassin')),
    CHECK (receive_type IN ('ducats', 'assassin')),
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);
"""


def upgrade_connection(conn: sqlite3.Connection) -> None:
    """Apply pending migrations without taking ownership of the connection."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA user_version;")
    row = cursor.fetchone()
    current_version = int(row[0]) if row else 0

    if current_version >= _SCHEMA_VERSION:
        logger.info("Esquema de BBDD actualizado (versión %d).", current_version)
        return

    logger.warning(
        "Actualizando esquema de BBDD de versión %d a %d.",
        current_version,
        _SCHEMA_VERSION,
    )

    target_version = current_version
    try:
        for version in range(current_version, min(_SCHEMA_VERSION, 3)):
            target_version = version + 1
            logger.info("Aplicando migración a versión %d...", target_version)
            cursor.executescript(_UPGRADES[version])
            cursor.execute(f"PRAGMA user_version = {target_version};")
        conn.commit()

        if current_version < 4 <= _SCHEMA_VERSION:
            target_version = 4
            logger.info("Aplicando migración a versión 4...")
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("DROP TABLE game_events")
            cursor.execute(_GAME_EVENTS_V4_SQL)
            cursor.execute("PRAGMA user_version = 4")
            conn.commit()

        if current_version < 5 <= _SCHEMA_VERSION:
            target_version = 5
            logger.info("Aplicando migración a versión 5...")
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(_EXCHANGE_PROPOSALS_V5_SQL)
            cursor.execute("PRAGMA user_version = 5")
            conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Falló la actualización al esquema %d.", target_version)
        raise

    logger.info(
        "Esquema de BBDD actualizado con éxito a la versión %d.",
        _SCHEMA_VERSION,
    )


def upgrade(db_path: str | Path) -> None:
    """Open a SQLite database, apply pending migrations, and always close it."""
    conn = sqlite3.connect(db_path)
    try:
        upgrade_connection(conn)
    finally:
        conn.close()


class DatabaseManager:
    """Configure SQLite connections and initialize the canonical schema."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def get_connection(self) -> sqlite3.Connection:
        """Open a connection and apply the required per-session configuration."""
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def init_db(self) -> None:
        """Initialize or upgrade the database through the canonical migration path."""
        conn = self.get_connection()
        try:
            upgrade_connection(conn)
        finally:
            conn.close()
