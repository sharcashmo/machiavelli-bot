# bot.py

import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from machiavelli.database import upgrade
from machiavelli.discord import init_game_commands


def setup_service_logging() -> None:
    # Nivel de log en la variable de entorno LOG_LEVEL
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    logger = logging.getLogger("machiavelli")
    logger.setLevel(log_level)

    logger.propagate = False

    stream_handler = logging.StreamHandler(sys.stdout)

    # Si estás en modo DEBUG local, incluye información del archivo y línea
    if log_level == logging.DEBUG:
        fmt = "[%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    else:
        fmt = "[%(levelname)s] %(name)s: %(message)s"

    stream_handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(stream_handler)


# La ruta se puede sobrescribir al arrancar, pero importarla no toca el sistema.
DB_PATH = os.getenv("DATABASE_PATH", "machiavelli.db")

# Configurar intents
intents = discord.Intents.default()
intents.message_content = True

# Inicializar bot
bot = commands.Bot(command_prefix="!", intents=intents)


# --- COMANDO DE SINCRONIZACIÓN MANUAL ---
@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx, mode: str | None = None):
    """Gestión de los slash commands (Solo Dueño del Bot)

    Uso:
        !sync                -> Sincroniza en ESTE servidor (instantáneo)
        !sync global         -> Sincroniza en TODO Discord (tarda en verse)
        !sync status         -> Lista lo registrado global y en el servidor
        !sync clean          -> Borra los comandos de ESTE servidor (deja los globales)
        !sync clean_global   -> Borra los comandos globales (deja los de servidor)
    """
    if mode == "global":
        await ctx.send("🌍 Sincronizando comandos GLOBALMENTE...")
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"✅ {len(synced)} comandos globales sincronizados.")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    elif mode == "clean":
        await ctx.send("🧹 Borrando los comandos de ESTE servidor...")
        try:
            # Vaciar el árbol de servidor y sincronizar => Discord los borra
            bot.tree.clear_commands(guild=ctx.guild)
            await bot.tree.sync(guild=ctx.guild)
            await ctx.send(
                "✅ Comandos de servidor eliminados. Solo quedan los globales."
            )
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    elif mode == "clean_global":
        await ctx.send("🧹 Borrando los comandos GLOBALES...")
        try:
            # Se borran en Discord sin vaciar el árbol en memoria,
            # así un futuro !sync global puede volver a registrarlos.
            app_id = bot.application_id or bot.user.id
            for cmd in await bot.tree.fetch_commands():
                await bot.http.delete_global_command(app_id, cmd.id)
            await ctx.send("✅ Comandos globales eliminados. Solo quedan los locales.")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    elif mode == "status":
        globals = await bot.tree.fetch_commands()
        locals = await bot.tree.fetch_commands(guild=ctx.guild)
        await ctx.send(
            f"🌍 Globales ({len(globals)}): {[c.name for c in globals]}\n"
            f"🏠 En este servidor ({len(locals)}): {[c.name for c in locals]}"
        )

    else:
        await ctx.send("🏠 Sincronizando comandos en este servidor...")
        try:
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ {len(synced)} comandos sincronizados en este servidor.")
        except Exception as e:
            await ctx.send(f"❌ Error local: {e}")


@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")

    # Los comandos usan la ruta preparada por main() o el valor seguro por defecto.
    mach_group, shar_group = init_game_commands(bot.machiavelli_db_path)
    if not bot.tree.get_command("mach"):
        bot.tree.add_command(mach_group)
        print("Grupo 'mach' registrado en memoria local.")

    if not bot.tree.get_command("shar"):
        bot.tree.add_command(shar_group)
        print("Grupo 'shar' registrado en memoria local.")


@bot.event
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            "⛔ Canal o permisos no autorizados.", ephemeral=True
        )
    else:
        print(f"Error: {error}")
        await interaction.response.send_message("❌ Error interno.", ephemeral=True)


def main() -> None:
    """Prepara la configuración local e inicia explícitamente el cliente de Discord."""
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    db_path = os.getenv("DATABASE_PATH", "machiavelli.db")

    setup_service_logging()
    if not token:
        print(
            "⚠️ ADVERTENCIA: Por favor, configura tu DISCORD_TOKEN en el archivo .env."
        )
        return

    upgrade(db_path)
    bot.machiavelli_db_path = db_path
    bot.run(token)


bot.machiavelli_db_path = DB_PATH


if __name__ == "__main__":
    main()
