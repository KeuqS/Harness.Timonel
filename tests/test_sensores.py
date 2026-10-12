import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import contrato_check as cc  # noqa: E402
import cosechar_retro as cr  # noqa: E402
import dor_check as dc  # noqa: E402
import estado_historia as eh  # noqa: E402
import eval_dor as ed  # noqa: E402
import integracion as ig  # noqa: E402
import metricas_flujo as mf  # noqa: E402
import pr_check as pc  # noqa: E402
import timonel_gh as tg  # noqa: E402
import validar_marcador as vm  # noqa: E402
from test_markers import RETRO_COMMENT, REVIEW_COMMENT  # noqa: E402

HU_OK = {
    "number": 42, "title": "Registrar gasto con categoría", "parent": {"number": 3},
    "label_names": ["tipo:hu", "estado:listo", "alcance:full-stack", "sp:5", "mod:mis-finanzas", "moscow:must", "prioridad:alta"],
    "body": """## Historia

**Como** empleado, **quiero** registrar un gasto, **para** controlar mis finanzas.

## Criterios de aceptación

```gherkin
DADO QUE estoy en el dashboard
CUANDO registro un gasto
ENTONCES aparece en la lista
```

## Ficha técnica

| Campo | Valor |
| --- | --- |
| Alcance | Full-stack |
| Módulo destino | mis-finanzas |

## Endpoints

- `POST /api/mis-finanzas/gastos`

## Modelos compartidos

- `Gasto { monto: number }`

## Dependencias

Ninguna

## Tareas

- [x] Contrato API aprobado
- [x] Modelos compartidos
- [x] Backend
- [ ] Frontend
- [ ] Consolidación (lint + tests)
- [ ] Code review
- [ ] Retrospectiva
- [ ] Definition of Done
""",
}


HOTFIX_OK = {
    "number": 83, "title": "Aislar flechodiezx-hotfix con worktree y PR",
    "label_names": ["tipo:hotfix", "estado:listo", "alcance:backend", "sp:2", "mod:plugin"],
    "body": """## Historia

Corrige que flechodiezx-hotfix integraba directo a la rama actual.

## Criterios de aceptación

```gherkin
DADO QUE se invoca /timonel:hotfix N
CUANDO flechodiezx-hotfix arranca
ENTONCES crea fix/N-slug en un worktree aislado y abre PR
```

## Ficha técnica

| Campo | Valor |
| --- | --- |
| Alcance | Backend |
| Módulo destino | plugin |

## Endpoints

Ninguno

## Modelos compartidos

Ninguno

## Dependencias

Ninguna

## Tareas

- [ ] Implementación
- [ ] Lint + tests afectados
- [ ] Code review
- [ ] PR abierto
- [ ] Definition of Done
""",
}


class ValidarMarcadorTests(unittest.TestCase):
    def test_review_valido(self):
        self.assertEqual(vm.validar(REVIEW_COMMENT), [])

    def test_retro_sin_harness_engineering_falla(self):
        problemas = vm.validar(RETRO_COMMENT)
        self.assertTrue(any("Harness engineering" in p for p in problemas))

    def test_retro_completa_valida(self):
        retro = RETRO_COMMENT.replace("precision_estimacion: subestimado", "precision_estimacion: subestimado") + "\n### Harness engineering\n\n- ¿Surgió un patrón repetitivo? → Ninguno\n"
        self.assertEqual(vm.validar(retro), [])

    def test_review_inconsistente(self):
        malo = REVIEW_COMMENT.replace("warnings: 2", "warnings: 1").replace("bloquea_dod: no", "bloquea_dod: si")
        problemas = vm.validar(malo)
        self.assertTrue(any("no coincide con la tabla" in p for p in problemas))
        self.assertTrue(any("bloquea_dod" in p for p in problemas))

    def test_precision_incoherente(self):
        malo = RETRO_COMMENT.replace("precision_estimacion: subestimado", "precision_estimacion: preciso") + "\n### Harness engineering\n- Ninguno\n"
        self.assertTrue(any("deberia ser `subestimado`" in p for p in vm.validar(malo)))

    def test_sin_marcador(self):
        self.assertIn("la primera linea debe ser el marcador `<!-- timonel:<tipo> -->`", vm.validar("hola"))


DOD_OK = """<!-- timonel:dod -->
## Definition of Done

```yaml
fecha: 2026-09-10
decision: DONE
```

| # | Item | Estado |
| --- | --- | --- |
""" + "\n".join(f"| {i} | Item {i} | {'SKIPPED' if i in (5, 7) else 'PASSED'} |" for i in range(1, 12)) + """

Item 11 · veredicto: APROBADO

### Decisión final

**DONE**
"""


class ValidarDodTests(unittest.TestCase):
    def test_dod_completo_valido(self):
        self.assertEqual(vm.validar(DOD_OK), [])

    def test_dod_con_6_filas_y_review_skipped_es_invalido(self):
        malo = "<!-- timonel:dod -->\n## DoD\n\n```yaml\nfecha: 2026-09-10\ndecision: DONE\n```\n\n| # | Item | Estado |\n| --- | --- | --- |\n| 1 | a | PASSED |\n| 2 | b | PASSED |\n| 4 | c | PASSED |\n| 8 | d | PASSED |\n| 10 | e | PASSED |\n| 11 | Code review | SKIPPED |\n\n### Decisión final\n**DONE**\n"
        problemas = vm.validar(malo)
        self.assertTrue(any("11 filas" in p for p in problemas))
        self.assertTrue(any("Item 11 · veredicto" in p for p in problemas))
        self.assertTrue(any("fila 11 en PASSED" in p for p in problemas))

    def test_dod_done_con_veredicto_11_no_generado_es_invalido(self):
        # Fila 11 dice PASSED pero la linea de veredicto dice NO_GENERADO: el
        # sensor no debe confiar solo en el estado de la fila (gap de #28).
        malo = DOD_OK.replace("Item 11 · veredicto: APROBADO", "Item 11 · veredicto: NO_GENERADO")
        problemas = vm.validar(malo)
        self.assertTrue(any("NO_GENERADO" in p for p in problemas))

    def test_dod_done_con_veredicto_11_skipped_es_invalido(self):
        malo = DOD_OK.replace("Item 11 · veredicto: APROBADO", "Item 11 · veredicto: SKIPPED")
        problemas = vm.validar(malo)
        self.assertTrue(any("SKIPPED" in p for p in problemas))

    def test_dod_perfil_plugin_con_fila_9_failed_es_invalido(self):
        malo = (
            DOD_OK.replace("decision: DONE\n```", "decision: DONE\nperfil: plugin\n```")
            .replace("| 9 | Item 9 | PASSED |", "| 9 | Item 9 | FAILED |")
        )
        problemas = vm.validar(malo)
        self.assertTrue(any("fila 9" in p for p in problemas))

    def test_dod_perfil_consumidor_con_fila_9_failed_y_pendientes_es_valido(self):
        bueno = (
            DOD_OK.replace("decision: DONE\n```", "decision: PENDIENTES\nperfil: consumidor\n```")
            .replace("| 9 | Item 9 | PASSED |", "| 9 | Item 9 | FAILED |")
        )
        self.assertEqual(vm.validar(bueno), [])

    def test_dod_perfil_desconocido_es_invalido(self):
        malo = DOD_OK.replace("decision: DONE\n```", "decision: DONE\nperfil: otro\n```")
        problemas = vm.validar(malo)
        self.assertTrue(any("perfil" in p for p in problemas))

    def test_dod_ok_con_perfil_consumidor_explicito_sigue_valido(self):
        bueno = DOD_OK.replace("decision: DONE\n```", "decision: DONE\nperfil: consumidor\n```")
        self.assertEqual(vm.validar(bueno), [])

    def test_dod_ok_sin_perfil_sigue_valido_por_retrocompatibilidad(self):
        # DOD_OK ya no declara `perfil`: los DoD historicos deben seguir siendo validos.
        self.assertNotIn("perfil", vm.parse_yaml_plano(DOD_OK))
        self.assertEqual(vm.validar(DOD_OK), [])


class NumeroIssueTests(unittest.TestCase):
    """#231: los sensores aceptan `#N` ademas de `N` como numero de issue."""

    def test_acepta_con_y_sin_numeral(self):
        self.assertEqual(tg.numero_issue("223"), 223)
        self.assertEqual(tg.numero_issue("#223"), 223)
        self.assertEqual(tg.numero_issue(" #223 "), 223)

    def test_rechaza_lo_que_no_es_numero(self):
        for valor in ("#abc", "", "#", "##5"):
            with self.subTest(valor=valor):
                with self.assertRaises(argparse.ArgumentTypeError):
                    tg.numero_issue(valor)

    def test_dor_check_cli_acepta_numeral_sin_red(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "dor_check.py"
        resultado = subprocess.run(
            [sys.executable, str(script), "#223", "--json", json.dumps(HU_OK)],
            capture_output=True, text=True,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_sensores_cablean_numero_issue(self):
        """Cada sensor con posicional `issue` usa `numero_issue`: el rechazo de `#abc`
        sale con su mensaje, no con el `invalid int value` de `type=int` (que es lo
        que rechazaba tambien `#223`). argparse falla antes de cualquier llamada a gh."""
        for nombre in ("dor_check", "estado_historia", "contrato_check", "integracion", "cosechar_retro", "pr_check"):
            with self.subTest(sensor=nombre):
                script = Path(__file__).resolve().parents[1] / "scripts" / f"{nombre}.py"
                resultado = subprocess.run([sys.executable, str(script), "#abc"], capture_output=True, text=True)
                self.assertEqual(resultado.returncode, 2, resultado.stderr)
                self.assertIn("numero de issue invalido", resultado.stderr)


class DorCheckTests(unittest.TestCase):
    def test_hu_completa(self):
        self.assertEqual(dc.check(HU_OK), [])
        self.assertEqual(dc.check(HU_OK, "implementar"), [])

    def test_faltan_labels_y_secciones(self):
        hu = dict(HU_OK, label_names=["tipo:hu", "estado:borrador"], body=HU_OK["body"].replace("## Tareas", "## Otras"))
        faltantes = dc.check(hu, "implementar")
        self.assertTrue(any("`alcance:*`" in f for f in faltantes))
        self.assertTrue(any("Tareas" in f for f in faltantes))
        self.assertTrue(any("estado:listo" in f for f in faltantes))

    def test_por_definir_y_sp_grande(self):
        hu = dict(HU_OK, label_names=[l if not l.startswith("sp:") else "sp:21" for l in HU_OK["label_names"]], body=HU_OK["body"] + "\nPOR DEFINIR")
        faltantes = dc.check(hu)
        self.assertTrue(any("POR DEFINIR" in f for f in faltantes))
        self.assertTrue(any("supera el maximo" in f for f in faltantes))

    def test_hotfix_con_endpoints_no_es_hotfix(self):
        hf = dict(HU_OK, label_names=["tipo:hotfix", "estado:listo", "alcance:backend", "sp:2", "mod:x"])
        self.assertTrue(any("Ninguno" in f for f in dc.check(hf)))

    def test_hu_sin_padre(self):
        hu = dict(HU_OK, parent=None)
        self.assertTrue(any("sub-issue" in f for f in dc.check(hu)))

    def test_bloqueado_impide_implementar(self):
        hu = dict(HU_OK, label_names=HU_OK["label_names"] + ["bloqueado"])
        self.assertEqual(dc.check(hu), [])
        self.assertTrue(any("bloqueado" in f for f in dc.check(hu, "implementar")))


class EvalDorTests(unittest.TestCase):
    """Sensor del eval del planner (#32): DoR sobre la HU devuelta en la respuesta del agente."""

    def _respuesta(self, body: str, labels: str | None = "tipo:hu, alcance:full-stack, sp:5, mod:mis-finanzas, moscow:must, prioridad:alta") -> str:
        texto = f"Aca va la HU propuesta:\n\n```markdown\n{body}```\n"
        if labels is not None:
            texto += f"\nLabels: {labels}\n"
        return texto

    def test_extraer_hu_de_bloque_markdown_y_labels(self):
        hu = ed.extraer_hu(self._respuesta(HU_OK["body"]))
        self.assertEqual(hu["body"], HU_OK["body"])
        self.assertEqual(
            hu["label_names"],
            ["tipo:hu", "alcance:full-stack", "sp:5", "mod:mis-finanzas", "moscow:must", "prioridad:alta"],
        )
        self.assertEqual(len(hu["label_names"]), 6)
        self.assertEqual(hu["title"], "")

    def test_extraer_hu_sin_bloque_usa_todo_el_texto(self):
        hu = ed.extraer_hu("texto plano sin fences", title="Registrar gasto")
        self.assertEqual(hu["body"], "texto plano sin fences")
        self.assertEqual(hu["title"], "Registrar gasto")
        self.assertEqual(hu["label_names"], [])

    def test_evaluar_hu_completa_cumple_dor(self):
        self.assertEqual(ed.evaluar(self._respuesta(HU_OK["body"])), [])

    def test_evaluar_reporta_faltantes_si_falta_seccion_o_label(self):
        cuerpo_sin_tareas = HU_OK["body"].split("## Tareas")[0]
        faltantes = ed.evaluar(self._respuesta(
            cuerpo_sin_tareas,
            labels="tipo:hu, alcance:full-stack, mod:mis-finanzas, moscow:must, prioridad:alta",
        ))
        self.assertTrue(faltantes)
        self.assertTrue(any("Tareas" in f for f in faltantes))
        self.assertTrue(any("sp:" in f for f in faltantes))

    def test_evaluar_sin_labels_reporta_falta_label(self):
        faltantes = ed.evaluar(self._respuesta(HU_OK["body"], labels=None))
        self.assertTrue(any("label" in f for f in faltantes))


class EstadoHistoriaTests(unittest.TestCase):
    def test_reanuda_en_frontend(self):
        issue = dict(HU_OK, state="OPEN", comments=[{"body": "<!-- timonel:investigacion -->\nx"}, {"body": "<!-- timonel:contrato-api -->\nx"}])
        e = eh.estado(issue, rama="hu/42-registrar-gasto")
        self.assertEqual(e["reanudar_en"], "3 Frontend")
        self.assertIn("2 Contrato API", e["fases_hechas"])
        self.assertFalse(e["nueva"])

    def test_historia_nueva(self):
        body = HU_OK["body"].replace("- [x]", "- [ ]")
        issue = dict(HU_OK, body=body, state="OPEN", comments=[], label_names=[l for l in HU_OK["label_names"]])
        e = eh.estado(issue, rama=None)
        self.assertTrue(e["nueva"])
        self.assertEqual(e["reanudar_en"], "1.5 Investigacion")

    def test_alcance_backend_salta_frontend(self):
        issue = dict(HU_OK, state="OPEN", comments=[{"body": "<!-- timonel:investigacion -->"}], label_names=[l.replace("full-stack", "backend") for l in HU_OK["label_names"]])
        e = eh.estado(issue, rama="hu/42-x")
        self.assertNotIn("3 Frontend", e["fases_pendientes"])
        self.assertEqual(e["reanudar_en"], "4-5 Consolidacion")


    def test_cerrada_no_reanuda(self):
        issue = dict(HU_OK, state="CLOSED", comments=[{"body": "<!-- timonel:dod -->\nx"}])
        e = eh.estado(issue, rama=None)
        self.assertEqual(e["reanudar_en"], "cerrar")
        self.assertFalse(e["nueva"])

    def test_nueva_backend_sin_tareas(self):
        body = HU_OK["body"].replace("- [x]", "- [ ]")
        issue = dict(HU_OK, body=body, state="OPEN", comments=[], label_names=[l.replace("full-stack", "backend").replace("estado:listo", "estado:listo") for l in HU_OK["label_names"]])
        e = eh.estado(issue, rama=None)
        self.assertTrue(e["nueva"], "una HU backend sin trabajo debe ser nueva aunque Frontend cuente como N/A")

    COMENTARIOS_HASTA_DOD = [{"body": "<!-- timonel:investigacion -->\nx"}, {"body": "<!-- timonel:dod -->\nx"}]
    COMENTARIOS_SIN_DOD = [{"body": "<!-- timonel:investigacion -->\nx"}]

    def test_pr_pendiente_antes_de_dod(self):
        """Con el DoD y el PR ambos pendientes, la fase activa es 6.5 PR/Integración: el PR se abre antes del DoD."""
        body = HU_OK["body"].replace(
            "- [ ] Frontend\n- [ ] Consolidación (lint + tests)\n- [ ] Code review\n- [ ] Retrospectiva\n- [ ] Definition of Done",
            "- [x] Frontend\n- [x] Consolidación (lint + tests)\n- [x] Code review\n- [x] Retrospectiva\n- [ ] Definition of Done\n- [ ] PR abierto",
        )
        issue = dict(HU_OK, body=body, state="OPEN", comments=self.COMENTARIOS_SIN_DOD)
        e = eh.estado(issue, rama="hu/42-registrar-gasto")
        self.assertEqual(e["reanudar_en"], "6.5 PR/Integración")
        self.assertFalse(e["nueva"])

    def test_pr_marcado_dod_pendiente(self):
        """Con el PR ya marcado pero el DoD pendiente, la fase activa es 7 Definition of Done."""
        body = HU_OK["body"].replace(
            "- [ ] Frontend\n- [ ] Consolidación (lint + tests)\n- [ ] Code review\n- [ ] Retrospectiva\n- [ ] Definition of Done",
            "- [x] Frontend\n- [x] Consolidación (lint + tests)\n- [x] Code review\n- [x] Retrospectiva\n- [ ] Definition of Done\n- [x] PR abierto",
        )
        issue = dict(HU_OK, body=body, state="OPEN", comments=self.COMENTARIOS_SIN_DOD)
        e = eh.estado(issue, rama="hu/42-registrar-gasto")
        self.assertEqual(e["reanudar_en"], "7 Definition of Done")

    def test_sin_linea_pr_es_pendiente(self):
        """Decision aprobada del humano (#83): 'PR abierto' esta declarada en el checklist
        de tipo:hu, asi que su ausencia en el body (HUs anteriores a v0.6.0) ya no cuenta
        como hecha/SKIPPED por defecto -- cuenta como pendiente. Antes de #83 esta misma
        entrada (con el `.get(..., True)` viejo) marcaba la fase como hecha y la historia
        como `cerrar`; la semantica cambio con ella, por eso el test se reescribe en vez
        de solo actualizar el nombre."""
        body = HU_OK["body"].replace("- [ ]", "- [x]")
        issue = dict(HU_OK, body=body, state="OPEN", comments=self.COMENTARIOS_HASTA_DOD)
        e = eh.estado(issue, rama="hu/42-registrar-gasto")
        self.assertEqual(e["reanudar_en"], "6.5 PR/Integración")
        self.assertNotIn("6.5 PR/Integración", e["fases_hechas"])


class InvestigacionReutilizadaTests(unittest.TestCase):
    """`1.5 Investigacion` es la unica fase cuya evidencia puede vivir legitimamente en OTRO
    issue: la Fase 1.5 de flechodiezx autoriza reutilizar una investigacion vigente en vez
    de relanzar a Dora, y entonces el issue de la historia nunca recibe su comentario
    `timonel:investigacion`. Acepta por eso el marcador `timonel:contrato-api` como
    evidencia indirecta: el dominio garantiza que si hubo contrato hubo investigacion
    ("nunca redactes el contrato sin la investigacion", flechodiezx.md). Sin eso, una HU
    terminada que reutilizo investigacion reporta REANUDAR_EN: 1.5 Investigacion en vez de
    `cerrar` -- falso pendiente observable en la ventana que abrio #80 (toda HU terminada
    queda OPEN hasta el merge humano del PR).

    La tarea `Contrato API aprobado` NO cuenta como esa evidencia, aunque la fase
    `2 Contrato API` si la acepte: las dos fuentes no tienen la misma fuerza. Un marcador
    solo existe si alguien paso por `publicar_marcador` (y por `validar_marcador.py`); un
    checkbox lo tilda a mano cualquiera en la web de GitHub. El unico estado que la rama
    por tarea haria pasar por hecho -- contrato marcado sin ningun marcador -- es
    justamente una VIOLACION de la regla dura de flechodiezx.md, y dejar la fase pendiente
    ahi es la senal correcta, no un falso positivo (hallazgo del code review de #83)."""

    SIN_CONTRATO = HU_OK["body"].replace("- [x] Contrato API aprobado", "- [ ] Contrato API aprobado")
    TODO_HECHO = HU_OK["body"].replace("- [ ]", "- [x]").replace(
        "- [x] Definition of Done", "- [x] Definition of Done\n- [x] PR abierto")

    @staticmethod
    def _issue(body: str, marcadores: list[str]):
        return dict(HU_OK, body=body, state="OPEN",
                    comments=[{"body": f"<!-- timonel:{m} -->\nx"} for m in marcadores])

    def test_investigacion_propia_marca_la_fase(self):
        """Evidencia en el propio issue: el marcador basta, sin ninguna senal de contrato."""
        e = eh.estado(self._issue(self.SIN_CONTRATO, ["investigacion"]), rama="hu/42-x")
        self.assertIn("1.5 Investigacion", e["fases_hechas"])

    def test_investigacion_reutilizada_con_marcador_de_contrato(self):
        """Evidencia fuera del issue: no hay marcador de investigacion propio, pero si el
        comentario `timonel:contrato-api`, que solo existe si la Fase 2 corrio de verdad."""
        e = eh.estado(self._issue(self.SIN_CONTRATO, ["contrato-api"]), rama="hu/42-x")
        self.assertIn("1.5 Investigacion", e["fases_hechas"])

    def test_tarea_del_contrato_tildada_a_mano_no_cuenta_como_investigacion(self):
        """Contraejemplo hallado en el code review de #83: la tarea `Contrato API aprobado`
        marcada SIN ningun marcador no es un contrato aprobado por el pipeline -- es un
        checkbox que cualquiera tilda en la web. Ese estado viola la regla dura de
        flechodiezx.md (contrato sin investigacion), y el sensor debe seguir senalandolo
        como pendiente en vez de encubrirlo dandolo por hecho."""
        e = eh.estado(self._issue(HU_OK["body"], []), rama="hu/42-x")
        self.assertNotIn("1.5 Investigacion", e["fases_hechas"])
        self.assertEqual(e["reanudar_en"], "1.5 Investigacion")

    def test_sin_investigacion_ni_contrato_sigue_pendiente(self):
        """El guard contra la sobre-correccion: sin ninguna de las tres senales la fase
        sigue pendiente, que es lo correcto."""
        e = eh.estado(self._issue(self.SIN_CONTRATO, []), rama="hu/42-x")
        self.assertEqual(e["reanudar_en"], "1.5 Investigacion")

    def test_hu_terminada_que_reutilizo_investigacion_cierra(self):
        """El sintoma que motivo el arreglo, end-to-end: todo hecho, PR abierto, DoD
        publicado, investigacion reutilizada de otro issue -> `cerrar`, no un falso
        pendiente en la primera fase."""
        e = eh.estado(self._issue(self.TODO_HECHO, ["contrato-api", "dod"]), rama="hu/42-x")
        self.assertEqual(e["reanudar_en"], "cerrar")
        self.assertEqual(e["fases_pendientes"], [])


class RamaLocalTests(unittest.TestCase):
    """rama_local() itera PREFIJOS_RAMA (#83): antes solo miraba `hu/<N>-*`, asi que una
    rama `fix/N-slug` (creada por el hotfix aislado) se reportaba como 'ninguna'."""

    @staticmethod
    def _fake_run(salidas_por_patron: dict[str, str]):
        def run(cmd, capture_output=True, text=True):
            patron = cmd[-1]
            return subprocess.CompletedProcess(cmd, 0, stdout=salidas_por_patron.get(patron, ""), stderr="")
        return run

    def test_solo_rama_hu(self):
        with mock.patch.object(eh.subprocess, "run", side_effect=self._fake_run({"hu/83-*": "  hu/83-x\n"})):
            self.assertEqual(eh.rama_local(83), "hu/83-x")

    def test_solo_rama_fix(self):
        with mock.patch.object(eh.subprocess, "run", side_effect=self._fake_run({"fix/83-*": "  fix/83-x\n"})):
            self.assertEqual(eh.rama_local(83), "fix/83-x")

    def test_ambas_ramas_prefiere_la_primera_del_orden(self):
        with mock.patch.object(
            eh.subprocess, "run",
            side_effect=self._fake_run({"hu/83-*": "  hu/83-x\n", "fix/83-*": "  fix/83-y\n"}),
        ):
            self.assertEqual(eh.rama_local(83), "hu/83-x")

    def test_ninguna_rama(self):
        with mock.patch.object(eh.subprocess, "run", side_effect=self._fake_run({})):
            self.assertIsNone(eh.rama_local(83))

    def test_fix_de_otro_issue_no_matchea(self):
        with mock.patch.object(eh.subprocess, "run", side_effect=self._fake_run({"fix/8-*": "  fix/8-x\n"})):
            self.assertIsNone(eh.rama_local(83))


class TipoChecklistTests(unittest.TestCase):
    """label_value(labels, 'tipo') despacha al checklist/fases del tipo (#83); tipo
    ausente o desconocido cae al de `hu`."""

    def test_tipo_hotfix_usa_checklist_de_hotfix(self):
        issue = dict(HOTFIX_OK, state="OPEN", comments=[])
        e = eh.estado(issue, rama=None)
        self.assertEqual(e["fases_pendientes"][0], "2 Implementacion")

    def test_tipo_hu_no_regresion(self):
        issue = dict(HU_OK, state="OPEN", comments=[])
        e = eh.estado(issue, rama=None)
        self.assertIn("6.5 PR/Integración", e["fases_hechas"] + e["fases_pendientes"])

    def test_sin_label_tipo_cae_a_hu(self):
        labels = [l for l in HOTFIX_OK["label_names"] if not l.startswith("tipo:")]
        issue = dict(HOTFIX_OK, label_names=labels, state="OPEN", comments=[])
        e = eh.estado(issue, rama=None)
        self.assertIn("6.5 PR/Integración", e["fases_hechas"] + e["fases_pendientes"])

    def test_tipo_desconocido_cae_a_hu(self):
        labels = [l if not l.startswith("tipo:") else "tipo:epica" for l in HOTFIX_OK["label_names"]]
        issue = dict(HOTFIX_OK, label_names=labels, state="OPEN", comments=[])
        e = eh.estado(issue, rama=None)
        self.assertIn("6.5 PR/Integración", e["fases_hechas"] + e["fases_pendientes"])


class EstadoHistoriaHotfixTests(unittest.TestCase):
    def test_sin_pr_marcar_reanuda_en_pr(self):
        body = (HOTFIX_OK["body"]
                .replace("- [ ] Implementación", "- [x] Implementación")
                .replace("- [ ] Lint + tests afectados", "- [x] Lint + tests afectados")
                .replace("- [ ] Code review", "- [x] Code review"))
        issue = dict(HOTFIX_OK, body=body, state="OPEN", comments=[])
        e = eh.estado(issue, rama="fix/83-worktree-pr")
        self.assertEqual(e["reanudar_en"], "5 PR/Integración")

    def test_pr_marcado_con_dod_pendiente_reanuda_en_dod(self):
        body = HOTFIX_OK["body"].replace("- [ ]", "- [x]").replace("- [x] Definition of Done", "- [ ] Definition of Done")
        issue = dict(HOTFIX_OK, body=body, state="OPEN", comments=[])
        e = eh.estado(issue, rama="fix/83-worktree-pr")
        self.assertEqual(e["reanudar_en"], "6 DoD reducido")

    def test_todo_marcado_cierra(self):
        body = HOTFIX_OK["body"].replace("- [ ]", "- [x]")
        issue = dict(HOTFIX_OK, body=body, state="OPEN", comments=[{"body": "<!-- timonel:dod -->\nx"}])
        e = eh.estado(issue, rama="fix/83-worktree-pr")
        self.assertEqual(e["reanudar_en"], "cerrar")

    def test_linea_pr_ausente_es_pendiente_no_skipped(self):
        """Decision aprobada del humano (#83): con PR obligatorio, una linea 'PR abierto'
        ausente del body ya no cuenta como SKIPPED/hecha -- cuenta como pendiente."""
        body = (HOTFIX_OK["body"]
                .replace("- [ ] Implementación", "- [x] Implementación")
                .replace("- [ ] Lint + tests afectados", "- [x] Lint + tests afectados")
                .replace("- [ ] Code review", "- [x] Code review")
                .replace("- [ ] PR abierto\n", "")
                .replace("- [ ] Definition of Done", "- [x] Definition of Done"))
        issue = dict(HOTFIX_OK, body=body, state="OPEN", comments=[{"body": "<!-- timonel:dod -->\nx"}])
        e = eh.estado(issue, rama="fix/83-worktree-pr")
        self.assertEqual(e["reanudar_en"], "5 PR/Integración")
        self.assertNotIn("5 PR/Integración", e["fases_hechas"])


class FasesAusentesEnHotfixTests(unittest.TestCase):
    """El hotfix no tiene contrato, modelos, backend/frontend separados ni retro como
    fase del sensor (la retro es opcional y no forma parte del checklist)."""

    def test_hotfix_no_tiene_fases_de_hu(self):
        issue = dict(HOTFIX_OK, state="OPEN", comments=[])
        e = eh.estado(issue, rama=None)
        todas = e["fases_hechas"] + e["fases_pendientes"]
        for prohibida in ("Contrato API", "Backend", "Frontend", "Consolidacion", "Retrospectiva"):
            for nombre in todas:
                self.assertNotIn(prohibida, nombre, f"la fase `{nombre}` de hotfix no deberia mencionar `{prohibida}`")

    def test_checklist_hotfix_no_declara_tareas_de_hu(self):
        prohibidas = ("Contrato API aprobado", "Modelos compartidos", "Backend", "Frontend",
                      "Consolidación (lint + tests)", "Retrospectiva")
        for tarea in prohibidas:
            self.assertNotIn(tarea, eh.CHECKLISTS["hotfix"])


class CosecharRetroTests(unittest.TestCase):
    def test_extrae_mejoras_y_omite_ninguno(self):
        retro = RETRO_COMMENT + "\n### Harness engineering\n\n- ¿Surgió un patrón repetitivo? → Ninguno\n- ¿Un proceso manual se ejecutó 2+ veces? → Agregar hook que corra prettier tras Edit\n"
        items = cr.extraer_mejoras(retro)
        self.assertEqual(items, [("mejora", "Documentar Date.setMonth en heuristicas."), ("harness", "Agregar hook que corra prettier tras Edit")])

    def test_destino(self):
        self.assertEqual(cr.destino("Agregar en el skill ngrx-signal-store una nota sobre rxMethod"), "plugin")
        self.assertEqual(cr.destino("Agregar en CLAUDE.md la recomendación de state machine con flag"), "consumidor")

    def test_titulo_recortado(self):
        self.assertLessEqual(len(cr._titulo("x" * 100)), 70)

    def _retro_harness(self, *bullets: str) -> str:
        return "### Harness engineering\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n"

    def test_omite_las_dos_lineas_literales_de_la_retro_231(self):
        retro = self._retro_harness(
            "¿Faltó contexto que ralentizó? → Ninguno (la regla del orquestador ya está en Mejoras sugeridas)",
            "¿Un error pudo prevenirse automáticamente? → Ninguno (el test de cableado ya quedó en el commit e1e1e11)",
        )
        self.assertEqual(cr.extraer_mejoras(retro), [])

    def test_omite_variantes_de_ninguno_con_motivo(self):
        variantes = ["Ninguno", "Ninguno.", "Ninguno (motivo)", "Ninguna — motivo", "N/A: motivo", "No aplica (motivo)"]
        for v in variantes:
            with self.subTest(variante=v):
                retro = self._retro_harness(f"¿Surgió algo? → {v}")
                self.assertEqual(cr.extraer_mejoras(retro), [])

    def test_no_omite_palabra_que_solo_empieza_como_ninguno(self):
        for mejora in ("Ningunos de los tests cubren X", "Ninguno de los tests cubre X", "Ninguna heurística cubre el caso X"):
            with self.subTest(mejora=mejora):
                retro = self._retro_harness(f"¿Surgió algo? → {mejora}")
                self.assertEqual(cr.extraer_mejoras(retro), [("harness", mejora)])

    def test_titulo_conserva_guion_bajo_de_identificadores(self):
        for ident in ("retro_query.py", "contrato_check.py", "guard_integracion.py", "test_consistencia.py", "mutar_literal.py"):
            with self.subTest(ident=ident):
                self.assertEqual(cr._titulo(f"Agregar `{ident}` al sensor"), f"Agregar {ident} al sensor")

    def test_titulo_quita_enfasis_y_backticks(self):
        casos = {"Agregar *nota* al skill": "Agregar nota al skill", "Agregar **nota** al skill": "Agregar nota al skill",
                 "Agregar _nota_ al skill": "Agregar nota al skill", "Agregar `nota` al skill": "Agregar nota al skill"}
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(cr._titulo(entrada), esperado)


class ContratoCheckTests(unittest.TestCase):
    def test_extrae_y_verifica(self):
        contrato = "Metodo: POST\nRuta:   /api/mis-finanzas/ingreso-nomina/upsert\nMetodo: GET\nRuta: /api/mis-finanzas/movimientos/:id"
        endpoints = cc.extraer_endpoints(contrato)
        self.assertEqual(len(endpoints), 2)
        indice = [("apps/api/x.controller.ts", "mis-finanzas", [("POST", "ingreso-nomina/upsert"), ("GET", "movimientos/:movimientoId")])]
        res = cc.verificar(endpoints, indice)
        self.assertEqual([r[2] for r in res], ["PASSED", "PASSED"])
        res2 = cc.verificar([("DELETE", "/api/mis-finanzas/movimientos/:id")], indice)
        self.assertEqual(res2[0][2], "FAILED")

    def test_query_string_no_rompe(self):
        indice = [("c.ts", "organigrama", [("GET", "empleados")])]
        res = cc.verificar([("GET", "/api/organigrama/empleados?area=1&sede=2")], indice)
        self.assertEqual(res[0][2], "PASSED")


class IntegracionTests(unittest.TestCase):
    def test_tipo_remote_github(self):
        for url in ("https://github.com/o/r.git", "git@github.com:o/r.git"):
            self.assertEqual(ig.tipo_remote(url), "github")

    def test_tipo_remote_azure_devops(self):
        urls = (
            "https://dev.azure.com/org/proj/_git/repo",
            "https://org.visualstudio.com/proj/_git/repo",
            "git@ssh.dev.azure.com:v3/org/proj/repo",
        )
        for url in urls:
            self.assertEqual(ig.tipo_remote(url), "azure-devops")

    def test_tipo_remote_desconocido(self):
        self.assertEqual(ig.tipo_remote("https://gitlab.com/o/r"), "desconocido")

    def test_comando_pr_github(self):
        cmd = ig.comando_pr("https://github.com/o/r.git", "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md")
        for esperado in ("gh pr create", "--head hu/31-x", "--base main", "(#31)"):
            self.assertIn(esperado, cmd)

    def test_comando_pr_azure_devops(self):
        cmd = ig.comando_pr("https://dev.azure.com/org/proj/_git/repo", "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md")
        for esperado in ("az repos pr create", "--source-branch hu/31-x", "--organization https://dev.azure.com/org", "--project proj", "--repository repo"):
            self.assertIn(esperado, cmd)

    def test_comando_pr_visualstudio(self):
        cmd = ig.comando_pr("https://org.visualstudio.com/proj/_git/repo", "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md")
        self.assertIn("--organization https://org.visualstudio.com", cmd)

    def test_comando_pr_azure_ssh(self):
        cmd = ig.comando_pr("git@ssh.dev.azure.com:v3/org/proj/repo", "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md")
        for esperado in ("az repos pr create", "--organization https://dev.azure.com/org", "--project proj", "--repository repo"):
            self.assertIn(esperado, cmd)

    def test_comando_pr_draft_github(self):
        cmd = ig.comando_pr("https://github.com/o/r.git", "poc/x", "main", "Titulo (#31)", "/tmp/pr-31.md", draft=True)
        self.assertIn("gh pr create --draft ", cmd)

    def test_comando_pr_draft_azure_devops(self):
        import shlex
        for url in ("https://dev.azure.com/org/proj/_git/repo", "git@ssh.dev.azure.com:v3/org/proj/repo"):
            partes = shlex.split(ig.comando_pr(url, "poc/x", "main", "Titulo (#31)", "/tmp/pr-31.md", draft=True))
            i = partes.index("--draft")
            self.assertEqual(partes[i + 1], "true")

    def test_comando_pr_sin_draft_no_lo_contiene(self):
        for url in ("https://github.com/o/r.git", "https://dev.azure.com/org/proj/_git/repo"):
            self.assertNotIn("--draft", ig.comando_pr(url, "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md"))
            self.assertNotIn("--draft", ig.comando_pr(url, "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md", draft=False))

    def test_cli_acepta_flag_draft(self):
        import subprocess, sys
        proc = subprocess.run(
            [sys.executable, "scripts/integracion.py", "--tipo-remote", "--draft", "--remote-url", "https://github.com/o/r.git"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_comando_pr_escapa_titulo_con_apostrofo(self):
        import shlex
        titulo = "Fase 6.5: abrir el PR de 'hu/N-slug' y registrarlo (#31)"
        for url in ("https://github.com/o/r.git", "https://dev.azure.com/org/proj/_git/repo"):
            cmd = ig.comando_pr(url, "hu/31-x", "main", titulo, "/tmp/pr-31.md")
            partes = shlex.split(cmd)
            self.assertIn(titulo, partes, "el titulo debe sobrevivir intacto a shlex.split (eval en la Fase 6.5)")

    def test_comando_pr_azure_no_usa_command_substitution(self):
        cmd = ig.comando_pr("https://dev.azure.com/org/proj/_git/repo", "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md")
        self.assertNotIn("$(", cmd)
        self.assertIn("--description @/tmp/pr-31.md", cmd)

    def test_comando_pr_desconocido_lanza(self):
        with self.assertRaises(ValueError):
            ig.comando_pr("https://gitlab.com/o/r", "hu/31-x", "main", "Titulo (#31)", "/tmp/pr-31.md")

    def test_cuerpo_pr_sin_contrato(self):
        cuerpo = ig.cuerpo_pr(31, "luisfelipediaz/Harness.Timonel", "Titulo", None, DOD_OK)
        self.assertIn("Closes #31", cuerpo)
        self.assertIn("Sin contrato publicado", cuerpo)
        self.assertIn("| 1 |", cuerpo)

    def test_cuerpo_pr_corta_en_seccion_hermana_y_prefiere_contrato_de_cambio(self):
        contrato = ("<!-- timonel:contrato-api -->\n## Contrato API aprobado\n\n### Modelos compartidos\n\nNinguno.\n\n"
                    "### Endpoints\n\nNinguno — explícito.\n\n### Contrato de cambio\n\n| Archivo | Cambio |\n| --- | --- |\n| a.py | x |\n\n"
                    "### Invariantes nuevas\n\n- una invariante\n")
        cuerpo = ig.cuerpo_pr(31, "o/r", "Titulo", contrato, None)
        self.assertIn("| a.py | x |", cuerpo)
        self.assertNotIn("una invariante", cuerpo)
        self.assertNotIn("Ninguno — explícito", cuerpo)

    def test_cuerpo_pr_sin_dod(self):
        cuerpo = ig.cuerpo_pr(31, "luisfelipediaz/Harness.Timonel", "Titulo", "### Contrato de cambio\n\nTabla.", None)
        self.assertIn("DoD pendiente", cuerpo)
        self.assertNotIn("Sin DoD publicado", cuerpo)
        self.assertIn("Tabla.", cuerpo)

    def test_cuerpo_pr_primera_linea_es_keyword_y_cuerpo_cita_el_titulo(self):
        cuerpo = ig.cuerpo_pr(31, "o/r", "Abrir el PR antes del DoD", None, None)
        self.assertEqual(cuerpo.splitlines()[0], "Closes #31",
                         "la primera linea debe ser la keyword de cierre de GitHub; `Cierra #N` es prosa y no cierra nada")
        self.assertIn("Abrir el PR antes del DoD", cuerpo,
                      "el cuerpo debe decir que historia trae el PR, usando el titulo que `cuerpo_pr` ya recibe")
        self.assertNotIn("Cierra #31", cuerpo, "no se repite el cierre en prosa: duplica la referencia al issue")

    def test_cuerpo_pr_dod_none_contiene_keyword_contrato_y_dod_pendiente(self):
        contrato = "### Contrato de cambio\n\n| Archivo | Cambio |\n| --- | --- |\n| agents/flechodiezx.md | Fase 6.5 nueva |\n"
        cuerpo = ig.cuerpo_pr(80, "o/r", "Titulo", contrato, None)
        self.assertIn("Closes #80", cuerpo)
        self.assertIn("agents/flechodiezx.md", cuerpo)
        self.assertIn("DoD pendiente", cuerpo)

    def test_cuerpo_pr_con_dod_con_filas_sigue_rindiendo_tabla(self):
        cuerpo = ig.cuerpo_pr(31, "luisfelipediaz/Harness.Timonel", "Titulo", None, DOD_OK)
        self.assertIn("| 1 |", cuerpo)
        self.assertNotIn("DoD pendiente", cuerpo)


class PrCheckTests(unittest.TestCase):
    """Casos de `pr_check.py` (#81), uno por fila de la tabla de 27 casos del contrato.
    Las filas 19 y 22 (`verde` de CheckRun/StatusContext) son `-` por si solas: se
    verifican dentro de los casos compuestos 19+27 y 22+27 (PASSED completo)."""

    RAMA = "hu/31-x"
    ISSUE = 31
    BASE = "main"

    @staticmethod
    def _pr(**over):
        pr = {
            "number": 31, "url": "https://github.com/o/r/pull/31", "state": "OPEN",
            "isDraft": False, "baseRefName": "main", "headRefName": "hu/31-x",
            "body": "Closes #31\n\nTexto", "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
        }
        pr.update(over)
        return pr

    def _evaluar(self, prs, **over):
        args = dict(issue=self.ISSUE, rama=self.RAMA, base=self.BASE,
                     tipo_remote="github", hay_workflows=False)
        args.update(over)
        return pc.evaluar(prs, **args)

    # --- R0 remote / herramienta ---

    def test_caso1_remote_azure_devops_no_critico(self):
        v = self._evaluar([], tipo_remote="azure-devops")
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("azure-devops", v.motivo)

    def test_caso2_remote_desconocido_no_critico(self):
        v = self._evaluar([], tipo_remote="desconocido")
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("no reconocido", v.motivo)

    def test_caso3_gh_error_critico(self):
        v = self._evaluar([], gh_error="rate limit exceeded")
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("rate limit exceeded", v.motivo)

    # --- R1 rama / existencia ---

    def test_caso4_rama_no_corresponde_al_issue(self):
        v = self._evaluar([], rama="hu/99-otra")
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("#31", v.motivo)
        self.assertIn("hu/99-otra", v.motivo)

    def test_caso5_sin_prs(self):
        v = self._evaluar([])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("no hay PR abierto", v.motivo)
        self.assertIn(self.RAMA, v.motivo)

    def test_caso6_pr_cerrado_sin_merge(self):
        v = self._evaluar([self._pr(state="CLOSED")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("#31", v.motivo)
        self.assertIn("cerrado sin merge", v.motivo)

    def test_caso7_multiples_prs_abiertos(self):
        v = self._evaluar([self._pr(number=31), self._pr(number=32)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("2 PRs abiertos", v.motivo)
        self.assertIn("ambigüedad", v.motivo)

    # --- R2 estado ---

    def test_caso8_pr_mergeado_passed(self):
        v = self._evaluar([self._pr(state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")])
        self.assertEqual(v.estado, "PASSED")
        self.assertIn("#31", v.motivo)
        self.assertIn("ya fue mergeado", v.motivo)

    # --- R3 forma ---

    def test_caso9_draft(self):
        v = self._evaluar([self._pr(isDraft=True)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("draft", v.motivo)

    def test_caso10_base_incorrecta(self):
        v = self._evaluar([self._pr(baseRefName="develop")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("develop", v.motivo)
        self.assertIn("main", v.motivo)

    def test_caso11_sin_keyword_de_cierre_no_critico(self):
        v = self._evaluar([self._pr(body="Sin keyword de cierre")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("#31", v.motivo)

    # --- R4 merge ---

    def test_caso12_conflicting(self):
        v = self._evaluar([self._pr(mergeable="CONFLICTING")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("conflictos", v.motivo)

    def test_caso13_mergestate_dirty_con_mergeable_no_conflicting(self):
        v = self._evaluar([self._pr(mergeable="MERGEABLE", mergeStateStatus="DIRTY")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("conflictos", v.motivo)
        self.assertIn("DIRTY", v.motivo)

    def test_caso14_mergeable_unknown_tras_espera(self):
        v = self._evaluar([self._pr(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")], espera=60)
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("60", v.motivo)

    def test_caso15_behind_no_critico(self):
        v = self._evaluar([self._pr(mergeStateStatus="BEHIND")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("detrás", v.motivo)

    def test_caso16_mergestate_blocked_no_altera_veredicto(self):
        """D2: BLOCKED es el estado normal de un PR sano bajo branch protection; no
        debe alterar el veredicto (aqui, sin checks configurados, sigue PASSED)."""
        v = self._evaluar([self._pr(mergeStateStatus="BLOCKED")], hay_workflows=False)
        self.assertEqual(v.estado, "PASSED")

    # --- R5 checks: rollup vacio ---

    def test_caso17_sin_checks_sin_workflows_passed(self):
        v = self._evaluar([self._pr(statusCheckRollup=[])], hay_workflows=False)
        self.assertEqual(v.estado, "PASSED")
        self.assertIn("no tiene checks configurados", v.motivo)

    def test_caso18_sin_checks_con_workflows_failed_no_critico(self):
        v = self._evaluar([self._pr(statusCheckRollup=[])], hay_workflows=True)
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("reevalu", v.motivo)

    # --- R5 checks: CheckRun / StatusContext ---

    def test_caso19_y_27_checkrun_verde_passed(self):
        checks = [{"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "PASSED")
        self.assertIn("checks en verde", v.motivo)

    def test_caso20_checkrun_rojo_nombra_el_check(self):
        checks = [{"__typename": "CheckRun", "name": "lint-check", "status": "COMPLETED", "conclusion": "FAILURE"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("lint-check", v.motivo, "el motivo debe nombrar el check por su name, no solo el exit code")
        self.assertIn("FAILURE", v.motivo)

    def test_caso21_checkrun_en_curso_no_critico(self):
        checks = [{"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("build", v.motivo)

    def test_caso22_y_27_statuscontext_verde_passed(self):
        checks = [{"__typename": "StatusContext", "context": "ci/circleci", "state": "SUCCESS"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "PASSED")

    def test_caso23_statuscontext_rojo_nombra_el_check(self):
        checks = [{"__typename": "StatusContext", "context": "ci/circleci", "state": "FAILURE"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("ci/circleci", v.motivo)
        self.assertIn("FAILURE", v.motivo)

    def test_caso24_statuscontext_en_curso_no_critico(self):
        checks = [{"__typename": "StatusContext", "context": "ci/circleci", "state": "PENDING"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("ci/circleci", v.motivo)

    def test_caso25_valor_no_mapeado_es_desconocido_no_critico(self):
        checks = [{"__typename": "CheckRun", "name": "raro", "status": "COMPLETED", "conclusion": "ALGO_NUEVO"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertIn("raro", v.motivo)
        self.assertIn("ALGO_NUEVO", v.motivo)
        self.assertIn("desconocido", v.motivo)

    def test_caso26_mezcla_de_tipos_prioriza_rojo(self):
        checks = [
            {"__typename": "StatusContext", "context": "ok", "state": "SUCCESS"},
            {"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"__typename": "StatusContext", "context": "pend", "state": "PENDING"},
        ]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("build", v.motivo, "rojo > desconocido > en_curso > verde: debe nombrar el check rojo")

    def test_caso27_passed_completo(self):
        checks = [{"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "PASSED")
        self.assertEqual(v.criticidad, "n-a")
        self.assertIn("#31", v.motivo)
        self.assertIn("main", v.motivo)
        self.assertIn("checks en verde", v.motivo)
        self.assertEqual(v.pr, "https://github.com/o/r/pull/31")

    # --- Hallazgo #1 del code review (#81): filtrar por estado, nunca por
    # cardinalidad de la lista total. Reproducen los tres defectos reportados. ---

    def test_caso8b_merged_base_incorrecta_failed_critico(self):
        """Defecto (a): un unico MERGED contra la base equivocada daba PASSED
        porque `len(prs) == 1` no mira `baseRefName`."""
        v = self._evaluar([self._pr(state="MERGED", baseRefName="develop",
                                     mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("#31", v.motivo)
        self.assertIn("develop", v.motivo)
        self.assertIn("main", v.motivo)

    def test_caso8c_merged_base_correcta_con_closed_passed(self):
        """Defecto (b): un MERGED valido que coexiste con un CLOSED daba
        FAILED critico "cerrado sin merge" porque `len(prs) == 1` ya era falso."""
        mergeado = self._pr(number=31, state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        cerrado = self._pr(number=32, state="CLOSED")
        v = self._evaluar([mergeado, cerrado])
        self.assertEqual(v.estado, "PASSED")
        self.assertIn("#31", v.motivo)

    def test_caso8d_dos_merged_sin_closed_passed_nombra_ambos(self):
        """Defecto (c): dos MERGED sin CLOSED caian en el generico "no hay PR
        abierto", que ni nombraba los PRs mergeados existentes."""
        m1 = self._pr(number=31, state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        m2 = self._pr(number=32, state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        v = self._evaluar([m1, m2])
        self.assertEqual(v.estado, "PASSED")
        self.assertIn("#31", v.motivo)
        self.assertIn("#32", v.motivo)

    def test_caso8e_merged_base_incorrecta_junto_a_closed_sigue_critico(self):
        """Un MERGED a la base equivocada no se salva porque coexista con un
        CLOSED: sigue FAILED critico nombrando la base real."""
        mergeado_mal = self._pr(number=31, state="MERGED", baseRefName="develop",
                                 mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        cerrado = self._pr(number=32, state="CLOSED")
        v = self._evaluar([mergeado_mal, cerrado])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("#31", v.motivo)
        self.assertIn("develop", v.motivo)

    # --- Regresiones que el fix del hallazgo #1 no puede introducir ---

    def test_regresion_merged_mas_open_evalua_el_open(self):
        mergeado = self._pr(number=30, state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        check_verde = [{"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        abierto = self._pr(number=31, statusCheckRollup=check_verde)
        v = self._evaluar([mergeado, abierto])
        self.assertEqual(v.estado, "PASSED")
        self.assertIn("#31", v.motivo)
        self.assertIn("checks en verde", v.motivo)

    def test_regresion_dos_open_sigue_siendo_ambiguedad(self):
        """Ya cubierto por test_caso7; se repite explicito como guardia de
        regresion del fix del hallazgo #1 (no debe tocar la rama `> 1 OPEN`)."""
        v = self._evaluar([self._pr(number=31), self._pr(number=32)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertIn("2 PRs abiertos", v.motivo)
        self.assertIn("ambigüedad", v.motivo)

    # --- #223: rama fuera de hu/ y fix/, y draft intencional ---

    POC = "poc/mcp-apps"

    def _evaluar_poc(self, prs, **over):
        return self._evaluar(prs, issue=213, rama=self.POC, **over)

    def test_esc1_rama_sin_prefijo_se_vincula_por_cuerpo(self):
        """Escenario 1. Mutacion verificada a mano (escenario 9): si en `evaluar()`
        se quita SOLO la vinculacion por cuerpo (la rama sin prefijo cae siempre
        en el critico "ni la rama ni su PR referencian"), falla este caso y
        ningun caso preexistente de `PrCheckTests`: los preexistentes usan la
        rama `hu/31-x`, que nunca llega a la rama (c)."""
        pr = self._pr(body="Closes #213", headRefName=self.POC)
        v = self._evaluar_poc([pr])
        self.assertEqual((v.estado, v.criticidad), ("PASSED", "n-a"))
        self.assertEqual(v.pr, pr["url"])

    def test_esc2_rama_sin_prefijo_y_pr_sin_mencion_es_critico(self):
        v = self._evaluar_poc([self._pr(body="Sin referencias")])
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn(self.POC, v.motivo)
        self.assertIn("ni la rama ni su PR referencian el issue #213", v.motivo)

    def test_esc3_rama_con_prefijo_y_otro_issue_no_mira_el_cuerpo(self):
        # El cuerpo SI menciona #213: si se consultara, vincularia. No debe.
        v = self._evaluar([self._pr(body="Closes #213")], issue=213, rama="hu/214-otra-historia")
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn("la rama actual hu/214-otra-historia no corresponde al issue #213", v.motivo)

    def test_esc4_draft_intencional_sano_es_failed_no_critico(self):
        v = self._evaluar([self._pr(isDraft=True)], draft_intencional=True)
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "no-critico"))
        self.assertIn("draft intencional declarado (label draft-intencional)", v.motivo)

    def test_esc4_draft_intencional_sin_checks_ni_con_checks_verdes_tambien(self):
        # Los dos `return PASSED` (sin checks / checks verdes) no deben escaparse.
        verde = [{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        for etiqueta, checks in (("sin checks", []), ("checks verdes", verde)):
            with self.subTest(etiqueta):
                v = self._evaluar([self._pr(isDraft=True, statusCheckRollup=checks)], draft_intencional=True)
                self.assertEqual((v.estado, v.criticidad), ("FAILED", "no-critico"))

    def test_esc5_draft_intencional_no_oculta_un_check_en_rojo(self):
        rojo = [{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}]
        v = self._evaluar([self._pr(isDraft=True, statusCheckRollup=rojo)], draft_intencional=True)
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn("ci", v.motivo)
        self.assertNotIn("draft intencional", v.motivo)

    def test_esc6_draft_sin_declarar_es_el_critico_de_hoy(self):
        v = self._evaluar([self._pr(isDraft=True)], draft_intencional=False)
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn("el PR #31 está en draft; un humano no puede mergearlo", v.motivo)

    def test_esc7_draft_con_label_ilegible_es_no_se_y_distinto_de_sin_declarar(self):
        v = self._evaluar([self._pr(isDraft=True)], draft_intencional=None)
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn("no se pudo saber si el draft es intencional", v.motivo)
        self.assertNotIn("un humano no puede mergearlo", v.motivo)

    def test_pr_no_draft_ignora_draft_intencional(self):
        for valor in (None, False, True):
            with self.subTest(valor=valor):
                v = self._evaluar([self._pr()], draft_intencional=valor)
                self.assertEqual(v.estado, "PASSED")

    def test_refs_vincula_pero_no_cierra(self):
        v = self._evaluar_poc([self._pr(body="Refs #213")])
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "no-critico"))
        self.assertIn("no cierra #213", v.motivo)

    def test_refs_de_un_numero_mas_largo_no_vincula(self):
        v = self._evaluar([self._pr(body="Refs #2130")], issue=213, rama=self.POC)
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn("ni la rama ni su PR referencian", v.motivo)

    def test_rama_head_detached_sin_vinculo_es_critico(self):
        v = self._evaluar([], rama="HEAD")
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        v = self._evaluar([self._pr(body="nada")], rama="HEAD")
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "critico"))
        self.assertIn("HEAD", v.motivo)

    def test_draft_intencional_con_refs_combina_ambos_motivos_draft_primero(self):
        pr = self._pr(isDraft=True, body="Refs #213")
        v = self._evaluar_poc([pr], draft_intencional=True)
        self.assertEqual((v.estado, v.criticidad), ("FAILED", "no-critico"))
        self.assertIn("draft intencional declarado (label draft-intencional)", v.motivo)
        self.assertIn("no cierra #213", v.motivo)
        self.assertLess(v.motivo.index("draft intencional"), v.motivo.index("no cierra"))
        self.assertIn("; ", v.motivo)

    def test_esc8_cli_help_no_expone_pr_y_si_rama(self):
        proc = subprocess.run(
            [sys.executable, str(Path(pc.__file__)), "--help"], capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotRegex(proc.stdout, r"--pr\b")
        self.assertIn("--rama", proc.stdout)

    # --- Hallazgo #3 del code review (#81): propiedad de entrada degenerada ---

    def test_propiedad_entrada_degenerada_nunca_passed_ni_lanza(self):
        """Invariante: para toda entrada degenerada -cualquier campo del PR
        ausente o None- `evaluar()` no lanza y su estado nunca es PASSED.

        Generado programaticamente a partir de un PR valido (nunca a mano) para
        que cubra tambien los campos que GitHub agregue mañana, y detecte un
        `.get(x, True)` descuidado dentro de seis meses. Escenario realista de
        este propio repo (D7 del contrato de #81): `hay_workflows=True`.
        """
        base = self._pr()
        variantes: list[tuple[str, dict]] = []
        for campo in base:
            sin_campo = dict(base)
            del sin_campo[campo]
            variantes.append((f"{campo} ausente", sin_campo))
            con_none = dict(base)
            con_none[campo] = None
            variantes.append((f"{campo} en None", con_none))

        check_sin_name = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        variantes.append(("check sin name (verde)", self._pr(statusCheckRollup=check_sin_name)))
        check_typename_desconocido = [{"__typename": "TipoQueNoExisteAun", "name": "raro"}]
        variantes.append(("__typename desconocido", self._pr(statusCheckRollup=check_typename_desconocido)))

        for etiqueta, pr in variantes:
            with self.subTest(etiqueta):
                try:
                    v = self._evaluar([pr], hay_workflows=True)
                except Exception as exc:  # la propiedad exige "no lanza"
                    self.fail(f"{etiqueta}: evaluar() lanzo {type(exc).__name__}: {exc}")
                self.assertNotEqual(v.estado, "PASSED", f"{etiqueta}: dio PASSED sobre una entrada degenerada")

    # --- Hallazgo #4 del code review (#81): motivo que nombra el campo ausente ---

    def test_motivo_nombra_campo_faltante_en_vez_de_reevaluar(self):
        """Con `mergeable`, `isDraft` o `number` ausentes el veredicto ya era
        correcto (FAILED no-critico), pero el motivo decia "checks aun no
        registrados; reevalua en unos segundos" -- manda a esperar por algo
        que no va a cambiar. Debe nombrar el campo que falta."""
        for campo in ("mergeable", "isDraft", "number"):
            with self.subTest(campo):
                pr = self._pr()
                del pr[campo]
                v = self._evaluar([pr], hay_workflows=True)
                self.assertEqual(v.estado, "FAILED")
                self.assertEqual(v.criticidad, "no-critico")
                self.assertIn(campo, v.motivo)
                self.assertNotIn("reevaluá en unos segundos", v.motivo)

    # --- WARNINGs cosméticos del review de #81: motivo que miente (check sin
    # identificador) y concordancia en plural (uno o varios PRs). Ningún
    # veredicto (estado/criticidad) cambia respecto de antes de este fix. ---

    def test_checkrun_sin_name_no_dice_estado_desconocido(self):
        """SUCCESS sí se reconoce -- lo que falta es la identidad del check,
        no el estado. El motivo no debe sugerir que SUCCESS no está mapeado."""
        checks = [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertEqual(v.motivo, "check sin name identificable (estado SUCCESS); no cuenta como verde")
        self.assertNotIn("estado desconocido", v.motivo)

    def test_statuscontext_sin_context_no_dice_estado_desconocido(self):
        checks = [{"__typename": "StatusContext", "state": "SUCCESS"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertEqual(v.motivo, "check sin context identificable (estado SUCCESS); no cuenta como verde")
        self.assertNotIn("estado desconocido", v.motivo)

    def test_valor_no_mapeado_con_identificador_sigue_diciendo_estado_desconocido(self):
        """El otro lado del mismo mapa: con identificador presente pero un
        valor fuera de `_CONCLUSION_A_EJE`, el mensaje original sigue siendo
        verdadero (ahí sí es un estado no reconocido) y no se toca."""
        checks = [{"__typename": "CheckRun", "name": "raro", "status": "COMPLETED", "conclusion": "ALGO_NUEVO"}]
        v = self._evaluar([self._pr(statusCheckRollup=checks)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "no-critico")
        self.assertEqual(v.motivo, "check raro: estado desconocido ALGO_NUEVO")

    def test_un_pr_cerrado_concuerda_en_singular(self):
        v = self._evaluar([self._pr(state="CLOSED", number=31)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertEqual(v.motivo, "el PR #31 está cerrado sin merge")

    def test_varios_prs_cerrados_concuerdan_en_plural(self):
        v = self._evaluar([self._pr(state="CLOSED", number=31), self._pr(state="CLOSED", number=32)])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertEqual(v.motivo, "los PR #31 y #32 están cerrados sin merge")

    def test_varios_prs_mergeados_concuerdan_en_plural(self):
        m1 = self._pr(number=31, state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        m2 = self._pr(number=32, state="MERGED", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        v = self._evaluar([m1, m2])
        self.assertEqual(v.estado, "PASSED")
        self.assertEqual(v.criticidad, "n-a")
        self.assertEqual(v.motivo, "los PR #31 y #32 ya fueron mergeados (fuera del flujo: el merge va después del DoD)")

    def test_varios_prs_mergeados_a_base_incorrecta_concuerdan_en_plural(self):
        m1 = self._pr(number=31, state="MERGED", baseRefName="develop", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        m2 = self._pr(number=32, state="MERGED", baseRefName="feature", mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        v = self._evaluar([m1, m2])
        self.assertEqual(v.estado, "FAILED")
        self.assertEqual(v.criticidad, "critico")
        self.assertEqual(
            v.motivo,
            "los PR #31 (base develop) y #32 (base feature) fueron mergeados contra una base distinta de main",
        )


class MetricasTests(unittest.TestCase):
    def test_calcula_cobertura_y_lead_time(self):
        cerradas = [
            {"number": 1, "label_names": ["tipo:hu", "review:aprobado", "retro:preciso"], "comments": [{"body": REVIEW_COMMENT}, {"body": RETRO_COMMENT}], "createdAt": "2026-09-01T00:00:00Z", "closedAt": "2026-09-03T00:00:00Z"},
            {"number": 2, "label_names": ["tipo:hu"], "comments": [], "createdAt": "2026-09-01T00:00:00Z", "closedAt": "2026-09-02T00:00:00Z"},
        ]
        abiertas = [{"number": 3, "label_names": ["tipo:hu", "estado:listo", "bloqueado"], "comments": []}]
        m = mf.calcular(cerradas, abiertas)
        self.assertEqual(m["cobertura"]["review"], 1)
        self.assertEqual(m["review"]["aprobado"], 1)
        self.assertAlmostEqual(m["lead_time_prom"], 1.5)
        self.assertEqual(m["bloqueadas"], 1)
        self.assertIn("50%", mf.reporte(m, mf.calcular_ratchet(cerradas, abiertas), None))


@contextlib.contextmanager
def _en(directorio: Path):
    previo = Path.cwd()
    os.chdir(directorio)
    try:
        yield
    finally:
        os.chdir(previo)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ResolverConfigWorktreeTests(unittest.TestCase):
    """#210: un worktree no hereda `.claude/` (sin versionar); `load_config` y el
    resolvedor deben hallar el config del checkout principal, y decir "no se" (con el
    stderr de git) cuando no pueden resolverlo, nunca "no se encontro"."""

    CONFIG = {"projectName": "demo", "github": {"repo": "o/r"}, "git": {"baseBranches": ["trunk"]}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.principal = self.base / "principal"
        self.principal.mkdir()
        _git(self.principal, "init", "-q", "-b", "main")
        _git(self.principal, "config", "user.name", "t")
        _git(self.principal, "config", "user.email", "t@t.co")
        _git(self.principal, "commit", "--allow-empty", "-q", "-m", "init #210")
        (self.principal / ".claude").mkdir()
        (self.principal / ".claude/timonel.config.json").write_text(json.dumps(self.CONFIG), encoding="utf-8")
        self.worktree = self.base / "wt"
        _git(self.principal, "worktree", "add", "-q", "-b", "hu/210-x", str(self.worktree))

    def test_worktree_sin_claude_resuelve_el_config_del_principal(self):
        with _en(self.worktree):
            self.assertEqual(tg.load_config()["git"]["baseBranches"], ["trunk"])
            ruta, motivo = tg.resolver_config_path()
        self.assertEqual(ruta.resolve(), (self.principal / ".claude/timonel.config.json").resolve())
        self.assertEqual(motivo, "")

    def test_subdirectorio_de_worktree_resuelve_el_config_del_principal(self):
        sub = self.worktree / "a" / "b"
        sub.mkdir(parents=True)
        with _en(sub):
            self.assertEqual(tg.load_config()["projectName"], "demo")

    def test_config_local_gana_y_no_consulta_git(self):
        (self.worktree / ".claude").mkdir()
        (self.worktree / ".claude/timonel.config.json").write_text(
            json.dumps({"projectName": "local"}), encoding="utf-8")
        with _en(self.worktree), mock.patch.object(tg.subprocess, "run", side_effect=AssertionError("git")):
            self.assertEqual(tg.load_config()["projectName"], "local")
            self.assertEqual(tg.resolver_config_path()[0], tg.CONFIG_PATH)

    def test_fuera_de_git_dice_no_se_con_stderr_de_git(self):
        fuera = self.base / "fuera"
        fuera.mkdir()
        with _en(fuera), self.assertRaises(SystemExit) as ctx:
            tg.load_config()
        msg = str(ctx.exception)
        self.assertIn("no se pudo resolver el checkout principal", msg)
        self.assertIn("not a git repository", msg)
        self.assertNotIn("no se encontro", msg)

    def test_git_ausente_dice_no_se(self):
        with _en(self.worktree), mock.patch.object(tg.subprocess, "run", side_effect=FileNotFoundError("git")):
            ruta, motivo = tg.resolver_config_path()
        self.assertIsNone(ruta)
        self.assertIn("git no respondio", motivo)

    def test_repo_bare_dice_no_se(self):
        bare = self.base / "x.git"
        _git(self.base, "init", "-q", "--bare", str(bare))
        with _en(bare):
            ruta, motivo = tg.resolver_config_path()
            with self.assertRaises(SystemExit) as ctx:
                tg.load_config()
        self.assertIsNone(ruta)
        self.assertIn("no es un `.git` de checkout", motivo)
        self.assertIn("no se pudo resolver el checkout principal", str(ctx.exception))

    def test_submodulo_dice_no_se(self):
        sub = self.principal / "sm"
        _git(self.principal, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(self.principal), "sm")
        with _en(sub):
            ruta, motivo = tg.resolver_config_path()
        self.assertIsNone(ruta)
        self.assertIn("no es un `.git` de checkout", motivo)

    def test_repo_normal_sin_config_dice_no_se_encontro(self):
        otro = self.base / "otro"
        otro.mkdir()
        _git(otro, "init", "-q", "-b", "main")
        with _en(otro), self.assertRaises(SystemExit) as ctx:
            tg.load_config()
        self.assertIn("no se encontro", str(ctx.exception))
        self.assertNotIn("no se pudo resolver", str(ctx.exception))

    def test_perfil_plugin_sin_config_devuelve_config_minimo(self):
        (self.worktree / ".claude-plugin").mkdir()
        (self.worktree / ".claude-plugin/plugin.json").write_text('{"name": "timonel"}', encoding="utf-8")
        with _en(self.worktree), mock.patch.object(tg, "gh", return_value="o/timonel\n") as gh_mock:
            cfg = tg.load_config()
        self.assertEqual(cfg["modulos"], ["plugin"])
        self.assertEqual(cfg["github"]["repo"], "o/timonel")
        gh_mock.assert_called_once()

    def test_path_explicito_no_consulta_git(self):
        with _en(self.worktree), mock.patch.object(tg.subprocess, "run", side_effect=AssertionError("git")):
            with self.assertRaises(SystemExit) as ctx:
                tg.load_config(Path("otro/config.json"))
        self.assertIn("no se encontro", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
