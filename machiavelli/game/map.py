# machiavelli/game/map.py
from dataclasses import dataclass, field
from enum import StrEnum


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
