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
async def sync_commands(ctx, modo: str | None = None):
    """Sincroniza los slash commands bajo demanda (Solo Dueño del Bot)

    Uso:
        !sync        -> Sincroniza en ESTE servidor (Instantáneo)
        !sync global -> Sincroniza en TODO Discord (Tarda unos minutos/hora)"""
    if modo == "global":
        await ctx.send(
            "🌍 Sincronizando comandos GLOBALMENTE (puede tardar en aparecer)..."
        )
        try:
            synced = await bot.tree.sync()
            await ctx.send(
                f"✅ Éxito: Sincronizados {len(synced)} comandos globalmente."
            )
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")
    else:
        await ctx.send("🏠 Sincronizando comandos en este servidor específico...")
        try:
            # Clona los comandos que tenemos en memoria dentro de este servidor concreto
            bot.tree.copy_global_to(guild=ctx.guild)

            # Sincroniza solo este servidor
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(
                f"✅ Éxito: Sincronizados {len(synced)} comandos en este servidor."
            )
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
