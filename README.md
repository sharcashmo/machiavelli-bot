# Machiavelli Bot 🎭

Este es un bot de Discord desarrollado en Python utilizando la librería `discord.py`. El propósito principal del bot es recopilar y almacenar mensajes enviados por los usuarios en una base de datos local SQLite y permitir que usuarios con un rol administrativo específico puedan ver o borrar estos mensajes.

---

## 🛠️ Estructura del Proyecto

* **`bot.py`**: Archivo principal. Contiene la inicialización del bot de Discord, la lógica de los comandos y el middleware de comprobación de roles.
* **`database.py`**: Capa de datos. Maneja la base de datos SQLite y exporta funciones para inicializar la tabla, insertar mensajes, leer todos los registros y vaciar la tabla.
* **`requirements.txt`**: Listado de dependencias (`discord.py` y `python-dotenv`).
* **`.gitignore`**: Configurado para omitir entornos virtuales (`venv/`), la base de datos de pruebas local (`messages.db`) y las credenciales secretas (`.env`).
* **`.env.example`**: Archivo de ejemplo para la configuración de las variables de entorno locales.

---

## 📖 Funcionalidad y Comandos

El bot responde al prefijo `!` y cuenta con los siguientes comandos:

1. **`!send <mensaje>`**:
   * **Permiso**: Público (cualquier usuario del servidor).
   * **Acción**: Almacena en la base de datos el nombre del autor (Usuario#1234), el contenido del mensaje y la fecha/hora del envío en formato UTC.
2. **`!view`**:
   * **Permiso**: Restringido (solo usuarios con el rol configurado en `ADMIN_ROLE_NAME`, por defecto **Juez**).
   * **Acción**: Recupera todos los mensajes almacenados en la base de datos y los imprime en el canal en bloques cronológicos ordenados de menor a mayor.
3. **`!clean`**:
   * **Permiso**: Restringido (solo usuarios con el rol configurado en `ADMIN_ROLE_NAME`, por defecto **Juez**).
   * **Acción**: Elimina todos los registros de la base de datos de manera irreversible.

---

## 💻 Desarrollo y Ejecución Local

### Requisitos previos:
* Python 3.10 o superior instalado.
* Token de Bot de Discord con el **Message Content Intent** habilitado en la sección "Bot" del Discord Developer Portal.

### Pasos para ejecutar:
1. Crea un entorno virtual e instálate las dependencias:
   ```powershell
   python -m venv venv
   # En Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   # En Windows (CMD clásico):
   .\venv\Scripts\activate.bat
   # En Linux / macOS:
   source venv/bin/activate
   
   pip install -r requirements.txt
   ```
2. Crea un archivo **`.env`** en la raíz del proyecto (copiándolo desde `.env.example`) y configura tus variables de entorno:
   ```env
   DISCORD_TOKEN="tu_token_de_discord_aqui"
   ADMIN_ROLE_NAME="Juez"
   ```
3. Crea un rol en tu servidor de Discord que se llame exactamente igual a tu `ADMIN_ROLE_NAME` (ej: `Juez`) y asígnatelo.
4. Arranca el bot localmente:
   ```powershell
   python bot.py
   ```
