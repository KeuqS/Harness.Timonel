"""Utilidades compartidas de Timonel para leer y escribir GitHub Issues via `gh`.

Stdlib puro. Los scripts hermanos (retro_query, review_query, *_distill,
migrate_backlog) importan de aqui: carga de config, wrapper de `gh`, parseo
de comentarios con marcador (TIM-ADR-0001) y modelos Retro/Review.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(".claude/timonel.config.json")


def numero_issue(valor: str) -> int:
    """`type=` de argparse para el posicional `issue`: acepta `223` y `#223` (#231)."""
    texto = valor.strip()
    numero = texto[1:] if texto.startswith("#") else texto
    if not numero.isdigit():
        raise argparse.ArgumentTypeError(f"numero de issue invalido: {valor!r} (usa 223 o #223)")
    return int(numero)


MARCADOR_RETRO = "retro"
MARCADOR_REVIEW = "review"
MARCADOR_DOD = "dod"
MARCADOR_INVESTIGACION = "investigacion"
MARCADOR_HARNESS_AUDIT = "harness-audit"

VEREDICTOS_VALIDOS = {"APROBADO", "APROBADO CON OBSERVACIONES", "REQUIERE CAMBIOS"}
SEVERIDADES_VALIDAS = {"CRITICO", "WARNING"}
TIPOS_NORMALIZADOS = {"Convención": "Convencion", "Heurística": "Heuristica"}


# ---------------------------------------------------------------------------
# Config y gh
# ---------------------------------------------------------------------------


PLUGIN_MANIFEST = Path(".claude-plugin/plugin.json")


GIT_TIMEOUT_SEGUNDOS = 5


def _git_common_dir() -> tuple[Path | None, str]:
    """`git rev-parse --git-common-dir` absoluto. (None, motivo) si git no responde."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT_SEGUNDOS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git no respondio: {exc}"
    if proc.returncode != 0:
        return None, f"git rev-parse fallo (rc={proc.returncode}): {proc.stderr.strip()}"
    return Path(proc.stdout.strip()), ""


def resolver_config_path(local: Path = CONFIG_PATH) -> tuple[Path | None, str]:
    """Donde vive el config del consumidor, con tres respuestas (#210): (ruta, "") si lo
    sabe, (None, motivo) si no puede saberlo. Un worktree no hereda `.claude/` (esta sin
    versionar): si el config local no existe se busca en el checkout principal, el padre
    del common-dir `.git`. Bare y submodulo (common-dir que no se llama `.git`) son "no se"."""
    if local.exists():
        return local, ""
    common, motivo = _git_common_dir()
    if common is None:
        return None, motivo
    if common.name != ".git":
        return None, f"el common-dir {common} no es un `.git` de checkout (bare o submodulo)"
    return common.parent / local, ""


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Config del consumidor. Dentro del repo del plugin (TIM-ADR-0005) devuelve un
    config minimo con github.repo = repo actual, para que los comandos funcionen ahi.
    Sin `path` explicito y sin config local, lo busca en el checkout principal (#210)."""
    if not path.exists() and PLUGIN_MANIFEST.exists():
        repo = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()
        return {"projectName": "timonel", "github": {"repo": repo}, "modulos": ["plugin"]}
    destino = path
    if not path.exists() and path == CONFIG_PATH:
        destino, motivo = resolver_config_path(path)
        if destino is None:
            raise SystemExit(
                f"ERROR: no se pudo resolver el checkout principal para buscar {path}: {motivo}"
            )
    if destino.exists():
        return json.loads(destino.read_text(encoding="utf-8"))
    raise SystemExit(
        f"ERROR: no se encontro {destino}. Ejecuta /timonel:onboard en la raiz del consumidor."
    )


def repo_from_config(config: dict | None = None) -> str:
    cfg = config or load_config()
    repo = (cfg.get("github") or {}).get("repo") or ""
    if "/" not in repo:
        raise SystemExit("ERROR: github.repo debe ser owner/repo en .claude/timonel.config.json")
    return repo


def gh(*args: str, stdin: str | None = None) -> str:
    """Ejecuta `gh` y devuelve stdout. Lanza SystemExit con el stderr si falla."""
    proc = subprocess.run(
        ["gh", *args], input=stdin, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(f"ERROR gh {' '.join(args[:3])}: {proc.stderr.strip()}")
    return proc.stdout


def gh_json(*args: str) -> object:
    out = gh(*args)
    return json.loads(out) if out.strip() else []


def fetch_issues(repo: str, labels: list[str], state: str = "all", limit: int = 500) -> list[dict]:
    """Issues con body, labels y comentarios. `labels` se aplican en AND."""
    args = ["issue", "list", "-R", repo, "--state", state, "--limit", str(limit),
            "--json", "number,title,state,body,labels,comments,closedAt"]
    for label in labels:
        args += ["--label", label]
    issues = gh_json(*args)
    for issue in issues:
        issue["label_names"] = [l["name"] for l in issue.get("labels", [])]
    return issues


def epic_children(repo: str, epic: int) -> set[int]:
    owner, name = repo.split("/", 1)
    query = (
        "query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ issue(number:$n){"
        " subIssues(first:100){ nodes{ number } } } } }"
    )
    data = gh_json("api", "graphql", "-f", f"query={query}", "-f", f"o={owner}",
                   "-f", f"r={name}", "-F", f"n={epic}")
    nodes = (((data.get("data") or {}).get("repository") or {}).get("issue") or {}).get("subIssues", {}).get("nodes", [])
    return {n["number"] for n in nodes}


def upsert_issue_by_title(repo: str, title: str, body: str, labels: list[str]) -> int:
    """Crea o reemplaza el body de un issue abierto con ese titulo exacto. Devuelve el numero."""
    existing = gh_json("issue", "list", "-R", repo, "--state", "open", "--limit", "50",
                       "--search", f'"{title}" in:title', "--json", "number,title")
    match = next((i for i in existing if i["title"] == title), None)
    if match:
        gh("issue", "edit", str(match["number"]), "-R", repo, "--body", body)
        return match["number"]
    out = gh("issue", "create", "-R", repo, "--title", title, "--body", body,
             *sum((["--label", l] for l in labels), []))
    return int(out.strip().rsplit("/", 1)[-1])


# ---------------------------------------------------------------------------
# Parseo de comentarios con marcador
# ---------------------------------------------------------------------------


def marker_prefix(marker: str) -> str:
    return f"<!-- timonel:{marker} -->"


def find_marker_comment(comments: list[dict], marker: str) -> str | None:
    """Ultimo comentario cuyo body empieza con el marcador."""
    prefix = marker_prefix(marker)
    hits = [c["body"] for c in comments if (c.get("body") or "").lstrip().startswith(prefix)]
    return hits[-1] if hits else None


def parse_yaml_plano(text: str) -> dict[str, str]:
    """Primer bloque ```yaml con pares clave: valor planos."""
    match = re.search(r"```yaml\s*\n(.*?)\n```", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


def parse_secciones(text: str, nivel: str = "###") -> dict[str, list[str]]:
    """Secciones `### Nombre` con sus bullets (omite `- Ninguno`)."""
    sections: dict[str, list[str]] = {}
    current = ""
    heading = re.compile(rf"^{re.escape(nivel)}\s+(.+)")
    for line in text.splitlines():
        m = heading.match(line)
        if m:
            current = m.group(1).strip()
            sections[current] = []
            continue
        if current and line.strip().startswith("- "):
            bullet = line.strip()[2:].strip()
            if bullet and bullet.lower() != "ninguno":
                sections[current].append(bullet)
    return sections


def _split_row(line: str) -> list[str]:
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")]


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(re.match(r"^:?-+:?$", c) for c in cells if c)


def parse_tabla(text: str, seccion: str) -> list[list[str]]:
    """Filas (sin header ni separador) de la primera tabla bajo `### <seccion>`."""
    rows: list[list[str]] = []
    in_section = header = sep = False
    for line in text.splitlines():
        if re.match(r"^#{2,3}\s+", line):
            if in_section and (header or rows):
                break
            in_section = line.split(None, 1)[1].strip().lower() == seccion.lower()
            continue
        if not in_section or not line.strip().startswith("|"):
            if in_section and header and sep and line.strip() == "":
                break
            continue
        if not header:
            header = True
            continue
        if not sep:
            sep = _is_separator(line)
            continue
        rows.append(_split_row(line))
    return rows


def _safe_int(value: str | None) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def label_value(labels: list[str], prefix: str) -> str:
    return next((l.split(":", 1)[1] for l in labels if l.startswith(prefix + ":")), "")


ORIGEN_RETRO_RE = re.compile(r"Origen: retro #(\d+)\b")


def origen_retro(body: str) -> int | None:
    """Numero de HU origen si `body` es un issue derivado de una retro
    (`cosechar_retro.py` lo marca con `Origen: retro #N (k) — <repo>`)."""
    match = ORIGEN_RETRO_RE.search(body)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


@dataclass
class Retro:
    issue: int
    titulo: str
    meta: dict[str, str]
    sections: dict[str, list[str]]
    labels: list[str]

    @property
    def modulo(self) -> str:
        return self.meta.get("modulo") or label_value(self.labels, "mod")

    @property
    def alcance(self) -> str:
        return self.meta.get("alcance") or label_value(self.labels, "alcance")

    @property
    def estimado_sp(self) -> int:
        return _safe_int(self.meta.get("estimado_sp") or label_value(self.labels, "sp"))

    @property
    def real_sp(self) -> int:
        return _safe_int(self.meta.get("real_sp"))

    @property
    def precision(self) -> str:
        return self.meta.get("precision_estimacion") or label_value(self.labels, "retro")


@dataclass
class Hallazgo:
    indice: int
    tipo: str
    severidad: str
    archivo_linea: str
    descripcion: str
    sugerencia: str

    @property
    def archivo(self) -> str:
        return self.archivo_linea.split(":", 1)[0].strip()

    @property
    def tipo_normalizado(self) -> str:
        return TIPOS_NORMALIZADOS.get(self.tipo, self.tipo)


@dataclass
class Review:
    issue: int
    titulo: str
    meta: dict[str, str]
    resumen: str
    hallazgos: list[Hallazgo]
    labels: list[str] = field(default_factory=list)

    @property
    def veredicto(self) -> str:
        return self.meta.get("veredicto", "").upper()

    @property
    def modulo(self) -> str:
        return self.meta.get("modulo") or label_value(self.labels, "mod")

    @property
    def alcance(self) -> str:
        return self.meta.get("alcance") or label_value(self.labels, "alcance")

    @property
    def criticos(self) -> int:
        return _safe_int(self.meta.get("criticos"))

    @property
    def warnings(self) -> int:
        return _safe_int(self.meta.get("warnings"))

    @property
    def bloquea_dod(self) -> bool:
        return self.meta.get("bloquea_dod", "").lower() == "si"


def retro_from_comment(issue: dict, body: str) -> Retro:
    return Retro(
        issue=issue["number"],
        titulo=issue["title"],
        meta=parse_yaml_plano(body),
        sections=parse_secciones(body),
        labels=issue.get("label_names", []),
    )


def review_from_comment(issue: dict, body: str) -> Review:
    rows = parse_tabla(body, "Hallazgos")
    hallazgos = [
        Hallazgo(_safe_int(r[0]), r[1], r[2].upper(), r[3], r[4], r[5])
        for r in rows if len(r) >= 6
    ]
    resumen_match = re.search(r"^### Resumen\s*\n(.*?)(?=^###\s|\Z)", body, re.DOTALL | re.MULTILINE)
    return Review(
        issue=issue["number"],
        titulo=issue["title"],
        meta=parse_yaml_plano(body),
        resumen=(resumen_match.group(1).strip() if resumen_match else ""),
        hallazgos=hallazgos,
        labels=issue.get("label_names", []),
    )


def load_retros(repo: str, labels: list[str] | None = None) -> list[Retro]:
    retros: list[Retro] = []
    for issue in fetch_issues(repo, labels or ["tipo:hu"]):
        body = find_marker_comment(issue.get("comments", []), MARCADOR_RETRO)
        if body:
            retros.append(retro_from_comment(issue, body))
    return retros


def load_reviews(repo: str, labels: list[str] | None = None) -> list[Review]:
    reviews: list[Review] = []
    for issue in fetch_issues(repo, labels or ["tipo:hu"]):
        body = find_marker_comment(issue.get("comments", []), MARCADOR_REVIEW)
        if body:
            reviews.append(review_from_comment(issue, body))
    return reviews


def normalizar_descripcion(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.strip().lower())
