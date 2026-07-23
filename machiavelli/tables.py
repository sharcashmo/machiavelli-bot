# machiavelli/tables.py

class GameTables:
    """Tablas del juego."""
    variable_income = {
        "A": [1, 2, 3, 3, 4, 4],
        "L": [1, 2, 3, 3, 4, 5],
        "F": [1, 2, 3, 4, 5, 6],
        "M": [2, 3, 3, 4, 4, 5],
        "N": [1, 2, 2, 3, 3, 4],
        "P": [2, 2, 3, 4, 5, 6],
        "T": [1, 2, 3, 4, 5, 6],
        "V": [2, 3, 3, 4, 4, 5],
        "genoa": [1, 2, 2, 3, 3, 4],
        "milan": [2, 3, 3, 4, 4, 5],
        "naple": [1, 2, 2, 3, 3, 4],
        "rome": [2, 2, 3, 4, 5, 6]
    }

    assassination_rebellions = [1, 2, 3, 5]

    expenses = {
        "A": {
            "text": "Paliar hambruna",
            "target_type": "province",
            "cost": 3
        },
        "B": {
            "text": "Pacificar rebelión",
            "target_type": "province",
            "cost": 12
        },
        "C": {
            "text": "Comenzar rebelión en provincia no natal",
            "target_type": "province",
            "cost": 9
        },
        "D": {
            "text": "Comenzar rebelión en provincia natal",
            "target_type": "province",
            "cost": 15
        },
        "E": {
            "text": "Ordenar asesinato",
            "target_type": "power",
            "cost": 12
        },
        "F": {
            "text": "Contra-soborno",
            "target_type": "unit",
            "cost": 3
        },
        "G": {
            "text": "Desbandar guarnición autónoma",
            "target_type": "unit",
            "cost": 6
        },
        "H": {
            "text": "Comprar guarnición autónoma",
            "target_type": "unit",
            "cost": 9
        },
        "I":  {
            "text": "Convertir guarnición en autónoma",
            "target_type": "unit",
            "cost": 9
        },
        "J":  {
            "text": "Desbandar unidad",
            "target_type": "unit",
            "cost": 12
        },
        "K":  {
            "text": "Comprar Ejército o Flota",
            "target_type": "unit",
            "cost": 18
        }
    }

    disasters = [
        ("no", "Año excelente (sin desastre)"),
        ("row", "Buen año (solo fila)"),
        ("row", "Buen año (solo fila)"),
        ("column", "Buen año (solo columna)"),
        ("column", "Buen año (solo columna)"),
        ("both", "Mal año (fila y columna)")
    ]

    powers = {
        "M": "Milan",
        "V": "Venice",
        "L": "Florence",
        "N": "Naples",
        "P": "Papacy",
        "F": "France",
        "T": "Turks",
        "A": "Austria",
    }

    actors = {
        "A": "Ejército",
        "F": "Flota",
        "G": "Guarnición",
        "E": "Gasto"
    }

    military_orders = {
        "A": {
            "text": "Avanzar a Provincia o Mar",
            "target_type": "location"
        },
        "B": {
            "text": "Asediar Ciudad",
            "target_type": None
        },
        "H": {
            "text": "Mantener",
            "target_type": None
        },
        "L": {
            "text": "Levantar asedio",
            "target_type": None
        },
        "S": {
            "text": "Apoyar Provincia o Mar",
            "target_type": "location_ext"
        },
        "T": {
            "text": "Transportar Ejército",
            "target_type": "army_ext"
        },
        "C": {
            "text": "Convertir o desbandar",
            "target_type": "unit_type"
        }
    }

    maintenance_orders = {
        "M": {
            "text": "Mantener",
            "target_type": None
        },
        "D": {
            "text": "Desbandar",
            "target_type": None
        },
        "R": {
            "text": "Reclutar",
            "target_type": None
        }
    }

    famine = [
        [None, None, "prove", "patri", "moden", None, "corsi", "ancon", None, "perug", None],
        [None, "piomb", None, None, None, None, None, "tunis", None, None, "paler"],
        ["tivol", None, "otran", "padua", "swiss", "cremo", "pontr", None, "herze", None, None],
        ["friul", None, "bolog", "saler", "veron", "austr", "milan", "sienn", None, None, "duraz"],
        ["marse", "ragus", "vicen", "carin", "berga", "pisto", "spole", None, "pianc", "hunga", None],
        [None, "bari", "slavo", "montf", "urbin", "forno", None, "como", "trent", None, None],
        ["ferra", None, "rome", "pavia", None, None, "arezz", "bresc", "saluz", "alban", "genoa"],
        [None, None, "croat", None, "flore", "turin", "mantu", "capua", "trevi", None, None],
        ["savoy", None, "sardi", None, "parma", "bosni", "tyrol", None, "naples", "romag", "dalma"],
        [None, None, "venic", None, None, None, None, "carni", None, "messi", None],
        [None, None, None, "pisa", "aquil", "avign", "lucca", None, "istri", None, None]
    ]

    plague = [
        ["vicen", "swiss", None, None, "carni", None, None, None, None, "montf", "capua"],
        ["pontr", "bosni", "slavo", None, None, None, "croat", None, "tivol", "bari", "tyrol"],
        ["savoy", None, None, "friul", None, "rome", None, "marse", "pavia", None, None],
        [None, "saler", "veron", None, "dalma", "lucca", "bolog", "carin", "prove", None, None],
        [None, None, "turin", "sienn", "messi", "padua", "austr", "ferra", None, "ragus", None],
        ["paler", None, "genoa", "alban", "pisa", "tunis", "avign", "milan", None, None, "sardi"],
        ["duraz", None, "naple", "moden", "perug", "cremo", "venic", "flore", None, None, None],
        [None, "berga", "ancon", "parma", None, None, None, None, "mantu", "istri", None],
        ["romag", "hunga", None, "urbin", None, None, "piomb", None, "trevi", None, "como"],
        ["pianc", "forno", None, None, "pisto", None, None, "otran", None, "aquil", "spole"],
        ["trent", "herze", None, "bresc", None, "arezz", None, "corsi", None, "patri", "saluz"]
    ]