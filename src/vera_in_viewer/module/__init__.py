"""Client side assets loaded by the trame application."""

from pathlib import Path

from ..samples import SAMPLES_DIR

serve = {
    "__vera_in_viewer": str(Path(__file__).with_name("serve").resolve()),
    "__vera_samples": str(SAMPLES_DIR),
}
scripts = ["__vera_in_viewer/view_2d.js", "__vera_in_viewer/frame_timing.js"]
styles = ["__vera_in_viewer/landing.css", "__vera_in_viewer/panels.css"]
