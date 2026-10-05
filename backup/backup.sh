#!/usr/bin/env bash
# ============================================================================
#  backup.sh — copia de seguridad del pipeline ComfyUI a un disco externo/secundario
#
#  Destino: <montaje>/<destino>/ (UUID y rutas en config.json -> "backup")
#    modelos/       modelos de config.json, sin duplicar (rsync) + SHA256 verificado en destino
#    datasets/      datasets completos de cada LoRA
#    entrenamiento/ salidas de ai-toolkit (checkpoints intermedios incluidos) + configs
#    referencia/    carpeta de referencia congelada (p. ej. imágenes de control)
#    publicadas/    resultados finales (patrones en config.json; ruta relativa a ComfyUI/output)
#    video/         clips validados
#    snapshots/AAAA-MM-DD_HHMMSS/  repo completo (scripts, config, locks,
#                   documentación). Hard links contra el anterior: lo que no cambia no ocupa.
#    repos/         git bundle de ComfyUI y de los nodos usados (para restore.sh sin internet)
#    wheels/        wheels exactos de requirements.lock (wheels.sh)
#    ultimo_backup.log
#
#  ABORTA si el disco no está REALMENTE montado (nunca escribe en el punto de montaje
#  vacío, que llenaría el disco del sistema).
#  Uso: bash backup/backup.sh [--sin-wheels]
# ============================================================================
set -uo pipefail
R="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"   # raíz del repo
CANON="${PIPELINE_CONFIG:-$R/backup/config.json}"
j() { python3 -c "import json,sys;d=json.load(open('$CANON'))
for k in sys.argv[1].split('.'): d=d[k]
print('\n'.join(d) if isinstance(d,list) else d)" "$1"; }

UUID=$(j backup.uuid); MNT=$(j backup.montaje); DEST="$MNT/$(j backup.destino)"
TS=$(date +%Y-%m-%d_%H%M%S)
TMPLOG=$(mktemp)
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$TMPLOG"; }

# --- 1. el disco tiene que estar montado DE VERDAD, y ser el del UUID ---
real=$(findmnt -n -o TARGET --source "UUID=$UUID" 2>/dev/null | head -1)
[ "$real" = "$MNT" ] || { echo "❌ El disco UUID=$UUID no está montado en $MNT (findmnt: '${real:-nada}'). ABORTO sin copiar."; exit 1; }
mountpoint -q "$MNT" || { echo "❌ $MNT no es un punto de montaje. ABORTO."; exit 1; }
[ "$(findmnt -n -o SOURCE --target "$MNT")" = "$(findmnt -n -o SOURCE --target /)" ] && { echo "❌ $MNT está en el mismo disco que /. ABORTO."; exit 1; }
mkdir -p "$DEST"/{modelos,datasets,entrenamiento,publicadas,video,snapshots,wheels}
log "=== backup $TS -> $DEST (UUID $UUID) ==="
log "espacio libre en destino: $(df -h --output=avail "$MNT" | tail -1 | tr -d ' ')"

RS=(rsync -a --human-readable --info=stats1)
errores=0

# --- 2. modelos de config.json (sin duplicar) ---
COMFY=$(j comfyui.dir)
while read -r clave ruta; do
  mkdir -p "$DEST/modelos/$(dirname "$ruta")"
  "${RS[@]}" "$COMFY/models/$ruta" "$DEST/modelos/$ruta" >>"$TMPLOG" 2>&1 || { log "❌ rsync $clave"; errores=$((errores+1)); }
done < <(python3 -c "import json;[print(k,m['ruta']) for k,m in json.load(open('$CANON'))['modelos'].items()]")
log "modelos copiados; verificando SHA256 en destino..."
python3 - "$CANON" "$DEST/modelos" >>"$TMPLOG" 2>&1 <<'EOF' || errores=$((errores+1))
import json, sys, hashlib
from pathlib import Path
c = json.load(open(sys.argv[1])); base = Path(sys.argv[2]); mal = 0
for k, m in c["modelos"].items():
    p = base / m["ruta"]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""): h.update(b)
    ok = h.hexdigest() == m["sha256"]; mal += not ok
    print(("   OK  " if ok else "   MAL ") + k)
print(f"verificación modelos: {len(c['modelos']) - mal} OK, {mal} MAL")
sys.exit(1 if mal else 0)
EOF
grep -q ", 0 MAL" "$TMPLOG" || log "⚠️  hay modelos que NO cuadran en destino (ver log)"

# --- 3. datasets y entrenamiento ---
while read -r d; do "${RS[@]}" "$d" "$DEST/datasets/" >>"$TMPLOG" 2>&1 || errores=$((errores+1)); done < <(j backup.datasets)
while read -r d; do
  sub="$DEST/entrenamiento/$(basename "$(dirname "$d")")"; mkdir -p "$sub"
  "${RS[@]}" "$d" "$sub/" >>"$TMPLOG" 2>&1 || errores=$((errores+1))
done < <(j backup.entrenamiento)
log "datasets y entrenamiento OK"

# --- 4. referencia, publicadas y vídeo ---
"${RS[@]}" "$(j backup.referencia)/" "$DEST/referencia/" >>"$TMPLOG" 2>&1 || errores=$((errores+1))
OUT="$COMFY/output"
while read -r patron; do
  for f in $(compgen -G "$patron" || true); do
    [[ "$patron" == *"/**/"* ]] && continue
    rel="${f#$OUT/}"; mkdir -p "$DEST/publicadas/$(dirname "$rel")"
    "${RS[@]}" "$f" "$DEST/publicadas/$rel" >>"$TMPLOG" 2>&1 || errores=$((errores+1))
  done
done < <(j backup.publicadas)
while read -r f; do "${RS[@]}" "$f" "$DEST/video/" >>"$TMPLOG" 2>&1 || errores=$((errores+1)); done < <(j backup.video_validado)
log "referencia, publicadas y vídeo OK"

# --- 4b. código de ComfyUI y de los nodos usados, como git bundle (restore.sh sin internet) ---
mkdir -p "$DEST/repos"
while read -r repo commit _ uso; do
  [[ "$repo" == \#* ]] && continue
  [ "$repo" = ComfyUI ] || [ "$uso" = "usado" ] || continue
  d="$COMFY"; [ "$repo" = ComfyUI ] || d="$COMFY/$repo"
  git -C "$d" bundle create "$DEST/repos/$(basename "$repo").bundle.tmp" --all >>"$TMPLOG" 2>&1 \
    && mv "$DEST/repos/$(basename "$repo").bundle.tmp" "$DEST/repos/$(basename "$repo").bundle" \
    || { log "❌ bundle $repo"; errores=$((errores+1)); }
done < "$R/locks/nodes.lock"
log "bundles de ComfyUI y nodos OK"

# --- 5. snapshot del repo (hard links contra el anterior) ---
prev=$(ls -1d "$DEST"/snapshots/*/ 2>/dev/null | tail -1)
"${RS[@]}" ${prev:+--link-dest="$prev"} "$R/" "$DEST/snapshots/$TS/" >>"$TMPLOG" 2>&1 || errores=$((errores+1))
log "snapshot del repo: snapshots/$TS (commit $(git -C "$R" rev-parse --short HEAD))"

# --- 6. wheels ---
if [[ " $* " != *" --sin-wheels "* ]]; then
  log "wheels (solo lo que falte)..."
  bash "$R/backup/wheels.sh" "$DEST/wheels" 2>&1 | tee -a "$TMPLOG"
fi

log "=== FIN: $errores errores. Ocupa: $(du -sh "$DEST" 2>/dev/null | cut -f1) ==="
cp "$TMPLOG" "$DEST/ultimo_backup.log"; rm -f "$TMPLOG"
[ "$errores" -eq 0 ]
