"""Resolución militar inmutable y aplicada en una única frontera."""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from ..game.command import Command
from ..game.events import TurnEvent
from ..game.game import Game
from ..game.map import Map, MovementMode
from ..game.player import Player

type ResolutionValue = str | int | bool | None | tuple[ResolutionValue, ...]
type ResolutionSignature = tuple[ResolutionValue, ...]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UnitKey:
    """Identifica de forma estable una unidad del snapshot militar."""

    player_id: str | None
    unit_type: str
    origin: str


@dataclass(frozen=True, slots=True)
class MilitaryUnit:
    """Relaciona la identidad militar con su propietario, si lo tiene."""

    key: UnitKey
    player: Player | None


@dataclass(frozen=True, slots=True)
class MilitaryOrder:
    """Representa la intención lógica ya compilada para una unidad."""

    unit: UnitKey
    order_type: str
    target_location: str | None = None
    path: tuple[str, ...] = ()
    transporters: tuple[UnitKey, ...] = ()
    transported_army: UnitKey | None = None
    supported_faction: str | None = None
    is_convoy: bool = False
    straits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DislodgementRecord:
    """Relaciona un desalojo con la procedencia inmediata del ataque."""

    unit: UnitKey
    attack_origin: str | None


@dataclass(frozen=True, slots=True)
class ResolutionState:
    """Agrupa un paso inmutable de la adjudicación militar."""

    active_supports: frozenset[UnitKey]
    available_convoys: frozenset[UnitKey]
    successful_moves: frozenset[UnitKey]
    successful_conversions: frozenset[UnitKey]
    dislodged_units: frozenset[UnitKey]
    cancelled_orders: frozenset[UnitKey]
    cancelled_by_self_conflict: frozenset[UnitKey]
    effective_positions: tuple[tuple[UnitKey, str | None], ...]
    resolved_conflicts: frozenset[str]
    dislodgements: tuple[DislodgementRecord, ...]


@dataclass(frozen=True, slots=True)
class CycleDiagnostic:
    """Describe con valores primitivos el ciclo que impidió resolver el tablero."""

    stage: str
    first_seen_iteration: int
    repeated_iteration: int
    pending_conflicts: tuple[str, ...]
    state_signature: ResolutionSignature


@dataclass(frozen=True, slots=True)
class UnitOutcome:
    """Resultado final de una unidad respecto al snapshot inicial."""

    unit: UnitKey
    final_unit_type: str
    final_location: str | None
    dislodged: bool
    attack_origin: str | None = None


@dataclass(frozen=True, slots=True)
class MilitaryResolution:
    """Resultado completo entregado al gestor de retiradas y al commit."""

    outcomes: tuple[UnitOutcome, ...]
    contested_locations: frozenset[str]


class MilitaryResolutionError(Exception):
    """No se pudo obtener un resultado militar completo."""

    def __init__(self, message):
        """Constructor  para dejar una traza de la causa del error."""
        super().__init__(message)  # Extrae la pila de llamadas en tiempo de ejecución

        stack = traceback.extract_stack()

        # stack[-1] es este __init__. stack[-2] es donde se instanció la excepción.
        origin = stack[-2]
        self.filename = origin.filename
        self.lineno = origin.lineno

        # Registrar el error explícitamente con logger.error
        logger.error(
            f"Excepción levantada: {message} | Archivo: {self.filename} | Línea:"
            f" {self.lineno}"
        )


class InvalidMilitaryState(MilitaryResolutionError):
    """El snapshot militar contiene ocupaciones incompatibles."""


class UnresolvedMilitaryConflict(MilitaryResolutionError):
    """No queda una regla determinista para resolver el conflicto."""

    def __init__(self, diagnostic: CycleDiagnostic):
        """Conserva el diagnóstico estructurado sin exponerlo en el mensaje."""
        super().__init__("Conflicto militar sin resolver")
        self.diagnostic = diagnostic


class DislodgementResolverRequired(MilitaryResolutionError):
    """Hay desalojos y falta el gestor externo de retiradas."""


class DecisionType(StrEnum):
    DISBAND = "disband"
    RETREAT = "retreat"
    GARRISON = "garrison"


@dataclass(frozen=True, slots=True)
class DislodgementDecision:
    """Define el resultado operativo y el destino opcional de una unidad desalojada."""

    decision_type: DecisionType
    destination: str | None


type DislodgementResolver = Callable[
    [MilitaryResolution], Mapping[UnitKey, DislodgementDecision]
]


def _key_sort(key: UnitKey) -> tuple[str, str, str]:
    """Proporciona un orden total estable para identidades militares."""
    return (key.player_id or "", key.unit_type, key.origin)


def conflict_location(location: str, unit_type: str) -> str:
    """Devuelve la plaza de conflicto sin perder la costa de la identidad."""
    base_location = location.split()[0]
    return f"G {base_location}" if unit_type == "G" else base_location


def _cancel_moves_towards(
    units: Iterable[UnitKey],
    effective_positions: dict[UnitKey, str],
    successful_moves: set[UnitKey],
    successful_conversions: set[UnitKey],
) -> None:
    """Elimina de successful_moves y successful_conversions las unidades que se dirigen
    al origen de alguna de las unidades pasadas."""
    blocked_origins = {u.origin for u in units}

    if not blocked_origins:
        return

    successful_moves -= {
        m for m in successful_moves if effective_positions.get(m) in blocked_origins
    }
    successful_conversions -= {
        c
        for c in successful_conversions
        if effective_positions.get(c) in blocked_origins
    }


class MilitaryResolver:
    """Compila, resuelve y aplica una campaña sin mutar el snapshot intermedio."""

    def __init__(self, game: Game):
        """Inicializa los índices efímeros usados durante una campaña."""
        self.game = game
        self.units_by_key: dict[UnitKey, MilitaryUnit] = {}
        self.actor_to_unit: dict[tuple[str, str], UnitKey] = {}
        self.army_by_origin: dict[str, UnitKey] = {}
        self.fleet_by_conflict_location: dict[str, UnitKey] = {}
        self.campaign_unit_by_conflict_location: dict[str, UnitKey] = {}
        self.rebellions_by_location: dict[str, tuple[str, str]] = {}
        self.orders_by_unit: dict[UnitKey, MilitaryOrder] = {}
        self.invalid_orders: dict[UnitKey, str] = {}
        self._broken_convoys: set[UnitKey] = set()
        self.disbanded_units: frozenset[UnitKey] = frozenset()

    @property
    def map(self) -> Map:
        """Devuelve el mapa necesario para cada resolución militar."""
        return self.game.require_map()

    def _player_power(self, key: UnitKey) -> str | None:
        """Devuelve la potencia que controla una unidad, si tiene propietario."""
        player = self.units_by_key[key].player
        return player.power if player is not None else None

    def _build_unit_index(self) -> None:
        """Captura y valida el snapshot antes de leer cualquier orden."""
        self.units_by_key.clear()
        self.actor_to_unit.clear()
        self.army_by_origin.clear()
        self.fleet_by_conflict_location.clear()
        self.campaign_unit_by_conflict_location.clear()
        self.rebellions_by_location.clear()
        self._broken_convoys.clear()
        self.disbanded_units = frozenset()
        campaign_locations: set[str] = set()
        garrison_locations: set[str] = set()

        for player in sorted(self.game.players, key=lambda item: item.player_id):
            for unit_type, locations in (
                ("A", player.armies),
                ("F", player.fleets),
                ("G", player.garrisons),
            ):
                for origin in sorted(locations):
                    self._add_unit(
                        UnitKey(player.player_id, unit_type, origin),
                        player,
                        campaign_locations,
                        garrison_locations,
                    )
        for origin in sorted(self.game.independent_garrisons):
            self._add_unit(
                UnitKey(None, "G", origin),
                None,
                campaign_locations,
                garrison_locations,
            )
        self._build_rebellion_index()

    def _add_unit(
        self,
        key: UnitKey,
        player: Player | None,
        campaign_locations: set[str],
        garrison_locations: set[str],
    ) -> None:
        """Añade una unidad al snapshot y rechaza ocupaciones incompatibles."""
        if not self._valid_unit_origin(key):
            raise InvalidMilitaryState(f"Localización de unidad inválida: {key.origin}")
        if key in self.units_by_key:
            raise InvalidMilitaryState(f"Unidad duplicada: {key.origin}")
        location = conflict_location(key.origin, key.unit_type)
        occupied = garrison_locations if key.unit_type == "G" else campaign_locations
        if location in occupied:
            raise InvalidMilitaryState(f"Ocupación incompatible: {location}")
        occupied.add(location)
        self.units_by_key[key] = MilitaryUnit(key, player)
        if player is not None:
            self.actor_to_unit[(player.player_id, f"{key.unit_type} {key.origin}")] = (
                key
            )
        if key.unit_type in {"A", "F"}:
            self.campaign_unit_by_conflict_location[location] = key
        if key.unit_type == "A":
            self.army_by_origin[key.origin] = key
        elif key.unit_type == "F":
            self.fleet_by_conflict_location[location] = key

    def _is_defensible_city(self, city: str | None) -> bool:
        """Comprueba si una ciudad admite defensa con el escenario activo."""
        scenario = self.game.scenario
        if scenario is None:
            return city in {"fortified", "fortress"}
        return scenario.is_defensible_city(city)

    def _build_rebellion_index(self) -> None:
        """Indexa cada rebelión una sola vez sin convertirla en participante."""
        for player in sorted(self.game.players, key=lambda item: item.player_id):
            for kind, locations in (
                ("province", player.rebelled_provinces),
                ("city", player.rebelled_cities),
            ):
                for location in sorted(locations):
                    province = self.map.provinces.get(location)
                    if province is None:
                        raise InvalidMilitaryState(
                            f"Localización de rebelión inválida: {location}"
                        )
                    if kind == "city" and not self._is_defensible_city(province.city):
                        raise InvalidMilitaryState(
                            f"Rebelión urbana fuera de ciudad defendible: {location}"
                        )
                    if kind == "city" and any(
                        key.unit_type == "G" and key.origin == location
                        for key in self.units_by_key
                    ):
                        raise InvalidMilitaryState(
                            f"Rebelión urbana incompatible con guarnición: {location}"
                        )
                    if location in self.rebellions_by_location:
                        raise InvalidMilitaryState(f"Rebelión duplicada: {location}")
                    self.rebellions_by_location[location] = (player.player_id, kind)

    def _remove_disbanded_units(self) -> tuple[UnitKey, ...]:
        """Retira las unidades con orden de combate de desbandarse."""
        disbanded = tuple(
            key
            for key in sorted(self.orders_by_unit, key=_key_sort)
            if self.orders_by_unit[key].order_type == "C"
            and self.orders_by_unit[key].target_location == "0"
        )
        for key in disbanded:
            del self.units_by_key[key]
            del self.orders_by_unit[key]
            self.invalid_orders.pop(key, None)
            if key.player_id is not None:
                self.actor_to_unit.pop(
                    (key.player_id, f"{key.unit_type} {key.origin}"), None
                )
            location = conflict_location(key.origin, key.unit_type)
            if self.campaign_unit_by_conflict_location.get(location) == key:
                del self.campaign_unit_by_conflict_location[location]
            if key.unit_type == "A":
                self.army_by_origin.pop(key.origin, None)
            elif key.unit_type == "F":
                self.fleet_by_conflict_location.pop(location, None)
        self.disbanded_units = frozenset(disbanded)
        return disbanded

    def _compile_orders(self) -> None:
        """Convierte filas existentes en una intención por unidad, sin ejecutarlas."""
        self.orders_by_unit.clear()
        self.invalid_orders.clear()
        rows: dict[UnitKey, list[Command]] = {key: [] for key in self.units_by_key}
        for player in self.game.players:
            for command in player.commands:
                key = self.actor_to_unit.get((player.player_id, command.actor))
                if key is not None:
                    rows[key].append(command)

        for key in sorted(self.units_by_key, key=_key_sort):
            commands = rows[key]
            if not commands:
                self.orders_by_unit[key] = MilitaryOrder(key, "H")
                continue
            if len(commands) > 1 and (
                key.unit_type != "A"
                or any(command.command != "A" for command in commands)
            ):
                self._invalid_order(key, "combinación de órdenes inválida")
                continue
            if len(commands) > 1:
                targets = [
                    command.target for command in commands if command.target is not None
                ]
                if len(targets) != len(commands):
                    self._invalid_order(key, "ruta de convoy incompleta")
                    continue
                self.orders_by_unit[key] = MilitaryOrder(
                    key,
                    "A",
                    target_location=targets[-1],
                    path=(key.origin, *targets),
                    is_convoy=True,
                )
                continue
            command = commands[0]
            order_type = command.command
            if order_type not in {"A", "B", "H", "L", "S", "T", "C"}:
                self._invalid_order(key, "código de orden inválido")
                continue
            self.orders_by_unit[key] = MilitaryOrder(
                key,
                order_type,
                target_location=command.target,
                path=(key.origin, command.target)
                if order_type == "A" and command.target is not None
                else (),
            )

    def _link_and_validate_orders(self) -> None:
        """Valida gramática y geometría que no depende de convoyes ni conflictos."""
        for key in sorted(self.orders_by_unit, key=_key_sort):
            order = self.orders_by_unit[key]
            if order.order_type == "H":
                continue
            reason = self._order_invalid_reason(order)
            if reason:
                self._invalid_order(key, reason)
        self._link_convoys()
        for key, order in self.orders_by_unit.items():
            if order.order_type in {"A", "S"}:
                path = (
                    order.path
                    if order.is_convoy
                    else (key.origin, order.target_location)
                )
                mode = (
                    MovementMode.SEA
                    if order.is_convoy or key.unit_type == "F"
                    else MovementMode.LAND
                )
                self.orders_by_unit[key] = replace(
                    order,
                    straits=self._straits_for_path(path, mode),
                )

    def _straits_for_path(
        self, path: tuple[str | None, ...], mode: MovementMode
    ) -> tuple[str, ...]:
        """Devuelve los estrechos atravesados por una ruta ya validada."""
        straits: list[str] = []
        for origin, target in zip(path[:-1], path[1:], strict=True):
            if origin is None or target is None:
                continue
            route = next(
                (
                    item
                    for item in (
                        self.map.locations[origin].land_routes
                        if mode is MovementMode.LAND
                        else self.map.locations[origin].sea_routes
                    )
                    if item.destination == target
                ),
                None,
            )
            if route is not None and route.strait is not None:
                straits.append(route.strait)
        return tuple(straits)

    def _link_convoys(self) -> None:
        """Enlaza rutas encadenadas con sus flotas Transport iniciales."""
        for key in sorted(self.orders_by_unit, key=_key_sort):
            order = self.orders_by_unit[key]
            if not order.is_convoy:
                continue
            destination = order.path[-1]
            if destination not in self.map.provinces:
                self._invalid_order(key, "destino de convoy inválido")
                continue
            transporters: list[UnitKey] = []
            for origin, target in zip(order.path[:-1], order.path[1:], strict=True):
                if target not in self.map.adjacent_locations(origin):
                    self._invalid_order(key, "tramo de convoy no adyacente")
                    break
            else:
                for location in order.path[1:-1]:
                    fleet = self.fleet_by_conflict_location.get(
                        conflict_location(location, "F")
                    )
                    if fleet is None:
                        self._invalid_order(key, "transportadora de convoy inválida")
                        break
                    transport = self.orders_by_unit.get(fleet)
                    if (
                        transport is None
                        or transport.order_type != "T"
                        or transport.transported_army != key
                    ):
                        self._invalid_order(key, "transportadora de convoy inválida")
                        break
                    if fleet not in transporters:
                        transporters.append(fleet)
                else:
                    self.orders_by_unit[key] = MilitaryOrder(
                        key,
                        "A",
                        destination,
                        path=order.path,
                        transporters=tuple(transporters),
                        is_convoy=True,
                    )

    def _order_invalid_reason(self, order: MilitaryOrder) -> str | None:
        """Devuelve la primera regla estática que invalida una orden compilada."""
        key = order.unit
        target = order.target_location or ""
        province = key.origin.split()[0]
        if self._is_active_besieger(key) and order.order_type not in {"B", "H", "L"}:
            return "orden no permitida durante asedio"
        if (
            key.unit_type == "G"
            and key.origin in self.game.besieges
            and order.order_type not in {"H", "S"}
        ):
            return "orden de guarnición no permitida durante asedio"

        if order.order_type == "A":
            if order.is_convoy:
                return None
            if key.unit_type == "G" or not self._location_exists(target):
                return "avance inválido"
            mode = MovementMode.LAND if key.unit_type == "A" else MovementMode.SEA
            if target not in self.map.adjacent_locations(key.origin, mode):
                return "avance no adyacente"
            if key.unit_type == "A" and target not in self.map.provinces:
                return "ejército no puede avanzar al mar"
        elif order.order_type == "B":
            siege_location = target or province
            reason = self._besiege_invalid_reason(key, siege_location)
            if reason is None:
                self.orders_by_unit[key] = MilitaryOrder(key, "B", siege_location)
            return reason
        elif order.order_type == "L":
            siege_location = target or province
            if not self._is_active_besieger(key) or siege_location != province:
                return "levantamiento de asedio inválido"
            self.orders_by_unit[key] = MilitaryOrder(key, "L", siege_location)
        elif order.order_type == "S":
            match = re.fullmatch(r"(.+?)(?: \(([^()\s]+)\))?", target)
            if match is None or not self._location_exists(match.group(1)):
                return "apoyo inválido"
            support_location, faction = match.groups()
            if (
                key.unit_type == "G"
                and key.origin in self.game.besieges
                and support_location != key.origin
            ):
                return "guarnición asediada solo puede apoyar su provincia"
            player_power = self._player_power(key)
            supported_power = player_power if faction is None else faction
            if supported_power not in {player.power for player in self.game.players}:
                return "facción apoyada inválida"
            mode = (
                MovementMode.LAND if key.unit_type in {"A", "G"} else MovementMode.SEA
            )
            own_garrison_province = (
                key.unit_type == "G" and support_location == key.origin
            )
            # El apoyo a una provincia puede ser a través de una de sus costas
            adjacent_locations = [
                location.split()[0]
                for location in self.map.adjacent_locations(key.origin, mode)
            ]
            if not own_garrison_province and support_location not in (
                adjacent_locations
            ):
                return "apoyo no adyacente"
            self.orders_by_unit[key] = MilitaryOrder(
                key, "S", support_location, supported_faction=supported_power
            )
        elif order.order_type == "T":
            match = re.fullmatch(r"A (\S+)", target)
            if match is None or key.unit_type != "F":
                return "transporte inválido"
            army = self.army_by_origin.get(match.group(1))
            if army is None:
                return "ejército transportado inexistente"
            self.orders_by_unit[key] = MilitaryOrder(
                key, "T", target, transported_army=army
            )
        elif order.order_type == "C":
            if target == "0":
                return None
            location = self.map.provinces.get(province)
            if location is None:
                return "conversión fuera de provincia"
            rebellion = self.rebellions_by_location.get(province)
            if province in self.game.besieges or (
                rebellion is not None and rebellion[1] == "city"
            ):
                return "conversión bloqueada por asedio o rebelión urbana"
            valid = self._is_defensible_city(location.city) and (
                (key.unit_type == "G" and target == "A")
                or (key.unit_type == "G" and target == "F" and location.has_port)
                or (key.unit_type == "A" and target == "G")
                or (key.unit_type == "F" and target == "G" and location.has_port)
            )
            if not valid:
                return "conversión inválida"
        return None

    def _is_active_besieger(self, key: UnitKey) -> bool:
        """Comprueba si la unidad mantiene el asedio registrado en su provincia."""
        province = key.origin.split()[0]
        return (
            key.unit_type in {"A", "F"}
            and province in self.game.besieges
            and self.campaign_unit_by_conflict_location.get(province) == key
        )

    def _besiege_invalid_reason(self, key: UnitKey, target: str) -> str | None:
        """Valida el asediador, la ciudad y el objetivo que se pretende someter."""
        province = key.origin.split()[0]
        location = self.map.provinces.get(province)
        if (
            key.unit_type not in {"A", "F"}
            or target != province
            or location is None
            or not self._is_defensible_city(location.city)
        ):
            return "objetivo de asedio inválido"
        if key.unit_type == "F" and not location.has_port:
            return "flota no puede asediar una ciudad sin puerto"

        garrison_present = any(
            unit.unit_type == "G" and unit.origin == province
            for unit in self.units_by_key
        )
        rebellion = self.rebellions_by_location.get(province)
        city_rebellion = rebellion is not None and rebellion[1] == "city"
        if not garrison_present and not city_rebellion:
            return "asedio sin objetivo"
        if (
            rebellion is not None
            and rebellion[1] == "city"
            and not garrison_present
            and rebellion[0] != key.player_id
        ):
            return "solo el controlador puede someter la rebelión urbana"
        return None

    def _invalid_order(self, key: UnitKey, reason: str) -> None:
        """Registra el rechazo y conserva físicamente la unidad mediante Hold."""
        self.invalid_orders[key] = reason
        self.orders_by_unit[key] = MilitaryOrder(key, "H")

    def _location_exists(self, location: str) -> bool:
        """Acepta únicamente identificadores presentes en el mapa cargado."""
        return location in self.map.provinces or location in self.map.seas

    def _valid_fleet_location(self, location: str) -> bool:
        """Valida mares y costas exactas sin aceptar provincias interiores."""
        if location in self.map.seas:
            return True
        province = self.map.provinces.get(location)
        if province is None or not (province.has_port or province.sea_routes):
            return False
        base_location = location.split()[0]
        if location == base_location:
            coast_variants = {
                candidate
                for candidate, candidate_province in self.map.provinces.items()
                if candidate.startswith(f"{base_location} ")
                and candidate_province is province
            }
            if len(coast_variants) > 1:
                return False
        return True

    def _valid_unit_origin(self, key: UnitKey) -> bool:
        """Valida el origen según las restricciones del tipo de unidad."""
        if key.unit_type == "F":
            return self._location_exists(key.origin)
        province = self.map.provinces.get(key.origin)
        if key.unit_type == "G":
            return province is not None and self._is_defensible_city(province.city)
        return province is not None

    def _conflicts(self) -> dict[str, list[UnitKey]]:
        """Proyecta cada orden sobre la plaza donde puede generar conflicto."""
        conflicts: dict[str, list[UnitKey]] = {}
        for key, order in self.orders_by_unit.items():
            if order.order_type == "A":
                location = conflict_location(
                    order.target_location or key.origin, key.unit_type
                )
            elif order.order_type == "C":
                location = conflict_location(
                    key.origin, order.target_location or key.unit_type
                )
            else:
                location = conflict_location(key.origin, key.unit_type)
            conflicts.setdefault(location, []).append(key)
        return conflicts

    def _effective_conflict_keys(self) -> frozenset[str]:
        """Devuelve las plazas con más de una unidad proyectada."""
        return frozenset(
            location for location, units in self._conflicts().items() if len(units) > 1
        )

    def _resolve_conflicts(self) -> ResolutionState:
        """Resuelve dependencias y cancelaciones por etapas hasta estabilizar."""
        state = self._initial_resolution_state()
        seen: dict[ResolutionSignature, int] = {}
        contested: set[str] = set()
        stage = "targeted-support-cancellation-exhausted"
        iteration = 0
        previous_signature: ResolutionSignature | None = None
        targeted_applied = False
        all_applied = False
        while True:
            # Cada iteración parte de un estado canónico para comparar firmas fiables.
            state = self._normalise_state(state)
            groups, moving = self._conflict_groups(state)
            pending = tuple(
                sorted(
                    location
                    for locations, _ in groups.values()
                    for location in locations
                )
            )
            signature = self._state_signature(state)
            first_seen = seen.get(signature)
            if first_seen is None:
                seen[signature] = iteration
            if not groups:
                if previous_signature == signature:
                    self._contested_locations = frozenset(contested)
                    return state
                previous_signature = signature
                iteration += 1
                continue
            # Solo se adjudican grupos cuya fuerza ya no depende de otro pendiente.
            independent = [
                group
                for group in groups
                if not self._dependencies(group, groups, moving, state)
            ]
            if independent:
                group = min(independent, key=lambda item: groups[item][0])
                state, resolved_contested = self._resolve_group(
                    groups[group], moving, state
                )
                contested.update(resolved_contested)
                previous_signature = signature
                targeted_applied = False
                all_applied = False
                iteration += 1
                continue
            # Primer mecanismo de ruptura: cancelar únicamente apoyos atacados.
            if not targeted_applied:
                targeted = self._targeted_supports(state)
                if targeted:
                    state = self._cancel_supports(state, targeted)
                targeted_applied = True
                stage = "targeted-support-cancellation-exhausted"
                previous_signature = signature
                iteration += 1
                continue

            # Último mecanismo de ruptura: retirar todos los apoyos todavía activos.
            if not all_applied:
                if state.active_supports:
                    state = self._cancel_supports(state, set(state.active_supports))
                all_applied = True
                stage = "all-support-cancellation-exhausted"
                previous_signature = signature
                iteration += 1
                continue
            # Una firma repetida tras agotar ambas reglas confirma un ciclo real.
            if all_applied and first_seen is not None:
                raise UnresolvedMilitaryConflict(
                    CycleDiagnostic(stage, first_seen, iteration, pending, signature)
                )
            targeted_applied = True
            all_applied = True
            previous_signature = signature
            iteration += 1

    def _initial_resolution_state(self) -> ResolutionState:
        """Crea el estado inicial sin éxitos, cancelaciones ni desalojos."""
        supports = frozenset(
            key for key, order in self.orders_by_unit.items() if order.order_type == "S"
        )
        convoys = frozenset(
            key for key, order in self.orders_by_unit.items() if order.is_convoy
        )
        return ResolutionState(
            supports,
            convoys,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            tuple(
                (key, key.origin) for key in sorted(self.units_by_key, key=_key_sort)
            ),
            frozenset(),
            tuple(),
        )

    def _normalise_state(self, state: ResolutionState) -> ResolutionState:
        """Recalcula convoyes, apoyos y posiciones tras cada cambio de estado."""
        cancelled = set(state.cancelled_orders)
        cancelled.update(state.dislodged_units)
        available = frozenset(
            key
            for key, order in self.orders_by_unit.items()
            if order.is_convoy
            and key not in cancelled
            and key not in state.dislodged_units
            and all(
                transporter not in state.dislodged_units
                for transporter in order.transporters
            )
        )
        broken = {
            key
            for key, order in self.orders_by_unit.items()
            if order.is_convoy
            and any(
                transporter in state.dislodged_units
                for transporter in order.transporters
            )
        }
        cancelled.update(broken)
        self._broken_convoys.update(broken)
        return replace(
            state,
            active_supports=(
                state.active_supports - state.dislodged_units - frozenset(cancelled)
            ),
            available_convoys=available,
            cancelled_orders=frozenset(cancelled),
            effective_positions=self._effective_positions_for(state),
        )

    def _effective_positions_for(
        self, state: ResolutionState
    ) -> tuple[tuple[UnitKey, str | None], ...]:
        """Materializa posiciones finales provisionales en orden determinista."""
        return tuple(
            (
                key,
                self._final_location(
                    key,
                    state.successful_moves,
                    state.successful_conversions,
                    state.dislodged_units,
                ),
            )
            for key in sorted(self.units_by_key, key=_key_sort)
        )

    def _conflict_groups(
        self, state: ResolutionState
    ) -> tuple[dict[str, tuple[tuple[str, ...], tuple[UnitKey, ...]]], set[UnitKey]]:
        """Agrupa conflictos pendientes, incluidos los cruces directos enemigos."""
        locations: dict[str, list[UnitKey]] = {}
        moving: set[UnitKey] = set()
        direct: dict[tuple[str, str], UnitKey] = {}
        for key in sorted(self.units_by_key, key=_key_sort):
            if key in state.dislodged_units:
                continue
            order = self.orders_by_unit[key]
            advance = (
                order.order_type == "A"
                and key not in state.cancelled_orders
                and key not in state.successful_moves
                and (not order.is_convoy or key in state.available_convoys)
            )
            conversion = (
                order.order_type == "C"
                and key not in state.cancelled_orders
                and key not in state.successful_conversions
            )
            if advance:
                location = conflict_location(
                    order.target_location or key.origin, key.unit_type
                )
                moving.add(key)
                if not order.is_convoy:
                    direct[key.origin, order.target_location or key.origin] = key
            elif conversion:
                location = conflict_location(
                    key.origin, order.target_location or key.unit_type
                )
                moving.add(key)
            else:
                unit_type = self._final_type(key, state.successful_conversions)
                position = self._final_location(
                    key,
                    state.successful_moves,
                    state.successful_conversions,
                    state.dislodged_units,
                )
                location = conflict_location(position or key.origin, unit_type)
            locations.setdefault(location, []).append(key)
        groups: dict[str, tuple[tuple[str, ...], tuple[UnitKey, ...]]] = {}
        crossed: set[str] = set()
        for (origin, target), unit in direct.items():
            opponent = direct.get((target, origin))
            if opponent is None or opponent.player_id == unit.player_id:
                continue
            endpoints = tuple(
                sorted(
                    (
                        conflict_location(origin, unit.unit_type),
                        conflict_location(target, unit.unit_type),
                    )
                )
            )
            group = "|".join(endpoints)
            if group in groups:
                continue
            members = tuple(
                sorted(
                    {*locations[endpoints[0]], *locations[endpoints[1]]},
                    key=_key_sort,
                )
            )
            groups[group] = (endpoints, members)
            crossed.update(endpoints)
        for location, location_members in locations.items():
            if location not in crossed and set(location_members) & moving:
                groups[location] = (
                    (location,),
                    tuple(sorted(location_members, key=_key_sort)),
                )
        return groups, moving

    def _dependencies(
        self,
        group: str,
        groups: dict[str, tuple[tuple[str, ...], tuple[UnitKey, ...]]],
        moving: set[UnitKey],
        state: ResolutionState,
    ) -> frozenset[str]:
        """Obtiene los grupos pendientes de los que depende un conflicto."""
        locations, units = groups[group]
        group_by_unit = {
            unit: name for name, (_, members) in groups.items() for unit in members
        }
        dependencies: set[str] = set()
        for unit in units:
            order = self.orders_by_unit[unit]
            if unit in moving and order.is_convoy:
                dependencies.update(
                    group_by_unit[transporter]
                    for transporter in order.transporters
                    if transporter in group_by_unit
                )
            if unit in moving:
                dependencies.update(
                    self._strait_dependencies(unit, group_by_unit, moving, state)
                )
            for supporter in state.active_supports:
                support = self.orders_by_unit[supporter]
                if (
                    support.target_location in locations
                    and support.supported_faction == self._player_power(unit)
                    and supporter in group_by_unit
                ):
                    dependencies.add(group_by_unit[supporter])
                if (
                    support.target_location in locations
                    and support.supported_faction == self._player_power(unit)
                ):
                    dependencies.update(
                        self._strait_dependencies(
                            supporter, group_by_unit, moving, state
                        )
                    )
        dependencies.discard(group)
        return frozenset(dependencies)

    def _strait_dependencies(
        self,
        mover: UnitKey,
        group_by_unit: Mapping[UnitKey, str],
        moving: set[UnitKey],
        state: ResolutionState,
    ) -> set[str]:
        """Obtiene los conflictos que determinan la ocupación de un estrecho."""
        dependencies: set[str] = set()
        for strait in self.orders_by_unit[mover].straits:
            for candidate in self.units_by_key:
                if (
                    candidate.unit_type != "F"
                    or candidate.player_id == mover.player_id
                    or candidate in state.dislodged_units
                ):
                    continue
                candidate_order = self.orders_by_unit[candidate]
                may_remain = conflict_location(candidate.origin, "F") == strait
                may_enter = (
                    candidate in moving
                    and candidate_order.order_type == "A"
                    and conflict_location(
                        candidate_order.target_location or candidate.origin, "F"
                    )
                    == strait
                )
                if (may_remain or may_enter) and candidate in group_by_unit:
                    dependencies.add(group_by_unit[candidate])
        return dependencies

    def _blocked_strait_movers(
        self, movers: frozenset[UnitKey], state: ResolutionState
    ) -> set[UnitKey]:
        """Devuelve avances bloqueados por flotas enemigas al fin del movimiento."""
        return {
            mover
            for mover in movers
            if any(
                self._foreign_fleet_occupies_strait(mover, strait, state)
                for strait in self.orders_by_unit[mover].straits
            )
        }

    def _blocked_strait_supports(
        self,
        locations: tuple[str, ...],
        state: ResolutionState,
    ) -> set[UnitKey]:
        """Devuelve los apoyos al grupo bloqueados por un estrecho enemigo."""
        return {
            supporter
            for supporter in state.active_supports
            if self.orders_by_unit[supporter].target_location in locations
            and any(
                self._foreign_fleet_occupies_strait(supporter, strait, state)
                for strait in self.orders_by_unit[supporter].straits
            )
        }

    def _foreign_fleet_occupies_strait(
        self, mover: UnitKey, strait: str, state: ResolutionState
    ) -> bool:
        """Comprueba si una flota enemiga termina ocupando el estrecho."""
        return any(
            candidate.unit_type == "F"
            and candidate.player_id != mover.player_id
            and candidate not in state.dislodged_units
            and (
                final_location := self._final_location(
                    candidate,
                    state.successful_moves,
                    state.successful_conversions,
                    state.dislodged_units,
                )
            )
            is not None
            and conflict_location(final_location, "F") == strait
            for candidate in self.units_by_key
        )

    def _resolve_group(
        self,
        definition: tuple[tuple[str, ...], tuple[UnitKey, ...]],
        moving: set[UnitKey],
        state: ResolutionState,
    ) -> tuple[ResolutionState, frozenset[str]]:
        """Adjudica un grupo independiente y devuelve su nuevo estado."""
        locations, units = definition
        movers = frozenset(set(units) & moving)
        if blocked := self._blocked_strait_movers(movers, state):
            return (
                replace(
                    state,
                    cancelled_orders=state.cancelled_orders | frozenset(blocked),
                ),
                frozenset(),
            )
        if blocked := self._blocked_strait_supports(locations, state):
            return self._cancel_supports(state, blocked), frozenset()
        strengths = {
            unit: (
                1
                + self._support_strength_for(unit, locations, state.active_supports)
                + self._rebellion_modifier(
                    unit, self._conflict_location_for(unit, movers)
                )
            )
            for unit in units
        }
        maximum = max(strengths.values())
        winners = frozenset(unit for unit in units if strengths[unit] == maximum)
        # Un apoyo atacado por el ganador se corta antes de adjudicar el grupo.
        cuts = {
            supporter
            for supporter in state.active_supports & set(units)
            if any(
                attacker in movers
                and attacker in winners
                and self.orders_by_unit[attacker].target_location == supporter.origin
                and attacker.origin != self.orders_by_unit[supporter].target_location
                for attacker in movers
            )
        }
        if cuts:
            return self._cancel_supports(state, cuts), frozenset()
        cancelled = set(state.cancelled_orders)
        cancelled_self = set(state.cancelled_by_self_conflict)
        dislodged = set(state.dislodged_units)
        dislodgements = {record.unit: record for record in state.dislodgements}
        successful_moves = set(state.successful_moves)
        successful_conversions = set(state.successful_conversions)
        factions = {unit.player_id for unit in units}
        effective_positions = dict(self._effective_positions_for(state))
        # Las colisiones propias se cancelan; los empates enemigos tampoco avanzan.
        if len(factions) == 1 and len(units) > 1:
            cancelled.update(movers)
            cancelled_self.update(movers)
            _cancel_moves_towards(
                movers, effective_positions, successful_moves, successful_conversions
            )
        elif len(winners) != 1 or next(iter(winners)) not in movers:
            cancelled.update(movers)
            _cancel_moves_towards(
                movers, effective_positions, successful_moves, successful_conversions
            )
        else:
            winner = next(iter(winners))
            self._mark_success(winner, successful_moves, successful_conversions)
            for unit in units:
                if unit == winner:
                    continue
                if unit in movers:
                    winner_target = conflict_location(
                        self.orders_by_unit[winner].target_location or winner.origin,
                        winner.unit_type,
                    )
                    if conflict_location(unit.origin, unit.unit_type) == winner_target:
                        dislodged.add(unit)
                        self._record_dislodgement(dislodgements, unit, winner)
                        cancelled.add(unit)
                    else:
                        _cancel_moves_towards(
                            [unit],
                            effective_positions,
                            successful_moves,
                            successful_conversions,
                        )
                        cancelled.add(unit)
                else:
                    dislodged.add(unit)
                    self._record_dislodgement(dislodgements, unit, winner)
                    cancelled.add(unit)
        next_state = ResolutionState(
            state.active_supports - frozenset(dislodged),
            state.available_convoys,
            frozenset(successful_moves),
            frozenset(successful_conversions),
            frozenset(dislodged),
            frozenset(cancelled),
            frozenset(cancelled_self),
            self._effective_positions_for(
                replace(
                    state,
                    successful_moves=frozenset(successful_moves),
                    successful_conversions=frozenset(successful_conversions),
                    dislodged_units=frozenset(dislodged),
                )
            ),
            state.resolved_conflicts | frozenset(locations),
            tuple(dislodgements[key] for key in sorted(dislodgements, key=_key_sort)),
        )
        return (
            next_state,
            frozenset(locations) if len(factions) > 1 else frozenset(),
        )

    def _conflict_location_for(self, unit: UnitKey, moving: frozenset[UnitKey]) -> str:
        """Calcula la plaza defendida o atacada por una unidad del grupo."""
        order = self.orders_by_unit[unit]
        if unit not in moving:
            return conflict_location(unit.origin, unit.unit_type)
        if order.order_type == "A":
            return conflict_location(
                order.target_location or unit.origin, unit.unit_type
            )
        if order.order_type == "C":
            return conflict_location(
                unit.origin, order.target_location or unit.unit_type
            )
        return conflict_location(unit.origin, unit.unit_type)

    def _record_dislodgement(
        self,
        dislodgements: dict[UnitKey, DislodgementRecord],
        unit: UnitKey,
        winner: UnitKey,
    ) -> None:
        """Registra una única procedencia para cada unidad desalojada."""
        if unit in dislodgements:
            raise MilitaryResolutionError("Unidad desalojada más de una vez")
        order = self.orders_by_unit[winner]
        attack_origin = (
            order.path[-2]
            if order.order_type == "A" and order.is_convoy
            else conflict_location(winner.origin, winner.unit_type)
            if order.order_type in ("A", "C")
            else None
        )
        dislodgements[unit] = DislodgementRecord(unit, attack_origin)

    def _support_strength_for(
        self,
        unit: UnitKey,
        locations: tuple[str, ...],
        active_supports: frozenset[UnitKey],
    ) -> int:
        """Cuenta los apoyos activos que refuerzan a la facción en el grupo."""
        order = self.orders_by_unit[unit]
        target = (
            conflict_location(order.target_location or unit.origin, unit.unit_type)
            if order.order_type == "A"
            else conflict_location(unit.origin, unit.unit_type)
        )
        if order.order_type == "A":
            target = conflict_location(
                order.target_location or unit.origin, unit.unit_type
            )
        elif order.order_type == "C" and order.target_location in ("A", "F"):
            target = conflict_location(unit.origin, order.target_location)
        else:
            target = conflict_location(unit.origin, unit.unit_type)
        faction = self._player_power(unit)
        return sum(
            1
            for supporter in active_supports
            if (support := self.orders_by_unit[supporter]).target_location == target
            and support.target_location in locations
            and support.supported_faction == faction
        )

    def _active_attackers(
        self,
        cancelled: set[UnitKey],
        dislodged: set[UnitKey],
        available_convoys: set[UnitKey],
    ) -> tuple[tuple[str, str], ...]:
        """Enumera avances todavía capaces de cortar apoyos."""
        return tuple(
            (key.origin, order.target_location or key.origin)
            for key, order in self.orders_by_unit.items()
            if order.order_type == "A"
            and key not in cancelled
            and key not in dislodged
            and (not order.is_convoy or key in available_convoys)
        )

    def _targeted_supports(self, state: ResolutionState) -> set[UnitKey]:
        """Selecciona apoyos atacados que pueden romper un ciclo de dependencias."""
        attackers = self._active_attackers(
            set(state.cancelled_orders),
            set(state.dislodged_units),
            set(state.available_convoys),
        )
        return {
            supporter
            for supporter in state.active_supports
            if any(
                target == supporter.origin
                and origin != self.orders_by_unit[supporter].target_location
                for origin, target in attackers
            )
        }

    def _cancel_supports(
        self, state: ResolutionState, supporters: set[UnitKey]
    ) -> ResolutionState:
        """Devuelve un estado nuevo con los apoyos indicados cancelados."""
        return replace(
            state,
            active_supports=state.active_supports - supporters,
            cancelled_orders=state.cancelled_orders | supporters,
        )

    def _rebellion_modifier(self, unit: UnitKey, location: str) -> int:
        """Aplica la rebelión a la plaza provincial sin crear participantes."""
        if location.startswith("G "):
            return 0
        rebellion = self.rebellions_by_location.get(location.split()[0])
        if rebellion is None or unit.player_id is None:
            return 0
        controller_id, _kind = rebellion
        return int(unit.player_id != controller_id)

    def _state_signature(self, state: ResolutionState) -> ResolutionSignature:
        """Reduce el estado a primitivas para detectar estabilidad y ciclos."""

        def keys(items: frozenset[UnitKey]) -> tuple[tuple[str, str, str], ...]:
            """Serializa un conjunto de unidades con orden estable."""
            return tuple(
                (_key.player_id or "", _key.unit_type, _key.origin)
                for _key in sorted(items, key=_key_sort)
            )

        return (
            ("active_supports", keys(state.active_supports)),
            ("available_convoys", keys(state.available_convoys)),
            ("successful_moves", keys(state.successful_moves)),
            ("successful_conversions", keys(state.successful_conversions)),
            ("dislodged_units", keys(state.dislodged_units)),
            (
                "dislodgements",
                tuple(
                    (
                        record.unit.player_id or "",
                        record.unit.unit_type,
                        record.unit.origin,
                        record.attack_origin,
                    )
                    for record in state.dislodgements
                ),
            ),
            ("cancelled_orders", keys(state.cancelled_orders)),
            ("cancelled_by_self_conflict", keys(state.cancelled_by_self_conflict)),
            (
                "effective_positions",
                tuple(
                    (key.player_id or "", key.unit_type, key.origin, location)
                    for key, location in state.effective_positions
                ),
            ),
            ("resolved_conflicts", tuple(sorted(state.resolved_conflicts))),
        )

    def _mark_success(
        self,
        key: UnitKey,
        moves: set[UnitKey],
        conversions: set[UnitKey],
    ) -> None:
        """Registra el éxito en la colección correspondiente al tipo de orden."""
        if self.orders_by_unit[key].order_type == "A":
            moves.add(key)
        else:
            conversions.add(key)

    def _final_location(
        self,
        key: UnitKey,
        moves: set[UnitKey] | frozenset[UnitKey],
        conversions: set[UnitKey] | frozenset[UnitKey],
        dislodged: set[UnitKey] | frozenset[UnitKey],
    ) -> str | None:
        """Obtiene la posición resultante; una conversión cambia tipo, no ubicación."""
        if key in dislodged:
            return None
        order = self.orders_by_unit[key]
        if key in moves:
            return order.target_location
        return key.origin

    def _build_resolution(
        self,
        state: ResolutionState,
        additional_dislodged: frozenset[UnitKey] = frozenset(),
    ) -> MilitaryResolution:
        """Convierte el estado estable en un resultado completo por unidad inicial."""
        dislodged_units = state.dislodged_units | additional_dislodged
        attack_origins = {
            record.unit: record.attack_origin for record in state.dislodgements
        }
        outcomes = tuple(
            UnitOutcome(
                key,
                self._final_type(key, state.successful_conversions),
                self._final_location(
                    key,
                    state.successful_moves,
                    state.successful_conversions,
                    dislodged_units,
                ),
                key in dislodged_units,
                attack_origins.get(key),
            )
            for key in sorted(self.units_by_key, key=_key_sort)
        )
        if len({outcome.unit for outcome in outcomes}) != len(self.units_by_key):
            raise MilitaryResolutionError("Falta un resultado de unidad")
        return MilitaryResolution(
            outcomes,
            getattr(self, "_contested_locations", frozenset()),
        )

    def _final_type(
        self, key: UnitKey, successful_conversions: frozenset[UnitKey]
    ) -> str:
        """Devuelve el tipo original o el obtenido por una conversión exitosa."""
        if key not in successful_conversions:
            return key.unit_type
        return self.orders_by_unit[key].target_location or key.unit_type

    def _build_final_collections(
        self, resolution: MilitaryResolution
    ) -> tuple[dict[str, dict[str, list[str]]], list[str], list[str]]:
        """Prepara colecciones nuevas sin modificar todavía jugadores ni partida."""
        if any(
            order.is_convoy
            and (
                not order.transporters
                or any(
                    transporter not in self.units_by_key
                    for transporter in order.transporters
                )
            )
            for order in self.orders_by_unit.values()
        ):
            raise MilitaryResolutionError("Convoy parcial")
        players: dict[str, dict[str, list[str]]] = {
            player.player_id: {"A": [], "F": [], "G": []}
            for player in self.game.players
        }
        independent: list[str] = []
        for outcome in resolution.outcomes:
            if outcome.dislodged:
                continue
            if outcome.final_location is None:
                raise MilitaryResolutionError("Resultado sin localización")
            if outcome.unit.player_id is None:
                independent.append(outcome.final_location)
            elif outcome.final_unit_type in ("A", "F", "G"):
                players[outcome.unit.player_id][outcome.final_unit_type].append(
                    outcome.final_location
                )
        for collections in players.values():
            for locations in collections.values():
                locations.sort()
        independent.sort()
        self._validate_final_collections(players, independent)
        return players, independent, list(self.game.besieges)

    def _validate_final_collections(
        self, players: dict[str, dict[str, list[str]]], independent: list[str]
    ) -> None:
        """Comprueba tipos, costas y ocupaciones antes del commit militar."""
        occupied_campaign: set[str] = set()
        occupied_garrisons: set[str] = set()
        for collections in players.values():
            for unit_type, locations in collections.items():
                for location in locations:
                    if unit_type == "A" and location not in self.map.provinces:
                        raise MilitaryResolutionError("Ejército en el mar")
                    if unit_type == "F" and not self._valid_fleet_location(location):
                        raise MilitaryResolutionError("Costa de flota inválida")
                    occupied = (
                        occupied_garrisons if unit_type == "G" else occupied_campaign
                    )
                    conflict = conflict_location(location, unit_type)
                    if conflict in occupied:
                        raise MilitaryResolutionError("Ocupación final duplicada")
                    occupied.add(conflict)
        for location in independent:
            conflict = conflict_location(location, "G")
            if conflict in occupied_garrisons:
                raise MilitaryResolutionError("Ocupación final duplicada")
            occupied_garrisons.add(conflict)

    def _siege_target_present(self, location: str) -> bool:
        """Comprueba si un asedio conserva su objetivo aplicar desbandar."""
        if any(
            key.unit_type == "G" and key.origin == location for key in self.units_by_key
        ):
            return True
        rebellion = self.rebellions_by_location.get(location)
        return rebellion is not None and rebellion[1] == "city"

    def _build_rule_transitions(
        self,
        state: ResolutionState,
        player_collections: dict[str, dict[str, list[str]]],
        independent: list[str],
    ) -> tuple[
        dict[str, dict[str, list[str]]],
        list[str],
        list[list[object]],
        list[list[object]],
        frozenset[UnitKey],
    ]:
        """Calcula rebeliones, asedios y desalojos derivados sin tocar el juego."""
        rebellions = {
            player.player_id: {
                "province": list(player.rebelled_provinces),
                "city": list(player.rebelled_cities),
            }
            for player in self.game.players
        }
        rebellion_events: list[list[object]] = []
        siege_events: list[list[object]] = []
        siege_dislodged: set[UnitKey] = set()

        if len(set(self.game.besieges)) != len(self.game.besieges):
            raise MilitaryResolutionError("Asedio duplicado")
        active_sieges = set(self.game.besieges)
        # Primero se resuelven los asedios que ya estaban activos al comenzar.
        for location in sorted(tuple(active_sieges)):
            besieger = self.campaign_unit_by_conflict_location.get(location)
            if besieger is None:
                raise MilitaryResolutionError("Asedio sin unidad asediadora")
            order = self.orders_by_unit[besieger]
            if besieger in state.dislodged_units:
                active_sieges.remove(location)
                siege_events.append([self._primitive_key(besieger), location, "lifted"])
            elif order.order_type == "L" and besieger not in state.cancelled_orders:
                active_sieges.remove(location)
                siege_events.append([self._primitive_key(besieger), location, "lifted"])
            elif order.order_type == "B" and besieger not in state.cancelled_orders:
                active_sieges.remove(location)
                rebellion_event, removed_unit = self._remove_siege_target(
                    location, player_collections, independent, rebellions
                )
                if rebellion_event is not None:
                    rebellion_events.append(rebellion_event)
                if removed_unit is not None:
                    siege_dislodged.add(removed_unit)
                siege_events.append(
                    [self._primitive_key(besieger), location, "completed"]
                )

        # Después se registran los nuevos asedios válidos de esta campaña.
        for key in sorted(self.orders_by_unit, key=_key_sort):
            order = self.orders_by_unit[key]
            location = key.origin.split()[0]
            if (
                order.order_type == "B"
                and key not in state.cancelled_orders
                and key not in state.dislodged_units
                and location not in self.game.besieges
                and self._siege_target_present(location)
            ):
                active_sieges.add(location)
                siege_events.append([self._primitive_key(key), location, "started"])

        final_dislodged = state.dislodged_units | frozenset(siege_dislodged)
        # Las rebeliones provinciales se actualizan desde el resultado militar estable.
        for owner_id in sorted(rebellions):
            for location in tuple(sorted(rebellions[owner_id]["province"])):
                if self._foreign_advance_succeeded(location, owner_id, state):
                    rebellions[owner_id]["province"].remove(location)
                    rebellion_events.append(
                        [owner_id, "province", location, "liberated"]
                    )
                elif self._controller_hold_succeeded(
                    location,
                    owner_id,
                    state,
                    final_dislodged,
                ):
                    rebellions[owner_id]["province"].remove(location)
                    rebellion_events.append([owner_id, "province", location, "subdued"])

        for collections in rebellions.values():
            collections["province"].sort()
            collections["city"].sort()
        besieges = sorted(active_sieges)
        self._validate_rule_collections(
            player_collections, independent, rebellions, besieges
        )
        return (
            rebellions,
            besieges,
            rebellion_events,
            siege_events,
            frozenset(siege_dislodged),
        )

    def _foreign_advance_succeeded(
        self, location: str, owner_id: str, state: ResolutionState
    ) -> bool:
        """Detecta si una facción ajena liberó la provincia rebelada."""
        return any(
            key.player_id != owner_id
            and conflict_location(
                self.orders_by_unit[key].target_location or key.origin,
                key.unit_type,
            )
            == location
            for key in state.successful_moves
        )

    def _controller_hold_succeeded(
        self,
        location: str,
        owner_id: str,
        state: ResolutionState,
        dislodged_units: frozenset[UnitKey],
    ) -> bool:
        """Comprueba si un Hold efectivo del controlador sometió la rebelión."""
        for key in sorted(self.units_by_key, key=_key_sort):
            if (
                key.player_id != owner_id
                or key.origin.split()[0] != location
                or self.orders_by_unit[key].order_type != "H"
                or key in state.cancelled_orders
                or key in dislodged_units
            ):
                continue
            final_location = self._final_location(
                key,
                state.successful_moves,
                state.successful_conversions,
                dislodged_units,
            )
            if final_location is not None and final_location.split()[0] == location:
                return True
        return False

    def _remove_siege_target(
        self,
        location: str,
        player_collections: dict[str, dict[str, list[str]]],
        independent: list[str],
        rebellions: dict[str, dict[str, list[str]]],
    ) -> tuple[list[object] | None, UnitKey | None]:
        """Retira el único objetivo de un asedio completado y describe su efecto."""
        for player_id in sorted(player_collections):
            if location in player_collections[player_id]["G"]:
                key = UnitKey(player_id, "G", location)
                if key not in self.units_by_key:
                    raise MilitaryResolutionError(
                        "Guarnición asediada ausente del snapshot inicial"
                    )
                player_collections[player_id]["G"].remove(location)
                return None, key
        if location in independent:
            key = UnitKey(None, "G", location)
            if key not in self.units_by_key:
                raise MilitaryResolutionError(
                    "Guarnición independiente asediada ausente del snapshot inicial"
                )
            independent.remove(location)
            return None, key
        for owner_id in sorted(rebellions):
            if location in rebellions[owner_id]["city"]:
                rebellions[owner_id]["city"].remove(location)
                return [owner_id, "city", location, "subdued"], None
        raise MilitaryResolutionError("Asedio completado sin objetivo")

    def _validate_rule_collections(
        self,
        player_collections: dict[str, dict[str, list[str]]],
        independent: list[str],
        rebellions: dict[str, dict[str, list[str]]],
        besieges: list[str],
    ) -> None:
        """Valida la coherencia conjunta de rebeliones, guarniciones y asedios."""
        garrison_locations = [
            location
            for collections in player_collections.values()
            for location in collections["G"]
        ] + independent
        for location in garrison_locations:
            province = self.map.provinces.get(location)
            if province is None or not self._is_defensible_city(province.city):
                raise MilitaryResolutionError(
                    "Estado final de guarnición fuera de ciudad defendible"
                )

        rebellion_locations: set[str] = set()
        city_rebellions: dict[str, str] = {}
        for owner_id, collections in rebellions.items():
            for kind in ("province", "city"):
                for location in collections[kind]:
                    province = self.map.provinces.get(location)
                    if province is None or location in rebellion_locations:
                        raise MilitaryResolutionError(
                            "Estado final de rebelión inválido"
                        )
                    if kind == "city":
                        if not self._is_defensible_city(province.city):
                            raise MilitaryResolutionError(
                                "Rebelión urbana final fuera de ciudad defendible"
                            )
                        city_rebellions[location] = owner_id
                    rebellion_locations.add(location)

        for location in city_rebellions:
            if location in independent or any(
                location in collections["G"]
                for collections in player_collections.values()
            ):
                raise MilitaryResolutionError(
                    "Rebelión urbana final incompatible con guarnición"
                )

        if len(set(besieges)) != len(besieges):
            raise MilitaryResolutionError("Estado final de asedios duplicado")
        for location in besieges:
            province = self.map.provinces.get(location)
            if province is None or not self._is_defensible_city(province.city):
                raise MilitaryResolutionError("Estado final de asedio inválido")
            besiegers = [
                (player_id, unit_type)
                for player_id, collections in player_collections.items()
                for unit_type in ("A", "F")
                for unit_location in collections[unit_type]
                if conflict_location(unit_location, unit_type) == location
            ]
            if len(besiegers) != 1:
                raise MilitaryResolutionError("Asedio final sin asediador único")
            if besiegers[0][1] == "F" and not province.has_port:
                raise MilitaryResolutionError("Flota asediadora final sin puerto")
            targets = sum(
                location in collections["G"]
                for collections in player_collections.values()
            )
            targets += int(location in independent)
            targets += int(location in city_rebellions)
            if targets != 1:
                raise MilitaryResolutionError("Asedio final sin objetivo único")
            if (
                location in city_rebellions
                and besiegers[0][0] != city_rebellions[location]
            ):
                raise MilitaryResolutionError(
                    "Asedio de rebelión urbana por una facción no controladora"
                )

    @staticmethod
    def _primitive_key(key: UnitKey) -> list[str | None]:
        """Convierte una identidad militar al formato serializable del evento."""
        return [key.player_id, key.unit_type, key.origin]

    def _resolve_dislodgements(
        self,
        resolution: MilitaryResolution,
        dislodgement_resolver: DislodgementResolver | None,
        siege_dislodged: frozenset[UnitKey],
    ) -> dict[UnitKey, DislodgementDecision]:
        """Solicita y valida una decisión exacta para cada unidad desalojada."""
        dislodged = {
            outcome.unit for outcome in resolution.outcomes if outcome.dislodged
        }
        if not dislodged:
            return {}
        if dislodgement_resolver is None:
            raise DislodgementResolverRequired("Se requiere gestor de desalojos")
        try:
            raw_decisions = dislodgement_resolver(resolution)
            if not isinstance(raw_decisions, Mapping):
                raise MilitaryResolutionError(
                    "El gestor de desalojos debe devolver un mapping"
                )
            decisions = dict(raw_decisions)
        except MilitaryResolutionError:
            raise
        except Exception as error:
            raise MilitaryResolutionError("Falló el gestor de desalojos") from error
        if set(decisions) != dislodged:
            raise MilitaryResolutionError(
                "El gestor de desalojos no cubre exactamente las unidades desalojadas"
            )
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
        for key, decision in decisions.items():
            destination = decision.destination
            decision_type = decision.decision_type
            if destination is not None and (
                not isinstance(destination, str) or not destination
            ):
                raise MilitaryResolutionError("Destino de retirada inválido")
            if key in siege_dislodged and destination is not None:
                raise MilitaryResolutionError(
                    "Una guarnición eliminada por asedio no puede retirarse"
                )
            if destination is not None and destination == outcomes[key].attack_origin:
                raise MilitaryResolutionError(
                    "Una unidad no puede retirarse al origen del ataque que la desalojó"
                )
            if (
                destination is not None
                and decision_type == DecisionType.RETREAT
                and conflict_location(destination, outcomes[key].final_unit_type)
                in resolution.contested_locations
            ):
                raise MilitaryResolutionError(
                    "Destino de retirada situado en un lugar disputado"
                )
        return decisions

    def _apply_dislodgement_decisions(
        self,
        resolution: MilitaryResolution,
        decisions: Mapping[UnitKey, DislodgementDecision],
        player_collections: dict[str, dict[str, list[str]]],
        independent: list[str],
    ) -> None:
        """Integra retiradas válidas en las colecciones locales ya consolidadas."""
        outcomes = {outcome.unit: outcome for outcome in resolution.outcomes}
        for key in sorted(decisions, key=_key_sort):
            decision = decisions[key]
            decision_type = decision.decision_type
            destination = decision.destination

            outcome = outcomes[key]

            unit_type = (
                outcome.unit.unit_type if decision_type == DecisionType.RETREAT else "G"
            )

            if (
                decision.decision_type == DecisionType.DISBAND
                or decision.destination is None
            ):
                continue

            if unit_type == "A" and destination not in self.map.provinces:
                raise MilitaryResolutionError("Retirada de ejército fuera de provincia")
            if unit_type == "F" and not self._valid_fleet_location(destination):
                raise MilitaryResolutionError("Retirada de flota a costa inválida")
            if unit_type == "G" and destination not in self.map.provinces:
                raise MilitaryResolutionError(
                    "Retirada de guarnición fuera de provincia"
                )

            if key.player_id is None:
                if unit_type != "G":
                    raise MilitaryResolutionError(
                        "Unidad independiente desalojada de tipo inválido"
                    )
                independent.append(destination)
            else:
                try:
                    player_collections[key.player_id][unit_type].append(destination)
                except KeyError as error:
                    raise MilitaryResolutionError(
                        "Propietario o tipo de retirada inválido"
                    ) from error
        for collections in player_collections.values():
            for locations in collections.values():
                locations.sort()
        independent.sort()
        self._validate_final_collections(player_collections, independent)

    def _apply_final_collections(
        self,
        player_collections: dict[str, dict[str, list[str]]],
        independent: list[str],
        besieges: list[str],
        event: TurnEvent,
        rebellion_collections: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        """Única frontera de commit: todos los valores ya están validados."""
        if rebellion_collections is None:
            rebellion_collections = {
                player.player_id: {
                    "province": list(player.rebelled_provinces),
                    "city": list(player.rebelled_cities),
                }
                for player in self.game.players
            }
        # Se conservan las referencias originales para restaurarlas si algo falla.
        previous_players = {
            player.player_id: (
                player.armies,
                player.fleets,
                player.garrisons,
                player.rebelled_provinces,
                player.rebelled_cities,
            )
            for player in self.game.players
        }
        previous_game = (
            self.game.independent_garrisons,
            self.game.besieges,
            self.game.turn_events,
        )
        try:
            # Todas las asignaciones forman una única frontera lógica de commit.
            for player in self.game.players:
                collections = player_collections[player.player_id]
                player.armies = collections["A"]
                player.fleets = collections["F"]
                player.garrisons = collections["G"]
                player_rebellions = rebellion_collections[player.player_id]
                player.rebelled_provinces = player_rebellions["province"]
                player.rebelled_cities = player_rebellions["city"]
            self.game.independent_garrisons = independent
            self.game.besieges = besieges
            self.game.turn_events.append(event)
        except Exception as error:
            # El rollback es defensivo y restaura cada colección por separado.
            for player in self.game.players:
                armies, fleets, garrisons, rebelled_provinces, rebelled_cities = (
                    previous_players[player.player_id]
                )
                for name, value in (
                    ("armies", armies),
                    ("fleets", fleets),
                    ("garrisons", garrisons),
                    ("rebelled_provinces", rebelled_provinces),
                    ("rebelled_cities", rebelled_cities),
                ):
                    try:
                        setattr(player, name, value)
                    except Exception:
                        logger.exception(
                            "No se pudo restaurar %s del jugador %s",
                            name,
                            player.player_id,
                        )
            for name, game_value in zip(
                ("independent_garrisons", "besieges", "turn_events"),
                previous_game,
                strict=True,
            ):
                try:
                    setattr(self.game, name, game_value)
                except Exception:
                    logger.exception("No se pudo restaurar %s del juego", name)
            raise MilitaryResolutionError("Falló el commit militar") from error

    def run(
        self, dislodgement_resolver: DislodgementResolver | None = None
    ) -> MilitaryResolution:
        """Resuelve el turno y solo entonces sustituye el estado del juego."""
        # Fase 1: capturar el snapshot y compilar una intención por unidad
        self._build_unit_index()
        self._compile_orders()
        self._link_and_validate_orders()

        # Fase 2: generar el evento con las órdenes válidas recibidas
        try:
            event = self._event_from_military_orders()
        except Exception as error:
            raise MilitaryResolutionError(
                "No se pudo construir el evento de resumen de órdenes"
            ) from error
        self.game.turn_events.append(event)

        # Antes de comenzar la fase 3, retiramos las unidades desbandadas
        disbanded = self._remove_disbanded_units()
        if disbanded:
            logger.info("Unidades desbandadas: %s", len(disbanded))

        # Fase 3: alcanzar un estado estable y verificar que no quedan conflictos
        state = self._resolve_conflicts()
        pending = self._effective_conflict_keys() - state.resolved_conflicts
        if pending:
            raise MilitaryResolutionError("Quedan conflictos efectivos pendientes")

        # Fase 4: calcular transiciones derivadas sobre colecciones provisionales
        provisional_resolution = self._build_resolution(state)
        provisional_collections, provisional_independent, _ = (
            self._build_final_collections(provisional_resolution)
        )
        (
            rebellions,
            besieges,
            rebellion_events,
            siege_events,
            siege_dislodged,
        ) = self._build_rule_transitions(
            state,
            provisional_collections,
            provisional_independent,
        )
        resolution = self._build_resolution(state, siege_dislodged)
        collections, independent, _ = self._build_final_collections(resolution)

        # Fase 5: resolver retiradas antes de validar el estado definitivo
        decisions = self._resolve_dislodgements(
            resolution,
            dislodgement_resolver,
            siege_dislodged,
        )
        self._apply_dislodgement_decisions(
            resolution,
            decisions,
            collections,
            independent,
        )
        self._validate_rule_collections(
            collections,
            independent,
            rebellions,
            besieges,
        )

        # Fase 6: construir el evento antes del commit
        try:
            event = self._event_from_resolution(
                resolution,
                state,
                rebellions=rebellion_events,
                sieges=siege_events,
                decisions=decisions,
            )
        except Exception as error:
            raise MilitaryResolutionError(
                "No se pudo construir el evento militar"
            ) from error
        logger.info(
            "Resolución militar: outcomes=%s cancelled=%s",
            len(resolution.outcomes),
            len(state.cancelled_orders),
        )

        # Fase 7: aplicar todas las colecciones validadas de forma atómica.
        self._apply_final_collections(
            collections,
            independent,
            besieges,
            event,
            rebellion_collections=rebellions,
        )
        return resolution

    def _event_from_military_orders(self) -> TurnEvent:
        """Construye el evento de compilación y resumen de órdenes."""
        orders: list[list[object]] = [
            [
                [unit.player_id, unit.unit_type, unit.origin],
                order.order_type,
                order.target_location or None,
                list(order.path) if order.path else None,
                (
                    [
                        order.transported_army.player_id,
                        order.transported_army.unit_type,
                        order.transported_army.origin,
                    ]
                    if order.transported_army is not None
                    else None
                ),
                order.supported_faction,
                order.is_convoy,
            ]
            for unit, order in self.orders_by_unit.items()
        ]
        invalid_orders: list[list[object]] = (
            [
                [[unit.player_id, unit.unit_type, unit.origin], reason]
                for unit, reason in self.invalid_orders.items()
            ]
            if self.invalid_orders
            else []
        )
        try:
            return TurnEvent.military_orders_summary(orders, invalid_orders)
        except Exception as error:
            raise MilitaryResolutionError(
                "No se pudo construir el evento de resumen de órdenes"
            ) from error

    def _event_from_resolution(
        self,
        resolution: MilitaryResolution,
        state: ResolutionState,
        *,
        rebellions: list[list[object]] | None = None,
        sieges: list[list[object]] | None = None,
        decisions: dict[UnitKey, DislodgementDecision] | None = None,
    ) -> TurnEvent:
        """Construye el único evento canónico de la resolución militar."""
        outcomes: list[list[object]] = [
            [
                [outcome.unit.player_id, outcome.unit.unit_type, outcome.unit.origin],
                outcome.final_unit_type,
                outcome.final_location,
                outcome.dislodged,
                outcome.attack_origin,
            ]
            for outcome in resolution.outcomes
        ]

        decision_list: list[list[object]] = (
            [
                [
                    [unit.player_id, unit.unit_type, unit.origin],
                    decision.decision_type,
                    decision.destination,
                ]
                for unit, decision in decisions.items()
            ]
            if decisions
            else None
        )

        def primitive_keys(items: Iterable[UnitKey]) -> list[list[object]]:
            """Ordena y serializa una colección de identidades militares."""
            return [
                [key.player_id, key.unit_type, key.origin]
                for key in sorted(items, key=_key_sort)
            ]

        try:
            return TurnEvent.military_resolution(
                outcomes,
                primitive_keys(state.cancelled_orders),
                primitive_keys(self._broken_convoys),
                primitive_keys(
                    outcome.unit for outcome in resolution.outcomes if outcome.dislodged
                ),
                rebellions if rebellions is not None else [],
                sieges if sieges is not None else [],
                decision_list if decision_list is not None else [],
            )
        except Exception as error:
            raise MilitaryResolutionError(
                "No se pudo construir el evento militar"
            ) from error
