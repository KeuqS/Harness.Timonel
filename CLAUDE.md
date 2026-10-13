# CLAUDE.md — timonel

Plugin de Claude Code (`.claude-plugin/plugin.json`) que empaqueta el harness de planificación e implementación de historias para monorepos Nx Angular + NestJS con backlog en GitHub Issues.

## Principios

- Comunícate en **español**.
- Los agentes **no hardcodean rutas del consumidor**: todo sale de `.claude/timonel.config.json` (TIM-ADR-0002).
- La fuente de verdad del backlog es GitHub (TIM-ADR-0001). Ningún agente escribe HUs, SDD, retros o reviews como archivos en el consumidor.
- El formato de issues y comentarios vive en un solo lugar: `skills/github-issues/`. Los demás skills y agentes lo referencian, no lo duplican.

## Estructura

| Carpeta | Contenido |
| --- | --- |
| `agents/` | `sdd-planner`, `user-story-planner`, `backlog-refiner`, `harness-auditor` (opus); `flechodiezx`, `flechodiezx-hotfix` (opus, orquestan); `dora-exploradora` (sonnet, investiga) |
| `commands/` | Slash commands `/timonel:*` (lanzadores delgados, haiku/sonnet) |
| `skills/` | Skills de implementación y del ciclo + `github-issues` (plantillas/recetas) + `retro-tools` + `disenar` (diseño de front entregado como canvas de Claude Design; sus scripts node viven en `skills/disenar/scripts/`) |
| `scripts/` | `_common.sh`, `setup-github-labels.sh`, `install-git-hooks.sh`, `timonel_gh.py`, `retro_query.py`, `retro_distill.py`, `review_query.py`, `review_distill.py`, `metricas_flujo.py`, `migrate_backlog.py`, y los sensores `dor_check.py`, `validar_marcador.py`, `estado_historia.py`, `contrato_check.py`, `cosechar_retro.py`, `smoke_harness.py`, `eval_dor.py`, `integracion.py`, `guard_integracion.py` (guard de integracion por PR: bloquea `push`/`merge`/`pull`/`gh pr merge` que crean integracion nueva sin PR, epica #79), `detectar_commit.py` (sensor de tres respuestas —sí/no/no sé— que reconoce si un comando invoca un `git commit` real, reutilizando el tokenizador de `guard_integracion.py`; lo consumen los guards de commit de `hooks/hooks.json`, #176), `verificar_plugin.py` (la verificacion de "Verificación antes de commit" con una sola salida: corre unittest, `bash -n` por archivo y `jq` —siempre los tres— y termina en `PASSED`\|`FAILED: <paso>`\|`no sé: <motivo>`, exit 0/1/2; un paso sin evidencia jamas se lee como pasado, #270), `pr_check.py` (item 1 del DoD: traduce `gh pr list --head <rama>` — mergeable, mergeStateStatus, statusCheckRollup — a `PASSED`\|`FAILED` con criticidad y motivo; el PR lo descubre por la rama actual, nunca por un `pr_url` que le pasen, #81), `mutar_literal.py` (borra o mueve un literal fuera de su sección en una copia temporal del cwd, comprueba que la mutación entró y reporta `fallos: N de M` de unittest; "no sé" —mutación que no entra, objetivo sin tests— es exit 2, nunca `0 de M`, #263) |
| `heuristics/` | Heurísticas de código que consumen `code-review` **e** `implement-plugin-change` **por glob**, nunca por lista de nombres: `general/*.md` siempre y `<stack>/*.md` según el alcance (ver "Heurísticas" abajo) |
| `evals/` | Casos `prompt.md` + `graders/*.md` para `claude plugin eval`, uno por agente/skill (`evals/<agente-o-skill>/<caso>/`) |
| `hooks/` | `SessionStart`: `.timonel/.plugin-root`, aviso de config faltante, aviso de versión nueva. `PreToolUse`: issue obligatorio en commits del plugin; en consumidores protege ramas base, bloquea `push --force` (incondicional, tambien en perfil plugin), exige `#N` en ramas `hu/`, y ejecuta `scripts/guard_integracion.py` para bloquear `push`/`merge`/`pull` que integren la rama base sin PR y todo `gh pr merge` (TIM-ADR-0005, epica #79/#80); los dos guards de commit deciden **cuándo** correr con `scripts/detectar_commit.py`, con la compuerta literal de antes como respaldo si el sensor no responde (#176). `PostToolUse`: loguea `gh issue *` y avisa al editar archivos raíz (`modelos.path`, `moduleFile`, `routesFile`) |
| `docs/adr/` | `TIM-ADR-000N` decisiones del marco |
| `docs/superpowers/specs/` | Spec de diseño de la extracción |
| `.github/workflows/ci.yml` | CI: unittest, `bash -n`, `jq`, labels dry-run, `#issue` en commits de PR |
| `tests/` | `unittest` stdlib; `python3 -m unittest discover -s tests` |

## Gobernanza (TIM-ADR-0005)

- **Todo cambio nace en un issue de este repo** (`tipo:hu|hotfix`, `mod:plugin`, agrupado en la épica de la versión). Capturalo con `/timonel:draft` o `gh issue create -R luisfelipediaz/Harness.Timonel`.
- **Todo commit referencia el issue** (`#N`). Lo exige `.githooks/commit-msg` (activar con `scripts/install-git-hooks.sh`) y el hook `PreToolUse` del plugin cuando corre en este repo.
- **El plugin se desarrolla con Timonel** (#27): `/timonel:implement #N` detecta el perfil `plugin` (sin nx): rama `hu/N-*` en un **worktree aislado**, Dora, contrato de cambio, sub-agente con `implement-plugin-change`, consolidación (unittest, `bash -n`, `jq`, línea en CHANGELOG), review, retro (+ cosecha), **PR obligatorio con merge humano** (única vía de integración, en cualquier perfil) con `integracion.py` antes del DoD, y DoD. El hook bloquea `git commit` en `main` de forma incondicional; `/timonel:onboard` puede además activar protección de rama server-side en el repo consumidor (#85).
- Al cerrar la épica de la versión: `git tag vX.Y.Z` + `gh release create`; CHANGELOG pasa de "en desarrollo" a la fecha; se cierran los issues con el comentario de DoD.

## Convenciones al editar el plugin

- Agentes: frontmatter `name`, `description`, `model`, `color`, `skills: [github-issues]`. Empiezan leyendo el config y resolviendo `PLUGIN_ROOT`.
- Commands: frontmatter `description`, `argument-hint`, `model`. No implementan: validan y lanzan al agente.
- Skills: frontmatter `name`, `description` (con cuándo usarlo). Parámetros de entrada en tabla; reporte de salida en bloque fijo.
- Cambios de formato de issue/comentario → actualizar `skills/github-issues/references/*.md`, `scripts/timonel_gh.py` y sus tests en el mismo commit.
- Versionado semver en `plugin.json` + entrada en `CHANGELOG.md`.
- Python: stdlib puro, sin `any`-equivalentes (`dict` tipados), lookup maps antes que cadenas de `if` (ver `heuristics/general/evitar-ifs.md`).

## Heurísticas (`heuristics/`)

Las consumen `code-review` (Paso 1 y §3.4) e `implement-plugin-change` **por glob**, nunca por una lista de nombres: un archivo nuevo entra al ciclo con solo crearlo. Enumerarlas por nombre en un skill es el defecto que la HU #134 corrigió.

| Carpeta | Cuándo se lee | Cómo la alcanza el glob |
| --- | --- | --- |
| `general/` | **Siempre**, en todo perfil y todo alcance | `general/*.md` |
| `angular/` | Alcance `Frontend` o `Full-stack` con `stack.frontend` = `"Angular …"` | `<stack>/*.md` |

**Regla de derivación de `<stack>`**: primer token en minúsculas de `stack.frontend` (alcance `Frontend`/`Full-stack`) y de `stack.backend` (`Backend`/`Full-stack`). `"Angular 21"` → `angular`; `"NestJS 10"` → `nestjs`. **Nunca** de `stack.estado` (daría `ngrx`, carpeta inexistente). Solo si la carpeta existe; el perfil plugin (sin config) degrada a `general/*.md`. Una carpeta que ninguna derivación alcanza es código muerto: o se documenta en esta tabla con la regla que la alcanza, o no se crea — `HeuristicasDescubriblesTests` lo verifica.

**Formato de cada heurística**: `# Heurística: <regla en una frase>` · `## Regla general` · `## Qué hacer` (con ejemplo mal/bien) · `## Señales de que lo estás haciendo mal` · `## Caso real` (issue + commit + cita textual de la evidencia, sin parafrasear) · `## Cuándo NO aplica` · `## Relación con otras heurísticas` (en qué se distingue de su vecina y cuándo invocar una y no la otra).

**Opt-out explícito**: un archivo de `heuristics/` que no sea una heurística (el catálogo de convenciones de un consumidor) lo declara con el sufijo literal ` — catálogo` en su H1. Es la única exención de las invariantes de formato, y es una declaración positiva a propósito: usar "el H1 no empieza con `# Heurística:`" sería *default-out* y dejaría exento en silencio a un archivo con el encabezado mal escrito.

## Verificación antes de commit

```bash
python3 scripts/verificar_plugin.py   # unittest + bash -n + jq; ultima linea PASSED | FAILED: <paso> | no sé: <motivo> (exit 0/1/2)
claude plugin eval . --no-publish   # early access: si responde "currently in early access", deja los casos y sigue
```
