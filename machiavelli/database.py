# machiavelli/database.py
import logging
import sqlite3

_SCHEMA_VERSION = 3

_UPGRADES = (
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
        FOREIGN KEY (game_id, player_id) REFERENCES players(game_id, player_id) ON DELETE CASCADE
    );
    """,
)

# Importamos el logger
logger = logging.getLogger(__name__)


def upgrade(db_path: str):
    """Comprueba el schema de la base de datos y la actualiza, si es necesario.

    Lee el pragma 'user_version' actual de la base de datos y lo compara con la versión del código objetivo
    (`_SCHEMA_VERSION`). Si la base de datos no está actualizada, ejecuta de forma secuencial y ordenada los scripts de
    `_UPGRADES`.

    Si un script de falla en un paso intermedio, los cambios de ese paso concreto se revierten por completo (rollback),
    asegurando que la base de datos quede en un estado consistente y se marca con la versión del último schema
    actualizaco con éxito.

    Args:
        db_path (str): Ruta al archivo de la base de datos SQLite.

    Raises:
        Exception: Si ocurre un error al ejecutar un script de SQL, deshace el paso actual y eleva la Excepción."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # lee la versión actual (0 por defecto)
        cursor.execute("PRAGMA user_version;")
        current = cursor.fetchone()[0]

        if current < _SCHEMA_VERSION:
            logger.warning(f"Actualiza el schema de la BBDD de {current} a {_SCHEMA_VERSION}")

            try:
                for v in range(current, _SCHEMA_VERSION):
                    logger.info(f"Actualizando a la versión  {v + 1}")
                    cursor.executescript(_UPGRADES[v])
                    cursor.execute(f"PRAGMA user_version = {v + 1};")
                else:
                    conn.commit()
                    logger.info("Esquema de la BBDD actualizado con éxito")
            except Exception:
                conn.rollback()
                logger.exception(f"Falló la actualización al schema {v + 1}")
                raise
        else:
            logger.info("No existen actualizaciones de base de datos")

    finally:
        conn.close()
