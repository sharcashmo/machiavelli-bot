# machiavelli/discord.py
import os
import sqlite3
import discord
from discord import app_commands
from datetime import datetime

from machiavelli.game import Game, Player, DuplicatedGameException, GameNotFoundException
from machiavelli.scenario import Scenario

# Grupo de comandos
game_group = app_commands.Group(
    name="sharcashvelli", 
    description="Comandos de las partidas de Machiavelli"
)

# Grupo de administración
admin_group = app_commands.Group(
    name="sharcashvelli_admin", 
    description="Comandos de gestión interna para el Juez/Admin",
    default_permissions=discord.Permissions(administrator=True)
)

# Ruta por defecto
DB_PATH = os.getenv("DATABASE_PATH", "machiavelli.db")

game_group.db_path = DB_PATH
admin_group.db_path = DB_PATH


def init_game_commands(db_path: str) -> app_commands.Group:
    """Configura dinámicamente la ruta de la BBDD en el grupo de comandos y lo devuelve."""
    game_group.db_path = db_path
    admin_group.db_path = db_path
    return game_group, admin_group


@admin_group.command(
    name="create", description="Crea una nueva partida en este canal")
@app_commands.describe(name="Nombre de la partida")
async def create(interaction: discord.Interaction, name: str):
    # Deferimos la respuesta para evitar el timeout de 3 segundos de Discord
    await interaction.response.defer(ephemeral=False)
    
    try:
        # Accedemos de forma segura a la propiedad del grupo.
        with sqlite3.connect(admin_group.db_path) as conn:
            game = Game.create_game(
                name=name, 
                channel_id=interaction.channel_id, 
                conn=conn
            )
            
        await interaction.followup.send(
            f"**¡Partida Creada!**\n"
            f"Se ha creado la partida *'{game.name}'* en el canal <#{interaction.channel_id}>.\n"
            f"ID de registro: `{game.database_id}`. ¡Que comience la diplomacia!"
        )
        
    except DuplicatedGameException as e:
        await interaction.followup.send(f"Error al crear partida: {e}")

@admin_group.command(
    name="add_player", description="Añade un jugador a la partida de este canal")
@app_commands.describe(
    discord_player="El usuario de Discord que vas a registrar",
    name="El nombre político o ID interno del jugador (ej: 'Francia' o 'Carlos')"
)
async def add_player(interaction: discord.Interaction, discord_player: discord.Member, name: str):
    # Deferimos la respuesta para evitar el timeout de 3 segundos
    await interaction.response.defer(ephemeral=False)
    
    try:
        with sqlite3.connect(admin_group.db_path) as conn:
            # Carga el objeto Game utilizando el channel_id actual
            game = Game.load_game(conn, channel_id=interaction.channel_id)

            if any(p.discord_id == discord_player.id for p in game.players):
                await interaction.followup.send(
                    f"**Error:** El usuario {discord_player.mention} ya está inscrito en esta partida."
                )
                return
            
            # Crea el Player usando el nombre como player_id y el usuario como discord_id
            new_player = Player(player_id=name, discord_id=discord_player.id)
            
            # Lo añade a la lista de la partida en memoria
            game.players.append(new_player)
            
            # Hace el save estrictamente del Player usando el ID de la partida cargada
            new_player.save(conn, game.database_id)
            
        # Confirmación
        report = []
        for p in game.players:
            # Añadimos un fallback por si algún jugador antiguo no tuviera discord_id
            mention = f"<@{p.discord_id}>" if p.discord_id else "Sin usuario"
            report.append(f"- {p.player_id} {mention}")
            
        # Unimos todas las líneas con saltos de línea
        formatted_output = "\n".join(report)

        await interaction.followup.send(
            f"El jugador **'{name}'** (<@{discord_player.id}>) se ha unido con éxito a la partida *'{game.name}'*.\n\n"
            f"Jugadores inscritos hasta ahora:\n{formatted_output}"
        )
        
    except GameNotFoundException:
        # Si no hay partida en este canal, avisamos limpiamente
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.\n"
            "Crea una primero usando `/sharcashvelli_admin create`."
        )
        
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado:** `{type(e).__name__}: {e}`")

@admin_group.command(name="remove_player", description="Elimina a un jugador de la partida de este canal")
@app_commands.describe(discord_user="El usuario de Discord que deseas eliminar")
async def remove_player(interaction: discord.Interaction, discord_user: discord.Member):
    await interaction.response.defer(ephemeral=False)
    
    try:
        with sqlite3.connect(admin_group.db_path) as conn:
            game = Game.load_game(conn, channel_id=interaction.channel_id)
            
            player = next((p for p in game.players if p.discord_id == discord_user.id), None)
            
            if not player:
                await interaction.followup.send(
                    f"**Error:** El usuario {discord_user.mention} no está inscrito en la partida *'{game.name}'*."
                )
                return

            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM players WHERE game_id = ? AND discord_id = ?",
                (game.database_id, discord_user.id)
            )
            
            game.players.remove(player)
            
        if game.players:
            new_list = "\n".join([f"- {p.player_id} (<@{p.discord_id}>)" for p in game.players])
        else:
            new_list = "*No quedan jugadores inscritos en la partida.*"
            
        await interaction.followup.send(
            f"El jugador **'{player.player_id}'** ({discord_user.mention}) "
            f"ha sido eliminado con éxito de la partida *'{game.name}'*.\n\n"
            f"**Jugadores inscritos ahora:**\n{new_list}"
        )
        
    except GameNotFoundException:
        await interaction.followup.send("**Error:** No hay ninguna partida activa en este canal.")
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado:** `{type(e).__name__}: {e}`")

@admin_group.command(
    name="set_scenario", description="Asigna un escenario a la partida de este canal")
@app_commands.describe(scenario_id="Elige uno de los escenarios disponibles en la lista")
async def set_scenario(interaction: discord.Interaction, scenario_id: str):
    await interaction.response.defer(ephemeral=False)
    
    try:
        # Cargamos los escenarios para poder sacar el nombre real en la confirmación
        escenarios_disponibles = Scenario.load_scenarios()
        
        if scenario_id not in escenarios_disponibles:
            await interaction.followup.send("**Error:** El escenario seleccionado no es válido.")
            return

        escenario_elegido = escenarios_disponibles[scenario_id]

        with sqlite3.connect(admin_group.db_path) as conn:
            # Cargamos la partida actual por el canal
            game = Game.load_game(conn, channel_id=interaction.channel_id)
            
            # Le asignamos el ID del escenario elegido
            game.scenario_id = scenario_id
            
            # Guardamos la partida (como ya tiene ID, ejecutará el UPDATE)
            game.save(conn)
            
        await interaction.followup.send(
            f"**¡Escenario Configurado!**\n"
            f"La partida *'{game.name}'* jugará al escenario: **{escenario_elegido.name}**."
        )
        
    except GameNotFoundException:
        await interaction.followup.send("**Error:** No hay ninguna partida activa en este canal.")
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado:** `{type(e).__name__}: {e}`")


# LA FUNCIÓN DE AUTOCOMPLETE
@set_scenario.autocomplete("scenario_id")
async def set_scenario_autocomplete(
    interaction: discord.Interaction, 
    current: str
) -> list[app_commands.Choice[str]]:
    """Genera dinámicamente la lista de sugerencias mientras el usuario escribe."""
    
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
                    value=s_id           # El código de escenario
                )
            )
            
    # Discord capa el Autocomplete a un máximo de 25 opciones en pantalla
    return choices[:25]

@game_group.command(name="game_status", description="Muestra el estado actual de la partida en este canal")
async def game_status(interaction: discord.Interaction):
    # Deferimos porque leer la base de datos y procesar el estado puede tomar un instante
    await interaction.response.defer(ephemeral=False)
    
    try:
        with sqlite3.connect(game_group.db_path) as conn:
            # Cargamos la partida usando el canal actual
            game = Game.load_game(conn, channel_id=interaction.channel_id)
            
            # Llamamos a tu función interna que genera las líneas del reporte
            lineas_reporte = game.report_status()
            
            # Unimos todas las líneas devueltas con saltos de línea
            # Ponemos un fallback por si acaso la lista viniera vacía
            mensaje_status = "\n".join(lineas_reporte) if lineas_reporte else "No hay datos de estado disponibles."
            
        # Enviamos el reporte maquetado al canal
        await interaction.followup.send(mensaje_status)
        
    except GameNotFoundException:
        await interaction.followup.send(
            "**Error:** No hay ninguna partida activa en este canal.\n"
            "Crea una primero usando `/sharcashvelli_admin create`."
        )
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado:** `{type(e).__name__}: {e}`")

@game_group.command(name="game_report", description="Muestra el informe del último turno jugado")
async def game_report(interaction: discord.Interaction):
    # Deferimos porque leer la base de datos y procesar el estado puede tomar un instante
    await interaction.response.defer(ephemeral=False)
    
    try:
        with sqlite3.connect(game_group.db_path) as conn:
            # Cargamos la partida usando el canal actual
            game = Game.load_game(conn, channel_id=interaction.channel_id)

        report = game.turn_report()

        current_message = ""

        for l in report:
            # Comprobamos si añadir esta línea supera el límite de Discord (dejamos margen de seguridad)
            if len(current_message) + len(l) + 1 > 1950:
                # Enviamos lo que llevamos acumulado hasta ahora
                await interaction.followup.send(current_message)
                # Empezamos el nuevo bloque con la línea actual
                current_message = l
            else:
                # Si cabe, la acumulamos separada por un salto de línea
                if current_message:
                    current_message += f"\n{l}"
                else:
                    current_message = l
        
        # Enviamos el último bloque que haya quedado rezagado en el bucle
        if current_message:
            await interaction.followup.send(current_message)
            
    except GameNotFoundException:
        await interaction.followup.send("**Error:** No hay ninguna partida activa en este canal para poder ejecutarla.")
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado al mostrar el informe:** `{type(e).__name__}: {e}`.")

@admin_group.command(
    name="set_deadlines", description="Configura el horario semanal y el próximo deadline")
@app_commands.describe(
    dia_semanal="El día de la semana en que se procesarán los turnos de forma habitual",
    hora_semanal="La hora del deadline semanal (Formato HH:MM, ej: 22:00)",
    proximo_deadline="Fecha exacta del siguiente turno (Formato: DD/MM/AAAA HH:MM, ej: 22/07/2026 22:00)"
)
# Creamos un desplegable cerrado para los días de la semana
@app_commands.choices(dia_semanal=[
    app_commands.Choice(name="Lunes", value="Lunes"),
    app_commands.Choice(name="Martes", value="Martes"),
    app_commands.Choice(name="Miércoles", value="Miércoles"),
    app_commands.Choice(name="Jueves", value="Jueves"),
    app_commands.Choice(name="Viernes", value="Viernes"),
    app_commands.Choice(name="Sábado", value="Sábado"),
    app_commands.Choice(name="Domingo", value="Domingo"),
])
async def set_deadlines(
    interaction: discord.Interaction, 
    dia_semanal: app_commands.Choice[str] = None, 
    hora_semanal: str = None, 
    proximo_deadline: str = None
):
    await interaction.response.defer(ephemeral=False)
    
    try:
        with sqlite3.connect(admin_group.db_path) as conn:
            game = Game.load_game(conn, channel_id=interaction.channel_id)
            
            cambios = []

            # VALIDACIÓN DEL DEADLINE SEMANAL
            if dia_semanal or hora_semanal:
                # Si me dan el día, exijo la hora, y viceversa
                if not (dia_semanal and hora_semanal):
                    await interaction.followup.send("**Error:** Para fijar el horario semanal debes indicar tanto el día como la hora.")
                    return
                
                # Validamos que la hora tenga un formato HH:MM correcto
                try:
                    datetime.strptime(hora_semanal, "%H:%M")
                except ValueError:
                    await interaction.followup.send("**Error:** La hora semanal debe tener el formato `HH:MM` (ej: `22:00` o `09:30`).")
                    return
                
                game.weekly_deadline = f"{dia_semanal.value} a las {hora_semanal}"
                cambios.append(f"**Horario semanal:** {game.weekly_deadline}")

            # VALIDACIÓN DEL PRÓXIMO DEADLINE ESPECÍFICO
            if proximo_deadline:
                try:
                    # Parseamos lo que escribe el usuario (Formato natural en español: DD/MM/AAAA HH:MM)
                    fecha_parsed = datetime.strptime(proximo_deadline, "%d/%m/%Y %H:%M")
                    
                    # Lo guardamos en formato ISO (AAAA-MM-DD HH:MM) para la BBDD
                    game.next_deadline = fecha_parsed.strftime("%Y-%m-%d %H:%M")
                    
                    # Pero para mostrárselo al usuario en el mensaje, usamos un formato bonito
                    fecha_bonita = fecha_parsed.strftime("%A, %d de %B a las %H:%M")
                    cambios.append(f"**Próximo Deadline:** `{fecha_bonita}`")
                except ValueError:
                    await interaction.followup.send(
                        "**Error:** El formato del próximo deadline es incorrecto.\n"
                        "Debe ser estrictamente: `DD/MM/AAAA HH:MM` (ej: `22/07/2026 22:00`)."
                    )
                    return

            # GUARDADO (Si se ha configurado algo)
            if not cambios:
                await interaction.followup.send("No has introducido ningún parámetro para modificar.")
                return
                
            game.save(conn)
            
        # Generamos una respuesta elegante listando lo que ha cambiado
        resumen = "\n".join(cambios)
        await interaction.followup.send(
            f"**¡Plazos Actualizados!**\n"
            f"Se han guardado los nuevos plazos para la partida *'{game.name}'*:\n{resumen}"
        )
        
    except GameNotFoundException:
        await interaction.followup.send("**Error:** No hay ninguna partida activa en este canal.")
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado:** `{type(e).__name__}: {e}`")

@admin_group.command(name="run_game", description="Ejecuta y procesa el turno actual de la partida")
async def run_game(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    try:
        with sqlite3.connect(admin_group.db_path) as conn:
            # Cargamos la partida según el canal de Discord actual
            game = Game.load_game(conn, channel_id=interaction.channel_id)
            
            # Ejecutamos la magia del motor del juego
            report = game.run_game()
            
            # Guardamos en la BBDD cualquier cambio que haya hecho el motor
            game.save(conn)
            
        if not report:
            await interaction.followup.send("El turno se ha procesado, pero no se ha generado ninguna línea de reporte.")
            return

        current_message = ""
        for l in report:
            # Comprobamos si añadir esta línea supera el límite de Discord (dejamos margen de seguridad)
            if len(current_message) + len(l) + 1 > 1950:
                # Enviamos lo que llevamos acumulado hasta ahora
                await interaction.followup.send(current_message)
                # Empezamos el nuevo bloque con la línea actual
                current_message = l
            else:
                # Si cabe, la acumulamos separada por un salto de línea
                if current_message:
                    current_message += f"\n{l}"
                else:
                    current_message = l
        
        # Enviamos el último bloque que haya quedado rezagado en el bucle
        if current_message:
            await interaction.followup.send(current_message)
            
    except GameNotFoundException:
        await interaction.followup.send("**Error:** No hay ninguna partida activa en este canal para poder ejecutarla.")
    except Exception as e:
        await interaction.followup.send(f"**Error inesperado al ejecutar el turno:** `{type(e).__name__}: {e}`.")