---
name: implement-plugin-change
description: Implementa un cambio del propio plugin Timonel (agentes, skills, scripts, hooks, docs) a partir de un issue mod:plugin de luisfelipediaz/Harness.Timonel, con TDD sobre unittest y respetando la gobernanza del repo (issue obligatorio, commits con #N, sin tocar CHANGELOG ni version). Lo usa el sub-agente unico del perfil plugin de flechodiezx.
---

Recibes del orquestador: `issue`, el body del issue, el **contrato de cambio** (tabla archivo → cambio) y la investigacion (`timonel:investigacion`). Trabajas en **un worktree aislado** (`isolation: worktree`), en una rama nueva que el harness genera (tipicamente `worktree-agent-<id>`, bajo `.claude/worktrees/agent-<id>/`) creada desde `origin/main` — no en `hu/<issue>-*` directamente. El orquestador mergea esa rama a `hu/<issue>-*` despues, via `consolidate-story`. Siempre en espanol.

## Reglas del repo (TIM-ADR-0005 y `CLAUDE.md` del plugin)

- **Nunca uses `$PLUGIN_ROOT`.** Dentro de tu worktree esa variable resuelve al plugin instalado en cache (una version vieja), no al repo que estas editando. Usa siempre **rutas relativas a tu CWD** (`tests/`, `scripts/`, `agents/`, `skills/`): el codigo que se prueba es el codigo que se edita.
- **Solo los archivos del contrato de cambio.** Si necesitas tocar otro, no lo hagas: documentalo en el output como pendiente.
- **No edites `CHANGELOG.md` ni `.claude-plugin/plugin.json`**: lo hace `consolidate-story`.
- **Cambios de formato de issue/comentario** → actualiza en el mismo commit `skills/github-issues/references/*.md`, `scripts/timonel_gh.py`, `scripts/validar_marcador.py` y sus tests.
- **Agentes**: frontmatter `name`, `description`, `model`, `color`, `skills`. **Commands**: `description`, `argument-hint`, `model`. **Skills**: `name` = carpeta, `description` con cuando usarlo.
- Python stdlib puro, lookup maps antes que cadenas de `if` (`heuristics/general/evitar-ifs.md`), sin tipos espejo. Esa cita es un recordatorio, **no** el mecanismo: antes de escribir codigo lee **todo** `heuristics/general/*.md` con el glob (`ls heuristics/general/*.md`), nunca una lista de nombres. Un archivo nuevo ahi te aplica con solo existir, y si tuvieras que editar este skill para enterarte, el descubrimiento esta roto.
- Commits: `<tipo>: <que> (#<issue>)`; el hook `commit-msg` rechaza sin `#N`.

## Limites conocidos del worktree

- **`core.hooksPath` resuelve al `.githooks` del arbol principal** (ruta absoluta), incluso dentro de tu worktree. Si tu cambio toca `.githooks/commit-msg`, tus propios commits se validan con el hook **viejo** — es una trampa: que tus commits pasen no prueba que el hook nuevo funcione.
- **El guard `PreToolUse` si resuelve al de tu worktree** (`./scripts/guard_integracion.py`, relativo a tu CWD). Si tu cambio toca ese guard, tu propia sesion ya corre contra el guard **nuevo** — te "dogfoodeas" el cambio. Es la asimetria opuesta a la anterior.

## Pasos

0. **Sincroniza tu worktree con la rama de la historia, solo si el orquestador te la indico**: naces de `origin/main`, nunca de esa rama. El orquestador (perfil plugin de `flechodiezx`) te pasa `RAMA_HISTORIA` (tipicamente `hu/N-slug`) despues de intentar pushearla; que no te la pase no es un error, es una respuesta valida por su cuenta:
   - **Te indico `RAMA_HISTORIA` y existe en el remoto, pero `origin/<RAMA_HISTORIA>` esta atrasada respecto de la rama local**: medilo antes de creerle al merge — `git fetch origin && git rev-list --left-right --count <RAMA_HISTORIA>...origin/<RAMA_HISTORIA>`; tu worktree comparte refs y objetos con el arbol principal, asi que la ref local es visible desde aca. Si el primer numero es > 0, la rama local tiene commits que el remoto no vio (nadie pusheo tras consolidar): **detente y reportalo en tu output, no sigas al paso 1**. Un `git merge --no-edit origin/<RAMA_HISTORIA>` en ese estado responde `Already up to date`, la misma cadena exacta que devuelve el no-op legitimo — no podes distinguir "no habia nada que traer" de "el remoto que consulte esta viejo", y eso es un **"no se"**, no un "no" (heuristica de `heuristics/general/*.md`: `sensor-declara-su-evidencia.md`). No implementes sobre codigo viejo. Este caso es distinto del siguiente: aca el remoto **existe y miente**; en el siguiente, el remoto **no existe**.
   - **Te indico `RAMA_HISTORIA` y existe en el remoto**: `git fetch origin && git merge --no-edit origin/<RAMA_HISTORIA>` antes de leer nada (ejemplo real: `git merge --no-edit origin/hu/N-slug`).
   - **Te indico `RAMA_HISTORIA` pero no existe en el remoto** (el push no fue posible, #122): segui desde `origin/main` y dejalo dicho en tu output — no abortes, el orquestador ya te aviso que podia pasar.
   - **El merge trae conflictos**: detente y reportalo.
   - **No te indico ninguna `RAMA_HISTORIA`**: no hay nada que sincronizar — segui normalmente.
1. Lee `CLAUDE.md` del plugin, el contrato de cambio y los "Archivos de referencia" de la investigacion. No explores fuera de ellos.
2. **TDD**: si el cambio toca `scripts/*.py`, escribe primero el test en `tests/` (unittest, sin `gh` real: funciones puras sobre dicts, y ademas un test por fila del cableado de `main()` que ejercite `main()` o la funcion que lo cablea, con `--json` o con `subprocess.run` parcheado: la funcion pura sola deja sin evidencia el tramo que decide el "no sé") y velo fallar; si toca agentes/skills/commands/hooks, `tests/test_consistencia.py` es tu red: agrega ahi la regla nueva si el cambio introduce una invariante (p. ej. "todo agente X invoca Y").
3. Implementa. Para `hooks/hooks.json`, prueba el hook con entradas simuladas (`echo '{"tool_input":{...}}' | bash -c "$(jq -r '.hooks...command' hooks/hooks.json)"`) y deja el caso en el output.
4. Verifica localmente con una sola orden, que corre unittest, `bash -n` y `jq` y termina en una linea:
   ```bash
   python3 scripts/verificar_plugin.py
   ```
   - **exit 0** (`PASSED`): verificado.
   - **exit 1** (`FAILED: <paso>`): corrige. Maximo 2 intentos de correccion; si persiste, detente y reporta el error exacto.
   - **exit 2** (`no sé: <motivo>`): **nunca se reporta como pasado**. Si el entorno bloquea `bash -n` o `jq` (o cualquier paso queda sin evidencia), copia el motivo tal cual al Output como **pendiente obligatorio de la consolidacion**; no lo des por verde ni lo reintentes en bucle.
5. Commit: `git add <archivos del contrato> && git commit -m "<tipo>: <que> (#<issue>)"`.

## Output (obligatorio)

- Archivos creados/modificados (incluidos tests)
- Casos de prueba de hooks ejecutados (si aplica)
- Invariantes nuevas agregadas a `test_consistencia.py` (si aplica)
- Tests del cableado de main(): lista de los tests que ejercitan `main()` (uno por fila de decision), o `Ninguno: el cambio no toca main()`
- Pendientes fuera del contrato (o "Ninguno")
- **Rama exacta y ruta de tu worktree** (`git branch --show-current` y `pwd`): el orquestador los necesita para consolidar
- Hash del commit
- Si la verificacion fallo: error exacto y lo intentado
- `Verificación: PASSED \| FAILED: <paso> \| no sé: <motivo>` — la linea final de `scripts/verificar_plugin.py` (un "no sé" es un pendiente obligatorio de la consolidacion, nunca un pasado)
- `### Tests verdes en su primera corrida` — exactamente una de tres formas (nunca vacio ni en prosa libre):
  - una linea por test que paso en su primera corrida: `` - `<test>` — mutación: <qué se cambió> — fallos: N de M `` (N≥1; si N=0 el test es vacuo: reportalo como tal, no lo cuentes como evidencia). Si `mutar_literal.py` reporta `base: B de M` con B > 0 (la suite ya falla sin mutar), la evidencia de cobertura es `N > B`, no `N > 0`: con `N <= B` el test es vacuo;
  - `Ninguno`: corriste tests y ninguno nacio verde;
  - `No medido: <motivo>`: distinto de `Ninguno`, para cuando no corriste tests.

