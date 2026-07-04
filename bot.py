import os
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
async def send_msg(ctx, *, message_content: str):
    """Guarda un mensaje en la base de datos."""
    try:
        # ctx.message.created_at nos da un objeto datetime en UTC
        database.add_message(str(ctx.author), message_content, ctx.message.created_at)
        await ctx.send("✅ Mensaje guardado correctamente.")
    except Exception as e:
        print(f"Error al guardar mensaje: {e}")
        await ctx.send("❌ Hubo un error al guardar el mensaje.")

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
    for user_name, content, timestamp in messages:
        # Añadir al texto de respuesta. Si supera 2000 caracteres se debe enviar en varios mensajes,
        # pero para simplificar, enviamos todo en bloques asumiendo que no será gigante de golpe
        # (o controlaremos la longitud).
        line = f"🕒 **{timestamp}** | 👤 **{user_name}**: {content}\n"
        
        # Límite de mensaje en Discord es 2000 caracteres
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

if __name__ == '__main__':
    if not TOKEN or TOKEN == "tu_token_aqui":
        print("⚠️ ADVERTENCIA: Por favor, configura tu DISCORD_TOKEN en el archivo .env antes de iniciar el bot.")
    else:
        bot.run(TOKEN)
