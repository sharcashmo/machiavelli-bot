"""Exceptions raised by the game domain lifecycle."""


class FailedToStartError(Exception):
    """Raised when a game cannot start because prerequisites are missing."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class DuplicatedGameException(Exception):
    """Raised when a game name or channel is already registered."""


class GameNotFoundException(Exception):
    """Raised when a requested game does not exist in persistence."""


class GameRuleException(Exception):
    """Base class for invalid aggregate operations requested by a caller."""


class DuplicatePlayerException(GameRuleException):
    """Raised when a player identifier or Discord account is already registered."""


class PlayerNotFoundException(GameRuleException):
    """Raised when a requested player does not belong to a game."""


class TradeRuleException(GameRuleException):
    """Raised when a trading rule rejects a requested resource operation."""


__all__ = [
    "DuplicatePlayerException",
    "DuplicatedGameException",
    "FailedToStartError",
    "GameNotFoundException",
    "GameRuleException",
    "PlayerNotFoundException",
    "TradeRuleException",
]
