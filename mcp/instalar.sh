#!/usr/bin/env bash
# Instala pipeline_runner como servidor MCP local en la app de escritorio de Claude (Linux).
# Uso: bash mcp/instalar.sh   (después, copia allowlist.example.json a allowlist.json y edítalo)
set -euo pipefail
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
VENV="$DIR/.venv"
CONF="$HOME/.config/Claude/claude_desktop_config.json"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "mcp>=2.3,<3"
"$VENV/bin/python" -c "import ast;ast.parse(open('$DIR/pipeline_runner.py').read())"
"$VENV/bin/python" -c "from mcp.server.mcpserver import MCPServer"
mkdir -p "$(dirname "$CONF")"
[ -f "$CONF" ] && cp "$CONF" "$CONF.bak.$(date +%Y%m%d-%H%M%S)"
"$VENV/bin/python" - "$CONF" "$VENV/bin/python" "$DIR/pipeline_runner.py" <<'PY'
import json, os, sys
conf, py, script = sys.argv[1:4]
data = json.load(open(conf)) if os.path.exists(conf) and os.path.getsize(conf) else {}
data.setdefault("mcpServers", {})["pipeline_runner"] = {"command": py, "args": [script]}
json.dump(data, open(conf, "w"), indent=2, ensure_ascii=False)
print("añadido 'pipeline_runner' a", conf)
PY
mkdir -p "$DIR/logs"
echo "Hecho. Reinicia la app de Claude para cargarlo."
