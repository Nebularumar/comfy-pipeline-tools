"""Comprobaciones estáticas de los scripts del repo."""
import json
import py_compile
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SH = sorted(RAIZ.glob("**/*.sh"))
PY = sorted(p for p in RAIZ.glob("**/*.py") if ".venv" not in p.parts and "tests" not in p.parts)


@pytest.mark.parametrize("f", SH, ids=lambda p: str(p.relative_to(RAIZ)))
def test_sintaxis_bash(f):
    subprocess.run(["bash", "-n", str(f)], check=True)


@pytest.mark.parametrize("f", PY, ids=lambda p: str(p.relative_to(RAIZ)))
def test_sintaxis_python(f, tmp_path):
    py_compile.compile(str(f), cfile=str(tmp_path / "x.pyc"), doraise=True)


@pytest.mark.parametrize("f", ["backup/config.example.json", "mcp/allowlist.example.json"])
def test_json_de_ejemplo(f):
    json.loads((RAIZ / f).read_text())


def test_nodes_lock_bien_formado():
    for linea in (RAIZ / "locks/nodes.lock").read_text().splitlines():
        if linea and not linea.startswith("#"):
            repo, commit, origen, uso = linea.split()
            assert len(commit) == 40 and uso in ("usado", "no_usado")


def test_sin_rutas_personales():
    prohibido = ["/home/nebu", "claude-1000"]
    for f in [*SH, *PY, RAIZ / "README.md"]:
        txt = f.read_text()
        assert not any(p in txt for p in prohibido), f
