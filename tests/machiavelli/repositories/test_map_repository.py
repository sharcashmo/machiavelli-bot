from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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

    game_map = MapRepository(map_path).load_map(map_id="machiavelli")

    assert list(game_map.provinces) == ["rome"]
    assert list(game_map.seas) == ["IS"]


def test_load_map_uses_map_id_to_resolve_the_package_resource() -> None:
    raw_map = {"provinces": [], "seas": []}

    with patch(
        "machiavelli.repositories.map_repository.read_package_json",
        return_value=raw_map,
    ) as read_package_json:
        MapRepository().load_map(map_id="custom")

    read_package_json.assert_called_once_with("assets/maps/custom.json")
