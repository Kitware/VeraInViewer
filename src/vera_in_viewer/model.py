"""Turn a parsed VERAin dictionary into a renderable 3D core description.

This is the Python counterpart of ``ImageGenerator.compute3DCore`` and its
helpers from VeraInView. The output only contains plain numpy arrays so it can
be tested without VTK and fed to any rendering backend.

Coordinate system: assembly ``(i, j)`` of the core map (row ``j`` = 0 at the
top) occupies ``[i * apitch, (i + 1) * apitch]`` along X and is flipped along Y
so the 3D view matches the 2D maps when looking down the Z axis. Pins are
centered inside their assembly pitch.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .colors import RGBA, bank_color_manager, material_color_manager

EPSILON = 0.01
CORE_MAPS = {
    "ASSEMBLIES": "assm_map",
    "CONTROLS": "crd_map",
    "DETECTORS": "det_map",
    "INSERTS": "insert_map",
}
EMPTY = "-"


# -----------------------------------------------------------------------------
# Data containers
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """Concentric rings of materials, ``radii`` being the outer radius of each ring."""

    mats: tuple[str, ...]
    radii: tuple[float, ...]

    @property
    def key(self) -> str:
        return "|".join(f"{m}_{r:g}" for m, r in zip(self.mats, self.radii, strict=True))


@dataclass
class SpacerGrid:
    label: str
    material: str
    height: float
    mass: float
    elevations: list[float] = field(default_factory=list)


@dataclass
class LayoutItem:
    """Assembly, control, detector or insert definition."""

    category: str
    label: str
    title: str
    cells: dict[str, Cell]
    cell_maps: dict[str, list[str]]
    axial_labels: list[str]
    axial_elevations: list[float]
    num_pins: int
    ppitch: float | None
    grids: list[SpacerGrid]
    densities: dict[str, float]
    stroke: float | None = None
    maxstep: float | None = None


@dataclass
class PinGlyphs:
    """All instances of one cell type. ``bank`` is set for movable control rods."""

    cell: Cell
    bank: str | None
    centers: np.ndarray  # (n, 3) float32
    lengths: np.ndarray  # (n,) float32

    @property
    def scales(self) -> np.ndarray:
        scales = np.ones((len(self.lengths), 3), dtype=np.float32)
        scales[:, 2] = self.lengths
        return scales

    def z_mask(self, z_min: float, z_max: float) -> np.ndarray:
        return overlap_mask(self.centers[:, 2], self.lengths, z_min, z_max)


@dataclass
class BoxGlyphs:
    """Axis aligned boxes of a single material (spacer grids, baffle)."""

    material: str
    centers: np.ndarray  # (n, 3) float32
    sizes: np.ndarray  # (n, 3) float32


@dataclass
class RingStack:
    """Large concentric structure rendered once (vessel, pads, core plates)."""

    group: str  # name of the visibility toggle: "Vessel" or "Plate"
    label: str
    mats: tuple[str, ...]
    radii: tuple[float, ...]
    center: tuple[float, float, float]
    height: float
    hidden_layers: frozenset[int] = frozenset()
    start_theta: float = 0.0
    end_theta: float = 360.0
    resolution: int = 120


@dataclass
class CoreState:
    """A VERA ``STATES`` entry with cumulative control bank positions."""

    label: str
    title: str
    params: dict[str, Any]
    bank_positions: dict[str, float]


@dataclass
class CoreModel:
    title: str
    name: str
    core_size: int
    apitch: float
    ppitch: float
    height: float
    center: tuple[float, float, float]
    outer_radius: float
    pins: list[PinGlyphs]
    grids: list[BoxGlyphs]
    baffle: BoxGlyphs | None
    rings: list[RingStack]
    elevations: list[float]
    materials: list[str]
    material_colors: dict[str, RGBA]
    states: list[CoreState]
    banks: list[str]
    bank_colors: dict[str, RGBA]
    stroke: float | None
    maxstep: float | None

    @property
    def instance_count(self) -> int:
        return sum(len(p.lengths) for p in self.pins)

    @property
    def slices(self) -> list[tuple[float, float]]:
        return list(zip(self.elevations[:-1], self.elevations[1:], strict=True))

    @property
    def rod_step_size(self) -> float:
        """Axial travel (cm) of a control rod for one step."""
        if self.stroke and self.maxstep:
            return self.stroke / self.maxstep
        return 1.0


def overlap_mask(z_centers: np.ndarray, z_lengths: np.ndarray, z_min: float, z_max: float) -> np.ndarray:
    """Select items whose ``[center - len/2, center + len/2]`` overlaps ``[z_min, z_max]``."""
    half = 0.5 * z_lengths
    return ~((z_min + EPSILON > z_centers + half) | (z_max < z_centers - half + EPSILON))


# -----------------------------------------------------------------------------
# Extraction helpers
# -----------------------------------------------------------------------------


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _densities(node: dict[str, Any]) -> dict[str, float]:
    result = {}
    for mat in (node.get("Materials") or {}).values():
        if isinstance(mat, dict) and "key_name" in mat:
            result[str(mat["key_name"])] = float(mat.get("density", 1))
    return result


def _extract_cells(node: dict[str, Any]) -> dict[str, Cell]:
    cells = {}
    for cell in (node.get("Cells") or {}).values():
        if not isinstance(cell, dict):
            continue
        mats = [str(m) for m in _as_list(cell.get("mats"))]
        radii = [float(r) for r in _as_list(cell.get("radii"))]
        size = min(len(mats), len(radii))
        if size:
            cells[str(cell.get("label"))] = Cell(tuple(mats[:size]), tuple(radii[:size]))
    return cells


def _extract_grids(node: dict[str, Any]) -> list[SpacerGrid]:
    grids: dict[str, SpacerGrid] = {}
    for grid in (node.get("SpacerGrids") or {}).values():
        if isinstance(grid, dict):
            label = str(grid.get("label"))
            grids[label] = SpacerGrid(
                label=label,
                material=str(grid.get("material")),
                height=float(grid.get("height", 0)),
                mass=float(grid.get("mass", 0)),
            )
    for label, elevation in zip(
        _as_list(node.get("grid_map")), _as_list(node.get("grid_elev")), strict=False
    ):
        if str(label) in grids:
            grids[str(label)].elevations.append(float(elevation))
    return list(grids.values())


def extract_items(caseid: dict[str, Any], category: str) -> dict[str, LayoutItem]:
    """Extract all assemblies/controls/... of a category keyed by label."""
    items = {}
    for node in (caseid.get(category) or {}).values():
        if not isinstance(node, dict) or "label" not in node:
            continue
        label = str(node["label"])
        items[label] = LayoutItem(
            category=category,
            label=label,
            title=str(node.get("title", label)),
            cells=_extract_cells(node),
            cell_maps={
                str(m.get("label")): [str(c) for c in _as_list(m.get("cell_map"))]
                for m in (node.get("CellMaps") or {}).values()
                if isinstance(m, dict)
            },
            axial_labels=[str(v) for v in _as_list(node.get("axial_labels"))],
            axial_elevations=[float(v) for v in _as_list(node.get("axial_elevations"))],
            num_pins=int(node.get("num_pins", 0)),
            ppitch=float(node["ppitch"]) if "ppitch" in node else None,
            grids=_extract_grids(node),
            densities=_densities(node),
            stroke=float(node["stroke"]) if "stroke" in node else None,
            maxstep=float(node["maxstep"]) if "maxstep" in node else None,
        )
    return dict(sorted(items.items()))


def extract_states(caseid: dict[str, Any]) -> list[CoreState]:
    """States sorted by number with cumulative control bank positions."""
    entries = []
    for key, node in (caseid.get("STATES") or {}).items():
        if not isinstance(node, dict):
            continue
        suffix = key[len("State_") :] if key.startswith("State_") else key
        order = (0, int(suffix), "") if suffix.isdigit() else (1, 0, suffix)
        entries.append((order, suffix, node))
    entries.sort(key=lambda e: e[0])

    states = []
    positions: dict[str, float] = {}
    for _, label, node in entries:
        for bank, pos in zip(_as_list(node.get("bank_labels")), _as_list(node.get("bank_pos")), strict=False):
            positions[str(bank)] = float(pos)
        params = {k: v for k, v in node.items() if not isinstance(v, dict)}
        states.append(
            CoreState(
                label=label,
                title=str(node.get("title", f"State {label}")),
                params=params,
                bank_positions=dict(positions),
            )
        )
    return states


def extract_elevations(items: dict[str, dict[str, LayoutItem]]) -> list[float]:
    values = {z for category in items.values() for item in category.values() for z in item.axial_elevations}
    return sorted(values)


def grid_thickness(pitch: float, mass: float, density: float, height: float, npins: int) -> float:
    """Spacer grid strap thickness (spacer_grid_thickness_notes.pdf, final formula)."""
    if not (density and height and npins):
        return 0.0
    disc = 4 * pitch * pitch - 4 * (mass / (density * height * npins * npins))
    return (2 * pitch - math.sqrt(max(disc, 0.0))) / 4


# -----------------------------------------------------------------------------
# Baffle
# -----------------------------------------------------------------------------

_DIRECTIONS = {
    # outward normal (di, dr) -> tangent (di, dr), with r the row from the bottom
    "top": ((0, 1), (1, 0)),
    "bottom": ((0, -1), (1, 0)),
    "left": ((-1, 0), (0, 1)),
    "right": ((1, 0), (0, 1)),
}


def extract_baffle(core: dict[str, Any], shape: list[int]) -> BoxGlyphs | None:
    """Baffle plates surrounding the core outline.

    Each exposed assembly edge gets a plate at ``gap + thick / 2`` from the
    edge. Plate ends are extended at convex corners and shortened at concave
    ones so plates join without overlapping assemblies.
    """
    if not all(k in core for k in ("baffle_thick", "baffle_gap", "baffle_mat")):
        return None

    size = int(core["core_size"])
    apitch = float(core["apitch"])
    gap = float(core["baffle_gap"])
    thick = float(core["baffle_thick"])
    height = float(core["height"])
    shift = gap + thick / 2

    def filled(i: int, r: int) -> bool:
        if 0 <= i < size and 0 <= r < size:
            return bool(shape[(size - 1 - r) * size + i])
        return False

    centers = []
    sizes = []
    for r in range(size):
        for i in range(size):
            if not filled(i, r):
                continue
            cx, cy = (i + 0.5) * apitch, (r + 0.5) * apitch
            for (ni, nr), (ti, tr) in _DIRECTIONS.values():
                if filled(i + ni, r + nr):
                    continue
                ext = []
                for sign in (-1, 1):
                    if filled(i + ni + sign * ti, r + nr + sign * tr):
                        ext.append(-gap)  # concave corner
                    elif filled(i + sign * ti, r + sign * tr):
                        ext.append(0.0)  # straight run
                    else:
                        ext.append(gap + thick)  # convex corner
                length = apitch + ext[0] + ext[1]
                offset = 0.5 * (ext[1] - ext[0])
                x = cx + ni * (0.5 * apitch + shift) + ti * offset
                y = cy + nr * (0.5 * apitch + shift) + tr * offset
                centers.append((x, y, 0.5 * height))
                sizes.append((thick if ni else length, thick if nr else length, height))

    if not centers:
        return None
    return BoxGlyphs(
        material=str(core["baffle_mat"]),
        centers=np.asarray(centers, dtype=np.float32),
        sizes=np.asarray(sizes, dtype=np.float32),
    )


# -----------------------------------------------------------------------------
# Vessel, pads and plates
# -----------------------------------------------------------------------------


def extract_rings(core: dict[str, Any], center_xy: tuple[float, float]) -> list[RingStack]:
    height = float(core["height"])
    cx, cy = center_xy
    rings = []

    vessel_radii = [float(r) for r in _as_list(core.get("vessel_radii"))]
    vessel_mats = [str(m) for m in _as_list(core.get("vessel_mats"))]
    if vessel_radii and vessel_mats:
        size = min(len(vessel_radii), len(vessel_mats))
        rings.append(
            RingStack(
                group="Vessel",
                label="vessel",
                mats=tuple(vessel_mats[:size]),
                radii=tuple(vessel_radii[:size]),
                center=(cx, cy, height / 2),
                height=height,
                hidden_layers=frozenset({0}),  # inside the core barrel
            )
        )

    pads = _as_list(core.get("pad_azi_locs"))
    if pads and all(k in core for k in ("pad_arc", "pad_inner_radius", "pad_outer_radius", "pad_mat")):
        width = float(core["pad_arc"])
        mat = str(core["pad_mat"])
        for angle in pads:
            # VERA wants angle counter-clockwise from positive-X.
            theta = float(angle) % 360
            rings.append(
                RingStack(
                    group="Vessel",
                    label="pad",
                    mats=(mat, mat),
                    radii=(float(core["pad_inner_radius"]), float(core["pad_outer_radius"])),
                    center=(cx, cy, height / 2),
                    height=height - EPSILON,
                    hidden_layers=frozenset({0}),
                    start_theta=theta - width / 2,
                    end_theta=theta + width / 2,
                    resolution=math.ceil(120 / len(pads)),
                )
            )

    if core.get("lower_thick") and core.get("upper_thick"):
        radius = vessel_radii[-1] if vessel_radii else float(core["apitch"]) / 2
        for plate in ("lower", "upper"):
            thick = float(core[f"{plate}_thick"])
            z = -thick / 2 if plate == "lower" else height + thick / 2
            rings.append(
                RingStack(
                    group="Plate",
                    label=f"{plate} plate",
                    mats=(str(core.get(f"{plate}_mat", "ss")),),
                    radii=(radius,),
                    center=(cx, cy, z),
                    height=thick,
                )
            )
    return rings


# -----------------------------------------------------------------------------
# Core assembly
# -----------------------------------------------------------------------------


class _LayoutResolver:
    """Resolve axial layouts into pin positions, with caching.

    A layout can be borrowed from another item of the same category (see
    ``getLayout`` in ImageGenerator.js), in which case its cells are resolved
    against the item that defines it.
    """

    def __init__(self, items: dict[str, LayoutItem], default_ppitch: float):
        self.items = items
        self.default_ppitch = default_ppitch
        self._cache: dict[tuple[str, str], dict[str, tuple[Cell, np.ndarray]]] = {}

    def owner(self, item: LayoutItem, name: str) -> LayoutItem | None:
        if name in item.cell_maps:
            return item
        found = None
        for other in self.items.values():
            if name in other.cell_maps:
                found = other
        return found

    def pins(self, item: LayoutItem, name: str) -> dict[str, tuple[Cell, np.ndarray]]:
        """Map of cell key -> (cell, local XY positions) for an axial layout."""
        owner = self.owner(item, name)
        if owner is None:
            return {}
        cache_key = (owner.label, name)
        if cache_key in self._cache:
            return self._cache[cache_key]

        cell_map = owner.cell_maps[name]
        width = item.num_pins or round(math.sqrt(len(cell_map)))
        pitch = item.ppitch or self.default_ppitch
        positions: dict[str, list[tuple[float, float]]] = defaultdict(list)
        cells: dict[str, Cell] = {}
        for idx, label in enumerate(cell_map):
            cell = owner.cells.get(label)
            if cell is None:
                continue
            i, j = idx % width, idx // width
            positions[cell.key].append((i * pitch, (width - j - 1) * pitch))
            cells[cell.key] = cell

        result = {key: (cells[key], np.asarray(xy, dtype=np.float32)) for key, xy in positions.items()}
        self._cache[cache_key] = result
        return result


def _pin_pitch(items: dict[str, dict[str, LayoutItem]]) -> float:
    ppitch = 0.0
    for category in items.values():
        for item in category.values():
            if item.ppitch:
                ppitch = item.ppitch
    return ppitch or 1.26


def build_core_model(data: dict[str, Any]) -> CoreModel:
    """Build the 3D description of the full core from parsed VERAin content."""
    caseid = data.get("CASEID", data)
    core = caseid["CORE"]
    size = int(core.get("core_size", 1))
    apitch = float(core["apitch"])
    height = float(core["height"])
    shape = [int(v) for v in _as_list(core.get("shape"))] or [1] * (size * size)
    core_densities = _densities(core)

    items = {category: extract_items(caseid, category) for category in CORE_MAPS}
    ppitch = _pin_pitch(items)
    resolvers = {category: _LayoutResolver(items[category], ppitch) for category in CORE_MAPS}
    bank_map = [str(v) for v in _as_list(core.get("crd_bank"))]

    # (cell key, bank) -> list of (xy (m,2), z center, z length)
    pin_chunks: dict[tuple[str, str | None], list[tuple[np.ndarray, float, float]]] = defaultdict(list)
    pin_cells: dict[str, Cell] = {}
    grid_centers: dict[str, list[np.ndarray]] = defaultdict(list)
    grid_sizes: dict[str, list[np.ndarray]] = defaultdict(list)
    item_grids: dict[tuple[str, str], dict[str, tuple[np.ndarray, np.ndarray]]] = {}
    bank_order: list[str] = []

    pidx = 0
    for j in range(size):
        for i in range(size):
            if not shape[i + j * size]:
                continue
            origin = np.array([i * apitch, (size - j - 1) * apitch], dtype=np.float32)
            for category, map_name in CORE_MAPS.items():
                key_map = _as_list(core.get(map_name))
                if pidx >= len(key_map):
                    continue
                item = items[category].get(str(key_map[pidx]))
                if item is None:
                    continue

                pitch = item.ppitch or ppitch
                width = item.num_pins * pitch
                margin = 0.5 * (apitch - width)
                pin_origin = origin + (margin + 0.5 * pitch)

                bank = None
                if category == "CONTROLS":
                    bank = bank_map[pidx] if pidx < len(bank_map) and bank_map[pidx] != EMPTY else item.label
                    if bank not in bank_order:
                        bank_order.append(bank)

                elevations = item.axial_elevations
                for seg, name in enumerate(item.axial_labels[: len(elevations) - 1]):
                    z0, z1 = elevations[seg], elevations[seg + 1]
                    for cell_key, (cell, xy) in resolvers[category].pins(item, name).items():
                        pin_cells[cell_key] = cell
                        pin_chunks[(cell_key, bank)].append((xy + pin_origin, 0.5 * (z0 + z1), abs(z1 - z0)))

                if item.grids:
                    cache_key = (category, item.label)
                    if cache_key not in item_grids:
                        item_grids[cache_key] = _local_grids(item, pitch, core_densities)
                    for mat, (centers, sizes) in item_grids[cache_key].items():
                        shifted = centers.copy()
                        shifted[:, :2] += origin + margin
                        grid_centers[mat].append(shifted)
                        grid_sizes[mat].append(sizes)
            pidx += 1

    pins = []
    for (cell_key, bank), chunks in pin_chunks.items():
        count = sum(len(xy) for xy, _, _ in chunks)
        centers = np.empty((count, 3), dtype=np.float32)
        lengths = np.empty(count, dtype=np.float32)
        offset = 0
        for xy, zc, zl in chunks:
            end = offset + len(xy)
            centers[offset:end, :2] = xy
            centers[offset:end, 2] = zc
            lengths[offset:end] = zl
            offset = end
        pins.append(PinGlyphs(cell=pin_cells[cell_key], bank=bank, centers=centers, lengths=lengths))
    pins.sort(key=lambda p: (p.bank or "", p.cell.key))

    grids = [
        BoxGlyphs(
            material=mat, centers=np.concatenate(grid_centers[mat]), sizes=np.concatenate(grid_sizes[mat])
        )
        for mat in grid_centers
    ]

    center = (size * apitch / 2, size * apitch / 2, height / 2)
    has_shape = "shape" in core
    baffle = extract_baffle(core, shape) if has_shape else None
    rings = extract_rings(core, center[:2]) if has_shape else []
    vessel = next((r for r in rings if r.label == "vessel"), None)
    # VeraInView sizes the slice outline with the core barrel (2nd vessel radius)
    outer_radius = vessel.radii[min(1, len(vessel.radii) - 1)] if vessel else size * apitch / 2

    # Material colors are assigned the same way as VeraInView: cell + vessel
    # materials in alphabetical order first, then everything else.
    colors = material_color_manager()
    primary = sorted(
        {
            m
            for category in items.values()
            for item in category.values()
            for c in item.cells.values()
            for m in c.mats
        }
        | {str(m) for m in _as_list(core.get("vessel_mats"))}
    )
    secondary = [g.material for g in grids] + ([baffle.material] if baffle else [])
    secondary += [m for r in rings for m in r.mats]
    materials = list(dict.fromkeys(primary + secondary))
    material_colors = {name: colors.get_color(name) for name in materials}

    states = extract_states(caseid)
    banks = list(dict.fromkeys([b for s in states for b in s.bank_positions if b in bank_order] + bank_order))
    bank_manager = bank_color_manager()
    controls = list(items["CONTROLS"].values())

    return CoreModel(
        title=str(caseid.get("case_id", core.get("name", "VERA core"))),
        name=str(core.get("name", "")),
        core_size=size,
        apitch=apitch,
        ppitch=ppitch,
        height=height,
        center=center,
        outer_radius=outer_radius,
        pins=pins,
        grids=grids,
        baffle=baffle,
        rings=rings,
        elevations=extract_elevations(items),
        materials=materials,
        material_colors=material_colors,
        states=states,
        banks=banks,
        bank_colors={bank: bank_manager.get_color(bank) for bank in banks},
        stroke=controls[0].stroke if controls else None,
        maxstep=controls[0].maxstep if controls else None,
    )


def _local_grids(
    item: LayoutItem, pitch: float, core_densities: dict[str, float]
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Spacer grid straps of one assembly, relative to its pin lattice corner."""
    npins = item.num_pins
    width = npins * pitch
    result: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for grid in item.grids:
        density = item.densities.get(grid.material) or core_densities.get(grid.material) or 1.0
        thickness = grid_thickness(pitch, grid.mass, density, grid.height, npins)
        centers, sizes = result[grid.material]
        for elevation in grid.elevations:
            for k in range(npins + 1):
                centers.append((k * pitch, width / 2, elevation))
                sizes.append((thickness, width, grid.height))
                centers.append((width / 2, k * pitch, elevation))
                sizes.append((width, thickness, grid.height))
    return {
        mat: (np.asarray(c, dtype=np.float32), np.asarray(s, dtype=np.float32))
        for mat, (c, s) in result.items()
    }
