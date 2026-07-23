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

La versión actual de desarrollo es la 0.1.1. La versión actual de producción es la 0.1.0.

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

### `/mach` (todos)

*Versión 0.2.0*
Conjunto de comandos para su uso por los jugadores. En este momento hay dos.
En la versión `0.1.x` el comando era `/sharcashvelli`.

#### `/mach game_status`

*Versión 0.2.0*
Muestra el estado de la partida.

#### `/mach game_report`

*Versión 0.2.0*
Muestra el último informe de la partida.

### `/shar` (Juez)

*Versión 0.2.0*
Conjunto de comandos para su uso por el administrador. En este momento hay seis.
En la versión `0.1.x` el comando era `/sharcashvelli_admin`.

#### `/shar create`

*Versión 0.2.0*
Crea una partida en el canal en que se ejecuta.

#### `/shar set_scenario`

*Versión 0.2.0*
Selecciona un escenario para la partida.

#### `/shar set_deadlines`

*Versión 0.2.0*
Fija las fechas de ejecuciones de turnos de la partida.

#### `/shar add_player`

*Versión 0.2.0*
Añade un jugador a la partida.

#### `/shar run_game`

*Versión 0.2.0*
Ejecuta las órdenes de la partida y genera el informe para el siguiente turno.

## Futuras versiones

Se prevén las siguientes versiones

### Versión 0.2.0

- [X] Los grupos de comandos ahora se llaman `mach` para los comandos de usuario, y `shar` para los de administración.
- [X] El comando `/mach game_report` ahora manda un mensaje privado (ephemeral) para no saturar el canal.
- [X] Crear nuevos comandos para introducir las órdenes de forma interactiva
  - [X] Crear nuevo comando `/mach cmdlist` para mostrar los comandos que tenemos actualmente escritos.
  - [X] Crear nuevo comando `/mach cmd` para enviar órdenes. Parcial, solo soporta las órdenes de mantenimiento.

### Desarrollos futuros

Cambios que afectan a los comandos del bot. Los cambios se irán incorporando a las versiones conforme se completen.

- [ ] Ejecutar todas las acciones del turno. Actualmente solo hace el setup inicial
- [ ] Hacer que el reporte incluya un mapa gráfico con la posición de las unidades en él

## Histórico de versiones

- Versión 0.0.1:
  Primera versión, incluye comandos para enviar las órdenes como fichero adjunto, para ver quién los ha mandado y para descargarlos. Este primer bot no tiene ninguna lógica relacionada con el juego, solo es un "almacenador" de mensajes.
- Versión 0.1.0: Primer bot que tiene la lógica del juego. Incorpora sus tablas; las potencias, los jugadores, la información de la situación del tablero y la ejecución y reporte del primerísimo turno, el inicio de Primavera (Hambre e Ingresos).

  Estos comandos (bajo el grupo `/sharcashvelli` y `/sharcashvelli_admin`) conviven con los de la *versión 0.0.1* ya que no tienen forma de permitir el envío de órdenes de juego, que todavía deben enviarse con `!send`.

- Versión 0.1.1: Se añade información sobre los asedios.
