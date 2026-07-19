import os
import io
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import database
import random

load_dotenv()

from machiavelli.database import upgrade
from machiavelli.discord import init_game_commands

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE = os.getenv("ADMIN_ROLE_NAME", "Juez")
COMMANDS_CHANNEL = int(os.getenv("COMMANDS_CHANNEL"))

# La base de datos
DB_PATH = os.getenv("DATABASE_PATH", "machiavelli.db")

# Ejecutamos las migraciones
upgrade(DB_PATH)

# Configurar intents
intents = discord.Intents.default()
intents.message_content = True

# Inicializar bot
bot = commands.Bot(command_prefix='!', intents=intents)

# Decoradores de seguridad
def has_admin_role(interaction: discord.Interaction):
    if not interaction.guild: return False
    return any(role.name == ADMIN_ROLE for role in interaction.user.roles)

def is_allowed_channel(interaction: discord.Interaction):
    return interaction.channel_id == COMMANDS_CHANNEL

# --- COMANDO DE SINCRONIZACIÓN MANUAL ---
@bot.command(name='sync')
@commands.is_owner()
async def sync_commands(ctx, modo: str | None = None):
    """Sincroniza los slash commands bajo demanda (Solo Dueño del Bot)
    
    Uso:
        !sync        -> Sincroniza en ESTE servidor (Instantáneo)
        !sync global -> Sincroniza en TODO Discord (Tarda unos minutos/hora)"""
    if modo == "global":
        await ctx.send("🌍 Sincronizando comandos GLOBALMENTE (puede tardar en aparecer)...")
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"✅ Éxito: Sincronizados {len(synced)} comandos globalmente.")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")
    else:
        await ctx.send("🏠 Sincronizando comandos en este servidor específico...")
        try:
            # Clona los comandos que tenemos en memoria dentro de este servidor concreto
            bot.tree.copy_global_to(guild=ctx.guild)
            # Sincroniza solo este servidor
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ Éxito: Sincronizados {len(synced)} comandos en este servidor al instante.")
        except Exception as e:
            await ctx.send(f"❌ Error local: {e}")

@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user}')
    
    # La base de datos antigua, por si todavía me hace falta
    database.init_db()

    # Los nuevos comandos
    sharcashvelli_group, sharcashvelli_admin_group = init_game_commands(DB_PATH)
    if not bot.tree.get_command("sharcashvelli"):
        bot.tree.add_command(sharcashvelli_group)
        print("Grupo 'sharcashvelli' registrado en memoria local.")
        
    if not bot.tree.get_command("sharcashvelli_admin"):
        bot.tree.add_command(sharcashvelli_admin_group)
        print("Grupo 'sharcashvelli_admin' registrado en memoria local.")

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("⛔ Canal o permisos no autorizados.", ephemeral=True)
    else:
        print(f"Error: {error}")
        await interaction.response.send_message("❌ Error interno.", ephemeral=True)

@bot.tree.command(name="dice", description="Lanza dados (1 o 2)")
@app_commands.check(is_allowed_channel)
async def lanzar_dado(interaction: discord.Interaction, cantidad: int = 1):
    """Lanza 1 o 2 dados. Uso de interfaz nativa en Discord"""
    if cantidad < 1 or cantidad > 2:
        await interaction.response.send_message("Por favor, lanza 1 o 2 dados.", ephemeral=True)
        return
    
    resultados = [random.randint(1, 6) for _ in range(cantidad)]
    suma = sum(resultados)
    await interaction.response.send_message(f"🎲 Resultado: {resultados} (Total: {suma})")

@bot.tree.command(name="send", description="Guarda mensaje o archivo")
@app_commands.check(is_allowed_channel)
async def send_msg(interaction: discord.Interaction, message: str = None, file: discord.Attachment = None):
    # 'defer' para operaciones de IO/DB que puedan tardar
    await interaction.response.defer(ephemeral=True) 
    
    file_name = None
    file_data = None
    
    if file:
        if file.size > 5 * 1024 * 1024:
            await interaction.followup.send("❌ Archivo > 5MB.", ephemeral=True)
            return
        file_name = file.filename
        file_data = await file.read()

    if not message and not file_data:
        await interaction.followup.send("❌ Nada que guardar.", ephemeral=True)
        return

    database.add_message(str(interaction.user), message or "", interaction.created_at, file_name, file_data)
    await interaction.followup.send("✅ Guardado correctamente.", ephemeral=True)

@bot.tree.command(name="view", description="Ver mensajes")
@app_commands.check(is_allowed_channel)
async def view_msgs(interaction: discord.Interaction):
    if not has_admin_role(interaction):
        await interaction.response.send_message("⛔ Sin permisos.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)
    messages = database.get_all_messages()
    if not messages:
        await interaction.followup.send("Vacío.", ephemeral=False)
        return

    # Construcción de respuesta
    response = "\n".join([f"👤 {m[1]}: {m[2]}" for m in messages])
    await interaction.followup.send(response[:2000], ephemeral=False)

@bot.tree.command(name='clean', description="Elimina todos los mensajes almacenados (Solo Juez)")
@app_commands.check(is_allowed_channel)
async def clean_msgs(interaction: discord.Interaction):
    if not has_admin_role(interaction):
        await interaction.response.send_message("⛔ Solo un Juez puede ejecutar este comando.", ephemeral=True)
        return
        
    try:
        database.clear_all_messages()
        await interaction.response.send_message("🗑️ Todos los mensajes han sido eliminados.", ephemeral=False)
    except Exception as e:
        print(f"Error al eliminar mensajes: {e}")
        await interaction.response.send_message("❌ Hubo un error al intentar eliminar los mensajes.", ephemeral=False)

@bot.tree.command(name='list', description="Muestra usuarios que han enviado mensajes (Solo Juez)")
@app_commands.check(is_allowed_channel)
async def list_users(interaction: discord.Interaction):
    if not has_admin_role(interaction):
        await interaction.response.send_message("⛔ No tienes permisos para usar este comando.", ephemeral=True)
        return

    # Deferimos la respuesta por si la base de datos es grande
    await interaction.response.defer(ephemeral=False)

    try:
        users = database.get_users_with_messages()
        if not users:
            await interaction.followup.send("No hay usuarios registrados (no hay mensajes).", ephemeral=False)
            return

        response = "**Usuarios que han enviado mensajes:**\n\n"
        for user, timestamp in users:
            line = f"🕒 **{timestamp}** | 👤 {user}\n"
            if len(response) + len(line) > 1900:
                await interaction.followup.send(response, ephemeral=False)
                response = line
            else:
                response += line
        if response:
            await interaction.followup.send(response, ephemeral=False)
    except Exception as e:
        print(f"Error al listar usuarios: {e}")
        await interaction.followup.send("❌ Hubo un error al obtener la lista de usuarios.", ephemeral=False)

@bot.tree.command(name='file', description="Descarga el archivo adjunto de un mensaje por su ID (Solo Juez)")
@app_commands.check(is_allowed_channel)
async def download_file(interaction: discord.Interaction, message_id: int):
    if not has_admin_role(interaction):
        await interaction.response.send_message("⛔ No tienes permisos para usar este comando.", ephemeral=True)
        return

    # Descargar archivos puede tardar más de 3 segundos, usamos defer
    await interaction.response.defer(ephemeral=False)

    try:
        result = database.get_file(message_id)
        if not result or not result[0]:
            await interaction.followup.send(f"❌ No se encontró ningún archivo asociado al mensaje ID {message_id}.", ephemeral=False)
            return
        
        file_name, file_data = result
        file_stream = io.BytesIO(file_data)
        await interaction.followup.send(file=discord.File(file_stream, filename=file_name), ephemeral=False)
    except Exception as e:
        print(f"Error al descargar el archivo: {e}")
        await interaction.followup.send("❌ Hubo un error al intentar recuperar el archivo.", ephemeral=False)


if __name__ == '__main__':
    if not TOKEN or TOKEN == "tu_token_aqui":
        print("⚠️ ADVERTENCIA: Por favor, configura tu DISCORD_TOKEN en el archivo .env antes de iniciar el bot.")
    else:
        bot.run(TOKEN)
