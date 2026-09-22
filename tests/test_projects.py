import json
from pathlib import Path
from lattice import core, execution, secrets


def test_register_reads_kind_and_description(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    proj = tmp_path / "demo"; proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (proj / "README.md").write_text("# Demo\n\nA small thing that does one job.\n")

    core.init()
    r = core.register_project(proj)
    assert r["kind"] == "python"
    assert r["description"] == "Demo"
    assert r["git"]["is_repo"] is False


def test_registering_twice_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    proj = tmp_path / "demo"; proj.mkdir()
    core.init(); core.register_project(proj)
    assert "error" in core.register_project(proj)


def test_track_creates_a_real_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    proj = tmp_path / "demo"; proj.mkdir()
    (proj / "main.py").write_text("print('hi')\n")
    core.init()
    r = core.register_project(proj, git_init=True)
    assert r["git"]["is_repo"] is True
    assert (proj / ".gitignore").exists()


def test_project_index_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    proj = tmp_path / "demo"; proj.mkdir()
    core.init(); core.register_project(proj, side="work")
    idx = (tmp_path / "data" / "project_index.md").read_text()
    assert "demo" in idx and "Work" in idx


def test_secret_scanner_finds_a_key_without_echoing_it(tmp_path):
    proj = tmp_path / "leaky"; proj.mkdir()
    (proj / "config.py").write_text('AWS_KEY = "AKIAQ7ZX3PLMVN2WRTUY"\n')  # lattice:allow
    findings = secrets.scan(proj)
    assert any(f["kind"] == "AWS access key id" for f in findings)
    # the value must never appear in a finding or the rendered report
    assert all("AKIA" not in str(v) for f in findings for v in f.values())
    assert "AKIAQ7ZX3PLMVN2WRTUY" not in secrets.format_findings(findings, proj)  # lattice:allow


def test_scanner_flags_credential_files_by_name(tmp_path):
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "client_secret_123.json").write_text("{}")
    assert any(f["kind"] == "credential file by name" for f in secrets.scan(proj))


def test_scanner_skips_venv_and_node_modules(tmp_path):
    proj = tmp_path / "p"; (proj / ".venv").mkdir(parents=True)
    (proj / ".venv" / "leak.py").write_text('k = "AKIAQ7ZX3PLMVN2WRTUY"\n')  # lattice:allow
    assert secrets.scan(proj) == []


def test_side_is_inferred_from_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    core.init()
    (tmp_path / "data" / "config.json").write_text(json.dumps({"roots": [
        {"alias": "W", "name": "Work", "path": str(tmp_path / "Work"), "side": "work"},
        {"alias": "P", "name": "Personal", "path": str(tmp_path / "Personal"), "side": "personal"},
    ]}))
    assert execution.guess_side(tmp_path / "Work" / "thing") == "work"
    assert execution.guess_side(tmp_path / "Personal" / "thing") == "personal"
    assert execution.guess_side(tmp_path / "elsewhere") == "unknown"


def test_published_example_keys_are_not_flagged(tmp_path):
    """AWS's documentation placeholder must not trip the scanner — a scanner
    that always cries wolf gets ignored, which is the real failure mode."""
    proj = tmp_path / "docs"; proj.mkdir()
    (proj / "README.md").write_text('Set AWS_KEY="AKIAIOSFODNN7EXAMPLE" as shown.\n')  # lattice:allow
    assert secrets.scan(proj) == []


def test_inline_allow_marker_silences_one_line(tmp_path):
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "fixture.py").write_text('k = "AKIA1234567890ABCDEF"  # lattice:allow\n')
    assert secrets.scan(proj) == []


def test_a_real_looking_key_is_still_caught(tmp_path):
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "conf.py").write_text('k = "AKIA1234567890ABCDEF"\n')  # lattice:allow
    assert any(f["kind"] == "AWS access key id" for f in secrets.scan(proj))
