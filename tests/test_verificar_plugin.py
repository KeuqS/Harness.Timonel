"""Tests de scripts/verificar_plugin.py (#270).

Nunca corren la suite real del plugin (recursion): el subproceso real opera sobre
un repo de juguete y main() en proceso parchea `subprocess.run`."""

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verificar_plugin as vp  # noqa: E402

SCRIPT = ROOT / "scripts" / "verificar_plugin.py"
TEST_VERDE = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"
TEST_ROJO = TEST_VERDE.replace("assertTrue(True)", "assertTrue(False)")


def _repo_minimo(tmp: str) -> Path:
    repo = Path(tmp) / "repo"
    for d in ("tests", "scripts", ".githooks", ".claude-plugin", "hooks"):
        (repo / d).mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text(TEST_VERDE, encoding="utf-8")
    (repo / "scripts" / "a.sh").write_text("#!/bin/bash\necho hola\n", encoding="utf-8")
    (repo / ".githooks" / "commit-msg").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    for j in vp.JSON_FIJOS:
        (repo / j).write_text("{}\n", encoding="utf-8")
    return repo


def _correr(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=repo, capture_output=True, text=True, timeout=120,
    )


class SubprocesoRealTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = _repo_minimo(tmp.name)

    def test_todo_verde_imprime_una_linea_por_paso_y_passed(self):
        r = _correr(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lineas = r.stdout.splitlines()
        self.assertEqual(len(lineas), 4)
        self.assertEqual(lineas[0], "unittest: PASSED (Ran 1 tests)")
        self.assertTrue(lineas[1].startswith("bash -n: PASSED"))
        self.assertTrue(lineas[2].startswith("jq: PASSED"))
        self.assertEqual(lineas[3], "PASSED")

    def test_sh_roto_falla_bash_n_y_corre_los_otros_pasos(self):
        (self.repo / "scripts" / "a.sh").write_text("if then\n", encoding="utf-8")
        r = _correr(self.repo)
        self.assertEqual(r.returncode, 1)
        lineas = r.stdout.splitlines()
        self.assertEqual(lineas[-1], "FAILED: bash -n")
        self.assertIn("bash -n: FAILED (scripts/a.sh)", lineas)
        self.assertTrue(lineas[0].startswith("unittest: PASSED"))
        self.assertTrue(any(l.startswith("jq: PASSED") for l in lineas))

    def test_test_rojo_falla_unittest(self):
        (self.repo / "tests" / "test_x.py").write_text(TEST_ROJO, encoding="utf-8")
        r = _correr(self.repo)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines()[-1], "FAILED: unittest")
        self.assertEqual(len(r.stdout.splitlines()), 4)

    def test_json_invalido_falla_jq(self):
        (self.repo / "hooks" / "hooks.json").write_text("{no", encoding="utf-8")
        r = _correr(self.repo)
        self.assertEqual(r.returncode, 1)
        lineas = r.stdout.splitlines()
        self.assertEqual(lineas[-1], "FAILED: jq")
        self.assertIn("jq: FAILED (hooks/hooks.json)", lineas)


def _ok(*_a, **_k):
    return subprocess.CompletedProcess([], 0, "", "")


def _rc(rc: int):
    return lambda *_a, **_k: subprocess.CompletedProcess([], rc, "", "err")


class MainEnProcesoTests(unittest.TestCase):
    """Una fila por decision de main()."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = _repo_minimo(tmp.name)
        cwd = mock.patch("pathlib.Path.cwd", return_value=self.repo)
        cwd.start()
        self.addCleanup(cwd.stop)

    def _main(self, unittest_salida, run):
        out = io.StringIO()
        with mock.patch.object(vp, "correr_unittest", return_value=unittest_salida) as cu, \
                mock.patch.object(vp.subprocess, "run", side_effect=run) as sr, \
                contextlib.redirect_stdout(out):
            codigo = vp.main()
        return codigo, out.getvalue().splitlines(), sr, cu

    OK = "Ran 3 tests in 0.1s\n\nOK\n"

    def test_los_tres_verdes_passed_exit_0(self):
        codigo, lineas, _, _ = self._main(self.OK, _ok)
        self.assertEqual((codigo, lineas[-1]), (0, "PASSED"))
        self.assertEqual(lineas[0], "unittest: PASSED (Ran 3 tests)")

    def test_bash_n_se_invoca_como_lista_por_archivo(self):
        _, _, sr, _ = self._main(self.OK, _ok)
        bash = [c.args[0] for c in sr.call_args_list if c.args[0][0] == "bash"]
        self.assertEqual(
            bash,
            [
                ["bash", "-n", str(self.repo / ".githooks" / "commit-msg")],
                ["bash", "-n", str(self.repo / "scripts" / "a.sh")],
            ],
        )

    def test_jq_se_invoca_sobre_la_lista_fija(self):
        _, _, sr, _ = self._main(self.OK, _ok)
        jq = [c.args[0] for c in sr.call_args_list if c.args[0][0] == "jq"]
        self.assertEqual(jq, [["jq", ".", str(self.repo / j)] for j in vp.JSON_FIJOS])

    def test_unittest_con_fallos_es_rojo_exit_1(self):
        codigo, lineas, _, _ = self._main("Ran 3 tests in 0.1s\n\nFAILED (failures=1)\n", _ok)
        self.assertEqual((codigo, lineas[-1]), (1, "FAILED: unittest"))

    def test_bash_distinto_de_cero_es_rojo_exit_1(self):
        def run(cmd, **_k):
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] == "bash" else 0, "", "")
        codigo, lineas, _, _ = self._main(self.OK, run)
        self.assertEqual((codigo, lineas[-1]), (1, "FAILED: bash -n"))

    def test_jq_distinto_de_cero_es_rojo_exit_1(self):
        def run(cmd, **_k):
            return subprocess.CompletedProcess(cmd, 5 if cmd[0] == "jq" else 0, "", "")
        codigo, lineas, _, _ = self._main(self.OK, run)
        self.assertEqual((codigo, lineas[-1]), (1, "FAILED: jq"))

    def test_varios_rojos_en_orden_separados_por_coma(self):
        codigo, lineas, _, _ = self._main("Ran 3 tests in 0.1s\n\nFAILED (errors=2)\n", _rc(1))
        self.assertEqual((codigo, lineas[-1]), (1, "FAILED: unittest, bash -n, jq"))

    def test_unittest_salida_no_parseable_es_no_se_exit_2(self):
        codigo, lineas, _, _ = self._main("basura", _ok)
        self.assertEqual(codigo, 2)
        self.assertEqual(lineas[-1], f"no sé: unittest: {vp.MOTIVOS['salida_no_parseable']}")

    def test_unittest_ran_0_es_no_se_sin_tests(self):
        codigo, lineas, _, _ = self._main("Ran 0 tests in 0.0s\n\nOK\n", _ok)
        self.assertEqual(codigo, 2)
        self.assertIn(vp.MOTIVOS["sin_tests"], lineas[-1])

    def test_unittest_timeout_es_no_se_timeout(self):
        out = io.StringIO()
        with mock.patch.object(vp.subprocess, "run", side_effect=[
                subprocess.TimeoutExpired("x", 1), *([_ok()] * 5)]), \
                contextlib.redirect_stdout(out):
            codigo = vp.main()
        # el timeout de unittest sale de correr_unittest real (subprocess.run parcheado)
        self.assertEqual(codigo, 2)
        self.assertEqual(out.getvalue().splitlines()[-1], f"no sé: unittest: {vp.MOTIVOS['timeout_unittest']}")

    def test_jq_ausente_es_no_se_exit_2_nunca_passed(self):
        def run(cmd, **_k):
            if cmd[0] == "jq":
                raise FileNotFoundError("jq")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        codigo, lineas, _, _ = self._main(self.OK, run)
        self.assertEqual(codigo, 2)
        self.assertEqual(lineas[-1], f"no sé: jq: {vp.MOTIVOS['jq_no_ejecutable']}")
        self.assertNotIn("PASSED", lineas[-1])

    def test_bash_sin_permiso_es_no_se_bash_no_ejecutable(self):
        def run(cmd, **_k):
            if cmd[0] == "bash":
                raise PermissionError("bash")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        codigo, lineas, _, _ = self._main(self.OK, run)
        self.assertEqual(codigo, 2)
        self.assertIn(vp.MOTIVOS["bash_no_ejecutable"], lineas[-1])

    def test_bash_o_jq_con_timeout_es_no_se_timeout(self):
        for herramienta in ("bash", "jq"):
            with self.subTest(herramienta=herramienta):
                def run(cmd, **_k):
                    if cmd[0] == herramienta:
                        raise subprocess.TimeoutExpired(cmd, 1)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                codigo, lineas, _, _ = self._main(self.OK, run)
                self.assertEqual(codigo, 2)
                self.assertIn(vp.MOTIVOS[f"timeout_{herramienta}"], lineas[-1])

    def test_cero_archivos_bash_es_no_se(self):
        shutil.rmtree(self.repo / "scripts")
        shutil.rmtree(self.repo / ".githooks")
        codigo, lineas, sr, _ = self._main(self.OK, _ok)
        self.assertEqual(codigo, 2)
        self.assertIn(vp.MOTIVOS["sin_archivos_bash"], lineas[-1])
        self.assertFalse([c for c in sr.call_args_list if c.args[0][0] == "bash"])

    def test_rojo_gana_sobre_no_se_pero_el_paso_sin_evidencia_se_imprime(self):
        def run(cmd, **_k):
            if cmd[0] == "jq":
                raise FileNotFoundError("jq")
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] == "bash" else 0, "", "")
        codigo, lineas, _, _ = self._main(self.OK, run)
        self.assertEqual((codigo, lineas[-1]), (1, "FAILED: bash -n"))
        self.assertIn(f"jq: no sé ({vp.MOTIVOS['jq_no_ejecutable']})", lineas)

    def test_siempre_imprime_los_tres_pasos_mas_la_final(self):
        _, lineas, _, _ = self._main("basura", _rc(1))
        self.assertEqual(len(lineas), 4)


if __name__ == "__main__":
    unittest.main()
