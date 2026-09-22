"""The property that matters: regenerating a brief never touches the half a
human wrote. Everything else about briefs is cosmetic; this is not."""

from lattice import briefs

HAND = """# demo — project context

**work · active**

## What this is
A thing.

## Traps and settled facts
- Never merge column M with the band.
"""


def test_marked_file_splits_at_the_marker():
    text = HAND + "\n" + briefs.MARKER + "\n\n## Open work\n- `[1]` do it\n"
    head, tail = briefs.split_brief(text)
    assert head == HAND.rstrip()
    # The marker is the boundary, so it belongs to neither half; generated_tail
    # re-emits it.
    assert tail.startswith("## Open work")


def test_unmarked_file_splits_at_the_earliest_legacy_heading():
    # "## Decisions on record" comes before "## Related memory" in the file but
    # after it in LEGACY_BOUNDARIES — position in the file decides, not order
    # in the tuple.
    text = HAND + "\n## Decisions on record\n- chose X\n\n## Related memory\n- a.md\n"
    head, tail = briefs.split_brief(text)
    assert head == HAND.rstrip()
    assert tail.startswith("## Decisions on record")


def test_file_with_no_generated_half_is_all_hand_written():
    head, tail = briefs.split_brief(HAND)
    assert head == HAND.rstrip()
    assert tail == ""


def test_remigrate_ignores_a_marker_placed_too_late():
    # The failure this flag exists for: a generated section stranded above the
    # marker, so a plain refresh would keep duplicating it.
    bad = (HAND + "\n## Decisions on record\n- chose X\n\n"
           + briefs.MARKER + "\n\n## Open work\n- `[1]` do it\n")
    head, _ = briefs.split_brief(bad)
    assert "Decisions on record" in head          # the bug
    head, tail = briefs.split_brief(bad, remigrate=True)
    assert "Decisions on record" not in head      # the repair
    assert head == HAND.rstrip()


def test_hand_written_half_survives_a_round_trip():
    text = HAND + "\n" + briefs.MARKER + "\n\n## Open work\n- `[1]` do it\n"
    head, _ = briefs.split_brief(text)
    rebuilt = head + "\n\n" + briefs.MARKER + "\n\n## Open work\n_None open._\n"
    assert briefs.split_brief(rebuilt)[0] == head


def test_est_tokens_is_in_the_right_order_of_magnitude():
    assert briefs.est_tokens("one two three") == 4
    assert briefs.est_tokens("") == 0
