#!/usr/bin/env python3
"""pipeline_runner: servidor MCP local con LISTA DE PERMITIDOS para un pipeline de ComfyUI.

Reglas:
- Nunca hay shell: se ejecuta con subprocess y lista de argumentos (shlex), sin `bash -c`.
  Pipes, ;, &&, redirecciones, $(...) y comillas invertidas se rechazan.
- Ejecutables permitidos (con cualquier argumento): solo los de allowlist.json (rutas resueltas
  con realpath). Sin ese fichero no se puede ejecutar ningún script: ver allowlist.example.json.
- Solo lectura (ls, cat, head, tail, wc, du, find, nvidia-smi, git status/log/diff):
  todos los argumentos que no sean opciones deben resolverse (~, .., enlaces) dentro de
  la raíz del pipeline (PIPELINE_ROOT, por defecto la carpeta padre de mcp/).
- Escritura: solo write_batch_file, ficheros .csv/.json NUEVOS en la carpeta batch_dir (dentro de la raíz).
- start_job: máx. 2 trabajos a la vez, 3 h por trabajo, el estado se lee del disco.
- Entorno de los procesos: HOME, PATH, LANG, USER y poco más.
"""
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HOME = Path.home()
HERE = Path(__file__).resolve().parent
ROOT = Path(os.path.realpath(os.environ.get("PIPELINE_ROOT") or HERE.parent))
LOGDIR = HERE / "logs"


def _cargar_allowlist() -> dict:
    ruta = Path(os.environ.get("PIPELINE_ALLOWLIST") or HERE / "allowlist.json")
    try:
        return json.loads(ruta.read_text())
    except OSError:
        return {}


def _ruta(x: str) -> Path:
    p = Path(os.path.expanduser(x))
    return Path(os.path.realpath(p if p.is_absolute() else ROOT / p))


ALLOW = _cargar_allowlist()
BATCHES = _ruta(ALLOW.get("batch_dir", "batches"))
if BATCHES != ROOT and ROOT not in BATCHES.parents:  # batch_dir siempre dentro de la raíz
    BATCHES = ROOT / "batches"

MAX_JOBS = 2
JOB_LIMIT_S = 3 * 3600
RUN_MAX_S = 900
READ_MAX_S = 60
OUT_CAP = 20000
ERR_CAP = 8000
WRITE_MAX = 200_000
JOB_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

# Scripts ejecutables (de allowlist.json): intérprete -> rutas reales exactas
EXEC_FILES = {
    "bash": {_ruta(x) for x in ALLOW.get("bash", [])},
    "python3": {_ruta(x) for x in ALLOW.get("python3", [])},
}
BATCH_GLOB = ALLOW.get("batch_glob")  # solo bash, solo dentro de batch_dir; None = sin tiradas
BATCH_EXCLUDE = set(ALLOW.get("batch_exclude", []))  # nombres reales (tras realpath)
INTERPRETERS = {"bash": "/usr/bin/bash", "python3": "/usr/bin/python3"}

READ_CMDS = {"ls", "cat", "head", "tail", "wc", "du", "find", "nvidia-smi", "git"}
FIND_DENY = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
             "-fprintf", "-fls", "-L", "-H"}
TAIL_DENY = {"-f", "-F", "--follow", "--retry"}
NVSMI_OK = ("-q", "-L", "-i", "--id", "-d", "--display", "-x", "--query-gpu",
            "--query-compute-apps", "--format")
GIT_SUBS = {"status", "log", "diff"}
GIT_DENY_PREFIX = ("--output", "--no-index", "--ext-diff", "--textconv", "-O",
                   "--open-files-in-pager", "--exec-path", "-c", "--git-dir",
                   "--work-tree", "--paginate")

OPERATORS = set(";()<>|&")


class Rechazo(Exception):
    pass


# ----------------------------------------------------------------------------
# análisis del comando
# ----------------------------------------------------------------------------
def _tokenize(command: str) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        raise Rechazo("Comando vacío.")
    if len(command) > 8000:
        raise Rechazo("Comando demasiado largo.")
    if "\n" in command or "\r" in command or "\0" in command:
        raise Rechazo("Saltos de línea y caracteres nulos no permitidos.")
    if "`" in command or "$" in command:
        raise Rechazo("No se permiten comillas invertidas ni '$' (sin expansiones ni sustituciones).")
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as e:
        raise Rechazo(f"No se puede analizar el comando: {e}")
    for t in tokens:
        if t and set(t) <= OPERATORS:
            raise Rechazo(f"Operador de shell no permitido: {t!r} (pipes, ;, &&, redirecciones y subshells están prohibidos).")
    return tokens


def _real(arg: str) -> Path:
    """Resuelve ~, relativas (desde ROOT), .. y enlaces."""
    p = Path(os.path.expanduser(arg))
    if not p.is_absolute():
        p = ROOT / p
    return Path(os.path.realpath(p))


def _inside(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def _classify_exec(tokens: list[str]) -> tuple[list[str], Path] | None:
    """Si es 'bash|python3 <script permitido> args', devuelve (argv, script real)."""
    if len(tokens) < 2 or tokens[0] not in INTERPRETERS:
        return None
    interp = tokens[0]
    script = tokens[1]
    if script.startswith("-"):
        raise Rechazo(f"{interp} {script}: solo se admite un script permitido como primer argumento.")
    real = _real(script)
    ok = real in EXEC_FILES[interp]
    if not ok and interp == "bash" and BATCH_GLOB:
        ok = (real.parent == BATCHES and real.is_file()
              and fnmatch.fnmatchcase(real.name, BATCH_GLOB)
              and real.name not in BATCH_EXCLUDE)
    if not ok:
        raise Rechazo(f"Script no permitido: {script} (resuelve a {real})")
    if not real.is_file():
        raise Rechazo(f"El script no existe: {real}")
    return [INTERPRETERS[interp], str(real), *tokens[2:]], real


def _classify_read(tokens: list[str]) -> list[str]:
    cmd = tokens[0]
    if cmd not in READ_CMDS:
        raise Rechazo(f"Comando no permitido: {cmd!r}")
    args = tokens[1:]
    if cmd == "git":
        if not args or args[0] not in GIT_SUBS:
            raise Rechazo("git: solo status, log y diff.")
        sub, rest = args[0], args[1:]
        for a in rest:
            if a.startswith(GIT_DENY_PREFIX):
                raise Rechazo(f"git {sub}: opción no permitida {a!r}")
        argv = ["/usr/bin/git", "--no-pager", "-C", str(ROOT), sub]
        if sub in ("diff", "log"):
            argv.append("--no-ext-diff")
        argv += rest
        _check_paths(rest, cmd)
        return argv
    exe = shutil.which(cmd, path="/usr/bin:/bin:/usr/local/bin")
    if not exe:
        raise Rechazo(f"{cmd} no está instalado.")
    _check_paths(args, cmd)
    return [exe, *args]


def _check_paths(args: list[str], cmd: str) -> None:
    after_dd = False
    for a in args:
        if a == "--" and not after_dd:
            after_dd = True
            continue
        cand = a
        if a.startswith("-") and not after_dd:
            if cmd == "find" and a in FIND_DENY:
                raise Rechazo(f"find {a} no está permitido.")
            if cmd == "tail" and a in TAIL_DENY:
                raise Rechazo(f"tail {a} no está permitido.")
            if cmd in ("ls", "du"):
                if a.startswith("--dereference") or (not a.startswith("--") and set(a[1:]) & {"L", "H"}):
                    raise Rechazo(f"{cmd} {a}: seguir enlaces no está permitido.")
            if cmd == "nvidia-smi" and not a.startswith(NVSMI_OK):
                raise Rechazo(f"nvidia-smi {a}: opción no permitida (solo consulta).")
            if "=" not in a:
                continue
            cand = a.split("=", 1)[1]  # --files0-from=/etc/passwd, etc.
            if not cand:
                continue
        if "\0" in cand:
            raise Rechazo("Carácter nulo en argumento.")
        real = _real(cand)
        if not _inside(real, ROOT):
            raise Rechazo(f"Ruta fuera de {ROOT}: {a} -> {real}")
        if cmd in ("cat", "head", "tail", "wc") and ".git" in real.parts:
            raise Rechazo("No se lee el interior de .git con este comando (usa git log/diff).")


def _classify(command: str, allow_read: bool) -> tuple[list[str], str]:
    """Devuelve (argv, tipo) con tipo 'exec' o 'read', o lanza Rechazo."""
    tokens = _tokenize(command)
    ex = _classify_exec(tokens)
    if ex:
        return ex[0], "exec"
    if not allow_read:
        raise Rechazo("start_job solo admite scripts de generación permitidos (bash/python3 + script).")
    return _classify_read(tokens), "read"


# ----------------------------------------------------------------------------
# entorno y trabajos
# ----------------------------------------------------------------------------
def _env() -> dict:
    return {
        "HOME": str(HOME),
        "USER": os.environ.get("USER", ""),
        "LOGNAME": os.environ.get("USER", ""),
        "PATH": f"{HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
    }


def _meta_path(job_id: str) -> Path:
    return LOGDIR / f"{job_id}.json"


def _read_meta(job_id: str) -> dict | None:
    try:
        return json.loads(_meta_path(job_id).read_text())
    except (OSError, ValueError):
        return None


def _write_meta(job_id: str, meta: dict) -> None:
    tmp = _meta_path(job_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(meta))
    os.replace(tmp, _meta_path(job_id))


def _wrapper_alive(meta: dict, job_id: str) -> bool:
    pid = meta.get("wrapper_pid")
    if not pid:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return b"--wrap" in cmd and job_id.encode() in cmd


def _running_jobs() -> list[str]:
    out = []
    for f in LOGDIR.glob("*.json"):
        jid = f.stem
        if not JOB_ID_RE.match(jid):
            continue
        meta = _read_meta(jid)
        if meta and "exit" not in meta and _wrapper_alive(meta, jid):
            out.append(jid)
    return out


def _wrap(job_id: str) -> int:
    """Proceso vigilante de un trabajo: lo lanza, aplica el límite de 3 h y anota el código."""
    meta = _read_meta(job_id)
    if not meta:
        return 2
    child = subprocess.Popen(meta["argv"], cwd=ROOT, env=_env(), stdin=subprocess.DEVNULL,
                             start_new_session=True)

    def parar(signum, frame):
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        meta["stopped"] = True

    signal.signal(signal.SIGTERM, parar)
    try:
        rc = child.wait(timeout=JOB_LIMIT_S)
    except subprocess.TimeoutExpired:
        meta["timeout"] = True
        os.killpg(child.pid, signal.SIGTERM)
        try:
            rc = child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            rc = child.wait()
    meta["exit"] = rc
    meta["end"] = time.time()
    _write_meta(job_id, meta)
    return 0


# ----------------------------------------------------------------------------
# herramientas (funciones normales; se registran en MCP más abajo)
# ----------------------------------------------------------------------------
def run(command: str, timeout_s: int = 300) -> str:
    """Ejecuta un comando permitido sin shell y devuelve código, stdout y stderr.
    Permitidos: scripts de allowlist.json y consultas de solo lectura dentro de la raíz del pipeline (ls, cat, head, tail, wc, du,
    find, nvidia-smi, git status/log/diff). Para tiradas largas usa start_job."""
    try:
        argv, tipo = _classify(command, allow_read=True)
    except Rechazo as e:
        return f"RECHAZADO: {e}"
    limite = RUN_MAX_S if tipo == "exec" else READ_MAX_S
    timeout_s = max(1, min(int(timeout_s), limite))
    try:
        r = subprocess.run(argv, cwd=ROOT, env=_env(), capture_output=True, text=True,
                           errors="replace", timeout=timeout_s, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"Tiempo agotado ({timeout_s}s). Usa start_job para tareas largas."
    return f"exit={r.returncode}\n--- stdout ---\n{r.stdout[-OUT_CAP:]}\n--- stderr ---\n{r.stderr[-ERR_CAP:]}"


def start_job(command: str) -> str:
    """Lanza en segundo plano un script permitido (allowlist.json). Máx. 2 a la vez, 3 h por trabajo. Devuelve un id."""
    try:
        argv, _ = _classify(command, allow_read=False)
    except Rechazo as e:
        return f"RECHAZADO: {e}"
    LOGDIR.mkdir(parents=True, exist_ok=True)
    activos = _running_jobs()
    if len(activos) >= MAX_JOBS:
        return f"RECHAZADO: ya hay {len(activos)} trabajos en curso (máx. {MAX_JOBS}): {', '.join(activos)}"
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    log = LOGDIR / f"{job_id}.log"
    meta = {"cmd": command, "argv": argv, "start": time.time(), "log": str(log)}
    _write_meta(job_id, meta)
    with open(log, "w") as fh:
        p = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--wrap", job_id],
            cwd=ROOT, env=_env(), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    meta["wrapper_pid"] = p.pid
    # el vigilante puede acabar antes de que escribamos esto: no pisar un "exit" ya anotado
    actual = _read_meta(job_id) or meta
    actual["wrapper_pid"] = p.pid
    _write_meta(job_id, actual)
    return f"job={job_id} pid={p.pid} log={log}"


def _estado(job_id: str, meta: dict) -> str:
    mins = ((meta.get("end") or time.time()) - meta["start"]) / 60
    if "exit" in meta:
        extra = " (límite de 3 h)" if meta.get("timeout") else " (parado)" if meta.get("stopped") else ""
        return f"terminado exit={meta['exit']}{extra} ({mins:.1f} min)"
    if _wrapper_alive(meta, job_id):
        return f"en curso ({mins:.1f} min)"
    return f"interrumpido sin código de salida ({mins:.1f} min)"


def job_status(job_id: str, tail_lines: int = 60) -> str:
    """Estado de un trabajo (se lee del disco: sobrevive a reinicios del servidor) y últimas líneas del log."""
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        return "Id de trabajo no válido."
    meta = _read_meta(job_id)
    log = LOGDIR / f"{job_id}.log"
    if not log.exists():
        return "No existe ese trabajo."
    estado = _estado(job_id, meta) if meta else "estado desconocido (sin metadatos)"
    n = max(1, min(int(tail_lines), 400))
    lines = log.read_text(errors="replace").splitlines()[-n:]
    return f"{estado}\n" + "\n".join(lines)


def list_jobs() -> str:
    """Lista los trabajos lanzados (también los de sesiones anteriores), los más recientes primero."""
    filas = []
    for f in sorted(LOGDIR.glob("*.json"), reverse=True)[:30]:
        jid = f.stem
        meta = _read_meta(jid) if JOB_ID_RE.match(jid) else None
        if meta:
            filas.append(f"{jid}  {_estado(jid, meta)}  {meta['cmd']}")
    return "\n".join(filas) or "No hay trabajos."


def stop_job(job_id: str) -> str:
    """Detiene un trabajo en curso."""
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        return "Id de trabajo no válido."
    meta = _read_meta(job_id)
    if not meta:
        return "No existe ese trabajo."
    if "exit" in meta or not _wrapper_alive(meta, job_id):
        return "Ya había terminado."
    os.kill(meta["wrapper_pid"], signal.SIGTERM)
    return "Señal de parada enviada."


def write_batch_file(name: str, content: str) -> str:
    """Crea un fichero NUEVO .csv o .json (tiradas) en la carpeta batch_dir. No sobrescribe."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.(csv|json)", name):
        return "RECHAZADO: el nombre debe ser simple (letras, números, . _ -) y acabar en .csv o .json; sin rutas."
    if not isinstance(content, str) or "\0" in content:
        return "RECHAZADO: el contenido debe ser texto."
    data = content.encode("utf-8")
    if len(data) > WRITE_MAX:
        return f"RECHAZADO: demasiado grande (máx. {WRITE_MAX} bytes)."
    if name.endswith(".json"):
        try:
            json.loads(content)
        except ValueError as e:
            return f"RECHAZADO: JSON no válido: {e}"
    if Path(os.path.realpath(BATCHES)) != BATCHES:
        return "RECHAZADO: batch_dir no resuelve a su ruta esperada (enlace simbólico)."
    destino = BATCHES / name
    if os.path.lexists(destino):
        return f"RECHAZADO: {name} ya existe (no se sobrescribe)."
    try:
        fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except OSError as e:
        return f"RECHAZADO: no se pudo crear: {e}"
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return f"creado {destino} ({len(data)} bytes)"


def _main_server() -> None:
    from mcp.server.mcpserver import MCPServer
    LOGDIR.mkdir(parents=True, exist_ok=True)
    mcp = MCPServer("pipeline_runner")
    for fn in (run, start_job, job_status, list_jobs, stop_job, write_batch_file):
        mcp.tool()(fn)
    mcp.run()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--wrap" and JOB_ID_RE.match(sys.argv[2]):
        sys.exit(_wrap(sys.argv[2]))
    _main_server()
