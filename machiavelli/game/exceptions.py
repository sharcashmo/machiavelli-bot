"""Excepciones generadas durante el ciclo de vida del dominio de la partida."""


class FailedToStartError(Exception):
    """Se lanza cuando una partida no puede comenzar porque faltan requisitos."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class GameRuleException(Exception):
    """Clase base para las operaciones no válidas del agregado solicitadas por el
    llamador.
    """


class DuplicatedGameException(Exception):
    """La partida ya está registrada en la base de datos."""


class GameNotFoundException(Exception):
    """La partida buscada no existe."""


class DuplicatePlayerException(GameRuleException):
    """Se lanza cuando el identificador de un jugador o su cuenta de Discord ya están
    registrados.
    """


class PlayerNotFoundException(GameRuleException):
    """Se lanza cuando el jugador solicitado no pertenece a una partida."""


class TradeRuleException(GameRuleException):
    """Se lanza cuando una regla de intercambio rechaza una operación de recursos
    solicitada.
    """


__all__ = [
    "DuplicatePlayerException",
    "FailedToStartError",
    "GameRuleException",
    "PlayerNotFoundException",
    "TradeRuleException",
]
