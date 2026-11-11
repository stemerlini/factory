"""Publications: a paper or report as an element. The outline is its architecture, the
claims are its requirements, studies produce its numbers and figures, and the build
produces the PDF (pages and words become metrics, so a page limit is a requirement)."""

from __future__ import annotations

import re
import shutil
import subprocess

from ..model import Metric
from .api import Check, Command, ElementKind, FileKind, Module, Param, RunContext, Task, Tool

ENGINES = {
    "latexmk": lambda src, out: ["latexmk", "-pdf", "-interaction=nonstopmode",
                                 "-halt-on-error", f"-outdir={out}", src.name],
    "typst": lambda src, out: ["typst", "compile", src.name, str(out / (src.stem + ".pdf"))],
    "pandoc": lambda src, out: ["pandoc", src.name, "--citeproc", "-o",
                                str(out / (src.stem + ".pdf"))],
}


def build_command(ctx: RunContext) -> Command:
    engine = ctx.params["engine"]
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r} (use {', '.join(ENGINES)})")
    src = ctx.element_dir / ctx.params["source"]
    return Command(ENGINES[engine](src, ctx.outputs_dir), cwd=src.parent)


def page_count(pdf) -> int:
    """pdfinfo when present; else count page objects (misses compressed object streams)."""
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
        return int(re.search(r"(?m)^Pages:\s+(\d+)", out).group(1))
    except (OSError, AttributeError):
        return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


def build_collect(ctx: RunContext) -> dict[str, Metric]:
    src = ctx.element_dir / ctx.params["source"]
    pdf = ctx.outputs_dir / (src.stem + ".pdf")
    found = {}
    if pdf.is_file():
        found["pages"] = Metric(page_count(pdf))
        out = ctx.element_dir / "out"
        out.mkdir(exist_ok=True)
        shutil.copy2(pdf, out / pdf.name)  # the deliverable, for the Deliver gate
    text = src.read_text(errors="replace")
    text = re.sub(r"(?m)%.*$", "", text)  # LaTeX comments
    text = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", text)  # commands
    found["words"] = Metric(len(re.findall(r"[A-Za-z][A-Za-z'-]+", text)))
    return found


def checks() -> list[Check]:
    found = []
    for exe, pkg in (("latexmk", "texlive-binextra texlive-latexextra"), ("typst", "typst"),
                     ("pandoc", "pandoc-cli")):
        path = shutil.which(exe)
        found.append(Check(exe, bool(path), path or f"not installed (pacman -S {pkg})"))
    return found


module = Module(
    id="publication",
    title="Publication",
    description="Papers and reports: outline, claims, figures, build to PDF.",
    kinds=[ElementKind("manuscript", "Manuscript", "¶", ("publication", "script", "citadel"),
                       "PUB", "A paper, report or thesis chapter.")],
    tasks=[Task(id="build", title="Build the manuscript", command=build_command,
                collect=build_collect, outputs=("*.pdf",),
                params=(Param("source", str, "design/main.tex", help="main file"),
                        Param("engine", str, "latexmk", help="latexmk | typst | pandoc")))],
    tools=[Tool("open", "PDF viewer", lambda f: ["xdg-open", str(f)], globs=("*.pdf",))],
    file_kinds=[FileKind("LaTeX", ("*.tex",), "¶"), FileKind("Typst", ("*.typ",), "¶"),
                FileKind("Bibliography", ("*.bib",), "❡")],
    checks=checks,
)
