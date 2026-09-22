from lattice import core, storage


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(core, "IDEAS_DIR", tmp_path / "ideas")
    # never let a test reach the real ~/.claude/skills — promotion writes there
    monkeypatch.setattr(core, "SKILLS_DIR", tmp_path / "skills")
    proj = tmp_path / "demo"; proj.mkdir()
    (proj / "README.md").write_text("# Demo\n\nDoes one job.\n")
    core.init()
    return core.register_project(proj, side="work")["id"]


def test_context_index_is_compact_and_names_projects(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    pack = core.context_pack()
    assert "demo" in pack and "work" in pack
    assert len(pack) < 2000, "the index loads every session; it must stay small"


def test_context_for_one_project_includes_decisions(tmp_path, monkeypatch):
    """Context packs carry decisions but NOT memory text — memory lives in
    ~/Lattice/memory/, which Claude Code loads on its own. Repeating it here
    would spend the same tokens twice."""
    pid = _setup(tmp_path, monkeypatch)
    core.add_decision("demo", "use SQLite not JSON", rationale="needs cross-project queries")
    pack = core.context_pack("demo")
    assert "use SQLite not JSON" in pack
    assert "needs cross-project queries" in pack


def test_context_for_unknown_project_is_graceful(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert "No project matching" in core.context_pack("nope")


def test_promotion_refuses_a_one_off(tmp_path, monkeypatch):
    pid = _setup(tmp_path, monkeypatch)
    conn = core.open_db(); storage.add_pattern(conn, pid, "do the thing"); conn.close()
    r = core.promote_pattern("do the thing")
    assert "error" in r and "only been seen once" in r["error"]


def test_promotion_succeeds_once_a_pattern_recurs(tmp_path, monkeypatch):
    pid = _setup(tmp_path, monkeypatch)
    conn = core.open_db()
    storage.add_pattern(conn, pid, "rebuild a venv after moving")
    storage.add_pattern(conn, pid, "rebuild a venv after moving")
    conn.close()
    r = core.promote_pattern("rebuild a venv after moving")
    assert "error" not in r and r["seen"] == 2
    from pathlib import Path
    assert Path(r["path"]).exists()
    conn = core.open_db()
    assert storage.get_pattern(conn, "rebuild a venv after moving")["promoted_to_skill"] == 1
    assert len(storage.list_skills(conn)) == 1
    conn.close()


def test_force_promote_overrides_the_guard(tmp_path, monkeypatch):
    pid = _setup(tmp_path, monkeypatch)
    conn = core.open_db(); storage.add_pattern(conn, pid, "one off thing"); conn.close()
    assert "error" not in core.force_promote("one off thing")


def test_session_round_trip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sid = core.session_start()["session_id"]
    assert core.session_end("did some work")["session_id"] == sid
    assert core.session_end()["session_id"] is None   # nothing left open


def test_idea_capture_writes_a_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = core.add_idea("PCB generator", trigger="I'd like to build this eventually", side="personal")
    from pathlib import Path
    assert Path(r["path"]).exists()
    assert "pcb-generator" in r["path"]
    ideas = core.list_ideas()
    assert len(ideas) == 1 and ideas[0]["side"] == "personal"


def test_duplicate_idea_is_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    core.add_idea("PCB generator")
    assert "error" in core.add_idea("PCB generator")


def test_export_then_import_into_a_fresh_store(tmp_path, monkeypatch):
    """The whole point: a second machine ends up knowing what the first knows."""
    import json, shutil
    from lattice import core as C

    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "a"))
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    proj = tmp_path / "demo"; proj.mkdir()
    C.init()
    pid = C.register_project(proj, side="work")["id"]
    conn = C.open_db()
    storage.add_task(conn, pid, "rebuild the venv")
    conn.close()
    C.add_decision("demo", "use SQLite", rationale="cross-project queries")
    exported = C.export_all()
    assert "memories" not in exported["counts"]
    assert exported["counts"]["tasks"] == 1

    # a second, empty machine
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "b"))
    C.init()
    assert C.status()["projects"] == 0
    shutil.copytree(tmp_path / "a" / "export", tmp_path / "b" / "export")

    r = C.import_all()
    assert r["added"]["projects"] == 1
    assert r["added"]["tasks"] == 1
    s = C.status()
    assert s["projects"] == 1 and s["open_tasks"] == 1
    assert "rebuild the venv" in C.context_pack("demo")


def test_import_is_idempotent(tmp_path, monkeypatch):
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "a"))
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    proj = tmp_path / "demo"; proj.mkdir()
    C.init(); pid = C.register_project(proj)["id"]
    conn = C.open_db(); storage.add_memory(conn, pid, "a fact"); conn.close()
    C.export_all()
    assert sum(C.import_all()["added"].values()) == 0     # already present
    assert sum(C.import_all()["added"].values()) == 0     # and still


def test_export_is_deterministic(tmp_path, monkeypatch):
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "a"))
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    proj = tmp_path / "demo"; proj.mkdir()
    C.init(); C.register_project(proj)
    C.export_all()
    first = (tmp_path / "a" / "export" / "projects.jsonl").read_text()
    C.export_all()
    assert (tmp_path / "a" / "export" / "projects.jsonl").read_text() == first


def test_description_skips_html_and_table_markup(tmp_path):
    """A README that opens with a <div> banner must not become the description."""
    from lattice import execution
    proj = tmp_path / "p"; proj.mkdir()
    (proj / "README.md").write_text(
        '<div align="center">\n\n# Lattice\n\n### One shared brain.\n')
    assert execution.describe(proj) == "Lattice"


def test_promotion_writes_a_loadable_claude_code_skill(tmp_path, monkeypatch):
    """A promoted skill must land where Claude Code loads from, with frontmatter."""
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(C, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    proj = tmp_path / "demo"; proj.mkdir()
    C.init(); pid = C.register_project(proj)["id"]
    conn = C.open_db()
    storage.add_pattern(conn, pid, "scan before committing")
    storage.add_pattern(conn, pid, "scan before committing")
    conn.close()

    r = C.promote_pattern("scan before committing")
    from pathlib import Path
    f = Path(r["path"])
    assert f.name == "SKILL.md" and f.parent.parent == tmp_path / "skills"
    body = f.read_text()
    assert body.startswith("---\nname: scan-before-committing")
    assert "description:" in body


def test_skill_scan_registers_existing_skills(tmp_path, monkeypatch):
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(C, "SKILLS_DIR", tmp_path / "skills")
    d = tmp_path / "skills" / "my-skill"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: my-skill\ndescription: Does a thing.\n---\n\n# My skill\n")
    C.init()
    assert C.scan_skills()["found"] == ["my-skill"]
    conn = C.open_db()
    rows = storage.list_skills(conn); conn.close()
    assert len(rows) == 1 and rows[0]["description"] == "Does a thing."


def test_idea_frontmatter_ignores_inline_comments(tmp_path, monkeypatch):
    from lattice import core as C
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    (tmp_path / "ideas").mkdir()
    (tmp_path / "ideas" / "x.md").write_text(
        "---\nidea: A thing\nside: work   # because reasons\nstatus: idea\n---\n\nbody\n")
    ideas = C.list_ideas()
    assert ideas[0]["side"] == "work"
    assert ideas[0]["title"] == "A thing"


def test_skill_scan_drops_skills_whose_file_is_gone(tmp_path, monkeypatch):
    """A registered skill with no file is not a skill — otherwise `status`
    reports a count that does not match what Claude Code can actually load."""
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(C, "SKILLS_DIR", tmp_path / "skills")
    d = tmp_path / "skills" / "real"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: real\ndescription: Exists.\n---\n")
    C.init(); C.scan_skills()

    conn = C.open_db()
    storage.add_skill(conn, "ghost", "gone", tmp_path / "skills" / "ghost" / "SKILL.md")
    assert len(storage.list_skills(conn)) == 2
    conn.close()

    r = C.scan_skills()
    assert r["removed"] == ["ghost"]
    conn = C.open_db()
    names = [s["name"] for s in storage.list_skills(conn)]; conn.close()
    assert names == ["real"]


def test_skills_are_not_synced_between_machines(tmp_path, monkeypatch):
    """Skills are files installed per machine and their paths are machine-specific.
    Syncing them would propagate dead paths, so they stay out of the export."""
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "a"))
    monkeypatch.setattr(C, "SKILLS_DIR", tmp_path / "skills")
    d = tmp_path / "skills" / "a-skill"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: a-skill\ndescription: Does a thing.\n---\n")
    C.init(); C.scan_skills()

    counts = C.export_all()["counts"]
    assert "skills" not in counts
    assert not (tmp_path / "a" / "export" / "skills.jsonl").exists()


def test_thinner_machine_cannot_wipe_shared_knowledge(tmp_path, monkeypatch):
    """The 2026-08-19 bug: the Mac Studio exported its own empty state and
    committed it before pulling, deleting what the laptop knew. Import must run
    before export so the export is always a superset."""
    import shutil
    from lattice import core as C

    # rich machine
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "rich"))
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    proj = tmp_path / "demo"; proj.mkdir()
    C.init(); pid = C.register_project(proj)["id"]
    conn = C.open_db()
    storage.add_task(conn, pid, "hard-won task")
    conn.close()
    assert C.export_all()["counts"]["tasks"] == 1

    # thin machine receives that export and has nothing of its own
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "thin"))
    C.init()
    shutil.copytree(tmp_path / "rich" / "export", tmp_path / "thin" / "export")

    # correct order: import first, then export
    C.import_all()
    counts = C.export_all()["counts"]

    assert counts["tasks"] == 1, "export must be a superset, never a wipe"
    body = (tmp_path / "thin" / "export" / "tasks.jsonl").read_text()
    assert "hard-won task" in body


def test_memory_writes_markdown_to_the_single_store(tmp_path, monkeypatch):
    """Memory is markdown in Claude Code's auto-memory directory — one store,
    loaded every session for free, synced because it sits in the repo."""
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(C, "MEMORY_DIR", tmp_path / "memory")
    C.init()

    r = C.memory_add("demo", "The venv must use python.org 3.13, not Homebrew's.")
    from pathlib import Path
    f = Path(r["path"])
    assert f.exists() and f.suffix == ".md"
    body = f.read_text()
    assert body.startswith("---\nname: ")
    assert "python.org 3.13" in body
    assert "**Project:** demo" in body
    # indexed, so it is discoverable without opening every file
    assert f.name in (tmp_path / "memory" / "MEMORY.md").read_text()


def test_memory_search_finds_it_and_never_duplicates_filenames(tmp_path, monkeypatch):
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(C, "MEMORY_DIR", tmp_path / "memory")
    C.init()
    a = C.memory_add(None, "CuraEngine lives inside Cura.app on macOS.")
    b = C.memory_add(None, "CuraEngine lives inside Cura.app on macOS.")
    assert a["path"] != b["path"], "a second identical note must not overwrite the first"

    hits = C.memory_search("CuraEngine")
    assert len(hits) == 2
    assert C.memory_search("nothing-like-this") == []


def test_status_counts_the_markdown_store(tmp_path, monkeypatch):
    from lattice import core as C
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(C, "MEMORY_DIR", tmp_path / "memory")
    C.init()
    assert C.status()["memories"] == 0
    C.memory_add(None, "one fact")
    C.memory_add(None, "another fact")
    assert C.status()["memories"] == 2


def test_task_completion_propagates_between_machines(tmp_path, monkeypatch):
    """Closing a task on one machine must close it on the other. Import skips
    rows it already has, so without an explicit rule a finished task stays open
    forever and the task list lies."""
    import shutil
    from lattice import core as C

    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "a"))
    monkeypatch.setattr(C, "IDEAS_DIR", tmp_path / "ideas")
    monkeypatch.setattr(C, "MEMORY_DIR", tmp_path / "mem")
    proj = tmp_path / "demo"; proj.mkdir()
    C.init(); pid = C.register_project(proj)["id"]
    conn = C.open_db(); tid = storage.add_task(conn, pid, "ship the thing"); conn.close()
    C.export_all()

    # machine B receives it while still open
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "b"))
    C.init()
    shutil.copytree(tmp_path / "a" / "export", tmp_path / "b" / "export")
    C.import_all()
    assert len(C.status()["open_tasks"] * [0]) == 1 or C.status()["open_tasks"] == 1

    # A completes it and republishes
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "a"))
    conn = C.open_db(); storage.complete_task(conn, tid); conn.close()
    C.export_all()

    # B must pick the completion up
    monkeypatch.setenv("LATTICE_HOME", str(tmp_path / "b"))
    shutil.rmtree(tmp_path / "b" / "export")
    shutil.copytree(tmp_path / "a" / "export", tmp_path / "b" / "export")
    r = C.import_all()
    assert r["completed"].get("tasks") == 1
    assert C.status()["open_tasks"] == 0
