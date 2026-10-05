import shutil

import pytest

PERMITIDOS = [
    "ls", "ls -la scripts", "cat datos.txt", "head -n 1 datos.txt", "tail -n 1 datos.txt",
    "wc -l datos.txt", "du -sh .", "find . -maxdepth 1 -name '*.txt'",
    "git status", "git log --oneline -3", "git diff",
    pytest.param("nvidia-smi", marks=pytest.mark.skipif(
        shutil.which("nvidia-smi") is None, reason="sin GPU NVIDIA")),
]
RECHAZADOS = [
    "ls ~", "ls ..", "ls /etc", "ls ~/.ssh", "cat ../../.bashrc", "cat ../x",
    "find . -delete", "find . -exec ls {} +", "find . -fprint /tmp/x", "find -L .",
    "find / -name x", "ls -L", "ls -lL", "du -H .", "tail -f datos.txt",
    "wc --files0-from=/etc/passwd", "cat .git/config",
    "git push", "git commit -m x", "git -c core.pager=x log", "git diff --output=x",
    "git log --output=x", "git diff --no-index /etc/passwd /etc/hosts",
    "nvidia-smi -f x", "nvidia-smi -r",
]


@pytest.mark.parametrize("cmd", PERMITIDOS)
def test_lecturas_permitidas(runner, cmd):
    argv, tipo = runner._classify(cmd, allow_read=True)
    assert tipo == "read"


@pytest.mark.parametrize("cmd", RECHAZADOS)
def test_lecturas_rechazadas(runner, cmd):
    with pytest.raises(runner.Rechazo):
        runner._classify(cmd, allow_read=True)


def test_enlace_que_sale_de_la_raiz(runner, tmp_path):
    fuera = tmp_path / "secreto.txt"
    fuera.write_text("x")
    (runner.TEST_ROOT / "enlace.txt").symlink_to(fuera)
    with pytest.raises(runner.Rechazo):
        runner._classify("cat enlace.txt", allow_read=True)


def test_run_ejecuta_de_verdad(runner):
    out = runner.run("cat datos.txt")
    assert out.startswith("exit=0") and "hola" in out
    assert "gen:x y" in runner.run("bash scripts/generate.sh x y")


def test_run_rechazado_devuelve_texto(runner):
    assert runner.run("ls ; rm x").startswith("RECHAZADO")


def test_entorno_minimo(runner, monkeypatch):
    monkeypatch.setenv("SECRETO_DE_PRUEBA", "no-debe-pasar")
    env = runner._env()
    assert "SECRETO_DE_PRUEBA" not in env
    assert set(env) <= {"HOME", "USER", "LOGNAME", "PATH", "LANG", "PYTHONUNBUFFERED"}
