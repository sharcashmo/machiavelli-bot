"""API pública de SQLite compatible con versiones anteriores."""

from machiavelli.db.database import (
    DatabaseManager,
    upgrade,
    upgrade_connection,
)

__all__ = [
    "DatabaseManager",
    "upgrade",
    "upgrade_connection",
]
