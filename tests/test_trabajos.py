import json
import time

import pytest


def esperar(cond, t=10):
    fin = time.time() + t
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.1)
    return False


def id_de(resp):
    assert resp.startswith("job="), resp
    return resp.split()[0][4:]


def test_trabajo_corto_guarda_codigo_y_log(runner):
    jid = id_de(runner.start_job("bash scripts/generate.sh hola"))
    assert esperar(lambda: "terminado" in runner.job_status(jid))
    st = runner.job_status(jid)
    assert "exit=0" in st and "gen:hola" in st


def test_maximo_dos_trabajos(runner):
    a = id_de(runner.start_job("bash scripts/lento.sh"))
    b = id_de(runner.start_job("bash scripts/lento.sh"))
    assert esperar(lambda: len(runner._running_jobs()) == 2)
    assert runner.start_job("bash scripts/lento.sh").startswith("RECHAZADO")
    for j in (a, b):
        runner.stop_job(j)
    assert esperar(lambda: not runner._running_jobs())


def test_stop_job_y_estado_desde_disco(runner):
    jid = id_de(runner.start_job("bash scripts/lento.sh"))
    assert esperar(lambda: "inicio" in runner.job_status(jid))  # el hijo ya corre
    assert "Señal" in runner.stop_job(jid)
    assert esperar(lambda: "parado" in runner.job_status(jid))
    # un "servidor nuevo" (sin memoria) lee lo mismo del disco
    assert "parado" in runner.job_status(jid) and jid in runner.list_jobs()
    assert runner.stop_job(jid) == "Ya había terminado."


def test_limite_de_tiempo(runner, monkeypatch):
    monkeypatch.setattr(runner, "JOB_LIMIT_S", 1)
    jid = "20260101-000000-abcdef"
    runner._write_meta(jid, {"cmd": "t", "argv": ["/usr/bin/bash", str(runner.TEST_ROOT / "scripts/lento.sh")],
                             "start": time.time(), "log": ""})
    assert runner._wrap(jid) == 0
    meta = runner._read_meta(jid)
    assert meta["timeout"] is True and meta["exit"] != 0


@pytest.mark.parametrize("jid", ["../../etc/passwd", "x", "", "20260101-000000-ZZZZZZ"])
def test_ids_invalidos(runner, jid):
    assert runner.job_status(jid) == "Id de trabajo no válido."
    assert runner.stop_job(jid) == "Id de trabajo no válido."


def test_el_vigilante_hereda_la_configuracion(runner):
    env = runner._wrapper_env()
    assert env["PIPELINE_ROOT"] == str(runner.ROOT) and "PIPELINE_LOGDIR" in env
    assert "PIPELINE_" not in "".join(runner._env())
