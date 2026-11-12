"""trame application rendering the VERA 3D core with VTK.wasm (trame-vtklocal)."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

from packaging.version import Version
from trame.app import TrameApp
from trame.app.file_upload import ClientFile
from trame.decorators import change, controller
from trame.ui.vuetify4 import VAppLayout
from trame.widgets import html, vtklocal
from trame.widgets import vuetify4 as v4
from vtkmodules.vtkCommonCore import vtkVersion

from . import module
from .colors import to_hex
from .model import CoreModel, build_core_model
from .parser import parse_string
from .pipeline import TOGGLES, CoreScene
from .samples import SAMPLES_DIR, list_samples

logger = logging.getLogger(__name__)

FILE_PATTERNS = ("*.xml", "*.xml.gold")
HIGH_RESOLUTION_INSTANCE_LIMIT = 50_000
# Give up waiting for the client acknowledgment after that many seconds
SYNC_TIMEOUT = 10.0
# Fake entry of the state drop down selected at startup
ALL_RODS_IN = -1
ALL_RODS_IN_TITLE = "All rods inserted"
# Client side screenshots (LocalView.download_screenshot) fail with VTK 9.7.1 and work with
# the 9.7 nightly builds (single VTK.wasm build with WebGL and WebGPU)
SCREENSHOT_VTK_VERSION = Version("9.7.20260716")


def supports_screenshot(vtk_version: str | None = None) -> bool:
    return Version(vtk_version or vtkVersion.GetVTKVersion()) >= SCREENSHOT_VTK_VERSION


EMPTY_STATS = {"actors": 0, "visible_actors": 0, "triangles": 0}
SAMPLE_PREFIX = "sample:"
SAMPLES_URL = "__vera_samples"


def model_info(model: CoreModel) -> list[str]:
    info = [
        f"{model.core_size}x{model.core_size} core",
        f"{model.instance_count:,} pin segments",
        f"{len(model.materials)} materials",
    ]
    if model.states:
        info.append(f"{len(model.states)} state{'s' if len(model.states) > 1 else ''}")
    return info


def _format_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


class VeraInViewer(TrameApp):
    def __init__(self, server=None):
        super().__init__(server)
        self.server.enable_module(module)
        self.server.cli.add_argument(
            "--data",
            help="VERAin XML file or directory of files to make available",
            default=None,
        )
        args, _ = self.server.cli.parse_known_args()

        self.scene = CoreScene()
        self.model: CoreModel | None = None
        self.files: dict[str, Path] = {}
        self._sync_time: float | None = None  # set while the client processes an update
        self._sync_dirty = False
        self._sync_camera = False

        self._init_state()
        self._build_ui()
        self._register_samples()
        self._register_files(args.data)

    # -------------------------------------------------------------------------
    # File handling
    # -------------------------------------------------------------------------

    def _register_samples(self) -> None:
        samples = list_samples()
        for sample in samples:
            self.files[SAMPLE_PREFIX + sample["name"]] = SAMPLES_DIR / sample["name"]
        self.state.samples = [
            {
                **sample,
                "key": SAMPLE_PREFIX + sample["name"],
                "preview": sample["preview"] and f"{SAMPLES_URL}/{sample['preview']}",
            }
            for sample in samples
        ]

    def _register_files(self, data: str | None) -> None:
        if data is None:
            return
        path = Path(data).expanduser().resolve()
        candidates = [path] if path.is_file() else sorted({f for p in FILE_PATTERNS for f in path.glob(p)})
        self.files.update({f.name: f for f in candidates})
        self.state.file_items = [f.name for f in candidates]
        if len(candidates) == 1:
            self.state.active_file = candidates[0].name

    @change("active_file")
    def _on_active_file(self, active_file, **_):
        if active_file in self.files:
            self.state.upload = None
            path = self.files[active_file]
            self.load_content(path.read_bytes(), path.name)

    @change("upload")
    def _on_upload(self, upload, **_):
        if not upload:
            return
        if isinstance(upload, list):
            upload = upload[0]
        file = ClientFile(upload)
        if file.is_empty:
            return
        self.state.active_file = None
        self.load_content(file.content, file.name)

    def load_content(self, content: bytes, name: str) -> None:
        """Parse VERAin XML content and display it, reporting invalid files in the UI."""
        try:
            data = parse_string(content)
        except ET.ParseError as error:
            self.state.error = f"Unable to parse {name}: {error}"
            return
        self.load(data, name)

    def load(self, data: dict, name: str = "") -> None:
        try:
            model = build_core_model(data)
        except (KeyError, ValueError, TypeError) as error:
            logger.exception("Unable to load %s", name)
            self.state.error = f"Unable to load {name}: {error!r}"
            return

        self.model = model
        resolution = 6 if model.instance_count > HIGH_RESOLUTION_INSTANCE_LIMIT else 24
        self.scene.set_resolution(resolution)
        self.scene.update_appearance(z_scale=1.0)
        self.scene.set_2d_mode(False)
        self.scene.set_parallel_projection(False)
        self.scene.load(model)

        with self.state:
            self.state.loaded = True
            self.state.error = None
            self.state.title = model.title
            self.state.file_name = name
            self.state.info = model_info(model)
            self.state.resolution = resolution
            self.state.z_scale = 1.0
            self.state.view_2d = False
            self.state.parallel = False
            self.state.slice_items = [{"title": "Full core", "value": -1}] + [
                {"title": f"{z0:g} to {z1:g}", "value": idx} for idx, (z0, z1) in enumerate(model.slices)
            ]
            self.state.slice_index = -1
            self.state.materials = [
                {"name": m, "color": to_hex(model.material_colors[m])} for m in model.materials
            ]
            self.state.hidden_materials = []
            self.state.available_toggles = [
                name
                for name, present in (
                    ("Grid", bool(model.grids)),
                    ("Baffle", model.baffle is not None),
                    ("Vessel", any(r.group == "Vessel" for r in model.rings)),
                    ("Plate", any(r.group == "Plate" for r in model.rings)),
                )
                if present
            ]
            self.state.visible_toggles = [t for t in self.state.available_toggles if self.scene.toggles[t]]
            self.state.banks = [{"name": b, "color": to_hex(model.bank_colors[b])} for b in model.banks]
            self.state.max_step = model.maxstep or 0
            self.state.bank_positions = dict(self.scene.bank_positions)
            self.state.hidden_banks = []
            self.state.color_by_bank = False
            self.state.states = (
                [{"title": ALL_RODS_IN_TITLE, "value": ALL_RODS_IN}]
                + [{"title": f"{s.label}: {s.title}", "value": idx} for idx, s in enumerate(model.states)]
                if model.states
                else []
            )
            self.state.state_index = ALL_RODS_IN if model.states else None
            self._update_state_params()

        self.push(camera=True)

    # -------------------------------------------------------------------------
    # View settings
    # -------------------------------------------------------------------------

    def _init_state(self) -> None:
        self.state.update(
            {
                "loaded": False,
                "title": "VeraInViewer",
                "file_name": "",
                "info": [],
                "error": None,
                "file_items": [],
                "samples": [],
                "active_file": None,
                "upload": None,
                "resolution": 6,
                "z_scale": 1.0,
                "rendering": "webgl",
                "view_2d": False,
                "parallel": False,
                "can_screenshot": supports_screenshot(),
                "slice_items": [],
                "slice_index": -1,
                "materials": [],
                "hidden_materials": [],
                "available_toggles": [],
                "visible_toggles": [],
                "banks": [],
                "max_step": 0,
                "bank_positions": {},
                "hidden_banks": [],
                "color_by_bank": False,
                "states": [],
                "state_index": None,
                "state_params": [],
                "stats": dict(EMPTY_STATS),
            }
        )

    @change("resolution")
    def _on_resolution(self, resolution, **_):
        self.scene.set_resolution(resolution)
        self.push()

    @change("slice_index")
    def _on_slice(self, slice_index, **_):
        if self.model is None:
            return
        slices = self.model.slices
        self.scene.set_z_range(slices[slice_index] if 0 <= slice_index < len(slices) else None)
        self.push()

    @change(
        "z_scale", "hidden_materials", "visible_toggles", "bank_positions", "hidden_banks", "color_by_bank"
    )
    def _on_appearance(
        self, z_scale, hidden_materials, visible_toggles, bank_positions, hidden_banks, color_by_bank, **_
    ):
        if self.model is None:
            return
        self.scene.update_appearance(
            z_scale=float(z_scale),
            hidden_materials=set(hidden_materials),
            toggles={name: name in visible_toggles for name in TOGGLES},
            bank_positions={k: float(v) for k, v in bank_positions.items()},
            hidden_banks=set(hidden_banks),
            color_by_bank=bool(color_by_bank),
        )
        self.push()

    @change("view_2d")
    def _on_view_2d(self, view_2d, **_):
        self.scene.set_2d_mode(view_2d)
        self.push(camera=True)

    @change("state_index")
    def _on_state(self, **_):
        self.reset_rods()
        self._update_state_params()

    def _update_state_params(self) -> None:
        index = self.state.state_index
        if self.model is None or index is None or index < 0 or not self.model.states:
            self.state.state_params = []
            return
        params = self.model.states[index].params
        self.state.state_params = [
            {"key": k, "value": _format_value(v)} for k, v in params.items() if k not in ("title",)
        ]

    @controller.set("clear")
    def clear(self):
        """Drop the current data and go back to the file selection."""
        self.model = None
        self.scene.clear()
        with self.state:
            self.state.loaded = False
            self.state.title = "VeraInViewer"
            self.state.file_name = ""
            self.state.info = []
            self.state.active_file = None
            self.state.upload = None
        self.push()

    @change("rendering")
    def _on_rendering(self, **_):
        # The view remounts with a new runtime and fetches the full scene,
        # so any update in flight on the previous one will never be acknowledged
        self._sync_time = None
        self._sync_dirty = False

    @change("parallel")
    def _on_parallel(self, parallel, **_):
        self.scene.set_parallel_projection(parallel)
        self.push(camera=True)

    @controller.set("orient")
    def orient(self, view: str):
        self.scene.orient(view)
        self.push(camera=True)

    @controller.set("download_screenshot")
    def download_screenshot(self):
        """Save the 3D view as a PNG in the browser (read back from VTK.wasm)."""
        stem = (self.state.file_name or "vera").split(".")[0]
        self.ctx.view.download_screenshot(f"{stem}.png")

    @controller.set("reset_camera")
    def reset_camera(self):
        self.scene.reset_camera()
        self.push(camera=True)

    @controller.set("reset_rods")
    def reset_rods(self):
        """Move rods where the simulation sees them when visiting states in order."""
        index = self.state.state_index
        if self.model is None or index is None or not self.model.states:
            return
        if index == ALL_RODS_IN:
            self.set_all_rods(False)
            return
        self.state.bank_positions = dict(self.model.states[index].bank_positions)

    @controller.set("set_all_rods")
    def set_all_rods(self, withdrawn: bool):
        if self.model is None:
            return
        position = (self.model.maxstep or 0) if withdrawn else 0
        self.state.bank_positions = {b: position for b in self.model.banks}

    def push(self, camera: bool = False) -> None:
        """Send scene changes to the client, one update at a time.

        The client runs overlapping updates concurrently and a small change can
        get lost behind a large one, so changes made while an update is in
        flight are coalesced and sent once the client reports ``updated``.
        """
        self.state.stats = self.scene.stats()
        self._sync_camera = self._sync_camera or camera
        if self._sync_time is not None and time.monotonic() - self._sync_time < SYNC_TIMEOUT:
            self._sync_dirty = True
            return
        self._sync_time = time.monotonic()
        self._sync_dirty = False
        push_camera, self._sync_camera = self._sync_camera, False
        self.ctx.view.update(push_camera=push_camera)

    def on_view_updated(self):
        self._sync_time = None
        if self._sync_dirty:
            self.push()

    def on_end_interaction(self, camera_states):
        """Keep the server camera in sync with the client one."""
        for camera_state in camera_states or []:
            self.ctx.view.vtk_update_from_state(camera_state)

    # -------------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.state.trame__title = "VeraInViewer"
        with VAppLayout(self.server, full_height=True) as self.ui:
            with v4.VAppBar(v_if="loaded", density="compact", border="b", elevation=0) as toolbar:
                self._build_toolbar(toolbar)

            self._build_footer()

            with v4.VMain():
                with v4.VContainer(
                    v_if="loaded", fluid=True, classes="fill-height pa-0", style="position: relative;"
                ):
                    vtklocal.LocalView(
                        self.scene.render_window,
                        classes="vera-view",
                        ctx_name="view",
                        # config is only read on mount: remount the view to switch backend
                        key=("rendering",),
                        config=("{ rendering }",),
                        progress_enabled=True,
                        end_interaction=(self.on_end_interaction, "[$event]"),
                        updated=self.on_view_updated,
                    )
                    self._build_camera_bar()
                    self._build_side_panels()

                self._build_landing()

                v4.VAlert(
                    v_if="error",
                    text=("error",),
                    type="error",
                    closable=True,
                    click_close="error = null",
                    style="position: absolute; bottom: 1rem; left: 1rem; right: 1rem; z-index: 10;",
                )

    def _build_landing(self) -> None:
        """Full screen drop zone over a rendering of a reactor core."""
        with v4.VThemeProvider(theme="light", with_background=False):
            with html.Div(v_if="!loaded", classes="vera-landing"):
                v4.VFileUpload(
                    v_model=("upload",),
                    title="Drop a VERAin XML file here",
                    filter_by_type=".xml,.gold",
                    accept=".xml,.gold",
                    icon="mdi-file-upload-outline",
                    classes="vera-dropzone",
                    hide_details=True,
                    __properties=["accept"],
                )
                with html.Div(classes="vera-files"):
                    with html.Div(v_if="samples.length", classes="vera-gallery"):
                        with v4.VCard(
                            v_for="s in samples",
                            key="s.key",
                            classes="vera-sample",
                            rounded="lg",
                            hover=True,
                            click="active_file = s.key",
                        ):
                            v4.VImg(
                                v_if="s.preview",
                                src=("s.preview",),
                                aspect_ratio=1.5,
                                cover=True,
                            )
                            with v4.VCardText(classes="pa-2"):
                                html.Div(
                                    "{{ s.title }}",
                                    classes="text-label-large vera-sample-title",
                                    title=("s.title",),
                                )
                                html.Div(
                                    "{{ s.info.join(' · ') }}",
                                    classes="text-label-small text-medium-emphasis",
                                )
                    with v4.VCard(v_if="file_items.length", rounded="lg", classes="vera-data mt-3"):
                        with v4.VList(density="compact", bg_color="transparent"):
                            v4.VListSubheader("Available files")
                            v4.VListItem(
                                v_for="f in file_items",
                                key="f",
                                title=("f",),
                                prepend_icon="mdi-file-document-outline",
                                click="active_file = f",
                            )
                v4.VProgressLinear(
                    active=("trame__busy",),
                    indeterminate=True,
                    absolute=True,
                    location="top",
                    color="primary",
                )

    def _build_toolbar(self, toolbar) -> None:
        with toolbar:
            v4.VIcon("mdi-radioactive", classes="ml-4", title="VeraInViewer")
            html.Div(
                "{{ title }}",
                classes="text-title-medium text-truncate mx-2",
                style="max-width: 50vw;",
            )
            v4.VBtn(
                icon="mdi-close",
                density="comfortable",
                size="small",
                variant="text",
                click=self.ctrl.clear,
                title="Close data and load another file",
            )
            v4.VSpacer()
            self._build_view_controls()
            v4.VProgressLinear(
                active=("trame__busy",),
                indeterminate=True,
                absolute=True,
                location="bottom",
                color="primary",
            )

    def _build_view_controls(self) -> None:
        with v4.VChipGroup(
            v_model=("visible_toggles",),
            multiple=True,
            filter=True,
            color="primary",
            classes="mx-2",
        ):
            v4.VChip(
                "{{ name }}", v_for="name in available_toggles", key="name", value=("name",), size="small"
            )

        v4.VSpacer()

        with html.Div(classes="d-flex align-center mx-2"):
            v4.VBtn(
                icon="mdi-chevron-left",
                size="small",
                disabled=("slice_index <= -1",),
                click="slice_index -= 1",
                title="Lower elevation",
                classes="rounded",
            )
            v4.VSelect(
                v_model=("slice_index",),
                items=("slice_items",),
                variant="outlined",
                density="compact",
                hide_details=True,
                classes="mx-2",
                style="width: 14rem;",
            )
            v4.VBtn(
                icon="mdi-chevron-right",
                size="small",
                disabled=("slice_index >= slice_items.length - 2",),
                click="slice_index += 1",
                title="Higher elevation",
                classes="rounded",
            )

    def _build_footer(self) -> None:
        with v4.VFooter(v_if="loaded", app=True, height=32, border="t", classes="py-0 px-3 text-label-small"):
            html.Div(
                "{{ [file_name !== title && file_name, ...info].filter(Boolean).join(' · ') }}",
                classes="text-medium-emphasis text-truncate",
            )
            v4.VSpacer()
            html.Div(
                "{{ stats.visible_actors }} / {{ stats.actors }} actors"
                " · {{ stats.triangles.toLocaleString() }} triangles",
                classes="text-medium-emphasis text-no-wrap ml-4",
                title="Visible / total actors and triangles rendered (glyphs count once per instance)",
            )
            v4.VDivider(vertical=True, classes="mx-4 my-2")
            v4.VSlider(
                v_model=("z_scale",),
                min=0.01,
                max=1,
                step=0.01,
                density="compact",
                hide_details=True,
                prepend_icon="mdi-arrow-expand-vertical",
                title="Vertical scale",
                style="max-width: 200px;",
                classes="ml-2",
            )
            v4.VDivider(vertical=True, classes="mx-4 my-2")
            with v4.VBtnToggle(
                v_model=("resolution",),
                mandatory=True,
                density="compact",
                variant="outlined",
                divided=True,
                rounded="lg",
                style="height: 24px;",
            ):
                for value, icon in (
                    (3, "mdi-triangle-outline"),
                    (6, "mdi-hexagon-outline"),
                    (24, "mdi-circle-outline"),
                ):
                    v4.VBtn(icon=icon, value=(value,), size="small", title=f"Resolution {value}")
            v4.VDivider(vertical=True, classes="mx-4 my-2")
            with v4.VBtnToggle(
                v_model=("rendering",),
                mandatory=True,
                density="compact",
                variant="outlined",
                divided=True,
                rounded="lg",
                style="height: 24px;",
            ):
                for value, label in (("webgl", "WebGL"), ("webgpu", "WebGPU")):
                    v4.VBtn(label, value=value, size="small", title=f"Render with {label}")

    def _build_camera_bar(self) -> None:
        """Vertical floating toolbar over the 3D view for camera controls."""
        with v4.VCard(
            classes="d-flex flex-column pa-1 ga-1",
            rounded="lg",
            elevation=3,
            style="position: absolute; top: 0.75rem; right: 0.75rem; z-index: 2;",
        ):
            v4.VBtn(
                icon=("camera_bar ? 'mdi-chevron-up' : 'mdi-chevron-down'",),
                size="small",
                classes="rounded",
                variant="text",
                click="camera_bar = !camera_bar",
                title=("camera_bar ? 'Collapse camera controls' : 'Expand camera controls'",),
            )
            with v4.VExpandTransition():
                with html.Div(v_show=("camera_bar", True), classes="d-flex flex-column ga-1"):
                    v4.VDivider()
                    v4.VBtn(
                        icon="mdi-crop-free",
                        size="small",
                        classes="rounded",
                        variant="text",
                        click=self.ctrl.reset_camera,
                        title="Reset camera",
                    )
                    v4.VBtn(
                        icon=("view_2d ? 'mdi-pan' : 'mdi-rotate-orbit'",),
                        size="small",
                        classes="rounded",
                        variant="text",
                        active=("view_2d",),
                        click="view_2d = !view_2d",
                        title=(
                            "view_2d ? '2D: pan and zoom (click for 3D rotation)'"
                            " : '3D: rotate (click for 2D top view, pan and zoom)'",
                        ),
                    )
                    v4.VBtn(
                        icon=("parallel || view_2d ? 'mdi-cylinder' : 'mdi-cone'",),
                        size="small",
                        classes="rounded",
                        variant="text",
                        active=("parallel || view_2d",),
                        disabled=("view_2d",),
                        click="parallel = !parallel",
                        title=(
                            "parallel ? 'Parallel projection (click for perspective)'"
                            " : 'Perspective projection (click for parallel)'",
                        ),
                    )
                    v4.VDivider()
                    for view, icon, label in (
                        ("top", "mdi-axis-z-arrow", "Top view"),
                        ("side", "mdi-axis-x-arrow", "Side view"),
                        ("iso", "mdi-axis-arrow", "Isometric view"),
                    ):
                        v4.VBtn(
                            icon=icon,
                            size="small",
                            classes="rounded",
                            variant="text",
                            disabled=("view_2d",) if view != "top" else False,
                            click=(self.ctrl.orient, f"['{view}']"),
                            title=label,
                        )
                    v4.VDivider(v_if=("can_screenshot",))
                    v4.VBtn(
                        v_if=("can_screenshot",),
                        icon="mdi-camera",
                        size="small",
                        classes="rounded",
                        variant="text",
                        click=self.ctrl.download_screenshot,
                        title="Download a screenshot (PNG)",
                    )

    def _build_side_panels(self) -> None:
        """Floating collapsible panels stacked over the left side of the 3D view."""
        with html.Div(classes="vera-side-panels"):
            self._build_materials_panel()
            self._build_rods_panel()

    @staticmethod
    @contextmanager
    def _overlay_panel(title: str, expanded: str, **kwargs):
        """Collapsible floating card, the body of the `with` block fills its scrollable content."""
        with v4.VCard(rounded="lg", elevation=3, classes="vera-panel", **kwargs):
            with v4.VCardTitle(
                classes="d-flex align-center text-title-small py-1 pl-3 pr-1",
                style="cursor: pointer;",
                click=f"{expanded} = !{expanded}",
            ):
                html.Span(title)
                v4.VSpacer()
                v4.VBtn(
                    icon=(f"{expanded} ? 'mdi-chevron-up' : 'mdi-chevron-down'",),
                    size="small",
                    variant="text",
                    density="comfortable",
                    title=(f"{expanded} ? 'Collapse {title.lower()}' : 'Expand {title.lower()}'",),
                )
            with html.Div(v_show=(expanded, True), classes="vera-panel-body"):
                v4.VDivider()
                yield

    def _build_materials_panel(self) -> None:
        toggle = (
            "hidden_materials = hidden_materials.includes(m.name)"
            " ? hidden_materials.filter((n) => n !== m.name)"
            " : [...hidden_materials, m.name]"
        )
        with self._overlay_panel("Materials", "materials_panel"):
            with v4.VList(density="compact", classes="py-0", bg_color="transparent"):
                with v4.VListItem(v_for="m in materials", key="m.name", click=toggle, classes="px-3"):
                    with v4.Template(v_slot_prepend=True):
                        v4.VCheckboxBtn(
                            model_value=("!hidden_materials.includes(m.name)",),
                            density="compact",
                            readonly=True,
                        )
                    v4.VListItemTitle("{{ m.name }}")
                    with v4.Template(v_slot_append=True):
                        html.Div(
                            style=(
                                "{ background: m.color, width: '24px', height: '16px',"
                                " borderRadius: '3px', border: '1px solid #888' }",
                            ),
                        )

    def _build_rods_panel(self) -> None:
        toggle_bank = (
            "hidden_banks = hidden_banks.includes(b.name)"
            " ? hidden_banks.filter((n) => n !== b.name)"
            " : [...hidden_banks, b.name]"
        )
        with self._overlay_panel("Control rods", "rods_panel", v_if="banks.length"):
            with html.Div(classes="pa-3"):
                with html.Div(v_if="states.length", classes="d-flex align-center mb-2 ga-1"):
                    v4.VBtn(
                        icon="mdi-chevron-left",
                        size="small",
                        classes="rounded",
                        flat=True,
                        disabled=("state_index <= states[0].value",),
                        click="state_index -= 1",
                    )
                    v4.VSelect(
                        v_model=("state_index",),
                        items=("states",),
                        density="compact",
                        hide_details=True,
                        variant="outlined",
                    )
                    v4.VBtn(
                        icon="mdi-chevron-right",
                        size="small",
                        flat=True,
                        classes="rounded",
                        disabled=("state_index >= states[states.length - 1].value",),
                        click="state_index += 1",
                    )

                # Sliders go from inserted (0, left) to withdrawn (max step, right)
                with html.Div(classes="d-flex align-center mb-2"):
                    v4.VBtn(
                        "Insert all",
                        prepend_icon="mdi-chevron-double-left",
                        size="x-small",
                        variant="tonal",
                        title="Move every bank to step 0 (fully inserted)",
                        click=(self.ctrl.set_all_rods, "[false]"),
                    )
                    v4.VSpacer()
                    v4.VBtn(
                        "Use state positions",
                        size="x-small",
                        variant="tonal",
                        classes="rounded",
                        title="Apply the rod positions of the selected state",
                        click=self.ctrl.reset_rods,
                    )
                    v4.VSpacer()
                    v4.VBtn(
                        "Withdraw all",
                        append_icon="mdi-chevron-double-right",
                        size="x-small",
                        variant="tonal",
                        title="Move every bank to the maximum step (fully withdrawn)",
                        click=(self.ctrl.set_all_rods, "[true]"),
                    )

                    v4.VSpacer()
                    v4.VSwitch(
                        v_model=("color_by_bank",),
                        inset="square",
                        density="compact",
                        hide_details=True,
                        color="primary",
                        true_icon="mdi-palette-outline",
                        size="x-small",
                    )

                with html.Div(v_for="b in banks", key="b.name", classes="d-flex align-center"):
                    v4.VCheckboxBtn(
                        model_value=("!hidden_banks.includes(b.name)",),
                        click=toggle_bank,
                        density="compact",
                        color=("b.color",),
                    )
                    html.Div("{{ b.name }}", classes="text-body-medium", style="width: 2.5rem;")
                    v4.VSlider(
                        model_value=("bank_positions[b.name] || 0",),
                        update_modelValue="bank_positions = { ...bank_positions, [b.name]: $event }",
                        min=0,
                        max=("max_step",),
                        step=1,
                        density="compact",
                        hide_details=True,
                        color=("b.color",),
                        classes="mx-2",
                    )
                    html.Div(
                        "{{ bank_positions[b.name] || 0 }}",
                        classes="text-body-small text-right",
                        style="width: 2.5rem;",
                    )
                with v4.VTable(v_if="state_params.length", density="compact", classes="mt-3 text-body-small"):
                    with html.Tbody():
                        with html.Tr(v_for="p in state_params", key="p.key"):
                            html.Td("{{ p.key }}", classes="font-weight-medium")
                            html.Td("{{ p.value }}")


def main(server=None, **kwargs):
    app = VeraInViewer(server)
    app.server.start(**kwargs)


if __name__ == "__main__":
    main()
