from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

_REQUIRED_FILES = {
    "machiavelli/assets/maps/machiavelli.json",
    "machiavelli/assets/scenarios/Aa.json",
    "machiavelli/assets/scenarios/De.json",
    "machiavelli/engine/__init__.py",
    "machiavelli/engine/core.py",
    "machiavelli/db/database.py",
    "machiavelli/database.py",
}

_FORBIDDEN_EXACT_FILES = {
    "machiavelli/engine.py",
    "database.py",
    "cli.log",
}


def _resolve_wheel(tmp_path: Path) -> Path:
    configured_wheel = os.environ.get("MACHIAVELLI_WHEEL")
    if configured_wheel:
        wheel = Path(configured_wheel)
        assert wheel.is_file(), f"No existe el wheel configurado: {wheel}"
        return wheel

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--outdir",
            str(tmp_path),
        ],
        check=True,
    )
    wheels = sorted(tmp_path.glob("machiavelli-*.whl"))
    assert len(wheels) == 1, f"Se esperaba un wheel y se encontraron: {wheels}"
    return wheels[0]


def test_wheel_contains_only_supported_project_files(tmp_path: Path) -> None:
    wheel = _resolve_wheel(tmp_path)

    with ZipFile(wheel) as archive:
        contents = set(archive.namelist())

    assert _REQUIRED_FILES <= contents

    forbidden = {
        name
        for name in contents
        if name in _FORBIDDEN_EXACT_FILES
        or name.startswith(("tests/", "specs/"))
        or name.lower().endswith((".db", ".log"))
    }
    assert not forbidden
