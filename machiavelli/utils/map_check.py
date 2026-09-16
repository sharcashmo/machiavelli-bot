# machiavelli/utils/map_check.py
"""Comprobaciones de consistencia y estadísticas del mapa.

Proporciona funciones para validar un objeto `Map` (construido directamente
o cargado vía `MapRepository`) y para extraer estadísticas del mismo.
Pensado para usarse en desarrollo, tests o scripts de verificación.

Uso típico:

    from machiavelli.utils.map_check import check_map, check_map_by_id

    # A partir de un mapa ya cargado:
    result = check_map(game_map)

    # A partir del id del mapa:
    result = check_map_by_id("italy")
    if not result.is_valid:
        print(result.summary())
"""

from dataclasses import dataclass, field
from pathlib import Path

from machiavelli.game.map import Location, Map
from machiavelli.game.resources import read_package_json
from machiavelli.repositories.map_repository import MapRepository


@dataclass
class MapCheckResult:
    """Resultado de la validación de un mapa.

    Attributes:
        map_id: Identificador del mapa validado, si se conoce.
        errors: Problemas graves que rompen la consistencia del mapa
            (duplicados, rutas asimétricas, destinos inexistentes...).
        warnings: Posibles problemas que no invalidan el mapa pero conviene
            revisar (p. ej. estrechos inconsistentes).
        statistics: Conteo de elementos del mapa (provincias, ciudades, etc.).
    """

    map_id: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    statistics: dict[str, int] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        """True si no se ha encontrado ningún error grave."""
        return not self.errors

    def summary(self) -> str:
        """Devuelve un resumen legible del resultado."""
        title = f"Mapa: {self.map_id}" if self.map_id else "Mapa"
        lines = [title]
        lines.append(f"Estadísticas: {self.statistics}")
        lines.append(f"Errores: {len(self.errors)}")
        lines.extend(f"  [ERROR] {e}" for e in self.errors)
        lines.append(f"Avisos: {len(self.warnings)}")
        lines.extend(f"  [AVISO] {w}" for w in self.warnings)
        return "\n".join(lines)


def _base_id(location_id: str) -> str:
    """Devuelve el ID base de una localización.

    Las provincias con dos costas tienen IDs del tipo "Roma 1" y "Roma 2";
    para la simetría de rutas se consideran la misma provincia.
    """
    return location_id.split()[0]


def check_duplicate_province_ids(game_map: Map) -> list[str]:
    """Comprueba que no haya identificadores de provincia duplicados.

    Como `Map.provinces` es un diccionario indexado por ID, un duplicado se
    pierde silenciosamente en la construcción. Aquí se detectan colisiones
    recomputando el ID a partir del nombre (o del custom_id) de cada provincia.

    Returns:
        Lista de mensajes de error (vacía si todo está bien).
    """
    errors = []
    seen: dict[str, str] = {}

    for province in game_map.provinces.values():
        computed_id = province.custom_id or province.name.lower()[:5]
        if computed_id in seen:
            errors.append(
                f"ID de provincia duplicado '{computed_id}': "
                f"'{seen[computed_id]}' y '{province.name}'"
            )
        else:
            seen[computed_id] = province.name

    return errors


def check_duplicate_sea_ids(game_map: Map) -> list[str]:
    """Comprueba que no haya identificadores de mar duplicados.

    El ID de un mar son las iniciales de su nombre (p. ej. "Eastern Tyrrhenian
    Sea" -> "ETS"), por lo que es relativamente fácil que dos mares colisionen.

    Returns:
        Lista de mensajes de error (vacía si todo está bien).
    """
    errors = []
    seen: dict[str, str] = {}

    for sea in game_map.seas.values():
        computed_id = (
            sea.custom_id or "".join(word[0] for word in sea.name.split()).upper()
        )
        if computed_id in seen:
            errors.append(
                f"ID de mar duplicado '{computed_id}': "
                f"'{seen[computed_id]}' y '{sea.name}'"
            )
        else:
            seen[computed_id] = sea.name

    return errors


def check_route_destinations_exist(game_map: Map) -> list[str]:
    """Comprueba que todas las rutas apunten a localizaciones existentes.

    También valida que el estrecho de cada ruta, si lo hay, referencie una
    provincia existente.

    Returns:
        Lista de mensajes de error (vacía si todo está bien).
    """
    errors = []

    for location_id, location in game_map.locations.items():
        for route in location.land_routes + location.sea_routes:
            if route.destination not in game_map.locations:
                errors.append(
                    f"Ruta con destino inexistente: {location_id} -> "
                    f"'{route.destination}'"
                )
            if route.strait is not None and route.strait not in game_map.provinces:
                errors.append(
                    f"Ruta {location_id} -> '{route.destination}' hace referencia "
                    f"a un estrecho inexistente: '{route.strait}'"
                )

    return errors


def check_route_symmetry(game_map: Map) -> list[str]:
    """Comprueba que las rutas sean simétricas y del mismo tipo.

    Si existe una ruta de A a B, debe existir la ruta de B a A por el mismo
    tipo de ruta (terrestre o marítima). Para las rutas marítimas se comprueba
    además la coherencia del estrecho: si la ruta de ida controla el paso
    mediante una provincia, la de vuelta debería hacerlo mediante la misma.

    Returns:
        Lista de mensajes de error para rutas asimétricas y de aviso para
        estrechos inconsistentes.
    """
    errors = []
    warnings = []

    for location_id, location in game_map.locations.items():
        for route in location.land_routes:
            destination = game_map.locations.get(route.destination)
            if destination is None:
                continue  # Ya se reporta en check_route_destinations_exist
            if not any(r.destination == location_id for r in destination.land_routes):
                errors.append(
                    f"Ruta terrestre asimétrica: {location_id} -> "
                    f"{route.destination} no tiene ruta de vuelta"
                )

        for route in location.sea_routes:
            destination = game_map.locations.get(route.destination)
            if destination is None:
                continue
            reverse_routes = [
                r for r in destination.sea_routes if r.destination == location_id
            ]
            if not reverse_routes:
                errors.append(
                    f"Ruta marítima asimétrica: {location_id} -> "
                    f"{route.destination} no tiene ruta de vuelta"
                )
            elif route.strait is not None:
                # La ruta de ida es un estrecho: la vuelta debería controlar
                # el paso con la misma provincia.
                reverse_straits = {r.strait for r in reverse_routes}
                if route.strait not in reverse_straits:
                    warnings.append(
                        f"Estrecho inconsistente: {location_id} -> "
                        f"{route.destination} lo controla '{route.strait}', "
                        f"pero la vuelta controla {reverse_straits or 'nada'}"
                    )

    return errors + warnings


def _has_any_route(location: Location) -> bool:
    return bool(location.land_routes or location.sea_routes)


def compute_statistics(game_map: Map) -> dict[str, int]:
    """Calcula estadísticas del mapa.

    Returns:
        Diccionario con los conteos.
    """
    provinces = game_map.provinces.values()
    unique_provinces = set(_base_id(province) for province in game_map.provinces.keys())

    return {
        "provinces": len(unique_provinces),
        "seas": len(game_map.seas),
        "cities": sum(1 for p in provinces if p.city == "city"),
        "fortified_cities": sum(1 for p in provinces if p.city == "fortified"),
        "fortresses": sum(1 for p in provinces if p.city == "fortress"),
        "ports": sum(1 for p in provinces if p.has_port),
        "major_cities": sum(1 for p in provinces if p.major_city > 1),
        "isolated_locations": sum(
            1 for loc in game_map.locations.values() if not _has_any_route(loc)
        ),
    }


def check_map(game_map: Map, map_id: str | None = None) -> MapCheckResult:
    """Ejecuta todas las comprobaciones sobre un mapa ya construido.

    Args:
        game_map: El mapa a validar.
        map_id: Identificador del mapa, opcional, para incluirlo en el
            resultado y en los mensajes.

    Returns:
        Un `MapCheckResult` con los errores, avisos y estadísticas.
    """
    result = MapCheckResult(map_id=map_id)

    result.errors.extend(check_duplicate_province_ids(game_map))
    result.errors.extend(check_duplicate_sea_ids(game_map))
    result.errors.extend(check_route_destinations_exist(game_map))
    result.errors.extend(check_route_symmetry(game_map))
    result.statistics = compute_statistics(game_map)

    return result


def check_map_by_id(
    map_id: str,
    exclude_ids: list[str] | None = None,
    fortress_active: bool = True,
    json_path: Path | str | None = None,
) -> MapCheckResult:
    """Carga el mapa con `MapRepository` y ejecuta todas las comprobaciones.

    Args:
        map_id: Identificador del mapa a cargar (p. ej. "italy").
        exclude_ids: IDs de localizaciones a excluir, igual que en
            `MapRepository.load_map`. Nota: excluir localizaciones puede
            provocar asimetrías espurias en las rutas (una ruta hacia un
            destino excluido se elimina, pero la inversa no). Tenlo en cuenta
            si el mapa reporta errores que no esperabas.
        fortress_active: Si las fortalezas están activas, igual que en
            `MapRepository.load_map`.
        json_path: Ruta a un JSON alternativo. Si es None se usa el recurso
            del paquete `assets/maps/{map_id}.json`.

    Returns:
        Un `MapCheckResult` con el `map_id` informado.

    Raises:
        FileNotFoundError: Si el recurso del mapa no existe (se comprueba
            antes de delegar en el repositorio, para dar un mensaje claro).
    """
    if json_path is None:
        # Comprobación temprana: MapRepository fallaría con un error más críptico.
        read_package_json(f"assets/maps/{map_id}.json")

    repository = MapRepository(json_path=json_path)
    game_map = repository.load_map(
        map_id, exclude_ids=exclude_ids, fortress_active=fortress_active
    )
    return check_map(game_map, map_id=map_id)
