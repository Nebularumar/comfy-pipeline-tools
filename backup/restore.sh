#!/usr/bin/env bash
# ============================================================================
#  restore.sh — reconstruye el entorno EXACTO del pipeline desde el backup externo,
#  SIN internet: código de ComfyUI y nodos desde git bundles, Python desde los wheels
#  guardados, modelos desde <backup>/modelos (verificados contra config.json).
#
#  Uso:
#    bash backup/restore.sh --comprobar            # solo mira que el backup tiene todo (no instala)
#    bash backup/restore.sh DESTINO                # reconstruye en DESTINO (p. ej. /mnt/disco/ComfyUI_restaurado)
#  Tras restaurar: arrancar ComfyUI con DESTINO/venv/bin/python main.py y apuntar
#  config.json comfyui.dir a DESTINO.
# ============================================================================
set -uo pipefail
R="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"   # raíz del repo
BK="${PIPELINE_BACKUP_DIR:?define PIPELINE_BACKUP_DIR con la carpeta del backup (montaje + backup.destino)}"
[ $# -ge 1 ] || { sed -n '3,13p' "$0"; exit 1; }

falta=0
chk() { if [ -e "$1" ]; then echo "  ✅ $2"; else echo "  ❌ falta $2 ($1)"; falta=$((falta+1)); fi; }
echo "Backup: $BK"
chk "$BK/wheels/requirements.lock" "wheels + requirements.lock"
chk "$BK/repos/ComfyUI.bundle" "git bundle de ComfyUI"
while read -r repo commit _ uso; do
  [[ "$repo" == \#* || "$repo" == ComfyUI ]] && continue
  [ "$uso" = "usado" ] && chk "$BK/repos/$(basename "$repo").bundle" "git bundle de $(basename "$repo")"
done < "$R/locks/nodes.lock"
python3 - "${PIPELINE_CONFIG:-$R/backup/config.json}" "$BK/modelos" <<'EOF' || falta=$((falta+1))
import json, sys
from pathlib import Path
c = json.load(open(sys.argv[1])); falta = [k for k, m in c["modelos"].items() if not (Path(sys.argv[2]) / m["ruta"]).exists()]
print("  ✅ modelos de config.json" if not falta else "  ❌ faltan modelos: " + ", ".join(falta))
sys.exit(1 if falta else 0)
EOF
n_whl=$(ls "$BK/wheels" 2>/dev/null | grep -c '\.whl$\|\.tar\.gz$')
n_req=$(grep -vc '^#' "$R/locks/requirements.lock"); n_fallo=$(grep -c . "$BK/wheels/FALLOS.txt" 2>/dev/null || echo 0)
echo "  wheels: $n_whl ficheros para $n_req paquetes ($n_fallo sin wheel: paquetes de apt, ver FALLOS.txt)"
[ "$1" = "--comprobar" ] && { [ $falta -eq 0 ] && echo "✅ el backup tiene todo lo necesario" || echo "❌ faltan $falta piezas"; exit $falta; }
[ $falta -eq 0 ] || { echo "❌ el backup está incompleto: no restauro."; exit 1; }

DEST="$1"; [ -e "$DEST" ] && { echo "❌ $DEST ya existe: elige otra ruta (nunca se pisa nada)."; exit 1; }
set -e
echo ">>> código de ComfyUI y nodos desde los bundles"
while read -r repo commit _ uso; do
  [[ "$repo" == \#* ]] && continue
  if [ "$repo" = ComfyUI ]; then dst="$DEST"; b="$BK/repos/ComfyUI.bundle"
  elif [ "$uso" = "usado" ]; then dst="$DEST/$repo"; b="$BK/repos/$(basename "$repo").bundle"
  else continue; fi
  git clone -q "$b" "$dst" && git -C "$dst" checkout -q "$commit" && echo "  $repo @ ${commit:0:12}"
done < "$R/locks/nodes.lock"
echo ">>> venv con los wheels guardados (sin internet)"
python3.10 -m venv "$DEST/venv"
grep -v '^#' "$R/locks/requirements.lock" | grep -vxF -f "$BK/wheels/FALLOS.txt" > "$DEST/requirements.restaurar"
"$DEST/venv/bin/pip" install -q --no-index --find-links "$BK/wheels" -r "$DEST/requirements.restaurar"
echo ">>> modelos (copia desde el backup; se verifican los SHA256)"
python3 - "${PIPELINE_CONFIG:-$R/backup/config.json}" "$BK/modelos" "$DEST/models" <<'EOF'
import json, sys, shutil, hashlib
from pathlib import Path
c = json.load(open(sys.argv[1])); src, dst = Path(sys.argv[2]), Path(sys.argv[3])
for k, m in c["modelos"].items():
    d = dst / m["ruta"]; d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src / m["ruta"], d)
    h = hashlib.sha256()
    with open(d, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""): h.update(b)
    assert h.hexdigest() == m["sha256"], f"SHA256 distinto en {k}"
    print("  OK", k)
EOF
echo "✅ restaurado en $DEST. Paquetes de apt no incluidos: $BK/wheels/FALLOS.txt"
echo "   Siguiente: config.json -> comfyui.dir = $DEST"
