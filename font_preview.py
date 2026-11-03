#!/usr/bin/env python3
"""
FontPreview 2 — a fast, big-preview font browser for client work.
Windows 11 · Python 3.10+ · PySide6 (+ fontTools for "My fonts" & grouping)

What's new in v2
- Sizing modes: Fit to card / Cap height / x-height / True size
- List view: rows per screen  ·  Grid view: columns
- "My fonts" filter (only fonts you installed yourself)
- Hide fonts you never use
- Automatic missing-character warning (French accents + your sample text)
- Weights grouped per family, with a style picker on every card
- Multi-line sample text
- Export: text always fits, stack or grid layout, live preview
- Copy any preview as an image
- Keyboard navigation
- Redesigned interface
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QByteArray, QEvent,
                            QModelIndex, QPointF, QRectF, QSize, Qt, QTimer)
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF,
                           QGuiApplication, QIcon, QImage, QKeySequence,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap,
                           QPolygonF,
                           QRawFont, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup,
                               QCheckBox, QColorDialog, QComboBox, QDialog,
                               QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListView, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSlider, QStyle,
                               QStyledItemDelegate, QToolButton, QToolTip,
                               QVBoxLayout, QWidget)

try:
    from fontTools.ttLib import TTCollection, TTFont
    HAVE_FONTTOOLS = True
except Exception:  # pragma: no cover - optional dependency
    HAVE_FONTTOOLS = False

APP_NAME = "FontPreview"
VERSION = "2.0"
SETTINGS_PATH = Path.home() / ".fontpreview_settings.json"
CACHE_PATH = Path.home() / ".fontpreview_cache.json"

REF = 100.0          # reference pixel size for all font measurements
GAP = 12             # space between cards
HEADER_H = 36        # card header (font name, style, badges)

ACCENT_TEST = "éàèçùœÉÀÈÇÙŒ«»"

PRESETS = [
    ("Pangramme FR", "Portez ce vieux whisky au juge blond qui fume"),
    ("Pangram EN", "The quick brown fox jumps over the lazy dog"),
    ("Accents FR", "é à è ç ù œ  É À È Ç Ù Œ  « Voilà ! »"),
    ("Alphabet", "AaBbCcDdEeFfGgHhIiJjKkLlMm\nNnOoPpQqRrSsTtUuVvWwXxYyZz"),
    ("Chiffres & ponctuation", "0123456789  $ € % & @ # ? ! ( ) “ ”"),
    ("Titre + sous-titre", "Titre principal\nUn sous-titre élégant"),
]

SIZE_MODES = [
    ("fit", "Fit to card"),
    ("cap", "Cap height"),
    ("x", "x-height"),
    ("true", "True size"),
]

STYLES = [
    ("regular", "Regular", 400, False),
    ("light", "Light", 300, False),
    ("medium", "Medium", 500, False),
    ("bold", "Bold", 700, False),
    ("black", "Black", 900, False),
    ("italic", "Italic", 400, True),
    ("bolditalic", "Bold Italic", 700, True),
]
STYLE_BY_KEY = {s[0]: s for s in STYLES}

THEMES = {
    "dark": dict(
        base="#131418", surface="#1a1b20", raised="#23252b", border="#2c2e35",
        border_hover="#3c3f48", text="#ececef", muted="#8d909a",
        accent="#8b9cff", on_accent="#0e1022", seg_on="#33363f",
        scroll="#3a3d46", star="#f2c14e", warn="#f5a524"),
    "light": dict(
        base="#eff0f3", surface="#fbfbfc", raised="#ffffff", border="#dfe0e6",
        border_hover="#c5c7cf", text="#1b1c21", muted="#6a6d77",
        accent="#4f63e6", on_accent="#ffffff", seg_on="#e6e8f0",
        scroll="#c9cbd3", star="#e0a91f", warn="#d98a00"),
}

DEFAULTS = dict(
    sample=PRESETS[0][1],
    view="list",            # list | grid | compare
    list_rows=5,
    grid_cols=3,
    align="left",           # left | center
    size_mode="fit",
    fit_fill=90,            # % of the card used in Fit mode
    px_size=72,             # px for cap / x / true modes
    tracking=0,             # 1/1000 em, like Photoshop
    leading=120,            # line spacing, % of the font's visual size
    style="regular",
    fg="#16171a",
    bg="#ffffff",
    theme="dark",
    favorites=[],
    hidden=[],
    overrides={},           # family group -> chosen style label
    swatches=[],
    library="all",          # all | mine
    show_hidden=False,
    sidebar=True,
    geometry="",
    export=dict(layout="stack", cols=2, names=True, watermark_on=False,
                watermark="", scale=2),
)


# =========================================================== settings
def load_settings():
    s = copy.deepcopy(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return s
    # ---- migrate v1 settings
    if "dark_theme" in data and "theme" not in data:
        s["theme"] = "dark" if data["dark_theme"] else "light"
    if "size" in data and "px_size" not in data:
        s["px_size"] = int(data["size"])
    if "style" not in data and ("bold" in data or "italic" in data):
        b, i = data.get("bold"), data.get("italic")
        s["style"] = ("bolditalic" if b and i else "bold" if b
                      else "italic" if i else "regular")
    if isinstance(data.get("watermark"), str):
        s["export"]["watermark"] = data["watermark"]
    # ---- copy known keys
    for k in DEFAULTS:
        if k in data and k != "export" and type(data[k]) is type(DEFAULTS[k]):
            s[k] = data[k]
    s["list_rows"] = max(1, min(10, s["list_rows"]))
    if isinstance(data.get("export"), dict):
        s["export"].update({k: v for k, v in data["export"].items()
                            if k in DEFAULTS["export"]})
    return s


def save_settings(s):
    try:
        SETTINGS_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception:
        pass


# =========================================================== font library
FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
TRUSTED_INSTALLER_SID = ("S-1-5-80-956008885-3418522649-1831038044-"
                         "1853292631-2271478464")


def _win_owner_sid(path):
    """SID string of a file's owner (Windows only). System fonts are owned
    by TrustedInstaller; fonts you installed are not."""
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    # SE_FILE_OBJECT = 1, OWNER_SECURITY_INFORMATION = 1
    if advapi32.GetNamedSecurityInfoW(path, 1, 1, ctypes.byref(owner), None,
                                      None, None, ctypes.byref(sd)) != 0:
        return None
    try:
        s = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(owner, ctypes.byref(s)):
            return None
        value = s.value
        kernel32.LocalFree(ctypes.cast(s, ctypes.c_void_p))
        return value
    finally:
        kernel32.LocalFree(sd)


def _win_registry_fonts(hive):
    import winreg
    out = []
    try:
        with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT"
                                  r"\CurrentVersion\Fonts") as key:
            i = 0
            while True:
                try:
                    _, val, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if isinstance(val, str):
                    out.append(val)
    except OSError:
        pass
    return out


def find_font_files():
    """Return [(path, user_installed: bool | None)].
    None means 'decide later' (needs an owner check)."""
    files = {}
    if sys.platform == "win32":
        import winreg
        windir = os.environ.get("WINDIR", r"C:\Windows")
        sys_dir = os.path.join(windir, "Fonts")
        for v in _win_registry_fonts(winreg.HKEY_CURRENT_USER):
            p = os.path.expandvars(v)
            if not os.path.isabs(p):
                p = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                 "Microsoft", "Windows", "Fonts", p)
            files[os.path.normcase(p)] = (p, True)
        user_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                "Microsoft", "Windows", "Fonts")
        if os.path.isdir(user_dir):
            for n in os.listdir(user_dir):
                p = os.path.join(user_dir, n)
                files.setdefault(os.path.normcase(p), (p, True))
        for v in _win_registry_fonts(winreg.HKEY_LOCAL_MACHINE):
            p = os.path.expandvars(v)
            if not os.path.isabs(p):
                p = os.path.join(sys_dir, p)
            files.setdefault(os.path.normcase(p), (p, None))
    else:  # Linux / macOS: handy for testing
        mine = [Path.home() / ".local/share/fonts", Path.home() / ".fonts",
                Path.home() / "Library/Fonts"]
        system = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                  Path("/Library/Fonts"), Path("/System/Library/Fonts")]
        for roots, is_mine in ((system, False), (mine, True)):
            for root in roots:
                if root.is_dir():
                    for p in root.rglob("*"):
                        files[str(p)] = (str(p), is_mine)
    return [v for v in files.values()
            if v[0].lower().endswith(FONT_EXTS) and os.path.isfile(v[0])]


def _read_faces(path):
    """[(legacy family, typographic family), ...] for each face in a file."""
    faces = []
    if path.lower().endswith((".ttc", ".otc")):
        coll = TTCollection(path, lazy=True)
        fonts = list(coll.fonts)
    else:
        fonts = [TTFont(path, lazy=True, fontNumber=-1)]
    for f in fonts:
        try:
            name = f["name"]
            fam1 = name.getDebugName(1) or ""
            fam16 = name.getDebugName(16) or fam1
            if fam1:
                faces.append([fam1, fam16])
        finally:
            try:
                f.close()
            except Exception:
                pass
    return faces


class Library:
    """Which families are user-installed + how to group weights."""

    def __init__(self):
        self.typo_map = {}       # legacy family -> typographic family
        self.mine = set()        # family names (both kinds) you installed
        self.scanned = False
        self.error = ""

    def scan(self, progress=None):
        if not HAVE_FONTTOOLS:
            self.error = "fontTools is not installed"
            return
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
        new_cache = {}
        files = find_font_files()
        for i, (path, is_mine) in enumerate(files):
            if progress and i % 25 == 0:
                progress(i, len(files))
            try:
                st = os.stat(path)
            except OSError:
                continue
            stamp = [int(st.st_mtime), st.st_size]
            entry = cache.get(path)
            if not entry or entry.get("stamp") != stamp:
                entry = {"stamp": stamp, "faces": [], "owner_sys": None}
                try:
                    entry["faces"] = _read_faces(path)
                except Exception:
                    entry["faces"] = []
            if is_mine is None:  # Windows all-users font: check the owner
                if entry.get("owner_sys") is None:
                    try:
                        entry["owner_sys"] = (_win_owner_sid(path)
                                              == TRUSTED_INSTALLER_SID)
                    except Exception:
                        entry["owner_sys"] = True
                is_mine = not entry["owner_sys"]
            new_cache[path] = entry
            for fam1, fam16 in entry["faces"]:
                if fam16 and fam16 != fam1:
                    self.typo_map.setdefault(fam1, fam16)
                if is_mine:
                    self.mine.update((fam1, fam16))
        try:
            CACHE_PATH.write_text(json.dumps(new_cache), encoding="utf-8")
        except Exception:
            pass
        self.scanned = True


# =========================================================== font entries
WEIGHT_WORDS = {
    "thin", "hairline", "extralight", "ultralight", "light", "semilight",
    "book", "regular", "normal", "medium", "semibold", "demibold", "demi",
    "semi", "bold", "extrabold", "ultrabold", "extra", "ultra", "heavy",
    "black", "italic", "oblique", "it",
}
REGULAR_NAMES = {"regular", "normal", "roman", "book", "plain", ""}


@dataclass
class Variant:
    family: str
    style: str
    label: str
    weight: int
    italic: bool


@dataclass
class Entry:
    name: str
    variants: list = field(default_factory=list)
    mine: bool = False
    default: int = 0


def _heuristic_base(fam):
    tokens = fam.split()
    while len(tokens) > 1 and tokens[-1].lower() in WEIGHT_WORDS:
        tokens.pop()
    return " ".join(tokens)


def build_entries(library: Library):
    fams = [f for f in QFontDatabase.families()
            if not f.startswith("@") and not QFontDatabase.isPrivateFamily(f)
            and QFontDatabase.isScalable(f)]
    famset = set(fams)
    base_count = {}
    for f in fams:
        b = _heuristic_base(f)
        base_count[b] = base_count.get(b, 0) + 1

    groups = {}
    for fam in fams:
        g = library.typo_map.get(fam)
        if not g:
            b = _heuristic_base(fam)
            g = b if b != fam and (b in famset or base_count[b] >= 2) else fam
        groups.setdefault(g, []).append(fam)

    entries = []
    for g, members in groups.items():
        variants, seen = [], set()
        for fam in members:
            suffix = fam[len(g):].strip() if fam.startswith(g) else ""
            for style in QFontDatabase.styles(fam) or ["Regular"]:
                st = style if style.lower() not in REGULAR_NAMES else ""
                label = " ".join(x for x in (suffix, st) if x) or "Regular"
                if label in seen:
                    continue
                seen.add(label)
                variants.append(Variant(
                    fam, style, label,
                    int(QFontDatabase.weight(fam, style)),
                    bool(QFontDatabase.italic(fam, style))
                    or "italic" in label.lower() or "oblique" in label.lower()))
        if not variants:
            continue
        variants.sort(key=lambda v: (v.italic, v.weight, len(v.label)))
        e = Entry(g, variants,
                  mine=(g in library.mine or any(m in library.mine
                                                 for m in members)))
        e.default = nearest_variant(e, 400, False)
        entries.append(e)
    entries.sort(key=lambda e: e.name.lower())
    return entries


def nearest_variant(entry, weight, italic):
    best, best_score = 0, 1e9
    for i, v in enumerate(entry.variants):
        score = abs(v.weight - weight) + (0 if v.italic == italic else 1000)
        # prefer plain names over e.g. "Condensed Bold" at equal weight
        score += len(v.label) * 0.01
        if score < best_score:
            best, best_score = i, score
    return best


# =========================================================== drawing helpers
def star_path(r: QRectF):
    cx, cy, R = r.center().x(), r.center().y() + 0.5, r.width() / 2
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else R * 0.45
        pts.append(QPointF(cx + rad * math.cos(a), cy + rad * math.sin(a)))
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    return path


def make_icon(kind, color, size=18):
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    s = size
    if kind == "search":
        p.drawEllipse(QRectF(s * .2, s * .2, s * .48, s * .48))
        p.drawLine(QPointF(s * .6, s * .6), QPointF(s * .82, s * .82))
    elif kind == "sidebar":
        p.drawRoundedRect(QRectF(s * .15, s * .2, s * .7, s * .6), 2, 2)
        p.drawLine(QPointF(s * .4, s * .2), QPointF(s * .4, s * .8))
    elif kind == "swap":
        p.drawLine(QPointF(s * .35, s * .2), QPointF(s * .35, s * .8))
        p.drawLine(QPointF(s * .2, s * .35), QPointF(s * .35, s * .2))
        p.drawLine(QPointF(s * .5, s * .35), QPointF(s * .35, s * .2))
        p.drawLine(QPointF(s * .65, s * .2), QPointF(s * .65, s * .8))
        p.drawLine(QPointF(s * .5, s * .65), QPointF(s * .65, s * .8))
        p.drawLine(QPointF(s * .8, s * .65), QPointF(s * .65, s * .8))
    elif kind in ("minus", "plus"):
        p.drawLine(QPointF(s * .28, s * .5), QPointF(s * .72, s * .5))
        if kind == "plus":
            p.drawLine(QPointF(s * .5, s * .28), QPointF(s * .5, s * .72))
    elif kind == "chevron":
        p.drawLine(QPointF(s * .3, s * .4), QPointF(s * .5, s * .6))
        p.drawLine(QPointF(s * .7, s * .4), QPointF(s * .5, s * .6))
    elif kind == "check":
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.drawLine(QPointF(s * .25, s * .52), QPointF(s * .43, s * .7))
        p.drawLine(QPointF(s * .43, s * .7), QPointF(s * .76, s * .32))
    elif kind == "star":
        p.drawPath(star_path(QRectF(s * .18, s * .18, s * .64, s * .64)))
    elif kind == "app":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(0, 0, s, s), s * .22, s * .22)
        f = QFont("Georgia")
        f.setPixelSize(int(s * .58))
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "Aa")
    p.end()
    return pm


def asset_dir():
    d = Path(tempfile.gettempdir()) / "fontpreview_assets"
    d.mkdir(exist_ok=True)
    return d


# =========================================================== rendering
@dataclass
class CardLayout:
    card: QRectF
    name: QRectF
    chip: QRectF | None
    badge: QRectF | None
    star: QRectF
    text: QRectF


class Renderer:
    """Draws one preview card. Shared by the list, export and clipboard."""

    def __init__(self, win):
        self.win = win
        self._paths = OrderedDict()   # (variant, text, tracking, align)
        self._ref_h = {}              # (variant, 'H'|'x') -> ink height
        self._raw = {}                # variant -> {char: bool}
        self.label_font = QFont(QApplication.font())
        self.label_font.setPixelSize(12)
        self.label_font.setWeight(QFont.Weight.DemiBold)
        self.chip_font = QFont(QApplication.font())
        self.chip_font.setPixelSize(11)

    # ---- fonts & measurements ------------------------------------------
    @staticmethod
    def qfont(v: Variant, tracking=0):
        f = QFontDatabase.font(v.family, v.style, 12)
        f.setPixelSize(int(REF))
        f.setStyleStrategy(QFont.StyleStrategy.NoFontMerging
                           | QFont.StyleStrategy.PreferAntialias)
        f.setKerning(True)
        if tracking:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,
                               tracking / 1000.0 * REF)
        return f

    def line_step(self, v: Variant, leading):
        """Distance between baselines, ignoring the font's own line gap.
        Based on the cap height, so every font gets the same visual
        spacing (100 % = tight, 120 % = like Photoshop's auto leading)."""
        visual_em = self.ref_height(v, "H") / 0.7
        return visual_em * leading / 100.0

    def text_path(self, v: Variant, text, tracking, align, leading=120):
        key = (v.family, v.style, text, tracking, align, leading)
        hit = self._paths.get(key)
        if hit:
            self._paths.move_to_end(key)
            return hit
        f = self.qfont(v, tracking)
        fm = QFontMetricsF(f)
        step = self.line_step(v, leading)
        lines = text.split("\n")
        widths = [fm.horizontalAdvance(ln) for ln in lines]
        maxw = max(widths) if widths else 0
        path = QPainterPath()
        for i, (ln, w) in enumerate(zip(lines, widths)):
            x = (maxw - w) / 2 if align == "center" else 0
            path.addText(x, i * step, f, ln)
        ink = path.boundingRect()
        if ink.width() < 1 or ink.height() < 1:   # spaces only, etc.
            ink = QRectF(0, -fm.ascent(), max(maxw, 1),
                         step * len(lines))
        self._paths[key] = (path, ink)
        if len(self._paths) > 800:
            self._paths.popitem(last=False)
        return path, ink

    def ref_height(self, v: Variant, ch):
        key = (v.family, v.style, ch)
        if key not in self._ref_h:
            f = self.qfont(v)
            p = QPainterPath()
            p.addText(0, 0, f, ch)
            h = p.boundingRect().height()
            if h < 5:  # font without that glyph: fall back to metrics
                fm = QFontMetricsF(f)
                h = fm.capHeight() if ch == "H" else fm.xHeight()
                h = h if h > 5 else fm.ascent() * (0.7 if ch == "H" else 0.5)
            self._ref_h[key] = h
        return self._ref_h[key]

    def missing_chars(self, v: Variant, chars):
        table = self._raw.setdefault((v.family, v.style), {})
        if "__raw__" not in table:
            table["__raw__"] = QRawFont.fromFont(self.qfont(v))
        raw = table["__raw__"]
        out = []
        for c in chars:
            if c not in table:
                try:
                    table[c] = bool(raw.supportsCharacter(ord(c)))
                except Exception:
                    table[c] = True
            if not table[c]:
                out.append(c)
        return out

    def check_chars(self):
        sample = self.win.state["sample"]
        extra = [c for c in dict.fromkeys(sample) if not c.isspace()]
        return list(dict.fromkeys(list(ACCENT_TEST) + extra))

    # ---- layout --------------------------------------------------------
    def layout(self, rect: QRectF, entry: Entry, v: Variant, header=True,
               with_badge=True):
        card = QRectF(rect)
        pad_x = max(16.0, min(40.0, card.width() * 0.035))
        top = HEADER_H if header else 0
        bottom = max(14.0, min(36.0, card.height() * 0.10))
        if not header:
            top = bottom
        text = card.adjusted(pad_x, top, -pad_x, -bottom)
        star = QRectF(card.right() - pad_x - 16, card.top() + 10, 16, 16)
        badge = None
        if header and with_badge and self.missing_chars(v, self.check_chars()):
            badge = QRectF(star.left() - 26, card.top() + 9, 18, 18)
        right_limit = (badge or star).left() - 10
        fm_l = QFontMetricsF(self.label_font)
        fm_c = QFontMetricsF(self.chip_font)
        chip_w = fm_c.horizontalAdvance(v.label) + (
            26 if len(entry.variants) > 1 else 14)
        avail = max(20.0, right_limit - card.left() - pad_x)
        full_name = fm_l.horizontalAdvance(entry.name)
        if full_name + 6 + chip_w <= avail:      # room for both
            name_w, show_chip = full_name, True
        else:                                    # the name always wins
            name_w, show_chip = min(full_name, avail), False
        name = QRectF(card.left() + pad_x, card.top() + 8, name_w, 20)
        chip = (QRectF(name.right() + 6, card.top() + 8, chip_w, 20)
                if show_chip else None)
        return CardLayout(card, name, chip, badge, star, text)

    # ---- painting ------------------------------------------------------
    def paint_card(self, p: QPainter, rect: QRectF, entry: Entry, *,
                   header=True, selected=False, hovered=False, dim=False,
                   border=True, radius=12.0, mode=None, export=False,
                   wrap=False):
        win = self.win
        st = win.state
        t = THEMES[st["theme"]]
        v = win.variant_for(entry)
        bg, fg = QColor(st["bg"]), QColor(st["fg"])
        light = bg.lightnessF() > 0.5
        ink = QColor(0, 0, 0) if light else QColor(255, 255, 255)

        lay = self.layout(rect, entry, v, header=header,
                          with_badge=not export)
        shape = QPainterPath()
        shape.addRoundedRect(lay.card, radius, radius)

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if dim:
            p.setOpacity(0.4)
        p.fillPath(shape, bg)

        if header:
            name_c = QColor(ink)
            name_c.setAlphaF(0.72)
            p.setFont(self.label_font)
            p.setPen(name_c)
            fm = QFontMetricsF(self.label_font)
            p.drawText(lay.name, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(entry.name, Qt.TextElideMode.ElideRight,
                                     lay.name.width()))
            if lay.chip:
                chip_c = QColor(ink)
                chip_c.setAlphaF(0.48)
                multi = len(entry.variants) > 1 and not export
                if multi and (hovered or selected):
                    hl = QColor(ink)
                    hl.setAlphaF(0.07)
                    cp = QPainterPath()
                    cp.addRoundedRect(lay.chip, 6, 6)
                    p.fillPath(cp, hl)
                p.setFont(self.chip_font)
                p.setPen(chip_c)
                tr = lay.chip.adjusted(7, 0, -(19 if multi else 7), 0)
                p.drawText(tr, Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter,
                           QFontMetricsF(self.chip_font).elidedText(
                               v.label, Qt.TextElideMode.ElideRight,
                               tr.width()))
                if multi:
                    c = QPointF(lay.chip.right() - 11, lay.chip.center().y())
                    pen = QPen(chip_c, 1.3)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(c + QPointF(-3, -1.5), c + QPointF(0, 1.5))
                    p.drawLine(c + QPointF(3, -1.5), c + QPointF(0, 1.5))
            if lay.badge:
                tri = QPainterPath()
                b = lay.badge
                tri.moveTo(b.center().x(), b.top() + 1)
                tri.lineTo(b.right(), b.bottom() - 1)
                tri.lineTo(b.left(), b.bottom() - 1)
                tri.closeSubpath()
                p.fillPath(tri, QColor(t["warn"]))
                f = QFont(self.label_font)
                f.setPixelSize(11)
                f.setWeight(QFont.Weight.Black)
                p.setFont(f)
                p.setPen(QColor("#1b1300"))
                p.drawText(b.adjusted(0, 4, 0, 0), Qt.AlignmentFlag.AlignCenter,
                           "!")
            fav = entry.name in st["favorites"]
            if not export and (fav or hovered or selected):
                sp = star_path(lay.star)
                if fav:
                    p.fillPath(sp, QColor(t["star"]))
                else:
                    oc = QColor(ink)
                    oc.setAlphaF(0.35)
                    p.setPen(QPen(oc, 1.3))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(sp)

        # the preview itself
        p.setClipPath(shape)
        self.draw_text(p, lay.text, v, fg, mode or st["size_mode"],
                       wrap=wrap)
        p.setClipping(False)

        if dim:
            p.setOpacity(1.0)
        if border and not selected:
            bc = QColor(t["border_hover"] if hovered else t["border"])
            p.setPen(QPen(bc, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(lay.card.adjusted(.5, .5, -.5, -.5),
                              radius, radius)
        if selected:
            p.setPen(QPen(QColor(t["accent"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(lay.card.adjusted(1, 1, -1, -1), radius, radius)
        p.restore()
        return lay

    def wrap_for_box(self, v, text, tracking, aspect, leading=120):
        """Break a single line into 1-4 balanced lines so it fills a box of
        the given width/height ratio as large as possible."""
        key = ("wrap", v.family, v.style, text, tracking, round(aspect, 1),
               leading)
        if key in self._paths:
            return self._paths[key]
        words = text.split()
        result = text
        if 1 < len(words) <= 40:
            fm = QFontMetricsF(self.qfont(v, tracking))
            n = len(words)
            width = {}

            def w(i, j):
                if (i, j) not in width:
                    width[(i, j)] = fm.horizontalAdvance(" ".join(words[i:j]))
                return width[(i, j)]

            best_s = 0.0
            for k in range(1, min(4, n) + 1):
                # DP: split words into k lines minimising the widest line
                INF = float("inf")
                dp = [[INF] * (n + 1) for _ in range(k + 1)]
                cut = [[0] * (n + 1) for _ in range(k + 1)]
                dp[0][0] = 0
                for lines in range(1, k + 1):
                    for j in range(1, n + 1):
                        for i in range(lines - 1, j):
                            c = max(dp[lines - 1][i], w(i, j))
                            if c < dp[lines][j]:
                                dp[lines][j], cut[lines][j] = c, i
                parts, j = [], n
                for lines in range(k, 0, -1):
                    i = cut[lines][j]
                    parts.append(" ".join(words[i:j]))
                    j = i
                parts.reverse()
                step = self.line_step(v, leading)
                h = step / 1.2 * 1.3 + (k - 1) * step
                s = min(aspect / max(dp[k][n], 1), 1 / h)
                if s > best_s * 1.12:   # only add a line if clearly bigger
                    best_s, result = s, "\n".join(parts)
        self._paths[key] = result
        return result

    def draw_text(self, p, R: QRectF, v: Variant, fg: QColor, mode,
                  wrap=False):
        st = self.win.state
        if R.width() < 4 or R.height() < 4:
            return
        text = self.win.render_text()
        sample_chars = [c for c in dict.fromkeys(text) if not c.isspace()]
        miss = self.missing_chars(v, sample_chars)
        if sample_chars and len(miss) > len(sample_chars) / 2:
            c = QColor(fg)
            c.setAlphaF(0.45)
            f = QFont(QApplication.font())
            f.setPixelSize(max(12, min(16, int(R.height() * 0.18))))
            p.setFont(f)
            p.setPen(c)
            p.drawText(R, Qt.TextFlag.TextWordWrap
                       | Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignHCenter if st["align"] == "center"
                else Qt.AlignmentFlag.AlignLeft),
                "This font can’t display your text")
            return
        if wrap and mode == "fit" and "\n" not in text:
            text = self.wrap_for_box(v, text, st["tracking"],
                                     R.width() / R.height(), st["leading"])
        path, ink = self.text_path(v, text, st["tracking"], st["align"],
                                   st["leading"])
        if mode == "fit":
            s = min(R.width() / ink.width(), R.height() / ink.height())
            s *= st["fit_fill"] / 100.0
        elif mode == "cap":
            s = st["px_size"] / self.ref_height(v, "H")
        elif mode == "x":
            s = st["px_size"] / self.ref_height(v, "x")
        else:
            s = st["px_size"] / REF
        if st["align"] == "center":
            x = R.center().x() - (ink.left() + ink.width() / 2) * s
        else:
            x = R.left() - ink.left() * s
        y = R.center().y() - (ink.top() + ink.height() / 2) * s
        p.save()
        p.translate(x, y)
        p.scale(s, s)
        p.fillPath(path, fg)
        p.restore()

    # ---- export ----------------------------------------------------------
    def render_export(self, entries, cfg, max_width=None):
        st = self.win.state
        W = 1600.0
        m = 56.0
        cols = 1 if cfg["layout"] == "stack" else max(1, int(cfg["cols"]))
        cols = min(cols, max(1, len(entries)))
        cell_w = (W - 2 * m) / cols
        cell_h = 240.0 if cols == 1 else cell_w * 0.62
        if not cfg["names"]:
            cell_h *= 0.85
        rows = math.ceil(len(entries) / cols)
        wm = cfg["watermark"].strip() if cfg["watermark_on"] else ""
        H = m + rows * cell_h + (m * 0.6 + 24 if wm else m)
        scale = float(cfg.get("scale", 2))
        if max_width:
            scale = max_width / W
        img = QImage(int(W * scale), int(H * scale),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(st["bg"]))
        p = QPainter(img)
        p.scale(scale, scale)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, e in enumerate(entries):
            r, c = divmod(i, cols)
            rect = QRectF(m + c * cell_w, m + r * cell_h, cell_w, cell_h)
            self.paint_card(p, rect, e, header=cfg["names"], border=False,
                            radius=0, mode="fit", export=True,
                            wrap=cols > 1)
        # hairline dividers between cells
        line = QColor(st["fg"])
        line.setAlphaF(0.10)
        p.setPen(QPen(line, 1))
        for r in range(1, rows):
            y = m + r * cell_h
            p.drawLine(QPointF(m, y), QPointF(W - m, y))
        for c in range(1, cols):
            x = m + c * cell_w
            p.drawLine(QPointF(x, m), QPointF(x, m + rows * cell_h))
        if wm:
            wc = QColor(st["fg"])
            wc.setAlphaF(0.45)
            f = QFont(QApplication.font())
            f.setPixelSize(15)
            p.setFont(f)
            p.setPen(wc)
            p.drawText(QRectF(m, H - m * 0.6 - 24, W - 2 * m, 24),
                       Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter, wm)
        p.end()
        return img


# =========================================================== model / view
class FontModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.entries = []

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return e.name
        if role == Qt.ItemDataRole.UserRole:
            return e
        return None


class CardDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index):
        return self.win.cell_size

    @staticmethod
    def card_rect(option):
        return QRectF(option.rect).adjusted(GAP / 2, GAP / 2,
                                            -GAP / 2, -GAP / 2)

    def paint(self, painter, option, index):
        e = index.data(Qt.ItemDataRole.UserRole)
        st = option.state
        self.win.renderer.paint_card(
            painter, self.card_rect(option), e,
            selected=bool(st & QStyle.StateFlag.State_Selected),
            hovered=bool(st & QStyle.StateFlag.State_MouseOver),
            dim=e.name in self.win.state["hidden"],
            wrap=self.win.state["view"] == "grid")

    def _layout(self, option, index):
        e = index.data(Qt.ItemDataRole.UserRole)
        return e, self.win.renderer.layout(self.card_rect(option), e,
                                           self.win.variant_for(e))

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            e, lay = self._layout(option, index)
            pos = QPointF(event.position())
            if lay.star.adjusted(-6, -6, 6, 6).contains(pos):
                self.win.toggle_favorite(e)
                return True
            if (lay.chip and len(e.variants) > 1
                    and lay.chip.contains(pos)):
                gp = self.win.view.viewport().mapToGlobal(
                    lay.chip.bottomLeft().toPoint())
                self.win.style_menu(e, gp)
                return True
        return False

    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.Type.ToolTip or not index.isValid():
            return super().helpEvent(event, view, option, index)
        e, lay = self._layout(option, index)
        pos = QPointF(event.pos())
        tip = ""
        if lay.badge and lay.badge.adjusted(-4, -4, 4, 4).contains(pos):
            miss = self.win.renderer.missing_chars(
                self.win.variant_for(e), self.win.renderer.check_chars())
            tip = "Missing characters:  " + "  ".join(miss)
        elif lay.star.adjusted(-6, -6, 6, 6).contains(pos):
            tip = ("Remove from favorites" if e.name in
                   self.win.state["favorites"] else "Add to favorites") \
                + "  (Space)"
        elif lay.chip and lay.chip.contains(pos) and len(e.variants) > 1:
            tip = f"{len(e.variants)} styles — click to change  ( [  ] )"
        elif lay.name.contains(pos):
            tip = e.name + ("  ·  installed by you" if e.mine else "")
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
        else:
            QToolTip.hideText()
        return True


class FontView(QListView):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("Fonts")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setViewportMargins(10, 10, 4, 10)

        self.empty = QLabel(self.viewport())
        self.empty.setObjectName("Empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.hide()

    def show_empty(self, text):
        self.empty.setText(text)
        self.empty.setVisible(bool(text))
        self._place_empty()

    def _place_empty(self):
        vp = self.viewport().rect()
        self.empty.setGeometry(vp.adjusted(40, 0, -40, 0))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.win.update_cell_size()
        self._place_empty()

    def keyPressEvent(self, e):
        idx = self.currentIndex()
        entry = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
        shift = e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        key = e.key()
        if entry is not None:
            if key == Qt.Key.Key_Space:
                self.win.toggle_favorite(entry)
                return
            if key == Qt.Key.Key_H and not ctrl:
                self.win.toggle_hidden(entry)
                return
            if key == Qt.Key.Key_C and ctrl and shift:
                self.win.copy_image(entry)
                return
            if key == Qt.Key.Key_C and ctrl:
                self.win.copy_name(entry)
                return
            if key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
                self.win.cycle_style(
                    entry, -1 if key == Qt.Key.Key_BracketLeft else 1)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    len(entry.variants) > 1:
                r = self.visualRect(idx)
                self.win.style_menu(entry, self.viewport().mapToGlobal(
                    r.topLeft() + r.center() * 0))
                return
        super().keyPressEvent(e)


# =========================================================== small widgets
class Segmented(QFrame):
    """Pill-style segmented control."""

    def __init__(self, options, current, on_change):
        super().__init__()
        self.setObjectName("Seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == current)
            b.setProperty("key", key)
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        self.group.buttonClicked.connect(
            lambda b: on_change(b.property("key")))

    def set_current(self, key):
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class Stepper(QFrame):
    def __init__(self, lo, hi, value, on_change):
        super().__init__()
        self.setObjectName("Seg")
        self.lo, self.hi, self.value, self.on_change = lo, hi, value, on_change
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(0)
        self.minus = QToolButton()
        self.plus = QToolButton()
        for b in (self.minus, self.plus):
            b.setObjectName("StepBtn")
            b.setAutoRepeat(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label = QLabel(str(value))
        self.label.setObjectName("StepVal")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumWidth(34)
        lay.addWidget(self.minus)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self.step(-1))
        self.plus.clicked.connect(lambda: self.step(1))

    def set_icons(self, color):
        self.minus.setIcon(QIcon(make_icon("minus", color, 16)))
        self.plus.setIcon(QIcon(make_icon("plus", color, 16)))

    def step(self, d):
        v = max(self.lo, min(self.hi, self.value + d))
        if v != self.value:
            self.value = v
            self.label.setText(str(v))
            self.on_change(v)

    def set_value(self, v):
        self.value = v
        self.label.setText(str(v))


class ColorButton(QPushButton):
    """Shows the hex code and a swatch; click to pick a color."""

    def __init__(self):
        super().__init__("")
        self.setObjectName("ColorBtn")
        self.setFixedWidth(118)
        self.color = QColor("#000")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, c):
        self.color = QColor(c)
        self.setText(self.color.name().upper())
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.width() - 30, (self.height() - 18) / 2, 18, 18)
        p.setPen(QPen(QColor(128, 128, 128, 110), 1))
        p.setBrush(self.color)
        p.drawRoundedRect(r, 5, 5)
        p.end()


class SampleEdit(QPlainTextEdit):
    """Multi-line sample text that grows from 1 to 4 lines."""

    def __init__(self, text, on_change):
        super().__init__()
        self.setObjectName("Sample")
        self.setPlainText(text)
        self.setTabChangesFocus(True)
        self.setPlaceholderText("Type your sample text…  (Enter for a new line)")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.textChanged.connect(lambda: on_change(self.toPlainText()))
        self.textChanged.connect(self._fit)
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        lines = min(max(self.document().blockCount(), 1), 4)
        lh = QFontMetricsF(self.font()).lineSpacing()
        self.setFixedHeight(int(lines * lh + 26))
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if self.document().blockCount() > 4
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


def section(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("Section")
    return lbl


def field_row(label_text, widget=None, value_label=None):
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(label_text)
    lbl.setObjectName("Field")
    row.addWidget(lbl)
    row.addStretch(1)
    if value_label is not None:
        row.addWidget(value_label)
    if widget is not None:
        row.addWidget(widget)
    return row


# =========================================================== export dialog
class ExportDialog(QDialog):
    def __init__(self, win, entries):
        super().__init__(win)
        self.win, self.entries = win, entries
        self.cfg = copy.deepcopy(win.state["export"])
        self.setWindowTitle("Export shortlist")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        title = QLabel("Export shortlist")
        title.setObjectName("DialogTitle")
        lay.addWidget(title)
        info = QLabel(f"{len(entries)} favorite font"
                      f"{'s' if len(entries) != 1 else ''} · current sample "
                      "text and colors · text always fits each cell")
        info.setObjectName("Muted")
        lay.addWidget(info)

        self.preview = QLabel()
        self.preview.setObjectName("ExportPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(300)
        lay.addWidget(self.preview)

        self.layout_seg = Segmented([("stack", "Stack"), ("grid", "Grid")],
                                    self.cfg["layout"], self._set_layout)
        self.cols = Stepper(2, 4, int(self.cfg["cols"]), self._set_cols)
        self.cols.set_icons(THEMES[win.state["theme"]]["text"])
        row = QHBoxLayout()
        row.addWidget(QLabel("Layout"))
        row.addWidget(self.layout_seg)
        row.addSpacing(16)
        self.cols_lbl = QLabel("Columns")
        row.addWidget(self.cols_lbl)
        row.addWidget(self.cols)
        row.addStretch(1)
        lay.addLayout(row)

        self.names = QCheckBox("Show font names")
        self.names.setChecked(self.cfg["names"])
        self.names.toggled.connect(self._changed)
        lay.addWidget(self.names)

        wm_row = QHBoxLayout()
        self.wm_on = QCheckBox("Watermark")
        self.wm_on.setChecked(self.cfg["watermark_on"])
        self.wm_on.toggled.connect(self._changed)
        self.wm_text = QLineEdit(self.cfg["watermark"])
        self.wm_text.setPlaceholderText("e.g. Samuel — Design")
        self.wm_text.textChanged.connect(self._changed)
        wm_row.addWidget(self.wm_on)
        wm_row.addWidget(self.wm_text, 1)
        lay.addLayout(wm_row)

        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("Resolution"))
        self.res = Segmented([("1", "Standard"), ("2", "High (2×)")],
                             str(int(self.cfg["scale"])), self._set_scale)
        res_row.addWidget(self.res)
        res_row.addStretch(1)
        lay.addLayout(res_row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Export PNG…")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self._changed()

    def _set_layout(self, k):
        self.cfg["layout"] = k
        self._changed()

    def _set_cols(self, v):
        self.cfg["cols"] = v
        self._changed()

    def _set_scale(self, k):
        self.cfg["scale"] = int(k)

    def _changed(self, *_):
        self.cfg["names"] = self.names.isChecked()
        self.cfg["watermark_on"] = self.wm_on.isChecked()
        self.cfg["watermark"] = self.wm_text.text()
        grid = self.cfg["layout"] == "grid"
        self.cols.setEnabled(grid)
        self.cols_lbl.setEnabled(grid)
        self.wm_text.setEnabled(self.wm_on.isChecked())
        img = self.win.renderer.render_export(self.entries, self.cfg,
                                              max_width=1600)
        box_w, box_h = 2 * (self.width() - 70), 2 * 276
        img = img.scaled(box_w, box_h, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(2)
        self.preview.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._changed()


# =========================================================== main window
class MainWindow(QMainWindow):
    def __init__(self, library: Library, entries):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.state = load_settings()
        self.library = library
        self.entries = entries
        self.by_name = {e.name: e for e in entries}
        self.cell_size = QSize(400, 160)
        self.renderer = Renderer(self)
        self._migrate_names()

        self._build()
        self.apply_theme()
        self.refresh()
        geo = self.state.get("geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromHex(geo.encode()))
        else:
            self.resize(1200, 900)

    # ---------------------------------------------------- state helpers
    def _migrate_names(self):
        """v1 stored raw family names; map them onto grouped entries."""
        fam_to_entry = {v.family: e.name for e in self.entries
                        for v in e.variants}
        for key in ("favorites", "hidden"):
            out = []
            for n in self.state[key]:
                n = n if n in self.by_name else fam_to_entry.get(n, n)
                if n not in out:
                    out.append(n)
            self.state[key] = out

    def variant_for(self, e: Entry) -> Variant:
        label = self.state["overrides"].get(e.name)
        if label:
            for v in e.variants:
                if v.label == label:
                    return v
        key, _, w, it = STYLE_BY_KEY.get(self.state["style"], STYLES[0])
        if key == "regular":
            return e.variants[e.default]
        return e.variants[nearest_variant(e, w, it)]

    def render_text(self):
        t = self.state["sample"]
        return t if t.strip() else "Aa"

    # ---------------------------------------------------- build UI
    def _build(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(256)
        sb = QVBoxLayout(self.sidebar)
        sb.setContentsMargins(18, 14, 18, 16)
        sb.setSpacing(8)

        brand = QHBoxLayout()
        self.brand_icon = QLabel()
        brand.addWidget(self.brand_icon)
        bt = QLabel(APP_NAME)
        bt.setObjectName("Brand")
        brand.addWidget(bt)
        brand.addStretch(1)
        sb.addLayout(brand)

        # layout
        sb.addWidget(section("Layout"))
        self.rows_stepper = Stepper(1, 10, self.state["list_rows"],
                                    self._set_rows)
        self.cols_stepper = Stepper(1, 6, self.state["grid_cols"],
                                    self._set_cols)
        self.rows_row = QWidget()
        rr = QHBoxLayout(self.rows_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addLayout(field_row("Rows per screen", self.rows_stepper))
        self.cols_row = QWidget()
        cr = QHBoxLayout(self.cols_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addLayout(field_row("Columns", self.cols_stepper))
        sb.addWidget(self.rows_row)
        sb.addWidget(self.cols_row)
        self.align_seg = Segmented([("left", "Left"), ("center", "Center")],
                                   self.state["align"], self._set_align)
        sb.addLayout(field_row("Align", self.align_seg))

        # sizing
        sb.addWidget(section("Sizing"))
        self.mode_box = QComboBox()
        for k, label in SIZE_MODES:
            self.mode_box.addItem(label, k)
        self.mode_box.setCurrentIndex(
            [k for k, _ in SIZE_MODES].index(self.state["size_mode"]))
        self.mode_box.setToolTip(
            "Fit to card — every font fills its card the same way\n"
            "Cap height — capitals match across fonts\n"
            "x-height — lowercase letters match across fonts\n"
            "True size — real pixel size (fonts vary)")
        self.mode_box.currentIndexChanged.connect(self._set_mode)
        sb.addWidget(self.mode_box)

        self.size_value = QLabel()
        self.size_value.setObjectName("Value")
        self.size_label = QLabel("Size")
        self.size_label.setObjectName("Field")
        srow = QHBoxLayout()
        srow.addWidget(self.size_label)
        srow.addStretch(1)
        srow.addWidget(self.size_value)
        sb.addLayout(srow)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.valueChanged.connect(self._set_size)
        sb.addWidget(self.size_slider)

        self.track_value = QLabel()
        self.track_value.setObjectName("Value")
        trow = QHBoxLayout()
        tl = QLabel("Tracking")
        tl.setObjectName("Field")
        trow.addWidget(tl)
        trow.addStretch(1)
        trow.addWidget(self.track_value)
        sb.addLayout(trow)
        self.track_slider = QSlider(Qt.Orientation.Horizontal)
        self.track_slider.setRange(-100, 400)
        self.track_slider.setSingleStep(5)
        self.track_slider.setPageStep(25)
        self.track_slider.setValue(self.state["tracking"])
        self.track_slider.setToolTip("Letter spacing in 1/1000 em, "
                                     "same unit as Photoshop.\n"
                                     "Double-click to reset.")
        self.track_slider.valueChanged.connect(self._set_tracking)
        self.track_slider.installEventFilter(self)
        sb.addWidget(self.track_slider)

        self.lead_value = QLabel()
        self.lead_value.setObjectName("Value")
        lrow = QHBoxLayout()
        ll = QLabel("Line spacing")
        ll.setObjectName("Field")
        lrow.addWidget(ll)
        lrow.addStretch(1)
        lrow.addWidget(self.lead_value)
        sb.addLayout(lrow)
        self.lead_slider = QSlider(Qt.Orientation.Horizontal)
        self.lead_slider.setRange(70, 250)
        self.lead_slider.setSingleStep(5)
        self.lead_slider.setPageStep(10)
        self.lead_slider.setValue(self.state["leading"])
        self.lead_slider.setToolTip(
            "Space between lines, the same for every font\n"
            "(fixes fonts that come with huge built-in line gaps).\n"
            "120 % is like Photoshop's auto leading. Double-click to reset.")
        self.lead_slider.valueChanged.connect(self._set_leading)
        self.lead_slider.installEventFilter(self)
        sb.addWidget(self.lead_slider)

        self.style_box = QComboBox()
        for k, label, *_ in STYLES:
            self.style_box.addItem(label, k)
        keys = [s[0] for s in STYLES]
        self.style_box.setCurrentIndex(
            keys.index(self.state["style"])
            if self.state["style"] in keys else 0)
        self.style_box.setToolTip("Closest matching style in every family.\n"
                                  "Change a single card with its style menu.")
        self.style_box.currentIndexChanged.connect(self._set_style)
        sb.addLayout(field_row("Style", self.style_box))

        # colors
        self.swap_btn = QToolButton()
        self.swap_btn.setObjectName("Icon")
        self.swap_btn.setToolTip("Swap text and background")
        self.swap_btn.clicked.connect(self._swap_colors)
        chead = QHBoxLayout()
        chead.addWidget(section("Colors"))
        chead.addStretch(1)
        chead.addWidget(self.swap_btn, 0, Qt.AlignmentFlag.AlignBottom)
        sb.addLayout(chead)
        self.fg_btn = ColorButton()
        self.bg_btn = ColorButton()
        self.fg_btn.clicked.connect(lambda: self._pick_color("fg"))
        self.bg_btn.clicked.connect(lambda: self._pick_color("bg"))
        sb.addLayout(field_row("Text", self.fg_btn))
        sb.addLayout(field_row("Background", self.bg_btn))
        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(5)
        sb.addLayout(self.swatch_row)

        # library
        sb.addWidget(section("Library"))
        self.lib_seg = Segmented([("all", "All fonts"), ("mine", "My fonts")],
                                 self.state["library"], self._set_library)
        if not library_ok(self.library):
            self.lib_seg.buttons["mine"].setEnabled(False)
            self.lib_seg.setToolTip("Install fontTools to use “My fonts”:\n"
                                    "pip install fonttools")
        sb.addWidget(self.lib_seg)
        self.hidden_check = QCheckBox("Show hidden fonts")
        self.hidden_check.setChecked(self.state["show_hidden"])
        self.hidden_check.toggled.connect(self._set_show_hidden)
        sb.addWidget(self.hidden_check)

        sb.addStretch(1)
        self.export_btn = QPushButton("Export shortlist…")
        self.export_btn.setObjectName("Primary")
        self.export_btn.setToolTip("Ctrl+E")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self.export_png)
        sb.addWidget(self.export_btn)
        self.theme_seg = Segmented([("dark", "Dark"), ("light", "Light")],
                                   self.state["theme"], self._set_theme)
        sb.addWidget(self.theme_seg)
        root.addWidget(self.sidebar)

        # ---------- main column
        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header.setSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Fixed)
        hv = QVBoxLayout(header)
        hv.setContentsMargins(16, 14, 16, 12)
        hv.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.sample = SampleEdit(self.state["sample"], self._set_sample)
        top.addWidget(self.sample, 1)
        self.preset_btn = QToolButton()
        self.preset_btn.setObjectName("Presets")
        self.preset_btn.setText("Presets")
        self.preset_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pm = QMenu(self.preset_btn)
        for name, text in PRESETS:
            a = pm.addAction(name)
            a.triggered.connect(lambda _=False, t=text:
                                self.sample.setPlainText(t))
        self.preset_btn.setMenu(pm)
        top.addWidget(self.preset_btn, 0, Qt.AlignmentFlag.AlignTop)
        hv.addLayout(top)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.sidebar_btn = QToolButton()
        self.sidebar_btn.setObjectName("Icon")
        self.sidebar_btn.setToolTip("Show / hide sidebar  (Ctrl+\\)")
        self.sidebar_btn.clicked.connect(self.toggle_sidebar)
        bar.addWidget(self.sidebar_btn)
        self.view_seg = Segmented([("list", "List"), ("grid", "Grid"),
                                   ("compare", "Favorites")],
                                  self.state["view"], self._set_view)
        self.view_seg.setToolTip("Ctrl+1 / Ctrl+2 / Ctrl+3")
        bar.addWidget(self.view_seg)
        bar.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        bar.addWidget(self.count_label)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search fonts")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.search_action = self.search.addAction(
            QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        bar.addWidget(self.search)
        hv.addLayout(bar)
        mv.addWidget(header)

        self.model = FontModel()
        self.view = FontView(self)
        self.view.setModel(self.model)
        self.view.setItemDelegate(CardDelegate(self))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        mv.addWidget(self.view, 1)
        root.addWidget(main, 1)
        self.setCentralWidget(central)

        self.sidebar.setVisible(self.state["sidebar"])
        self._sync_size_controls()
        self._sync_layout_controls()
        self._update_color_buttons()

        # shortcuts
        for seq, fn in (
                ("Ctrl+F", lambda: (self.search.setFocus(),
                                    self.search.selectAll())),
                ("Ctrl+L", lambda: (self.sample.setFocus(),
                                    self.sample.selectAll())),
                ("Ctrl+E", self.export_png),
                ("Ctrl+\\", self.toggle_sidebar),
                ("Ctrl+1", lambda: self._set_view("list", sync=True)),
                ("Ctrl+2", lambda: self._set_view("grid", sync=True)),
                ("Ctrl+3", lambda: self._set_view("compare", sync=True))):
            QShortcut(QKeySequence(seq), self, activated=fn)
        QShortcut(QKeySequence("Escape"), self.search,
                  activated=self._escape_search)
        QShortcut(QKeySequence("Down"), self.search,
                  activated=lambda: self.view.setFocus())

    def eventFilter(self, obj, ev):
        if obj is self.track_slider and \
                ev.type() == QEvent.Type.MouseButtonDblClick:
            self.track_slider.setValue(0)
            return True
        if obj is getattr(self, "lead_slider", None) and \
                ev.type() == QEvent.Type.MouseButtonDblClick:
            self.lead_slider.setValue(120)
            return True
        return super().eventFilter(obj, ev)

    # ---------------------------------------------------- theme
    def apply_theme(self):
        t = THEMES[self.state["theme"]]
        d = asset_dir()
        chev = d / f"chevron_{self.state['theme']}.png"
        check = d / "check.png"
        make_icon("chevron", t["muted"], 14).save(str(chev))
        make_icon("check", "#ffffff", 14).save(str(check))
        qss = QSS
        for k, val in t.items():
            qss = qss.replace("@" + k + ";", val + ";")
            qss = qss.replace("@" + k + " ", val + " ")
        qss = qss.replace("@chevron", chev.as_posix())
        qss = qss.replace("@check", check.as_posix())
        self.setStyleSheet(qss)
        QApplication.setPalette(theme_palette(t))

        self.sidebar_btn.setIcon(QIcon(make_icon("sidebar", t["muted"])))
        self.swap_btn.setIcon(QIcon(make_icon("swap", t["muted"])))
        self.search_action.setIcon(QIcon(make_icon("search", t["muted"], 16)))
        self.brand_icon.setPixmap(make_icon("app", t["accent"], 22))
        self.setWindowIcon(QIcon(make_icon("app", t["accent"], 64)))
        for s in (self.rows_stepper, self.cols_stepper):
            s.set_icons(t["text"])
        self._update_color_buttons()
        self._rebuild_swatches()
        self.view.viewport().update()

    # ---------------------------------------------------- setters
    def _set_sample(self, text):
        self.state["sample"] = text
        self.view.viewport().update()

    def _set_rows(self, v):
        self.state["list_rows"] = v
        self.update_cell_size()

    def _set_cols(self, v):
        self.state["grid_cols"] = v
        self.update_cell_size()

    def _set_align(self, k):
        self.state["align"] = k
        self.view.viewport().update()

    def _set_mode(self, i):
        self.state["size_mode"] = self.mode_box.itemData(i)
        self._sync_size_controls()
        self.view.viewport().update()

    def _set_size(self, v):
        if self.state["size_mode"] == "fit":
            self.state["fit_fill"] = v
        else:
            self.state["px_size"] = v
        self._sync_size_label()
        self.view.viewport().update()

    def _set_leading(self, v):
        self.state["leading"] = v
        self.lead_value.setText(f"{v} %")
        self.view.viewport().update()

    def _set_tracking(self, v):
        self.state["tracking"] = v
        self.track_value.setText(f"{v:+d}" if v else "0")
        self.view.viewport().update()

    def _set_style(self, i):
        self.state["style"] = self.style_box.itemData(i)
        self.state["overrides"] = {}
        self.view.viewport().update()

    def _set_view(self, k, sync=False):
        self.state["view"] = k
        if sync:
            self.view_seg.set_current(k)
        self._sync_layout_controls()
        self.refresh()

    def _set_library(self, k):
        self.state["library"] = k
        self.refresh()

    def _set_show_hidden(self, on):
        self.state["show_hidden"] = bool(on)
        self.refresh()

    def _set_theme(self, k):
        self.state["theme"] = k
        self.apply_theme()

    def toggle_sidebar(self):
        self.state["sidebar"] = not self.sidebar.isVisible()
        self.sidebar.setVisible(self.state["sidebar"])

    def _escape_search(self):
        self.search.clear()
        self.view.setFocus()

    def _sync_size_controls(self):
        fit = self.state["size_mode"] == "fit"
        self.size_slider.blockSignals(True)
        if fit:
            self.size_slider.setRange(30, 100)
            self.size_slider.setValue(self.state["fit_fill"])
            self.size_label.setText("Fill")
        else:
            self.size_slider.setRange(12, 300)
            self.size_slider.setValue(self.state["px_size"])
            self.size_label.setText(
                {"cap": "Cap height", "x": "x-height"}.get(
                    self.state["size_mode"], "Size"))
        self.size_slider.blockSignals(False)
        self._sync_size_label()
        self.track_value.setText(f"{self.state['tracking']:+d}"
                                 if self.state["tracking"] else "0")
        self.lead_value.setText(f"{self.state['leading']} %")

    def _sync_size_label(self):
        if self.state["size_mode"] == "fit":
            self.size_value.setText(f"{self.state['fit_fill']} %")
        else:
            self.size_value.setText(f"{self.state['px_size']} px")

    def _sync_layout_controls(self):
        grid = self.state["view"] == "grid"
        self.cols_row.setVisible(grid)
        self.rows_row.setVisible(not grid)

    # ---------------------------------------------------- layout math
    def update_cell_size(self):
        vp = self.view.viewport()
        vw, vh = max(200, vp.width()), max(200, vp.height())
        if self.state["view"] == "grid":
            cols = self.state["grid_cols"]
            # Qt wraps a cell onto the next row when a row fills the width
            # exactly, so keep a few pixels of slack
            w = (vw - 4) // cols
            h = max(120, int(w * 0.58))
        else:
            rows = self.state["list_rows"]
            w = vw
            h = max(84, vh // rows)
        size = QSize(int(w), int(h))
        if size != self.cell_size:
            self.cell_size = size
            self.view.setGridSize(size)
            self.view.verticalScrollBar().setSingleStep(max(20, h // 3))
            self.view.doItemsLayout()
        self.view.viewport().update()

    # ---------------------------------------------------- filtering
    def refresh(self):
        st = self.state
        current = self.view.currentIndex().data(Qt.ItemDataRole.DisplayRole)
        q = self.search.text().strip().lower()
        hidden = set(st["hidden"])
        favs = set(st["favorites"])
        out = []
        for e in self.entries:
            if st["view"] == "compare" and e.name not in favs:
                continue
            if st["library"] == "mine" and not e.mine:
                continue
            if e.name in hidden and not st["show_hidden"] \
                    and st["view"] != "compare":
                continue
            if q and q not in e.name.lower():
                continue
            out.append(e)
        if st["view"] == "compare":   # keep the order you starred them in
            order = {n: i for i, n in enumerate(st["favorites"])}
            out.sort(key=lambda e: order.get(e.name, 0))
        self.model.set_entries(out)
        self.update_cell_size()

        n_hidden = sum(1 for e in self.entries if e.name in hidden)
        parts = [f"{len(out)} font{'s' if len(out) != 1 else ''}"]
        if n_hidden and st["view"] != "compare":
            parts.append(f"{n_hidden} hidden")
        self.count_label.setText("  ·  ".join(parts))
        self._update_fav_label()

        empty = ""
        if not out:
            if q:
                empty = f"No fonts match “{self.search.text().strip()}”."
            elif st["view"] == "compare":
                empty = ("No favorites yet.\nStar fonts with ★ or the "
                         "Space bar to compare them here.")
            elif st["library"] == "mine":
                empty = "No fonts installed by you were found."
        self.view.show_empty(empty)

        if current:
            for row, e in enumerate(out):
                if e.name == current:
                    self.view.setCurrentIndex(self.model.index(row))
                    break

    # ---------------------------------------------------- actions
    def toggle_favorite(self, e):
        favs = self.state["favorites"]
        if e.name in favs:
            favs.remove(e.name)
        else:
            favs.append(e.name)
        if self.state["view"] == "compare":
            self.refresh()
        else:
            self._update_fav_label()
            self.view.viewport().update()

    def toggle_hidden(self, e):
        hid = self.state["hidden"]
        if e.name in hid:
            hid.remove(e.name)
        else:
            hid.append(e.name)
        row = self.view.currentIndex().row()
        self.refresh()
        if not self.view.currentIndex().isValid() and self.model.rowCount():
            self.view.setCurrentIndex(
                self.model.index(min(row, self.model.rowCount() - 1)))

    def _update_fav_label(self):
        n = len(self.state["favorites"])
        b = self.view_seg.buttons["compare"]
        b.setText(f"Favorites  {n}" if n else "Favorites")
        b.setMinimumWidth(b.fontMetrics().horizontalAdvance(b.text()) + 34)

    def set_variant(self, e, label):
        self.state["overrides"][e.name] = label
        self.view.viewport().update()

    def cycle_style(self, e, d):
        cur = self.variant_for(e)
        i = next((k for k, v in enumerate(e.variants) if v is cur), 0)
        self.set_variant(e, e.variants[(i + d) % len(e.variants)].label)

    def style_menu(self, e, global_pos):
        menu = QMenu(self)
        cur = self.variant_for(e)
        for v in e.variants:
            a = menu.addAction(v.label)
            a.setCheckable(True)
            a.setChecked(v is cur)
            a.triggered.connect(lambda _=False, lbl=v.label:
                                self.set_variant(e, lbl))
        menu.exec(global_pos)

    def copy_name(self, e):
        v = self.variant_for(e)
        text = e.name if v.label == "Regular" else f"{e.name} {v.label}"
        QGuiApplication.clipboard().setText(text)

    def card_image(self, e, scale=2):
        size = self.cell_size
        w, h = max(size.width() - GAP, 300), max(size.height() - GAP, 120)
        img = QImage(int(w * scale), int(h * scale),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(self.state["bg"]))
        p = QPainter(img)
        p.scale(scale, scale)
        self.renderer.paint_card(p, QRectF(0, 0, w, h), e, border=False,
                                 radius=0, export=True,
                                 wrap=self.state["view"] == "grid")
        p.end()
        return img

    def copy_image(self, e):
        QGuiApplication.clipboard().setImage(self.card_image(e))

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        self.view.setCurrentIndex(idx)
        e = idx.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        fav = e.name in self.state["favorites"]
        menu.addAction("Remove from favorites" if fav else
                       "Add to favorites", "Space",
                       lambda: self.toggle_favorite(e))
        if len(e.variants) > 1:
            sub = menu.addMenu("Style")
            cur = self.variant_for(e)
            for v in e.variants:
                a = sub.addAction(v.label)
                a.setCheckable(True)
                a.setChecked(v is cur)
                a.triggered.connect(lambda _=False, lbl=v.label:
                                    self.set_variant(e, lbl))
        menu.addSeparator()
        menu.addAction("Copy name", "Ctrl+C", lambda: self.copy_name(e))
        menu.addAction("Copy as image", "Ctrl+Shift+C",
                       lambda: self.copy_image(e))
        menu.addSeparator()
        menu.addAction("Unhide font" if e.name in self.state["hidden"]
                       else "Hide font", "H", lambda: self.toggle_hidden(e))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    # ---------------------------------------------------- colors
    def _pick_color(self, which):
        c = QColorDialog.getColor(QColor(self.state[which]), self,
                                  "Text color" if which == "fg"
                                  else "Background color")
        if c.isValid():
            self.state[which] = c.name()
            self._colors_changed()

    def _swap_colors(self):
        self.state["fg"], self.state["bg"] = self.state["bg"], self.state["fg"]
        self._colors_changed()

    def _colors_changed(self):
        combo = [self.state["fg"], self.state["bg"]]
        sw = self.state["swatches"]
        if combo in sw:
            sw.remove(combo)
        sw.insert(0, combo)
        del sw[7:]
        self._update_color_buttons()
        self._rebuild_swatches()
        self.view.viewport().update()

    def _update_color_buttons(self):
        self.fg_btn.set_color(self.state["fg"])
        self.bg_btn.set_color(self.state["bg"])

    def _rebuild_swatches(self):
        while self.swatch_row.count():
            it = self.swatch_row.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for fg, bg in self.state["swatches"]:
            b = QPushButton("Aa")
            b.setObjectName("Swatch")
            b.setFixedSize(28, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(f"{fg} on {bg}")
            b.setStyleSheet(f"QPushButton#Swatch {{ background:{bg}; "
                            f"color:{fg}; }}")
            b.clicked.connect(lambda _=False, f=fg, g=bg:
                              self._apply_swatch(f, g))
            self.swatch_row.addWidget(b)
        self.swatch_row.addStretch(1)

    def _apply_swatch(self, fg, bg):
        self.state["fg"], self.state["bg"] = fg, bg
        self._update_color_buttons()
        self.view.viewport().update()

    # ---------------------------------------------------- export
    def export_png(self):
        favs = [self.by_name[n] for n in self.state["favorites"]
                if n in self.by_name]
        if not favs:
            QMessageBox.information(
                self, "Export shortlist",
                "Star a few fonts first (★ or Space) — your favorites "
                "become the shortlist you export.")
            return
        dlg = ExportDialog(self, favs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.state["export"] = dlg.cfg
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PNG", str(Path.home() / "font_shortlist.png"),
            "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        img = self.renderer.render_export(favs, dlg.cfg)
        if img.save(path, "PNG"):
            QMessageBox.information(self, "Export shortlist",
                                    f"Saved:\n{path}")
        else:
            QMessageBox.warning(self, "Export shortlist",
                                f"Could not save to:\n{path}")

    # ---------------------------------------------------- lifecycle
    def closeEvent(self, e):
        self.state["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        save_settings(self.state)
        super().closeEvent(e)


def theme_palette(t):
    pal = QPalette()
    R = QPalette.ColorRole
    for role, key in ((R.Window, "surface"), (R.WindowText, "text"),
                      (R.Base, "raised"), (R.AlternateBase, "surface"),
                      (R.Text, "text"), (R.Button, "raised"),
                      (R.ButtonText, "text"), (R.ToolTipBase, "surface"),
                      (R.ToolTipText, "text"), (R.Highlight, "accent"),
                      (R.HighlightedText, "on_accent"),
                      (R.PlaceholderText, "muted"), (R.Mid, "border"),
                      (R.Dark, "border"), (R.Light, "raised"),
                      (R.Midlight, "raised"), (R.Link, "accent")):
        pal.setColor(role, QColor(t[key]))
    for role in (R.Text, R.ButtonText, R.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["muted"]))
    return pal


def library_ok(lib: Library):
    return HAVE_FONTTOOLS and lib.scanned


# =========================================================== stylesheet
QSS = """
* { font-size: 10pt; }
QWidget { color: @text; }
QMainWindow { background: @base; }
QFrame#Sidebar { background: @surface; border-right: 1px solid @border; }
QFrame#Header { background: @surface; border-bottom: 1px solid @border; }
QSpinBox { background: @raised; border: 1px solid @border;
    border-radius: 6px; padding: 3px 6px; }
QLabel#Brand { font-size: 12pt; font-weight: 600; padding-left: 4px; }
QLabel#Section { color: @muted; font-size: 8pt; font-weight: 700;
    letter-spacing: 1px; padding-top: 14px; padding-bottom: 2px; }
QLabel#Field { color: @text; }
QLabel#Value { color: @muted; }
QLabel#Muted { color: @muted; }
QLabel#Empty { color: @muted; font-size: 11pt; }
QLabel#DialogTitle { font-size: 14pt; font-weight: 600; }
QLabel#ExportPreview { background: @base; border: 1px solid @border;
    border-radius: 10px; padding: 10px; }

QPlainTextEdit#Sample { background: @raised; border: 1px solid @border;
    border-radius: 10px; padding: 6px 8px; font-size: 13pt;
    selection-background-color: @accent; selection-color: @on_accent; }
QPlainTextEdit#Sample:focus { border: 1px solid @accent; }
QLineEdit { background: @raised; border: 1px solid @border;
    border-radius: 8px; padding: 6px 8px;
    selection-background-color: @accent; selection-color: @on_accent; }
QLineEdit:focus { border: 1px solid @accent; }
QLineEdit:disabled { color: @muted; }

QPushButton, QToolButton { background: @raised; border: 1px solid @border;
    border-radius: 8px; padding: 6px 12px; color: @text; }
QPushButton:hover, QToolButton:hover { border: 1px solid @border_hover; }
QPushButton:pressed { background: @seg_on; }
QPushButton:disabled { color: @muted; }
QPushButton#Primary { background: @accent; color: @on_accent; border: none;
    font-weight: 600; padding: 9px 14px; }
QPushButton#Primary:hover { background: @accent; border: none; }
QPushButton#ColorBtn { text-align: left; padding: 6px 10px;
    font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt; }
QPushButton#Swatch { border: 1px solid @border; border-radius: 6px;
    padding: 0; font-size: 9pt; font-weight: 600; }
QToolButton#Presets { padding: 9px 14px; }
QToolButton#Presets::menu-indicator { image: none; width: 0; }
QToolButton#Icon { background: transparent; border: none; padding: 6px;
    border-radius: 8px; }
QToolButton#Icon:hover { background: @raised; }

QFrame#Seg { background: @raised; border: 1px solid @border;
    border-radius: 9px; }
QFrame#Seg QPushButton { background: transparent; border: none;
    border-radius: 6px; padding: 5px 12px; color: @muted; }
QFrame#Seg QPushButton:hover { color: @text; }
QFrame#Seg QPushButton:checked { background: @seg_on; color: @text;
    font-weight: 600; }
QFrame#Seg QPushButton:disabled { color: @border_hover; }
QFrame#Seg QToolButton#StepBtn { background: transparent; border: none;
    border-radius: 6px; padding: 3px; }
QFrame#Seg QToolButton#StepBtn:hover { background: @seg_on; }
QLabel#StepVal { font-weight: 600; }

QComboBox { background: @raised; border: 1px solid @border;
    border-radius: 8px; padding: 6px 10px; min-height: 18px; }
QComboBox:hover { border: 1px solid @border_hover; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url(@chevron); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: @surface; border: 1px solid @border;
    border-radius: 8px; padding: 4px; outline: 0;
    selection-background-color: @seg_on; selection-color: @text; }

QSlider { min-height: 22px; }
QSlider::groove:horizontal { height: 4px; background: @border;
    border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 2px; }
QSlider::handle:horizontal { background: @text; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid @border_hover; background: @raised; }
QCheckBox::indicator:checked { background: @accent; border: 1px solid @accent;
    image: url(@check); }

QListView#Fonts { background: @base; border: none; outline: 0; }
QScrollBar:vertical { background: transparent; width: 10px;
    margin: 6px 2px 6px 2px; }
QScrollBar::handle:vertical { background: @scroll; border-radius: 3px;
    min-height: 40px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none; }

QMenu { background: @surface; border: 1px solid @border;
    border-radius: 10px; padding: 5px; }
QMenu::item { padding: 6px 28px 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: @seg_on; }
QMenu::separator { height: 1px; background: @border; margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; left: 6px; }
QToolTip { background: @surface; color: @text; border: 1px solid @border;
    border-radius: 6px; padding: 5px 8px; }
QDialog { background: @surface; }
"""


# =========================================================== entry point
def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    ui = QFont("Segoe UI Variable Text") if sys.platform == "win32" \
        else QFont(app.font())
    if sys.platform == "win32" and ui.family() not in QFontDatabase.families():
        ui = QFont("Segoe UI")
    ui.setPointSize(10)
    app.setFont(ui)

    library = Library()
    splash = None
    if HAVE_FONTTOOLS:
        first_run = not CACHE_PATH.exists()
        if first_run:
            splash = QLabel("Indexing your fonts…  (first launch only)")
            splash.setAlignment(Qt.AlignmentFlag.AlignCenter)
            splash.setStyleSheet("background:#1a1b20;color:#ececef;"
                                 "padding:28px 40px;font-size:11pt;")
            splash.setWindowFlags(Qt.WindowType.SplashScreen
                                  | Qt.WindowType.FramelessWindowHint)
            splash.show()
            app.processEvents()

        def progress(i, n):
            if splash:
                splash.setText(f"Indexing your fonts…  {i} / {n}\n"
                               "(first launch only)")
                app.processEvents()
        try:
            library.scan(progress)
        except Exception as exc:  # never block the app over the index
            library.error = str(exc)
            library.scanned = False
    entries = build_entries(library)
    win = MainWindow(library, entries)
    win.show()
    if splash:
        splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
