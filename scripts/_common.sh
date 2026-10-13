#!/usr/bin/env bash
# _common.sh --- Funciones compartidas por los scripts de Timonel.
#
# Uso: source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
# No invocar directamente (prefijo _ = sourceable).

# resolve_plugin_root
#
# Exporta TIMONEL_PLUGIN_ROOT. Orden: CLAUDE_PLUGIN_ROOT (si Claude Code lo
# inyecta) -> .timonel/.plugin-root (escrito por el hook SessionStart) ->
# glob sobre el cache de marketplaces tomando la version mas reciente ->
# la carpeta padre de este script (cuando se corre desde el repo del plugin).
resolve_plugin_root() {
    local candidate="${CLAUDE_PLUGIN_ROOT:-}"
    if [ -z "$candidate" ] && [ -f .timonel/.plugin-root ]; then
        candidate="$(cat .timonel/.plugin-root)"
    fi
    if [ -z "$candidate" ]; then
        candidate="$(ls -d "$HOME"/.claude/plugins/cache/*/timonel/*/ 2>/dev/null | sort -V | tail -1)"
    fi
    if [ -z "$candidate" ]; then
        candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
    fi
    export TIMONEL_PLUGIN_ROOT="${candidate%/}"
}

# load_timonel_config [config_path]
#
# Carga .claude/timonel.config.json del consumidor y exporta TIMONEL_*.
# Retorna 1 con mensaje claro si falta el archivo, jq o github.repo. Sin argumento
# y sin config local, lo busca en el checkout principal (resolver_config_consumidor, #282).
load_timonel_config() {
    local config="${1:-.claude/timonel.config.json}"
    local explicito="${1:+si}"

    if ! command -v jq >/dev/null 2>&1; then
        echo "ERROR: jq no esta instalado. Requerido para leer $config" >&2
        return 1
    fi
    if [ ! -f "$config" ] && [ -f .claude-plugin/plugin.json ]; then
        # Dentro del repo del plugin (TIM-ADR-0005): el backlog del plugin vive en su propio repo.
        export TIMONEL_CONFIG_PATH="(plugin)"
        export TIMONEL_PROJECT_NAME="timonel"
        export TIMONEL_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)"
        export TIMONEL_MODULOS="plugin"
        export TIMONEL_API_PROJECT="" TIMONEL_API_PATH="" TIMONEL_API_MODULE_FILE="" TIMONEL_FRONTEND_PROJECTS="" TIMONEL_MODELOS_ALIAS="" TIMONEL_MODELOS_PATH="" TIMONEL_HEURISTICS_DIR=""
        [ -n "$TIMONEL_REPO" ] && return 0
    fi
    if [ ! -f "$config" ] && [ -z "$explicito" ]; then
        local resuelto rc=0
        resuelto="$(resolver_config_consumidor "$config" 2>&1)" || rc=$?
        case "$rc" in
            0) config="$resuelto" ;;
            2)
                echo "ERROR: $resuelto" >&2
                return 1
                ;;
        esac
    fi
    if [ ! -f "$config" ]; then
        echo "ERROR: no se encontro $config" >&2
        echo "  Ejecuta /timonel:onboard en la raiz del repo consumidor para generarlo." >&2
        return 1
    fi

    export TIMONEL_CONFIG_PATH="$config"
    export TIMONEL_PROJECT_NAME="$(jq -r '.projectName // ""' "$config")"
    export TIMONEL_REPO="$(jq -r '.github.repo // ""' "$config")"
    export TIMONEL_API_PROJECT="$(jq -r '.api.project // ""' "$config")"
    export TIMONEL_API_PATH="$(jq -r '.api.path // ""' "$config")"
    export TIMONEL_API_MODULE_FILE="$(jq -r '.api.moduleFile // ""' "$config")"
    export TIMONEL_FRONTEND_PROJECTS="$(jq -r '[.frontends[]?.project] | join(" ")' "$config")"
    export TIMONEL_MODELOS_ALIAS="$(jq -r '.modelos.alias // ""' "$config")"
    export TIMONEL_MODELOS_PATH="$(jq -r '.modelos.path // ""' "$config")"
    export TIMONEL_MODULOS="$(jq -r '[.modulos[]?] | join(" ")' "$config")"
    export TIMONEL_HEURISTICS_DIR="$(jq -r '.heuristicsDir // ""' "$config")"

    if [ -z "$TIMONEL_REPO" ] || [ "$TIMONEL_REPO" = "null" ]; then
        echo "ERROR: $config no declara github.repo (owner/repo donde viven los issues)" >&2
        return 1
    fi
    case "$TIMONEL_REPO" in
        */*) ;;
        *) echo "ERROR: github.repo debe tener la forma owner/repo (recibido: $TIMONEL_REPO)" >&2; return 1 ;;
    esac
}

# resolver_config_consumidor [local]
#
# Donde vive el config del consumidor, con tres respuestas (#282, espejo de
# timonel_gh.resolver_config_path): imprime la ruta por stdout.
#   rc 0  si: el config local existe (sin ejecutar git) o el del checkout principal.
#   rc 1  no: checkout resuelto, pero sin config. Silencio.
#   rc 2  no se: git rev-parse falla, o el common-dir no se llama `.git` (bare,
#         submodulo). stderr: `no se pudo resolver el checkout principal: <motivo>`.
# Sin efectos: solo variables locales. `--path-format=absolute` evita que git
# devuelva `.git` en la raiz del checkout y `../.git` en un subdirectorio.
# Sin timeout: a diferencia de timonel_gh._git_common_dir (5 s), bash no tiene
# uno portable (macOS no trae `timeout`); `git rev-parse` es local y no bloquea.
resolver_config_consumidor() {
    local local_cfg="${1:-.claude/timonel.config.json}"
    local common principal

    if [ -f "$local_cfg" ]; then
        echo "$local_cfg"
        return 0
    fi
    if ! common="$(git rev-parse --path-format=absolute --git-common-dir 2>&1)"; then
        echo "no se pudo resolver el checkout principal: $common" >&2
        return 2
    fi
    case "$common" in
        */.git) ;;
        *)
            echo "no se pudo resolver el checkout principal: el common-dir $common no es un .git de checkout (bare o submodulo)" >&2
            return 2
            ;;
    esac
    principal="${common%/.git}/$local_cfg"
    if [ -f "$principal" ]; then
        echo "$principal"
        return 0
    fi
    return 1
}

# require_gh
#
# Verifica gh autenticado. Retorna 1 con instrucciones si no.
require_gh() {
    if ! command -v gh >/dev/null 2>&1; then
        echo "ERROR: gh (GitHub CLI) no esta instalado. https://cli.github.com" >&2
        return 1
    fi
    if ! gh auth status >/dev/null 2>&1; then
        echo "ERROR: gh no esta autenticado. Ejecuta: gh auth login" >&2
        return 1
    fi
}
