import importlib
import subprocess

import pytest


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """pipeline_runner configurado sobre una raíz temporal con una lista de permitidos mínima."""
    root = tmp_path / "pipe"
    (root / "scripts").mkdir(parents=True)
    (root / "batches").mkdir()
    gen = root / "scripts" / "generate.sh"
    gen.write_text('#!/bin/bash\necho "gen:$@"\n')
    lento = root / "scripts" / "lento.sh"
    lento.write_text("#!/bin/bash\necho inicio\nsleep 30\n")
    (root / "scripts" / "otro.sh").write_text("#!/bin/bash\necho no\n")
    for n in ("batch_a.sh", "batch_x.sh"):
        (root / "batches" / n).write_text("#!/bin/bash\necho tirada\n")
    (root / "datos.txt").write_text("hola\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    allow = tmp_path / "allowlist.json"
    allow.write_text(
        '{"bash": ["scripts/generate.sh", "scripts/lento.sh"], "batch_dir": "batches",'
        ' "batch_glob": "batch_*.sh", "batch_exclude": ["batch_x.sh"]}'
    )
    monkeypatch.setenv("PIPELINE_ROOT", str(root))
    monkeypatch.setenv("PIPELINE_ALLOWLIST", str(allow))
    monkeypatch.setenv("PIPELINE_LOGDIR", str(tmp_path / "logs"))
    import pipeline_runner
    mod = importlib.reload(pipeline_runner)
    mod.LOGDIR.mkdir(parents=True, exist_ok=True)
    mod.TEST_ROOT = root
    return mod
