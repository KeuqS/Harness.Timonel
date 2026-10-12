"""Tests del guard de integracion por PR (epica #79, #82): `scripts/guard_integracion.py`
y su cableado en `hooks/hooks.json`.

Dos capas:
  - `GuardIntegracionPuroTests`: la funcion pura `decidir(cmd, rama, bases)` sobre la
    TABLA_DE_CASOS, sin tocar disco ni procesos.
  - `HookEnCajaNegraTests`: extrae los comandos REALES de `hooks/hooks.json` (no una
    copia pegada) y los corre con `bash -c` sobre un repo git temporal, tal como los
    correria Claude Code. Es el patron que faltaba (Dora, #82): hasta ahora nada
    ejecutaba el shell de hooks.json, solo se hacia `assertIn` sobre el string.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import detectar_commit as dc  # noqa: E402
import guard_integracion as gi  # noqa: E402

BLOQUEADO = "bloqueado"
PERMITIDO = "permitido"

# Cada fila = un caso del contrato del issue #82. `rama` es la rama actual antes de
# correr `cmd`; `esperado` es BLOQUEADO (exit 2, mensaje en stderr) o PERMITIDO
# (exit 0, sin salida). Agregar un caso nuevo es agregar una fila.
TABLA_DE_CASOS = [
    # numero, rama, cmd, esperado
    (1, "hu/82-x", "git push origin main", BLOQUEADO),
    (2, "main", "git push", BLOQUEADO),
    (3, "main", "git push origin HEAD", BLOQUEADO),
    (4, "hu/82-x", "git push origin hu/82-x:main", BLOQUEADO),
    (5, "main", "git push --all origin", BLOQUEADO),
    (6, "main", "git merge hu/82-x", BLOQUEADO),
    (7, "main", "git merge --ff-only hu/82-x", BLOQUEADO),  # el agujero de #30
    (8, "hu/82-x", "git checkout main && git merge --ff-only hu/82-x", BLOQUEADO),  # el compuesto no evade
    (9, "main", "git pull", BLOQUEADO),
    (10, "main", "git pull --ff-only origin hu/82-x", BLOQUEADO),
    (11, "main", "gh pr merge 94 --squash", BLOQUEADO),
    (12, "hu/82-x", "gh pr merge --auto 94", BLOQUEADO),
    (14, "main", "git checkout main && git merge --ff-only origin/main", PERMITIDO),
    (15, "hu/82-x", "git checkout main && git merge --ff-only origin/main", PERMITIDO),
    (16, "main", "git pull --ff-only", PERMITIDO),
    (17, "main", "git pull --ff-only origin main", PERMITIDO),
    (18, "main", "git merge --ff-only @{u}", PERMITIDO),
    (19, "hu/82-x", "git push -u origin hu/82-x", PERMITIDO),
    ("20a", "hu/82-x", "gh pr create --fill", PERMITIDO),
    ("20b", "hu/82-x", "gh pr view 94", PERMITIDO),
    ("20c", "hu/82-x", "gh pr checks", PERMITIDO),
    (21, "main-fix", "git push origin main-fix", PERMITIDO),  # frontera de substring
    (22, "hu/82-x", "git merge hu/82-backend", PERMITIDO),  # consolidacion de worktrees
    (26, "main", "git merge --abort", PERMITIDO),
    # --- review de #82: separador pegado a un token evade el guard (CRITICO) ---
    (27, "hu/82-x", "git checkout main; git merge hu/x", BLOQUEADO),  # ; pegado
    (28, "hu/82-x", "git checkout main ; git merge hu/x", BLOQUEADO),  # ; separado
    (29, "hu/82-x", "git checkout main; git merge --ff-only hu/x", BLOQUEADO),  # ; pegado + agujero #30
    (30, "hu/82-x", "git switch main; git merge hu/x", BLOQUEADO),  # ; pegado con switch
    (31, "hu/82-x", "git checkout main&&git merge hu/x", BLOQUEADO),  # && pegado
    (32, "hu/82-x", "git checkout main && git merge hu/x", BLOQUEADO),  # && separado
    (33, "hu/82-x", "git checkout main||git merge hu/x", BLOQUEADO),  # || pegado
    (34, "hu/82-x", "git checkout main || git merge hu/x", BLOQUEADO),  # || separado
    # --- warnings del review de #82 ---
    (35, "hu/82-x", "git -C /otro/repo push origin main", BLOQUEADO),  # -C salteado
    (36, "hu/82-x", "git push origin hu/x:refs/heads/main", BLOQUEADO),  # refs/heads/ normalizado
    (37, "hu/82-x", "GIT_DIR=.git git push origin main", BLOQUEADO),  # prefijo VAR=valor descartado
    # --- comillas: un separador dentro de comillas no es separador (trampa del arreglo) ---
    (38, "hu/82-x", 'git commit -m "fix del parser; ver #82"', PERMITIDO),  # ; interno no es separador
    (39, "hu/82-x", 'git checkout main && git merge hu/x -m "algo; con punto y coma"', BLOQUEADO),  # && externo si separa
    # --- regresion: esto ya funcionaba y no debe romperse ---
    (40, "main", "git pull origin main --ff-only", PERMITIDO),  # flags despues de la ref
    (41, "main", "git merge  --ff-only  origin/main", PERMITIDO),  # espacios multiples
    (42, "hu/82-x", "git push origin +hu/x:main", BLOQUEADO),
    (43, "main", "git push origin HEAD:main", BLOQUEADO),
    (44, "main", "git pull --rebase", BLOQUEADO),
    (45, "main", "git push --mirror origin", BLOQUEADO),
    (46, "hu/82-x", "git switch main && git merge hu/x", BLOQUEADO),
    # --- review ronda 2 de #82: CRITICO, separadores no tratados evaden el guard ---
    # salto de linea: como Bash entrega comandos multilinea todo el tiempo (no una
    # evasion deliberada) -- el mas grave de los cuatro.
    (47, "hu/82-x", "git checkout main\ngit merge hu/x", BLOQUEADO),  # \n pegado
    (48, "hu/82-x", "git checkout main \n git merge hu/x", BLOQUEADO),  # \n separado
    (49, "hu/82-x", "git checkout main\r\ngit merge hu/x", BLOQUEADO),  # \r\n pegado
    (50, "hu/82-x", "git checkout main \r\n git merge hu/x", BLOQUEADO),  # \r\n separado
    (51, "hu/82-x", "git status\ngit checkout main\ngit merge hu/82-x", BLOQUEADO),  # multilinea realista de 3 lineas
    # de fondo (&)
    (52, "hu/82-x", "git checkout main&git merge hu/x", BLOQUEADO),  # & pegado
    (53, "hu/82-x", "git checkout main & git merge hu/x", BLOQUEADO),  # & separado
    # pipe simple (|)
    (54, "hu/82-x", "git checkout main|git merge hu/x", BLOQUEADO),  # | pegado
    (55, "hu/82-x", "git checkout main | git merge hu/x", BLOQUEADO),  # | separado
    # agrupacion (...)
    (56, "hu/82-x", "(git checkout main && git merge hu/x)", BLOQUEADO),  # agrupado
    # \n dentro de comillas NO es separador
    (57, "hu/82-x", 'git commit -m "linea1\nlinea2"', PERMITIDO),
    # --- warnings ronda 2: flag-skip pegado ---
    (58, "hu/82-x", "git -Cpath push origin main", BLOQUEADO),  # -C pegado
    (59, "main", "git -c user.name=x merge hu/z", BLOQUEADO),  # -c global rompia la deteccion del subcomando
    (60, "main", "git -cuser.name=x merge hu/z", BLOQUEADO),  # -c pegado
    # --- warning ronda 2: casos limite de escapes (fijan el comportamiento actual) ---
    (61, "hu/82-x", 'git commit -m "cita \\"escapada\\" adentro"', PERMITIDO),  # backslash antes de comilla
    (63, "main", "git merge \\;hu/x", BLOQUEADO),  # backslash antes de separador: no evade, ref insegura en base
    (64, "hu/82-x", "git commit -m 'a;b'\\''c;d'", PERMITIDO),  # comillas anidadas simples (idioma it's)
    (65, "hu/82-x", 'git commit -m "mix \'single\' and \\"double\\""', PERMITIDO),  # comillas mixtas
    # el que SI resulto explotable (ver mutacion/reporte): comilla escapada + && sin espacio
    # -> en Bash real corre igual (`"a\"b"&&git merge x` ejecuta las dos), y el tracker
    # simple (sin reconocer el escape) lo dejaba pasar. Ya corregido arriba.
    (66, "main", 'git commit -m "a\\"b"&&git merge hu/x', BLOQUEADO),  # explotable, corregido
    (67, "main", 'git commit -m "a\\"b";git merge hu/x', BLOQUEADO),  # idem con ;
    # --- limitacion aceptada y documentada (NO perseguir): evasion deliberada ---
    # guard != sandbox; cada capa de parser nueva para cubrir esto es superficie de
    # bug nueva (ver docstring del modulo y CHANGELOG). Si este test empieza a fallar
    # porque alguien "lo arreglo", que sea una decision, no un descuido.
    (68, "main", 'eval "git merge hu/x"', PERMITIDO),  # limite conocido: eval
    (69, "main", 'bash -c "git merge hu/x"', PERMITIDO),  # limite conocido: bash -c
    # $(...) no se inspecciona por dentro, pero lo que queda AFUERA del `$()` se
    # juzga normalmente contra la rama REAL: parado en "main" (base), el
    # "&& git merge hu/x" es visible fuera del command substitution y bloquea.
    # Lo que NO se cubre es `$()` envolviendo el comando ENTERO (eso si seguiria
    # evadiendo, pero no es este caso).
    (70, "main", "$(git checkout main) && git merge hu/x", BLOQUEADO),  # $(...) no oculta lo de afuera

    # --- review #4 de #82: flags globales booleanos, la REGLA no una lista ---
    (71, "hu/82-x", "git --no-pager push origin main", BLOQUEADO),
    (72, "hu/82-x", "git --paginate push origin main", BLOQUEADO),
    (73, "hu/82-x", "git -P push origin main", BLOQUEADO),
    (74, "hu/82-x", "git --bare push origin main", BLOQUEADO),
    (75, "hu/82-x", "git --literal-pathspecs push origin main", BLOQUEADO),
    (76, "hu/82-x", "git --flag-que-no-existe push origin main", BLOQUEADO),  # inventado: prueba la regla, no la lista
    (77, "hu/82-x", "git --no-pager -C /otro push origin main", BLOQUEADO),  # booleano + con-valor combinados
    (78, "main", "git -c user.name=x --no-pager merge hu/z", BLOQUEADO),  # idem, con merge en base

    # --- review #2 de #82: rebase y cherry-pick sobre la rama base ---
    (79, "main", "git rebase hu/x", BLOQUEADO),
    (80, "main", "git cherry-pick abc1234", BLOQUEADO),
    (81, "hu/82-x", "git rebase hu/x", PERMITIDO),  # fuera de base, normal
    (82, "hu/82-x", "git cherry-pick abc1234", PERMITIDO),  # fuera de base, normal
    (83, "main", "git rebase origin/main", PERMITIDO),  # ref segura: sync
    (84, "main", "git rebase --continue", PERMITIDO),
    (85, "main", "git rebase --abort", PERMITIDO),
    (86, "main", "git rebase --skip", PERMITIDO),
    (87, "main", "git rebase --quit", PERMITIDO),
    (88, "main", "git cherry-pick --continue", PERMITIDO),
    (89, "main", "git cherry-pick --abort", PERMITIDO),

    # --- review #3 de #82: gh api como puerta trasera de gh pr merge ---
    (90, "hu/82-x", "gh api -X PUT repos/o/r/pulls/94/merge", BLOQUEADO),
    (91, "hu/82-x", "gh api --method PUT repos/o/r/pulls/94/merge", BLOQUEADO),
    (92, "hu/82-x", "gh api repos/o/r/pulls/94/merge", BLOQUEADO),  # sin metodo, igual bloqueado
    (93, "hu/82-x", "gh api repos/o/r/pulls/94", PERMITIDO),  # sin /merge: solo lectura
    (94, "hu/82-x", "gh api user", PERMITIDO),

    # --- ronda final de #82: gh api /merges (Merge a branch, sin PR) ---
    (95, "hu/82-x", "gh api -X POST repos/o/r/merges -f base=main -f head=hu/x", BLOQUEADO),
    (96, "hu/82-x", "gh api repos/o/r/merges", BLOQUEADO),
    (97, "hu/82-x", "gh api repos/o/merged-stuff/pulls/1", PERMITIDO),  # "merge" no es el segmento final

    # --- ronda final de #82: git revert / git am, misma familia que cherry-pick ---
    (98, "main", "git revert abc1234", BLOQUEADO),
    (99, "main", "git am /tmp/p.patch", BLOQUEADO),
    (100, "hu/82-x", "git revert abc1234", PERMITIDO),  # fuera de base, normal
    (101, "hu/82-x", "git am /tmp/p.patch", PERMITIDO),  # fuera de base, normal
    (102, "main", "git revert --abort", PERMITIDO),  # forma de control
    (103, "main", "git am --continue", PERMITIDO),  # forma de control

    # --- ronda final de #82: git reset --hard a una ref no segura ---
    (104, "main", "git reset --hard hu/x", BLOQUEADO),  # mueve main a codigo no revisado
    (105, "main", "git reset --hard origin/main", PERMITIDO),  # sync, ref segura
    (106, "hu/82-x", "git reset --hard hu/x", PERMITIDO),  # fuera de base, normal
    # decision documentada (ver _revisar_reset): el criterio de ref segura no
    # distingue modo, solo ref -- HEAD~1 no es vacia ni origin/main, bloquea
    # igual que --hard aunque --soft no toque el working tree.
    (107, "main", "git reset --soft HEAD~1", BLOQUEADO),

    # --- fix de falsos positivos de `reset` (#82): HEAD exacto y pathspec ---
    # `git reset --hard HEAD` no mueve el ref (queda donde estaba) y descarta
    # el working tree: sin integracion posible, no debe bloquear.
    (108, "main", "git reset --hard HEAD", PERMITIDO),
    (109, "main", "git reset", PERMITIDO),
    (110, "main", "git reset --hard", PERMITIDO),
    # `git reset [<ref>] [--] <paths>...` es un unstage: nunca mueve el ref,
    # sin importar la ref.
    (111, "main", "git reset HEAD archivo.py", PERMITIDO),
    (112, "main", "git reset -- archivo.py", PERMITIDO),
    # HEAD~1, HEAD^ y HEAD@{1} SI mueven el ref -- no se aflojan por este fix,
    # ninguna forma de HEAD "compuesta" es segura, solo HEAD exacto.
    (113, "main", "git reset --hard HEAD~1", BLOQUEADO),
    (114, "main", "git reset --hard HEAD^", BLOQUEADO),
    (115, "main", "git reset --hard HEAD@{1}", BLOQUEADO),
    # riesgo explicito de este cambio: HEAD no se vuelve ref segura para el
    # resto de la familia (`_es_ref_segura` global no cambia).
    (116, "main", "git push origin HEAD", BLOQUEADO),
]

BASES = ["main", "master", "develop"]


class GuardIntegracionPuroTests(unittest.TestCase):
    """`decidir()` sobre la tabla, sin disco ni procesos."""

    def test_tabla_de_casos(self):
        for numero, rama, cmd, esperado in TABLA_DE_CASOS:
            with self.subTest(caso=numero, cmd=cmd, rama=rama):
                mensaje = gi.decidir(cmd, rama, BASES)
                if esperado == BLOQUEADO:
                    self.assertIsNotNone(mensaje, f"caso {numero}: deberia bloquear `{cmd}` en `{rama}`")
                    self.assertTrue(mensaje.startswith("[timonel] Bloqueado: "))
                else:
                    self.assertIsNone(mensaje, f"caso {numero}: deberia permitir `{cmd}` en `{rama}`, bloqueo con: {mensaje}")

    def test_mensaje_push_a_base_es_accionable(self):
        mensaje = gi.decidir("git push origin main", "hu/82-x", BASES)
        self.assertIn("git push -u origin hu/<issue>-<slug>", mensaje)
        self.assertIn("epica #79", mensaje)

    def test_mensaje_merge_en_base_incluye_alternativa_de_pull(self):
        mensaje = gi.decidir("git merge hu/82-x", "main", BASES)
        self.assertIn("git merge --ff-only origin/main", mensaje)
        self.assertIn("git pull --ff-only", mensaje)

    def test_mensaje_pull_sin_ff_only_incluye_literal(self):
        mensaje = gi.decidir("git pull", "main", BASES)
        self.assertIn("git pull --ff-only", mensaje)

    def test_mensaje_rebase_ofrece_rebase_no_generico_de_merge(self):
        mensaje = gi.decidir("git rebase hu/x", "main", BASES)
        self.assertIn("git rebase origin/main", mensaje)

    def test_mensaje_reset_ofrece_reset_hard_origin_base(self):
        mensaje = gi.decidir("git reset --hard hu/x", "main", BASES)
        self.assertIn("git reset --hard origin/main", mensaje)

    def test_mensaje_reset_habla_de_mover_el_ref_no_de_integracion_nueva(self):
        """Asercion no vacia (#82, fix de falsos positivos): `reset` no crea
        integracion nueva, mueve el ref de la base. Si el mensaje vuelve a
        reusar el generico de `_mensaje_integracion_en_base`, este test debe
        fallar -- ver mutacion obligatoria."""
        mensaje = gi.decidir("git reset --hard hu/x", "main", BASES)
        self.assertIn("mover el ref", mensaje)
        self.assertIn("git reset --hard origin/main", mensaje)
        self.assertNotIn("crear integracion nueva", mensaje)

    def test_mensaje_cherry_pick_revert_am_ofrece_pr_no_ff_only(self):
        """Aserciones no vacias (#82 ronda final): a quien quiere llevar un commit a
        la base, sincronizar no le sirve. Si el mensaje vuelve al generico de
        `merge` (`--ff-only`), este test debe fallar -- ver mutacion obligatoria."""
        for cmd in ("git cherry-pick abc1234", "git revert abc1234", "git am /tmp/p.patch"):
            with self.subTest(cmd=cmd):
                mensaje = gi.decidir(cmd, "main", BASES)
                self.assertIn("abri el PR", mensaje)
                self.assertIn("git checkout hu/<issue>-<slug>", mensaje)
                self.assertNotIn("--ff-only", mensaje)

    def test_mensaje_gh_pr_merge_explica_via_humana(self):
        mensaje = gi.decidir("gh pr merge 1", "main", BASES)
        self.assertIn("humano", mensaje)
        self.assertIn("epica #79", mensaje)

    def test_shlex_invalido_es_fail_open(self):
        self.assertIsNone(gi.decidir("git push origin main 'sin cerrar", "main", BASES))

    def test_shlex_invalido_deja_rastro_auditable_en_events_log(self):
        """Fail-open silencioso == agujero que se descubre por casualidad seis HUs
        despues. El fail-open sigue permitiendo el comando, pero ahora queda rastro."""
        import os

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                self.assertIsNone(gi.decidir("git push origin main 'sin cerrar", "main", BASES))
            finally:
                os.chdir(cwd)
            log = tmp_path / ".timonel/events.log"
            self.assertTrue(log.exists(), "el fail-open deberia crear .timonel/events.log")
            contenido = log.read_text(encoding="utf-8")
            self.assertIn("guard-integracion-fail-open shlex-invalido", contenido)

    def test_fail_open_nunca_rompe_el_guard_si_no_puede_escribir_el_log(self):
        """Si .timonel no se puede crear (p. ej. un archivo ocupa ese nombre), el
        fail-open sigue devolviendo None: escribir el log nunca hace fallar el guard."""
        import os

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".timonel").write_text("no es un directorio", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                self.assertIsNone(gi.decidir("git push origin main 'sin cerrar", "main", BASES))
            finally:
                os.chdir(cwd)

    def test_bases_configuradas_lee_config_del_consumidor(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".claude").mkdir()
            (tmp_path / ".claude/timonel.config.json").write_text(
                json.dumps({"git": {"baseBranches": ["trunk"]}}), encoding="utf-8"
            )
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp_path)
                self.assertEqual(gi._bases_configuradas(), ["trunk"])
            finally:
                os.chdir(cwd)

    def test_bases_configuradas_por_defecto_sin_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                import os

                os.chdir(tmp)
                self.assertEqual(gi._bases_configuradas(), ["main", "master", "develop"])
            finally:
                os.chdir(cwd)


class GuardRamaPorSegmentoPuroTests(unittest.TestCase):
    """#229: `decidir(..., rama_de=...)` juzga cada segmento contra la rama del repo
    donde corre (`git -C <dir>` / `cd <dir> &&`), no contra la del cwd de la sesion.

    Todo con `rama_de` inyectado (un dict), sin subprocess. Cada escenario tiene su
    espejo con las ramas intercambiadas: si `decidir` ignorara `rama_de` (o usara la
    rama del dir equivocado), el espejo daria el resultado contrario y fallaria -- la
    aseveracion no es vacua (heuristics/general/aserciones-no-vacuas.md)."""

    WT = "/wt"

    @staticmethod
    def _decidir(cmd, rama_sesion, ramas_por_dir):
        return gi.decidir(cmd, rama_sesion, BASES, rama_de=ramas_por_dir.get)

    def test_escenario_1_git_C_en_worktree_de_historia_desde_sesion_en_main(self):
        # `git push` sin refspec empuja la rama actual: es lo que distingue.
        for cmd in ("git -C /wt push", "git -C /wt push origin HEAD", "git -C /wt push origin hu/229-x"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self._decidir(cmd, "main", {self.WT: "hu/229-x"}))

    def test_escenario_1_espejo_el_dir_en_main_bloquea_aunque_la_sesion_este_en_hu(self):
        for cmd in ("git -C /wt push", "git -C /wt push origin HEAD"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self._decidir(cmd, "hu/229-x", {self.WT: "main"}))

    def test_escenario_1_sin_rama_de_el_falso_positivo_original_persiste(self):
        """Control: sin `rama_de` es el comportamiento de antes (rama de la sesion)."""
        self.assertIsNotNone(gi.decidir("git -C /wt push", "main", BASES))

    def test_escenario_2_cd_a_worktree_de_historia_desde_sesion_en_main(self):
        self.assertIsNone(self._decidir("cd /wt && git push", "main", {self.WT: "hu/229-x"}))
        self.assertIsNone(self._decidir("cd /wt && git push origin hu/229-x", "main", {self.WT: "hu/229-x"}))

    def test_escenario_2_espejo_cd_a_dir_en_main_bloquea(self):
        self.assertIsNotNone(self._decidir("cd /wt && git push", "hu/229-x", {self.WT: "main"}))

    def test_escenario_2_cd_relativo_se_resuelve_contra_el_dir_vigente(self):
        ramas = {"/a/b": "hu/229-x", "/a": "main"}
        self.assertIsNone(self._decidir("cd /a && cd b && git push", "main", ramas))
        self.assertIsNotNone(self._decidir("cd /a && cd b && git push", "hu/229-x", {"/a/b": "main"}))

    def test_escenario_3_cada_segmento_usa_la_rama_de_su_dir(self):
        cmd = "git -C /wt commit -m 'x #229' ; git -C /wt push"
        self.assertIsNone(self._decidir(cmd, "main", {self.WT: "hu/229-x"}))
        self.assertIsNotNone(self._decidir(cmd, "hu/229-x", {self.WT: "main"}))

    def test_escenario_3_dos_dirs_distintos_cada_uno_con_su_rama(self):
        cmd = "git -C /wt push ; git -C /otro push"
        ramas = {"/wt": "hu/229-x", "/otro": "main"}
        self.assertIsNotNone(self._decidir(cmd, "hu/229-x", ramas))
        self.assertIsNone(self._decidir(cmd, "main", {"/wt": "hu/229-x", "/otro": "hu/1-y"}))

    def test_la_rama_simulada_por_checkout_se_lleva_por_dir(self):
        """`git -C /a checkout main` no cambia la rama de /b: /b se consulta ANTES del
        checkout en /a y su push posterior se juzga con la rama propia de /b."""
        ramas = {"/a": "hu/1-x", "/b": "hu/2-y"}
        cmd = "git -C /b push ; git -C /a checkout main ; git -C /b push"
        self.assertIsNone(self._decidir(cmd, "hu/3-z", ramas))
        # espejo: el push posterior en /a SI ve el checkout.
        cmd_a = "git -C /b push ; git -C /a checkout main ; git -C /a push"
        self.assertIsNotNone(self._decidir(cmd_a, "hu/3-z", ramas))

    def test_cd_en_pipe_o_background_no_se_aplica_y_queda_la_rama_de_la_sesion(self):
        """En bash un `cd` en pipeline/background corre en un subshell: el dir de los
        segmentos siguientes no cambia. Sesion en main -> sigue bloqueando."""
        ramas = {self.WT: "hu/229-x"}
        # Push sin refspec o `HEAD`: el destino es la rama vigente (con refspec
        # explicito a hu/N nunca bloquea, asi que no distinguiria nada).
        for cmd in ("cd /wt | git push origin HEAD", "cd /wt & git push", "cd /wt | git push"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self._decidir(cmd, "main", ramas))
        # espejo: con && el cd si aplica.
        self.assertIsNone(self._decidir("cd /wt && git push", "main", ramas))

    def test_git_C_acumulativo_se_resuelve_sobre_el_dir_vigente(self):
        self.assertIsNone(self._decidir("git -C /a -C b push", "main", {"/a/b": "hu/1-x"}))
        self.assertIsNotNone(self._decidir("git -C /a -C b push", "hu/1-x", {"/a/b": "main"}))

    def test_git_C_pegado_y_cd_mas_C_relativo(self):
        self.assertIsNone(self._decidir("git -C/wt push", "main", {self.WT: "hu/1-x"}))
        self.assertIsNone(self._decidir("cd /a && git -C b push", "main", {"/a/b": "hu/1-x"}))
        self.assertIsNotNone(self._decidir("git -C/wt push", "hu/1-x", {self.WT: "main"}))

    def test_escenario_4_sesion_en_hu_repo_en_main_sigue_bloqueando(self):
        ramas = {"/repo": "main"}
        mensaje_push = self._decidir("git -C /repo push origin main", "hu/229-x", ramas)
        self.assertEqual(mensaje_push, gi.decidir("git push origin main", "hu/229-x", BASES))
        mensaje_merge = self._decidir("git -C /repo merge hu/229-x", "hu/229-x", ramas)
        self.assertEqual(mensaje_merge, gi.decidir("git merge hu/229-x", "main", BASES))
        self.assertIsNotNone(mensaje_merge)
        # espejo: el mismo merge con /repo en una rama de historia se permite.
        self.assertIsNone(self._decidir("git -C /repo merge hu/229-x", "main", {"/repo": "hu/229-y"}))

    def test_escenario_5_dir_no_resoluble_usa_la_rama_de_la_sesion_y_deja_rastro(self):
        import os

        casos = (
            ("git -C /no-existe push", {}),  # rama_de -> None
            ("cd /no-existe && git push", {}),
        )
        for cmd, ramas in casos:
            for rama_sesion, esperado_bloquea in (("main", True), ("hu/1-x", False)):
                with self.subTest(cmd=cmd, rama_sesion=rama_sesion):
                    with tempfile.TemporaryDirectory() as tmp:
                        cwd = Path.cwd()
                        try:
                            os.chdir(tmp)
                            mensaje = self._decidir(cmd, rama_sesion, ramas)
                        finally:
                            os.chdir(cwd)
                        self.assertEqual(mensaje is not None, esperado_bloquea)
                        log = (Path(tmp) / ".timonel/events.log").read_text(encoding="utf-8")
                        self.assertIn("guard-integracion-fail-open rama-no-resuelta", log)

    def test_escenario_5_formas_no_resolubles_caen_a_la_sesion_con_fail_open(self):
        import os

        cmds = (
            "cd && git push",
            "cd - && git push",
            "cd ~/x && git push",
            "cd $WT && git push",
            "git --git-dir=/x/.git push",
            "git --work-tree /x push",
            "git -C $WT push",
        )
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                with tempfile.TemporaryDirectory() as tmp:
                    cwd = Path.cwd()
                    try:
                        os.chdir(tmp)
                        # rama_de que explotaria si se la consultara: no debe llamarse.
                        def rama_de(_d):
                            raise AssertionError("no debe consultar rama_de de un dir no resoluble")

                        self.assertIsNotNone(gi.decidir(cmd, "main", BASES, rama_de=rama_de))
                        self.assertIsNone(gi.decidir(cmd, "hu/1-x", BASES, rama_de=rama_de))
                    finally:
                        os.chdir(cwd)
                    log = (Path(tmp) / ".timonel/events.log").read_text(encoding="utf-8")
                    self.assertIn("rama-no-resuelta", log)

    def test_cd_en_comando_con_subshell_no_se_aplica(self):
        ramas = {self.WT: "hu/1-x"}
        # sin subshell el cd se aplica: permite desde sesion en main.
        self.assertIsNone(self._decidir("cd /wt && git push", "main", ramas))
        # con subshell el cd NO se modela: rama de la sesion (main) -> bloquea.
        import os

        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                os.chdir(tmp)
                self.assertIsNotNone(self._decidir("(cd /wt && git push)", "main", ramas))
            finally:
                os.chdir(cwd)

    def test_escenario_6_decidir_es_pura_no_lanza_subprocess(self):
        from unittest import mock

        with mock.patch.object(gi.subprocess, "run", side_effect=AssertionError("subprocess")):
            self.assertIsNone(self._decidir("git -C /wt push", "main", {self.WT: "hu/1-x"}))
            self.assertIsNotNone(self._decidir("git -C /wt push", "hu/1-x", {self.WT: "main"}))

    def test_dir_de_segmento_es_pura_y_resuelve_las_formas_del_contrato(self):
        casos = (
            # segmento, dir_actual, (efectivo, siguiente)
            (["cd", "wt"], ".", (".", "wt")),
            (["cd", "/a"], "wt", ("wt", "/a")),
            (["cd", "b"], "/a", ("/a", "/a/b")),
            (["cd", ".."], "/a/b", ("/a/b", "/a")),
            (["git", "-C", "/a", "-C", "b", "push"], ".", ("/a/b", ".")),
            (["git", "-C/a", "push"], ".", ("/a", ".")),
            (["git", "push"], "/a", ("/a", "/a")),
            (["git", "-c", "x=y", "-C", "wt", "push"], ".", ("wt", ".")),
            (["git", "--git-dir=/x", "push"], ".", (None, ".")),
            (["cd"], ".", (".", None)),
            (["cd", "-"], ".", (".", None)),
            (["cd", "~/x"], ".", (".", None)),
            (["cd", "$X"], ".", (".", None)),
            (["cd", "rel"], None, (None, None)),
            (["cd", "/abs"], None, (None, "/abs")),
            (["echo", "x"], "/a", ("/a", "/a")),
        )
        for segmento, dir_actual, esperado in casos:
            with self.subTest(segmento=segmento, dir_actual=dir_actual):
                self.assertEqual(gi._dir_de_segmento(segmento, dir_actual), esperado)

    def test_casos_35_y_58_con_la_rama_del_dir_explicita(self):
        """Reescritura de los casos 35 (`-C /otro/repo`) y 58 (`-Cpath`) de la tabla:
        el push a `main` bloquea sea cual sea la rama del dir (se juzga el destino)."""
        for cmd, dir_ in (("git -C /otro/repo push origin main", "/otro/repo"), ("git -Cpath push origin main", "path")):
            for rama_del_dir in ("main", "hu/82-x"):
                with self.subTest(cmd=cmd, rama_del_dir=rama_del_dir):
                    self.assertIsNotNone(self._decidir(cmd, "hu/82-x", {dir_: rama_del_dir}))


def _comandos_pretooluse() -> list[str]:
    """Extrae, del hooks.json REAL, los comandos del bloque PreToolUse/Bash."""
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    comandos = []
    for bloque in hooks["hooks"]["PreToolUse"]:
        for hook in bloque["hooks"]:
            comandos.append(hook["command"])
    return comandos


def _comando_guard_integracion() -> str:
    for cmd in _comandos_pretooluse():
        if "guard_integracion.py" in cmd:
            return cmd
    raise AssertionError("hooks.json no tiene ningun guard que invoque guard_integracion.py")


def _comando_guard_commits_consumidor() -> str:
    """El guard hermano de `_comando_guard_integracion`: selecciona por CONTENIDO del
    hooks.json real (el mensaje que bloquea commits directos en la base), nunca por
    indice del array -- un guard nuevo insertado antes correria este test contra el
    hook equivocado sin que nadie lo notara."""
    for cmd in _comandos_pretooluse():
        if "commit directo en la rama base" in cmd:
            return cmd
    raise AssertionError("hooks.json no tiene ningun guard que bloquee commits en la rama base")


def _comando_guard_commits_plugin() -> str:
    """Analogo a `_comando_guard_commits_consumidor` para el Entry A (guard del propio
    repo Timonel): selecciona por el literal unico de su mensaje de bloqueo, nunca por
    indice del array (#174)."""
    for cmd in _comandos_pretooluse():
        if "no se commitea directo en main" in cmd:
            return cmd
    raise AssertionError("hooks.json no tiene ningun guard que bloquee commits directos en main del plugin")


def _comando_push_force() -> str:
    for cmd in _comandos_pretooluse():
        if "push" in cmd and "--force" in cmd:
            return cmd
    raise AssertionError("hooks.json no tiene el guard de push --force")


def _preparar_repo(
    tmp: Path,
    rama: str,
    perfil: str,
    config_extra: dict | None = None,
    version: str | None = None,
    nombre: str = "timonel",
) -> None:
    """`config_extra` se mezcla (nivel superior) dentro de `.claude/timonel.config.json`
    en perfil `consumidor`, que hoy solo escribia `github.repo`. Lo necesita el caso 6
    de #85 para simular `{"git": {"protectBase": false}}` sin escribir un config a mano
    en cada test.

    `version` y `nombre` (#147) parametrizan el `.claude-plugin/plugin.json` que se
    escribe en perfil `plugin`: `HookVersionRepoInstaladaTests` los necesita para simular
    una version del repo distinta de la instalada, y un plugin con otro `name` (caso
    negro 3 de la tabla del issue)."""
    subprocess.run(["git", "init", "-q", "-b", rama], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "a@a.com"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=tmp, check=True)
    if rama != "main":
        subprocess.run(["git", "branch", "main"], cwd=tmp, check=True)
    if perfil == "plugin":
        (tmp / ".claude-plugin").mkdir(exist_ok=True)
        plugin_json = {"name": nombre}
        if version is not None:
            plugin_json["version"] = version
        (tmp / ".claude-plugin/plugin.json").write_text(json.dumps(plugin_json), encoding="utf-8")
    elif perfil == "consumidor":
        config = {"github": {"repo": "o/r"}}
        if config_extra:
            config.update(config_extra)
        (tmp / ".claude").mkdir(exist_ok=True)
        (tmp / ".claude/timonel.config.json").write_text(json.dumps(config), encoding="utf-8")


def _preparar_plugin_root(tmp: Path, version: str) -> Path:
    """Crea, dentro de `tmp`, un directorio separado que simula la instalacion
    (`CLAUDE_PLUGIN_ROOT`) con su propio `.claude-plugin/plugin.json` en `version` --
    distinto del `plugin.json` del repo que arma `_preparar_repo` (#147)."""
    root = tmp / "instalada"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "timonel", "version": version}), encoding="utf-8"
    )
    return root


def _comando_version_repo_vs_instalada() -> str:
    """Extrae, del hooks.json REAL, el cuarto hook de `SessionStart` (#147): avisa
    cuando la version del repo difiere de la instalada. Se selecciona por contenido
    (`version_instalada`, variable exclusiva de este hook), nunca por indice del array."""
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
    for bloque in hooks["hooks"]["SessionStart"]:
        for hook in bloque["hooks"]:
            if "version_instalada" in hook["command"]:
                return hook["command"]
    raise AssertionError("hooks.json no tiene el hook de version del repo vs instalada")


def _correr_hook(comando_hook: str, cmd_bash: str, tmp: Path, plugin_root: str) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    payload = json.dumps({"tool_input": {"command": cmd_bash}})
    return subprocess.run(
        ["bash", "-c", comando_hook],
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmp,
        env=env,
    )


class HookEnCajaNegraTests(unittest.TestCase):
    """Corre el shell REAL de hooks.json sobre un repo git temporal (perfil plugin)."""

    def test_tabla_de_casos_contra_el_hook_real(self):
        comando = _comando_guard_integracion()
        for numero, rama, cmd, esperado in TABLA_DE_CASOS:
            with self.subTest(caso=numero, cmd=cmd, rama=rama):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    _preparar_repo(tmp_path, rama, perfil="plugin")
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    if esperado == BLOQUEADO:
                        self.assertEqual(resultado.returncode, 2, f"caso {numero}: stderr={resultado.stderr!r}")
                        self.assertIn("[timonel] Bloqueado: ", resultado.stderr)
                    else:
                        self.assertEqual(resultado.returncode, 0, f"caso {numero}: stderr={resultado.stderr!r}")
                        self.assertEqual(
                            resultado.stderr,
                            "",
                            f"caso {numero}: no deberia imprimir nada en stderr, obtuvo {resultado.stderr!r}",
                        )

    def test_repo_sin_harness_no_bloquea_nada(self):
        """Caso 23: sin .claude/timonel.config.json ni plugin.json de timonel, el guard no corre."""
        comando = _comando_guard_integracion()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="ninguno")
            for cmd in ("git push origin main", "git merge hu/82-x", "gh pr merge 1"):
                resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                self.assertEqual(resultado.returncode, 0, f"`{cmd}` sin harness no deberia bloquear: {resultado.stderr!r}")

    def test_script_ausente_es_fail_open_caso_25(self):
        """Caso 25: perfil plugin pero guard_integracion.py no se puede resolver -> pasa."""
        comando = _comando_guard_integracion()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin")
            plugin_root_inexistente = str(tmp_path / "no-existe")
            resultado = _correr_hook(comando, "git merge hu/82-x", tmp_path, plugin_root=plugin_root_inexistente)
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")

    def test_push_force_bloqueado_en_perfil_plugin_caso_13(self):
        comando = _comando_push_force()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "hu/82-x", perfil="plugin")
            resultado = _correr_hook(comando, "git push --force origin hu/82-x", tmp_path, plugin_root=str(ROOT))
            self.assertEqual(resultado.returncode, 2)
            self.assertIn("[timonel] Bloqueado", resultado.stderr)

    def test_protectbase_false_ya_no_habilita_commit_en_base_caso_6(self):
        """Caso 6 de #85: `protectBase: false` quedo deprecado e ignorado (TIM-ADR-0002).
        Ejecucion real del hook, no un grep de su texto -- repo consumidor, rama `main`,
        config con el flag en `false`, `git commit` sigue bloqueado."""
        comando = _comando_guard_commits_consumidor()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="consumidor", config_extra={"git": {"protectBase": False}})
            resultado = _correr_hook(comando, 'git commit -m "x"', tmp_path, plugin_root=str(ROOT))
            self.assertEqual(resultado.returncode, 2, f"stderr={resultado.stderr!r}")
            self.assertIn("[timonel] Bloqueado", resultado.stderr)

    def test_mensaje_del_guard_de_commits_no_ofrece_protectbase_como_remedio_caso_7(self):
        """Caso 7 de #85: el mensaje de bloqueo ya no debe sugerir `protectBase: false`
        como salida, porque esa salida ya no existe."""
        comando = _comando_guard_commits_consumidor()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="consumidor", config_extra={"git": {"protectBase": False}})
            resultado = _correr_hook(comando, 'git commit -m "x"', tmp_path, plugin_root=str(ROOT))
            self.assertNotIn("protectBase", resultado.stderr)

    def test_push_force_bloqueado_incondicional_sin_script_caso_24(self):
        """Caso 24: aunque guard_integracion.py sea inexistente/no resoluble, --force sigue bloqueado
        (es la protección INCONDICIONAL, no fail-open) -- vive inline, no depende del script python."""
        comando = _comando_push_force()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "hu/82-x", perfil="plugin")
            plugin_root_inexistente = str(tmp_path / "no-existe")
            resultado = _correr_hook(comando, "git push --force origin hu/82-x", tmp_path, plugin_root=plugin_root_inexistente)
            self.assertEqual(resultado.returncode, 2, f"stderr={resultado.stderr!r}")
            self.assertIn("[timonel] Bloqueado", resultado.stderr)


def _preparar_repo_destino(tmp_base: Path, rama: str, nombre: str = "destino") -> Path:
    """Crea, DENTRO de `tmp_base` (el mismo tempdir que se pasa a `_correr_hook`), un
    SEGUNDO repo git independiente (la "otra ruta" del issue #174) con su propia
    rama, sin perfil (el guard no lee config/plugin.json del destino, solo su rama).
    Al vivir dentro de `tmp_base`, se limpia solo con el `TemporaryDirectory` del
    llamador -- sin necesidad de borrarlo a mano."""
    destino = tmp_base / nombre
    destino.mkdir(parents=True, exist_ok=True)
    _preparar_repo(destino, rama, perfil="ninguno")
    return destino


class GuardResuelveConfigDesdeWorktreeTests(unittest.TestCase):
    """#210 Esc 5: el guard lee `baseBranches` del config del checkout principal desde un
    worktree sin `.claude/`, y deja rastro cuando cae a los defaults por un "no se"."""

    def _principal_con_worktree(self, base: Path, config_texto: str) -> tuple[Path, Path]:
        principal = base / "principal"
        principal.mkdir()
        _preparar_repo(principal, "main", perfil="consumidor")
        (principal / ".claude/timonel.config.json").write_text(config_texto, encoding="utf-8")
        wt = base / "wt"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "hu/210-x", str(wt)], cwd=principal, check=True)
        return principal, wt

    def _bases_en(self, directorio: Path) -> tuple[list[str], str]:
        previo = Path.cwd()
        err = io.StringIO()
        try:
            os.chdir(directorio)
            with contextlib.redirect_stderr(err):
                return gi._bases_configuradas(), err.getvalue()
        finally:
            os.chdir(previo)

    def test_bases_desde_worktree_vienen_del_config_principal(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, wt = self._principal_con_worktree(Path(tmp).resolve(), json.dumps({"git": {"baseBranches": ["trunk"]}}))
            bases, rastro = self._bases_en(wt)
        self.assertEqual(bases, ["trunk"])
        self.assertEqual(rastro, "")

    def test_config_ilegible_cae_a_defaults_con_rastro(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, wt = self._principal_con_worktree(Path(tmp).resolve(), "{no es json")
            bases, rastro = self._bases_en(wt)
        self.assertEqual(bases, gi.BASES_POR_DEFECTO)
        self.assertIn("config ilegible", rastro)

    def test_no_se_cae_a_defaults_con_rastro(self):
        with tempfile.TemporaryDirectory() as tmp:
            bases, rastro = self._bases_en(Path(tmp).resolve())
        self.assertEqual(bases, gi.BASES_POR_DEFECTO)
        self.assertIn("no se resolvio el config", rastro)
        self.assertIn("not a git repository", rastro)

    def test_compuerta_del_hook_deja_pasar_al_worktree_sin_claude(self):
        """`gh pr merge` siempre se bloquea; sin la rama de la compuerta el worktree salia con 0."""
        comando = _comando_guard_integracion()
        with tempfile.TemporaryDirectory() as tmp:
            _, wt = self._principal_con_worktree(Path(tmp).resolve(), json.dumps({"git": {"baseBranches": ["main"]}}))
            self.assertFalse((wt / ".claude").exists())
            resultado = _correr_hook(comando, "gh pr merge 1", wt, plugin_root=str(ROOT))
        self.assertEqual(resultado.returncode, 2, f"stderr={resultado.stderr!r}")
        self.assertIn("[timonel] Bloqueado: ", resultado.stderr)


class GuardCommitsResuelveRepoDestinoTests(unittest.TestCase):
    """#174: los guards de commit deben resolver la rama del repositorio donde el
    comando `git commit` realmente corre (`git -C <ruta>` o `cd <ruta> &&`), no la
    del cwd de la sesion (`tmp`, el mismo directorio que recibe `_correr_hook`).

    Unidad de verificacion: el `command` de CADA entry por separado, ejecutado con el
    runner de caja negra `_correr_hook` (bash -c real, payload JSON por stdin, sin
    grep sobre el texto de hooks.json). Cada fila arma un `tmp` (cwd de la sesion) y
    un `destino` (segundo repo temporal) con ramas que, si el guard resolviera la
    rama equivocada, produciria el resultado CONTRARIO al esperado -- asi la mutacion
    (revertir el fix a `branch=$(git branch --show-current 2>/dev/null)`) hace fallar
    la fila, no solo dejarla en un estado casualmente igual.
    """

    def _escenario(self, comando, perfil_cwd, rama_cwd, rama_destino, cmd_template, config_extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, rama_cwd, perfil=perfil_cwd, config_extra=config_extra)
            destino = _preparar_repo_destino(tmp_path, rama_destino)
            cmd_bash = cmd_template.format(destino=str(destino))
            return _correr_hook(comando, cmd_bash, tmp_path, plugin_root=str(ROOT))

    # --- Entry A: guard del propio plugin (mensaje "no se commitea directo en main") ---
    # Bases fijas main/master; exige #N en cualquier parte del mensaje (no atado al
    # numero de la rama).

    CASOS_ENTRY_PLUGIN = [
        # numero, mecanismo, rama_cwd, rama_destino, cmd_template, esperado
        (
            "AC1",
            "git -C explicito: la rama sale del destino, no del cwd",
            "hu/999-y",  # si se usara el cwd (no es main), pasaria -- bug enmascarado
            "main",      # el destino SI es base -> debe bloquear
            'git -C {destino} commit -m "arregla resolucion de rama via -C (#1)"',
            BLOQUEADO,
        ),
        (
            "AC2",
            "cd sin -C: la rama sale del destino, no del cwd",
            "main",        # si se usara el cwd, bloquearia -- bug enmascarado
            "hu/174-x",    # el destino NO es base -> debe permitir
            'cd {destino} && git commit -m "fix #174"',
            PERMITIDO,
        ),
        (
            "AC3a",
            "sin ruta, sesion de un solo repositorio: identico a hoy (cwd en base)",
            "main",
            "main",  # no se usa (no hay ruta en el comando)
            'git commit -m "fix #1"',
            BLOQUEADO,
        ),
        (
            "AC3b",
            "sin ruta, sesion de un solo repositorio: identico a hoy (cwd fuera de base)",
            "hu/1-x",
            "main",  # no se usa
            'git commit -m "fix #1"',
            PERMITIDO,
        ),
        (
            "AC4",
            "reproduccion real: cwd en rama base + commit dirigido a otro repo en rama de trabajo -> no bloquea por rama base",
            "main",
            "hu/174-x",
            'git -C {destino} commit -m "fix #174: resuelve rama via -C"',
            PERMITIDO,
        ),
    ]

    def test_entry_plugin_resuelve_rama_del_destino(self):
        comando = _comando_guard_commits_plugin()
        for numero, motivo, rama_cwd, rama_destino, cmd_template, esperado in self.CASOS_ENTRY_PLUGIN:
            with self.subTest(caso=numero, motivo=motivo):
                resultado = self._escenario(comando, "plugin", rama_cwd, rama_destino, cmd_template)
                if esperado == BLOQUEADO:
                    self.assertEqual(resultado.returncode, 2, f"caso {numero} ({motivo}): stderr={resultado.stderr!r}")
                    self.assertIn("[timonel] Bloqueado", resultado.stderr)
                else:
                    self.assertEqual(resultado.returncode, 0, f"caso {numero} ({motivo}): stderr={resultado.stderr!r}")

    # --- Entry B: guard del repo consumidor (mensaje "commit directo en la rama base") ---
    # Bases configurables (default main/master/develop, leidas del cwd); exige #N solo
    # si la rama resuelta empieza con `hu/`, y el numero debe coincidir con ESA rama.

    CASOS_ENTRY_CONSUMIDOR = [
        (
            "AC1",
            "git -C explicito: bloquea porque el DESTINO es base, aunque el cwd no lo sea",
            "feature-abc",  # no es base ni hu/* -> si se usara el cwd, pasaria
            "main",         # el destino SI es base -> debe bloquear
            'git -C {destino} commit -m "arregla resolucion de rama via -C"',
            BLOQUEADO,
        ),
        (
            "AC2",
            "cd sin -C: permite porque el DESTINO no es base, aunque el cwd si lo sea",
            "main",          # si se usara el cwd, bloquearia
            "feature-xyz",   # el destino no es base ni hu/* -> debe permitir
            'cd {destino} && git commit -m "cualquier cosa"',
            PERMITIDO,
        ),
        (
            "AC3a",
            "sin ruta, sesion de un solo repositorio: identico a hoy (cwd en base)",
            "main",
            "main",  # no se usa
            'git commit -m "x"',
            BLOQUEADO,
        ),
        (
            "AC3b",
            "sin ruta, sesion de un solo repositorio: identico a hoy (cwd fuera de base)",
            "feature-abc",
            "main",  # no se usa
            'git commit -m "x"',
            PERMITIDO,
        ),
        (
            "AC4",
            "reproduccion real (issue #174): cwd en rama base + commit dirigido a otro repo en rama de trabajo -> no bloquea por rama base",
            "main",
            "feature-xyz",
            'git -C {destino} commit -m "arregla resolucion de rama via -C"',
            PERMITIDO,
        ),
        (
            "AC5",
            "el numero de issue exigido sale del hu/<N> del DESTINO, no del cwd",
            "hu/999-x",     # si se usara el cwd, exigiria #999 y el mensaje trae #174 -> bloquearia
            "hu/174-y",     # el destino exige #174
            'git -C {destino} commit -m "fix #174: resuelve rama via -C"',
            PERMITIDO,
        ),
    ]

    def test_entry_consumidor_resuelve_rama_del_destino(self):
        comando = _comando_guard_commits_consumidor()
        for numero, motivo, rama_cwd, rama_destino, cmd_template, esperado in self.CASOS_ENTRY_CONSUMIDOR:
            with self.subTest(caso=numero, motivo=motivo):
                resultado = self._escenario(comando, "consumidor", rama_cwd, rama_destino, cmd_template)
                if esperado == BLOQUEADO:
                    self.assertEqual(resultado.returncode, 2, f"caso {numero} ({motivo}): stderr={resultado.stderr!r}")
                    self.assertIn("[timonel] Bloqueado", resultado.stderr)
                else:
                    self.assertEqual(resultado.returncode, 0, f"caso {numero} ({motivo}): stderr={resultado.stderr!r}")

    # --- Limite conocido (documentado en el issue): NO perseguir, solo fijar como
    # comportamiento esperado -- un `cd` que no esta al inicio del comando, o una ruta
    # entrecomillada con espacios, degradan al cwd de la sesion. ---

    def test_limite_cd_no_al_inicio_degrada_al_cwd_entry_plugin(self):
        """`true && cd <destino> && ...` no matchea el patron `^cd` (anclado al
        inicio): el `dir` resuelto queda vacio y cae al cwd. Con cwd fuera de base y
        destino en base, el resultado documentado es PERMITIDO (no ve el destino)."""
        comando = _comando_guard_commits_plugin()
        resultado = self._escenario(
            comando, "plugin", "hu/999-y", "main",
            'true && cd {destino} && git commit -m "fix #1"',
        )
        self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")

    def test_limite_cd_no_al_inicio_degrada_al_cwd_entry_consumidor(self):
        comando = _comando_guard_commits_consumidor()
        resultado = self._escenario(
            comando, "consumidor", "feature-abc", "main",
            'true && cd {destino} && git commit -m "x"',
        )
        self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")

    def test_limite_ruta_entrecomillada_con_espacios_degrada_al_cwd(self):
        """`cd "<ruta con espacios>" && ...` SI matchea `^cd`, pero el `dir` capturado
        incluye las comillas literales (`"..."`): `[ -d "$dir" ]` nunca es cierto para
        ese literal, cae a `.`. Documentado, no corregido (issue #174)."""
        comando = _comando_guard_commits_consumidor()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "feature-abc", perfil="consumidor")
            destino = _preparar_repo_destino(tmp_path, "main", nombre="destino con espacios")
            cmd_bash = f'cd "{destino}" && git commit -m "x"'
            resultado = _correr_hook(comando, cmd_bash, tmp_path, plugin_root=str(ROOT))
            # cwd ("feature-abc") no es base -> pasa, aunque el destino real ("main") si lo es.
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")


class HookVersionRepoInstaladaTests(unittest.TestCase):
    """AC4 de #147: el cuarto hook de `SessionStart` avisa, en perfil plugin, cuando la
    version del repo difiere de la instalada -- nombrando ambas -- y calla en cualquier
    otro caso (version igual, otro plugin, repo consumidor, `CLAUDE_PLUGIN_ROOT` vacio).
    Molde de `HookEnCajaNegraTests`: shell REAL de hooks.json sobre repos temporales.
    Tabla de casos del issue: 1 (avisa), 2 (silencio por version igual), 3 (silencio por
    otro plugin), 4 (silencio por repo consumidor), 5 (silencio fail-open)."""

    def test_versiones_distintas_avisa_con_ambas_caso_1(self):
        comando = _comando_version_repo_vs_instalada()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin", version="9.9.9")
            plugin_root = _preparar_plugin_root(tmp_path, "0.6.0")
            resultado = _correr_hook(comando, "", tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")
            self.assertIn("9.9.9", resultado.stdout)
            self.assertIn("0.6.0", resultado.stdout)

    def test_versiones_iguales_silencio_caso_2(self):
        comando = _comando_version_repo_vs_instalada()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin", version="0.6.0")
            plugin_root = _preparar_plugin_root(tmp_path, "0.6.0")
            resultado = _correr_hook(comando, "", tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")
            self.assertEqual(resultado.stdout, "")

    def test_otro_plugin_silencio_caso_3(self):
        comando = _comando_version_repo_vs_instalada()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin", version="9.9.9", nombre="otro-plugin")
            plugin_root = _preparar_plugin_root(tmp_path, "0.6.0")
            resultado = _correr_hook(comando, "", tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")
            self.assertEqual(resultado.stdout, "")

    def test_repo_consumidor_sin_plugin_json_silencio_caso_4(self):
        comando = _comando_version_repo_vs_instalada()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="ninguno")
            plugin_root = _preparar_plugin_root(tmp_path, "0.6.0")
            resultado = _correr_hook(comando, "", tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")
            self.assertEqual(resultado.stdout, "")

    def test_claude_plugin_root_vacio_silencio_fail_open_caso_5(self):
        comando = _comando_version_repo_vs_instalada()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin", version="9.9.9")
            resultado = _correr_hook(comando, "", tmp_path, plugin_root="")
            self.assertEqual(resultado.returncode, 0, f"stderr={resultado.stderr!r}")
            self.assertEqual(resultado.stdout, "")


class DetectarCommitUnitarioTests(unittest.TestCase):
    """Tabla B del contrato de #176: `invoca_commit()` puro, sin disco ni procesos.
    Cubre la CLASE de formas de invocar (o no invocar) un commit real, no un
    representante (`heuristics/general/enumerar-la-clase-no-el-representante.md`)."""

    SI, NO, NOSE = True, False, None

    CASOS = [
        ("git commit", 'git commit -m "x"', SI),
        ("git -C <r> commit, valor separado", 'git -C /tmp/r commit -m "x"', SI),
        ("git -C<r> commit, valor pegado", 'git -C/tmp/r commit -m "x"', SI),
        ("git --no-pager commit (flag booleano)", 'git --no-pager commit -m "x"', SI),
        ("git -c user.name=x commit (flag con valor)", 'git -c user.name=x commit -m "x"', SI),
        ("GIT_DIR=.git git commit (asignacion de entorno)", 'GIT_DIR=.git git commit -m "x"', SI),
        ("cd <r> && git commit", 'cd /tmp/r && git commit -m "x"', SI),
        ("git commit tras ;", 'git status; git commit -m "x"', SI),
        ("git commit tras ||", 'git status || git commit -m "x"', SI),
        ("git commit tras |", 'git status | git commit -m "x"', SI),
        ("git commit tras salto de linea", 'git status\ngit commit -m "x"', SI),
        ('echo con la cadena entre comillas', 'echo "git commit"', NO),
        ("grep con la cadena como patron", "grep -n 'git commit' archivo.txt", NO),
        ("printf con la cadena", 'printf "git commit"', NO),
        ("bash -n sobre un archivo llamado commit-msg", "bash -n .githooks/commit-msg", NO),
        ("bash -n con el glob CRUDO sin expandir (AC6, #176/#122)", "bash -n scripts/*.sh .githooks/*", NO),
        ("git status", "git status", NO),
        ("git log", "git log", NO),
        ("git commit-graph write: subcomando DISTINTO que empieza con commit", "git commit-graph write", NO),
        ("comando vacio", "", NO),
        ("comilla sin cerrar: no se, no False", 'git commit -m "sin cerrar', NOSE),
    ]

    def test_tabla_de_casos(self):
        for descripcion, cmd, esperado in self.CASOS:
            with self.subTest(caso=descripcion, cmd=cmd):
                self.assertIs(dc.invoca_commit(cmd), esperado)


class DetectarCommitSoloUsaSeamsPublicosDeGuardIntegracionTests(unittest.TestCase):
    """Contrato entre modulos (#176, hallazgo 1 del code review): `detectar_commit.py`
    solo puede depender de los DOS seams publicos de `guard_integracion.py` --
    `segmentar()` y `clave_de_segmento()` -- nunca de una privada (prefijo `_`) como
    `_clave_y_resto`, que el docstring de `guard_integracion.py` trata como zona
    interna sujeta a cambiar sin aviso.

    Prueba de COMPORTAMIENTO, no un `assertIn` sobre el texto del archivo (esa clase
    de asercion esta prohibida en esta HU por vacua,
    `heuristics/general/unidad-de-verificacion.md`): se sustituye el seam publico
    `clave_de_segmento` por un doble que MIENTE deliberadamente, y se confirma que
    `invoca_commit()` sigue el resultado del doble en vez del que devolveria el
    parser real. Si `detectar_commit` llamara a `_clave_y_resto` directamente (el
    defecto original), el doble de abajo no tendria ningun efecto y el test fallaria
    -- es la mutacion que reproduce el hallazgo 1."""

    def test_invoca_commit_sigue_el_resultado_del_seam_publico_no_la_privada(self):
        original = gi.clave_de_segmento

        def doble_que_miente(segmento):
            # 'git status' NO invoca un commit real; el doble dice que si.
            # Si invoca_commit usa el VALOR DEVUELTO por el seam publico, el
            # resultado sigue al doble (True). Si en cambio resolviera la clave
            # llamando `_clave_y_resto` por su cuenta (bypaseando este doble), el
            # resultado real para 'git status' seria False y el test fallaria.
            return dc.CLAVE_COMMIT

        gi.clave_de_segmento = doble_que_miente
        try:
            resultado = dc.invoca_commit("git status")
        finally:
            gi.clave_de_segmento = original

        self.assertTrue(
            resultado,
            "invoca_commit no siguio el resultado de clave_de_segmento(): "
            "sugiere que resuelve la clave por otro camino (posible privada directa)",
        )

    def test_segmentar_y_clave_de_segmento_son_los_dos_seams_que_el_sensor_usa(self):
        """Los dos seams publicos existen, son invocables desde fuera del modulo (sin
        tocar `_clave_y_resto`) y, encadenados, reproducen la deteccion de un commit
        real -- el mismo camino que sigue `invoca_commit()`."""
        segmentos = gi.segmentar('git status && git commit -m "x"')
        self.assertIsNotNone(segmentos)
        claves = [gi.clave_de_segmento(s) for s in segmentos]
        self.assertIn(dc.CLAVE_COMMIT, claves)


class GuardCommitsDetectaCommitRealTests(unittest.TestCase):
    """Tabla A del contrato de #176: los escenarios de deteccion de commit real contra
    CADA entry por separado. Unidad de verificacion: el `command` real de cada entry
    via `_correr_hook` -- nunca `assertIn`/`assertRegex` sobre el texto de
    `hooks/hooks.json`, `scripts/detectar_commit.py` ni `scripts/guard_integracion.py`
    (asercion prohibida del contrato, `heuristics/general/unidad-de-verificacion.md`).

    AC8 (filas `-C` sin la frase del matcher) se verifica en
    `GuardCommitsResuelveRepoDestinoTests`, ya editada para no incluir la frase. AC9
    (mutacion de un solo entry) es evidencia manual del reporte del implementador, no
    un test persistido."""

    HEREDOC_MATCHER = (
        "cat > /tmp/heredoc-176.md <<'TIMONEL_EOF'\n"
        "- Archivo: `hooks/hooks.json`, los dos entries `PreToolUse`/`Bash` que filtran "
        "por `*'git commit'*`.\n"
        "TIMONEL_EOF"
    )

    def _bloqueado(self, resultado):
        return resultado.returncode == 2 and "[timonel] Bloqueado" in resultado.stderr

    def _permitido(self, resultado):
        return resultado.returncode == 0 and resultado.stderr == ""

    def _con_repo(self, perfil, rama):
        tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp.name)
        _preparar_repo(tmp_path, rama, perfil=perfil)
        return tmp, tmp_path

    # --- Entry plugin (bases fijas main/master, exige #N en cualquier parte) ---

    def test_entry_plugin_ac1_bloquea_commit_sin_numero_en_rama_hu(self):
        comando = _comando_guard_commits_plugin()
        tmp, tmp_path = self._con_repo("plugin", "hu/1-x")
        with tmp:
            resultado = _correr_hook(comando, 'git commit -m "sin numero"', tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._bloqueado(resultado), resultado.stderr)

    def test_entry_plugin_ac2_bloquea_por_rama_base_del_destino_con_dashC(self):
        comando = _comando_guard_commits_plugin()
        tmp, tmp_path = self._con_repo("plugin", "hu/999-y")
        with tmp:
            destino = _preparar_repo_destino(tmp_path, "main")
            resultado = _correr_hook(comando, f'git -C {destino} commit -m "x #1"', tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._bloqueado(resultado), resultado.stderr)

    def test_entry_plugin_ac3_bloquea_por_rama_base_del_destino_con_cd(self):
        comando = _comando_guard_commits_plugin()
        tmp, tmp_path = self._con_repo("plugin", "hu/999-y")
        with tmp:
            destino = _preparar_repo_destino(tmp_path, "main")
            resultado = _correr_hook(comando, f'cd {destino} && git commit -m "x #1"', tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._bloqueado(resultado), resultado.stderr)

    def test_entry_plugin_ac4_heredoc_que_cita_la_cadena_no_bloquea(self):
        comando = _comando_guard_commits_plugin()
        tmp, tmp_path = self._con_repo("plugin", "main")
        with tmp:
            resultado = _correr_hook(comando, self.HEREDOC_MATCHER, tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._permitido(resultado), resultado.stderr)

    def test_entry_plugin_ac5_echo_grep_printf_no_bloquean(self):
        comando = _comando_guard_commits_plugin()
        for cmd in ('echo "git commit"', "grep -n 'git commit' archivo.txt", 'printf "git commit"'):
            with self.subTest(cmd=cmd):
                tmp, tmp_path = self._con_repo("plugin", "main")
                with tmp:
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertTrue(self._permitido(resultado), resultado.stderr)

    def test_entry_plugin_ac6_nombre_de_archivo_commit_msg_no_es_commit(self):
        comando = _comando_guard_commits_plugin()
        for cmd in (
            "bash -n .githooks/commit-msg",
            "bash -n scripts/_common.sh .githooks/commit-msg",
            "bash -n scripts/*.sh .githooks/*",
        ):
            with self.subTest(cmd=cmd):
                tmp, tmp_path = self._con_repo("plugin", "main")
                with tmp:
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertTrue(self._permitido(resultado), resultado.stderr)

    def test_entry_plugin_ac7_subcomandos_de_git_que_no_son_commit_no_bloquean(self):
        comando = _comando_guard_commits_plugin()
        for cmd in ("git status --short", "git log --oneline -5"):
            with self.subTest(cmd=cmd):
                tmp, tmp_path = self._con_repo("plugin", "main")
                with tmp:
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertTrue(self._permitido(resultado), resultado.stderr)

    # --- Entry consumidor (bases configurables, exige #N solo si la rama es hu/*) ---

    def test_entry_consumidor_ac1_bloquea_commit_sin_numero_en_rama_hu(self):
        comando = _comando_guard_commits_consumidor()
        tmp, tmp_path = self._con_repo("consumidor", "hu/1-x")
        with tmp:
            resultado = _correr_hook(comando, 'git commit -m "sin numero"', tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._bloqueado(resultado), resultado.stderr)

    def test_entry_consumidor_ac2_bloquea_por_rama_base_del_destino_con_dashC(self):
        comando = _comando_guard_commits_consumidor()
        tmp, tmp_path = self._con_repo("consumidor", "feature-abc")
        with tmp:
            destino = _preparar_repo_destino(tmp_path, "main")
            resultado = _correr_hook(comando, f'git -C {destino} commit -m "x"', tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._bloqueado(resultado), resultado.stderr)

    def test_entry_consumidor_ac3_bloquea_por_rama_base_del_destino_con_cd(self):
        comando = _comando_guard_commits_consumidor()
        tmp, tmp_path = self._con_repo("consumidor", "feature-abc")
        with tmp:
            destino = _preparar_repo_destino(tmp_path, "main")
            resultado = _correr_hook(comando, f'cd {destino} && git commit -m "x"', tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._bloqueado(resultado), resultado.stderr)

    def test_entry_consumidor_ac4_heredoc_que_cita_la_cadena_no_bloquea(self):
        comando = _comando_guard_commits_consumidor()
        tmp, tmp_path = self._con_repo("consumidor", "main")
        with tmp:
            resultado = _correr_hook(comando, self.HEREDOC_MATCHER, tmp_path, plugin_root=str(ROOT))
            self.assertTrue(self._permitido(resultado), resultado.stderr)

    def test_entry_consumidor_ac5_echo_grep_printf_no_bloquean(self):
        comando = _comando_guard_commits_consumidor()
        for cmd in ('echo "git commit"', "grep -n 'git commit' archivo.txt", 'printf "git commit"'):
            with self.subTest(cmd=cmd):
                tmp, tmp_path = self._con_repo("consumidor", "main")
                with tmp:
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertTrue(self._permitido(resultado), resultado.stderr)

    def test_entry_consumidor_ac6_nombre_de_archivo_commit_msg_no_es_commit(self):
        comando = _comando_guard_commits_consumidor()
        for cmd in (
            "bash -n .githooks/commit-msg",
            "bash -n scripts/_common.sh .githooks/commit-msg",
            "bash -n scripts/*.sh .githooks/*",
        ):
            with self.subTest(cmd=cmd):
                tmp, tmp_path = self._con_repo("consumidor", "main")
                with tmp:
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertTrue(self._permitido(resultado), resultado.stderr)

    def test_entry_consumidor_ac7_subcomandos_de_git_que_no_son_commit_no_bloquean(self):
        comando = _comando_guard_commits_consumidor()
        for cmd in ("git status --short", "git log --oneline -5"):
            with self.subTest(cmd=cmd):
                tmp, tmp_path = self._con_repo("consumidor", "main")
                with tmp:
                    resultado = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertTrue(self._permitido(resultado), resultado.stderr)


class GuardCommitsRespaldoSinSensorTests(unittest.TestCase):
    """Tabla C del contrato de #176: sin el sensor disponible (`CLAUDE_PLUGIN_ROOT`
    apunta a una instalacion sin `detectar_commit.py`, y el cwd del repo temporal no
    tiene `./scripts/detectar_commit.py`), el comportamiento debe ser IDENTICO al de
    hoy -- la compuerta literal `case "$cmd" in *'git commit'*)` como respaldo. Se
    verifica en caja negra (comportamiento), no leyendo el texto del entry."""

    HEREDOC_MATCHER = GuardCommitsDetectaCommitRealTests.HEREDOC_MATCHER

    def _plugin_root_sin_sensor(self, tmp_path):
        root = tmp_path / "instalada-sin-sensor"
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        return root

    def test_entry_plugin_heredoc_reaparece_como_falso_positivo_sin_sensor(self):
        """Sin sensor, el falso positivo de #174 reaparece -- es el respaldo
        degradado, no una regresion del arreglo."""
        comando = _comando_guard_commits_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin")
            plugin_root = self._plugin_root_sin_sensor(tmp_path)
            resultado = _correr_hook(comando, self.HEREDOC_MATCHER, tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 2, resultado.stderr)
            self.assertIn("[timonel] Bloqueado", resultado.stderr)

    def test_entry_plugin_dashC_no_reconocido_sin_sensor(self):
        """Sin sensor, el falso negativo de #174 reaparece: `git -C <ruta> commit` no
        contiene el literal contiguo `git commit`, la compuerta de respaldo lo deja pasar."""
        comando = _comando_guard_commits_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "hu/999-y", perfil="plugin")
            destino = _preparar_repo_destino(tmp_path, "main")
            plugin_root = self._plugin_root_sin_sensor(tmp_path)
            resultado = _correr_hook(comando, f'git -C {destino} commit -m "x #1"', tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_entry_consumidor_heredoc_reaparece_como_falso_positivo_sin_sensor(self):
        comando = _comando_guard_commits_consumidor()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="consumidor")
            plugin_root = self._plugin_root_sin_sensor(tmp_path)
            resultado = _correr_hook(comando, self.HEREDOC_MATCHER, tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 2, resultado.stderr)
            self.assertIn("[timonel] Bloqueado", resultado.stderr)

    def test_entry_consumidor_dashC_no_reconocido_sin_sensor(self):
        comando = _comando_guard_commits_consumidor()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "feature-abc", perfil="consumidor")
            destino = _preparar_repo_destino(tmp_path, "main")
            plugin_root = self._plugin_root_sin_sensor(tmp_path)
            resultado = _correr_hook(comando, f'git -C {destino} commit -m "x"', tmp_path, plugin_root=str(plugin_root))
            self.assertEqual(resultado.returncode, 0, resultado.stderr)


class GuardRamaPorSegmentoCajaNegraTests(unittest.TestCase):
    """#229: el hook REAL (`bash -c` + `main()` con `rama_de` real) sobre repos git
    reales. La sesion (cwd = `tmp`) y el directorio destino tienen ramas opuestas, de
    modo que juzgar contra la rama equivocada daria el resultado contrario."""

    def _worktree_de_historia(self, tmp: Path, rama: str = "hu/229-x") -> Path:
        wt = tmp / "wt"
        subprocess.run(["git", "worktree", "add", "-q", "-b", rama, str(wt)], cwd=tmp, check=True)
        return wt

    def test_sesion_en_main_push_desde_worktree_en_hu_se_permite(self):
        comando = _comando_guard_integracion()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "main", perfil="plugin")
            wt = self._worktree_de_historia(tmp_path)
            for cmd in (
                f"git -C {wt} push",
                f"git -C {wt} push origin hu/229-x",
                f"cd {wt} && git push",
                f"cd {wt} && git push origin hu/229-x",
                f"git -C {wt} commit -m 'x #229' ; git -C {wt} push",
            ):
                with self.subTest(cmd=cmd):
                    r = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stderr, "")

    def test_control_sin_resolver_el_mismo_push_bloquea_si_el_dir_esta_en_main(self):
        """Espejo: mismas formas, pero el dir destino esta en `main` y la sesion en hu."""
        comando = _comando_guard_integracion()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "hu/229-x", perfil="plugin")
            destino = _preparar_repo_destino(tmp_path, "main")
            for cmd in (f"git -C {destino} push", f"cd {destino} && git push"):
                with self.subTest(cmd=cmd):
                    r = _correr_hook(comando, cmd, tmp_path, plugin_root=str(ROOT))
                    self.assertEqual(r.returncode, 2, r.stderr)

    def test_sesion_en_hu_repo_en_main_sigue_bloqueando_con_el_mismo_mensaje(self):
        comando = _comando_guard_integracion()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _preparar_repo(tmp_path, "hu/229-x", perfil="plugin")
            repo = _preparar_repo_destino(tmp_path, "main", nombre="repo")
            # mensajes de referencia: los de `decidir` sin `-C`, parado en cada rama.
            ref_push = gi.decidir("git push origin main", "hu/229-x", BASES)
            ref_merge = gi.decidir("git merge hu/229-x", "main", BASES)
            r_push = _correr_hook(comando, f"git -C {repo} push origin main", tmp_path, plugin_root=str(ROOT))
            r_merge = _correr_hook(comando, f"git -C {repo} merge hu/229-x", tmp_path, plugin_root=str(ROOT))
            self.assertEqual(r_push.returncode, 2, r_push.stderr)
            self.assertEqual(r_push.stderr, ref_push + "\n")
            self.assertEqual(r_merge.returncode, 2, r_merge.stderr)
            self.assertEqual(r_merge.stderr, ref_merge + "\n")

    def test_dir_inexistente_mantiene_el_comportamiento_actual_y_deja_rastro(self):
        comando = _comando_guard_integracion()
        for rama_sesion, esperado in (("main", 2), ("hu/229-x", 0)):
            with self.subTest(rama_sesion=rama_sesion):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    _preparar_repo(tmp_path, rama_sesion, perfil="plugin")
                    r = _correr_hook(comando, f"git -C {tmp_path}/no-existe push", tmp_path, plugin_root=str(ROOT))
                    self.assertEqual(r.returncode, esperado, r.stderr)
                    log = (tmp_path / ".timonel/events.log").read_text(encoding="utf-8")
                    self.assertIn("guard-integracion-fail-open rama-no-resuelta", log)


if __name__ == "__main__":
    unittest.main()
