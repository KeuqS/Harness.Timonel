---
name: code-review
description: Revisa el codigo implementado contra los criterios de aceptacion del issue de GitHub, clasifica hallazgos por severidad (CRITICO | WARNING) y publica el review como comentario timonel:review en el issue con su label review:*. Usa como Fase 5.5 del flechodiezx (y Fase 3 del flechodiezx-hotfix), tras consolidate-story y antes de generate-retro. El veredicto bloquea el DoD cuando hay hallazgos criticos.
---

Revisa la implementacion de una historia contra su issue y las convenciones del proyecto, clasifica cada hallazgo por severidad y publica el review en el issue. Siempre en espanol.

## Parametros de entrada

| Parametro | Descripcion |
| --- | --- |
| `issue` | Numero del issue |
| `repo` | `owner/repo` |
| `modulo` | Modulo destino |
| `alcance` | `Backend` \| `Frontend` \| `Full-stack` |
| `archivos_modificados` | Lista consolidada (ARCHIVOS_BACKEND + ARCHIVOS_FRONTEND + MODELOS_COMPARTIDOS) |
| `plugin_root` | Raiz absoluta del plugin (para heuristicas y plantillas) |
| `tests_primera_corrida` | **Opcional.** El bloque `Tests verdes en su primera corrida` del sub-agente, tal cual. Ausente = no informado |
| `directorio_trabajo` | **Opcional.** Ruta del worktree donde esta checkouteada la rama a revisar (default: el directorio actual). En el modo "rama del usuario" del orquestador es la ruta del usuario |

**`directorio_trabajo`**: el `git diff <rama-base>...HEAD` del Paso 2 corre **ahi** (`git -C <directorio_trabajo>`), porque `HEAD` es la rama de ese directorio, no la del arbol del orquestador. Sin el parametro, corre en el directorio actual, como antes.

Si falta alguno de los obligatorios, detente y reporta.

## Paso 1: Cargar contexto

En paralelo:

- **Issue**: `gh issue view <issue> -R <repo> --json title,body,labels,comments`. Extrae Gherkin, Ficha tecnica, Tipo de operacion.
- **Retro previa** del modulo: `python3 <plugin_root>/scripts/retro_query.py --modulo <modulo> --seccion "Errores recurrentes"`.
- **Convenciones del consumidor (mandan)**: `CLAUDE.md` y todo `.claude/rules/*.md` si existe. Extrae de ahi la lista de reglas verificables; solo lo que no este cubierto se toma del **catalogo de convenciones del stack**: el archivo que el glob del punto siguiente trae desde `<stack>/` y cuyo H1 termina en ` — catálogo`.
- **Heuristicas** (descubrimiento por glob, **sin lista de nombres** en este skill): `HEUR=$(jq -r '.heuristicsDir // empty' .claude/timonel.config.json 2>/dev/null); HEUR="${HEUR:-<plugin_root>/heuristics}"; ls "$HEUR"/general/*.md "$HEUR"/<stack>/*.md 2>/dev/null` → lee **todas**. `general/*.md` siempre, en todo perfil. `<stack>` = primer token en minusculas de `stack.frontend` (alcance `Frontend`/`Full-stack`) y de `stack.backend` (`Backend`/`Full-stack`): `"Angular 21"` → `angular`, `"NestJS 10"` → `nestjs`; solo si la carpeta existe. Nunca derives el stack de `stack.estado`. Perfil plugin (sin config): degrada a `general/*.md`. Un archivo nuevo en `heuristics/` entra al review con solo crearlo; si tuvieras que editar este skill para leerlo, el descubrimiento esta roto.
- **Contrato vs codigo (sensor)**: `python3 <plugin_root>/scripts/contrato_check.py <issue>` → tabla PASSED/FAILED por endpoint declarado en `timonel:contrato-api`.
- **Formato**: `<plugin_root>/skills/github-issues/references/marcadores.md`, seccion `timonel:review`.

Si el issue no declara Gherkin verificable, registra WARNING tipo `Gherkin` "issue no declara criterios Gherkin verificables" y continua.

## Paso 2: Localizar el codigo

`archivos_modificados` es la lista autorizada. Complementa con `git -C <directorio_trabajo> diff <rama-base>...HEAD --name-only` si hace falta. No audites el resto del repo.

## Paso 3: Revision con severidad

- **CRITICO**: un criterio Gherkin no se cumple en el codigo, o un test que lo cubre directamente falla, o falta el unico test que lo verificaria.
- **WARNING**: todo lo demas.

### 3.1 Gherkin (`Gherkin`)
Por cada escenario, busca la logica concreta que lo implementa. No cumplido → CRITICO.

### 3.2 Ficha tecnica (`Ficha`)
Endpoints: pega la tabla de `contrato_check.py`; cada `FAILED` es un hallazgo `Ficha` (CRITICO si el endpoint sostiene un Gherkin). Permisos (`AuthorizationGuard<TiposDePermisos>` correcto), Auditoria (`@AuditoriaApi`), Modelos (compartidos desde `modelos.alias`, no redeclarados), Sesion propagada controller → aplicacion → servicio. WARNING salvo que rompa un Gherkin.

### 3.3 Convenciones (`Convención`)
Las reglas salen del `CLAUDE.md` / `.claude/rules/` del consumidor y, en lo no cubierto, del **catalogo de convenciones del stack** leido en el Paso 1 (el de H1 ` — catálogo`). No mantengas listas de reglas en este skill. Todo WARNING.

### 3.4 Heuristicas (`Heurística`)
Aplica **todas** las heuristicas que el glob del Paso 1 trajo a disco (`general/*.md` + `<stack>/*.md`), salvo las de H1 terminado en ` — catálogo`, que son convenciones y se revisan en 3.3. Este skill **no enumera heuristicas por nombre**: la lista es el contenido del directorio. WARNING.

### 3.5 Tests (`Tests`)
`.spec.ts` para codigo nuevo; cubren los Gherkin; usa `TESTS_RESULTADO` de consolidacion (no re-ejecutes salvo duda). Faltantes no directos de Gherkin → WARNING; directos → CRITICO con tipo `Gherkin`.

Con `tests_primera_corrida` informado: `No medido: <motivo>` → WARNING "evidencia faltante: tests no vistos fallar", nunca lo cuentes como verificado; una linea sin mutacion o sin conteo `fallos: N de M` → WARNING; una con N=0 es un test vacuo → WARNING. Si la linea de `mutar_literal.py` trae `base: B de M` con B > 0 (la suite ya fallaba sin mutar), la evidencia es `N > B`: con `N <= B` el test es vacuo → WARNING; sin linea `base:` sigue valiendo N=0. Es la misma regla que aplica `implement-plugin-change`. Ausente el parametro, no lo infieras: es "no informado".

### 3.6 Retro previa
Si un error recurrente documentado se repite, registralo (WARNING salvo que viole un Gherkin).

## Paso 4: Veredicto

`APROBADO` (0/0) · `APROBADO CON OBSERVACIONES` (0 criticos, ≥1 warning) · `REQUIERE CAMBIOS` (≥1 critico). `BLOQUEA_DOD = si` solo con `REQUIERE CAMBIOS`.

## Paso 5: Publicar en el issue

1. Escribe el comentario con el formato exacto `timonel:review` de `marcadores.md` (YAML plano + Resumen + tabla Hallazgos + Veredicto + Pasos sugeridos si aplica) en un archivo temporal.
2. **Valida**: `python3 <plugin_root>/scripts/validar_marcador.py <archivo> --tipo review`. Corrige hasta que salga 0 (conteos vs tabla, `bloquea_dod` coherente con el veredicto).
3. `publicar_marcador <issue> review <archivo>` (receta del skill `github-issues`: edita si ya existe).
4. Label exclusivo: `cambiar_label_exclusivo <issue> review <aprobado|observaciones|requiere-cambios>`.
5. Marca `- [x] Code review` en `## Tareas`.
6. **No hagas commit** de nada.

## Reporte de salida (obligatorio)

```
VEREDICTO: APROBADO | APROBADO CON OBSERVACIONES | REQUIERE CAMBIOS
HALLAZGOS_CRITICOS: <n>
HALLAZGOS_WARNING: <n>
REVIEW_PUBLICADO: si | no
REVIEW_VALIDO: si | no
CONTRATO_CHECK: PASSED | FAILED | N/A
BLOQUEA_DOD: si | no
RESUMEN: <1-2 lineas>
```

## Manejo de errores

- Issue no legible → `VEREDICTO: NO_GENERADO` + `MOTIVO`. No inventes la spec.
- Fallo al publicar → devuelve igual el bloque con conteos y el cuerpo del review en tu respuesta, `REVIEW_PUBLICADO: no`.
- Review previo existente → se sobreescribe (el codigo cambio).
