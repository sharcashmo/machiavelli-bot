"""Excepciones generadas durante el ciclo de vida del dominio de la partida."""


class FailedToStartError(Exception):
    """Se lanza cuando una partida no puede comenzar porque faltan requisitos previos.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class DuplicatedGameException(Exception):
    """Se lanza cuando el nombre o el canal de una partida ya están registrados."""


class GameNotFoundException(Exception):
    """Se lanza cuando la partida solicitada no existe en la persistencia."""


class GameRuleException(Exception):
    """Clase base para las operaciones no válidas del agregado solicitadas por el
    llamador.
    """


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
    "DuplicatedGameException",
    "FailedToStartError",
    "GameNotFoundException",
    "GameRuleException",
    "PlayerNotFoundException",
    "TradeRuleException",
]
