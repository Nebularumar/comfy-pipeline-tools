import pytest

RECHAZADOS_SHELL = [
    "ls ; rm x", "ls && ls", "ls | cat", "ls > x", "ls < x", "ls & ls",
    "ls $(whoami)", "ls `id`", "cat $HOME/.bashrc", "ls\nrm x",
]


@pytest.mark.parametrize("cmd", RECHAZADOS_SHELL)
def test_sin_operadores_de_shell(runner, cmd):
    with pytest.raises(runner.Rechazo):
        runner._classify(cmd, allow_read=True)


def test_operadores_dentro_de_comillas_son_texto(runner):
    argv, tipo = runner._classify("bash scripts/generate.sh 'a;b | c'", allow_read=False)
    assert tipo == "exec" and argv[-1] == "a;b | c"


@pytest.mark.parametrize("cmd", [
    'python3 -c "print(1)"', 'bash -c "ls"', "bash -x scripts/generate.sh",
    "bash scripts/otro.sh", "bash batches/batch_x.sh", "python3 scripts/generate.sh",
    "rm x", "sudo ls", "curl http://x", "env",
])
def test_ejecutables_fuera_de_la_lista(runner, cmd):
    with pytest.raises(runner.Rechazo):
        runner._classify(cmd, allow_read=True)


@pytest.mark.parametrize("cmd", [
    "bash scripts/generate.sh a b", "bash scripts/lento.sh", "bash batches/batch_a.sh --test",
])
def test_ejecutables_permitidos(runner, cmd):
    argv, tipo = runner._classify(cmd, allow_read=False)
    assert tipo == "exec" and argv[0] == "/usr/bin/bash"


def test_batch_exclude_resuelve_enlaces(runner):
    enlace = runner.BATCHES / "batch_enlace.sh"
    enlace.symlink_to(runner.BATCHES / "batch_x.sh")
    with pytest.raises(runner.Rechazo):
        runner._classify("bash batches/batch_enlace.sh", allow_read=False)


def test_start_job_no_admite_lecturas(runner):
    with pytest.raises(runner.Rechazo):
        runner._classify("ls", allow_read=False)
    assert runner.start_job("ls").startswith("RECHAZADO")
