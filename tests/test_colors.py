from vera_in_viewer.colors import MATERIAL_PRESETS, PALETTE, ColorManager, material_color_manager, to_hex


def test_presets_are_kept():
    manager = material_color_manager()
    for name, color in MATERIAL_PRESETS.items():
        assert manager.get_color(name) == color


def test_least_used_assignment_is_deterministic():
    first = ColorManager()
    second = ColorManager()
    names = [f"m{i}" for i in range(len(PALETTE) + 3)]
    assert [first.get_color(n) for n in names] == [second.get_color(n) for n in names]
    # The whole palette is used before any color gets reused
    assert len({first.get_color(n) for n in names[: len(PALETTE)]}) == len(PALETTE)


def test_ids_are_stable():
    manager = ColorManager()
    assert manager.get_id("a") == 1
    assert manager.get_id("b") == 2
    assert manager.get_id("a") == 1
    assert manager.has_name("a")
    assert not manager.has_name("c")


def test_to_hex():
    assert to_hex((1.0, 0.0, 0.5, 1.0)) == "#ff0080"
