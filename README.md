# Machiavelli Bot 🎭

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

Los parámetros que necesita el bot los fijamos en un fichero .env. Se puede tomar .env.example como punto de partida:

```env
DISCORD_TOKEN=tu_token_aqui
ADMIN_ROLE_NAME=Juez
DATABASE_PATH=messages.db
COMMANDS_CHANNEL=id_del_canal
```
