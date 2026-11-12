import numpy as np
import pytest

from vera_in_viewer.model import build_core_model, grid_thickness, overlap_mask
from vera_in_viewer.parser import parse_file

from .conftest import sample_files


def _pins_by_mats(model):
    return {(p.cell.mats, p.bank): p for p in model.pins}


def test_pin_instances(mini_model):
    pins = _pins_by_mats(mini_model)
    # 5 assemblies: 3 fuel pins over the FUEL layer, 3 plugs over PLUG, 1 guide tube on both
    assert len(pins[(("U31", "he", "zirc4"), None)].lengths) == 15
    assert len(pins[(("zirc4",), None)].lengths) == 15
    assert len(pins[(("mod", "zirc4"), None)].lengths) == 10
    assert mini_model.instance_count == 15 + 15 + 10 + 1


def test_pin_positions_are_centered_in_assemblies(mini_model):
    rod = _pins_by_mats(mini_model)[(("b4c", "ss"), "A")]
    # center assembly spans [3, 6], 2 pins of 1.4 -> margin 0.1, last pin of the map is bottom right
    np.testing.assert_allclose(rod.centers, [[5.2, 3.8, 7.5]], atol=1e-5)
    np.testing.assert_allclose(rod.lengths, [7.0])

    fuel = _pins_by_mats(mini_model)[(("U31", "he", "zirc4"), None)]
    assert fuel.centers[:, :2].min() >= 0
    assert fuel.centers[:, :2].max() <= mini_model.core_size * mini_model.apitch
    np.testing.assert_allclose(np.unique(fuel.centers[:, 2]), [6.0])


def test_states_are_sorted_and_cumulative(mini_model):
    assert [s.label for s in mini_model.states] == ["1", "2"]
    assert mini_model.states[0].title == "Start"
    assert mini_model.states[0].bank_positions == {"A": 10}
    assert mini_model.states[1].bank_positions == {"A": 5}
    assert mini_model.banks == ["A"]
    assert mini_model.rod_step_size == pytest.approx(0.4)


def test_elevations(mini_model):
    assert mini_model.elevations == [0.0, 2.0, 4.0, 10.0, 11.0]
    assert mini_model.slices[0] == (0.0, 2.0)


def test_spacer_grids(mini_model):
    (grid,) = mini_model.grids
    assert grid.material == "inc"
    # (num_pins + 1) straps in each direction per assembly
    assert len(grid.centers) == 5 * 2 * 3
    thickness = grid_thickness(1.4, 10, 8.19, 1.0, 2)
    assert thickness > 0
    np.testing.assert_allclose(sorted(set(grid.sizes[:, 0].round(6))), sorted({round(thickness, 6), 2.8}))


def test_vessel_and_plates(mini_model):
    rings = {r.label: r for r in mini_model.rings}
    assert rings["vessel"].hidden_layers == {0}
    assert rings["lower plate"].center[2] == pytest.approx(-0.5)
    assert rings["upper plate"].center[2] == pytest.approx(12.75)
    assert mini_model.outer_radius == pytest.approx(6.5)


def test_baffle_pieces(mini_model):
    baffle = mini_model.baffle
    assert baffle is not None
    assert len(baffle.centers) == 12  # 4 arms x 3 exposed edges
    lengths = np.sort(np.max(baffle.sizes[:, :2], axis=1))
    # 4 outer edges with two convex corners, 8 side edges with one concave corner
    np.testing.assert_allclose(lengths[:8], 3.0 - 0.1 + 0.3)
    np.testing.assert_allclose(lengths[8:], 3.0 + 0.6)


def _assert_baffle_outside_assemblies(model, shape):
    size, pitch = model.core_size, model.apitch
    lo = model.baffle.centers[:, :2] - model.baffle.sizes[:, :2] / 2
    hi = model.baffle.centers[:, :2] + model.baffle.sizes[:, :2] / 2
    for j in range(size):
        for i in range(size):
            if not shape[j * size + i]:
                continue
            x0, y0 = i * pitch, (size - 1 - j) * pitch
            overlap = np.minimum(hi, [x0 + pitch, y0 + pitch]) - np.maximum(lo, [x0, y0])
            assert not np.any((overlap > 1e-4).all(axis=1)), (i, j)


def test_baffle_does_not_overlap_assemblies(mini_model, mini_core_path):
    shape = parse_file(mini_core_path)["CASEID"]["CORE"]["shape"]
    _assert_baffle_outside_assemblies(mini_model, shape)


def test_overlap_mask():
    centers = np.array([1.0, 5.0, 9.0])
    lengths = np.array([2.0, 2.0, 2.0])
    np.testing.assert_array_equal(overlap_mask(centers, lengths, 2.0, 6.0), [False, True, False])


@pytest.mark.parametrize("path", sample_files(), ids=lambda p: p.name)
def test_sample_files(path):
    data = parse_file(path)
    model = build_core_model(data)
    assert model.instance_count > 0
    assert set(model.material_colors) == set(model.materials)
    if model.baffle is not None:
        _assert_baffle_outside_assemblies(model, data["CASEID"]["CORE"]["shape"])
