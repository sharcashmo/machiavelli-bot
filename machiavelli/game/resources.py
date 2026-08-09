"""Utilidades para cargar recursos JSON incluidos en el paquete de Machiavelli."""

from __future__ import annotations

import json
from importlib.resources import files


class PackageResourceError(RuntimeError):
    """Se lanza cuando no se puede leer o decodificar un recurso JSON empaquetado."""


def read_package_json(filename: str) -> object:
    """Lee y decodifica un recurso JSON UTF-8 del paquete instalado.

    Parámetros:
        filename: Nombre del recurso relativo al paquete de nivel superior
            ``machiavelli``.

    Excepciones:
        PackageResourceError: Si el recurso falta, no se puede leer o contiene
            JSON no válido.
    """
    resource = files("machiavelli").joinpath(filename)

    try:
        with resource.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except json.JSONDecodeError as exc:
        raise PackageResourceError(
            f"El recurso JSON del paquete {filename!r} no contiene JSON válido"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise PackageResourceError(
            f"No se pudo leer el recurso JSON del paquete {filename!r}"
        ) from exc
