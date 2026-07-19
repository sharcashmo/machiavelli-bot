# main_cli.py
import os
import logging
from dotenv import load_dotenv

import machiavelli

# Cargamos las variables de entorno
load_dotenv()
DB_PATH = os.getenv("DATABASE_PATH", "machiavelli.db")

# Configuramos el logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),                         # Muestra los logs en la consola
        logging.FileHandler("cli.log", encoding="utf-8") # Guarda los logs en un archivo de texto
    ]
)

# Importamos el logger
logger = logging.getLogger(__name__)

def main():
    logger.info(f"Iniciando Cliente de Machiavelli v{machiavelli.VERSION}...")

    # Comprobar actualizaciones
    try:
        machiavelli.upgrade(DB_PATH)
    except Exception as e:
        logger.critical(f"Error al iniciar la aplicación. No se pudo comprobar la versión de Base de datos. Error: {e}")
        return

    # 4. Bucle principal de la interfaz de línea de comandos (Placeholder)
    print("\n" + "="*40)
    print(f" 🎭 WELCOME TO MACHIAVELLI CLI (v{machiavelli.VERSION}) 🎭")
    print("="*40)
    
    # Pequeña prueba interactiva para comprobar que todo el paquete funciona
    print("\n[System] Creating a test game instance...")
    partida_test = machiavelli.Game(name="Test Match Italy")
    print(f"[System] Game instance ready: {partida_test}")
    
    print("\n[Menu] Proximamente: Gestión de partidas y turnos.")
    print("Exiting CLI. Bye!")


if __name__ == "__main__":
    main()