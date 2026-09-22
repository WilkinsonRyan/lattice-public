from lattice import core, storage


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    proj = tmp_path / "demo"; proj.mkdir()
    core.init()
    pid = core.register_project(proj)["id"]
    return core.open_db(), pid


def test_task_lifecycle(tmp_path, monkeypatch):
    conn, pid = _setup(tmp_path, monkeypatch)
    tid = storage.add_task(conn, pid, "write the README")
    assert len(storage.list_tasks(conn, pid, "open")) == 1
    assert storage.complete_task(conn, tid) == 1
    assert storage.list_tasks(conn, pid, "open") == []
    assert len(storage.list_tasks(conn, pid, "done")) == 1


def test_completing_twice_reports_no_change(tmp_path, monkeypatch):
    conn, pid = _setup(tmp_path, monkeypatch)
    tid = storage.add_task(conn, pid, "once")
    storage.complete_task(conn, tid)
    assert storage.complete_task(conn, tid) == 0


def test_pattern_recurrence_is_counted_not_duplicated(tmp_path, monkeypatch):
    conn, pid = _setup(tmp_path, monkeypatch)
    storage.add_pattern(conn, pid, "rebuild a venv after moving a project")
    storage.add_pattern(conn, pid, "rebuild a venv after moving a project")
    rows = storage.list_patterns(conn)
    assert len(rows) == 1 and rows[0]["seen_count"] == 2


def test_status_flags_untracked_projects(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert "demo" in core.status()["untracked_projects"]


def test_review_brief_names_the_missing_repo(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    brief = core.review("demo")
    assert "Review brief" in brief
    assert "Not a git repository" in brief
