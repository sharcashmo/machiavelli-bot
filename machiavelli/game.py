# machiavelli/game.py
from dataclasses import dataclass, field, fields
from machiavelli.scenario import Scenario, Power, HomeCountry, PowerDict
from machiavelli.map import Map
from machiavelli.tables import GameTables
from typing import Self
import sqlite3
import json
import random
from datetime import datetime, timedelta

class FailedToStartError(Exception):
    """Excepción lanzada cuando se intenta arrancar una partida sin tener todos los prerrequisitos."""

    def __init__(self, current: int, required: int, message: str):
        self.current = current
        self.required = required
        self.message = message
        super().__init__(self.message)

class DuplicatedGameException(Exception):
    """Excepción lanzada cuando se intenta crear una partida con un nombre o canal que ya están registrados."""
    pass

class GameNotFoundException(Exception):
    """Lanzada cuando se busca una partida en la BBDD y no existe."""
    pass

@dataclass
class Player:
    """Representa a un jugador de la partida.

    En esta clase guardaremos todo lo necesario para identificar al jugador y contactarle si fuera necesario,
    así como el estado de sus ejércitos, provincias y recursos.

    Attributes:
        player_id (str): Identificador único del jugador.
        discord_id (int): Identificador de usuario de Discord.
        controlled_locations (list[str]): Lista de códigos de localizaciones controladas por el jugador.
        armies (list[str]): Lista de códigos de localizaciones en que se sitúan los Ejércitos del jugador.
        fleets (list[str]): Lista de códigos de localizaciones en que se sitúan las Flotas del jugador.
        garrisons (list[str]): Lista de códigos de localizaciones en que se situán las Guarniciones del jugador.
        ass_counters (list[str]): Lista de fichas de asesinatos.
        ducats (int): Ducados del jugador.
        rebelled_provinces (list[str]): Lista de códigos de localizaciones de provincias rebeladas.
        rebelled_cities (list[str]): Lista de códigos de localizaciones de ciudades rebeladas.
        home_countries (list[str]): Lista de naciones natales que controla el jugador.
        power (str): Potencia que maneja el jugador
    """

    player_id: str
    discord_id: int | None = None
    controlled_locations: list[str] = field(default_factory=list)
    armies: list[str] = field(default_factory=list)
    fleets: list[str] = field(default_factory=list)
    garrisons: list[str] = field(default_factory=list)
    ass_counters: list[str] = field(default_factory=list)
    ducats: int = 0
    rebelled_provinces: list[str] = field(default_factory=list)
    rebelled_cities: list[str] = field(default_factory=list)
    home_countries: list[str] = field(default_factory=list)
    power: str | None = None

    def assign_power(self, power: Power):
        """Asigna una potencia al jugador e inicializa sus valores"""
        self.power = power.id
        self.home_countries = [power.id]
        self.controlled_locations = power.controlled_provinces.copy()
        self.armies = power.armies.copy()
        self.fleets = power.fleets.copy()
        self.garrisons = power.garrisons.copy()

    def save(self, conn: sqlite3.Connection, game_id: int) -> None:
        """Guarda o actualiza al jugador en la base de datos vinculándolo a una partida.

        Al usar ON CONFLICT, si el par (game_id, player_id) ya existe,
        actualizará el discord_id con el valor actual en memoria.

        Args:
            conn (sqlite3.Connection): La conexión a la base de datos.
            game_id (int): El ID de la partida.
        """
        cursor = conn.cursor()

        # Convertimos la lista de controlled_locations a un string JSON
        locations_json = json.dumps(self.controlled_locations)
        armies_json = json.dumps(self.armies)
        fleets_json = json.dumps(self.fleets)
        garrisons_json = json.dumps(self.garrisons)
        ass_counters_json = json.dumps(self.ass_counters)
        rebelled_provinces_json = json.dumps(self.rebelled_provinces)
        rebelled_cities_json = json.dumps(self.rebelled_cities)
        home_countries_json = json.dumps(self.home_countries)

        cursor.execute(
            """
            INSERT INTO players (
                game_id, player_id, discord_id, controlled_locations,
                armies, fleets, garrisons, ass_counters, ducats,
                rebelled_provinces, rebelled_cities, home_countries, power
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, player_id) DO UPDATE SET
                discord_id = excluded.discord_id,
                controlled_locations = excluded.controlled_locations,
                armies = excluded.armies,
                fleets = excluded.fleets,
                garrisons = excluded.garrisons,
                ass_counters = excluded.ass_counters,
                ducats = excluded.ducats,
                rebelled_provinces = excluded.rebelled_provinces,
                rebelled_cities = excluded.rebelled_cities,
                home_countries = excluded.home_countries,
                power = excluded.power
            """,
            (
                game_id, self.player_id, self.discord_id, locations_json,
                armies_json, fleets_json, garrisons_json, ass_counters_json, self.ducats,
                rebelled_provinces_json, rebelled_cities_json, home_countries_json, self.power
            ),
        )
    
    def player_report(self, map: Map):
        """Genera las líneas del informe de situación para el jugador."""
        report = []
        report.append(f"__**  {PowerDict[self.power]} (<@{self.discord_id}>)  **__")

        if self.home_countries:
            # Países natales
            hc_names = [PowerDict[p] for p in self.home_countries]
            if len(self.home_countries) > 1:
                hc = " y ".join([", ".join(hc_names[0:-1]), hc_names[-1]])
            else:
                hc = hc_names[0]
            report.append(f"***Naciones controladas:*** {hc}")

            # Recursos
            ass_names = [PowerDict[p] for p in self.ass_counters]
            if len(self.ass_counters) == 0:
                assassination = "Ninguna"
            elif len(self.ass_counters) > 1:
                assassination = " y ".join([", ".join(ass_names[0:-1]), ass_names[-1]])
            else:
                assassination = ass_names[0]
            report.append(f"***Recursos:*** {self.ducats} ducados. Fichas de asesinato: {assassination}")

            # Provincias controladas
            province_names = [p.name for k, p in map.provinces.items() if k in self.controlled_locations]
            if len(self.controlled_locations) == 0:
                provinces = "Ninguna"
            elif len(self.controlled_locations) > 1:
                provinces = " y ".join([", ".join(province_names[0:-1]), province_names[-1]])
            else:
                provinces = province_names[0]
            report.append(f"***Provincias controladas:*** {provinces}")

            # Rebeliones
            if self.rebelled_provinces or self.rebelled_cities:
                province_names = [p.name for k, p in map.provinces.items() if k in self.rebelled_provinces]
                city_names = [f"{p.name} (ciudad)" for k, p in map.provinces.items() if k in self.rebelled_cities]
                names = province_names + city_names
                
                if len(names) > 1:
                    provinces = " y ".join([", ".join(names[0:-1]), names[-1]])
                else:
                    provinces = names[0]
                
                report.append(f"***Rebeliones:*** {provinces}")
            
            # Ejércitos
            province_names = [p.name for k, p in map.provinces.items() if k in self.armies]
            if len(province_names) == 0:
                provinces = "Ninguno"
            elif len(province_names) > 1:
                provinces = " y ".join([", ".join(province_names[0:-1]), province_names[-1]])
            else:
                provinces = province_names[0]
            report.append(f"***Ejércitos:*** {provinces}")
            
            # Flotas
            province_names = [p.name for k, p in map.provinces.items() if k in self.fleets]
            if len(province_names) == 0:
                provinces = "Ninguna"
            elif len(province_names) > 1:
                provinces = " y ".join([", ".join(province_names[0:-1]), province_names[-1]])
            else:
                provinces = province_names[0]
            report.append(f"***Flotas:*** {provinces}")
            
            # Flotas
            province_names = [p.name for k, p in map.provinces.items() if k in self.garrisons]
            if len(province_names) == 0:
                provinces = "Ninguna"
            elif len(province_names) > 1:
                provinces = " y ".join([", ".join(province_names[0:-1]), province_names[-1]])
            else:
                provinces = province_names[0]
            report.append(f"***Guarniciones:*** {provinces}")
        else:
            report.append("Eliminado")

        return report

    @classmethod
    def load_players(cls, conn: sqlite3.Connection, game_id: int) -> list[Self]:
        """Busca y devuelve todos los jugadores asociados a un id de partida.

        Args:
            conn (sqlite3.Connection): Conexión activa a la BBDD.
            game_id (int): ID numérico de la partida en la base de datos.

        Returns:
            list[Player]: Lista de objetos Player instanciados.
        """
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT player_id, discord_id, controlled_locations, armies, fleets, garrisons,
                ass_counters, ducats, rebelled_provinces, rebelled_cities, home_countries, power
            FROM players WHERE game_id = ?
            """,
            (game_id,),
        )
        rows = cursor.fetchall()

        players = []
        for row in rows:
            locations = json.loads(row[2]) if row[2] else []
            armies = json.loads(row[3]) if row[3] else []
            fleets = json.loads(row[4]) if row[4] else []
            garrisons = json.loads(row[5]) if row[5] else []
            ass_counters = json.loads(row[6]) if row[6] else []
            rebelled_provinces = json.loads(row[8]) if row[8] else []
            rebelled_cities = json.loads(row[9]) if row[9] else []
            home_countries = json.loads(row[10]) if row[10] else []
            players.append(cls(
                player_id = row[0],
                discord_id = row[1],
                controlled_locations = locations,
                armies = armies,
                fleets = fleets,
                garrisons = garrisons,
                ass_counters = ass_counters,
                ducats = row[7],
                rebelled_provinces = rebelled_provinces,
                rebelled_cities = rebelled_cities,
                home_countries = home_countries,
                power = row[11]
            ))

        return players

@dataclass
class Game:
    """Representa una partida de Machiavelli.

    Attributes:
        name (str): El nombre descriptivo de la partida (ej. "Equilibrio de Poder I").
        channel_id (int): El identificador del canal de Discord.
        database_id (int | None): El ID autoincremental de la BBDD (None si es nueva).
        scenario_id (str | None): El identificador del escenario.
        turn_number (int): El número de turno actual de la partida. La partida se crea en el turn_number 0.
        weekly_deadline (str | None): La fecha semanal en la que se ejecutarán los turnos.
        next_deadline (str | None): La fecha en la que se ejecutará el siguiente turno.
        players (list[Player]): Lista de jugadores apuntados a la partida.
        scenario (Scenario | None): El escenario completo asociado a la partida.
        map (Map | None): El mapa de la partida.
        famine (list[str]): Identificadores de las provincias en que hay hambre.
        independent_garrisons (list[str]): Identificadores de las provincias en que hay guarniciones independientes.
        turn_events (list[str]): Eventos ocurridos durante el turno, para su publicación en el reporte.
    """

    name: str
    channel_id: int | None = None
    database_id: int | None = None
    scenario_id: str | None = None
    turn_number: int = 0
    weekly_deadline: str | None = None
    next_deadline: str | None = None
    players: list[Player] = field(default_factory=list)
    scenario: Scenario | None = None
    map: Map | None = None
    famine: list[str] = field(default_factory=list)
    independent_garrisons: list[str] = field(default_factory=list)
    turn_events: list[str] = field(default_factory=list)

    def save(self, conn: sqlite3.Connection) -> None:
        """Guarda el estado actual de la partida en la base de datos.
        
        Si no tiene database_id, la inserta como nueva. Si ya lo tiene, actualiza sus datos

        Raises:
            DuplicatedGameException: Si es una partida nueva y el nombre o canal ya existen.
        """
        cursor = conn.cursor()

        # Calcula los campos que vamos a guardar en la base de datos
        columns = [
            f.name for f in fields(self)
            if f.name not in (
                "database_id", "players", "scenario", "map", "famine", "independent_garrisons", "turn_events"
            )
        ]
        values = [getattr(self, col) for col in columns]

        famine_json = json.dumps(self.famine)
        columns.append("famine")
        values.append(famine_json)

        garrisons_json = json.dumps(self.independent_garrisons)
        columns.append("independent_garrisons")
        values.append(garrisons_json)

        # Partida nueva
        if self.database_id is None:
            try:
                placeholders = ", ".join(["?"] * len(columns))
                query = f"INSERT INTO games ({', '.join(columns)}) VALUES ({placeholders})"
                cursor.execute(query, tuple(values))
                self.database_id = cursor.lastrowid
            except sqlite3.IntegrityError as e:
                raise DuplicatedGameException(
                    "No se pudo crear la partida. "
                    f"El nombre '{self.name}' o el canal '{self.channel_id}' ya están en uso."
                ) from e
        # Actualizar
        else:
            set_clause = ", ".join([f"{col} = ?" for col in columns])
            query = f"UPDATE games SET {set_clause} WHERE id = ?"
            cursor.execute(query, tuple(values) + (self.database_id,))

        # Guardamos todos los elementos
        for player in self.players:
            player.save(conn, self.database_id)
        
        # Para los eventos refrescamos completamente la tabla
        cursor.execute("DELETE FROM game_events WHERE game_id = ?", (self.database_id,))
        if self.turn_events:
            payload = [(self.database_id, msg) for msg in self.turn_events]
            cursor.executemany("""
                INSERT INTO game_events (game_id, message) 
                VALUES (?, ?)
            """, payload)
    
    def report_status(self) -> list[str]:
        """Devuelve el estado actual de la partida.
        
        Este método devuelve una lista de strings, cada una de ellas una línea del estado.
        
        Returns:
            list(str): Estado actual de la partida.
        """
        report = [f"__**Partida**: {self.name}__"]

        report.append(f"**Escenario:** {self.scenario.name if self.scenario else 'Por definir'}.")

        report.append(f"**Horario de los turnos:** {self.weekly_deadline if self.weekly_deadline else 'Por definir'}.")

        report.append(f"**Próximo turno:** {self.next_deadline if self.next_deadline else 'Por definir'}.")

        if self.turn_number == 0:
            report.append("**Estado:** Por comenzar.")
            if self.players:
                players = ", ".join([f"<@{p.discord_id}> ({p.player_id})" for p in self.players])
                if self.scenario:
                    report.append(f"**Jugadores {len(self.players)}/{len(self.scenario.powers)}:** {players}")
                else:
                    report.append(f"**Jugadores {len(self.players)}:** {players}")
            else:
                report.append(f"**Jugadores:** Ninguno")

        return report
    
    def start_game(self) -> list[str]:
        """Comienza la partida.

        Antes de comenzar la partida, tendremos que haber seleccionado un escenario, añadido jugadores
        suficientes para ese escenario, y fijado las fechas de los turnos.

        Returns:
            list(str): Reporte de la ejecución.
        """
        # Comprobamos primero que se cumplan las condiciones
        message = None
        report = ["## __Iniciando partida...__"]

        if not self.scenario:
            message="No se seleccionó escenario"
        elif not self.weekly_deadline or not self.next_deadline:
            message="No se fijó la fecha de los turnos"
        elif len(self.players) != len(self.scenario.powers):
            message="El número de jugadores no coincide con el escenario"
        elif self.turn_number > 0:
            message="La partida ya está en curso"
        
        if message:
            report.append(f"No se pudo iniciar la partida: {message}")
            return report
        
        # Ahora la podemos comenzar
        try:
            report.extend(self.initial_setup())
            report.extend(self.spring_start())
        except:
            raise

        return report
    
    def run_game(self) -> list[str]:
        """Ejecuta un turno, genera el reporte y

        Antes de comenzar la partida, tendremos que haber seleccionado un escenario, añadido jugadores
        suficientes para ese escenario, y fijado las fechas de los turnos.

        Returns:
            list(str): Reporte de la ejecución.
        """
        # Clean up events
        self.turn_events = []

        self.turn_events.append(f"## __**{self.name}, turno {self.turn_number}**__")

        now = datetime.now().strftime("%d-%m-%Y %H:%M")
        last_date = datetime.fromisoformat(self.next_deadline)
        next_date = last_date + timedelta(weeks=1) 
        next_deadline = next_date.strftime("%d-%m-%Y %H:%M")
        self.turn_events.append(f"**Fecha:** {now}. **Próximo turno:** {next_deadline}")

        self.turn_events.append("## __**Eventos**__")

        if self.turn_number == 0:
            report = self.start_game()
        
        self.turn_number += 1
        last_date = datetime.fromisoformat(self.next_deadline)
        self.next_deadline = next_date.strftime("%Y-%m-%d %H:%M")

        report.append("## __**Turno completado**__")

        return report

    @classmethod
    def create_game(cls, name: str, channel_id: int, conn: sqlite3.Connection) -> Self:
        """Crea una nueva partida y la guarda en la base de datos.
        
        Args:
            name (str): Nombre de la partida.
            channel_id (int): El ID del canal de Discord de esta partida.
            conn (ssqlite3.Connectionql): Conexión activa a la base de datos.
        
        Returns:
            Self: La instancia de Game recién creada con su 'database_id ya asignado.
        
        Raises:
            DuplicatedGameException: Si el nombre de la partida o el canal de Discord ya existen.
        """
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO games (name, channel_id) VALUES (?, ?)",
                (name, channel_id),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicatedGameException(
                f"No se pudo crear la partida. El nombre '{name}' o el canal '{channel_id}' ya están en uso."
            ) from e

        db_id = cursor.lastrowid

        return cls(name=name, channel_id=channel_id, database_id=db_id)
    
    @classmethod
    def load_game(
        cls,
        conn: sqlite3.Connection,
        *,
        game_id: int | None = None,
        name: str | None = None,
        channel_id: int | None = None,
    ) -> Self:
        """Busca y carga una partida completa de la BBDD.

        El uso de '*' obliga a pasar los criterios de búsqueda como argumentos con nombre
        (ej: Game.load_game(conn, channel_id=12345)) para evitar confusiones.

        Raises:
            ValueError: Si no se proporciona ningún criterio de búsqueda.
            GameNotFoundException: Si la partida no existe en la base de datos.
        """
        cursor = conn.cursor()

        # Eliminamos las columnas quen no están en la base de datos
        columns = [
            f.name for f in fields(cls)
            if f.name not in ("database_id", "players", "scenario", "map", "turn_events")
        ]
        select_clause = ", ".join(["id"] + columns)

        if game_id is not None:
            cursor.execute(f"SELECT {select_clause} FROM games WHERE id = ?", (game_id,))
        elif name is not None:
            cursor.execute(f"SELECT {select_clause} FROM games WHERE name = ?", (name,))
        elif channel_id is not None:
            cursor.execute(f"SELECT {select_clause} FROM games WHERE channel_id = ?", (channel_id,))
        else:
            raise ValueError("Debes proporcionar al menos un criterio de búsqueda.")

        game_row = cursor.fetchone()
        if not game_row:
            raise GameNotFoundException("No se encontró ninguna partida.")

        # Mapeo dinámico de los datos primitivos
        init_kwargs = {columns[i]: game_row[i + 1] for i in range(len(columns))}

        # Parsea famine y garrisons (de JSON a list)
        famine = json.loads(init_kwargs["famine"]) if init_kwargs["famine"] else []
        garrisons = json.loads(init_kwargs["independent_garrisons"]) if init_kwargs["independent_garrisons"] else []

        init_kwargs["famine"] = famine
        init_kwargs["independent_garrisons"] = garrisons

        game = cls(**init_kwargs)

        # Cargamos los jugadores
        game.database_id = game_row[0]
        game.players = Player.load_players(conn, game_row[0])

        # Cargamos los eventos
        cursor.execute("SELECT message FROM game_events WHERE game_id = ? ORDER BY id ASC", (game.database_id,))
        
        game.turn_events = [row[0] for row in cursor.fetchall()]

        # Cargamos el escenario
        if game.scenario_id:
            game.scenario = Scenario.load_scenarios().get(game.scenario_id)
        else:
            game.scenario = None

        # Cargamos el mapa
        game.map = Map.load_map()

        if game.scenario_id:
            game.map.exclude_locations(game.scenario.excluded_locations)

        # Resultado
        return game

    # Game phases
    def initial_setup(self) -> list[str]:
        """Realiza todas las operaciones del setup inicial de la partida según el escenario.
        
        Estas acciones son:
        - Reparte las facciones al azar entre los jugadores
        - Asigna a cada jugador las provincias controladas y las unidades
        - Reparte recursos a cada jugador (fichas de asesinato principalmente)
        - Coloca guarniciones independientes en las ciudades fortificadas que no sean de ningún jugador

        Returns:
            list(str): Una lista con los mensajes generados en la operación.
        """
        report = []

        report.append("### __Setup inicial__")

        powers = self.scenario.powers.copy()
        random.shuffle(powers)

        garrisons = [k for k, p in self.map.provinces.items() if p.city == "fortified"]

        for player, power in zip(self.players, powers):
            report.append(f"<@{player.discord_id}> ({player.player_id}) dirigirá a {power.name}")
            # Asigna la potencia al jugador, junto con sus provincias y unidades.
            player.assign_power(power)
            # Asigna las fichas de asesinato
            player.ass_counters = [p.id for p in powers if p.id != power.id]
            # Elimina las guarniciones independientes de sus provincias
            garrisons = [p for p in garrisons if p not in power.controlled_provinces]
        
        self.independent_garrisons = garrisons

        return report
    
    def spring_start(self) -> list[str]:
        """Realiza las operaciones del inicio de la primavera.
        
        Estas acciones son:
        - Coloca marcadores de hambre
        - Calcula los ingresos

        Returns:
            list(str): Una lista con los mensajes generados en la operación.
        """
        report = []

        # Inicio de año
        year = self.scenario.year + self.turn_number // 4

        self.turn_events.append(f"__**Primavera de {year}**__")

        # El primer año no haremos tirada de hambre
        self.famine = []
        if self.scenario.rules.famine_active and self.turn_number > 0:
            self.turn_events.append("__**Fase de Hambre**__")

            report.append(f"### __Primavera de {self.scenario.year + self.turn_number // 4}: Hambre__")
            dice = random.randint(1, 6)
            famine = GameTables.disasters[dice-1]
            report.append(f"- **Fase de hambre**: 1d6 => {dice}. {famine[1]}")

            self.turn_events.append(f"***Hambre:*** {famine[1]}")
            famine_names = []

            # Fila
            if famine[0] in ['both', 'row']:
                dice = random.randint(1, 6) + random.randint(1, 6)
                row = GameTables.famine[dice - 2]
                provinces = {i: p for i, p in self.map.provinces.items() if i in row}
                self.famine.extend(provinces.keys())
                names = [v.name for v in provinces.values()]
                report.append(f"  * **Fila**: 2d6 => {dice}, **Hambre** en {', '.join(names)}")
                famine_names.extend(names)
            
            # Columna
            if famine[0] in ['both', 'column']:
                dice = random.randint(1, 6) + random.randint(1, 6)
                column = [r[dice - 2] for r in GameTables.famine]
                provinces = {i: p for i, p in self.map.provinces.items() if i in column}
                self.famine.extend(provinces.keys())
                names = [v.name for v in provinces.values()]
                report.append(f"  * **Columna**: 2d6 => {dice}, **Hambre** en {', '.join(names)}")
                famine_names.extend(names)
            
            if famine_names:
                joined_names = " y ".join([", ".join(famine_names[:-1]), famine_names[-1]])
                self.turn_events.append(f"***Provincias afectadas:*** {joined_names}")
            
        # Ingresos
        report.append(f"### __Primavera de {self.scenario.year + self.turn_number // 4}: Ingresos__")
        
        self.turn_events.append("__**Fase de Ingresos**__")

        for player in self.players:
            report.append(f"- {PowerDict[player.power]} (<@{player.discord_id}>)")

            # Ingresos fijos (provincias y mares)
            # Provincias controladas y ocupadas
            maybe_provinces = ({p for p in player.controlled_locations}
                | {p for p in player.armies}
                | {p.split()[0] for p in player.fleets})
            # Elimina las que tengan hambre o rebeliones
            provinces = [
                p for p in maybe_provinces
                if p not in self.famine
                if p not in player.rebelled_provinces
                if p not in player.rebelled_cities]
            province_income = len(provinces)
            
            # Ingresos fijos (ciudades). Las ciudades con hambre o rebeliones sí generan ingresos si tienen garrison
            maybe_cities = {
                p for p in player.controlled_locations
                if p not in self.famine
                if p not in player.rebelled_cities
                if p not in player.rebelled_provinces} | {p for p in player.garrisons}
            cities = [c for c in maybe_cities if self.map.provinces[c].city in ("city", "fortified")]
            city_income = sum(self.map.provinces[c].major_city for c in cities)

            report.append(f"  * **Ingresos fijos.** Por Provincias y Mares, {province_income} ducados. "
                f"Por Ciudades, {city_income} ducados")

            # Ingresos variables (home countries)
            hc_income = 0
            for hc in self.scenario.variable_income_home_countries:
                if hc in player.home_countries:
                    dice = random.randint(1, 6)
                    this_hc_income = GameTables.variable_income[hc][dice - 1]
                    report.append(
                        f"  * **Ingresos variables.** {PowerDict[hc]} (1d6 => {dice}), {this_hc_income} ducados")
                    hc_income += this_hc_income
            
            for p in self.scenario.variable_income_provinces:
                if p in player.controlled_locations:
                    dice = random.randint(1, 6)
                    this_hc_income = GameTables.variable_income[p][dice - 1]
                    report.append(
                        f"  * **Ingresos variables.** {self.map.provinces[p].name} (1d6 => {dice}), "
                        f"{this_hc_income} ducados")
                    hc_income += this_hc_income
            
            # Total ingresos
            total_income = province_income + city_income + hc_income
            player.ducats += total_income
            report.append(
                f"  * **Total ingresos.** {province_income} + {city_income} + {hc_income} = {total_income} ducados")
            
            self.turn_events.append(
                f"***{PowerDict[player.power]} (<@{player.discord_id}>):*** "
                f"Ingresos fijos: {province_income + city_income} ducados. "
                f"Variables: {hc_income} ducados. Total: {total_income} ducados."
            )

        return report

    def turn_report(self) -> list[str]:
        """Devuelve el informe del turno actual"""
        report = self.turn_events.copy()

        year = self.scenario.year + (self.turn_number - 1) // 4
        season_number = (self.turn_number - 1)  % 4
        season = ("Primavera (mantenimiento)", "Primavera (campaña)", "Verano", "Otoño")[season_number]

        report.append(f"## __**Informe de situación. {season} de {year}**__")

        if self.famine:
            names = [p.name for k, p in self.map.provinces.items() if k in self.famine]
            famine = " y ".join([", ".join(names[:-1]), names[-1]])
            report.append(f"***Hambre:*** {famine}")
        
        if self.independent_garrisons:
            names = [p.name for k, p in self.map.provinces.items() if k in self.independent_garrisons]
            if len(names) > 1:
                garrisons = " y ".join([", ".join(names[0:-1]), names[-1]])
            else:
                garrisons = ass_names[0]
            report.append(f"***Guarniciones independientes:*** {garrisons}")
        
        for p in self.players:
            report.extend(p.player_report(self.map))

        return report