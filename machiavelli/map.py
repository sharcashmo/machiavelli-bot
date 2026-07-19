# machiavelli/map.py
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

@dataclass(frozen=True)
class Route:
    """Representa una ruta o adyacencia de movimiento hacia otra localización.

    Attributes:
        destination (str): El código del mar o de la provincia de destino (id).
        strait (str | None): El código de la provincia que controla el paso si es un estrecho.
    """

    destination: str
    strait: str | None = None

@dataclass
class Location:
    """Clase base para cualquier localización en el mapa de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo del lugar.
        id (str): ID único generado automáticamente por las clases hijas.
        land_routes (list[Route]): Conexiones válidas para movimiento terrestre (ejércitos).
        sea_routes (list[Route]): Conexiones válidas para movimiento marítimo (flotas).
    """

    name: str
    id: str = field(init=False)
    land_routes: list[Route] = field(default_factory=list)
    sea_routes: list[Route] = field(default_factory=list)

    def exclude_routes(self, exclude_ids: list) -> Self:
        """Elimina de las rutas aquellas que tienen como destino alguna de las Location incluidas en exclude_ids.

        Elimina rutas que llevan a las provincias dadas. Para tratar las localizaciones que tienen varias posiciones
        (por ejemplo, Provence, que tiene costa sur y norte) identifica la provincia o mar eliminando el calificador
        de costa. Por ejemplo, eliminar 'prove' elimina tanto las rutas que llevan a 'prove' como las que llevan a
        'prove S' y 'prove N'.
        
        Args:
            exclude_ids (List) Lista de IDs de destinos para los que deben purgarse las rutas.
        
        Returns:
            Self: la propia instancia para permitir encadenamiento de métodos.
        """
        self.land_routes = [r for r in self.land_routes if r.destination.split()[0] not in exclude_ids]
        self.sea_routes = [r for r in self.sea_routes if r.destination.split()[0] not in exclude_ids]

        return self

@dataclass
class Province(Location):
    """Representa una provincia en el mapa de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo de la provincia.
        id (str): ID generado automáticamente usando con las cinco primeras letras en minúscula (ej "flore").
    """

    city: str | None = None
    has_port: bool = False
    major_city: int | None = None
    is_venice: bool = False
    custom_id: str | None = None

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
        id (str): ID generado automáticamente usando las iniciales en mayúsculas (ej. "ETS").
    """

    def __post_init__(self):
        """Genera el ID automático a partir del nombre tras la inicialización."""
        self.id = "".join([word[0] for word in self.name.split()]).upper()


@dataclass
class Map:
    """Contiene las provincias y mares del mapa.

    Attributes:
        provinces (dict[str, Province]): Provincias indexadas por su ID.
        seas (dict[str, Sea]): Zonas de mar indexadas por su ID.
    """

    provinces: dict[str, Province] = field(default_factory=dict)
    seas: dict[str, Sea] = field(default_factory=dict)

    @classmethod
    def load_map(cls, exclude_ids: list[str] = None) -> "Map":
        """Carga el JSON maestro, purga las exclusiones y clasifica tierra y mar."""
        if exclude_ids is None:
            exclude_ids = []

        json_path = Path(__file__).parent / "map_data.json"

        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        processed_provinces = {}
        processed_seas = {}

        # Procesamos las provincias
        for item in raw_data.get("provinces", []):
            province = Province(name=item["name"],
                city=item.get("city"),
                has_port=item.get("has_port", False),
                major_city=item.get("major_city"),
                is_venice=item.get("is_venice", False),
                custom_id=item.get("custom_id"))
            if province.id.split()[0] in exclude_ids:
                continue
            for r_data in item.get("land_routes", []):
                if r_data['destination'].split()[0] in exclude_ids:
                    continue
                route = Route(destination=r_data["destination"], strait=r_data.get("strait"))
                province.land_routes.append(route)
            for r_data in item.get("sea_routes", []):
                if r_data['destination'].split()[0] in exclude_ids:
                    continue
                route = Route(destination=r_data["destination"], strait=r_data.get("strait"))
                province.sea_routes.append(route)
            processed_provinces[province.id] = province

        # Procesamos los mares
        for item in raw_data.get("seas", []):
            sea = Sea(name=item["name"])
            if sea.id in exclude_ids:
                continue
            for r_data in item.get("land_routes", []):
                if r_data['destination'].split()[0] in exclude_ids:
                    continue
                route = Route(destination=r_data["destination"], strait=r_data.get("strait"))
                sea.land_routes.append(route)
            for r_data in item.get("sea_routes", []):
                if r_data['destination'].split()[0] in exclude_ids:
                    continue
                route = Route(destination=r_data["destination"], strait=r_data.get("strait"))
                sea.sea_routes.append(route)
            processed_seas[sea.id] = sea

        return cls(provinces=processed_provinces, seas=processed_seas)

    def exclude_locations(self, exclude_ids: list) -> Self:
        """Elimina provincias o mares del mapa.

        Elimina provincias o mares del mapa, así como las rutas que llevan a ellos, eliminándolos efectivamente del
        mapa. Para tratar las localizaciones que tienen varias posiciones (por ejemplo, Provence, que tiene costa sur y
        norte) identifica la provincia o mar eliminando el calificador de costa. Por ejemplo, eliminar 'prove' elimina
        tanto 'prove' como 'prove S' y 'prove N', así como las rutas que llevan a ellas.
        
        Args:
            exclude_ids (list): Lista de IDs de las provincias y mares a eliminar.

        Returns:
            Self: la propia instancia para permitir encadenamiento de métodos.
        """
        self.provinces = { k: v.exclude_routes(exclude_ids)
            for k, v in self.provinces.items()
            if k.split()[0] not in exclude_ids }
        self.seas = { k: v.exclude_routes(exclude_ids)
            for k, v in self.seas.items()
            if k.split()[0] not in exclude_ids }

        return self