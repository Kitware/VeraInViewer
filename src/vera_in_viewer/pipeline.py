"""VTK scene for a :class:`~vera_in_viewer.model.CoreModel`.

Python counterpart of VeraInView's ``PipelineManager.js``: each cell type and
each vessel / core plate structure is a single concentric cylinder source with
all its materials, hidden materials being masked so walls only get generated
where needed. Spacer grids and baffle are rendered with cube glyphs.

Unlike vtk.js, ``vtkGlyph3DMapper`` ignores the scalars of its source, so the
cylinder mesh is split by material into one glyph mapper each to keep material
colors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from vtkmodules import vtkInteractionStyle, vtkRenderingOpenGL2  # noqa: F401 (object factories)
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtkmodules.vtkCommonDataModel import vtkPolyData
from vtkmodules.vtkRenderingCore import (
    VTK_SURFACE,
    vtkActor,
    vtkGlyph3DMapper,
    vtkPolyDataMapper,
    vtkRenderer,
    vtkRenderWindow,
    vtkRenderWindowInteractor,
)

from . import geometry
from .model import BoxGlyphs, CoreModel, PinGlyphs, RingStack

TOGGLES = ("Grid", "Baffle", "Vessel", "Plate")
DEFAULT_TOGGLES = {"Grid": True, "Baffle": True, "Vessel": True, "Plate": False}
# Soft slate gradient (bottom, top): bright but keeps contrast with grey structures and pale materials
BACKGROUND = (0.45, 0.50, 0.58)
BACKGROUND_TOP = (0.78, 0.82, 0.87)
BANK_COLOR_ARRAY = "bank_color"
# Direction of projection of the side and iso views
VIEW_DIRECTIONS = {"side": (1.0, 0.0, 0.0), "iso": (-1.0, -1.0, -1.0)}


def _apply_property(actor: vtkActor, color) -> None:
    prop = actor.GetProperty()
    prop.SetDiffuse(0.7)
    prop.SetAmbient(0.3)
    prop.SetColor(*color[:3])


def triangle_count(polydata: vtkPolyData) -> int:
    """Number of triangles once the polygons get triangulated for rendering."""
    polys = polydata.GetPolys()
    if polys.GetNumberOfCells() == 0:
        return 0
    sizes = np.diff(vtk_to_numpy(polys.GetOffsetsArray()))
    return int(np.sum(sizes - 2))


def _glyph_mapper(instances: vtkPolyData, source: vtkPolyData) -> vtkGlyph3DMapper:
    mapper = vtkGlyph3DMapper()
    mapper.SetInputData(instances)
    mapper.SetSourceData(source)
    mapper.OrientOff()
    mapper.ScalingOn()
    mapper.SetScaleArray("scale")
    mapper.SetScaleModeToScaleByVectorComponents()
    mapper.ScalarVisibilityOff()
    # Optional per instance colors (control rod banks)
    mapper.SetScalarModeToUsePointFieldData()
    mapper.SelectColorArray(BANK_COLOR_ARRAY)
    mapper.SetColorModeToDirectScalars()
    return mapper


@dataclass
class _Layer:
    """One rendered ring/box set with what drives its color and visibility."""

    actor: vtkActor
    group: str  # "Pins", "Grid", "Baffle", "Vessel", "Plate" or "Slice"
    material: str | None = None
    bank: str | None = None


@dataclass
class _Cylinder:
    """Concentric cylinder source and its mesh split into one piece per material."""

    source: geometry.ConcentricCylinderSource
    mats: tuple[str, ...]
    forced_mask: frozenset[int] = frozenset()
    output: vtkPolyData = field(default_factory=vtkPolyData)
    pieces: dict[str, vtkPolyData] = field(default_factory=dict)
    _built: tuple | None = None

    def __post_init__(self):
        for idx, material in enumerate(self.mats):
            if idx not in self.forced_mask:
                self.pieces.setdefault(material, vtkPolyData())

    def update(self, hidden_materials: set[str]) -> None:
        """Mask the hidden materials and regenerate the mesh if anything changed."""
        mask = tuple(i in self.forced_mask or m in hidden_materials for i, m in enumerate(self.mats))
        key = (mask, self.source.resolution)
        if key == self._built:
            return
        self._built = key
        for idx, hidden in enumerate(mask):
            self.source.set_mask_layer(idx, hidden)
        self.source.update(self.output)
        for material, piece in self.pieces.items():
            layers = [i for i, m in enumerate(self.mats) if m == material]
            geometry.extract_cells(self.output, geometry.LAYER_ARRAY, layers, piece)


@dataclass
class _PinSet:
    glyphs: PinGlyphs
    instances: vtkPolyData
    cylinder: _Cylinder
    layers: list[_Layer] = field(default_factory=list)


class CoreScene:
    """Own the render window and keep VTK objects in sync with view settings."""

    def __init__(self):
        self.renderer = vtkRenderer()
        self.renderer.GradientBackgroundOn()
        self.renderer.SetBackground(*BACKGROUND)
        self.renderer.SetBackground2(*BACKGROUND_TOP)
        self.render_window = vtkRenderWindow()
        self.render_window.AddRenderer(self.renderer)
        self.render_window.OffScreenRenderingOn()
        self.render_window.SetAlphaBitPlanes(1)
        self.interactor = vtkRenderWindowInteractor()
        self.interactor.SetRenderWindow(self.render_window)
        self.interactor.GetInteractorStyle().SetCurrentStyleToTrackballCamera()
        self.view_2d = False
        self.parallel = False

        self.model: CoreModel | None = None
        self.resolution = 6
        self.z_scale = 1.0
        self.hidden_materials: set[str] = set()
        self.toggles = dict(DEFAULT_TOGGLES)
        self.z_range: tuple[float, float] | None = None
        self.bank_positions: dict[str, float] = {}
        self.hidden_banks: set[str] = set()
        self.color_by_bank = False

        self._layers: list[_Layer] = []
        self._pin_sets: list[_PinSet] = []
        self._cylinders: list[_Cylinder] = []
        self._slice_outline = vtkPolyData()

    # -------------------------------------------------------------------------
    # Scene construction
    # -------------------------------------------------------------------------

    def clear(self) -> None:
        self.renderer.RemoveAllViewProps()
        self._layers.clear()
        self._pin_sets.clear()
        self._cylinders.clear()

    def load(self, model: CoreModel) -> None:
        self.clear()
        self.model = model
        self.z_range = None
        self.toggles = dict(DEFAULT_TOGGLES)
        self.hidden_materials = set()
        self.hidden_banks = set()
        self.bank_positions = {b: 0.0 for b in model.banks}

        for pins in model.pins:
            self._add_pins(pins)

        cube = geometry.unit_cube()
        for grid in model.grids:
            self._add_boxes(grid, cube, "Grid")
        if model.baffle is not None:
            self._add_boxes(model.baffle, cube, "Baffle")

        for stack in model.rings:
            self._add_rings(stack)

        outline = vtkActor()
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(self._slice_outline)
        mapper.ScalarVisibilityOff()
        outline.SetMapper(mapper)
        outline.GetProperty().SetRepresentationToWireframe()
        outline.GetProperty().LightingOff()
        self._layers.append(_Layer(outline, "Slice"))
        self.renderer.AddActor(outline)

        self._update_slice()
        self._update_appearance()
        self._top_view()

    def _add_pins(self, pins: PinGlyphs) -> None:
        source = geometry.ConcentricCylinderSource(pins.cell.radii, resolution=self.resolution)
        cylinder = _Cylinder(source, pins.cell.mats)
        pin_set = _PinSet(pins, vtkPolyData(), cylinder)
        for material, piece in cylinder.pieces.items():
            actor = vtkActor()
            actor.SetMapper(_glyph_mapper(pin_set.instances, piece))
            layer = _Layer(actor, "Pins", material=material, bank=pins.bank)
            pin_set.layers.append(layer)
            self._layers.append(layer)
            self.renderer.AddActor(actor)
        self._pin_sets.append(pin_set)
        self._cylinders.append(cylinder)

    def _add_boxes(self, boxes: BoxGlyphs, cube: vtkPolyData, group: str) -> None:
        instances = geometry.glyph_instances(boxes.centers, boxes.sizes)
        actor = vtkActor()
        actor.SetMapper(_glyph_mapper(instances, cube))
        self._layers.append(_Layer(actor, group, material=boxes.material))
        self.renderer.AddActor(actor)

    def _add_rings(self, stack: RingStack) -> None:
        source = geometry.ConcentricCylinderSource(
            stack.radii,
            height=stack.height,
            resolution=stack.resolution,
            start_theta=stack.start_theta,
            end_theta=stack.end_theta,
            center=stack.center,
        )
        cylinder = _Cylinder(source, stack.mats, stack.hidden_layers)
        for material, piece in cylinder.pieces.items():
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(piece)
            mapper.ScalarVisibilityOff()
            actor = vtkActor()
            actor.SetMapper(mapper)
            self._layers.append(_Layer(actor, stack.group, material=material))
            self.renderer.AddActor(actor)
        self._cylinders.append(cylinder)

    # -------------------------------------------------------------------------
    # Updates
    # -------------------------------------------------------------------------

    def _update_slice(self) -> None:
        """Keep the rod segments intersecting the elevation range and outline it.

        Like VeraInView, structures (vessel, baffle, grids) are left untouched.
        """
        z_range = self.z_range
        for pin_set in self._pin_sets:
            glyphs = pin_set.glyphs
            centers, scales = glyphs.centers, glyphs.scales
            if z_range is not None:
                mask = glyphs.z_mask(*z_range)
                centers, scales = centers[mask], scales[mask]
            geometry.glyph_instances(centers, scales, pin_set.instances)
            if glyphs.bank is not None:
                self._add_bank_colors(pin_set.instances, glyphs.bank)

        if z_range is not None and self.model is not None:
            cx, cy, _ = self.model.center
            r = self.model.outer_radius
            self._slice_outline.ShallowCopy(
                geometry.box((cx, cy, 0.5 * sum(z_range)), (2 * r, 2 * r, z_range[1] - z_range[0]))
            )
        else:
            self._slice_outline.Initialize()

    def _add_bank_colors(self, instances: vtkPolyData, bank: str) -> None:
        """Constant color array used when coloring control rods by bank.

        Toggling the mapper scalar visibility (instead of the actor color)
        reliably makes the glyph mapper rebuild its colors on the client.
        """
        rgb = np.round(np.asarray(self.model.bank_colors[bank][:3]) * 255).astype(np.uint8)
        colors = numpy_to_vtk(np.tile(rgb, (instances.GetNumberOfPoints(), 1)), deep=True)
        colors.SetName(BANK_COLOR_ARRAY)
        instances.GetPointData().AddArray(colors)

    def _update_appearance(self) -> None:
        """Apply colors, visibility, z scaling and control rod positions."""
        model = self.model
        if model is None:
            return
        for cylinder in self._cylinders:
            cylinder.update(self.hidden_materials)
        step = model.rod_step_size
        for layer in self._layers:
            actor = layer.actor
            actor.SetScale(1, 1, self.z_scale)
            if layer.group == "Slice":
                actor.SetVisibility(self.z_range is not None)
                continue

            _apply_property(actor, model.material_colors.get(layer.material, (1, 1, 1, 1)))
            if layer.bank is not None:
                actor.GetMapper().SetScalarVisibility(self.color_by_bank)
                position = self.bank_positions.get(layer.bank, 0.0)
                actor.SetPosition(0, 0, position * step * self.z_scale)

            visible = layer.material not in self.hidden_materials
            if layer.group in self.toggles:
                visible = visible and self.toggles[layer.group]
            if layer.group == "Plate" and self.z_range is not None:
                visible = False
            if layer.bank is not None and layer.bank in self.hidden_banks:
                visible = False
            actor.SetVisibility(visible)

    def stats(self) -> dict[str, int]:
        """Actor and triangle counts of the scene, triangles only for visible surfaces.

        Glyph mappers render their source once per instance.
        """
        actors = visible = triangles = 0
        for layer in self._layers:
            actors += 1
            actor = layer.actor
            if not actor.GetVisibility():
                continue
            visible += 1
            if actor.GetProperty().GetRepresentation() != VTK_SURFACE:
                continue
            mapper = actor.GetMapper()
            if isinstance(mapper, vtkGlyph3DMapper):
                triangles += triangle_count(mapper.GetSource(0)) * mapper.GetInput().GetNumberOfPoints()
            else:
                triangles += triangle_count(mapper.GetInput())
        return {"actors": actors, "visible_actors": visible, "triangles": triangles}

    def set_resolution(self, resolution: int) -> None:
        resolution = max(3, int(resolution))
        if resolution == self.resolution:
            return
        self.resolution = resolution
        for pin_set in self._pin_sets:
            pin_set.cylinder.source.resolution = resolution
            pin_set.cylinder.update(self.hidden_materials)

    def set_z_range(self, z_range: tuple[float, float] | None) -> None:
        self.z_range = z_range
        self._update_slice()
        self._update_appearance()

    def update_appearance(self, **settings) -> None:
        """Update any of the appearance settings attributes then apply them."""
        for key, value in settings.items():
            if not hasattr(self, key):
                raise AttributeError(key)
            setattr(self, key, value)
        self._update_appearance()

    # -------------------------------------------------------------------------
    # Camera
    # -------------------------------------------------------------------------

    def reset_camera(self) -> None:
        """Fit the visible scene, keeping the current view direction."""
        self.renderer.ResetCamera()

    def orient(self, view: str) -> None:
        """Look at the scene from the top (``"top"``), the side (``"side"``) or diagonally (``"iso"``)."""
        if view == "top":
            self._top_view()
            return
        direction = VIEW_DIRECTIONS[view]
        camera = self.renderer.GetActiveCamera()
        focal = camera.GetFocalPoint()
        camera.SetPosition(*(f - d for f, d in zip(focal, direction, strict=True)))
        camera.SetViewUp(0, 0, 1)
        self.renderer.ResetCamera()

    def _top_view(self) -> None:
        """Look down the Z axis like the 2D core maps of VeraInView.

        Unlike ``vtkRenderer.ResetCamera()`` which fits the bounding sphere,
        fit the XY footprint so tall single assemblies fill the view.
        """
        camera = self.renderer.GetActiveCamera()
        x0, x1, y0, y1, z0, z1 = self.renderer.ComputeVisiblePropBounds()
        if x0 > x1:  # nothing visible
            self.renderer.ResetCamera()
            return
        half_width = 0.55 * max(x1 - x0, y1 - y0)
        distance = half_width / math.tan(0.5 * math.radians(camera.GetViewAngle()))
        cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        camera.SetFocalPoint(cx, cy, 0.5 * (z0 + z1))
        camera.SetPosition(cx, cy, z1 + distance)
        camera.SetViewUp(0, 1, 0)
        camera.SetParallelScale(half_width)
        self.renderer.ResetCameraClippingRange()

    def set_parallel_projection(self, enabled: bool) -> None:
        """Switch between perspective and parallel projection, keeping the apparent size."""
        self.parallel = bool(enabled)
        self._apply_projection()

    def _apply_projection(self) -> None:
        camera = self.renderer.GetActiveCamera()
        parallel = self.parallel or self.view_2d
        if bool(camera.GetParallelProjection()) == parallel:
            return
        tan_half_angle = math.tan(0.5 * math.radians(camera.GetViewAngle()))
        if parallel:
            camera.SetParallelScale(camera.GetDistance() * tan_half_angle)
        else:
            distance = camera.GetParallelScale() / tan_half_angle
            focal = camera.GetFocalPoint()
            direction = camera.GetDirectionOfProjection()
            camera.SetPosition(*(f - distance * d for f, d in zip(focal, direction, strict=True)))
        camera.SetParallelProjection(parallel)
        self.renderer.ResetCameraClippingRange()

    def set_2d_mode(self, enabled: bool) -> None:
        """Toggle between the 3D view and a 2D top view using parallel projection.

        The interactor style is left untouched: VTK.wasm ignores interactor
        style changes and cannot serialize ``vtkInteractorStyleManipulator``.
        In 2D the client remaps the left button to the middle one (pan), see
        ``module/serve/view_2d.js``, so the trackball style only pans and zooms.
        """
        if bool(enabled) == self.view_2d:
            return
        self.view_2d = bool(enabled)
        self._apply_projection()
        if self.view_2d:
            self._top_view()
