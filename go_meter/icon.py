"""Tray icon image, rendered to Apple's Menu Bar Extra guidelines.

Source geometry is the official OpenCode logomark (assets/opencode-logo.svg,
fetched from https://opencode.ai/favicon.svg). In the logo's 512x512 space:

  * white frame  (128,96)->(384,416) with an inner hole (192,160)->(320,352)
  * gray inset    (192,224)->(320,352), fill #5A5858 (the lower two-thirds of
    the hole)

Per the HIG, a menu-bar glyph must be a monochrome **template image**: a single
ink color plus an alpha channel, no baked-in colors. macOS then recolors it
(black / white / translucent gray) to match the light/dark bar and the
wallpaper behind it. A template is one *color*, but it keeps its alpha channel,
so we reproduce the logo's two tones as two opacities of the same ink:

    full ink   = frame               = the outer rect minus the hole
    dim ink     = gray inset          = the hole's lower two-thirds, at partial
                                       alpha (~0x5A/0xFF, the brand gray:white
                                       ratio) so it reads as a translucent panel
    clear       = outside the rect  +  the top slot (192,160)->(320,224)

macOS recolors the ink and honors the alpha, so the frame paints solid and the
inset paints as a dimmer translucent panel — the original's depth, monochrome.

macOS: `apply_macos_template()` flags the underlying NSImage as a template so
the system does the recoloring. pystray builds the NSImage from PNG bytes and
never sets that flag itself, which is why the flag must be poked in afterwards.

Windows / Linux tray backends have no template concept, so there we fall back
to picking a black or white ink up front from a best-effort OS theme check.
"""

import logging
import subprocess
import sys

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_SVG_SIZE = 512
_OUTER = (128, 96, 384, 416)   # frame outer edge
_HOLE = (192, 160, 320, 352)   # interior cut out of the frame
_INSET = (192, 224, 320, 352)  # gray panel filling the hole's lower two-thirds
# Inset opacity relative to the frame, matching the brand gray:white ratio
# (#5A5858 vs #FFFFFF ~= 0.35) so the panel reads as a dimmer translucent fill.
_INSET_ALPHA = round(0x5A / 0xFF * 255)  # ~101

# HIG: the glyph must not fill the ~22pt bar edge to edge. Scale the silhouette
# so its taller dimension spans this fraction of the square canvas, leaving the
# rest as a transparent interior margin (~16pt inside a 22pt bar).
_GLYPH_FRACTION = 0.72
# Windows/Linux notification-area icons have no HIG interior-margin rule and the
# system already pads them, so the 0.72 mac margin makes the glyph look tiny.
# Fill more of the canvas there (kept under 1.0 to avoid edge clipping at 16px).
_GLYPH_FRACTION_TRAY = 0.92

# Render larger than the ~22px bar slot; pystray downsamples to the bar
# thickness with LANCZOS, so this just buys retina sharpness.
_RENDER_SIZE = 128

_TEMPLATE_INK = (0, 0, 0, 255)  # canonical template color; recolored by macOS


def get_icon():
    """Render the OpenCode logomark for the system tray.

    macOS: black ink on transparency, to be flagged as a template image (see
    ``apply_macos_template``). Windows/Linux: ink color chosen from the OS
    theme so the monochrome glyph stays visible either way.
    """
    if sys.platform == "darwin":
        return _render_logo(_TEMPLATE_INK)
    ink = (0, 0, 0, 255) if _is_light_theme() else (255, 255, 255, 255)
    return _render_logo(ink, fraction=_GLYPH_FRACTION_TRAY)


def apply_macos_template(tray_icon) -> None:
    """Flag pystray's status-item image as a template so macOS auto-recolors it.

    No-op off macOS or if the button image isn't up yet. Call after the icon is
    visible (``visible=True`` triggers pystray to build the NSImage). Reaches
    into pystray internals because pystray has no public template hook.
    """
    if sys.platform != "darwin":
        return
    try:
        button = tray_icon._status_item.button()
        image = button.image()
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
    except Exception:  # noqa: BLE001 — best-effort; theme fallback still applies
        logger.debug("Could not flag tray image as template", exc_info=True)


def _is_light_theme() -> bool:
    """Best-effort OS theme detection. True for light mode."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            # `AppleInterfaceStyle` is only set (to "Dark") in dark mode.
            return r.stdout.strip() != "Dark"
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"theme detection failed: {e}")
            return False
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                0,
                winreg.KEY_READ,
            ) as key:
                # The tray icon sits on the taskbar, whose color follows
                # SystemUsesLightTheme (the "Windows mode"). AppsUseLightTheme
                # is the independent app-window mode and would pick the wrong
                # ink under the default "apps light, taskbar dark" combo.
                value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
                return value == 1
        except OSError as e:
            logger.debug(f"theme detection failed: {e}")
            return False
    return False  # Linux and others: assume dark


def _render_logo(ink, fraction=_GLYPH_FRACTION):
    size = _RENDER_SIZE
    scale = size / _SVG_SIZE

    # Alpha channel at native logo scale, carrying both tones:
    #   frame (outer minus hole) at full opacity, inset at partial opacity,
    #   the top slot (hole minus inset) left transparent.
    alpha = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(alpha)
    d.rectangle([c * scale for c in _OUTER], fill=255)
    d.rectangle([c * scale for c in _HOLE], fill=0)
    d.rectangle([c * scale for c in _INSET], fill=_INSET_ALPHA)

    glyph = Image.new("RGBA", (size, size), tuple(ink[:3]) + (0,))
    glyph.putalpha(alpha)

    # HIG interior padding + optical centering: fit the silhouette's bounding
    # box to _GLYPH_FRACTION of the canvas (by its larger side), centered.
    # Measure from alpha only — the ink RGB is a constant (e.g. white) that
    # would otherwise make getbbox() report the whole canvas.
    bbox = alpha.getbbox()
    if not bbox:
        return glyph
    glyph = glyph.crop(bbox)
    target = fraction * size
    factor = target / max(glyph.width, glyph.height)
    new_w = max(1, round(glyph.width * factor))
    new_h = max(1, round(glyph.height * factor))
    glyph = glyph.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(glyph, ((size - new_w) // 2, (size - new_h) // 2), glyph)
    return canvas
