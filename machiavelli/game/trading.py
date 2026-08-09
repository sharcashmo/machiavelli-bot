"""Domain rules for direct resource transfers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .exceptions import TradeRuleException
from .tables import GameTables

if TYPE_CHECKING:
    from .player import Player
    from .scenario import Scenario

TradeKind = Literal["ducats", "assassin"]


@dataclass(frozen=True, slots=True)
class TradeResource:
    """Represent one transferable resource and its value."""

    kind: TradeKind
    value: int | str

    def __post_init__(self) -> None:
        if self.kind not in ("ducats", "assassin"):
            raise TradeRuleException(
                "Tipo de recurso inválido. Usa 'ducats' o 'assassin'."
            )
        if self.kind == "ducats":
            if type(self.value) is not int or self.value <= 0:
                raise TradeRuleException(
                    "La cantidad de ducados debe ser un entero mayor que cero."
                )
        elif not isinstance(self.value, str) or not self.value:
            raise TradeRuleException(
                "La facción objetivo de la ficha de asesinato no es válida en "
                "este escenario."
            )


@dataclass(frozen=True, slots=True)
class ExchangeProposal:
    """Represent one pending bilateral exchange proposal."""

    proposer_power: str
    counterparty_power: str
    give: TradeResource
    receive: TradeResource

    def __post_init__(self) -> None:
        if self.proposer_power == self.counterparty_power:
            raise TradeRuleException(
                "La facción de destino no está asignada a otro jugador de esta partida."
            )

    @property
    def pair_key(self) -> tuple[str, str]:
        power_a, power_b = sorted((self.proposer_power, self.counterparty_power))
        return power_a, power_b

    def is_exact_inverse(self, other: ExchangeProposal) -> bool:
        """Return whether another proposal exactly reverses this one."""
        return (
            self.proposer_power == other.counterparty_power
            and self.counterparty_power == other.proposer_power
            and self.give == other.receive
            and self.receive == other.give
        )


def find_exchange_proposal_index(
    proposals: list[ExchangeProposal], pair_key: tuple[str, str]
) -> int | None:
    """Find the first pending proposal for one unordered power pair."""
    # ponytail: O(n) para 21 parejas de carga objetivo; añadir índice si cambia la escala  # noqa: E501
    for index, proposal in enumerate(proposals):
        if proposal.pair_key == pair_key:
            return index
    return None


def parse_trade_resource(
    scenario: Scenario,
    kind: str,
    raw_value: str,
) -> TradeResource:
    """Parse and validate one resource against the active scenario."""
    if kind not in ("ducats", "assassin"):
        raise TradeRuleException("Tipo de recurso inválido. Usa 'ducats' o 'assassin'.")

    if kind == "ducats":
        try:
            value = int(raw_value)
        except (ValueError, TypeError) as error:
            raise TradeRuleException(
                "La cantidad de ducados debe ser un entero mayor que cero."
            ) from error
        if value <= 0:
            raise TradeRuleException(
                "La cantidad de ducados debe ser un entero mayor que cero."
            )
        return TradeResource("ducats", value)

    if not scenario.rules.assassinations_active:
        raise TradeRuleException(
            "Las fichas de asesinato no están disponibles en este escenario."
        )
    if raw_value not in scenario.powers:
        raise TradeRuleException(
            "La facción objetivo de la ficha de asesinato no es válida en "
            "este escenario."
        )
    return TradeResource("assassin", raw_value)


def player_has_trade_resource(player: Player, resource: TradeResource) -> bool:
    """Return whether a player currently owns the requested resource."""
    if resource.kind == "ducats":
        assert isinstance(resource.value, int)
        return player.ducats >= resource.value
    assert isinstance(resource.value, str)
    return resource.value in player.ass_counters


def transfer_trade_resource(
    sender: Player,
    receiver: Player,
    resource: TradeResource,
) -> None:
    """Move one resource from sender to receiver after checking ownership."""
    if not player_has_trade_resource(sender, resource):
        if resource.kind == "ducats":
            raise TradeRuleException("No tienes suficientes ducados.")
        assert isinstance(resource.value, str)
        target = GameTables.powers[resource.value]
        raise TradeRuleException(f"No tienes una ficha de asesinato contra {target}.")

    if resource.kind == "ducats":
        assert isinstance(resource.value, int)
        sender.ducats -= resource.value
        receiver.ducats += resource.value
        return

    assert isinstance(resource.value, str)
    sender.ass_counters.remove(resource.value)
    receiver.ass_counters.append(resource.value)
