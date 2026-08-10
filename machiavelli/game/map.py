# machiavelli/game/map.py
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .resources import read_package_json


class MovementMode(StrEnum):
    LAND = "land"
    SEA = "sea"
    BOTH = "both"


@dataclass(frozen=True)
class Route:
    """Representa una ruta o adyacencia de movimiento hacia otra localización.

    Attributes:
        destination (str): El código del mar o de la provincia de destino (id).
        strait (str | None): Código de la provincia que controla el paso si es estrecho.
    """

    destination: str
    strait: str | None = None


@dataclass
class Location:
    """Clase base para cualquier localización en el mapa de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo del lugar.
        id (str): ID único generado automáticamente por las clases hijas.
        land_routes (list[Route]): Conexiones válidas para movimiento terrestre.
        sea_routes (list[Route]): Conexiones válidas para movimiento marítimo.
    """

    name: str
    custom_id: str | None = None
    id: str = field(init=False)
    land_routes: list[Route] = field(default_factory=list)
    sea_routes: list[Route] = field(default_factory=list)


@dataclass
class Province(Location):
    """Representa una provincia en el mapa de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo de la provincia.
        id (str): ID generado con las cinco primeras letras en minúscula.
    """

    city: str | None = None
    has_port: bool = False
    major_city: int | None = None
    is_venice: bool = False

    def __post_init__(self):
        """Genera el ID automático a partir del nombre tras la inicialización."""
        self.id = self.custom_id if self.custom_id else self.name.lower()[:5]

        if self.major_city is None:
            self.major_city = 1 if self.city is not None else 0


@dataclass
class Sea(Location):
    """Representa un mar en el mapa de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo del mar (ej. "Eastern Tyrrhenian Sea").
        id (str): ID generado automáticamente usando las iniciales en mayúsculas.
    """

    def __post_init__(self):
        """Genera el ID automático a partir del nombre tras la inicialización."""
        self.id = (
            self.custom_id
            if self.custom_id
            else "".join([word[0] for word in self.name.split()]).upper()
        )


def _parse_routes(
    routes_raw: list[dict[str, str]],
    exclude_set: set[str],
) -> list[Route]:
    """Helper interno para instanciar solo rutas hacia destinos no excluidos."""
    return [
        Route(destination=r["destination"], strait=r.get("strait"))
        for r in routes_raw
        if r["destination"].split()[0] not in exclude_set
    ]


@dataclass
class Map:
    """Contiene las provincias y mares del mapa.

    Attributes:
        provinces (dict[str, Province]): Provincias indexadas por su ID.
        seas (dict[str, Sea]): Zonas de mar indexadas por su ID.
    """

    provinces: dict[str, Province] = field(default_factory=dict)
    seas: dict[str, Sea] = field(default_factory=dict)

    def __post_init__(self):
        """Realiza algunas operaciones para completar la inicialización"""
        self.locations = self.provinces | self.seas

    @classmethod
    def load_map(
        cls, exclude_ids: list[str] | None = None, json_path: Path | str | None = None
    ) -> "Map":
        """Carga el JSON maestro, purga las exclusiones y clasifica tierra y mar."""
        exclude_set = set(exclude_ids) if exclude_ids else set()

        if json_path is None:
            raw_data = read_package_json("map_data.json")
        else:
            with Path(json_path).open(encoding="utf-8") as stream:
                raw_data = json.load(stream)

        if not isinstance(raw_data, dict):
            raise TypeError("El recurso del mapa debe contener un objeto JSON")

        processed_provinces: dict[str, Province] = {}
        processed_seas: dict[str, Sea] = {}

        # Procesamos las provincias
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
            processed_provinces[province.id] = province

        for item in raw_data.get("seas", []):
            sea = Sea(name=item["name"], custom_id=item.get("custom_id"))
            if sea.id in exclude_set:
                continue

            sea.land_routes = _parse_routes(item.get("land_routes", []), exclude_set)
            sea.sea_routes = _parse_routes(item.get("sea_routes", []), exclude_set)
            processed_seas[sea.id] = sea

        return cls(provinces=processed_provinces, seas=processed_seas)

    def adjacent_locations(
        self, origin: str, mode: MovementMode = MovementMode.BOTH
    ) -> set[str]:
        """Devuelve una lista de localizaciones adyacentes a una de origen.

        Las localizaciones adyacentes se devuelven como una lista de sus IDs. Se puede
        pasar un modo de movimiento, de forma que nos devuelve las localizaciones a las
        que se puede llegar por tierra, por mar o por ambos.

        En el caso de utilizarse MovementMode.BOTH, este modo va a utilizarse únicamente
        para sobornos y para transporte de tropas. En estos dos casos el tratamiento de
        las provincias con dos costas va a ser el mismo. La provincia terrestre se va a
        considerar equivalente a cualquiera de las dos costas. Eso implica:

        - las rutas que llevan a cualquiera de las costas llevan también a la provincia.
        - las rutas desde la provincia llevan a las adyacentes a ambas costas.

        En el caso de MovementMode.LAND y MovementMode.SEA eso no se aplica, y en las
        rutas por mar se distingue entre la situación en cada costa.

        Args:
            origin (str): ID o código de la localización de origen a consultar.
            mode (MovementMode, optional): Tipo de rutas a evaluar (MovementMode.LAND,
                MovementMode.SEA o MovementMode.BOTH). Por defecto es MovementMode.BOTH.

        Returns:
            set[str]: Lista con los IDs de las localizaciones adyacentes alcanzables.

        Raises:
            KeyError: Si el ID `origin` no existe entre las localizaciones del mapa.
        """
        locations = self.locations if self.locations else self.provinces | self.seas
        adjacent = set()
        origin_base = origin.split()[0]

        if mode in (MovementMode.LAND, MovementMode.BOTH):
            adjacent |= {r.destination for r in locations[origin].land_routes}
        if mode in (MovementMode.SEA, MovementMode.BOTH):
            adjacent |= {r.destination for r in locations[origin].sea_routes}
        # Provincias con dos costas
        if mode == MovementMode.BOTH:
            # La provincia de origen muestra los destinos de las dos costas
            adjacent |= {
                r.destination
                for lo in locations.keys()
                for r in locations[lo].sea_routes
                if lo.split()[0] == origin_base
            }
            # Los destinos a cualquiera de las dos costas llevan también a la provincia
            adjacent |= {dest.split()[0] for dest in adjacent}

        return adjacent
