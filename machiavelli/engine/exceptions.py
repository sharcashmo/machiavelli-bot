# machiavelli/engine/exceptions.py


class EngineError(Exception):
    """Clase base para todas las excepciones del motor de juego."""

    pass


class GameInitializationError(EngineError):
    """Se lanza cuando la configuración inicial de la partida no es válida."""

    pass


class InvalidPlayerCountError(GameInitializationError):
    """Se lanza cuando el número de jugadores no coincide con los del escenario."""

    def __init__(self, current: int, scenario_players: int):
        super().__init__(
            f"El escenario requiere {scenario_players} jugadores "
            f"y se inscribieron {current}."
        )
        self.current = current
        self.scenario_players = scenario_players


class DuplicatePlayerError(GameInitializationError):
    """Se lanza cuando hay algún jugador (id o su id de Discord) duplicado."""

    def __init__(self, player_id: str | None, discord_id: int | None):
        message = ["Jugador duplicado."]
        if player_id:
            message.append(f"ID: {player_id}.")
        if discord_id:
            message.append(f"Discord: <@{discord_id}>.")
        super().__init__(" ".join(message))
        self.player_id = player_id
        self.discord_id = discord_id


class ScenarioNotSelectedError(GameInitializationError):
    """Se lanza cuando se intenta iniciar la partida sin escenario seleccionado."""

    def __init__(
        self,
        message: str = "Se requiere un escenario para iniciar la partida.",
    ):
        super().__init__(message)


class GameAlreadyStartedError(GameInitializationError):
    """Se lanza cuando se intenta inicializar una partida que ya está en marcha."""

    def __init__(
        self,
        message: str = "La partida ya está en curso.",
    ):
        super().__init__(message)


class TurnExecutionFailed(EngineError):
    """Se lanza cuando falla la ejecución de un turno o el setup inicial."""

    pass


class TooManyExpenses(EngineError):
    """Se lanza cuando un jugador intenta registrar más de cuatro gastos por campaña."""

    def __init__(
        self,
        message: str = "Solo se permiten hasta cuatro gastos por campaña",
    ) -> None:
        super().__init__(message)
        self.message = message
