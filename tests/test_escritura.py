import json

import pytest


def test_crea_csv_y_json(runner):
    assert runner.write_batch_file("tirada.csv", "a,b\n1,2\n").startswith("creado")
    assert runner.write_batch_file("tirada.json", json.dumps({"ok": 1})).startswith("creado")
    assert (runner.BATCHES / "tirada.csv").read_text() == "a,b\n1,2\n"


def test_no_sobrescribe(runner):
    runner.write_batch_file("t.csv", "uno")
    assert "ya existe" in runner.write_batch_file("t.csv", "dos")
    assert (runner.BATCHES / "t.csv").read_text() == "uno"


@pytest.mark.parametrize("nombre", [
    "../x.csv", "/etc/x.csv", "sub/x.csv", "x.sh", "x.csv.sh", ".oculto.csv", "", "x",
])
def test_nombres_rechazados(runner, nombre):
    assert runner.write_batch_file(nombre, "a").startswith("RECHAZADO")


def test_json_invalido_y_tamano(runner):
    assert "JSON no válido" in runner.write_batch_file("m.json", "{mal")
    assert "demasiado grande" in runner.write_batch_file("g.csv", "x" * (runner.WRITE_MAX + 1))
    assert runner.write_batch_file("n.csv", "a\0b").startswith("RECHAZADO")


def test_no_escribe_a_traves_de_enlaces(runner, tmp_path):
    fuera = tmp_path / "fuera.csv"
    (runner.BATCHES / "enlace.csv").symlink_to(fuera)
    assert runner.write_batch_file("enlace.csv", "x").startswith("RECHAZADO")
    assert not fuera.exists()


def test_batch_dir_fuera_de_la_raiz_se_ignora(tmp_path, monkeypatch):
    import importlib
    root = tmp_path / "r"; root.mkdir()
    allow = tmp_path / "a.json"; allow.write_text(json.dumps({"batch_dir": "/tmp"}))
    monkeypatch.setenv("PIPELINE_ROOT", str(root)); monkeypatch.setenv("PIPELINE_ALLOWLIST", str(allow))
    import pipeline_runner
    mod = importlib.reload(pipeline_runner)
    assert mod.BATCHES == root.resolve() / "batches"
