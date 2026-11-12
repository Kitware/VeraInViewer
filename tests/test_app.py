from trame.app import get_server

from vera_in_viewer.app import VeraInViewer
from vera_in_viewer.parser import parse_file
from vera_in_viewer.samples import sample_files


def test_app_load(mini_core_path):
    server = get_server("vera_test", client_type="vue3")
    app = VeraInViewer(server)
    app.load(parse_file(mini_core_path), mini_core_path.name)

    state = app.state
    assert state.title == "Mini core"
    assert [b["name"] for b in state.banks] == ["A"]
    assert state.bank_positions == {"A": 0}
    assert state.state_index == -1
    assert state.states[0]["title"] == "All rods inserted"
    assert state.state_params == []

    state.state_index = 0
    app._on_state()
    assert state.bank_positions == {"A": 10}
    assert state.available_toggles == ["Grid", "Baffle", "Vessel", "Plate"]
    assert state.visible_toggles == ["Grid", "Baffle", "Vessel"]
    assert len(state.slice_items) == 1 + 4
    assert state.stats == app.scene.stats()
    assert state.stats["actors"] == 14

    app.set_all_rods(True)
    assert state.bank_positions == {"A": 20}


def test_app_reports_invalid_content():
    server = get_server("vera_test_invalid", client_type="vue3")
    app = VeraInViewer(server)
    app.load_content(b"<not-xml", "broken.xml")
    assert app.state.error.startswith("Unable to parse broken.xml")
    app.load_content(b'<ParameterList name="CASEID"/>', "empty.xml")
    assert app.state.error.startswith("Unable to load empty.xml")


def test_app_clear(mini_core_path):
    server = get_server("vera_test_clear", client_type="vue3")
    app = VeraInViewer(server)
    assert not app.state.loaded
    app.load(parse_file(mini_core_path), mini_core_path.name)
    assert app.state.loaded
    assert app.state.file_name == mini_core_path.name

    app.clear()
    assert not app.state.loaded
    assert app.model is None
    assert app.state.file_name == ""
    assert app.scene.renderer.GetViewProps().GetNumberOfItems() == 0
    assert app.state.stats == {"actors": 0, "visible_actors": 0, "triangles": 0}


def test_app_load_sample():
    server = get_server("vera_test_sample", client_type="vue3")
    app = VeraInViewer(server)
    assert [s["name"] for s in app.state.samples] == [f.name for f in sample_files()]
    sample = app.state.samples[0]
    assert sample["preview"].startswith("__vera_samples/")

    app._on_active_file(sample["key"])
    assert app.state.loaded
    assert app.state.file_name == sample["name"]
    assert app.state.title == sample["title"]


def test_supports_screenshot():
    from vera_in_viewer.app import supports_screenshot

    assert not supports_screenshot("9.7.1")
    assert supports_screenshot("9.7.20261004")
