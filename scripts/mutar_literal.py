#!/usr/bin/env python3
"""Mutacion de un literal en una copia del repo: cuantos tests lo notan.

Quien verifica una invariante "por mutacion" quita (o mueve fuera de su
seccion) el literal que la sostiene y mira si el test falla. Hacerlo a mano
repite el trabajo (#224 lo hizo tres veces en un ciclo) y deja pasar el caso
peor: reportar "verificado por mutacion" sobre un archivo que nunca cambio
(#124). Este script lo hace en una copia temporal del cwd, comprueba que la
mutacion entro y corre unittest en la copia. El arbol original no se toca.

Tres respuestas, nunca dos (`heuristics/general/sensor-declara-su-evidencia.md`):
hay conteo (`fallos: N de M`, exit 0) o hay "no se" (exit 2). Una mutacion que
no entra, un objetivo que no recolecta tests o una salida no parseable jamas se
reportan como `fallos: 0 de M`: ese 0 diria "invariante vacua" sin haberla
probado.

    python3 scripts/mutar_literal.py --ruta agents/x.md --literal "texto" \\
        --modo borrar [--test tests.modulo.Clase]
    python3 scripts/mutar_literal.py --ruta x.md --literal "texto" \\
        --modo mover --a "## Notas"

Modos (la unidad es la linea; se mutan TODAS las que contienen el literal):
    borrar  quita el literal de cada linea que lo contiene
    mover   las lineas con el literal fuera de la seccion `--a` se quitan y se
            reinsertan al final de esa seccion. `--a` es la linea completa del
            encabezado (`## Notas`, no un prefijo ni un titulo sin `#`).
            Seccion = desde el encabezado
            hasta el siguiente de nivel <= al suyo, o fin de archivo.

Sin `--test` corre `unittest discover -s tests` en la copia.

Stdout: `lineas mutadas: K` y `fallos: N de M`.

Codigos de salida:
    0   hubo conteo `fallos: N de M` (con la mutacion verificada)
    2   no se (stderr: `no sé: <motivo>`, uno de `MOTIVOS`): mutacion que no
        entro, `--a` que no es un encabezado `#+ `, seccion destino
        inexistente, copia o lectura imposible (OSError, shutil.Error, archivo
        no UTF-8), objetivo sin tests o inexistente, salida no parseable,
        timeout, o argumentos invalidos (coincide con el exit de uso de
        argparse). No hay exit 1: ningun "no pude medir" termina en traceback.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_S = 600
IGNORADOS_EN_CUALQUIER_DIR = {".git", "__pycache__", ".timonel"}

# Motivos del "no se" (exit 2). Lookup map en vez de mensajes sueltos.
MOTIVOS = {
    "literal_invalido": "--literal debe ser no vacío y de una sola línea",
    "ruta_invalida": "--ruta debe ser un archivo existente, relativo al repo y sin `..`",
    "mover_sin_destino": "--modo mover exige --a",
    "mutacion_no_aplicada": "la mutación no se aplicó (el literal no está fuera del destino o la copia quedó idéntica)",
    "destino_no_es_encabezado": "--a debe ser un encabezado markdown completo (`#+ ` seguido del título)",
    "seccion_destino_inexistente": "sección destino no existe en la ruta",
    "objetivo_no_encontrado": "objetivo de test no encontrado",
    "sin_tests": "el objetivo no recolectó ningún test",
    "salida_no_parseable": "la salida de unittest no trae `Ran M tests`",
    "copia_o_lectura_imposible": "no se pudo copiar el repo o leer/escribir la ruta (symlink roto, permisos o archivo no UTF-8)",
    "timeout": f"unittest superó el timeout de {TIMEOUT_S} s",
}


class NoSe(Exception):
    """El sensor no puede responder: lleva la clave de `MOTIVOS`."""

    def __init__(self, clave: str):
        super().__init__(MOTIVOS[clave])
        self.clave = clave


def _nivel(linea: str) -> int:
    m = re.match(r"(#+)\s", linea)
    return len(m.group(1)) if m else 0


def rango_de_seccion(lineas: list[str], encabezado: str) -> tuple[int, int] | None:
    """(inicio, fin) de la seccion: del encabezado al siguiente de nivel <= al
    suyo (exclusivo) o fin de archivo. Encabezado anclado por igualdad de linea
    completa (`## N` no ancla en `## Nueva`)."""
    nivel = _nivel(encabezado)
    for i, linea in enumerate(lineas):
        if linea.rstrip("\n") == encabezado:
            for j in range(i + 1, len(lineas)):
                if 0 < _nivel(lineas[j]) <= nivel:
                    return i, j
            return i, len(lineas)
    return None


def _fuera_del_destino(lineas: list[str], literal: str, destino: str) -> list[int]:
    rango = rango_de_seccion(lineas, destino)
    if rango is None:
        raise NoSe("seccion_destino_inexistente")
    return [i for i, l in enumerate(lineas) if literal in l and not rango[0] <= i < rango[1]]


def _borrar(lineas: list[str], literal: str, destino: str | None) -> list[str]:
    return [linea.replace(literal, "") for linea in lineas]


def _mover(lineas: list[str], literal: str, destino: str | None) -> list[str]:
    indices = set(_fuera_del_destino(lineas, literal, destino or ""))
    movidas = [lineas[i] if lineas[i].endswith("\n") else lineas[i] + "\n" for i in sorted(indices)]
    resto = [l for i, l in enumerate(lineas) if i not in indices]
    inicio, fin = rango_de_seccion(resto, destino or "") or (0, len(resto))
    corte = fin
    while corte > inicio + 1 and not resto[corte - 1].strip():
        corte -= 1
    if not resto[corte - 1].endswith("\n"):
        resto[corte - 1] += "\n"
    return resto[:corte] + movidas + resto[corte:]


MODOS = {"borrar": _borrar, "mover": _mover}


def mutar(texto: str, literal: str, modo: str, destino: str | None) -> tuple[str, int]:
    """Aplica la mutacion y la verifica. Devuelve (texto_mutado, lineas_mutadas).
    Lanza NoSe si el literal no esta, la seccion no existe o la copia no cambio."""
    lineas = texto.splitlines(keepends=True)
    contadores = {
        "borrar": lambda: [i for i, l in enumerate(lineas) if literal in l],
        "mover": lambda: _fuera_del_destino(lineas, literal, destino or ""),
    }
    afectadas = len(contadores[modo]())
    if afectadas == 0:
        raise NoSe("mutacion_no_aplicada")
    nuevo = "".join(MODOS[modo](lineas, literal, destino))
    # Alcanzable: si el literal es el encabezado que sigue al destino y esta al
    # final del archivo, se quita y se reinserta en el mismo sitio (texto igual).
    if nuevo == texto:
        raise NoSe("mutacion_no_aplicada")
    return nuevo, afectadas


def parsear_unittest(salida: str, con_objetivo: bool) -> tuple[int, int]:
    """(fallos N, tests M) desde la salida de unittest, o NoSe."""
    ran = re.search(r"^Ran (\d+) tests?", salida, re.MULTILINE)
    if ran is None:
        raise NoSe("salida_no_parseable")
    total = int(ran.group(1))
    if total == 0:
        raise NoSe("sin_tests")
    if con_objetivo and "unittest.loader._FailedTest" in salida:
        raise NoSe("objetivo_no_encontrado")
    fallos = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", salida))
    return fallos, total


def _ignorar(origen: Path):
    def ignorar(directorio: str, nombres: list[str]) -> set[str]:
        rel = Path(directorio).resolve().relative_to(origen)
        return {
            n for n in nombres
            if n in IGNORADOS_EN_CUALQUIER_DIR
            or n.endswith(".pyc")
            or (rel == Path(".claude") and n == "worktrees")
        }
    return ignorar


def correr_unittest(copia: Path, objetivo: str | None) -> str:
    cmd = [sys.executable, "-m", "unittest"] + (
        [objetivo] if objetivo else ["discover", "-s", "tests"]
    )
    try:
        r = subprocess.run(
            cmd, cwd=copia, capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        raise NoSe("timeout")
    return r.stdout + r.stderr


def _validar(args: argparse.Namespace) -> None:
    if not args.literal or "\n" in args.literal or "\r" in args.literal:
        raise NoSe("literal_invalido")
    ruta = Path(args.ruta)
    if ruta.is_absolute() or ".." in ruta.parts or not (Path.cwd() / ruta).is_file():
        raise NoSe("ruta_invalida")
    if args.modo == "mover" and not args.a:
        raise NoSe("mover_sin_destino")
    if args.modo == "mover" and not re.match(r"#+ \S", args.a):
        raise NoSe("destino_no_es_encabezado")


def medir(args: argparse.Namespace) -> tuple[int, int, int]:
    """(lineas_mutadas, fallos, tests). Lanza NoSe."""
    _validar(args)
    origen = Path.cwd().resolve()
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "copia"
        archivo = copia / args.ruta
        try:
            shutil.copytree(origen, copia, ignore=_ignorar(origen))
            original = archivo.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise NoSe("copia_o_lectura_imposible")
        nuevo, k = mutar(original, args.literal, args.modo, args.a)
        try:
            archivo.write_text(nuevo, encoding="utf-8")
        except OSError:
            raise NoSe("copia_o_lectura_imposible")
        salida = correr_unittest(copia, args.test)
        fallos, total = parsear_unittest(salida, args.test is not None)
    return k, fallos, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muta un literal en una copia del repo y cuenta los tests que fallan.")
    parser.add_argument("--ruta", required=True)
    parser.add_argument("--literal", required=True)
    parser.add_argument("--modo", required=True, choices=sorted(MODOS))
    parser.add_argument("--a")
    parser.add_argument("--test")
    args = parser.parse_args(argv)
    try:
        k, fallos, total = medir(args)
    except NoSe as e:
        print(f"no sé: {e}", file=sys.stderr)
        return 2
    print(f"lineas mutadas: {k}")
    print(f"fallos: {fallos} de {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
