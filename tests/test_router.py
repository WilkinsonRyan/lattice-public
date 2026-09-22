"""The budget is the whole point of T0, so it gets a test that fails loudly."""

from lattice import router


def test_t0_is_inside_budget():
    r = router.build(check_only=True)
    assert not r["over_budget"], (
        f"T0 is {r['tokens']} tokens against a {r['budget']} budget. "
        "Demote something to T1 — do not raise the budget."
    )


def test_t0_holds_addresses_not_content():
    """No preference bullet text may leak into the always-loaded layer."""
    text = router.build(check_only=True)["text"]
    bullets = [
        line for line in router.prefs_path().read_text().splitlines()
        if line.startswith("[")
    ]
    assert bullets, "preference corpus is empty"
    for line in bullets:
        body = line.split("] ", 1)[1] if "] " in line else line
        assert body[:40] not in text, f"bullet text leaked into T0: {line[:60]}"


def test_every_pref_domain_declares_a_trigger():
    for d in router.read_prefs():
        assert d["when"], f"domain {d['domain']} has no trigger"


def test_estimate_does_not_undercount():
    """Measured 2026-09-09: real o200k_base count on live T0 was 422 while the
    dependency-free estimate said 436. The estimate must stay conservative — an
    optimistic estimator silently lets the budget slip."""
    r = router.build(check_only=True)
    assert r["tokens"] >= len(r["text"]) / 4.0
