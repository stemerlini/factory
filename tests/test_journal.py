import datetime as dt

from factory import decisions, gates, home, journal, runs


def test_journal_tells_the_story_and_notices_gate_passes(make):
    p = make()
    fee = p.element("fee")
    decisions.ask(p, fee, "ADC architecture", ["SAR", "Sigma-delta"], "A")
    runs.execute(p, p.study("S001"))
    gates_of = {e.path: gates.gates(p, e) for e in p.elements()}
    titles = {e.path: e.title for e in p.elements()}
    assert journal.passed_gates(p, gates_of, titles) == []  # the first look only remembers

    (fee.dir / "concept").mkdir(exist_ok=True)
    (fee.dir / "concept/sketch.md").write_text("A charge amplifier per channel.\n")
    gates_of = {e.path: gates.gates(p, e) for e in p.elements()}
    passed = journal.passed_gates(p, gates_of, titles)
    assert [e.title for e in passed] == ["Front-end electronics passed Concept"]
    assert journal.passed_gates(p, gates_of, titles) == []

    kinds = [e.kind for e in journal.events(p)]
    assert {"approve", "decision", "run", "gate"} <= set(kinds)
    assert [e.kind for e in journal.events(p, element="fee")] == ["gate", "decision"]
    h, _ = home.load(p)
    assert any(i.kind == "decision" and i.target == "fee/D001" for i in h.waiting)
    assert 0 < h.progress < 1

    today = dt.date(2026, 11, 11)
    days = [journal.Event(f"2026-11-{d:02d}T10:00:00+00:00", "note", "x") for d in (8, 9, 10)]
    assert journal.streak(days, today) == 3  # today is young: yesterday still counts
    assert journal.streak(days[:2], today) == 0
