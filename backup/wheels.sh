#!/usr/bin/env bash
# ============================================================================
#  wheels.sh — descarga los wheels EXACTOS de requirements.lock a <backup>/wheels/
#  (para que restore.sh reconstruya el entorno sin internet). La llama backup.sh.
#  - torch/torchvision/torchaudio + nvidia-* salen del índice cu130 de PyTorch.
#  - Paquetes de apt (python-apt, dbus-python...) no están en PyPI: se apuntan en
#    FALLOS.txt y no bloquean. Se descarga paquete a paquete con --no-deps.
#  - Solo baja lo que falte: un wheel ya descargado no se vuelve a pedir.
# ============================================================================
set -uo pipefail
R="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"   # raíz del repo
DEST="${1:?uso: wheels.sh DESTINO}"
mkdir -p "$DEST"
TORCH_IDX="https://download.pytorch.org/whl/cu130"
: > "$DEST/FALLOS.txt"
ok=0; ya=0; fallo=0
while IFS= read -r req; do
  [[ -z "$req" || "$req" == \#* ]] && continue
  nombre="${req%%[=@ ]*}"
  norm=$(echo "$nombre" | tr 'A-Z-' 'a-z_')
  ver="${req#*==}"
  if [[ "$req" == *"=="* ]] && ls "$DEST" | tr 'A-Z-' 'a-z_' | grep -q "^${norm}_${ver%%+*}"; then ya=$((ya+1)); continue; fi
  case "$norm" in
    torch|torchvision|torchaudio)
      spec="$req"; [[ "$spec" != *+cu130 ]] && spec="${spec}+cu130"
      args=(--index-url "$TORCH_IDX") ;;
    nvidia_*|triton) spec="$req"; args=(--extra-index-url "$TORCH_IDX") ;;
    *) spec="$req"; args=() ;;
  esac
  if python3 -m pip download --no-deps --no-cache-dir --exists-action i --quiet -d "$DEST" "${args[@]}" "$spec" </dev/null 2>>"$DEST/_pip_errores.log"; then
    ok=$((ok+1))
  else
    fallo=$((fallo+1)); echo "$req" >> "$DEST/FALLOS.txt"
  fi
done < "$R/locks/requirements.lock"
cp "$R/locks/requirements.lock" "$DEST/requirements.lock"
echo "wheels: $ok nuevos, $ya ya estaban, $fallo sin wheel (ver $DEST/FALLOS.txt)"
