# machiavelli/services/player_reporter.py

from __future__ import annotations

from typing import TYPE_CHECKING

from machiavelli.game.tables import GameTables

if TYPE_CHECKING:
    from machiavelli.game.player import Player


class PlayerReporter:
    """Responsable exclusivo de generar el informe de situación de un jugador."""

    @staticmethod
    def generate_report(player: Player) -> list[str]:
        """Genera las líneas del informe de situación para el jugador.

        Args:
            player (Player): La instancia del jugador a reportar.

        Returns:
            list[str]: Líneas formateadas del informe listas para renderizar.
        """
        game = player.game
        game_map = game.map
        if game_map is None:
            raise RuntimeError("La partida requiere un mapa cargado")
        besieges = game.besieges

        report = []
        power_name = (
            GameTables.powers.get(player.power, player.power)
            if player.power
            else "Desconocido"
        )
        report.append(f"### 🏰 __**{power_name} (<@{player.discord_id}>)**__")

        if player.home_countries:
            # Países natales
            hc_names = [GameTables.powers.get(p, p) for p in player.home_countries]
            hc = PlayerReporter._format_joined_names(hc_names, default="Ninguno")
            report.append(
                f"> 👑 **Naciones controladas ({len(player.home_countries)}):** {hc}"
            )

            # Recursos
            ass_names = [GameTables.powers.get(p, p) for p in player.ass_counters]
            assassination = PlayerReporter._format_joined_names(
                ass_names, default="Ninguna"
            )
            report.append(f"> 💰 **Recursos:** {player.ducats} ducados.")
            report.append(
                f"> 🗡️ **Fichas de asesinato ({len(ass_names)}):** {assassination}"
            )

            # Provincias controladas
            province_names = [
                p.name
                for k, p in game_map.provinces.items()
                if k in player.controlled_locations
            ]
            provinces_str = PlayerReporter._format_joined_names(
                province_names, default="Ninguna"
            )

            # Ciudades
            cities = [
                c
                for c in player.controlled_locations
                if game_map.provinces[c].city in ("city", "fortified")
            ]

            report.append(
                f"> 🗺️ **Provincias controladas ({len(player.controlled_locations)} "
                f"provincias, {len(cities)} ciudades):** {provinces_str}"
            )

            # Rebeliones
            if player.rebelled_provinces or player.rebelled_cities:
                prov_rebel_names = [
                    p.name
                    for k, p in game_map.provinces.items()
                    if k in player.rebelled_provinces
                ]
                city_rebel_names = [
                    f"{p.name} (ciudad)"
                    for k, p in game_map.provinces.items()
                    if k in player.rebelled_cities
                ]
                rebel_names = prov_rebel_names + city_rebel_names
                rebel_str = PlayerReporter._format_joined_names(
                    rebel_names, default="Ninguna"
                )
                report.append(f"> 🔥 **Rebeliones:** {rebel_str}")

            # Ejércitos
            army_names = [
                f"{p.name} (asediando)" if k in besieges else p.name
                for k, p in game_map.provinces.items()
                if k in player.armies
            ]
            army_str = PlayerReporter._format_joined_names(
                army_names, default="Ninguno"
            )
            report.append(f"> ⚔️ **Ejércitos:** {army_str}")

            # Flotas
            locations = game_map.provinces | game_map.seas
            fleet_names = [
                f"{p.name} (asediando)" if k in besieges else p.name
                for k, p in locations.items()
                if k in player.fleets
            ]
            fleet_str = PlayerReporter._format_joined_names(
                fleet_names, default="Ninguna"
            )
            report.append(f"> ⚓ **Flotas:** {fleet_str}")

            # Guarniciones
            garrison_names = [
                p.name for k, p in game_map.provinces.items() if k in player.garrisons
            ]
            garrison_str = PlayerReporter._format_joined_names(
                garrison_names, default="Ninguna"
            )
            report.append(f"> 🛡️ **Guarniciones:** {garrison_str}")
        else:
            report.append("> ❌ **Eliminado**")

        return report

    @staticmethod
    def _format_joined_names(names: list[str], default: str) -> str:
        """Formatea una lista de nombres con comas y la conjunción 'y' en español."""
        if not names:
            return default
        if len(names) == 1:
            return names[0]
        return " y ".join([", ".join(names[:-1]), names[-1]])
