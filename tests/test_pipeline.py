import json

import pytest

from vera_in_viewer.pipeline import CoreScene


@pytest.fixture
def scene(mini_model):
    scene = CoreScene()
    scene.load(mini_model)
    return scene


def _visible_actors(scene):
    return [layer for layer in scene._layers if layer.actor.GetVisibility()]


def test_actor_count(scene):
    # pins: 3 + 2 + 1 rings, rod: 2 rings, grid, baffle, vessel (moderator hidden), plates x2, outline
    assert scene.renderer.GetActors().GetNumberOfItems() == 14


def test_default_visibility(scene):
    groups = {layer.group for layer in _visible_actors(scene)}
    assert groups == {"Pins", "Grid", "Baffle", "Vessel"}
    vessel = [layer.material for layer in scene._layers if layer.group == "Vessel"]
    assert vessel == ["ss"]  # moderator inside the barrel is never rendered


def test_material_and_toggle_visibility(scene):
    scene.update_appearance(hidden_materials={"zirc4"}, toggles={**scene.toggles, "Grid": False})
    visible = _visible_actors(scene)
    assert all(layer.material != "zirc4" for layer in visible)
    assert all(layer.group != "Grid" for layer in visible)


def test_control_rod_position(scene, mini_model):
    rods = [layer for layer in scene._layers if layer.bank == "A"]
    step = mini_model.rod_step_size
    # All rods start inserted
    assert rods[0].actor.GetPosition()[2] == pytest.approx(0)
    scene.update_appearance(bank_positions={"A": 20}, z_scale=0.5)
    assert rods[0].actor.GetPosition()[2] == pytest.approx(20 * step * 0.5)
    assert rods[0].actor.GetScale()[2] == pytest.approx(0.5)


def test_slice_filters_instances(scene):
    total = sum(p.instances.GetNumberOfPoints() for p in scene._pin_sets)
    scene.set_z_range((0.0, 2.0))
    sliced = sum(p.instances.GetNumberOfPoints() for p in scene._pin_sets)
    assert sliced < total
    assert all(layer.group != "Plate" for layer in _visible_actors(scene))
    scene.set_z_range(None)
    assert sum(p.instances.GetNumberOfPoints() for p in scene._pin_sets) == total


def test_resolution_updates_pin_sources(scene):
    piece = scene._pin_sets[0].cylinder.output
    before = piece.GetNumberOfPoints()
    scene.set_resolution(scene.resolution * 2)
    assert piece.GetNumberOfPoints() > before


def test_hidden_material_masks_pin_layers(scene):
    from vera_in_viewer.pipeline import triangle_count

    fuel = next(p for p in scene._pin_sets if p.glyphs.cell.mats == ("U31", "he", "zirc4"))
    pieces = fuel.cylinder.pieces
    clad, pellet = triangle_count(pieces["zirc4"]), triangle_count(pieces["U31"])
    scene.update_appearance(hidden_materials={"he"})
    assert triangle_count(pieces["he"]) == 0
    # The gap is gone: the pellet gets its side wall and the clad its inner wall
    assert triangle_count(pieces["zirc4"]) == clad + 2 * scene.resolution
    assert triangle_count(pieces["U31"]) == pellet + 2 * scene.resolution
    scene.update_appearance(hidden_materials=set())
    assert triangle_count(pieces["zirc4"]) == clad
    assert triangle_count(pieces["U31"]) == pellet


def test_scene_is_serializable_for_wasm(scene):
    from vtkmodules.vtkSerializationManager import vtkObjectManager

    manager = vtkObjectManager()
    manager.Initialize()
    window_id = manager.RegisterObject(scene.render_window)
    manager.UpdateStatesFromObjects()
    classes = {
        json.loads(manager.GetState(i)).get("ClassName") for i in manager.GetAllDependencies(window_id)
    }
    assert "vtkOpenGLGlyph3DMapper" in classes


def test_load_resets_view_settings(scene, mini_model):
    scene.update_appearance(toggles={"Grid": False, "Baffle": False, "Vessel": False, "Plate": True})
    scene.set_z_range((0.0, 2.0))
    scene.load(mini_model)
    assert scene.toggles == {"Grid": True, "Baffle": True, "Vessel": True, "Plate": False}
    assert scene.z_range is None


def test_color_by_bank_uses_instance_colors(scene, mini_model):
    rods = [layer for layer in scene._layers if layer.bank == "A"]
    instances = rods[0].actor.GetMapper().GetInput()
    colors = instances.GetPointData().GetArray("bank_color")
    assert colors.GetNumberOfTuples() == instances.GetNumberOfPoints()
    assert not rods[0].actor.GetMapper().GetScalarVisibility()
    scene.update_appearance(color_by_bank=True)
    assert all(layer.actor.GetMapper().GetScalarVisibility() for layer in rods)


def test_2d_mode(scene):
    camera = scene.renderer.GetActiveCamera()
    style = scene.interactor.GetInteractorStyle()
    scene.set_2d_mode(True)
    assert camera.GetParallelProjection()
    assert camera.GetDirectionOfProjection() == pytest.approx((0, 0, -1))
    assert camera.GetViewUp() == pytest.approx((0, 1, 0))
    scene.set_2d_mode(False)
    assert not camera.GetParallelProjection()
    assert scene.interactor.GetInteractorStyle() is style  # remapping happens client side


def test_reset_camera_keeps_orientation(scene):
    camera = scene.renderer.GetActiveCamera()
    scene.orient("iso")
    camera.Dolly(3)
    direction = camera.GetDirectionOfProjection()
    distance = camera.GetDistance()
    scene.reset_camera()
    assert camera.GetDirectionOfProjection() == pytest.approx(direction)
    assert camera.GetDistance() != pytest.approx(distance)


@pytest.mark.parametrize(
    ("view", "direction", "up"),
    [
        ("top", (0, 0, -1), (0, 1, 0)),
        ("side", (1, 0, 0), (0, 0, 1)),
        ("iso", tuple(-(3**-0.5) for _ in range(3)), None),
    ],
)
def test_orient(scene, view, direction, up):
    camera = scene.renderer.GetActiveCamera()
    scene.orient(view)
    assert camera.GetDirectionOfProjection() == pytest.approx(direction)
    if up is not None:
        assert camera.GetViewUp() == pytest.approx(up)


def test_parallel_projection_keeps_apparent_size(scene):
    camera = scene.renderer.GetActiveCamera()
    scene.orient("iso")
    distance = camera.GetDistance()
    scene.set_parallel_projection(True)
    assert camera.GetParallelProjection()
    scene.set_parallel_projection(False)
    assert not camera.GetParallelProjection()
    assert camera.GetDistance() == pytest.approx(distance)


def test_2d_mode_restores_projection_preference(scene):
    camera = scene.renderer.GetActiveCamera()
    scene.set_parallel_projection(True)
    scene.set_2d_mode(True)
    scene.set_2d_mode(False)
    assert camera.GetParallelProjection()
    scene.set_parallel_projection(False)
    scene.set_2d_mode(True)
    assert camera.GetParallelProjection()


def test_triangle_count():
    from vera_in_viewer import geometry
    from vera_in_viewer.pipeline import triangle_count

    assert triangle_count(geometry.unit_cube()) == 12
    # solid cylinder: wall quads + 2 hexagons
    assert triangle_count(geometry.ConcentricCylinderSource((1.0,)).update()) == 6 * 2 + 2 * 4
    assert triangle_count(geometry.box((0, 0, 0), (1, 1, 1))) == 12


def test_stats(scene):
    stats = scene.stats()
    assert stats["actors"] == 14
    assert stats["visible_actors"] == len(_visible_actors(scene))
    assert stats["triangles"] > 0

    scene.update_appearance(hidden_materials={"zirc4"})
    hidden = scene.stats()
    assert hidden["visible_actors"] < stats["visible_actors"]
    assert hidden["triangles"] < stats["triangles"]

    # Higher resolution pins means more triangles for the same actors
    scene.set_resolution(scene.resolution * 2)
    finer = scene.stats()
    assert finer["visible_actors"] == hidden["visible_actors"]
    assert finer["triangles"] > hidden["triangles"]
