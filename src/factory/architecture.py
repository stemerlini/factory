"""An element's architecture: blocks, interfaces and requirement allocation, as text.

``architecture.yaml`` is the source of truth. This module loads and checks it, lays the
blocks out in layers (sources left, sinks right) and exports the diagram to D2 and to an
Obsidian Canvas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FILE = "architecture.yaml"


@dataclass(frozen=True)
class Block:
    id: str
    title: str
    kind: str = "generic"
    element: str | None = None  # child element name this block becomes
    description: str = ""


@dataclass(frozen=True)
class Interface:
    src: str  # "block" or "block.port"
    dst: str
    type: str = ""
    label: str = ""

    @property
    def src_block(self) -> str:
        return self.src.split(".", 1)[0]

    @property
    def dst_block(self) -> str:
        return self.dst.split(".", 1)[0]


@dataclass
class Architecture:
    blocks: list[Block] = field(default_factory=list)
    interfaces: list[Interface] = field(default_factory=list)
    allocate: dict[str, list[str]] = field(default_factory=dict)  # requirement id -> blocks
    path: Path | None = None

    def block(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)

    def problems(self, requirement_ids: set[str] | None = None) -> list[str]:
        found = []
        ids = [b.id for b in self.blocks]
        found += [f"duplicate block {i!r}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        known = set(ids)
        for itf in self.interfaces:
            for end in (itf.src_block, itf.dst_block):
                if end not in known:
                    found.append(f"interface {itf.src} → {itf.dst}: unknown block {end!r}")
        for req, blocks in self.allocate.items():
            found += [f"{req} allocated to unknown block {b!r}" for b in blocks if b not in known]
            if requirement_ids is not None and req not in requirement_ids:
                found.append(f"allocation of unknown requirement {req}")
        return found

    def layers(self) -> list[list[Block]]:
        """Blocks in columns by longest path from a source; cycles are broken by order."""
        rank = {b.id: 0 for b in self.blocks}
        for _ in range(len(self.blocks)):
            changed = False
            for itf in self.interfaces:
                s, d = itf.src_block, itf.dst_block
                if s in rank and d in rank and s != d and rank[d] < rank[s] + 1 <= len(rank):
                    rank[d] = rank[s] + 1
                    changed = True
            if not changed:
                break
        cols: dict[int, list[Block]] = {}
        for b in self.blocks:
            cols.setdefault(rank[b.id], []).append(b)
        return [cols[k] for k in sorted(cols)]

    # --- exports -------------------------------------------------------------------------

    def to_d2(self) -> str:
        lines = ["direction: right"]
        for b in self.blocks:
            lines.append(f'{b.id}: "{b.title}"')
        for i in self.interfaces:
            label = f': "{i.label or i.type}"' if (i.label or i.type) else ""
            lines.append(f"{i.src_block} -> {i.dst_block}{label}")
        return "\n".join(lines) + "\n"

    def to_canvas(self, width: int = 240, height: int = 120, gap: int = 120) -> str:
        """Obsidian Canvas (JSON Canvas 1.0) of the diagram, for viewing in the vault."""
        nodes, edges = [], []
        for col, blocks in enumerate(self.layers()):
            for row, b in enumerate(blocks):
                text = f"**{b.title}**\n{b.kind}"
                if b.description:
                    text += f"\n\n{b.description}"
                nodes.append({"id": b.id, "type": "text", "text": text,
                              "x": col * (width + gap), "y": row * (height + gap // 2),
                              "width": width, "height": height})
        for n, i in enumerate(self.interfaces):
            edges.append({"id": f"e{n}", "fromNode": i.src_block, "fromSide": "right",
                          "toNode": i.dst_block, "toSide": "left",
                          "label": i.label or i.type})
        return json.dumps({"nodes": nodes, "edges": edges}, indent=2)


def load(element_dir: Path) -> Architecture | None:
    path = element_dir / FILE
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    blocks = [Block(str(b["id"]), str(b.get("title", b["id"])), b.get("kind", "generic"),
                    b.get("element"), b.get("description", ""))
              for b in data.get("blocks", [])]
    interfaces = [Interface(str(i["from"]), str(i["to"]), i.get("type", ""), i.get("label", ""))
                  for i in data.get("interfaces", [])]
    allocate = {str(k): [v] if isinstance(v, str) else list(v)
                for k, v in (data.get("allocate") or {}).items()}
    return Architecture(blocks, interfaces, allocate, path)


# --- terminal rendering ----------------------------------------------------------------------

BOX_W, BOX_H, GAP = 26, 4, 10


def render(arch: Architecture) -> tuple[str, list[Interface]]:
    """The diagram as box-drawing text, plus the interfaces it could not draw (those that
    skip a column or go backwards), which the caller lists separately."""
    layers = arch.layers()
    if not layers:
        return "(no blocks)", []
    pos: dict[str, tuple[int, int]] = {}
    for c, blocks in enumerate(layers):
        for r, b in enumerate(blocks):
            pos[b.id] = (c, r)
    rows = max(len(col) for col in layers)
    width = len(layers) * (BOX_W + GAP)
    height = rows * (BOX_H + 1)
    grid = [[" "] * width for _ in range(height)]

    def put(x: int, y: int, ch: str) -> None:
        if 0 <= y < height and 0 <= x < width:
            old = grid[y][x]
            if {old, ch} == {"│", "─"}:
                ch = "┼"
            grid[y][x] = ch

    for b in arch.blocks:
        c, r = pos[b.id]
        x0, y0 = c * (BOX_W + GAP), r * (BOX_H + 1)
        for x in range(x0 + 1, x0 + BOX_W - 1):
            put(x, y0, "─")
            put(x, y0 + BOX_H - 1, "─")
        for y in range(y0 + 1, y0 + BOX_H - 1):
            put(x0, y, "│")
            put(x0 + BOX_W - 1, y, "│")
        for (x, y), ch in {(x0, y0): "╭", (x0 + BOX_W - 1, y0): "╮", (x0, y0 + BOX_H - 1): "╰",
                           (x0 + BOX_W - 1, y0 + BOX_H - 1): "╯"}.items():
            put(x, y, ch)
        for i, ch in enumerate(b.title[: BOX_W - 4]):
            put(x0 + 2 + i, y0 + 1, ch)
        sub = (f"→ {b.element}" if b.element else b.kind)[: BOX_W - 4]
        for i, ch in enumerate(sub):
            put(x0 + 2 + i, y0 + 2, ch)

    skipped = []
    drawn: set[tuple[str, str]] = set()
    for itf in arch.interfaces:
        s, d = itf.src_block, itf.dst_block
        if s not in pos or d not in pos:
            continue
        (cs, rs), (cd, rd) = pos[s], pos[d]
        if cd != cs + 1:
            skipped.append(itf)
            continue
        if (s, d) in drawn:
            continue
        drawn.add((s, d))
        y1, y2 = rs * (BOX_H + 1) + 1, rd * (BOX_H + 1) + 2
        if y1 == y2 - 1 and rs == rd:
            y1 = y2 = rs * (BOX_H + 1) + 1
        xs = cs * (BOX_W + GAP) + BOX_W
        xd = cd * (BOX_W + GAP) - 1
        xm = xs + GAP // 2
        for x in range(xs, xm):
            put(x, y1, "─")
        if y1 != y2:
            for y in range(min(y1, y2), max(y1, y2) + 1):
                put(xm, y, "│")
            put(xm, y1, "╮" if y2 > y1 else "╯")
            put(xm, y2, "╰" if y2 > y1 else "╭")
        else:
            put(xm, y1, "─")
        for x in range(xm + 1, xd):
            put(x, y2, "─")
        put(xd, y2, "▶")
    text = "\n".join("".join(row).rstrip() for row in grid)
    return text, skipped


# --- decomposition -------------------------------------------------------------------------


def decompose(project, element, block_id: str):
    """Create the child element a block stands for, with stub requirements derived from the
    requirements allocated to the block. Returns the new element's folder."""
    from . import project as project_mod
    from .modules import registry

    arch = load(element.dir)
    block = arch.block(block_id) if arch else None
    if block is None:
        raise project_mod.ProjectError(f"no block {block_id!r} in {element.title}'s architecture")
    name = block.element or block.id
    folder = element.dir / "elements" / name
    if (folder / project_mod.ELEMENT).exists():
        raise project_mod.ProjectError(f"element {name!r} already exists")
    kind = registry.kind(block.kind)
    prefix = kind.prefix or name.upper()[:3]
    (folder / "req").mkdir(parents=True, exist_ok=True)
    (folder / project_mod.ELEMENT).write_text(
        f"---\ntitle: {block.title}\nkind: {kind.id}\nprefix: {prefix}\n---\n"
        f"{block.description or 'Describe this subsystem.'}\n")
    allocated = [r for r in project.requirements(element)
                 if block.id in r.allocated or block.id in arch.allocate.get(r.id, [])]
    for n, r in enumerate(allocated, 1):
        rid = f"{prefix}-{n:03d}"
        (folder / "req" / f"{rid}.md").write_text(
            f"---\nid: {rid}\ntitle: {r.title} ({block.title})\nparent: {r.id}\n"
            f"method: {r.method}\nstatus: draft\n---\n"
            f"Derived from {r.id}: state what {block.title.lower()} must do for it.\n")
    return folder
