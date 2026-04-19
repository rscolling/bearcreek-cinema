#!/usr/bin/env bash
# bootstrap-dev.sh - Set up a dev environment for archive-agent
#
# Idempotent: re-running is safe.
# Target: Ubuntu (don-quixote) or WSL/macOS (blueridge dev).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> Bootstrapping archive-agent dev environment"
echo "    Repo: $REPO_ROOT"

# --- 1. Check Python version ---
if ! command -v python3 >/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
    echo "ERROR: Python 3.11+ required, found $PY_VERSION"
    exit 1
fi

echo "==> Python $PY_VERSION detected"

# --- 2. Virtual environment ---
cd "$REPO_ROOT"
if [[ ! -d .venv ]]; then
    echo "==> Creating venv at .venv/"
    python3 -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate
python -m pip install --upgrade pip

# --- 3. Install project ---
echo "==> Installing archive-agent in editable mode with dev extras"
pip install -e ".[dev]"

# --- 4. .env bootstrap ---
if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        echo "==> Created .env from .env.example"
        echo "    Edit .env and fill in API keys before running the agent"
    else
        echo "WARN: .env.example not found; create .env manually"
    fi
else
    echo "==> .env already present"
fi

# --- 5. Dev state dir ---
DEV_STATE_DIR="/tmp/archive-agent-dev"
mkdir -p "$DEV_STATE_DIR"
echo "==> Dev state dir: $DEV_STATE_DIR"

# --- 6. Dev config ---
if [[ ! -f config.toml ]]; then
    if [[ -f config.example.toml ]]; then
        cp config.example.toml config.toml
        echo "==> Created config.toml from example"
        echo "    Edit paths and URLs for your environment"
    fi
fi

# --- 7. pre-commit ---
if command -v pre-commit >/dev/null; then
    echo "==> Installing pre-commit hooks"
    pre-commit install
else
    echo "WARN: pre-commit not on PATH after install; hooks not installed"
fi

# --- 8. Ollama availability check (non-fatal) ---
echo "==> Checking Ollama"
if command -v ollama >/dev/null; then
    if curl -sf http://localhost:11434/api/tags >/dev/null; then
        MODELS=$(curl -s http://localhost:11434/api/tags | python3 -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(m["name"] for m in d.get("models", [])))')
        echo "    Ollama reachable. Models: $MODELS"
        if ! echo "$MODELS" | grep -q "qwen2.5:7b"; then
            echo "    NOTE: qwen2.5:7b not pulled yet. Run: ollama pull qwen2.5:7b"
        fi
    else
        echo "    Ollama installed but not reachable at localhost:11434"
        echo "    Start with: systemctl --user start ollama"
    fi
else
    echo "    Ollama not installed. To install on Ubuntu:"
    echo "      curl -fsSL https://ollama.com/install.sh | sh"
    echo "      ollama pull qwen2.5:7b"
fi

# --- 9. Smoke test ---
echo ""
echo "==> Bootstrap complete. Next steps:"
echo "    1. Edit .env to set JELLYFIN_API_KEY, JELLYFIN_USER_ID, TMDB_API_KEY"
echo "    2. Edit config.toml to point at your Jellyfin and Ollama"
echo "    3. Run: archive-agent config validate"
echo "    4. Run: archive-agent state init"
echo "    5. Run: archive-agent health all"
echo ""
echo "    If all health checks pass, you're ready for phase1-04 and beyond."
