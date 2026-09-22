"""The page is one file, so one bad escape disables every button on it.

A Python f-string turns `\\n` in the source into a real newline before the
browser ever sees it, which produced a regex spanning two lines and a
SyntaxError that killed the whole script — Info, Approve, Wait, Deny, all of it,
silently. Nothing about the page looked wrong. These tests render it and check
the JavaScript actually parses.
"""

import re
import shutil
import subprocess

import pytest

from lattice import page, server


def _script() -> str:
    html = page.render(server.token())
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    assert m, "the page rendered without its script block"
    return m.group(1)


def test_page_javascript_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = _script()
    proc = subprocess.run([node, "--check", "-"], input=js, text=True,
                          capture_output=True)
    assert proc.returncode == 0, f"emitted JavaScript does not parse:\n{proc.stderr}"


def test_no_raw_newline_inside_a_regex_literal():
    """The specific failure, caught without needing node."""
    for line in _script().splitlines():
        slashes = line.count("/")
        if "replace(/" in line:
            assert slashes >= 2, f"regex literal left open on: {line.strip()[:70]}"


def test_every_control_has_a_handler():
    """A button whose action nothing listens for is a dead control."""
    html = page.render(server.token())
    js = _script()
    actions = set(re.findall(r'data-do="(\w+)"', html))
    for action in actions:
        assert action in js, f'nothing in the script handles data-do="{action}"'
    # Sweep actions are dispatched generically off dataset.sweep, so assert the
    # dispatcher exists rather than hunting for each literal — "dismiss" is
    # handled by a fallthrough and never appears by name.
    assert "dataset.sweep" in js, "nothing dispatches the sweep controls"
    assert "/api/sweep" in js, "the sweep controls post nowhere"
