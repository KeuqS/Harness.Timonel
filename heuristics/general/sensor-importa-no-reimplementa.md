# Heurística: Un sensor que necesita un cálculo que otro sensor ya resuelve lo importa — no lo reimplementa

## Regla general

Esta heurística registra un **acierto**, no una cicatriz: es un *"seguí haciendo esto"*.

Cuando escribís un sensor nuevo (una métrica, un check, un reporte) y necesitás un cálculo que otro sensor del mismo harness ya hace —contar eventos de un log, resolver el origen de una retro, formatear una fila de estado, leer el repo del config—, **importalo**. No porque duplicar sea feo, sino por algo más concreto: ese cálculo ya incorpora decisiones que costaron una retro descubrir (el ancla exacta con la que se cuenta, el tercer estado `sin datos`, los casos degenerados), y una reimplementación las pierde todas en silencio. Importar **hereda gratis** el aprendizaje; reimplementar lo revierte a la versión ingenua sin que nadie lo note, porque el número que devuelve sigue siendo plausible.

## Qué hacer

**Mal — el sensor nuevo recuenta por su cuenta:**

```python
def contar_gh(lineas: list[str]) -> int:
    return sum(1 for l in lineas if "[gh]" in l)   # `in`, no ancla al inicio
```

Plausible, y distinto: cuenta también las menciones de `[gh]` en prosa, y no tiene forma de decir "sin datos" cuando el log no existe.

**Bien — importar del sensor que ya lo resolvió:**

```python
from smoke_harness import contar_eventos, fila_hook
from timonel_gh import fetch_issues, find_marker_comment, label_value, origen_retro, repo_from_config, upsert_issue_by_title
```

Con esas dos líneas el sensor nuevo hereda el conteo por ancla, la distinción `None` / `0` / `N`, y el estado `sin datos` — sin decidir nada.

Cuando el cálculo existe pero **no** es importable (está embebido en un `main()`, o acoplado a `gh`), el movimiento correcto no es copiarlo: es **extraerlo a una función pura** en el sensor original y que los dos la importen. Ese refactor es parte del trabajo del sensor nuevo, no un pendiente.

## Señales de que lo estás haciendo mal

- Dos sensores reportan el "mismo" número y dan valores distintos sobre la misma corrida.
- Tu sensor nuevo tiene un helper de tres líneas cuyo nombre ya existe en otro script del repo.
- Copiaste una expresión regular o un ancla desde otro archivo en vez de importarla.
- El sensor nuevo devuelve `0` donde el viejo devuelve `sin datos`.
- Justificás la copia con *"el otro hace un poco más de lo que necesito"*: casi siempre ese "poco más" es exactamente el caso que la retro descubrió.

## Caso real

**`metricas_flujo.py` importó en vez de reimplementar (#51, retro #30(5)).** Es el caso **positivo** del conjunto: se hizo bien, y por eso se registra — para que la próxima vez no dependa de que alguien se acuerde.

`scripts/metricas_flujo.py:25-26`:

```python
from smoke_harness import contar_eventos, fila_hook
from timonel_gh import fetch_issues, find_marker_comment, label_value, origen_retro, repo_from_config, upsert_issue_by_title
```

La retro #30 lo registró así, textual:

> Un sensor nuevo que necesita un cálculo que otro sensor ya hace debe **importarlo**, no reimplementarlo: `metricas_flujo` importó `contar_eventos`/`fila_hook` de `smoke_harness` y `origen_retro` de `timonel_gh`, y con eso heredó gratis el conteo por ancla y el estado `sin datos` que la retro #29 había costado descubrir.

El contra-hecho no es un bug que ocurrió, sino uno que **no** ocurrió: el estado `sin datos` de `fila_hook` —la distinción entre `None` (no hay `events.log`) y `0` (el perfil no tiene ese consumidor)— llegó a `metricas_flujo.py:128-129` sin que su autor tuviera que redescubrirla. Una reimplementación habría devuelto `0` en los dos casos, con la misma cara de correcta.

## Cuándo NO aplica

- **Cuando la semántica es parecida pero no la misma**: dos sensores pueden contar "eventos" con anclas legítimamente distintas. Importar lo equivocado es peor que reimplementar, porque el acoplamiento hace creer que están alineados. Si diferís, diferí explícito y documentá en qué.
- **Cuando importar crea un ciclo o arrastra dependencias pesadas** (`gh`, red, credenciales) a un sensor que debía ser puro: ahí extraé la función pura del original y que los dos la importen, en vez de importar el módulo entero.
- **Cuando el "cálculo" son tres líneas sin ninguna decisión adentro** (un `len`, un `sorted`): el acoplamiento cuesta más que la copia. Lo que justifica importar es el **aprendizaje incorporado**, no la cantidad de líneas.
- **Cuando la copia es inevitable** (un hook shell no puede importar Python): queda atada por un test de paridad y el CHANGELOG nombra ambas ubicaciones. Sin ese test, si una copia cambia la otra deriva en silencio: lo señaló el warning 3 del review de #210 (la regla "el padre del common-dir solo vale si se llama `.git`" escrita en `timonel_gh.resolver_config_path` y en la compuerta shell de `hooks/hooks.json`), y `ParidadCompuertaResolverTests` (#283) la ata comparando ambas decisiones sobre los mismos repos de juguete.
- **Fuera del harness**: entre dominios de negocio distintos, compartir un cálculo porque "se parece" es el camino a la abstracción prematura.

## Relación con otras heurísticas

- **`sensor-declara-su-evidencia.md`** es su par y se invocan en momentos distintos del mismo sensor: aquélla al diseñar **qué reporta** (evidencia, ancla, tres estados); ésta al escribir **de dónde sale el número**. La conexión es causal: importar es el mecanismo más barato de cumplir aquélla, porque el ancla y el tercer estado vienen dentro del cálculo importado. Si reimplementás, tenés que volver a satisfacer aquélla a mano — y ahí es donde se pierde.
- **`no-tipos-espejo.md`** es el mismo principio sobre tipos en vez de cálculos: un segundo tipo que duplica a otro y un segundo contador que duplica a otro fallan igual, por divergencia silenciosa.
- **`usar-pipes-existentes.md`** (Angular) es la versión de esta regla frente al framework: no reimplementar lo que la plataforma ya resolvió. Ésta mira hacia adentro del propio harness; aquélla hacia afuera, al framework.
