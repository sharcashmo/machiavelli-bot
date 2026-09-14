from __future__ import annotations

import json
from pathlib import Path

from machiavelli.repositories.map_repository import MapRepository


def test_load_map_builds_domain_map_from_json(tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    map_path.write_text(
        json.dumps(
            {
                "provinces": [{"name": "Rome", "city": "city"}],
                "seas": [{"name": "Ionian Sea"}],
            }
        ),
        encoding="utf-8",
    )

    game_map = MapRepository(map_path).load_map()

    assert list(game_map.provinces) == ["rome"]
    assert list(game_map.seas) == ["IS"]
