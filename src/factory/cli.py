"""The ``factory`` command. Every verb has ``--json`` so agents can drive it."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import (
    __version__,
    agent,
    architecture,
    config,
    decisions,
    gates,
    guide,
    home,
    journal,
    pipeline,
    review,
    runs,
    trace,
)
from .model import GATES
from .modules import registry
from .project import Project, ProjectError, create, templates


def out(args: argparse.Namespace, data: Any, text: str) -> None:
    print(json.dumps(data, indent=2, default=str) if args.json else text)


def load(args: argparse.Namespace) -> Project:
    return Project.load(args.project)


# --- verbs ---------------------------------------------------------------------------------


def cmd_new(args):
    target = Path(args.path) if "/" in args.path else config.projects_root() / args.path
    p = create(target, args.template, args.name, args.id, git=not args.no_git)
    config.remember(p.root)
    out(args, {"root": p.root, "id": p.id, "template": p.template},
        f"Created {p.name} ({p.id}) from {p.template} in {p.root}")


def cmd_templates(args):
    t = templates()
    out(args, t, "\n".join(f"{k:20} {v}" for k, v in t.items()))


def element_of(p: Project, args) -> object | None:
    path = getattr(args, "element", None)
    return None if path is None else p.element(path)


def cmd_status(args):
    p = load(args)
    states = pipeline.states(p)
    tr = trace.trace(p, states)
    commit, dirty = p.commit()
    elements = []
    for e in p.elements():
        g = gates.gates(p, e, states, tr)
        cur = gates.current(g)
        elements.append({"path": e.path, "title": e.title, "kind": e.kind,
                         "gates": {x.gate: x.status for x in g},
                         "current": cur.gate if cur else None})
    counts = {s: sum(t.status == s for t in tr) for s in trace.MARKS}
    data = {"project": {"id": p.id, "name": p.name, "root": p.root, "template": p.template},
            "git": {"branch": p.branch(), "commit": commit, "dirty": dirty},
            "elements": elements, "trace": counts, "problems": trace.problems(p)}
    lines = [f"{p.name} ({p.id})  {p.branch() or '-'} @ {commit or 'no commits'}"
             + (" (uncommitted changes)" if dirty else "")]
    for e in elements:
        depth = e["path"].count("/") + (1 if e["path"] else 0)
        marks = "".join(gates.MARKS[s] for s in e["gates"].values())
        icon = registry.kind(e["kind"]).icon
        lines.append(f"  {'  ' * depth}{icon} {e['title']:28} {marks}  → {e['current'] or 'done'}")
    lines.append("  Trace: " + " · ".join(f"{n} {k}" for k, n in counts.items()))
    lines += [f"  ! {x}" for x in data["problems"]]
    out(args, data, "\n".join(lines))


def cmd_gates(args):
    p = load(args)
    e = p.element(args.element or "")
    g = gates.gates(p, e)
    checks = guide.checklist(p, e)
    lines = [e.title]
    for x in g:
        lines.append(f"  {gates.MARKS[x.status]} {x.title:10} {x.detail}")
        if x.status not in (gates.DONE, gates.SKIPPED):
            lines += [f"      {'✓' if c.done else '□'} {c.label}"
                      + (f"  ({c.detail})" if c.detail else "") for c in checks[x.gate]]
    out(args, [{**x.__dict__, "checks": [{"label": c.label, "done": c.done, "detail": c.detail}
                                          for c in checks[x.gate]]} for x in g],
        "\n".join(lines))


def cmd_req(args):
    p = load(args)
    e = element_of(p, args)
    tr = [t for t in trace.trace(p) if e is None or t.requirement.element == e.path]
    data = [{"id": t.requirement.id, "element": t.requirement.element,
             "title": t.requirement.title, "status": t.status,
             "parent": t.requirement.parent, "children": t.children,
             "allocated": list(t.requirement.allocated),
             "criterion": str(t.requirement.criterion or ""), "studies": t.studies,
             "value": t.value.__dict__ if t.value else None, "reason": t.reason} for t in tr]
    out(args, data, "\n".join(
        f"{trace.MARKS[t.status]} {t.requirement.id:8} {t.requirement.title:34} "
        f"{str(t.requirement.criterion or ''):26} {t.reason}" for t in tr))


def cmd_study_list(args):
    p = load(args)
    e = element_of(p, args)
    states = [s for s in pipeline.states(p).values() if e is None or s.study.element == e.path]
    data = [{"key": s.study.key, "title": s.study.title, "task": s.study.task,
             "gate": s.study.gate, "status": s.status, "reason": s.reason,
             "last_run": s.last.id if s.last else None} for s in states]
    out(args, data, "\n".join(
        f"{pipeline.MARKS[s.status]} {s.study.key:18} {s.study.task:16} {s.study.title:36} "
        f"{s.reason}" for s in states))


def cmd_study_show(args):
    p = load(args)
    st = pipeline.states(p)[p.study(args.study).key]
    s = st.study
    data = {"key": s.key, "title": s.title, "task": s.task, "gate": s.gate, "dir": s.dir,
            "params": s.params, "inputs": s.inputs, "verifies": s.verifies,
            "status": st.status, "reason": st.reason, "fingerprint": st.fingerprint,
            "upstream": s.upstream(), "downstream": pipeline.downstream(p.studies(), s.key),
            "good_run": st.good.to_dict() if st.good else None}
    text = "\n".join(f"{k:12} {v}" for k, v in data.items() if k != "good_run")
    out(args, data, text)


def cmd_arch(args):
    p = load(args)
    e = p.element(args.element or "")
    arch = architecture.load(e.dir)
    if arch is None:
        raise ProjectError(f"{e.title} has no {architecture.FILE}")
    if args.d2:
        print(arch.to_d2(), end="")
        return
    if args.canvas:
        Path(args.canvas).write_text(arch.to_canvas())
        out(args, {"canvas": args.canvas}, f"Wrote {args.canvas}")
        return
    text, _ = architecture.render(arch)
    data = {"blocks": [b.__dict__ for b in arch.blocks],
            "interfaces": [i.__dict__ for i in arch.interfaces],
            "allocate": arch.allocate,
            "problems": arch.problems({r.id for r in p.requirements(e)})}
    lines = [text, ""]
    lines += [f"  {i.src} → {i.dst}  {i.type}  {i.label}" for i in arch.interfaces]
    lines += [f"  ! {x}" for x in data["problems"]]
    out(args, data, "\n".join(lines))


def cmd_decompose(args):
    p = load(args)
    folder = architecture.decompose(p, p.element(args.element or ""), args.block)
    out(args, {"element": folder}, f"Created {folder.relative_to(p.root)}")


def cmd_lock(args):
    p = load(args)
    tag = gates.lock(p, p.element(args.element or ""), force=args.force)
    out(args, {"tag": tag}, f"Locked: {tag}")


def cmd_study_run(args):
    p = load(args)
    study = p.study(args.study)
    if args.wait:
        run = runs.execute(p, study)
        if not args.json:
            print(runs.run_dir(p, run).joinpath("log.txt").read_text(), end="")
        out(args, run.to_dict(), f"{run.id}: {run.status} {run.error}".rstrip())
        return 0 if run.status == "done" else 1
    run_id = runs.start(p, study)
    out(args, {"run": run_id, "log": p.store / study.key / run_id / "log.txt"},
        f"Started {study.key} run {run_id}")


def cmd_runs(args):
    p = load(args)
    studies = [p.study(args.study)] if args.study else p.studies()
    found = sorted((r for s in studies for r in runs.history(p, s)), key=lambda r: r.started)
    out(args, [r.to_dict() for r in found], "\n".join(
        f"{r.id}  {r.study:18} {r.status:9} {r.commit or '-':12}{'+' if r.dirty else ' '} "
        + " ".join(f"{k}={m.value:g}{m.unit and ' ' + m.unit}" for k, m in r.metrics.items())
        for r in found))


def cmd_log(args):
    p = load(args)
    _, run = runs.get(p, args.run)
    print(runs.run_dir(p, run).joinpath("log.txt").read_text(), end="")


def cmd_cancel(args):
    ok = runs.cancel(load(args), args.run)
    out(args, {"cancelled": ok}, "Cancelled" if ok else "Not running")


def cmd_modules(args):
    try:
        enabled = set(load(args).modules)
    except ProjectError:
        enabled = set()
    data = []
    for m in registry.all_modules().values():
        data.append({
            "id": m.id, "title": m.title, "enabled": m.id in enabled,
            "tasks": [f"{m.id}.{t.id}" if t.id != "main" else m.id for t in m.tasks],
            "tools": [t.id for t in m.tools],
            "health": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in m.health()],
        })
    lines = []
    for d in data:
        lines.append(f"{'●' if d['enabled'] else ' '} {d['id']:10} {d['title']:20} "
                     f"tasks: {', '.join(d['tasks']) or '-'}")
        lines += [f"      {'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}"
                  for c in d["health"]]
    out(args, data, "\n".join(lines))


def problems(p: Project) -> list[str]:
    """Everything that would stop the project from loading, running or tracing cleanly."""
    found: list[str] = []
    try:
        elements = p.elements()
    except ProjectError as e:
        return [str(e)]
    enabled = set(p.modules)
    for e in elements:
        try:
            p.requirements(e)
        except ProjectError as err:
            found.append(str(err))
        try:
            arch = architecture.load(e.dir)
        except Exception as err:  # noqa: BLE001 - report, do not crash
            found.append(f"{e.dir / architecture.FILE}: {err}")
            arch = None
        if arch:
            try:
                ids = {r.id for r in p.requirements(e)}
            except ProjectError:
                ids = None
            found += [f"{e.path or 'system'} architecture: {x}" for x in arch.problems(ids)]
            names = {c.name for c in e.children}
            found += [f"{e.path or 'system'}: block {b.id!r} names element {b.element!r}, "
                      f"which does not exist yet (factory decompose {b.id})"
                      for b in arch.blocks if b.element and b.element not in names]
        try:
            studies = p.studies(e)
        except ProjectError as err:
            found.append(str(err))
            continue
        for s in studies:
            try:
                mod, task = registry.find_task(s.task)
                task.resolve_params(s.params)
            except (LookupError, ValueError, TypeError) as err:
                found.append(f"{s.key}: {err}")
                continue
            if mod.id not in enabled:
                found.append(f"{s.key}: module {mod.id!r} is not enabled in factory.toml")
    try:
        pipeline.order(p.studies())
        found += trace.problems(p)
    except ProjectError as err:
        found.append(str(err))
    return found


def cmd_check(args):
    found = problems(load(args))
    out(args, {"ok": not found, "problems": found},
        "\n".join(f"! {x}" for x in found) or "✓ No problems.")
    return 1 if found else 0


def cmd_tasks(args):
    data = []
    for m in registry.all_modules().values():
        for t in m.tasks:
            data.append({
                "task": m.id if t.id == "main" else f"{m.id}.{t.id}",
                "title": t.title, "toolchain": t.toolchain if isinstance(t.toolchain, str) else
                "depends on params", "outputs": list(t.outputs),
                "params": [{"name": x.name, "type": x.type.__name__, "default": x.default,
                            "required": x.required, "help": x.help} for x in t.params],
            })
    kinds = [{"kind": k.id, "title": k.title, "prefix": k.prefix, "modules": list(k.modules)}
             for k in registry.kinds().values()]
    lines = [f"{d['task']:16} {d['title']:24} "
             + ", ".join(f"{x['name']}={x['default']!r}" for x in d["params"]) for d in data]
    lines += ["", "Element kinds: " + ", ".join(f"{k['kind']} ({k['prefix'] or '-'})"
                                               for k in kinds)]
    out(args, {"tasks": data, "kinds": kinds}, "\n".join(lines))


def decision_dict(d: decisions.Decision) -> dict:
    return {"key": d.key, "title": d.title, "status": d.status, "raised_by": d.raised_by,
            "date": d.date, "options": [o.__dict__ for o in d.options],
            "recommended": d.recommended, "chosen": d.chosen, "decided": d.decided,
            "context": d.context, "verdict": d.verdict, "path": d.path}


def cmd_decisions(args):
    p = load(args)
    found = decisions.all_decisions(p, element_of(p, args))
    if args.open:
        found = [d for d in found if d.status == "proposed"]
    marks = {"proposed": "?", "accepted": "✓", "rejected": "✗"}
    out(args, [decision_dict(d) for d in found], "\n".join(
        f"{marks[d.status]} {d.key:14} {d.title:44} "
        + (f"→ {d.chosen}" if d.chosen else f"recommends {d.recommended or '-'}")
        for d in found) or "No decisions.")


def cmd_ask(args):
    p = load(args)
    d = decisions.ask(p, p.element(args.element or ""), args.title, args.option,
                      args.recommend, args.context or "", raised_by=args.by)
    out(args, decision_dict(d), f"Asked {d.key}: {d.title} ({d.path.relative_to(p.root)})")


def cmd_decide(args):
    p = load(args)
    d = decisions.decide(p, args.decision, args.verdict, args.option, args.note or "")
    out(args, decision_dict(d), f"{d.key}: {d.status}" + (f" ({d.chosen})" if d.chosen else ""))


def cmd_review(args):
    p = load(args)
    found = review.changes(p)
    data = {"title": review.title(p), "proposal": review.proposal(p),
            "changes": [{"path": c.path, "kind": c.kind, "element": c.element} for c in found],
            "open_decisions": [d.key for d in decisions.all_decisions(p)
                               if d.status == "proposed"]}
    lines = [f"Proposal: {data['title'] or '(none)'}"]
    lines += [f"  {c.kind:9} {c.element or 'system':14} {c.path}" for c in found]
    if data["open_decisions"]:
        lines.append("Open decisions: " + ", ".join(data["open_decisions"]))
    out(args, data, "\n".join(lines) if found or data["open_decisions"] else "Nothing to review.")


def cmd_approve(args):
    p = load(args)
    pending = review.changes(p)
    if args.element is not None:
        pending = [c for c in pending if c.element == args.element.strip("/")]
    paths = args.paths or [c.path for c in pending]
    message = args.message or review.title(p)
    head = review.approve(p, paths, message)
    out(args, {"commit": head, "paths": paths}, f"Approved {len(paths)} file(s) as {head}")


def cmd_reject(args):
    p = load(args)
    for path in args.paths:
        review.reject(p, path)
        journal.record(p, "reject", f"Discarded the change to {path}")
    out(args, {"rejected": args.paths}, f"Discarded {len(args.paths)} change(s)")


def cmd_agent(args):
    p = load(args)
    e = p.element(args.element or "")
    task = args.task or ""
    if args.print:
        text = agent.brief(p, e, task, args.gate)
        out(args, {"brief": text}, text)
        return
    if args.sessions:
        found, live = agent.sessions(p), agent.running(p)
        out(args, {"running": live, "sessions": [
            {"id": s.id, "modified": dt.datetime.fromtimestamp(s.modified).isoformat(
                timespec="seconds"), "prompt": s.prompt} for s in found]},
            "\n".join([f"running: pid {', '.join(map(str, live))}"] if live else [])
            + "".join(f"\n{s.id[:8]}  {dt.datetime.fromtimestamp(s.modified):%Y-%m-%d %H:%M}  "
                      f"{s.prompt[:70]}" for s in found).lstrip("\n")
            or "No agent sessions yet")
        return
    if args.resume or args.fork:
        found = agent.sessions(p)
        if not found:
            raise ProjectError("no saved agent session in this project; start one with "
                               "`factory agent`")
        argv = agent.resume_command(found[0], fork=args.fork)
        argv = agent.window(p, argv) if args.window else argv
    else:
        argv = (agent.window_command(p, e, task, args.gate) if args.window
                else agent.command(p, e, task, args.gate))
    for rel in agent.ensure_files(p):
        print(f"Added {rel} (agent guardrails; approve it in the Review tab)", file=sys.stderr)
    if args.window:
        subprocess.Popen(argv, cwd=p.root, start_new_session=True)
        return
    os.chdir(p.root)
    os.execvp(argv[0], argv)


def event_dict(e: journal.Event) -> dict:
    return {"time": e.time, "kind": e.kind, "title": e.title, "element": e.element,
            "detail": e.detail}


def cmd_home(args):
    p = load(args)
    h, _ = home.load(p)
    data = {"waiting": [i.__dict__ for i in h.waiting], "next": [s.__dict__ for s in h.steps],
            "journey": [{"element": r.element, "title": r.title, "gates": r.statuses}
                        for r in h.journey],
            "progress": round(h.progress, 3), "streak": h.streak,
            "recent": [event_dict(e) for e in h.recent]}
    lines = [f"{p.name}: {h.progress:.0%} of the way"
             + (f" · {h.streak} day(s) in a row" if h.streak > 1 else "")]
    lines += ["", "Waiting on you"] + ([f"  {i.text}" for i in h.waiting] or ["  nothing"])
    lines += ["", "Next"] + [f"  {s.title:28} {s.gate_title}: {s.detail}" for s in h.steps]
    lines += ["", "Journey"] + [f"  {'  ' * r.depth}{r.title:{28 - 2 * r.depth}} "
                                f"{home.journey_bar(r.statuses)}" for r in h.journey]
    if h.recent:
        lines += ["", "Recently"] + [f"  {e.ago:>10}  {e.title}"
                                     for e in h.recent]
    out(args, data, "\n".join(lines))


def cmd_next(args):
    p = load(args)
    h, _ = home.load(p)
    steps = [s for s in h.steps if args.element is None or s.element == args.element.strip("/")]
    out(args, [s.__dict__ for s in steps], "\n".join(
        f"{s.title:28} {s.gate_title}: {s.detail}" for s in steps) or "Every gate is done.")


def cmd_journal(args):
    p = load(args)
    since = None
    if args.since:
        since = dt.datetime.now().astimezone() - dt.timedelta(days=args.since)
    found = journal.events(p, since, args.element, args.limit)
    if args.md:
        print(journal.markdown(p, found), end="")
        return
    out(args, [event_dict(e) for e in found], "\n".join(
        f"{e.when:%Y-%m-%d %H:%M}  {journal.MARKS.get(e.kind, '·')} {e.title}"
        + (f"  ({e.element})" if e.element else "") for e in found) or "Nothing yet.")


def cmd_projects(args):
    found = config.known_projects()
    out(args, [str(p) for p in found], "\n".join(map(str, found)) or "No projects yet.")


def cmd_tui(args):
    from .tui.app import FactoryApp

    root = None
    try:
        root = load(args).root
    except ProjectError:
        pass
    FactoryApp(root).run()


# --- parser --------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="factory", description=__doc__)
    ap.add_argument("--version", action="version", version=f"factory {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--project", default=".", help="project folder (default: here)")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help, **kw):
        p = sub.add_parser(name, parents=[common], help=help, **kw)
        p.set_defaults(fn=fn)
        return p

    p = add("new", cmd_new, "create a project from a template")
    p.add_argument("path", help="folder, or a name created under the projects root")
    p.add_argument("-t", "--template", default="hardware",
                   help="hardware | software | publication (or a template folder)")
    p.add_argument("--name")
    p.add_argument("--id")
    p.add_argument("--no-git", action="store_true")
    add("templates", cmd_templates, "list project templates")
    add("projects", cmd_projects, "list known projects")
    add("status", cmd_status, "project overview")
    add("home", cmd_home, "what now: waiting on you, next steps, journey, recent events")
    p = add("next", cmd_next, "each element's next step")
    p.add_argument("-e", "--element", help="only this element")
    p = add("journal", cmd_journal, "the project's timeline, newest first")
    p.add_argument("-e", "--element", help="only this element")
    p.add_argument("--since", type=int, metavar="DAYS", help="only the last DAYS days")
    p.add_argument("-n", "--limit", type=int, help="at most this many entries")
    p.add_argument("--md", action="store_true", help="a Markdown handover note")
    for name in ("req", "trace"):
        p = add(name, cmd_req, "requirements with their trace status")
        p.add_argument("-e", "--element", help="only this element")
    p = add("studies", cmd_study_list, "list studies with status")
    p.add_argument("-e", "--element", help="only this element")
    add("elements", cmd_status, "same as status")
    p = add("gates", cmd_gates, "the loop for one element")
    p.add_argument("element", nargs="?", help="element path (default: the system)")
    p = add("arch", cmd_arch, "an element's block diagram")
    p.add_argument("element", nargs="?")
    p.add_argument("--d2", action="store_true", help="print D2 source")
    p.add_argument("--canvas", help="write an Obsidian Canvas file")
    p = add("decompose", cmd_decompose, "turn a block into a child element")
    p.add_argument("block")
    p.add_argument("-e", "--element", help="parent element (default: the system)")
    p = add("lock", cmd_lock, "baseline an element (git tag)")
    p.add_argument("element", nargs="?")
    p.add_argument("--force", action="store_true", help="lock even if analysis is not proven")
    p = add("show", cmd_study_show, "show one study")
    p.add_argument("study")
    p = add("run", cmd_study_run, "run a study (in the background unless --wait)")
    p.add_argument("study")
    p.add_argument("-w", "--wait", action="store_true", help="run in the foreground")
    p = add("runs", cmd_runs, "run history")
    p.add_argument("study", nargs="?")
    p = add("log", cmd_log, "print a run's log")
    p.add_argument("run")
    p = add("cancel", cmd_cancel, "stop a running run")
    p.add_argument("run")
    add("modules", cmd_modules, "modules, toolchains and health")
    add("check", cmd_check, "validate the whole project (exit 1 if anything is wrong)")
    p = add("decisions", cmd_decisions, "decision records")
    p.add_argument("-e", "--element", help="only this element")
    p.add_argument("--open", action="store_true", help="only those waiting for a decision")
    p = add("ask", cmd_ask, "record a question for the human (a proposed decision)")
    p.add_argument("title")
    p.add_argument("-o", "--option", action="append", required=True,
                   help='an option, "Title" or "Title: summary" (repeat; first is A)')
    p.add_argument("-r", "--recommend", help="recommended option id (A, B…)")
    p.add_argument("-c", "--context", help="why it matters, evidence, trade-offs (Markdown)")
    p.add_argument("-e", "--element", help="element it belongs to (default: the system)")
    p.add_argument("--by", default="agent", help="who raises it (default: agent)")
    p = add("decide", cmd_decide, "answer a decision (the human's call)")
    p.add_argument("decision", help="decision key, e.g. D001 or fee/D002")
    p.add_argument("verdict", choices=("accept", "reject"))
    p.add_argument("--option", help="option to accept (default: the recommended one)")
    p.add_argument("--note", help="your reasoning, kept in the record")
    add("review", cmd_review, "pending changes and the agent's proposal")
    p = add("approve", cmd_approve, "commit pending changes (all, an element's, or paths)")
    p.add_argument("paths", nargs="*")
    p.add_argument("-e", "--element", help="only this element's changes")
    p.add_argument("-m", "--message", help="commit message (default: the proposal title)")
    p = add("reject", cmd_reject, "discard pending changes to these files")
    p.add_argument("paths", nargs="+")
    p = add("agent", cmd_agent, "start a Claude Code session on an element")
    p.add_argument("element", nargs="?", help="element path (default: the system)")
    p.add_argument("-t", "--task", help="what to work on (default: summarise and propose)")
    p.add_argument("-g", "--gate", choices=[g for g, _ in GATES],
                   help="work on this gate: its open items and playbook join the brief")
    p.add_argument("-w", "--window", action="store_true", help="open in a new window")
    p.add_argument("--print", action="store_true", help="only print the brief")
    p.add_argument("-c", "--continue", dest="resume", action="store_true",
                   help="continue the last session (e.g. after a usage limit reset)")
    p.add_argument("--fork", action="store_true",
                   help="continue a copy of the last session, keeping the original")
    p.add_argument("--sessions", action="store_true", help="list saved and running sessions")
    add("tasks", cmd_tasks, "tasks studies can use, with their parameters; element kinds")
    add("tui", cmd_tui, "open the TUI (default)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args = ap.parse_args(["tui", *(argv or sys.argv[1:])])
    try:
        return args.fn(args) or 0
    except (ProjectError, LookupError, ValueError) as e:
        print(f"factory: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
