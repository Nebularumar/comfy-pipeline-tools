# comfy-pipeline-tools

Utilidades para mantener un pipeline local de generación de imágenes y vídeo con ComfyUI:
copia de seguridad reproducible, restauración sin internet y un servidor MCP con lista de
permitidos para dejar que un asistente lance tiradas sin darle una shell.

## Contenido

| Carpeta | Qué hay |
|---|---|
| `backup/` | `backup.sh` (rsync a un disco secundario con comprobación de montaje, SHA256 de modelos, snapshots con hard links y bundles de git), `restore.sh` (reconstruye ComfyUI, nodos y venv sin internet), `wheels.sh` (descarga los wheels exactos de `locks/requirements.lock`) y `config.example.json` |
| `locks/` | `requirements.lock` y `nodes.lock` (commit exacto de ComfyUI y de cada nodo personalizado) |
| `scripts/` | `cf_client.py` (cliente mínimo de la API de ComfyUI) y `make_wf_seedvr2.py` (genera el workflow de SeedVR2) |
| `video/` | `kling_video.py` (image-to-video con Kling vía fal.ai) y `rife_60fps.py` (interpolación con rife-ncnn-vulkan, sin cruzar cortes) |
| `mcp/` | `pipeline_runner.py`: servidor MCP local con lista de permitidos |
| `systemd/` | servicio y timer de usuario para el backup semanal |

## Backup

```bash
cp backup/config.example.json backup/config.json   # edita UUID del disco, rutas y modelos con su SHA256
bash backup/backup.sh                              # aborta si el disco no está montado de verdad
export PIPELINE_BACKUP_DIR=/mnt/backup/backup_pipeline
bash backup/restore.sh --comprobar                 # ¿tiene el backup todo lo necesario?
bash backup/restore.sh /mnt/disco/ComfyUI_nuevo    # reconstruye sin internet
```

Para el timer: copia `systemd/pipeline-backup.*` a `~/.config/systemd/user/`, ajusta la ruta del
servicio y `systemctl --user enable --now pipeline-backup.timer`.

## Servidor MCP (`mcp/`)

Sin shell: se ejecuta con `subprocess` y lista de argumentos. Pipes, `;`, `&&`, redirecciones,
`$` y comillas invertidas se rechazan.

- **Scripts permitidos** (cualquier argumento): solo los de `mcp/allowlist.json` (copia el `.example`),
  más las tiradas `batch_dir/batch_glob` salvo `batch_exclude`.
- **Solo lectura**: `ls`, `cat`, `head`, `tail`, `wc`, `du`, `find` (sin `-delete`/`-exec`), `nvidia-smi`,
  `git status/log/diff`, con todos los argumentos resueltos (`~`, `..`, enlaces) dentro de la raíz del pipeline.
- **Escritura**: solo `write_batch_file`, ficheros `.csv`/`.json` nuevos en `batch_dir`; nunca sobrescribe.
- `start_job`: máx. 2 trabajos a la vez, 3 h por trabajo; el estado se lee del disco.

**Límites reales:** los argumentos de los scripts permitidos no se validan, y esos scripts hacen lo
que su código dice. La seguridad depende de que nadie edite los scripts de la lista.

```bash
bash mcp/instalar.sh   # crea el venv, registra el servidor y hace copia de la config de Claude
```

## Requisitos
Linux, Python 3.10, rsync y git. `video/` necesita una clave de fal.ai en `~/.fal_key`.
