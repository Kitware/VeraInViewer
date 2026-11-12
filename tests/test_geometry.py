import numpy as np
import pytest
from vtkmodules.util.numpy_support import vtk_to_numpy

from vera_in_viewer.geometry import LAYER_ARRAY, ConcentricCylinderSource, extract_cells
from vera_in_viewer.pipeline import triangle_count

RES = 6
DISK = RES - 2  # triangles of a full disk polygon
QUADS = 2 * RES  # triangles of a ring of quads


def _layers(polydata):
    return vtk_to_numpy(polydata.GetCellData().GetArray(LAYER_ARRAY))


def test_single_layer():
    output = ConcentricCylinderSource((0.5,), resolution=RES).update()
    assert output.GetNumberOfPoints() == 2 + 2 * RES
    assert triangle_count(output) == 2 * DISK + QUADS
    y = 0.5 * np.sin(np.pi / 3)
    assert output.GetBounds() == pytest.approx((-0.5, 0.5, -y, y, -0.5, 0.5))


def test_skip_inner_faces():
    source = ConcentricCylinderSource((0.3, 0.4, 0.5), resolution=RES)
    # core disks, top/bottom annulus of the 2 other layers, outer wall only
    assert triangle_count(source.update()) == 2 * DISK + 4 * QUADS + QUADS
    source.skip_inner_faces = False
    assert triangle_count(source.update()) == 2 * DISK + QUADS + 2 * 4 * QUADS


def test_mask_adds_walls_around_hidden_layer():
    source = ConcentricCylinderSource((0.3, 0.4, 0.5), cell_fields=(7, 8, 9), resolution=RES)
    assert source.set_mask_layer(1, True)
    assert not source.set_mask_layer(1, True)
    output = source.update()
    # core with its side wall + outer layer with inner and outer walls
    assert triangle_count(output) == (2 * DISK + QUADS) + (2 * QUADS + 2 * QUADS)
    assert set(_layers(output)) == {7, 9}

    assert source.set_mask_layer(1, False)
    assert triangle_count(source.update()) == 2 * DISK + 4 * QUADS + QUADS


def test_masked_core():
    source = ConcentricCylinderSource((0.3, 0.5), resolution=RES)
    source.set_mask_layer(0, True)
    output = source.update()
    assert triangle_count(output) == 4 * QUADS
    assert set(_layers(output)) == {1}


def test_partial_disk():
    source = ConcentricCylinderSource((0.5,), resolution=2, start_theta=0, end_theta=90)
    output = source.update()
    # quarter disks (center + 3 arc points), 2 side quads and 2 radial caps
    assert output.GetNumberOfPoints() == 2 + 2 * 3
    assert triangle_count(output) == 2 * 2 + 2 * 2 + 2 * 2
    x0, x1, y0, y1, _, _ = output.GetBounds()
    assert (x0, y0) == pytest.approx((0, 0))
    assert (x1, y1) == pytest.approx((0.5, 0.5))


def test_center_and_height():
    output = ConcentricCylinderSource((1.0,), height=4, center=(10, 20, 2)).update()
    _, _, _, _, z0, z1 = output.GetBounds()
    assert (z0, z1) == pytest.approx((0, 4))
    assert output.GetCenter()[:2] == pytest.approx((10, 20), abs=0.1)


def test_radius_api():
    source = ConcentricCylinderSource()
    source.clear_radius()
    with pytest.raises(ValueError):
        source.update()
    source.add_radius(0.2, 5)
    source.add_radius(0.4)
    assert source.cell_fields == [5, 1]


def test_extract_cells():
    output = ConcentricCylinderSource((0.3, 0.5), resolution=RES).update()
    core = extract_cells(output, LAYER_ARRAY, [0])
    outer = extract_cells(output, LAYER_ARRAY, [1])
    assert triangle_count(core) == 2 * DISK
    assert triangle_count(core) + triangle_count(outer) == triangle_count(output)
    assert triangle_count(extract_cells(output, LAYER_ARRAY, [])) == 0
