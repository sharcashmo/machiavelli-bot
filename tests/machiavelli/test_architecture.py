import ast
from pathlib import Path

from machiavelli import database as public_database
from machiavelli.db import database as canonical_database
from machiavelli.engine import GameEngine as PublicGameEngine
from machiavelli.engine.core import GameEngine


def _module_level_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in Path("machiavelli").rglob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in module.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                definitions.append(path)
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name
            ):
                definitions.append(path)
    return definitions


def test_public_game_engine_is_canonical() -> None:
    assert PublicGameEngine is GameEngine


def test_public_database_api_is_canonical() -> None:
    assert public_database.upgrade is canonical_database.upgrade
    assert public_database.upgrade_connection is canonical_database.upgrade_connection
    assert public_database.DatabaseManager is canonical_database.DatabaseManager


def test_private_migration_tables_are_not_public() -> None:
    assert not hasattr(public_database, "_UPGRADES")
    assert not hasattr(public_database, "_SCHEMA_VERSION")


def test_migration_tables_have_single_canonical_definitions() -> None:
    canonical_path = Path("machiavelli/db/database.py")
    assert _module_level_definitions("_UPGRADES") == [canonical_path]
    assert _module_level_definitions("_SCHEMA_VERSION") == [canonical_path]


def test_forbidden_legacy_files_do_not_exist() -> None:
    forbidden_paths = (
        Path("machiavelli/engine.py"),
        Path("database.py"),
        Path("cli.log"),
    )
    assert not [path for path in forbidden_paths if path.exists()]


def test_discord_imports_only_the_public_service_boundary() -> None:
    module = ast.parse(Path("machiavelli/discord.py").read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module or ""
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
    )

    assert "sqlite3" not in imports
    assert not any(name.startswith("machiavelli.db") for name in imports)


def test_discord_has_no_public_dislodgement_resolver_parameter() -> None:
    module = ast.parse(Path("machiavelli/discord.py").read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        assert "dislodgement_resolver" not in {
            parameter.arg for parameter in parameters
        }


def test_turn_producers_do_not_build_presentation_or_legacy_records() -> None:
    paths = (
        Path("machiavelli/game/events.py"),
        Path("machiavelli/engine/setup.py"),
        Path("machiavelli/engine/income.py"),
        Path("machiavelli/engine/maintenance.py"),
        Path("machiavelli/engine/disasters.py"),
        Path("machiavelli/engine/expenditure.py"),
        Path("machiavelli/engine/bribes.py"),
        Path("machiavelli/engine/rebellions.py"),
        Path("machiavelli/engine/control.py"),
        Path("machiavelli/engine/military.py"),
        Path("machiavelli/engine/core.py"),
    )
    forbidden_fragments = (
        "tipo|json",
        "**",
        "##",
        "<@",
        "@everyone",
        "@here",
        "`",
    )

    for path in paths:
        module = ast.parse(path.read_text(encoding="utf-8"))
        strings = (
            node.value
            for node in ast.walk(module)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        assert not [
            value
            for value in strings
            if any(fragment in value for fragment in forbidden_fragments)
        ], path


def test_game_has_no_removed_turn_algorithms_or_reporter() -> None:
    from machiavelli.game.game import Game

    for name in ("initial_setup", "spring_start", "turn_report"):
        assert not hasattr(Game, name)
