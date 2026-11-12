from pathlib import Path

import pytest

from vera_in_viewer import samples

DATA = Path(__file__).parent / "data"


@pytest.fixture
def mini_core_path() -> Path:
    return DATA / "mini_core.xml"


@pytest.fixture
def mini_model(mini_core_path):
    from vera_in_viewer.model import build_core_model
    from vera_in_viewer.parser import parse_file

    return build_core_model(parse_file(mini_core_path))


def sample_files() -> list[Path]:
    """VERAin samples shipped with the package."""
    return samples.sample_files()
