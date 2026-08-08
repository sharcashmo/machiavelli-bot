# machiavelli/discord.py
import asyncio
import logging
import os
import traceback
from datetime import datetime

import discord
from discord import app_commands

from machiavelli.engine.exceptions import TooManyExpenses
from machiavelli.engine.military import (
    DislodgementResolverRequired,
    InvalidMilitaryState,
    MilitaryResolutionError,
    UnresolvedMilitaryConflict,
)
from machiavelli.events import InvalidTurnEventError
from machiavelli.game import (
    DuplicatedGameException,
    DuplicatePlayerException,
    GameNotFoundException,
    PlayerNotFoundException,
    TradeRuleException,
)
from machiavelli.game.scenario import Scenario
from machiavelli.game.tables import GameTables
from machiavelli.services import game_service_session

logger = logging.getLogger(__name__)

_INVALID_TURN_EVENT_MESSAGE = (
    "No se pudo generar el informe porque el historial del turno no es válido.\n"
    "Comunícaselo al administrador para que revise los eventos guardados."
)


def _log_invalid_turn_event(error: InvalidTurnEventError) -> None:
    """Log only the persisted row context needed for administrative diagnosis."""
    logger.error(
        "Historial de turno inválido",
        extra={"row_id": error.row_id, "event_type": error.event_type},
    )


# Estructura del documento (para orientarme)
# 1. Grupos de comandos
# 2. Inicializa los comandos
# 4. Comandos administrativos
# 5. Comandos de los jugadores


def format_error_with_location(e: Exception) -> str:
    """Extrae tipo, mensaje y localización de una excepción."""
    # Obtenemos la lista de marcos de la pila donde ocurrió la excepción
    tb_list = traceback.extract_tb(e.__traceback__)

    if tb_list:
        # Cogemos el último marco (donde saltó la excepción exactamente)
        last_frame = tb_list[-1]
        filename = os.path.basename(
            last_frame.filename
        )  # Solo el nombre del archivo (ej: discord.py)
        lineno = last_frame.lineno
        func_name = last_frame.name

        return (
            f"`{type(e).__name__}: {e}`\n"
            f"Ubicación: `{filename}:{lineno}` en `{func_name}()`"
        )

    return f"`{type(e).__name__}: {e}`"


class DatabaseGroup(app_commands.Group):
    """Application command group carrying its configured SQLite path."""

    db_path: str


# Grupo de comandos
game_group = DatabaseGroup(
    name="mach", description="Comandos de las partidas de Machiavelli"
)

# Grupo de administración
admin_group = DatabaseGroup(
    name="shar",
    description="Comandos de gestión interna para el Juez/Admin",
    default_permissions=discord.Permissions(administrator=True),
)

# Ruta por defecto
DB_PATH = os.getenv("DATABASE_PATH", "machiavelli.db")

game_group.db_path = DB_PATH
admin_group.db_path = DB_PATH


def init_game_commands(db_path: str) -> tuple[app_commands.Group, app_commands.Group]:
    """Configura la ruta de BBDD de ambos grupos de comandos."""
    game_group.db_path = db_path
    admin_group.db_path = db_path
    return game_group, admin_group


def _require_channel_id(interaction: discord.Interaction) -> int:
    """Return the guild channel identifier required by game commands."""
    channel_id = interaction.channel_id
    if channel_id is None:
        raise RuntimeError("Este comando solo puede ejecutarse dentro de un canal")
    return channel_id


def _create_game_record(db_path: str, name: str, channel_id: int) -> tuple[str, int]:
    """Create one game through the application-service boundary."""
    with game_service_session(db_path) as service:
        game = service.create_game(name=name, channel_id=channel_id)
        if game.database_id is None:
            raise RuntimeError("La partida creada no recibió un ID de persistencia")
        return game.name, game.database_id


def _add_player_record(
    db_path: str,
    channel_id: int,
    discord_id: int,
    player_id: str,
) -> tuple[str, list[tuple[str, int | None]]]:
    """Add one player and return the game name plus the authoritative roster."""
    with game_service_session(db_path) as service:
        game_name = service.get_game(channel_id).name
        players = service.add_player(channel_id, discord_id, player_id)
        return game_name, players


def _remove_player_record(
    db_path: str,
    channel_id: int,
    discord_id: int,
) -> tuple[str, str, list[tuple[str, int | None]]]:
    """Remove one player through the service and return the updated roster."""
    with game_service_session(db_path) as service:
        game_name = service.get_game(channel_id).name
        player_id, players = service.remove_player(channel_id, discord_id)
        return game_name, player_id, players


def _set_scenario_record(
    db_path: str,
    channel_id: int,
    scenario_id: str,
) -> tuple[str, str]:
    """Assign a scenario and return the game and scenario display names."""
    with game_service_session(db_path) as service:
        game_name = service.get_game(channel_id).name
        scenario_name = service.set_scenario(channel_id, scenario_id)
        return game_name, scenario_name


def _update_deadlines_record(
    db_path: str,
    channel_id: int,
    weekly_deadline: str | None,
    next_deadline: str | None,
) -> str:
    """Persist validated deadline strings through the service boundary."""
    with game_service_session(db_path) as service:
        return service.update_deadlines(
            channel_id,
            weekly_deadline=weekly_deadline,
            next_deadline=next_deadline,
        )


def _get_status_report(db_path: str, channel_id: int) -> tuple[str, ...]:
    """Load the public status report through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.get_status_report(channel_id))


def _get_turn_report(db_path: str, channel_id: int) -> tuple[str, ...]:
    """Load the latest turn report through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.get_turn_report(channel_id))


def _get_player_commands(
    db_path: str,
    channel_id: int,
    discord_id: int,
) -> tuple[str, tuple[str, ...]]:
    """Load one player's current command strings through the service boundary."""
    with game_service_session(db_path) as service:
        player_id, commands = service.get_player_commands(channel_id, discord_id)
        return player_id, tuple(commands)


def _get_available_actors(
    db_path: str,
    channel_id: int,
    discord_id: int,
    selected_power: str | None,
) -> tuple[tuple[str, str], ...]:
    """Load actor choices through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(
            service.get_available_actors(channel_id, discord_id, selected_power)
        )


def _get_available_commands(
    db_path: str,
    channel_id: int,
    discord_id: int,
    actor: str,
    selected_power: str | None,
) -> tuple[tuple[str, str], ...]:
    """Load command choices through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(
            service.get_available_commands(
                channel_id,
                discord_id,
                actor,
                selected_power,
            )
        )


def _get_available_targets(
    db_path: str,
    channel_id: int,
    discord_id: int,
    actor: str,
    command: str,
    selected_power: str | None,
) -> tuple[tuple[str, str], ...]:
    """Load target choices through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(
            service.get_available_targets(
                channel_id,
                discord_id,
                actor,
                command,
                selected_power,
            )
        )


def _get_available_expenses(
    db_path: str,
    channel_id: int,
    discord_id: int,
    selected_power: str | None,
) -> tuple[tuple[str, str], ...]:
    """Load expense choices through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(
            service.get_available_expenses(channel_id, discord_id, selected_power)
        )


def _get_expense_targets(
    db_path: str,
    channel_id: int,
    discord_id: int,
    expense: str,
    selected_power: str | None,
) -> tuple[tuple[str, str], ...]:
    """Load expense target choices through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(
            service.get_expense_targets(
                channel_id,
                discord_id,
                expense,
                selected_power,
            )
        )


def _get_expense_amounts(
    db_path: str,
    channel_id: int,
    discord_id: int,
    expense: str,
    target: str,
    selected_power: str | None,
) -> tuple[tuple[str, str], ...]:
    """Load expense amount choices through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(
            service.get_expense_amounts(
                channel_id,
                discord_id,
                expense,
                target,
                selected_power,
            )
        )


def _get_active_powers(db_path: str, channel_id: int) -> tuple[str, ...]:
    """Load assigned powers through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.get_active_powers(channel_id))


def _submit_command_record(
    db_path: str,
    channel_id: int,
    discord_id: int,
    actor: str,
    command: str,
    target: str | None,
    selected_power: str | None = None,
) -> tuple[str, ...]:
    """Validate and persist one order through the application service."""
    with game_service_session(db_path) as service:
        return tuple(
            service.submit_command(
                channel_id,
                discord_id,
                {"actor": actor, "command": command, "target": target},
                selected_power=selected_power,
            )
        )


def _submit_expense_record(
    db_path: str,
    channel_id: int,
    discord_id: int,
    expense: str,
    target: str,
    amount: str,
    selected_power: str | None = None,
) -> tuple[str, ...]:
    """Validate and persist one expense through the application service."""
    with game_service_session(db_path) as service:
        return tuple(
            service.submit_expense(
                channel_id,
                discord_id,
                expense=expense,
                target=target,
                amount=amount,
                selected_power=selected_power,
            )
        )


def _give_resource_record(
    db_path: str,
    channel_id: int,
    discord_id: int,
    give_to: str,
    give_type: str,
    give_value: str,
) -> str:
    """Validate and persist one direct transfer through the service boundary."""
    with game_service_session(db_path) as service:
        return service.give_resource(
            channel_id,
            discord_id,
            give_to=give_to,
            give_type=give_type,
            give_value=give_value,
        )


def _exchange_resources_record(
    db_path: str,
    channel_id: int,
    discord_id: int,
    give_to: str,
    give_type: str,
    give_value: str,
    receive_type: str,
    receive_value: str,
) -> str:
    """Validate and persist one exchange through the service boundary."""
    with game_service_session(db_path) as service:
        return service.exchange_resources(
            channel_id,
            discord_id,
            give_to=give_to,
            give_type=give_type,
            give_value=give_value,
            receive_type=receive_type,
            receive_value=receive_value,
        )


def _get_trade_counterparties(
    db_path: str,
    channel_id: int,
    discord_id: int,
) -> tuple[tuple[str, str], ...]:
    """Load direct-transfer counterparties through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.get_trade_counterparties(channel_id, discord_id))


def _get_trade_resource_types(
    db_path: str,
    channel_id: int,
) -> tuple[tuple[str, str], ...]:
    """Load enabled direct-transfer resource types through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.get_trade_resource_types(channel_id))


def _get_trade_assassin_targets(
    db_path: str,
    channel_id: int,
) -> tuple[tuple[str, str], ...]:
    """Load assassin targets through the service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.get_trade_assassin_targets(channel_id))


def _chunk_lines(lines: tuple[str, ...] | list[str], limit: int = 1950) -> list[str]:
    """Partition report lines without exceeding Discord's message limit."""
    chunks: list[str] = []
    current = ""

    for line in lines:
        segments = [line[index : index + limit] for index in range(0, len(line), limit)]
        if not segments:
            segments = [""]
        for segment in segments:
            candidate = f"{current}\n{segment}" if current else segment
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = segment

    if current:
        chunks.append(current)
    return chunks


# Comandos administrativos
@admin_group.command(name="create", description="Crea una nueva partida en este canal")
@app_commands.describe(name="Nombre de la partida")
async def create(interaction: discord.Interaction, name: str):
    # Deferimos la respuesta para evitar el timeout de 3 segundos de Discord
    await interaction.response.defer(ephemeral=False)

    try:
        game_name, database_id = await asyncio.to_thread(
            _create_game_record,
            admin_group.db_path,
            name,
            _require_channel_id(interaction),
        )
        await interaction.followup.send(
            f"**¡Partida Creada!**\nSe ha creado la partida *'{game_name}'* "
            f"en el canal <#{interaction.channel_id}>.\n"
            f"ID de registro: `{database_id}`. ¡Que comience la diplomacia!"
        )
    except DuplicatedGameException as error:
        await interaction.followup.send(f"Error al crear partida: {error}")


@admin_group.command(
    name="add_player", description="Añade un jugador a la partida de este canal"
)
@app_commands.describe(
    discord_player="El usuario de Discord que vas a registrar",
    name="El nombre político o ID interno del jugador (ej: 'Francia' o 'Carlos')",
)
async def add_player(
    interaction: discord.Interaction, discord_player: discord.Member, name: str
):
    # Deferimos la respuesta para evitar el timeout de 3 segundos
    await interaction.response.defer(ephemeral=False)

    try:
        game_name, players = await asyncio.to_thread(
            _add_player_record,
            admin_group.db_path,
            _require_channel_id(interaction),
            discord_player.id,
            name,
        )
        report = [
            f"- {player_id} "
            f"{f'<@{discord_id}>' if discord_id is not None else 'Sin usuario'}"
            for player_id, discord_id in players
        ]
        formatted_output = "\n".join(report)
        await interaction.followup.send(
            f"El jugador **'{name}'** (<@{discord_player.id}>) se ha unido "
            f"con éxito a la partida *'{game_name}'*.\n\n"
            f"Jugadores inscritos hasta ahora:\n{formatted_output}"
        )
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.\n"
            "Crea una primero usando `/shar create`."
        )
    except DuplicatePlayerException:
        await interaction.followup.send(
            f"**Error:** El usuario {discord_player.mention} o el nombre "
            f"**'{name}'** ya está inscrito en esta partida."
        )
    except Exception as error:
        await interaction.followup.send(
            f"**Error inesperado:** `{type(error).__name__}: {error}`"
        )


@admin_group.command(
    name="remove_player", description="Elimina a un jugador de la partida de este canal"
)
@app_commands.describe(discord_user="El usuario de Discord que deseas eliminar")
async def remove_player(interaction: discord.Interaction, discord_user: discord.Member):
    await interaction.response.defer(ephemeral=False)

    try:
        game_name, player_id, players = await asyncio.to_thread(
            _remove_player_record,
            admin_group.db_path,
            _require_channel_id(interaction),
            discord_user.id,
        )
        if players:
            new_list = "\n".join(
                f"- {remaining_id} "
                f"({f'<@{discord_id}>' if discord_id is not None else 'Sin usuario'})"
                for remaining_id, discord_id in players
            )
        else:
            new_list = "*No quedan jugadores inscritos en la partida.*"
        await interaction.followup.send(
            f"El jugador **'{player_id}'** ({discord_user.mention}) "
            f"ha sido eliminado con éxito de la partida *'{game_name}'*.\n\n"
            f"**Jugadores inscritos ahora:**\n{new_list}"
        )
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal."
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            f"**Error:** El usuario {discord_user.mention} no está inscrito "
            "en la partida de este canal."
        )
    except Exception as error:
        await interaction.followup.send(
            f"**Error inesperado:** `{type(error).__name__}: {error}`"
        )


@admin_group.command(
    name="set_scenario", description="Asigna un escenario a la partida de este canal"
)
@app_commands.describe(
    scenario_id="Elige uno de los escenarios disponibles en la lista"
)
async def set_scenario(interaction: discord.Interaction, scenario_id: str):
    await interaction.response.defer(ephemeral=False)

    try:
        game_name, scenario_name = await asyncio.to_thread(
            _set_scenario_record,
            admin_group.db_path,
            _require_channel_id(interaction),
            scenario_id,
        )
        await interaction.followup.send(
            f"**¡Escenario Configurado!**\nLa partida *'{game_name}'* "
            f"jugará al escenario: **{scenario_name}**."
        )
    except ValueError:
        await interaction.followup.send(
            "**Error:** El escenario seleccionado no es válido."
        )
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal."
        )
    except Exception as error:
        await interaction.followup.send(
            f"**Error inesperado:** `{type(error).__name__}: {error}`"
        )


# Precarga de la lista de escenarios
@set_scenario.autocomplete("scenario_id")
async def set_scenario_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Genera sugerencias mientras el usuario escribe."""

    # Cargamos tu diccionario {str: Scenario}
    escenarios_disponibles = Scenario.load_scenarios()

    choices = []
    for s_id, scenario in escenarios_disponibles.items():
        # Filtramos por lo que el usuario esté escribiendo (ignorando mayúsculas)
        # Si no está escribiendo nada (current == ""), mostrará todos
        if current.lower() in scenario.name.lower() or current.lower() in s_id.lower():
            choices.append(
                app_commands.Choice(
                    name=scenario.name,  # Lo que ve el usuario en Discord
                    value=s_id,  # El código de escenario
                )
            )

    # Discord capa el Autocomplete a un máximo de 25 opciones en pantalla
    return choices[:25]


@admin_group.command(
    name="set_deadlines",
    description="Configura el horario semanal y el próximo deadline",
)
@app_commands.describe(
    dia_semanal="El día de la semana en que se procesarán los turnos de forma habitual",
    hora_semanal="La hora del deadline semanal (Formato HH:MM, ej: 22:00)",
    proximo_deadline=(
        "Fecha exacta del siguiente turno "
        "(Formato: DD/MM/AAAA HH:MM, ej: 22/07/2026 22:00)"
    ),
)
# Creamos un desplegable cerrado para los días de la semana
@app_commands.choices(
    dia_semanal=[
        app_commands.Choice(name="Lunes", value="Lunes"),
        app_commands.Choice(name="Martes", value="Martes"),
        app_commands.Choice(name="Miércoles", value="Miércoles"),
        app_commands.Choice(name="Jueves", value="Jueves"),
        app_commands.Choice(name="Viernes", value="Viernes"),
        app_commands.Choice(name="Sábado", value="Sábado"),
        app_commands.Choice(name="Domingo", value="Domingo"),
    ]
)
async def set_deadlines(
    interaction: discord.Interaction,
    dia_semanal: app_commands.Choice[str] | None = None,
    hora_semanal: str | None = None,
    proximo_deadline: str | None = None,
):
    await interaction.response.defer(ephemeral=False)

    try:
        changes: list[str] = []
        weekly_deadline: str | None = None
        next_deadline: str | None = None

        if dia_semanal or hora_semanal:
            if not (dia_semanal and hora_semanal):
                await interaction.followup.send(
                    "**Error:** Para fijar el horario semanal debes indicar "
                    "tanto el día como la hora."
                )
                return
            try:
                datetime.strptime(hora_semanal, "%H:%M")
            except ValueError:
                await interaction.followup.send(
                    "**Error:** La hora semanal debe tener el formato "
                    "`HH:MM` (ej: `22:00` o `09:30`)."
                )
                return
            weekly_deadline = f"{dia_semanal.value} a las {hora_semanal}"
            changes.append(f"**Horario semanal:** {weekly_deadline}")

        if proximo_deadline:
            try:
                parsed_deadline = datetime.strptime(
                    proximo_deadline,
                    "%d/%m/%Y %H:%M",
                )
            except ValueError:
                await interaction.followup.send(
                    "**Error:** El formato del próximo deadline es incorrecto.\n"
                    "Debe ser estrictamente: `DD/MM/AAAA HH:MM` "
                    "(ej: `22/07/2026 22:00`)."
                )
                return
            next_deadline = parsed_deadline.strftime("%Y-%m-%d %H:%M")
            readable_deadline = parsed_deadline.strftime("%A, %d de %B a las %H:%M")
            changes.append(f"**Próximo Deadline:** `{readable_deadline}`")

        if not changes:
            await interaction.followup.send(
                "No has introducido ningún parámetro para modificar."
            )
            return

        game_name = await asyncio.to_thread(
            _update_deadlines_record,
            admin_group.db_path,
            _require_channel_id(interaction),
            weekly_deadline,
            next_deadline,
        )
        summary = "\n".join(changes)
        await interaction.followup.send(
            f"**¡Plazos Actualizados!**\nSe han guardado los nuevos plazos "
            f"para la partida *'{game_name}'*:\n{summary}"
        )
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal."
        )
    except Exception as error:
        await interaction.followup.send(
            f"**Error inesperado:** `{type(error).__name__}: {error}`"
        )


def _execute_game_turn(db_path: str, channel_id: int) -> tuple[str, ...]:
    """Execute and persist one turn through the application-service boundary."""
    with game_service_session(db_path) as service:
        return tuple(service.run_turn(channel_id))


def _military_error_message(error: MilitaryResolutionError) -> str:
    """Traduce errores militares a orientación pública sin detalles internos."""
    prefix = "No se pudo resolver la fase militar; no se aplicó ningún cambio."
    if isinstance(error, InvalidMilitaryState):
        guidance = (
            " Revisa que no haya unidades duplicadas ni ocupaciones incompatibles "
            "antes de reintentar."
        )
    elif isinstance(error, UnresolvedMilitaryConflict):
        guidance = (
            " Revisa las órdenes y, si el problema se repite con las mismas entradas, "
            "comunícalo al administrador."
        )
    elif isinstance(error, DislodgementResolverRequired):
        guidance = " Activa la gestión de retiradas antes de reintentar."
    else:
        guidance = " Reintenta el turno y comunica el fallo si persiste."
    return prefix + guidance


@admin_group.command(
    name="run_game", description="Ejecuta y procesa el turno actual de la partida"
)
async def run_game(interaction: discord.Interaction):
    """Ejecuta un turno fuera del event loop y publica solo el resultado seguro."""
    await interaction.response.defer(ephemeral=True)

    try:
        # SQLite, motor y guardado se ejecutan juntos para no bloquear Discord.
        report = await asyncio.to_thread(
            _execute_game_turn,
            admin_group.db_path,
            _require_channel_id(interaction),
        )
    except GameNotFoundException:
        await interaction.edit_original_response(
            content=(
                "**Error:** No hay ninguna partida activa en este canal para poder "
                "ejecutarla."
            )
        )
        return
    except InvalidTurnEventError as error:
        _log_invalid_turn_event(error)
        await interaction.edit_original_response(content=_INVALID_TURN_EVENT_MESSAGE)
        return
    except MilitaryResolutionError as error:
        # El diagnóstico completo queda en logs; el usuario recibe orientación segura.
        logger.exception(
            "La fase militar abortó sin commit",
            extra={"cycle_diagnostic": getattr(error, "diagnostic", None)},
        )
        await interaction.edit_original_response(content=_military_error_message(error))
        return
    except Exception:
        logger.exception("Error inesperado al ejecutar el turno")
        await interaction.edit_original_response(
            content=(
                "**Error inesperado al ejecutar el turno:** No se pudo completar "
                "la operación. Comunícaselo al administrador."
            )
        )
        return

    # Al completar el turno, la respuesta efímera se sustituye por el informe público.
    await interaction.delete_original_response()
    if not report:
        await interaction.followup.send(
            "El turno se ha procesado, pero no se ha generado ninguna "
            "línea de reporte.",
            ephemeral=False,
        )
        return

    for message in _chunk_lines(report):
        await interaction.followup.send(message, ephemeral=False)


# 5. Comandos de los jugadores


@game_group.command(
    name="game_status",
    description="Muestra el estado actual de la partida en este canal",
)
async def game_status(interaction: discord.Interaction):
    # La lectura y preparación del estado puede tardar.
    await interaction.response.defer(ephemeral=False)

    try:
        report = await asyncio.to_thread(
            _get_status_report,
            game_group.db_path,
            _require_channel_id(interaction),
        )
        messages = _chunk_lines(report) or ["No hay datos de estado disponibles."]
        for message in messages:
            await interaction.followup.send(message, ephemeral=False)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.\n"
            "Crea una primero usando `/shar create`."
        )
    except Exception as error:
        await interaction.followup.send(
            f"**Error inesperado:** `{type(error).__name__}: {error}`"
        )


@game_group.command(
    name="game_report", description="Muestra el informe del último turno jugado"
)
async def game_report(interaction: discord.Interaction):
    # La lectura y preparación del informe puede tardar.
    await interaction.response.defer(ephemeral=True)

    try:
        report = await asyncio.to_thread(
            _get_turn_report,
            game_group.db_path,
            _require_channel_id(interaction),
        )
        messages = _chunk_lines(report) or ["No hay datos del último turno."]
        for message in messages:
            await interaction.followup.send(message, ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.",
            ephemeral=True,
        )
    except InvalidTurnEventError as error:
        _log_invalid_turn_event(error)
        await interaction.followup.send(
            _INVALID_TURN_EVENT_MESSAGE,
            ephemeral=True,
        )
    except Exception:
        logger.exception("Error inesperado al mostrar el informe")
        await interaction.followup.send(
            "**Error inesperado al mostrar el informe:** No se pudo completar la "
            "operación. Comunícaselo al administrador.",
            ephemeral=True,
        )


@game_group.command(
    name="cmdlist", description="Muestra la lista de tus órdenes registradas"
)
async def cmdlist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        player_id, commands = await asyncio.to_thread(
            _get_player_commands,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
        )
        if not commands:
            await interaction.followup.send(
                f"**No hay comandos para {player_id}:**",
                ephemeral=True,
            )
            return
        lines = "\n".join(
            f"**{index}.** `{command}`"
            for index, command in enumerate(commands, start=1)
        )
        await interaction.followup.send(
            f"**Comandos actuales para {player_id}:**\n{lines}",
            ephemeral=True,
        )
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.",
            ephemeral=True,
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            "**Error:** No estás inscrito en la partida de este canal.",
            ephemeral=True,
        )
    except Exception as error:
        error_message = format_error_with_location(error)
        await interaction.followup.send(
            f"**Error inesperado:** `{error_message}`",
            ephemeral=True,
        )


# ==============================================================================
# send commands
# ==============================================================================

# first, autocomplete


def _selected_power(interaction: discord.Interaction) -> str | None:
    """Return the optional administrative power selected in an interaction."""
    namespace = getattr(interaction, "namespace", None)
    return getattr(namespace, "power", None)


async def cmd_actor_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Actores disponibles para el jugador actual."""
    try:
        actors = await asyncio.to_thread(
            _get_available_actors,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            _selected_power(interaction),
        )
        choices = [
            app_commands.Choice(name=label, value=code)
            for code, label in actors
            if current.casefold() in label.casefold()
            or current.casefold() in code.casefold()
        ]
        return choices[:25]
    except Exception:
        return []


async def cmd_command_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Sugiere las órdenes válidas según el actor seleccionado previamente."""
    # Leemos el valor que el usuario ha seleccionado/escrito en el campo 'actor'
    actor = getattr(interaction.namespace, "actor", None)

    if not actor:
        return [app_commands.Choice(name="Selecciona primero un actor", value="")]

    try:
        commands = await asyncio.to_thread(
            _get_available_commands,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            actor,
            _selected_power(interaction),
        )
        choices = [
            app_commands.Choice(name=label, value=code)
            for code, label in commands
            if current.casefold() in label.casefold()
            or current.casefold() in code.casefold()
        ]
        return choices[:25]
    except Exception:
        return []


async def cmd_target_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Sugiere los objetivos válidos según el actor y el comando seleccionados."""
    actor = getattr(interaction.namespace, "actor", None)
    command = getattr(interaction.namespace, "command", None)

    if not actor or not command:
        return [
            app_commands.Choice(name="Selecciona primero actor y comando", value="")
        ]

    try:
        targets = await asyncio.to_thread(
            _get_available_targets,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            actor,
            command,
            _selected_power(interaction),
        )
        choices = [
            app_commands.Choice(name=label, value=code)
            for code, label in targets
            if current.casefold() in label.casefold()
            or current.casefold() in code.casefold()
        ]
        return choices[:25]
    except Exception:
        return []


async def exp_expense_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Gastos disponibles para el jugador actual."""
    try:
        expenses = await asyncio.to_thread(
            _get_available_expenses,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            _selected_power(interaction),
        )
        choices = [
            app_commands.Choice(name=label, value=code)
            for code, label in expenses
            if current.casefold() in label.casefold()
            or current.casefold() in code.casefold()
        ]
        return choices[:25]
    except Exception:
        return []


async def exp_target_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Sugiere los objetivos disponibles para el gasto seleccionado previamente."""
    # Leemos el valor que el usuario ha seleccionado/escrito en el campo 'expense'
    expense = getattr(interaction.namespace, "expense", None)

    if not expense:
        return [app_commands.Choice(name="Selecciona primero un gasto", value="")]

    try:
        targets = await asyncio.to_thread(
            _get_expense_targets,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            expense,
            _selected_power(interaction),
        )
        choices = [
            app_commands.Choice(name=label, value=code)
            for code, label in targets
            if current.casefold() in label.casefold()
            or current.casefold() in code.casefold()
        ]
        choices.sort(key=lambda choice: choice.name)
        return choices[:25]
    except Exception:
        return []


async def exp_amount_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Sugiere los objetivos válidos según el gasto y objetivo seleccionados."""
    expense = getattr(interaction.namespace, "expense", None)
    target = getattr(interaction.namespace, "target", None)

    if not expense or not target:
        return [
            app_commands.Choice(name="Selecciona primero gasto y objetivo", value="")
        ]

    try:
        amounts = await asyncio.to_thread(
            _get_expense_amounts,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            expense,
            target,
            _selected_power(interaction),
        )
        choices = [
            app_commands.Choice(name=label, value=code)
            for code, label in amounts
            if current.casefold() in label.casefold()
            or current.casefold() in code.casefold()
        ]
        return choices[:25]
    except Exception:
        return []


# ============================================================================
# COMANDOS DE TRADING
# ============================================================================


async def trade_give_to_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest assigned counterparties for a direct transfer."""
    try:
        counterparties = await asyncio.to_thread(
            _get_trade_counterparties,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
        )
        current_folded = current.casefold()
        return [
            app_commands.Choice(name=label, value=code)
            for code, label in counterparties
            if current_folded in label.casefold() or current_folded in code.casefold()
        ][:25]
    except Exception:
        return []


async def trade_give_type_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest resource types enabled by the current scenario."""
    try:
        resource_types = await asyncio.to_thread(
            _get_trade_resource_types,
            game_group.db_path,
            _require_channel_id(interaction),
        )
        current_folded = current.casefold()
        return [
            app_commands.Choice(name=label, value=code)
            for code, label in resource_types
            if current_folded in label.casefold() or current_folded in code.casefold()
        ][:25]
    except Exception:
        return []


async def trade_give_value_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest scenario powers when the direct transfer is an assassination."""
    if getattr(interaction.namespace, "give_type", None) != "assassin":
        return []

    try:
        targets = await asyncio.to_thread(
            _get_trade_assassin_targets,
            game_group.db_path,
            _require_channel_id(interaction),
        )
        current_folded = current.casefold()
        return [
            app_commands.Choice(name=label, value=code)
            for code, label in targets
            if current_folded in label.casefold() or current_folded in code.casefold()
        ][:25]
    except Exception:
        return []


async def _trade_exchange_value_autocomplete(
    interaction: discord.Interaction,
    current: str,
    resource_attribute: str,
) -> list[app_commands.Choice[str]]:
    """Suggest cancellation and scenario targets for one exchange value."""
    try:
        cancel = app_commands.Choice(name="0 — Cancelar intercambio", value="0")
        resource_type = getattr(
            getattr(interaction, "namespace", None), resource_attribute, None
        )
        if not isinstance(resource_type, str):
            return []

        current_folded = current.casefold()

        def matches(choice: app_commands.Choice[str]) -> bool:
            return (
                current_folded in choice.name.casefold()
                or current_folded in choice.value.casefold()
            )

        if resource_type != "assassin":
            return [cancel] if matches(cancel) else []

        targets = await asyncio.to_thread(
            _get_trade_assassin_targets,
            game_group.db_path,
            _require_channel_id(interaction),
        )
        choices = [cancel] + [
            app_commands.Choice(name=label, value=code) for code, label in targets
        ]
        return [choice for choice in choices if matches(choice)][:25]
    except Exception:
        return []


async def trade_exchange_give_value_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest exchange cancellation and offered assassin targets."""
    return await _trade_exchange_value_autocomplete(interaction, current, "give_type")


async def trade_exchange_receive_value_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest exchange cancellation and requested assassin targets."""
    return await _trade_exchange_value_autocomplete(
        interaction, current, "receive_type"
    )


# ==============================================================================
# COMANDO /mach cmd
# ==============================================================================


@game_group.command(
    name="give", description="Da ducados o una ficha de asesinato a otra facción."
)
@app_commands.describe(
    give_to="Facción que recibirá el recurso",
    give_type="Recurso que quieres dar",
    give_value="Cantidad de ducados o facción objetivo de la ficha",
)
@app_commands.autocomplete(
    give_to=trade_give_to_autocomplete,
    give_type=trade_give_type_autocomplete,
    give_value=trade_give_value_autocomplete,
)
async def give(
    interaction: discord.Interaction,
    give_to: str,
    give_type: str,
    give_value: str,
):
    await interaction.response.defer(ephemeral=True)
    channel_id = _require_channel_id(interaction)

    try:
        result = await asyncio.to_thread(
            _give_resource_record,
            game_group.db_path,
            channel_id,
            interaction.user.id,
            give_to,
            give_type,
            give_value,
        )
        await interaction.followup.send(result, ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.",
            ephemeral=True,
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            "**Error:** No se identificó al jugador o no tiene una facción asignada.",
            ephemeral=True,
        )
    except TradeRuleException as error:
        await interaction.followup.send(f"**Error:** {error}", ephemeral=True)
    except Exception:
        logger.exception(
            "Fallo inesperado en /mach give",
            extra={"operation": "trade_give", "channel_id": channel_id},
        )
        await interaction.followup.send(
            "**Error inesperado:** No se pudo completar la operación. "
            "Inténtalo de nuevo.",
            ephemeral=True,
        )


@game_group.command(
    name="exchange",
    description="Propón, cancela o completa un intercambio con otra facción.",
)
@app_commands.describe(
    give_to="Facción con la que quieres intercambiar",
    give_type="Recurso que ofreces",
    give_value="Cantidad u objetivo que ofreces; 0 cancela",
    receive_type="Recurso que solicitas",
    receive_value="Cantidad u objetivo que solicitas; 0 cancela",
)
@app_commands.autocomplete(
    give_to=trade_give_to_autocomplete,
    give_type=trade_give_type_autocomplete,
    give_value=trade_exchange_give_value_autocomplete,
    receive_type=trade_give_type_autocomplete,
    receive_value=trade_exchange_receive_value_autocomplete,
)
async def exchange(
    interaction: discord.Interaction,
    give_to: str,
    give_type: str,
    give_value: str,
    receive_type: str,
    receive_value: str,
):
    await interaction.response.defer(ephemeral=True)
    channel_id = _require_channel_id(interaction)

    try:
        result = await asyncio.to_thread(
            _exchange_resources_record,
            game_group.db_path,
            channel_id,
            interaction.user.id,
            give_to,
            give_type,
            give_value,
            receive_type,
            receive_value,
        )
        await interaction.followup.send(result, ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.",
            ephemeral=True,
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            "**Error:** No se identificó al jugador o no tiene una facción asignada.",
            ephemeral=True,
        )
    except TradeRuleException as error:
        await interaction.followup.send(f"**Error:** {error}", ephemeral=True)
    except Exception:
        logger.exception(
            "Fallo inesperado en /mach exchange",
            extra={"operation": "exchange", "channel_id": channel_id},
        )
        await interaction.followup.send(
            "**Error inesperado:** No se pudo completar la operación. "
            "Inténtalo de nuevo.",
            ephemeral=True,
        )


@game_group.command(
    name="cmd", description="Registra una nueva orden para tus unidades"
)
@app_commands.describe(
    actor="Unidad o recurso que ejecutará la acción",
    command="Acción u orden a realizar",
    target="Objetivo de la orden (Provincia, ciudad, unidad, facción, etc)",
)
@app_commands.autocomplete(
    actor=cmd_actor_autocomplete,
    command=cmd_command_autocomplete,
    target=cmd_target_autocomplete,
)
async def cmd(
    interaction: discord.Interaction,
    actor: str,
    command: str,
    target: str | None = None,
):
    await interaction.response.defer(ephemeral=True)

    try:
        lines = await asyncio.to_thread(
            _submit_command_record,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            actor,
            command,
            target,
        )
        await interaction.followup.send("\n".join(lines), ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.", ephemeral=True
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            "**Error:** No se identificó al jugador.", ephemeral=True
        )
    except ValueError as error:
        await interaction.followup.send(f"**Error:** {error}", ephemeral=True)
    except Exception as error:
        detailed_error = format_error_with_location(error)
        await interaction.followup.send(
            f"**Error inesperado:** {detailed_error}", ephemeral=True
        )


# ==============================================================================
# COMANDO /shar cmd_user
# ==============================================================================
async def cmd_power_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Sugiere las potencias/jugadores presentes en la partida actual."""
    try:
        active_powers = set(
            await asyncio.to_thread(
                _get_active_powers,
                admin_group.db_path,
                _require_channel_id(interaction),
            )
        )
        choices = [
            app_commands.Choice(name=name, value=code)
            for code, name in GameTables.powers.items()
            if code in active_powers
            and (
                current.casefold() in name.casefold()
                or current.casefold() in code.casefold()
            )
        ]
        return choices[:25]
    except Exception:
        return []


@admin_group.command(
    name="cmd_user", description="Registra una orden en nombre de un jugador"
)
@app_commands.describe(
    power="Código de la potencia/jugador a quien pertenece la orden",
    actor="Unidad o recurso que ejecutará la acción",
    command="Acción u orden a realizar",
    target="Objetivo de la orden (Provincia, ciudad, unidad, facción, etc)",
)
@app_commands.autocomplete(
    power=cmd_power_autocomplete,
    actor=cmd_actor_autocomplete,
    command=cmd_command_autocomplete,
    target=cmd_target_autocomplete,
)
async def cmd_user(
    interaction: discord.Interaction,
    power: str,
    actor: str,
    command: str,
    target: str | None = None,
):
    await interaction.response.defer(ephemeral=True)

    try:
        lines = await asyncio.to_thread(
            _submit_command_record,
            admin_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            actor,
            command,
            target,
            power,
        )
        await interaction.followup.send("\n".join(lines), ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.", ephemeral=True
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            f"**Error:** No se encontró la potencia `{power}` en la partida.",
            ephemeral=True,
        )
    except ValueError as error:
        await interaction.followup.send(f"**Error:** {error}", ephemeral=True)
    except Exception as error:
        detailed_error = format_error_with_location(error)
        await interaction.followup.send(
            f"**Error inesperado:** {detailed_error}", ephemeral=True
        )


# ==============================================================================
# COMANDO /mach expense
# ==============================================================================


@game_group.command(name="expense", description="Registra un nuevo gasto")
@app_commands.describe(
    expense="Tipo de gasto a realizar",
    target="Objetivo del gasto (Provincia, ciudad, unidad, facción, etc)",
    amount="Cantidad destinada al gasto",
)
@app_commands.autocomplete(
    expense=exp_expense_autocomplete,
    target=exp_target_autocomplete,
    amount=exp_amount_autocomplete,
)
async def expense(
    interaction: discord.Interaction, expense: str, target: str, amount: str
):
    await interaction.response.defer(ephemeral=True)

    try:
        lines = await asyncio.to_thread(
            _submit_expense_record,
            game_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            expense,
            target,
            amount,
        )
        await interaction.followup.send("\n".join(lines), ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.", ephemeral=True
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            "**Error:** No se identificó al jugador.", ephemeral=True
        )
    except TooManyExpenses:
        await interaction.followup.send(
            "**Error:** Superado el límite de gastos. La orden no se ha guardado.",
            ephemeral=True,
        )
    except ValueError as error:
        await interaction.followup.send(f"**Error:** {error}", ephemeral=True)
    except Exception as error:
        detailed_error = format_error_with_location(error)
        await interaction.followup.send(
            f"**Error inesperado:** {detailed_error}", ephemeral=True
        )


@admin_group.command(
    name="expense_user", description="Registra un gasto en nombre de un jugador"
)
@app_commands.describe(
    power="Código de la potencia/jugador a quien pertenece el gasto",
    expense="Tipo de gasto a realizar",
    target="Objetivo del gasto (Provincia, ciudad, unidad, facción, etc)",
    amount="Cantidad destinada al gasto",
)
@app_commands.autocomplete(
    power=cmd_power_autocomplete,
    expense=exp_expense_autocomplete,
    target=exp_target_autocomplete,
    amount=exp_amount_autocomplete,
)
async def expense_user(
    interaction: discord.Interaction, power: str, expense: str, target: str, amount: str
):
    await interaction.response.defer(ephemeral=True)

    try:
        lines = await asyncio.to_thread(
            _submit_expense_record,
            admin_group.db_path,
            _require_channel_id(interaction),
            interaction.user.id,
            expense,
            target,
            amount,
            power,
        )
        await interaction.followup.send("\n".join(lines), ephemeral=True)
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.", ephemeral=True
        )
    except PlayerNotFoundException:
        await interaction.followup.send(
            f"**Error:** No se encontró la potencia `{power}` en la partida.",
            ephemeral=True,
        )
    except TooManyExpenses:
        await interaction.followup.send(
            "**Error:** Superado el límite de gastos. La orden no se ha guardado.",
            ephemeral=True,
        )
    except ValueError as error:
        await interaction.followup.send(f"**Error:** {error}", ephemeral=True)
    except Exception as error:
        detailed_error = format_error_with_location(error)
        await interaction.followup.send(
            f"**Error inesperado:** {detailed_error}", ephemeral=True
        )
