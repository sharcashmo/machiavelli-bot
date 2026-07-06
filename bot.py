import os
import io
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database



# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE = os.getenv("ADMIN_ROLE_NAME", "Juez")

# Configurar intents
intents = discord.Intents.default()
intents.message_content = True

# Inicializar bot
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    database.init_db()
    print(f'Bot conectado como {bot.user}')

# Función auxiliar para comprobar rol
def has_admin_role(ctx):
    if not ctx.guild:
        return False # No permitir en mensajes directos
    for role in ctx.author.roles:
        if role.name == ADMIN_ROLE:
            return True
    return False

@bot.command(name='send')
async def send_msg(ctx, *, message_content: str = None):
    """Guarda un mensaje y/o archivo en la base de datos."""
    try:
        file_name = None
        file_data = None
        
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            # Límite de 5 MB
            if attachment.size > 5 * 1024 * 1024:
                await ctx.send("❌ El archivo es demasiado grande (máximo 5 MB).")
                return
            file_name = attachment.filename
            file_data = await attachment.read()

        if not message_content and not file_data:
            await ctx.send("❌ Debes proporcionar un mensaje de texto o adjuntar un archivo.")
            return

        database.add_message(str(ctx.author), message_content or "", ctx.message.created_at, file_name, file_data)
        # Borrar el mensaje original del canal para que no sea visible
        await ctx.message.delete()
        # Confirmación visible en el canal (sin borrar)
        if file_name:
            confirm_msg = f"✅ {ctx.author.mention} Tu archivo **{file_name}** ha sido guardado correctamente."
        else:
            confirm_msg = f"✅ {ctx.author.mention} Tu mensaje ha sido guardado correctamente."
        await ctx.send(confirm_msg)
    except Exception as e:
        print(f"Error al guardar mensaje: {e}")
        await ctx.send("❌ Hubo un error al guardar el mensaje/archivo.")

@bot.command(name='view')
async def view_msgs(ctx):
    """Muestra todos los mensajes almacenados (Solo para el rol definido)."""
    if not has_admin_role(ctx):
        await ctx.send("⛔ No tienes permisos para usar este comando.")
        return

    messages = database.get_all_messages()
    if not messages:
        await ctx.send("No hay mensajes almacenados.")
        return

    response = "**Mensajes almacenados:**\n\n"
    for msg_id, user_name, content, timestamp, file_name in messages:
        # Construir línea de texto, incluir enlace de descarga si hay archivo
        line = f"🆔 **{msg_id}** | 🕒 **{timestamp}** | 👤 **{user_name}**: {content}"
        if file_name:
            line += f" !file {msg_id}"
        line += "\n"
        # Añadir al response con control de tamaño
        if len(response) + len(line) > 1900:
            await ctx.send(response)
            response = line
        else:
            response += line
    if response:
        await ctx.send(response)

@bot.command(name='clean')
async def clean_msgs(ctx):
    """Elimina todos los mensajes almacenados (Solo para el rol definido)."""
    if not has_admin_role(ctx):
        await ctx.send("⛔ No tienes permisos para usar este comando.")
        return
        
    try:
        database.clear_all_messages()
        await ctx.send("🗑️ Todos los mensajes han sido eliminados.")
    except Exception as e:
        print(f"Error al eliminar mensajes: {e}")
        await ctx.send("❌ Hubo un error al intentar eliminar los mensajes.")

@bot.command(name='list')
async def list_users(ctx):
    """Muestra la lista de usuarios que han enviado mensajes (Solo para el rol definido)."""
    if not has_admin_role(ctx):
        await ctx.send("⛔ No tienes permisos para usar este comando.")
        return

    try:
        users = database.get_users_with_messages()
        if not users:
            await ctx.send("No hay usuarios registrados (no hay mensajes).")
            return

        response = "**Usuarios que han enviado mensajes:**\n\n"
        for user, timestamp in users:
            line = f"🕒 **{timestamp}** | 👤 {user}\n"
            if len(response) + len(line) > 1900:
                await ctx.send(response)
                response = line
            else:
                response += line
        if response:
            await ctx.send(response)
    except Exception as e:
        print(f"Error al listar usuarios: {e}")
        await ctx.send("❌ Hubo un error al obtener la lista de usuarios.")

@bot.command(name='file')
async def download_file(ctx, message_id: int):
    """Descarga el archivo adjunto de un mensaje por su ID (Solo para el rol definido)."""
    if not has_admin_role(ctx):
        await ctx.send("⛔ No tienes permisos para usar este comando.")
        return

    try:
        result = database.get_file(message_id)
        if not result or not result[0]:
            await ctx.send(f"❌ No se encontró ningún archivo asociado al mensaje ID {message_id}.")
            return
        
        file_name, file_data = result
        # Enviar archivo a través de discord.File
        file_stream = io.BytesIO(file_data)
        await ctx.send(file=discord.File(file_stream, filename=file_name))
    except Exception as e:
        print(f"Error al descargar el archivo: {e}")
        await ctx.send("❌ Hubo un error al intentar recuperar el archivo.")



if __name__ == '__main__':
    if not TOKEN or TOKEN == "tu_token_aqui":
        print("⚠️ ADVERTENCIA: Por favor, configura tu DISCORD_TOKEN en el archivo .env antes de iniciar el bot.")
    else:
        bot.run(TOKEN)
