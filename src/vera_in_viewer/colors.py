"""Categorical color assignment, ported from ``ColorManager.js``.

Colors are handed out lazily: each new name gets the least used palette color,
so the order in which names are requested matters. The material presets match
the ones from VeraInView's ``ImageGenerator.js``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RGBA = tuple[float, float, float, float]

# Desaturated categorical scheme (colorbrewer Set3), see ColorManager.js
PALETTE: list[RGBA] = [
    (217 / 255, 217 / 255, 217 / 255, 1.0),  # zirc, grey
    (255 / 255, 237 / 255, 111 / 255, 1.0),  # He, yellow
    (141 / 255, 211 / 255, 199 / 255, 1.0),  # blue-green, teal
    (190 / 255, 186 / 255, 218 / 255, 1.0),  # blue-purple, indigo
    (251 / 255, 128 / 255, 114 / 255, 1.0),  # red
    (253 / 255, 180 / 255, 98 / 255, 1.0),  # orange
    (179 / 255, 222 / 255, 105 / 255, 1.0),  # green
    (252 / 255, 205 / 255, 229 / 255, 1.0),  # pink
    (188 / 255, 128 / 255, 189 / 255, 1.0),  # purple
    (204 / 255, 235 / 255, 197 / 255, 1.0),  # light green
    (255 / 255, 255 / 255, 179 / 255, 1.0),  # light yellow
]

MATERIAL_PRESETS: dict[str, RGBA] = {
    "air": (0.0, 0.0, 0.0, 0.0),
    "cs": (121 / 255, 121 / 255, 121 / 255, 1.0),
    "ss": (153 / 255, 153 / 255, 153 / 255, 1.0),
    "mod": (128 / 255, 177 / 255, 211 / 255, 1.0),
    "water": (128 / 255, 177 / 255, 211 / 255, 1.0),
    "zirc2": (217 / 255, 217 / 255, 217 / 255, 1.0),
    "zirc4": (217 / 255, 217 / 255, 217 / 255, 1.0),
    "he": (255 / 255, 237 / 255, 111 / 255, 1.0),
}

# Control rod bank colors avoid moderator blue and cladding grey (StatePanel.js)
BANK_PRESETS: dict[str, RGBA] = {
    "mod": (128 / 255, 177 / 255, 211 / 255, 1.0),
    "zirc2": (217 / 255, 217 / 255, 217 / 255, 1.0),
    "NA": (255 / 255, 255 / 255, 179 / 255, 1.0),
}


def to_rgba_key(color: RGBA) -> str:
    """CSS ``rgba()`` string, also used as identity key for a color."""
    r, g, b = (int(c * 255) for c in color[:3])
    return f"rgba({r}, {g}, {b}, {color[3]:g})"


def to_hex(color: RGBA) -> str:
    """Hex string usable by HTML/vuetify widgets."""
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in color[:3]))


@dataclass
class _ColorInfo:
    color: RGBA
    names: list[str] = field(default_factory=list)


class ColorManager:
    """Assign stable colors and integer ids to names."""

    def __init__(self, palette: list[RGBA] = PALETTE, presets: dict[str, RGBA] | None = None):
        # JS inserts the palette in reverse order which drives the
        # "least used" selection order, keep the same behavior.
        self._colors: dict[str, _ColorInfo] = {
            to_rgba_key(color): _ColorInfo(color) for color in reversed(palette)
        }
        self._names: dict[str, tuple[str, int]] = {}
        self._next_id = 1
        for name, color in (presets or {}).items():
            self.set_color(name, color)

    def _map_if_needed(self, name: str, color: RGBA | None = None) -> None:
        if name in self._names:
            return
        if color is None:
            key = None
            min_seen = 1000
            for info_key, info in self._colors.items():
                if info.color[3] > 0 and (key is None or len(info.names) < min_seen):
                    key = info_key
                    min_seen = len(info.names)
        else:
            key = to_rgba_key(color)
            if key not in self._colors:
                self._colors[key] = _ColorInfo(color)
        self._colors[key].names.append(name)
        self._names[name] = (key, self._next_id)
        self._next_id += 1

    def set_color(self, name: str, color: RGBA) -> None:
        self._map_if_needed(name, color)

    def get_color(self, name: str) -> RGBA:
        self._map_if_needed(name)
        return self._colors[self._names[name][0]].color

    def get_id(self, name: str) -> int:
        self._map_if_needed(name)
        return self._names[name][1]

    def has_name(self, name: str) -> bool:
        return name in self._names


def material_color_manager() -> ColorManager:
    return ColorManager(presets=MATERIAL_PRESETS)


def bank_color_manager() -> ColorManager:
    return ColorManager(presets=BANK_PRESETS)
