"""Carga del mapa de juego desde recursos JSON."""

from __future__ import annotations

import json
from pathlib import Path

from machiavelli.game.map import Map, Province, Route, Sea
from machiavelli.game.resources import read_package_json


class MapRepository:
    """Construye el mapa de dominio a partir de su recurso JSON."""

    def __init__(self, json_path: Path | str | None = None) -> None:
        self.json_path = Path(json_path) if json_path is not None else None

    def load_map(
        self,
        map_id: str,
        exclude_ids: list[str] | None = None,
        fortress_active: bool = True,
    ) -> Map:
        """Carga el mapa, aplica exclusiones y configura sus fortalezas."""
        exclude_set = set(exclude_ids) if exclude_ids else set()
        raw_data = self._read_data(map_id)

        if not isinstance(raw_data, dict):
            raise TypeError("El recurso del mapa debe contener un objeto JSON")

        provinces = self._load_provinces(raw_data, exclude_set, fortress_active)
        seas = self._load_seas(raw_data, exclude_set)
        return Map(provinces=provinces, seas=seas)

    def _read_data(self, map_id: str) -> object:
        if self.json_path is None:
            return read_package_json(f"assets/maps/{map_id}.json")
        with self.json_path.open(encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _load_provinces(
        raw_data: dict, exclude_set: set[str], fortress_active: bool
    ) -> dict[str, Province]:
        provinces = {}
        for item in raw_data.get("provinces", []):
            province = Province(
                name=item["name"],
                city=item.get("city"),
                has_port=item.get("has_port", False),
                major_city=item.get("major_city"),
                is_venice=item.get("is_venice", False),
                custom_id=item.get("custom_id"),
            )
            if province.id.split()[0] in exclude_set:
                continue

            province.land_routes = _parse_routes(
                item.get("land_routes", []), exclude_set
            )
            province.sea_routes = _parse_routes(item.get("sea_routes", []), exclude_set)
            if not fortress_active and province.city == "fortress":
                province.city = None
            provinces[province.id] = province
        return provinces

    @staticmethod
    def _load_seas(raw_data: dict, exclude_set: set[str]) -> dict[str, Sea]:
        seas = {}
        for item in raw_data.get("seas", []):
            sea = Sea(name=item["name"], custom_id=item.get("custom_id"))
            if sea.id in exclude_set:
                continue

            sea.land_routes = _parse_routes(item.get("land_routes", []), exclude_set)
            sea.sea_routes = _parse_routes(item.get("sea_routes", []), exclude_set)
            seas[sea.id] = sea
        return seas


def _parse_routes(
    routes_raw: list[dict[str, str]], exclude_set: set[str]
) -> list[Route]:
    """Instancia únicamente rutas hacia destinos no excluidos."""
    return [
        Route(destination=route["destination"], strait=route.get("strait"))
        for route in routes_raw
        if route["destination"].split()[0] not in exclude_set
    ]
