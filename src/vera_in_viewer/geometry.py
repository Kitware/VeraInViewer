"""VTK geometry helpers.

VTK (C++) has no ``vtkConcentricCylinderSource`` like vtk.js, so
:class:`ConcentricCylinderSource` is a port of it: a single mesh holding every
material layer of a pin where vertical walls are only generated at the
boundary of visible layers (``skip_inner_faces``) and masked layers are left
out.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersSources import vtkCubeSource

LAYER_ARRAY = "layer"


def _set_polys(output: vtkPolyData, faces: list[np.ndarray]) -> None:
    """Assign polygons given as a list of ``(n, k)`` connectivity blocks."""
    faces = [f for f in faces if len(f)]
    sizes = np.concatenate([np.full(len(f), f.shape[1]) for f in faces]) if faces else np.zeros(0)
    offsets = np.concatenate(([0], np.cumsum(sizes))).astype(np.int32)
    connectivity = (
        np.concatenate([f.ravel() for f in faces]).astype(np.int32) if faces else np.zeros(0, np.int32)
    )
    # Use fixed width arrays: vtkIdTypeArray is 64-bit on the server but
    # 32-bit in the wasm32 client, which would garble the connectivity.
    polys = vtkCellArray()
    polys.SetData(numpy_to_vtk(offsets, deep=True), numpy_to_vtk(connectivity, deep=True))
    output.SetPolys(polys)


class ConcentricCylinderSource:
    """Python port of vtk.js ``vtkConcentricCylinderSource``.

    Concentric layers of a cylinder of ``height`` centered on ``center`` along
    Z, ``radius`` being the outer radius of each layer. Every cell carries the
    ``cell_fields`` value of its layer in the ``layer`` cell array. Layers can
    be hidden with :meth:`set_mask_layer`; with ``skip_inner_faces`` the walls
    between two visible layers are not generated.

    The default unit height centered on the origin makes it usable as a glyph
    that gets scaled along Z by the segment length.
    """

    def __init__(
        self,
        radius: Sequence[float] = (0.5,),
        cell_fields: Sequence[float] | None = None,
        height: float = 1.0,
        resolution: int = 6,
        start_theta: float = 0.0,
        end_theta: float = 360.0,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        skip_inner_faces: bool = True,
    ):
        self.radius = list(radius)
        self.cell_fields = list(cell_fields) if cell_fields is not None else []
        self.height = height
        self.resolution = resolution
        self.start_theta = start_theta
        self.end_theta = end_theta
        self.center = tuple(center)
        self.skip_inner_faces = skip_inner_faces
        self.mask: list[bool] | None = None  # True to skip a layer

    def _validate_cell_fields(self) -> None:
        while len(self.cell_fields) < len(self.radius):
            self.cell_fields.append(len(self.cell_fields))

    def clear_radius(self) -> None:
        self.radius = []
        self.cell_fields = []

    def add_radius(self, radius: float, cell_field: float | None = None) -> None:
        self.radius.append(radius)
        if cell_field is not None:
            self.cell_fields.append(cell_field)
        self._validate_cell_fields()

    def remove_mask(self) -> None:
        self.mask = None

    def set_mask_layer(self, index: int, hidden: bool) -> bool:
        """Hide or show a layer, return True when it changed."""
        changed = False
        if self.mask is None and hidden:
            changed = True
            self.mask = []
        if self.mask is not None:
            self.mask.extend([False] * (index + 1 - len(self.mask)))
            changed = changed or self.mask[index] != bool(hidden)
            self.mask[index] = bool(hidden)
        return changed

    def _masked(self, index: int) -> bool:
        return self.mask is not None and index < len(self.mask) and self.mask[index]

    def update(self, output: vtkPolyData | None = None) -> vtkPolyData:
        """Generate the mesh, in place into ``output`` when provided."""
        if not self.radius:
            raise ValueError("No radius defined")
        self._validate_cell_fields()

        num_layers = len(self.radius)
        z_ref = self.height / 2.0
        resolution = max(2, int(self.resolution))
        skip = self.skip_inner_faces
        masked = self._masked

        start = math.radians(min(self.start_theta, self.end_theta))
        end = math.radians(max(self.start_theta, self.end_theta))
        theta_resolution = resolution
        partial = False
        if end >= start + 2 * math.pi:
            end = start + 2 * math.pi  # complete, closed cylinder
        else:
            # Extra point at end_theta and radial caps on the sides
            theta_resolution += 1
            partial = True
        t = theta_resolution
        theta = start + np.arange(t) * (end - start) / resolution

        # Points: top and bottom centers (only used if partial), then for each
        # layer its top ring followed by its bottom ring.
        cos, sin = np.cos(theta), np.sin(theta)
        rings = []
        for radius in self.radius:
            for z in (z_ref, -z_ref):
                rings.append(np.column_stack((radius * cos, radius * sin, np.full(t, z))))
        points = np.vstack([[(0.0, 0.0, z_ref), (0.0, 0.0, -z_ref)], *rings])
        points += np.asarray(self.center, dtype=float)

        i = np.arange(resolution)
        j = (i + 1) % t
        faces: list[np.ndarray] = []
        fields: list[np.ndarray] = []

        def add(block: np.ndarray, value: float) -> None:
            block = np.atleast_2d(block)
            faces.append(block)
            fields.append(np.full(len(block), value, dtype=np.float32))

        # Core
        value = self.cell_fields[0]
        if not masked(0):
            ring = np.arange(t) + 2
            add(np.concatenate(([0], ring)) if partial else ring, value)  # top disk
            ring = 2 * t - np.arange(t) - 1 + 2
            add(np.concatenate(([1], ring)) if partial else ring, value)  # bottom disk
            if not skip or masked(1) or num_layers == 1:
                add(np.column_stack((j + 2, i + 2, i + t + 2, j + t + 2)), value)
            if partial:
                add(np.array([[2, 0, 1, t + 2], [0, t + 1, 2 * t + 1, 1]]), value)

        # Layers
        for layer in range(1, num_layers):
            if masked(layer):
                continue
            offset = t * 2 * (layer - 1) + 2
            a = offset + 2 * t  # next layer offset
            last_layer = layer == num_layers - 1
            value = self.cell_fields[layer]

            add(np.column_stack((i + offset, j + offset, j + a, i + a)), value)  # top
            add(np.column_stack((j + offset + t, i + offset + t, i + a + t, j + a + t)), value)  # bottom
            if not skip or masked(layer - 1):  # inner
                add(np.column_stack((i + offset, j + offset, j + t + offset, i + t + offset)), value)
            if not skip or last_layer or masked(layer + 1):  # outer
                add(np.column_stack((j + a, i + a, i + t + a, j + t + a)), value)
            if partial:
                r = resolution
                add(
                    np.array(
                        [
                            [a, offset, t + offset, t + a],
                            [r + a, r + offset, r + t + offset, r + t + a],
                        ]
                    ),
                    value,
                )

        output = output if output is not None else vtkPolyData()
        output.Initialize()
        vtk_points = vtkPoints()
        vtk_points.SetData(numpy_to_vtk(points.astype(np.float32), deep=True))
        output.SetPoints(vtk_points)
        _set_polys(output, faces)
        field = numpy_to_vtk(np.concatenate(fields) if fields else np.zeros(0, dtype=np.float32), deep=True)
        field.SetName(LAYER_ARRAY)
        output.GetCellData().SetScalars(field)
        return output


def extract_cells(
    polydata: vtkPolyData, field: str, values: Sequence[float], output: vtkPolyData | None = None
) -> vtkPolyData:
    """Polygons of ``polydata`` whose ``field`` cell value is in ``values``.

    Points are shared as is, only the polygons get filtered.
    """
    output = output if output is not None else vtkPolyData()
    polys = polydata.GetPolys()
    offsets = vtk_to_numpy(polys.GetOffsetsArray())
    connectivity = vtk_to_numpy(polys.GetConnectivityArray())
    keep = np.isin(vtk_to_numpy(polydata.GetCellData().GetArray(field)), values)
    faces = [connectivity[offsets[c] : offsets[c + 1]].reshape(1, -1) for c in np.flatnonzero(keep)]
    output.Initialize()
    output.SetPoints(polydata.GetPoints())
    _set_polys(output, faces)
    return output


def unit_cube() -> vtkPolyData:
    """Unit cube centered on the origin, used as glyph for grids and baffle."""
    cube = vtkCubeSource()
    cube.Update()
    output = vtkPolyData()
    output.ShallowCopy(cube.GetOutput())
    return output


def box(center: tuple[float, float, float], lengths: tuple[float, float, float]) -> vtkPolyData:
    cube = vtkCubeSource()
    cube.SetCenter(center)
    cube.SetXLength(lengths[0])
    cube.SetYLength(lengths[1])
    cube.SetZLength(lengths[2])
    cube.Update()
    output = vtkPolyData()
    output.ShallowCopy(cube.GetOutput())
    return output


def glyph_instances(
    centers: np.ndarray, scales: np.ndarray, output: vtkPolyData | None = None
) -> vtkPolyData:
    """Point set with a 3-component ``scale`` array, input of vtkGlyph3DMapper."""
    output = output if output is not None else vtkPolyData()
    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(centers, dtype=np.float32), deep=True))
    scale = numpy_to_vtk(np.ascontiguousarray(scales, dtype=np.float32), deep=True)
    scale.SetName("scale")
    output.Initialize()
    output.SetPoints(points)
    output.GetPointData().AddArray(scale)
    return output
