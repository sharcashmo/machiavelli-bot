# Machiavelli Bot

Este es un bot de Discord desarrollado para automatizar partidas de Machiavelli. Este bot permite (permitirá) a un
administrador (rol de Juez) crear partidas, seleccionar escenario y condiciones de victoria, añadir jugadores, repartir
facciones entre esos jugadores, fijar deadlines para la entrega de órdenes, y ejecutar esas órdenes de forma automática
o manual.

Además permitirá a los jugadores recibir el estado actual de la partida, enviar sus órdenes para el turno actual, y
realizar intercambios de recursos con el resto de jugadores.

## El bot de Discord

Tenemos dos versiones del bot registrada: sharcashvelli para la versión ya en marcha, y sharcashvelli-bot con la versión
de desarrollo.

Los dos bots necesitan los siguientes permisos:

- View Channel
- Send Messages
- Manage Messages
- Read Message History
- Use Application Commands
- Attach Files

Estos son los que tendré que utilizar para obtener el token del bot.

En el aparaado de Bot, Privileged Gateway Intents, deben marcarse:

- Server Members Intent
- Message Content Intent

Los parámetros que necesita el bot los fijamos en un fichero .env. Se puede tomar .env.example como punto de partida:

```env
DISCORD_TOKEN=tu_token_aqui
ADMIN_ROLE_NAME=Juez
DATABASE_PATH=game.db
COMMANDS_CHANNEL=id_del_canal
```

## Versión

La versión actual de desarrollo es la 0.1.0. La versión actual de producción es la 0.1.0.

## Comandos disponibles

### `!sync` (Juez)

*Versión 0.0.1*
Sincroniza los comandos disponibles. Ejecutar cuando se deban registrar nuevos comandos de slash.

### `/dice` (todos)

*Versión 0.0.1*
Lanza 1 o 2 dados de seis.

### `/send` (todos)

*Versión 0.0.1*
Envía un mensaje al bot. El mensaje puede ser de texto o un archivo adjunto. Se utilizará para enviar las órdenes.

### `/list` (Juez)

*Versión 0.0.1*
Muestra un listado de los jugadores que han mandado órdenes al bot. No se muestra el contenido de esas órdenes, para
ello debe utilizarse `/view`.

### `/view` (Juez)

*Versión 0.0.1*
Muestra el contenido de los mensajes enviados por los jugadores. En el caso de los archivos adjuntos, lo que se muestra
ese el identificador del archivo para descargarlo mediante `/file`.

### `/file` (Juez)

*Versión 0.0.1*
Descarga un archivo enviado por un jugador. El identificador del archivo es el que se muestra en `/view`.

### `/clean` (Juez)

*Versión 0.0.1*
Elimina todos los mensajes almacenados en el bot.

### `/sharcashvelli` (todos)

*Versión 0.1.0*
Conjunto de comandos para su uso por los jugadores. En este momento hay dos.

#### `/sharcashvelli game_status`

*Versión 0.1.0*
Muestra el estado de la partida.

#### `/sharcashvelli game_report`

*Versión 0.1.0*
Muestra el último informe de la partida.

### `/sharcashvelli_admin` (Juez)

*Versión 0.1.0*
Conjunto de comandos para su uso por el administrador. En este momento hay seis.

#### `/sharcashvelli_admin create`

*Versión 0.1.0*
Crea una partida en el canal en que se ejecuta.

#### `/sharcashvelli_admin set_scenario`

*Versión 0.1.0*
Selecciona un escenario para la partida.

#### `/sharcashvelli_admin set_deadlines`

*Versión 0.1.0*
Fija las fechas de ejecuciones de turnos de la partida.

#### `/sharcashvelli_admin add_player`

*Versión 0.1.0*
Añade un jugador a la partida.

#### `/sharcashvelli_admin run_game`

*Versión 0.1.0*
Ejecuta las órdenes de la partida y genera el informe para el siguiente turno.
