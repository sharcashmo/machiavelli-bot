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

Estos son los que tendré que utilizar para obtener el token del bot.

En el aparaado de Bot, Privileged Gateway Intents, deben marcarse:

- Server Members Intent
- Message Content Intent

Los parámetros que necesita el bot los fijamos en un fichero .env. Se puede tomar .env.example como punto de partida:

```env
DISCORD_TOKEN=tu_token_aqui
DATABASE_PATH=game.db
```

## Instalación

Machiavelli requiere Python 3.12 o posterior. Para instalar el paquete en modo
desarrollo con sus herramientas de calidad:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

La instalación de producción se puede realizar con `pip install .` o desde una
distribución wheel generada mediante `python -m build`.

## Versión

Versión de desarrollo actual: 0.7.0. Última versión estable publicada: 0.6.0.

## Comandos disponibles

### `!sync` (admin)

> Versión 0.0.1

Sincroniza los comandos disponibles. Ejecutar cuando se deban registrar nuevos comandos de slash.

### `/mach` (todos)

> Versión 0.2.0

Conjunto de comandos para su uso por los jugadores. Todos estos comandos son privados,
es decir, la respuesta a los comandos es un mensaje privado solo visible por el jugador
que los ejecuta.

#### `/mach game_status`

> Versión 0.2.0

Muestra las órdenes enviadas, el estado de la partida y los jugadores que han enviado
sus órdenes hasta ese momento.

#### `/mach game_report`

> Versión 0.2.0

Muestra el último informe de la partida.

#### `/mach cmd`

> Versión 0.2.0

Añade una orden nueva al turno actual.

#### `/mach expense`

> Versión 0.4.0

Añade un gasto nuevo al turno actual.

### `/mach exchange`

> Versión 0.7.0

Realiza un intercambio con otro jugador. Para que un intercambio tenga éxito los dos
jugadores tienen que realizarlo.

### `/mach give`

> Versión 0.7.0

Da recursos (ducados o fichas de asesinato) a otro jugador.

### `/mach retreats`

> Versión 0.7.0

Da a una unidad sus provincias prioritarias de retirada.

### `/shar` (administrador)

> Versión 0.2.0

Conjunto de comandos para su uso por el administrador. Todos estos comandos son públicos.

#### `/shar create`

> Versión 0.2.0

Crea una partida en el canal en que se ejecuta.

#### `/shar set_scenario`

> Versión 0.2.0

Selecciona un escenario para la partida.

#### `/shar set_deadlines`

> Versión 0.2.0

Fija las fechas de ejecuciones de turnos de la partida.

#### `/shar add_player`

> Versión 0.2.0

Añade un jugador a la partida.

#### `/shar run_game`

> Versión 0.2.0

Ejecuta las órdenes de la partida y genera el informe para el siguiente turno.

#### `/shar cmd_user`

> Versión 0.3.0

Introduce las órdenes para un jugador determinado, cuando este jugador ha hecho llegar las órdenes de forma
distinta al uso de `/mach cmd`, por ejemplo enviando la plantilla rellena.

#### `/shar expense_user`

> Versión 0.4.0

Introduce gastos para un jugador determinado, cuando este jugador ha hecho llegar los gastos de forma
distinta al uso de `/mach expense`, por ejemplo enviando la plantilla rellena.

## Futuras versiones

Se prevén las siguientes versiones

### Versión 0.7.0

- [X] Mejora del reporte.
  - [X] Incluye las órdenes de los jugadores en el reporte.
  - [X] Mejora la representación de los eventos.
  - [X] Mejora general del formato del reporte.
- [X] Nuevos comandos `/exchange` y `/give` para intercambiar o dar recursos a otro jugador.
- [X] Incorpora los resultados de `/mach cmdlist` en `/mach game_status`.
- [X] Mejora el filtrado de comandos en `/mach cmd` en el caso de unidades en asedio.
- [X] Cambio en el algoritmo de retirada. Ahora se ejecuta por pasos y los unidades que tienen
  una retirada "preferible" actúan primero.

### Desarrollos futuros

Cambios que afectan a los comandos del bot. Los cambios se irán incorporando a las versiones conforme se completen.

- [ ] Ejecutar todas las acciones del turno. Todavía faltan:
  - [ ] Asesinatos.
  - [ ] Tratamiento del fin de partida.
  - [ ] Tratamiento de eliminación de jugadores.
  - [ ] Control de estrechos.
- [ ] Incluir algún tipo de chequeo del turno para advertir de órdenes incorrectas, ilegales o inconsistentes.
- [ ] Hacer que el reporte incluya un mapa gráfico con la posición de las unidades en él.

## Histórico de versiones

- Versión 0.0.1:
  Primera versión, incluye comandos para enviar las órdenes como fichero adjunto, para ver quién los ha mandado y para
  descargarlos. Este primer bot no tiene ninguna lógica relacionada con el juego, solo es un "almacenador" de mensajes.
- Versión 0.1.0: Primer bot que tiene la lógica del juego. Incorpora sus tablas; las potencias, los jugadores, la
  información de la situación del tablero y la ejecución y reporte del primerísimo turno, el inicio de Primavera
  (Hambre e Ingresos).

  Estos comandos (bajo el grupo `/sharcashvelli` y `/sharcashvelli_admin`) conviven con los de la *versión 0.0.1* ya
  que no tienen forma de permitir el envío de órdenes de juego, que todavía deben enviarse con `!send`.
- Versión 0.1.1: Se añade información sobre los asedios.
- Versión 0.2.1: Se renombran los grupos `/sharcashvelli` y `/sharcashvelli_admin` a `/mach` y `/shar` respectivamente;
  se añaden dos nuevos comandos `/mach cmd` y `/mach cmdlist`; el comando `/mach game_report` ahora envía un mensaje
  privado.
- Versión 0.3.0: Se ejecutan las órdenes del turno (fase de mantenimiento solo). nuevo comando `/shar cmd_user` para
  introducir en el bot las órdenes de un jugador que las haya mandado usando `/send`. Se ha mejorado el formato de
  `/mach game_report` y de `/mach game_status`.
- Versión 0.3.1: Corrección de algunos bugs.
- Versión 0.4.0: Se eliminan definitivamente los comandos `/send`, `/list`, `/view`, `/dice`, `/file` y `/clean`.
  Se expanden `/mach cmd`, `/mach cmdlist` y `/shar cmd_user` para tratar las órdenes de una campaña, y se añaden
  los nuevos comandos `/mach expense` y `/shar expense_user` para enviar los gastos para el turno actual, que se
  separan del envío de órdenes a las tropas.
- Versión 0.4.1: Corrección de algunos bugs importantes.
- Versión 0.4.2: Corrección de un bug en el envío de órdenes de conversión.
- Versión 0.5.0: Primera versión con implementación de las fases de campaña. Incluye gestión completa de desastres
  (hambre y plagas), gestión de todos los gastos EXCEPTO el asesinato, pero sí todos los SOBORNOS. De las órdenes
  militares solo resuelve el AVANCE, la CONVERSIÓN y MANTENER, y NO gestiona conflictos.
  
  La generación de eventos se ha modificado, y los eventos ya no se generan ya formateados, sino de forma abstracta
  (tipo de evento y los datos asociados), pero todavía es necesario implementar un gestor de eventos que los muestre
  en el reporte correctamente (actualmente, solo muestra el tipo de evento).

  La gestión de la lógica del juego se ha trasladado a un nuevo componente, GameEngine, que actúa de orquestador entre
  distintos componentes (gestores de Desastres, de Control, de Gastos, de Rebeliones, de Sobornos, Militares, etc) y
  que está prácticamente terminado, pero le quedan las partes más complejas (TRANSPORTE y, sobre todo, CONFLICTOS).
- Versión 0.5.1: `/mach game_status` devuelve ahora un mensaje privado.
- Versión 0.5.2: todos los comandos `/mach` (de jugador) devuelven un mensaje privado, y todos los comandos `/shar`
  (de administrador) devuelven un mensaje público.
- Versión 0.6.0: refactorización y reescritura de buena parte del código. Se ha completado el módulo de engine con la
  resolución de conflictos militares, y las retiradas; se comprueban las reglas activas; nuevos servicios de Reporter
  para separar el reporte de la lógica del juego; el módulo de discord se ha simplificado y apartado de allí la lógica
  del juego y de la base de datos; se ha mejorado la gestión de eventos y la generación de reportes, etc.
- Versión 0.7.0: nuevos comandos `/mach exchange` y `/mach give`. `/mach game_status` ahora incluye el listado de
  órdenes enviadas por lo que `/mach cmdlist` queda eliminada. Presentación del reporte de turno ordenada y mejorada.
  Nuevo comando `/mach retreats`. Cambio en el algoritmo de retiradas: ahora las unidades con una retirada preferible
  (ie, hacia su propio territorio) actúan primero.
