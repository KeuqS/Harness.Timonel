#!/usr/bin/env python3
"""Verificacion del plugin con una sola salida: PASSED, FAILED o no se.

Corre los tres pasos de "Verificacion antes de commit" de `CLAUDE.md` sobre el
cwd y los corre SIEMPRE los tres, aunque uno falle (#270):

    python3 scripts/verificar_plugin.py

Pasos, en orden fijo:
    unittest  `python -m unittest discover -s tests` (reutiliza
              `mutar_literal.correr_unittest` y `parsear_unittest`)
    bash -n   `["bash", "-n", <archivo>]` por cada `scripts/*.sh` y `.githooks/*`
    jq        `["jq", ".", <archivo>]` sobre `.claude-plugin/plugin.json`,
              `.claude-plugin/marketplace.json` y `hooks/hooks.json`

Salida: una linea por paso (`unittest: PASSED (Ran M tests)`,
`bash -n: FAILED (<archivo>)`, `jq: no sé (<motivo>)`) y una linea final. Tres
respuestas, nunca dos (`heuristics/general/sensor-declara-su-evidencia.md`): un
paso que no pudo correr (herramienta ausente, timeout, salida sin `Ran M tests`)
es "no sé", jamas verde.

Linea final y codigos de salida:
    0   PASSED                         los tres pasos con evidencia verde
    1   FAILED: <pasos rojos>          >=1 paso con evidencia roja (gana sobre
                                       "no sé"; el paso sin evidencia igual se
                                       imprime en su linea)
    2   no sé: <paso: motivo; ...>     ningun rojo y >=1 paso sin evidencia
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from mutar_literal import NoSe, correr_unittest, parsear_unittest

TIMEOUT_S = 60
JSON_FIJOS = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "hooks/hooks.json",
)

# Motivos del "no sé" (exit 2). Lookup map en vez de mensajes sueltos.
MOTIVOS = {
    "timeout_unittest": "unittest superó el timeout",
    "salida_no_parseable": "la salida de unittest no trae `Ran M tests`",
    "sin_tests": "unittest no recolectó ningún test (Ran 0)",
    "bash_no_ejecutable": "no se pudo ejecutar `bash` (no está en el PATH o sin permiso)",
    "jq_no_ejecutable": "no se pudo ejecutar `jq` (no está en el PATH o sin permiso)",
    "timeout_bash": f"`bash -n` superó el timeout de {TIMEOUT_S} s",
    "timeout_jq": f"`jq` superó el timeout de {TIMEOUT_S} s",
    "sin_archivos_bash": "no hay archivos en `scripts/*.sh` ni `.githooks/*` para `bash -n`",
}


class Paso(NamedTuple):
    nombre: str
    estado: str  # "PASSED" | "FAILED" | "no sé"
    detalle: str


def _paso_unittest(raiz: Path) -> Paso:
    try:
        fallos, total = parsear_unittest(correr_unittest(raiz, None), False)
    except NoSe as e:
        clave = "timeout_unittest" if e.clave == "timeout" else e.clave
        return Paso("unittest", "no sé", MOTIVOS[clave])
    if fallos:
        return Paso("unittest", "FAILED", f"{fallos} fallos de {total} tests")
    return Paso("unittest", "PASSED", f"Ran {total} tests")


def _archivos_bash(raiz: Path) -> list[Path]:
    candidatos = [*raiz.glob("scripts/*.sh"), *raiz.glob(".githooks/*")]
    return sorted(p for p in candidatos if p.is_file())


def _correr_por_archivo(
    nombre: str, comando: list[str], herramienta: str, archivos: list[Path], raiz: Path,
) -> Paso:
    rojos: list[str] = []
    sin_evidencia: str | None = None
    for archivo in archivos:
        try:
            r = subprocess.run(
                [*comando, str(archivo)], cwd=raiz, capture_output=True, text=True, timeout=TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            sin_evidencia = f"timeout_{herramienta}"
            break
        except OSError:
            sin_evidencia = f"{herramienta}_no_ejecutable"
            break
        if r.returncode != 0:
            rojos.append(str(archivo.relative_to(raiz)))
    if rojos:
        return Paso(nombre, "FAILED", ", ".join(rojos))
    if sin_evidencia:
        return Paso(nombre, "no sé", MOTIVOS[sin_evidencia])
    return Paso(nombre, "PASSED", f"{len(archivos)} archivos")


def _paso_bash(raiz: Path) -> Paso:
    archivos = _archivos_bash(raiz)
    if not archivos:
        return Paso("bash -n", "no sé", MOTIVOS["sin_archivos_bash"])
    return _correr_por_archivo("bash -n", ["bash", "-n"], "bash", archivos, raiz)


def _paso_jq(raiz: Path) -> Paso:
    return _correr_por_archivo("jq", ["jq", "."], "jq", [raiz / j for j in JSON_FIJOS], raiz)


def _veredicto(pasos: list[Paso]) -> tuple[str, int]:
    rojos = [p.nombre for p in pasos if p.estado == "FAILED"]
    sin_evidencia = [f"{p.nombre}: {p.detalle}" for p in pasos if p.estado == "no sé"]
    if rojos:
        return f"FAILED: {', '.join(rojos)}", 1
    if sin_evidencia:
        return f"no sé: {'; '.join(sin_evidencia)}", 2
    return "PASSED", 0


def main() -> int:
    raiz = Path.cwd()
    pasos = [_paso_unittest(raiz), _paso_bash(raiz), _paso_jq(raiz)]
    for p in pasos:
        print(f"{p.nombre}: {p.estado} ({p.detalle})")
    linea, codigo = _veredicto(pasos)
    print(linea)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
