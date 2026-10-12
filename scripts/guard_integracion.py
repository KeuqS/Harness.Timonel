#!/usr/bin/env python3
"""Bloquea comandos que crean integracion nueva sin pasar por PR (epica #79, #82).

La politica de integracion por PR (TIM-ADR-0005, #80) dice que el merge a la
rama base lo hace un humano al aprobar el PR. Este script es el guard
`PreToolUse` que la hace cumplir: bloquea `git push` a la rama base, `git
merge`/`git pull`/`git rebase`/`git reset` que integrarian o moverian la base
a algo que nadie reviso estando parado en ella, `git cherry-pick`/`git
revert`/`git am` que crean un commit directo en la base sin pasar por PR, y
`gh pr merge`/`gh api .../merge(s)` siempre.

Criterio de frontera (no confundir con "en que rama estoy parado"): un comando
NO se juzga por la rama actual sino por si PUEDE CREAR INTEGRACION NUEVA.
  - `push`: el destino del refspec, no la rama actual. `git push -u origin
    hu/N-slug` (Fase 6.5, abre el PR) nunca se bloquea.
  - `merge`/`pull`/`rebase` estando en una rama base: se permite SOLO si la
    ref es vacia o el equivalente remoto de esa misma base (`origin/<base>`,
    `@{u}`, `@{upstream}`, o `origin <base>` en `pull`). `--ff-only` por si
    solo NO alcanza para `merge`/`pull`: `git merge --ff-only hu/N-x` en
    `main` es el agujero de la HU #30 y sigue bloqueado. Las formas de
    control de `rebase` (`--continue`, `--abort`, `--skip`, `--quit`) se
    permiten siempre, no integran nada nuevo.
  - `cherry-pick`/`revert`/`am` estando en una rama base: bloqueados siempre
    (misma familia -- los tres crean un commit nuevo directo en la base sin
    pasar por PR, no hay equivalente de ref segura); sus formas de control
    (`--continue`, `--abort`, `--skip`, `--quit`) se permiten siempre.
  - `reset` estando en una rama base: se permite si la ref es vacia, el
    equivalente remoto de esa misma base (mismo criterio de ref segura que
    `merge`/`rebase`/`pull`), o `HEAD` exacto -- a diferencia del resto de la
    familia, en `reset` (y solo ahi) `HEAD` no mueve el ref de la rama a nada
    nuevo (`HEAD~1`, `HEAD^` y `HEAD@{1}` si mueven, y siguen bloqueados).
    Tambien se permite la forma con pathspec (`git reset [<ref>] [--]
    <paths>...`): si aparece `--` o hay mas de un argumento no-flag, es un
    unstage y nunca mueve el ref sin importar cual sea la ref. `git reset
    --hard origin/main` en `main` sincroniza y se permite; `git reset --hard
    hu/x` mueve `main` a codigo no revisado y bloquea. Fuera de esos casos
    aplica a cualquier modo (`--hard`, `--soft`, `--mixed`): lo que importa es
    la ref, no el modo -- `git reset --soft HEAD~1` en la base tambien
    bloquea porque `HEAD~1` no es una ref segura.
  - `gh pr merge`: siempre bloqueado, el merge del PR es de un humano.
  - `gh api` hacia una ruta cuyo segmento final (quitando el query string y
    la barra final) es `merge` o `merges`: siempre bloqueado sin importar el
    metodo -- `/merges` es la API *Merge a branch*, tan directa como `/merge`
    (patron simple y deliberado sobre la ruta, no un parser de metodos
    HTTP).

Flags globales de `git` (los que van ANTES del subcomando, p. ej. `git
--no-pager push ...`): la regla es "todo token que empiece con `-` entre
`git` y el subcomando es un flag global", no una lista cerrada. Los que
llevan valor SEPARADO (`-C`, `-c`, `--git-dir`, `--work-tree`, `--namespace`,
`--exec-path`) consumen ademas el token siguiente, salvo que el valor venga
pegado al mismo token (`-Cpath`, `-cclave=valor`, `--git-dir=valor`).
Cualquier otro flag -- booleano, conocido o no (`--no-pager`, `--paginate`,
`-P`, `--bare`, `--literal-pathspecs`, ...) -- consume solo su propio token:
la regla los cubre a todos sin necesidad de enumerarlos.

Rama por segmento (#229): `-C <dir>` y `cd <dir> &&` NO son solo ruido que se
salta -- dicen DONDE corre el segmento. `decidir()` acepta un `rama_de(dir) ->
rama | None` inyectable y juzga cada segmento contra la rama del repo donde
corre (`_dir_de_segmento` resuelve el directorio efectivo), no contra la del
cwd de la sesion: un `git -C <worktree> push origin hu/N-slug` desde una
sesion parada en `main` ya no se bloquea como push de `main`. La rama
simulada (`git checkout` entre segmentos) se lleva POR directorio. Sin
`rama_de` el comportamiento es el de siempre (todo corre en la rama de la
sesion). NO se resuelven -- caen a la rama de la sesion y dejan fail-open
`rama-no-resuelta` -- `cd` sin argumento, `cd -`, `~`, `$VAR`/`$(...)`,
`--git-dir`/`--work-tree`, un `rama_de` que devuelve None (dir inexistente,
`git` falla) y cualquier `cd` en un comando con subshell `(`/`)` (decision
aprobada: no se modela el alcance del subshell). Mismo criterio conservador
para `|` y `&` simple: en bash un `cd` en pipeline o en background corre en un
subshell y no cambia el dir de los segmentos siguientes, asi que en un comando
que contiene `|` o `&` (no `&&`/`||`) ningun `cd` se aplica y los segmentos
siguen en la rama de la sesion (ante la duda, bloquea como antes).

Uso(stdin = payload de un hook PreToolUse de Claude Code):
    echo '{"tool_input": {"command": "git push origin main"}}' | python3 guard_integracion.py

Sale 2 con el mensaje en stderr si bloquea, 0 si lo permite. Ante cualquier
error inesperado (JSON invalido, `shlex` invalido, `git`/config ilegibles)
sale 0: fail-open deliberado, preferible que se cuele un merge -reversible-
a que el harness se trabe. La unica proteccion incondicional (no fail-open)
es `git push --force`, y esa vive inline en `hooks/hooks.json`, no aqui.
Todo fail-open deja rastro auditable en `.timonel/events.log` (linea `<hora>
guard-integracion-fail-open <motivo>`): nunca es silencioso.

Limitacion conocida y aceptada (revision #2 de #82; #229 retiro de ella el
cwd-de-la-sesion como unica rama: ahora `-C`/`cd` se resuelven, ver "Rama por
segmento"): el guard cubre TODAS las
formas naturales de escribir el comando -- `;`, `&&`, `||`, salto de linea
(`\n`/`\r\n`, la forma en que Bash entrega comandos multilinea todo el
tiempo), `&` de fondo, `|` (pipe simple) y agrupacion `(...)` -- pero NO
persigue la evasion deliberada: `eval "git merge hu/x"` y `bash -c "git merge
hu/x"` quedan PERMITIDOS a proposito y no se les agrega codigo. Con `$(...)`
pasa algo mas preciso, no "permitido a secas": lo de ADENTRO del `$()` no se
inspecciona, pero lo que queda AFUERA se juzga normalmente contra la rama
real. Por eso `$(git checkout main) && git merge hu/x` parado en una base
BLOQUEA (el `&& git merge hu/x` es visible fuera del command substitution);
lo que NO se cubre es `$()` envolviendo el comando ENTERO. Un guard de hook
no puede ser un sandbox: quien quiera evadirlo siempre va a poder, y cada
capa nueva de parser para perseguir estas formas es superficie nueva de bug
(la propia razon de ser de este modulo: el criterio de frontera importa mas
que acumular parsers).

Criterio de alcance de la tabla (ronda final de #82, cierre de la lista): un
vector nuevo entra a este guard si se arregla agregando FILAS a
`MANEJADORES`/la tabla de lookup -- datos, no codigo nuevo de parseo.
Reinterpretacion aprobada por el usuario en #229: esa regla nacio para no
perseguir evasiones. La resolucion de rama por segmento corrige un FALSO
POSITIVO (bloquear un push legitimo desde un worktree) y lo hace con una
funcion nueva y aparte (`_dir_de_segmento`), sin tocar `_normalizar_separadores`,
`_indice_subcomando_git` ni `_clave_y_resto`; no persigue ninguna evasion. Si
arreglarlo pide tocar el parser (`_normalizar_separadores`,
`_indice_subcomando_git`, `_clave_y_resto`), NO entra: cada capa nueva de
parser es superficie nueva de bug, no una mejora gratis -- ya paso en esta
misma HU, el arreglo del pre-parser de separadores introdujo un caso
explotable (comilla escapada + `&&` sin espacio) que solo aparecio al
escribir sus tests (ver caso 66 en `tests/test_hooks_integracion.py`).

La proteccion REAL de la rama base es server-side (branch protection en
GitHub, #85), no este guard. Este guard es defensa en profundidad contra el
error honesto y contra que el propio orquestador se autoautorice un merge sin
PR: atrapa la equivocacion, no al adversario. No debe pretender ser un
sandbox ni perseguir cada evasion deliberada (ver limitacion de arriba).

Seam publico adicional (#176): `segmentar(cmd) -> list[list[str]] | None`
envuelve la misma tokenizacion de arriba (`_normalizar_separadores` +
`shlex.split` + `_partir_en_segmentos`) para que `scripts/detectar_commit.py`
reconozca si un comando invoca un `git commit` real sin reimplementar el
parser. No cambia nada de lo anterior: ni `MANEJADORES`, ni `decidir()`, ni
sus mensajes, ni la politica de fail-open.

Segundo seam publico (#176, hallazgo 1 del code review): `clave_de_segmento(segmento)
-> tuple[str, ...] | None` toma UN segmento ya tokenizado (como los que devuelve
`segmentar()`) y devuelve la clave de subcomando reconocida (p. ej. `("git",
"commit")`, `("gh", "pr", "merge")`), o `None` si no matchea ninguna. Delega en
`_clave_y_resto` -- que sigue siendo zona interna de este modulo, con el mismo
derecho a cambiar sin aviso que ya declaraba el parser -- para no duplicar el
salto de flags globales de `git` (`-C`/`-c`/`--git-dir`/etc.). Junto con
`segmentar()`, son los DOS unicos seams publicos que `scripts/detectar_commit.py`
consume; ninguno de los dos cambia `MANEJADORES`, `decidir()`, sus mensajes ni
la politica de fail-open.
"""

from __future__ import annotations

import datetime
import json
import posixpath
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import timonel_gh

PREFIJO = "[timonel] Bloqueado: "

# Refs que solo pueden adelantar la copia local a lo ya publicado y revisado.
REFS_DE_SINCRONIA = {"", "@{u}", "@{upstream}"}

BASES_POR_DEFECTO = ["main", "master", "develop"]

EVENTS_LOG = Path(".timonel/events.log")

# Asignacion de variable de entorno delante del programa (`GIT_DIR=.git git ...`).
_RE_ASIGNACION_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _es_flag(token: str) -> bool:
    return token.startswith("-")


def _tokens_no_flag(tokens: list[str]) -> list[str]:
    return [t for t in tokens if not _es_flag(t)]


def _es_ref_segura(ref: str, base: str) -> bool:
    return ref in REFS_DE_SINCRONIA or ref == f"origin/{base}"


def _normalizar_referencia_destino(ref: str) -> str:
    """`refs/heads/<x>` y `<x>` son el mismo destino para `git push`; normaliza antes
    de comparar contra `bases` (si no, `hu/x:refs/heads/main` evade el guard aunque
    `hu/x:main` sí bloquee)."""
    prefijo = "refs/heads/"
    return ref[len(prefijo):] if ref.startswith(prefijo) else ref


def _registrar_fail_open(motivo: str) -> None:
    """Deja rastro de un fail-open en `.timonel/events.log`, mismo estilo que ya
    escriben los hooks `PostToolUse` de `hooks/hooks.json` (hora + evento). Nunca debe
    hacer fallar el guard: si el directorio no existe o no se puede escribir, sigue."""
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        hora = datetime.datetime.now().strftime("%H:%M:%S")
        with EVENTS_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{hora} guard-integracion-fail-open {motivo}\n")
    except Exception:
        pass


def _mensaje_push(base: str) -> str:
    return (
        f"{PREFIJO}push directo a la rama base '{base}' no esta permitido. "
        "Trabaja en la rama de la historia y publica con "
        "`git push -u origin hu/<issue>-<slug>`; el merge a "
        f"'{base}' lo hace un humano al aprobar el PR (epica #79)."
    )


#  Alternativa de sincronizacion por verbo: el comando ofrecido es el que el
# usuario tipeo, no siempre el de `merge` (ronda final de #82). `{base}` se
# interpola despues.
_ALTERNATIVAS_DE_SINCRONIA = {
    "merge": "`git merge --ff-only origin/{base}` (o `git pull --ff-only`)",
    "pull": "`git pull --ff-only`",
    "rebase": "`git rebase origin/{base}`",
}


def _mensaje_integracion_en_base(base: str, verbo: str) -> str:
    alternativa = _ALTERNATIVAS_DE_SINCRONIA[verbo].format(base=base)
    return (
        f"{PREFIJO}en la rama base '{base}' no se permite crear integracion "
        "nueva (TIM-ADR-0005, epica #79/#80). Para sincronizar tu copia "
        f"local si podes: {alternativa}."
    )


def _mensaje_reset_mueve_base(base: str) -> str:
    """`reset` con una ref no segura no "crea integracion nueva" (no hay nada que
    revisar en un PR): lo que hace es mover el ref de la rama base a un commit
    que nadie reviso. Mensaje propio -- reusar `_mensaje_integracion_en_base`
    diria algo falso aca."""
    return (
        f"{PREFIJO}en la rama base '{base}' no se permite mover el ref de la "
        "rama a un commit que nadie reviso (TIM-ADR-0005, epica #79/#80). "
        f"Para sincronizar tu copia local si podes: `git reset --hard "
        f"origin/{base}`."
    )


def _mensaje_commit_directo_en_base(base: str) -> str:
    """cherry-pick/revert/am en la base: a diferencia de merge/pull/rebase/reset,
    sincronizar no le sirve a quien quiere llevar un commit a la base -- la
    alternativa real es commitear en la rama de la historia y abrir el PR."""
    return (
        f"{PREFIJO}en la rama base '{base}' no se permite crear integracion "
        "nueva (TIM-ADR-0005, epica #79/#80). Hacelo en la rama de la "
        "historia (`git checkout hu/<issue>-<slug>`) y abri el PR: el "
        f"cambio llega a '{base}' cuando un humano lo mergea."
    )


def _mensaje_gh_pr_merge() -> str:
    return (
        f"{PREFIJO}gh pr merge esta bloqueado. El merge del PR lo hace un "
        "humano desde la web de GitHub o una terminal fuera de Claude "
        "(epica #79)."
    )


def _mensaje_gh_api_merge() -> str:
    return (
        f"{PREFIJO}gh api hacia una ruta que termina en /merge o /merges "
        "esta bloqueado: es el equivalente exacto de `gh pr merge` (o del "
        "merge de una rama del lado del servidor) por la API REST, y el "
        "merge lo hace un humano (epica #79). Si solo queres consultar el "
        "estado, usa `gh pr view <numero> --json mergeable,mergeStateStatus`."
    )


def _destino_push(resto: list[str], rama: str) -> str:
    """resto = tokens luego de `push`. Devuelve la rama remota destino."""
    no_flags = _tokens_no_flag(resto)
    if not no_flags:
        return rama  # sin remoto ni refspec: empuja la rama actual a su upstream
    _remoto, *refspecs = no_flags
    if not refspecs:
        return rama  # remoto explicito sin refspec: idem
    refspec = refspecs[0]
    if refspec == "HEAD":
        return rama
    if ":" in refspec:
        _, _, destino = refspec.partition(":")
        destino = destino or rama
    else:
        destino = refspec
    return _normalizar_referencia_destino(destino)


def _revisar_push(resto: list[str], rama: str, bases: list[str]) -> str | None:
    destino = _destino_push(resto, rama)
    return _mensaje_push(destino) if destino in bases else None


def _revisar_merge(resto: list[str], rama: str, bases: list[str]) -> str | None:
    if any(f in resto for f in ("--abort", "--continue", "--quit")):
        return None  # no integra nada nuevo
    if rama not in bases:
        return None  # el criterio de frontera solo aplica parado en una base
    ff_only = "--ff-only" in resto
    refs = _tokens_no_flag(resto)
    ref = refs[0] if refs else ""
    if ff_only and _es_ref_segura(ref, rama):
        return None
    return _mensaje_integracion_en_base(rama, "merge")


def _revisar_rebase(resto: list[str], rama: str, bases: list[str]) -> str | None:
    if any(f in resto for f in ("--abort", "--continue", "--skip", "--quit")):
        return None  # formas de control: no integran nada nuevo
    if rama not in bases:
        return None  # el criterio de frontera solo aplica parado en una base
    refs = _tokens_no_flag(resto)
    ref = refs[0] if refs else ""
    if _es_ref_segura(ref, rama):
        return None  # sync con el remoto de la misma base: `git rebase origin/main`
    return _mensaje_integracion_en_base(rama, "rebase")


def _revisar_commit_directo(resto: list[str], rama: str, bases: list[str]) -> str | None:
    """cherry-pick/revert/am: misma familia, bloqueadas siempre en la base (cualquier
    ref mete un commit nuevo directo, no hay equivalente de ref segura). Sus formas
    de control (`--continue`, `--abort`, `--skip`, `--quit`) se permiten siempre."""
    if any(f in resto for f in ("--abort", "--continue", "--skip", "--quit")):
        return None
    if rama not in bases:
        return None  # el criterio de frontera solo aplica parado en una base
    return _mensaje_commit_directo_en_base(rama)


def _revisar_reset(resto: list[str], rama: str, bases: list[str]) -> str | None:
    """Ref segura de merge/rebase/pull (vacia o equivalente remoto de la propia
    base), MAS dos casos propios de `reset` que no aplican al resto de la
    familia (ver docstring del modulo, ronda de falsos positivos de #82):

    - `HEAD` exacto: no mueve el ref de la rama a nada nuevo. `HEAD~1`,
      `HEAD^` y `HEAD@{1}` si lo mueven y siguen bloqueados -- por eso NO se
      delega a `_es_ref_segura` (esa sigue sin conocer `HEAD` para
      merge/rebase/pull, a proposito).
    - forma con pathspec (`git reset [<ref>] [--] <paths>...`): es un unstage,
      nunca mueve el ref sin importar cual sea la ref. Se detecta por `--`
      literal en `resto`, o por haber mas de un argumento no-flag (ref +
      al menos un path).

    No distingue --hard/--soft/--mixed a proposito: lo que mueve la base a
    codigo no revisado es la REF, no el modo (decision documentada: `git
    reset --soft HEAD~1` en la base tambien bloquea, aunque --soft no toque
    el working tree, porque HEAD~1 no es una ref segura)."""
    if rama not in bases:
        return None
    if "--" in resto:
        return None  # `git reset [<ref>] -- <paths>...`: unstage, no mueve el ref
    no_flags = _tokens_no_flag(resto)
    if len(no_flags) >= 2:
        return None  # `git reset <ref> <paths>...`: idem, unstage
    ref = no_flags[0] if no_flags else ""
    if ref == "HEAD" or _es_ref_segura(ref, rama):
        return None
    return _mensaje_reset_mueve_base(rama)


def _revisar_pull(resto: list[str], rama: str, bases: list[str]) -> str | None:
    if rama not in bases:
        return None
    ff_only = "--ff-only" in resto
    no_flags = _tokens_no_flag(resto)
    if len(no_flags) >= 2:
        remoto, rama_remota = no_flags[0], no_flags[1]
        ref_efectiva = f"{remoto}/{rama_remota}"
    else:
        ref_efectiva = ""  # sin refspec, o solo remoto sin rama: se asume seguro
    if ff_only and _es_ref_segura(ref_efectiva, rama):
        return None
    return _mensaje_integracion_en_base(rama, "pull")


def _rama_tras_checkout(resto: list[str], rama_actual: str) -> str:
    no_flags = _tokens_no_flag(resto)
    return no_flags[-1] if no_flags else rama_actual


def _manejar_push(resto, rama, bases):
    return _revisar_push(resto, rama, bases), rama


def _manejar_merge(resto, rama, bases):
    return _revisar_merge(resto, rama, bases), rama


def _manejar_pull(resto, rama, bases):
    return _revisar_pull(resto, rama, bases), rama


def _manejar_checkout(resto, rama, bases):  # noqa: ARG001 - firma uniforme para el lookup map
    return None, _rama_tras_checkout(resto, rama)


def _manejar_gh_pr_merge(resto, rama, bases):  # noqa: ARG001 - firma uniforme para el lookup map
    return _mensaje_gh_pr_merge(), rama


def _manejar_rebase(resto, rama, bases):
    return _revisar_rebase(resto, rama, bases), rama


def _manejar_cherry_pick(resto, rama, bases):
    return _revisar_commit_directo(resto, rama, bases), rama


def _manejar_revert(resto, rama, bases):
    return _revisar_commit_directo(resto, rama, bases), rama


def _manejar_am(resto, rama, bases):
    return _revisar_commit_directo(resto, rama, bases), rama


def _manejar_reset(resto, rama, bases):
    return _revisar_reset(resto, rama, bases), rama


# Flags de `gh api` que llevan valor separado: hay que saltearlos junto con su
# valor para no confundir ese valor (p. ej. el `PUT` de `-X PUT`) con la ruta.
_FLAGS_GH_API_CON_VALOR = {
    "-X", "--method", "-H", "--header", "--hostname", "--input", "--cache",
    "-f", "--raw-field", "-F", "--field", "-t", "--template", "-q", "--jq",
}


def _ruta_gh_api(resto: list[str]) -> str:
    """Primer argumento posicional de `gh api` (la ruta del endpoint), salteando
    flags y sus valores separados."""
    i = 0
    n = len(resto)
    while i < n:
        token = resto[i]
        if token in _FLAGS_GH_API_CON_VALOR:
            i += 2
            continue
        if _es_flag(token):
            i += 1
            continue
        return token
    return ""


def _es_ruta_merge(ruta: str) -> bool:
    """Patron deliberadamente simple (no un parser de metodos HTTP): quita el
    query string y mira si el segmento FINAL de la ruta es `merge` o `merges`
    -- `/merges` es la API *Merge a branch* (POST repos/{o}/{r}/merges), tan
    directa como `/merge` y con el mismo sufijo mal cubierto por un simple
    `endswith("/merge")` (ronda final de #82: `.../merges` no matcheaba)."""
    sin_query = ruta.split("?", 1)[0].rstrip("/")
    segmento_final = sin_query.rsplit("/", 1)[-1]
    return segmento_final in ("merge", "merges")


def _manejar_gh_api(resto, rama, bases):  # noqa: ARG001 - firma uniforme para el lookup map
    ruta = _ruta_gh_api(resto)
    if _es_ruta_merge(ruta):
        return _mensaje_gh_api_merge(), rama
    return None, rama


# Lookup map: subcomando -> manejador(resto_tras_subcomando, rama, bases) -> (mensaje|None, rama_siguiente)
MANEJADORES = {
    ("git", "push"): _manejar_push,
    ("git", "merge"): _manejar_merge,
    ("git", "pull"): _manejar_pull,
    ("git", "rebase"): _manejar_rebase,
    ("git", "cherry-pick"): _manejar_cherry_pick,
    ("git", "am"): _manejar_am,
    ("git", "revert"): _manejar_revert,
    ("git", "reset"): _manejar_reset,
    ("git", "checkout"): _manejar_checkout,
    ("git", "switch"): _manejar_checkout,
    ("gh", "pr", "merge"): _manejar_gh_pr_merge,
    ("gh", "api"): _manejar_gh_api,
}

SEPARADORES_DE_SEGMENTO = {"&&", "||", ";", "&", "|", "(", ")"}

# Flags globales de `git` que van ANTES del subcomando y llevan valor SEPARADO
# (el token siguiente), salvo que el valor venga pegado al mismo token
# (`-Cpath`, `-cclave=valor`, `--git-dir=valor`). Cualquier OTRO flag que
# empiece con `-` en esa zona es booleano y no consume nada mas -- esa parte
# es la regla, no una lista (ver `_indice_subcomando_git`).
_FLAGS_GLOBALES_GIT_CON_VALOR_SEPARADO = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
}


def _descartar_asignaciones_env(segmento: list[str]) -> list[str]:
    """`GIT_DIR=.git git push ...`: descarta los tokens `NOMBRE=valor` que preceden
    al programa real, tal como lo haria el shell al armar el entorno del comando."""
    i = 0
    while i < len(segmento) and _RE_ASIGNACION_ENV.match(segmento[i]):
        i += 1
    return segmento[i:]


def _indice_subcomando_git(segmento: list[str]) -> int | None:
    """Salta flags globales de `git` y devuelve el indice del subcomando real,
    o None si no hay ninguno (p. ej. `git` solo, o `git -C` sin subcomando).

    Regla (no lista): entre `git` y el subcomando, TODO token que empiece con
    `-` es un flag global. Los que llevan valor separado
    (`_FLAGS_GLOBALES_GIT_CON_VALOR_SEPARADO`) consumen ademas el token
    siguiente, salvo que el valor venga pegado al mismo token (`-Cpath`,
    `-cclave=valor`, `--git-dir=valor`) -- ahi no se consume nada extra.
    Cualquier otro flag -- booleano, conocido o no (`--no-pager`,
    `--paginate`, `-P`, `--bare`, `--literal-pathspecs`, ...) -- consume solo
    su propio token: no hace falta enumerarlos, la regla los cubre a todos
    por igual."""
    i = 1
    n = len(segmento)
    while i < n:
        token = segmento[i]
        if not _es_flag(token):
            return i
        if "=" in token:
            i += 1  # valor pegado con `=` (largo): --git-dir=valor
            continue
        base_corta = token[:2]
        if base_corta in _FLAGS_GLOBALES_GIT_CON_VALOR_SEPARADO and token != base_corta:
            i += 1  # valor pegado (corto): -Cpath, -cclave=valor
            continue
        if token in _FLAGS_GLOBALES_GIT_CON_VALOR_SEPARADO:
            i += 2  # valor separado: el token siguiente es el valor
            continue
        i += 1  # flag booleano: no consume nada mas
    return None


def _clave_y_resto(segmento: list[str]) -> tuple[tuple[str, ...] | None, list[str]]:
    segmento = _descartar_asignaciones_env(segmento)
    if not segmento:
        return None, []
    programa = segmento[0]
    if programa == "git":
        idx = _indice_subcomando_git(segmento)
        if idx is None:
            return None, []
        return ("git", segmento[idx]), segmento[idx + 1:]
    if programa == "gh" and len(segmento) >= 3 and segmento[1] == "pr":
        return ("gh", "pr", segmento[2]), segmento[3:]
    if programa == "gh" and len(segmento) >= 2 and segmento[1] == "api":
        return ("gh", "api"), segmento[2:]
    return None, []


def _normalizar_separadores(cmd: str) -> str:
    """Inserta espacios alrededor de `;`, `&&`, `||`, `&`, `|`, `(`, `)` y trata
    `\\n`/`\\r\\n` como `;` para que salgan como tokens propios de `shlex.split`,
    sin tocar lo que este dentro de comillas simples o dobles (si no, `git commit
    -m "fix; ver #82"` perderia la comilla de cierre y `shlex` fallaria: un
    fail-open silencioso y evadible a proposito). El salto de linea es el
    separador critico (#82 ronda 2): es como Bash entrega comandos multilinea
    todo el tiempo, no una evasion deliberada como `eval`/`bash -c`/`$(...)`
    (ver docstring del modulo).

    Reconoce el escape de comillas fuera y dentro de comillas dobles (`\\"`,
    `\\\\`) igual que `shlex`: sin esto, un `\\"` dentro de un mensaje de commit
    hace que el tracker de comillas -mas simple que `shlex`- cierre la comilla
    antes de tiempo, y un `&&`/`;` que en Bash real SIGUE ejecutandose como
    comando nuevo (no hace falta espacio alrededor: `"a\\"b"&&git merge x` corre
    igual) quedaria escondido dentro de lo que el tracker cree que sigue
    citado -- un bypass real, no solo una divergencia cosmetica."""
    resultado: list[str] = []
    comilla: str | None = None
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if comilla == "'":
            # dentro de comillas simples nada escapa: todo literal hasta el cierre.
            resultado.append(ch)
            if ch == "'":
                comilla = None
            i += 1
            continue
        if comilla == '"':
            if ch == "\\" and i + 1 < n and cmd[i + 1] in ('"', "\\"):
                # `\"` / `\\` dentro de comillas dobles: escapado, no cierra la comilla.
                resultado.append(ch)
                resultado.append(cmd[i + 1])
                i += 2
                continue
            resultado.append(ch)
            if ch == '"':
                comilla = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            # fuera de comillas, backslash escapa literalmente el caracter siguiente
            # (comilla o separador): no abre comillas ni cuenta como separador.
            resultado.append(ch)
            resultado.append(cmd[i + 1])
            i += 2
            continue
        if ch in ("'", '"'):
            comilla = ch
            resultado.append(ch)
            i += 1
            continue
        dos = cmd[i:i + 2]
        if dos in ("&&", "||"):
            resultado.append(f" {dos} ")
            i += 2
            continue
        if dos == "\r\n":
            resultado.append(" ; ")
            i += 2
            continue
        if ch == "\n":
            resultado.append(" ; ")
            i += 1
            continue
        if ch == "(" and i > 0 and cmd[i - 1] == "$":
            # `$(...)` es command substitution, no agrupacion: la limitacion aceptada
            # (docstring del modulo) dice explicitamente que no se persigue -- si la
            # tratamos como separador aca, el simulador de rama SI entra al `$(git
            # checkout main)` de casos como `$(git checkout main) && git merge hu/x`
            # y termina bloqueando un caso que la decision de alcance deja afuera
            # a proposito.
            resultado.append(ch)
            i += 1
            continue
        if ch in (";", "&", "|", "(", ")"):
            resultado.append(f" {ch} ")
            i += 1
            continue
        resultado.append(ch)
        i += 1
    return "".join(resultado)


def _partir_en_segmentos(tokens: list[str]) -> list[list[str]]:
    """Parte en `&&`/`||`/`;` para que un comando compuesto no evada el guard."""
    segmentos: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARADORES_DE_SEGMENTO:
            segmentos.append([])
        else:
            segmentos[-1].append(token)
    return [s for s in segmentos if s]


def segmentar(cmd: str) -> list[list[str]] | None:
    """Seam publico (#176): tokeniza `cmd` y lo parte en sub-comandos
    independientes por los separadores de shell (`&&`, `||`, `;`, `&`, `|`,
    `(`, `)`, salto de linea), exactamente como lo hace `decidir()` por
    dentro. Envuelve `_normalizar_separadores` + `shlex.split` +
    `_partir_en_segmentos`; devuelve `None` si `shlex` no puede tokenizar
    (comillas sin cerrar, etc.), el mismo caso que `decidir()` trata como
    fail-open. Lo consume `scripts/detectar_commit.py` para no reimplementar
    este parser (`heuristics/general/sensor-importa-no-reimplementa.md`)."""
    try:
        tokens = shlex.split(_normalizar_separadores(cmd), posix=True)
    except ValueError:
        return None
    return _partir_en_segmentos(tokens)


def clave_de_segmento(segmento: list[str]) -> tuple[str, ...] | None:
    """Segundo seam publico (#176): dado UN segmento ya tokenizado (uno de los
    que devuelve `segmentar()`), devuelve la clave de subcomando reconocida
    (p. ej. `("git", "commit")`) o `None` si el segmento no matchea ninguna
    clave conocida. Delega en `_clave_y_resto` y descarta el resto (los
    argumentos posteriores al subcomando), que es un detalle de `decidir()`
    que un consumidor externo como `scripts/detectar_commit.py` no necesita.
    No reimplementa nada del salto de flags globales de `git` -- lo hereda de
    `_clave_y_resto` (`heuristics/general/sensor-importa-no-reimplementa.md`)."""
    clave, _resto = _clave_y_resto(segmento)
    return clave


SESION = "."  # clave del directorio de la sesion (cwd del hook)

_FLAGS_DE_CD_SIN_VALOR = {"-P", "-L", "-e", "-@"}
_MARCAS_NO_RESOLUBLES = ("$", "`", "~")


def _unir_dir(base: str | None, destino: str) -> str | None:
    """`destino` relativo a `base` (normalizado); None si algo no es resoluble:
    `base` ya desconocido y `destino` relativo, o `destino` con `~`/`$`/backtick."""
    if not destino or any(marca in destino for marca in _MARCAS_NO_RESOLUBLES):
        return None
    if posixpath.isabs(destino):
        return posixpath.normpath(destino)
    if base is None:
        return None
    return posixpath.normpath(posixpath.join(base, destino))


def _dirs_de_flags_git(segmento: list[str], idx: int) -> list[str] | None:
    """Valores de los `-C` que preceden al subcomando (en orden); None si hay
    `--git-dir`/`--work-tree`, que cambian el repo de forma no resoluble aqui."""
    dirs: list[str] = []
    i = 1
    while i < idx:
        token = segmento[i]
        if token == "-C":
            dirs.append(segmento[i + 1])
            i += 2
        elif token.startswith("-C"):
            dirs.append(token[2:])
            i += 1
        elif token.split("=", 1)[0] in ("--git-dir", "--work-tree"):
            return None
        elif token in _FLAGS_GLOBALES_GIT_CON_VALOR_SEPARADO:
            i += 2
        else:
            i += 1
    return dirs


def _dir_de_cd(segmento: list[str], dir_actual: str | None) -> str | None:
    args = [t for t in segmento[1:] if t not in _FLAGS_DE_CD_SIN_VALOR]
    if len(args) != 1 or args[0] == "-":
        return None
    return _unir_dir(dir_actual, args[0])


def _dir_de_git(segmento: list[str], dir_actual: str | None) -> str | None:
    idx = _indice_subcomando_git(segmento)
    if idx is None:
        return dir_actual
    dirs = _dirs_de_flags_git(segmento, idx)
    if dirs is None:
        return None
    efectivo = dir_actual
    for d in dirs:
        efectivo = _unir_dir(efectivo, d)
    return efectivo


def _dir_de_segmento(segmento: list[str], dir_actual: str | None) -> tuple[str | None, str | None]:
    """Funcion pura (#229). Devuelve `(dir_efectivo, dir_siguiente)`: el directorio
    donde corre ESTE segmento y el vigente para los siguientes. `None` = no
    resoluble. `cd <dir>` no corre nada pero mueve el dir de los siguientes
    (relativo al vigente); `git -C <a> -C <b>` acumula sobre el vigente y no
    mueve el de los siguientes."""
    segmento = _descartar_asignaciones_env(segmento)
    if not segmento:
        return dir_actual, dir_actual
    programa = segmento[0]
    if programa == "cd":
        return dir_actual, _dir_de_cd(segmento, dir_actual)
    if programa == "git":
        return _dir_de_git(segmento, dir_actual), dir_actual
    return dir_actual, dir_actual


def _clave_de_rama(
    dir_efectivo: str | None, ramas: dict[str, str], rama_de: Callable[[str], str | None]
) -> str:
    """Clave de `ramas` con la rama del dir efectivo, leyendola (una vez) con
    `rama_de`. Si no se puede (dir no resoluble o `rama_de` -> None) usa la
    rama de la sesion y deja fail-open `rama-no-resuelta`."""
    if dir_efectivo == SESION or dir_efectivo in ramas:
        return dir_efectivo
    leida = None if dir_efectivo is None else rama_de(dir_efectivo)
    if leida is None:
        _registrar_fail_open("rama-no-resuelta")
        return SESION
    ramas[dir_efectivo] = leida
    return dir_efectivo


def decidir(
    cmd: str,
    rama: str,
    bases: list[str],
    rama_de: Callable[[str], str | None] | None = None,
) -> str | None:
    """Punto de entrada puro. `rama` es la rama actual real de la sesion; se simula
    su avance entre segmentos (`git checkout <base> && ...`) para que el compuesto
    no evada el guard. `rama_de(dir) -> rama | None` (inyectable, #229) da la rama
    del repo de otro directorio; sin ella todo corre en `rama`. Devuelve el
    mensaje de bloqueo, o None si el comando se permite."""
    try:
        tokens = shlex.split(_normalizar_separadores(cmd), posix=True)
    except ValueError:
        _registrar_fail_open("shlex-invalido")
        return None  # shlex invalido (comillas sin cerrar, etc.): fail-open
    try:
        ramas = {SESION: rama}
        dir_actual: str | None = SESION
        con_subshell = any(t in tokens for t in ("(", ")", "|", "&"))
        for segmento in _partir_en_segmentos(tokens):
            dir_efectivo, dir_siguiente = _dir_de_segmento(segmento, dir_actual)
            if not con_subshell:
                dir_actual = dir_siguiente
            clave, resto = _clave_y_resto(segmento)
            manejador = MANEJADORES.get(clave)
            if manejador is None:
                continue
            clave_rama = SESION if rama_de is None else _clave_de_rama(dir_efectivo, ramas, rama_de)
            mensaje, ramas[clave_rama] = manejador(resto, ramas[clave_rama], bases)
            if mensaje:
                return mensaje
        return None
    except Exception:
        _registrar_fail_open("error-inesperado")
        return None  # error inesperado: fail-open


def _rama_git_actual() -> str:
    resultado = subprocess.run(
        ["git", "branch", "--show-current"], capture_output=True, text=True, check=False
    )
    return resultado.stdout.strip()


def _rama_de_dir_real() -> Callable[[str], str | None]:
    """`rama_de` real para `main()`: `git -C <dir> branch --show-current`, cacheado
    por directorio, con timeout corto. `returncode != 0` (dir inexistente, no es
    repo) o fallo al ejecutar -> None; HEAD desacoplado -> "" (no es base)."""
    cache: dict[str, str | None] = {}

    def rama_de(directorio: str) -> str | None:
        if directorio not in cache:
            try:
                r = subprocess.run(
                    ["git", "-C", directorio, "branch", "--show-current"],
                    capture_output=True, text=True, check=False, timeout=5,
                )
                cache[directorio] = r.stdout.strip() if r.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                cache[directorio] = None
        return cache[directorio]

    return rama_de


def _bases_configuradas() -> list[str]:
    """Bases del config del consumidor (tambien desde un worktree, #210). Fail-open a
    BASES_POR_DEFECTO, pero dejando rastro en stderr cuando no pudo leer el config."""
    ruta, motivo = timonel_gh.resolver_config_path()
    if ruta is None:
        print(f"[timonel] aviso: bases por defecto, no se resolvio el config: {motivo}", file=sys.stderr)
        return list(BASES_POR_DEFECTO)
    if not ruta.exists():
        return list(BASES_POR_DEFECTO)
    try:
        bases = json.loads(ruta.read_text(encoding="utf-8")).get("git", {}).get("baseBranches")
    except (OSError, ValueError, AttributeError) as exc:
        print(f"[timonel] aviso: bases por defecto, config ilegible {ruta}: {exc}", file=sys.stderr)
        return list(BASES_POR_DEFECTO)
    return list(bases) if bases else list(BASES_POR_DEFECTO)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        cmd = payload.get("tool_input", {}).get("command", "")
        mensaje = decidir(cmd, _rama_git_actual(), _bases_configuradas(), _rama_de_dir_real())
    except Exception:
        _registrar_fail_open("payload-o-entorno-invalido")
        return 0  # payload roto o entorno inesperado: fail-open
    if mensaje:
        print(mensaje, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
