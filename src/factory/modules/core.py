"""Built-in basics: the generic element kinds and the result viewers every project uses."""

from __future__ import annotations

import shutil

from .api import Check, ElementKind, Module, Viewer

INLINE_IMAGES = ("*.png", "*.jpg", "*.jpeg", "*.svg")
VTK = ("*.vtu", "*.vtk", "*.vtp", "*.vti", "*.pvd", "*.pvsm", "*.xdmf", "*.exo")
MESH_CAD = ("*.step", "*.stp", "*.stl", "*.gltf", "*.glb", "*.obj", "*.ply", "*.3mf")


def checks() -> list[Check]:
    found = []
    for exe, why in (("paraview", "3D results"), ("f3d", "quick 3D look"),
                     ("d2", "diagram SVG export")):
        path = shutil.which(exe)
        found.append(Check(exe, bool(path), path or f"not installed ({why}; pacman -S {exe})"))
    return found


module = Module(
    id="core",
    title="Core",
    description="Element kinds system/generic; viewers for plots, 3D results and models.",
    kinds=[
        ElementKind("system", "System", "◆", ("script", "pascaline", "citadel"), "SYS",
                    "The whole product: architecture, budgets, verification."),
        ElementKind("generic", "Generic", "■", ("script", "pascaline"), "",
                    "Anything else."),
    ],
    viewers=[
        Viewer("image", "Inline image", INLINE_IMAGES),
        Viewer("f3d", "f3d", MESH_CAD, lambda f: ["f3d", str(f)], "f3d"),
        Viewer("paraview", "ParaView", VTK, lambda f: ["paraview", str(f)], "paraview"),
        Viewer("f3d-vtk", "f3d", VTK, lambda f: ["f3d", str(f)], "f3d"),
    ],
    checks=checks,
)
