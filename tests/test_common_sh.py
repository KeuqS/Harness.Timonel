"""#282: `scripts/_common.sh` (`load_timonel_config`, `resolver_config_consumidor`).

Replica en shell las tres respuestas de `timonel_gh.resolver_config_path` (#210): config local,
config del checkout principal via `--git-common-dir` llamado `.git`, y "no se" con el stderr de git.
Los repos de juguete se importan de `test_hooks_integracion` (no se copian)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_hooks_integracion as hi  # noqa: E402
COMMON = ROOT / "scripts/_common.sh"
NO_SE = "no se pudo resolver el checkout principal"


def _bash(script: str, cwd: Path, path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", "-c", f'source "{COMMON}" && {script}'],
        capture_output=True, text=True, cwd=cwd, env=env,
    )


def _git_falso(directorio: Path) -> tuple[str, Path]:
    """PATH con un `git` que escribe una marca y sale 99, conservando `jq`."""
    falso = directorio / "falso"
    falso.mkdir()
    marca = directorio / "git-llamado"
    (falso / "git").write_text(f'#!/bin/sh\ntouch "{marca}"\nexit 99\n', encoding="utf-8")
    (falso / "git").chmod(0o755)
    jq = shutil.which("jq")
    assert jq, "jq es requisito del test"
    (falso / "jq").symlink_to(jq)
    return f"{falso}:/usr/bin:/bin", marca


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()


class LoadTimonelConfigTests(_Base):
    def _principal_y_worktree(self) -> tuple[Path, Path]:
        principal = self.base / "principal"
        principal.mkdir()
        hi._preparar_repo(principal, "main", perfil="consumidor")
        wt = self.base / "wt"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "hu/282-x", str(wt)], cwd=principal, check=True)
        self.assertFalse((wt / ".claude").exists())
        return principal, wt

    def test_e1_worktree_sin_claude_imprime_repo_del_principal(self):
        principal, wt = self._principal_y_worktree()
        r = _bash('load_timonel_config && echo "$TIMONEL_REPO" && echo "$TIMONEL_CONFIG_PATH"', wt)
        self.assertEqual(r.returncode, 0, r.stderr)
        repo, ruta = r.stdout.split()
        self.assertEqual(repo, "o/r")
        self.assertEqual(Path(ruta).resolve(), (principal / ".claude/timonel.config.json").resolve())

    def test_e2_config_local_gana_sin_ejecutar_git(self):
        repo = self.base / "repo"
        repo.mkdir()
        hi._preparar_repo(repo, "main", perfil="consumidor")
        path, marca = _git_falso(self.base)
        r = _bash('load_timonel_config && echo "$TIMONEL_REPO"', repo, path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "o/r")
        self.assertFalse(marca.exists(), "git no debio ejecutarse")

    def test_e3_no_se_en_bare_submodulo_y_fuera_de_git(self):
        paridad = hi.ParidadCompuertaResolverTests()
        fuera = self.base / "fuera"
        fuera.mkdir()
        casos = {
            "bare": paridad._bare_desde_subdirectorio(self.base / "bare_base"),
            "submodulo": paridad._submodulo(self.base / "sm_base"),
            "fuera de git": fuera,
        }
        for nombre, cwd in casos.items():
            with self.subTest(caso=nombre):
                r = _bash("load_timonel_config", cwd)
                self.assertEqual(r.returncode, 1)
                self.assertIn(NO_SE, r.stderr)
                self.assertNotIn("no se encontro", r.stderr)
                f = _bash("resolver_config_consumidor", cwd)
                self.assertEqual(f.returncode, 2)
                self.assertEqual(f.stdout, "")
                self.assertIn(f"{NO_SE}: ", f.stderr)

    def test_fuera_de_git_incluye_el_stderr_de_git(self):
        fuera = self.base / "fuera"
        fuera.mkdir()
        r = _bash("load_timonel_config", fuera)
        self.assertIn("not a git repository", r.stderr)

    def test_repo_normal_sin_config_dice_no_se_encontro(self):
        repo = self.base / "sin"
        repo.mkdir()
        hi._preparar_repo(repo, "main", perfil="ninguno")
        r = _bash("load_timonel_config", repo)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no se encontro", r.stderr)
        self.assertNotIn(NO_SE, r.stderr)
        self.assertEqual(_bash("resolver_config_consumidor", repo).returncode, 1)

    def test_path_explicito_no_consulta_git(self):
        repo = self.base / "repo"
        repo.mkdir()
        hi._preparar_repo(repo, "main", perfil="ninguno")
        path, marca = _git_falso(self.base)
        r = _bash("load_timonel_config otro/config.json", repo, path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no se encontro otro/config.json", r.stderr)
        self.assertFalse(marca.exists())

    def test_perfil_plugin_sin_config_no_consulta_git(self):
        repo = self.base / "plugin"
        repo.mkdir()
        hi._preparar_repo(repo, "main", perfil="plugin")
        path, marca = _git_falso(self.base)
        gh = Path(path.split(":")[0]) / "gh"
        gh.write_text("#!/bin/sh\necho o/timonel\n", encoding="utf-8")
        gh.chmod(0o755)
        r = _bash('load_timonel_config; echo "$TIMONEL_CONFIG_PATH|$TIMONEL_MODULOS"', repo, path)
        self.assertIn("(plugin)|plugin", r.stdout)
        self.assertFalse(marca.exists(), "la rama plugin va antes que git")


# Variables que bash crea o reescribe por su cuenta entre dos comandos (no por `source`).
DINAMICAS_DE_BASH = frozenset({"_", "PIPESTATUS", "BASH_REMATCH", "COLUMNS", "LINES"})


class ResolverSinEfectosTests(_Base):
    def test_e4_sourcear_no_exporta_ni_imprime(self):
        # Se comparan nombres (`compgen -v`), no `declare -p`: los valores de BASH_ARGC,
        # SHLVL y similares cambian segun la version de bash (fallo del CI de #315), y
        # bash 3.2 imprime los globales como `YY=1` sin prefijo `declare` (WARNING 1 del
        # review de #282). Un global nuevo agrega un nombre; un export, una linea de env.
        script = (
            f'compgen -v > "{self.base}/antes.txt"; env > "{self.base}/env_antes.txt"; '
            f'source "{COMMON}"; '
            f'compgen -v > "{self.base}/despues.txt"; env > "{self.base}/env_despues.txt"; '
            "declare -F resolver_config_consumidor >/dev/null"
        )
        r = subprocess.run(["env", "-i", "bash", "-c", script], capture_output=True, text=True, cwd=self.base)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

        def leer(nombre: str) -> list[str]:
            lineas = (self.base / nombre).read_text(encoding="utf-8").splitlines()
            return sorted(linea for linea in lineas if linea not in DINAMICAS_DE_BASH and not linea.startswith("_="))

        self.assertEqual(leer("antes.txt"), leer("despues.txt"))
        self.assertEqual(leer("env_antes.txt"), leer("env_despues.txt"))

    def test_local_existente_imprime_la_ruta_local_con_rc_0(self):
        repo = self.base / "repo"
        repo.mkdir()
        hi._preparar_repo(repo, "main", perfil="consumidor")
        r = _bash("resolver_config_consumidor", repo)
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, ".claude/timonel.config.json", ""))


if __name__ == "__main__":
    unittest.main()
