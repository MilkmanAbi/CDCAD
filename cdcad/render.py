"""Headless preview pictures of a mesh: flat-shaded triangles drawn far-to-near with Pillow.

There is no OpenGL, so it works on servers, in CI and over SSH. Painter's ordering can
misdraw triangles that pierce each other, which a preview can live with.
"""
import io

import numpy as np

from ._common import CADError

# name: (camera direction the viewer looks along, screen-up direction)
VIEWS = {
    "front": ((0, 1, 0), (0, 0, 1)), "back": ((0, -1, 0), (0, 0, 1)),
    "right": ((-1, 0, 0), (0, 0, 1)), "left": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, -1), (0, 1, 0)), "bottom": ((0, 0, 1), (0, -1, 0)),
    "iso": ((-1, 1, -0.8), (0, 0, 1)),
}
GRID = ("iso", "front", "top", "right")
BACKGROUND, INK, FAINT = (250, 250, 248), (40, 40, 44), (150, 150, 150)
BASE = np.array([86, 132, 196], dtype=float)
AXIS_COLOURS = {"X": (210, 60, 50), "Y": (60, 150, 60), "Z": (50, 90, 210)}


def _font(size):
    from PIL import ImageFont
    from .generate import FONT
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def _basis(view):
    forward, up = (np.asarray(v, dtype=float) for v in VIEWS[view])
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    return forward, right, np.cross(right, forward)


def _panel(mesh, view, size, scale, centre, culled):
    """One view as an RGB image of size x size (supersampled by the caller)."""
    from PIL import Image, ImageDraw
    forward, right, up = _basis(view)
    image = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)
    v = mesh.vertices - centre
    sx = size/2 + (v @ right)*scale
    sy = size/2 - (v @ up)*scale
    depth = v @ forward
    faces = mesh.faces
    normals = mesh.face_normals
    facing = normals @ forward
    visible = facing < 0 if culled else np.ones(len(faces), dtype=bool)
    light = -forward*0.75 + up*0.55 - right*0.35
    light /= np.linalg.norm(light)
    lambert = np.abs(normals @ light) if not culled else np.clip(normals @ light, 0, None)
    shade = 0.32 + 0.48*lambert + 0.2*np.abs(facing)
    colours = np.clip(BASE[None, :]*shade[:, None] + 40*shade[:, None], 0, 255).astype(int)
    order = np.argsort(-depth[faces].mean(axis=1))
    order = order[visible[order]]
    px, py = sx[faces], sy[faces]
    for f in order:
        colour = tuple(colours[f])
        draw.polygon([(px[f, 0], py[f, 0]), (px[f, 1], py[f, 1]), (px[f, 2], py[f, 2])], fill=colour, outline=colour)
    return image


def render_png(mesh, view="grid", size=900, title=None, max_faces=250_000):
    """PNG bytes of the mesh. ``view`` is one of VIEWS or "grid" (iso, front, top, right)."""
    from PIL import Image, ImageDraw
    if view != "grid" and view not in VIEWS:
        raise CADError(f"Unknown view {view!r}", hint=f"views: grid, {', '.join(VIEWS)}")
    if not 64 <= size <= 4096:
        raise CADError("Image size must be between 64 and 4096 pixels")
    if len(mesh.faces) == 0:
        raise CADError("Nothing to draw: the mesh has no faces")
    note = None
    if len(mesh.faces) > max_faces:
        mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
        note = f"preview simplified to {len(mesh.faces):,} faces"
    views = GRID if view == "grid" else (view,)
    cells = 2 if view == "grid" else 1
    panel = size//cells
    supersample = 2
    inner = panel*supersample
    centre = mesh.bounds.mean(axis=0)
    radius = float(np.linalg.norm(mesh.extents))/2 or 1.0
    scale = inner*0.45/radius
    culled = bool(mesh.is_watertight and mesh.is_winding_consistent)
    sheet = Image.new("RGB", (panel*cells, panel*cells), BACKGROUND)
    label_font = _font(max(11, panel//28))
    for index, name in enumerate(views):
        image = _panel(mesh, name, inner, scale, centre, culled).resize((panel, panel), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        draw.text((10, 8), name, fill=INK, font=label_font)
        _axes(draw, name, panel, label_font)
        sheet.paste(image, ((index % cells)*panel, (index//cells)*panel))
    draw = ImageDraw.Draw(sheet)
    if cells > 1:
        draw.line([(panel, 0), (panel, 2*panel)], fill=(220, 220, 216), width=1)
        draw.line([(0, panel), (2*panel, panel)], fill=(220, 220, 216), width=1)
    x, y, z = mesh.extents
    caption = f"{x:.3g} x {y:.3g} x {z:.3g} mm" + (f" | {title}" if title else "") + (f" | {note}" if note else "")
    small = _font(max(10, size//60))
    width = draw.textlength(caption, font=small)
    draw.text((sheet.width - width - 10, sheet.height - small.size - 8), caption, fill=FAINT, font=small)
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _axes(draw, view, panel, font):
    """A small XYZ triad in the lower-left corner, as the axes appear in this view."""
    _, right, up = _basis(view)
    origin = np.array([34.0, panel - 34.0])
    for label, axis in zip("XYZ", np.eye(3)):
        d = np.array([axis @ right, -(axis @ up)])
        if np.linalg.norm(d) < 0.15:
            draw.text(tuple(origin + [-4, -7]), label, fill=AXIS_COLOURS[label], font=font)
            continue
        end = origin + d*22
        draw.line([tuple(origin), tuple(end)], fill=AXIS_COLOURS[label], width=2)
        draw.text(tuple(end + d*6 + [-4, -7]), label, fill=AXIS_COLOURS[label], font=font)


def describe_view(view):
    if view == "grid":
        return "2x2: iso, front (from -Y), top (from +Z), right (from +X)"
    forward, right, up = _basis(view)
    return f"looking along {np.round(forward, 2).tolist()}, right {np.round(right, 2).tolist()}, up {np.round(up, 2).tolist()}"

