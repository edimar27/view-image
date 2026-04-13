"""
Ed Image Preview — visualizador de imagens com carrossel (PageView) e faixa de miniaturas.

Tipografia da tarja EXIF: Inter em assets/fonts/ (SIL OFL — ver LICENSE.txt).

Execução local (macOS):
  pip install -r requirements.txt
  ./run.sh

Se vir «SSL: CERTIFICATE_VERIFY_FAILED» ao usar «flet run» diretamente, use ./run.sh
(exporta SSL_CERT_FILE com o certifi) ou execute uma vez:
  /Applications/Python 3.12/Install Certificates.command

Build iOS / macOS (requer Xcode e ferramentas Flet):
  ./build.sh macos          # usa certifi no SSL (evita erro ao descarregar Flutter)
  flet build ipa
Veja: https://flet.dev/docs/publish
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from dataclasses import dataclass

import flet as ft
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from i18n import init_app_i18n, tr

IMAGE_EXTENSIONS = frozenset(
    ".jpg .jpeg .png .gif .webp .bmp .tiff .tif .heic .heif".split()
)


def _local_path_for_read(path: Path | str) -> str:
    """Caminho local para Pillow; trata file:// (macOS/Windows) e expande ~."""
    s = os.fsdecode(path) if isinstance(path, bytes) else str(path).strip()
    if s.startswith("file:"):
        try:
            parsed = urlparse(s)
            raw = url2pathname(parsed.path) if parsed.path else s
            if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
                raw = raw.lstrip("/")
            return os.path.expanduser(raw)
        except Exception:
            return os.path.expanduser(s)
    return os.path.expanduser(s)


def _flet_image_display_src(path: str) -> str:
    """Caminho absoluto normalizado para `ft.Image(src=…)` (desktop)."""
    s = (path or "").strip()
    if not s:
        return s
    if s.startswith(("http://", "https://", "data:", "file:")):
        return s
    fs = _local_path_for_read(s)
    try:
        p = Path(fs).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return str(p.resolve(strict=False))
    except (OSError, ValueError, RuntimeError):
        return os.path.abspath(fs)


def _ratio_to_float(val: object) -> float | None:
    if val is None:
        return None
    if hasattr(val, "numerator") and hasattr(val, "denominator"):
        try:
            b = int(val.denominator)
            if b == 0:
                return None
            return float(val.numerator) / float(b)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if isinstance(val, tuple) and len(val) == 2:
        a, b = val[0], val[1]
        if b in (0, 0.0):
            return None
        return float(a) / float(b)
    if isinstance(val, (int, float)):
        return float(val)
    return None


_EXIF_DIAL_TOKEN_RE = re.compile(r"\b([UC][123])\b", re.IGNORECASE)

# CIPA ExposureProgram (Exif.Photo.ExposureProgram): códigos de modo de disparo.
_EXPOSURE_PROGRAM_PSAM: dict[int, str] = {
    1: "M",
    2: "P",
    3: "A",
    4: "S",
    5: "P+",
    6: "P*",
    7: "Portrait",
    8: "Landscape",
}

_SCENE_CAPTURE_LABEL: dict[int, str] = {
    0: "Standard",
    1: "Landscape",
    2: "Portrait",
    3: "Night",
    4: "Night portrait",
}


def _exif_int_scalar(val: object) -> int | None:
    """Inteiro EXIF (IFDRational, tuplo, str dígitos)."""
    if val is None or isinstance(val, bool):
        return None
    r = _ratio_to_float(val)
    if r is not None:
        try:
            ir = int(round(r))
            if abs(r - float(ir)) < 1e-5:
                return ir
        except (TypeError, ValueError, OverflowError):
            pass
    if isinstance(val, int):
        return val
    if isinstance(val, tuple) and val:
        return _exif_int_scalar(val[0])
    if isinstance(val, (bytes, memoryview)):
        return None
    s = str(val).strip()
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _psam_from_exposure_program_text(s: str) -> str | None:
    sl = s.lower().replace("_", " ")
    if "manual" in sl or sl.strip() in ("m", "manual control"):
        return "M"
    if "bulb" in sl:
        return "B"
    if "aperture" in sl or " av" in sl or sl.strip() in ("av", "a"):
        return "A"
    if "shutter" in sl or "time priority" in sl or sl.strip() in ("tv", "s"):
        return "S"
    if "program ae" in sl or "normal program" in sl or sl == "program":
        return "P"
    if "creative" in sl and "action" not in sl:
        return "P+"
    if "action" in sl or "sports" in sl:
        return "P*"
    if "portrait" in sl:
        return "Portrait"
    if "landscape" in sl:
        return "Landscape"
    return None


def _psam_from_exposure_program_value(val: object) -> str | None:
    n = _exif_int_scalar(val)
    if n is not None and n > 0:
        short = _EXPOSURE_PROGRAM_PSAM.get(n)
        if short:
            return short
    if val is None:
        return None
    s = _exif_tag_string(val)
    if s:
        return _psam_from_exposure_program_text(s)
    return None


def _extract_dial_tokens_from_text(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _EXIF_DIAL_TOKEN_RE.finditer(text):
        tok = m.group(1).upper()
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _scan_merged_for_dial_tokens(merged: dict[str, object]) -> list[str]:
    """U1/U2/U3/C1/C2/C3 em comentários ou Makernote legível (ex. Canon)."""
    found: list[str] = []
    seen: set[str] = set()
    for key in ("UserComment", "ImageDescription", "Artist"):
        t = _exif_tag_string(merged.get(key, ""))
        for tok in _extract_dial_tokens_from_text(t):
            if tok not in seen:
                seen.add(tok)
                found.append(tok)
    for key, val in merged.items():
        if not key.startswith("Maker_"):
            continue
        s = _exif_value_short(val, 120)
        if not s or s.startswith("<binário"):
            continue
        for tok in _extract_dial_tokens_from_text(s):
            if tok not in seen:
                seen.add(tok)
                found.append(tok)
    return found


def _scene_capture_label(val: object) -> str | None:
    n = _exif_int_scalar(val)
    if n is not None:
        lab = _SCENE_CAPTURE_LABEL.get(n)
        if lab and lab != "Standard":
            return lab
        return None
    s = _exif_tag_string(val)
    if not s:
        return None
    sl = s.lower()
    if sl in ("standard", "0"):
        return None
    return _exif_value_short(val, 24)


def _capture_mode_description(merged: dict[str, object]) -> str | None:
    """Resumo de modo de captura: dial U/C + PSAM (M, A, …) + cena / bracketing."""
    parts: list[str] = []
    for tok in _scan_merged_for_dial_tokens(merged):
        parts.append(tok)
    psam = _psam_from_exposure_program_value(merged.get("ExposureProgram"))
    if psam:
        parts.append(psam)
    else:
        ep = merged.get("ExposureProgram")
        if ep is not None and str(ep).strip():
            raw = _exif_value_short(ep, 40)
            if raw:
                parts.append(raw)
    em = _exif_int_scalar(merged.get("ExposureMode"))
    if em == 2:
        parts.append("AEB")
    sct = _scene_capture_label(merged.get("SceneCaptureType"))
    if sct and (not psam or sct.lower() != psam.lower()) and sct not in parts:
        parts.append(sct)
    if not parts:
        return None
    dedup: list[str] = []
    seen2: set[str] = set()
    for p in parts:
        if p not in seen2:
            seen2.add(p)
            dedup.append(p)
    return " · ".join(dedup)


def _format_exposure_seconds(val: object) -> str | None:
    sec = _ratio_to_float(val)
    if sec is None or sec <= 0:
        return None
    if sec >= 1:
        return f"{sec:.1f}s".replace(".0s", "s")
    inv = round(1.0 / sec)
    return f"1/{inv}s"


def _exif_value_short(val: object, max_len: int = 120) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        if len(val) > max_len:
            return f"<binário {len(val)} B>"
        try:
            return val.decode("utf-8", errors="replace").strip()
        except Exception:
            return f"<binário {len(val)} B>"
    r = _ratio_to_float(val)
    if r is not None and not isinstance(val, (str, bytes)):
        if isinstance(val, tuple) and len(val) == 2:
            return f"{val[0]}/{val[1]}"
        if abs(r - round(r)) < 1e-6:
            return str(int(round(r)))
        return f"{r:.4g}".rstrip("0").rstrip(".")
    s = str(val).strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


# Tags pouco úteis para o rodapé (DPI, estrutura TIFF/JPEG, apontadores).
_BORING_EXIF_NAMES = frozenset(
    {
        "ResolutionUnit",
        "XResolution",
        "YResolution",
        "ExifOffset",
        "GPSInfo",
        "JPEGInterchangeFormat",
        "JPEGInterchangeFormatLength",
        "RowsPerStrip",
        "StripOffsets",
        "StripByteCounts",
        "TileWidth",
        "TileLength",
        "TileOffsets",
        "TileByteCounts",
        "ImageWidth",
        "ImageLength",
        "BitsPerSample",
        "SamplesPerPixel",
        "Compression",
        "PhotometricInterpretation",
        "PlanarConfiguration",
        "Orientation",
        "ExifVersion",
        "FlashpixVersion",
        "ComponentsConfiguration",
        "YCbCrSubSampling",
        "YCbCrPositioning",
        "ReferenceBlackWhite",
        "TransferFunction",
        "WhitePoint",
        "PrimaryChromaticities",
    }
)

# Se alguma destas existir, há informação “de fotografia” além de DPI.
_PHOTO_EXIF_ANY_OF = frozenset(
    {
        "DateTimeOriginal",
        "DateTime",
        "Make",
        "Model",
        "LensMake",
        "LensModel",
        "LensSpecification",
        "FNumber",
        "ExposureTime",
        "ShutterSpeedValue",
        "ApertureValue",
        "BrightnessValue",
        "ExposureBiasValue",
        "ISOSpeedRatings",
        "PhotographicSensitivity",
        "FocalLength",
        "FocalLengthIn35mmFilm",
        "SubjectDistance",
        "Flash",
        "ExposureProgram",
        "ExposureMode",
        "MeteringMode",
        "WhiteBalance",
        "SceneCaptureType",
        "CustomRendered",
        "DigitalZoomRatio",
        "Artist",
        "Copyright",
    }
)


def _append_pillow_exif_to_merged(exif: object, merged: dict[str, object]) -> None:
    """Junta IFD Exif, GPS e IFD0 de um objeto PIL.Image.Exif ao dicionário merged."""
    from PIL.ExifTags import GPSTAGS, IFD, TAGS

    def merge_ifd(tag_ids: object, prefix: str = "") -> None:
        try:
            sub = exif.get_ifd(tag_ids)
        except (KeyError, ValueError, TypeError):
            return
        for k, v in sub.items():
            if isinstance(v, bytes) and len(v) > 512:
                continue
            if prefix:
                name = GPSTAGS.get(k, f"0x{k:02X}")
                merged[f"{prefix}{name}"] = v
            else:
                name = TAGS.get(k) or f"Tag_0x{k:04X}"
                merged[name] = v

    merge_ifd(IFD.Exif)
    merge_ifd(IFD.GPSInfo, "GPS.")
    try:
        mk = exif.get_ifd(IFD.Makernote)
    except (KeyError, ValueError, TypeError):
        mk = {}
    for mk_k, mk_v in mk.items():
        if isinstance(mk_v, bytes) and len(mk_v) > 512:
            continue
        merged[f"Maker_{mk_k:04X}"] = mk_v
    for k in exif:
        if k in (IFD.Exif, IFD.GPSInfo):
            continue
        v = exif[k]
        if isinstance(v, bytes) and len(v) > 512:
            continue
        name = TAGS.get(k) or f"Tag_0x{k:04X}"
        merged[name] = v


def _load_merged_exif_from_path(path: Path | str) -> dict[str, object] | None:
    """Carrega EXIF unificado; None se ficheiro/Pillow inválido; {} se sem tags."""
    try:
        from PIL import Image
    except ImportError:
        return None

    local = _local_path_for_read(path)
    merged: dict[str, object] = {}
    try:
        with Image.open(local) as im:
            _append_pillow_exif_to_merged(im.getexif(), merged)
            raw_exif = im.info.get("exif")
            if isinstance(raw_exif, bytes) and len(raw_exif) > 8:
                try:
                    ex2 = Image.Exif()
                    ex2.load(raw_exif)
                    _append_pillow_exif_to_merged(ex2, merged)
                except Exception:
                    pass
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return merged


def _exif_tag_string(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(val).strip()


# Coloque PNGs em assets/camera_logos/ (ex.: canon.png). Nomes de ficheiro abaixo.
_ASSETS_CAMERA_LOGOS = Path(__file__).resolve().parent / "assets" / "camera_logos"
# Inter (SIL OFL) — ficheiros em assets/fonts/; licença: assets/fonts/LICENSE.txt
_ASSET_FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
INTER_FONT_REGULAR = _ASSET_FONTS_DIR / "Inter-Regular.ttf"
INTER_FONT_MEDIUM = _ASSET_FONTS_DIR / "Inter-Medium.ttf"
INTER_FONT_SEMIBOLD = _ASSET_FONTS_DIR / "Inter-SemiBold.ttf"
_CAMERA_MAKE_LOGO_FILES: dict[str, str] = {
    "om digital solutions": "om_system.png",
    "om system": "om_system.png",
    "fujifilm": "fujifilm.png",
    "hasselblad": "hasselblad.png",
    "blackmagic": "blackmagic.png",
    "panasonic": "panasonic.png",
    "motorola": "motorola.png",
    "microsoft": "microsoft.png",
    "polaroid": "polaroid.png",
    "pentax": "pentax.png",
    "leica": "leica.png",
    "lumix": "panasonic.png",
    "nikon": "nikon.png",
    "canon": "canon.png",
    "sony": "sony.png",
    "olympus": "olympus.png",
    "apple": "apple.png",
    "samsung": "samsung.png",
    "google": "google.png",
    "gopro": "gopro.png",
    "dji": "dji.png",
    "sigma": "sigma.png",
    "tamron": "tamron.png",
    "zenit": "zenit.png",
    "kodak": "kodak.png",
    "huawei": "huawei.png",
    "xiaomi": "xiaomi.png",
    "oneplus": "oneplus.png",
    "oppo": "oppo.png",
    "vivo": "vivo.png",
    "realme": "realme.png",
    "nokia": "nokia.png",
    "ricoh": "ricoh.png",
    "red": "red.png",
    "zeiss": "zeiss.png",
    "minolta": "minolta.png",
    "konica": "konica.png",
    "lg electronics": "lg.png",
}


def _camera_brand_logo_path(make_raw: object) -> Path | None:
    ms = _exif_tag_string(make_raw)
    if not ms:
        return None
    low = ms.lower()
    for key, fname in sorted(_CAMERA_MAKE_LOGO_FILES.items(), key=lambda kv: -len(kv[0])):
        if key in low:
            p = _ASSETS_CAMERA_LOGOS / fname
            if p.is_file():
                return p
    return None


def _camera_brand_badge_widget(make_raw: object) -> ft.Control | None:
    """Logótipo PNG em assets/camera_logos/ ou etiqueta com o nome da marca (EXIF Make)."""
    ms = _exif_tag_string(make_raw)
    if not ms:
        return None
    logo_path = _camera_brand_logo_path(make_raw)
    if logo_path is not None:
        return ft.Container(
            padding=ft.Padding.all(10),
            bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
            border_radius=ft.BorderRadius.all(8),
            content=ft.Image(
                src=_flet_image_display_src(str(logo_path)),
                width=52,
                height=52,
                fit=ft.BoxFit.CONTAIN,
                filter_quality=ft.FilterQuality.MEDIUM,
            ),
        )
    short = ms.split()[0] if ms else ""
    if len(short) > 18:
        short = short[:17] + "…"
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        bgcolor=ft.Colors.with_opacity(0.55, ft.Colors.BLACK),
        border_radius=ft.BorderRadius.all(6),
        content=ft.Text(
            short.upper(),
            size=10,
            weight=ft.FontWeight.W_600,
            color=ft.Colors.WHITE,
        ),
    )


_MAX_EXIF_OVERLAY_PAIRS = 14

_EXIF_STRIP_APP_CREDIT = "Software desenvolvido por ©Edimar Barbosa"
_EXIF_STRIP_INSTAGRAM_HANDLE = "@b_edimar"
_ASSET_INSTAGRAM_GLYPH = Path(__file__).resolve().parent / "assets" / "social" / "instagram_glyph.png"


@dataclass
class ExifDisplayFilter:
    """Quais campos opcionais mostrar na tarja EXIF (ecrã e «Guardar como»)."""

    show_software: bool = True
    show_date: bool = True
    show_mode: bool = True


# Largura da tarja em % da imagem / área do slide; posição 0..1 (nx, ny) = canto sup. esq.
EXIF_STRIP_WIDTH_FRAC = 0.92
EXIF_STRIP_W_FRAC_MIN = 0.32
EXIF_STRIP_W_FRAC_MAX = 0.98
EXIF_STRIP_V_W_SCALE_MIN = 0.58
EXIF_STRIP_V_W_SCALE_MAX = 1.42
EXIF_VIEW_H_HORIZ = 158
EXIF_VIEW_H_VERT = 520

# Arrasto da tarja: posição = nx/ny normalizados (sem «pin» ao fundo/topo — isso congelava
# a posição vertical num intervalo de ny e parecia um íman).
EXIF_STRIP_DRAG_SNAP_Y = 0.0
EXIF_STRIP_DRAG_SNAP_X = 0.05
EXIF_STRIP_DRAG_EDGE_BOOST = 0.0
EXIF_STRIP_DRAG_BOOST_EDGE = 0.98
# Tarja EXIF: cantos (ecrã e exportação Pillow); cor = ecrã (#252a34).
EXIF_STRIP_CORNER_RADIUS = 12
EXIF_STRIP_PANEL_HEX = "#252a34"
EXIF_STRIP_PANEL_RGB = (37, 42, 52)
# Tamanhos lógicos da tarja no ecrã (exportação escala por letterbox).
EXIF_STRIP_UI_BODY_PT = 11
EXIF_STRIP_UI_CREDIT_PT = 9
EXIF_STRIP_UI_PAD_H = 14
EXIF_STRIP_UI_PAD_V = 11


def _exif_strip_typography() -> tuple[
    tuple[str, list[str]],
    tuple[str, list[str]],
    tuple[str, list[str]],
]:
    """(rótulo, valor, crédito) → (font_family, font_family_fallback) para a tarja EXIF."""
    fb_sys = [".SF Pro Text", "SF Pro Text", "Helvetica Neue", "Segoe UI", "Arial"]
    if INTER_FONT_MEDIUM.is_file() and INTER_FONT_SEMIBOLD.is_file():
        return (
            ("Inter Medium", ["Inter", "Inter SemiBold", *fb_sys]),
            ("Inter SemiBold", ["Inter", "Inter Medium", *fb_sys]),
            (
                "Inter" if INTER_FONT_REGULAR.is_file() else "Inter Medium",
                ["Inter Medium", "Inter SemiBold", *fb_sys],
            ),
        )
    if INTER_FONT_REGULAR.is_file():
        t = ("Inter", fb_sys)
        return t, t, t
    if sys.platform == "darwin":
        t = (".SF Pro Text", ["SF Pro Text", "Helvetica Neue", "Arial"])
        return t, t, t
    if sys.platform == "win32":
        t = ("Segoe UI Variable", ["Segoe UI", "Calibri", "Arial"])
        return t, t, t
    t = ("Ubuntu", ["Roboto", "DejaVu Sans", "Liberation Sans", "Arial"])
    return t, t, t


def _pillow_exif_stroke_width(font_px: int) -> int:
    """Contorno mínimo no texto sobre fundo escuro (evita halo grosso do Arial)."""
    return max(0, min(1, font_px // 48))

EXIF_PLACEMENT_KEYS = frozenset(
    {
        "auto",
        "h_bottom",
        "h_bottom_left",
        "h_bottom_right",
        "h_top",
        "h_top_left",
        "h_top_right",
        "h_center",
        "v_left",
        "v_left_bottom",
        "v_left_top",
        "v_right",
        "v_right_bottom",
        "v_right_top",
    }
)


def _exif_compose_branch(placement: str, w: int, h: int) -> str:
    """Família de desenho Pillow: v_left, v_right ou h_bottom (tarja horizontal)."""
    pl = (placement or "auto").strip().lower()
    if pl not in EXIF_PLACEMENT_KEYS:
        pl = "auto"
    if pl == "auto":
        return "v_left" if h > w else "h_bottom"
    if pl.startswith("v_"):
        return "v_right" if "right" in pl else "v_left"
    return "h_bottom"


def _exif_strip_rect_on_image(
    w: int,
    h: int,
    bar_h: int,
    nx: float,
    ny: float,
    width_frac: float,
) -> tuple[int, int, int, int]:
    """Retorna (strip_x, strip_y, strip_w, bar_h) em pixels sobre imagem w x h."""
    wf = max(0.38, min(1.0, float(width_frac)))
    strip_w = max(80, min(w, int(w * wf)))
    bh = min(max(8, bar_h), max(8, h - 2))
    strip_x = int(round(max(0.0, min(1.0, float(nx))) * max(0, w - strip_w)))
    strip_y = int(round(max(0.0, min(1.0, float(ny))) * max(0, h - bh)))
    strip_y = min(strip_y, max(0, h - bh))
    strip_x = min(strip_x, max(0, w - strip_w))
    return strip_x, strip_y, strip_w, bh


def _exif_strip_rect_vertical(
    w: int,
    h: int,
    bar_w: int,
    bar_h: int,
    nx: float,
    ny: float,
) -> tuple[int, int, int, int]:
    """Painel estreito vertical: (sx, sy, strip_w, bar_h)."""
    bw = max(60, min(bar_w, w - 2))
    bh = min(max(8, bar_h), max(8, h - 2))
    strip_x = int(round(max(0.0, min(1.0, float(nx))) * max(0, w - bw)))
    strip_y = int(round(max(0.0, min(1.0, float(ny))) * max(0, h - bh)))
    strip_y = min(strip_y, max(0, h - bh))
    strip_x = min(strip_x, max(0, w - bw))
    return strip_x, strip_y, bw, bh


def _letterbox_contain(
    iw: int, ih: int, pw: float, ph: float
) -> tuple[float, float, float, float, float]:
    """Imagem iw×ih centrada em viewport pw×ph (BoxFit.CONTAIN).
    Devolve (ox, oy, disp_w, disp_h, scale) com scale = disp_w/iw."""
    if iw <= 0 or ih <= 0 or pw <= 0 or ph <= 0:
        return 0.0, 0.0, max(1.0, pw), max(1.0, ph), max(1.0, pw) / max(1, iw)
    s = min(pw / iw, ph / ih)
    dw, dh = iw * s, ih * s
    ox = (pw - dw) / 2.0
    oy = (ph - dh) / 2.0
    return ox, oy, dw, dh, s


def _effective_exif_placement_ui(iw: int, ih: int, placement: str) -> str:
    """Disposição efectiva no ecrã (incl. «auto» pela proporção da foto)."""
    raw = (placement or "auto").strip().lower()
    if raw not in EXIF_PLACEMENT_KEYS:
        raw = "auto"
    if raw == "auto":
        return "v_left" if ih > iw else "h_bottom"
    return raw


def _viewport_exif_strip_free_y(
    iw: int,
    ih: int,
    pw: float,
    ph: float,
    ek: str,
    strip_h: float,
) -> float:
    """Comprimento útil em Y para ny (tarja horizontal): até ao fundo da imagem em CONTAIN."""
    ph_f = float(ph)
    stack_free = max(0.0, ph_f - strip_h)
    if ek.startswith("v_"):
        return max(1.0, stack_free)
    if iw <= 0 or ih <= 0:
        return max(1.0, stack_free)
    ox, oy, dw, dh, _ = _letterbox_contain(iw, ih, pw, ph_f)
    img_bot = oy + dh
    img_free = max(0.0, img_bot - strip_h)
    if img_free < 1.0:
        return max(1.0, stack_free)
    return max(1.0, min(stack_free, img_free))


def _viewport_exif_strip_rect(
    nx: float,
    ny: float,
    pw: float,
    ph: float,
    ek: str,
    *,
    image_wh: tuple[int, int] | None = None,
    width_frac: float = EXIF_STRIP_WIDTH_FRAC,
    v_strip_width_scale: float = 1.0,
) -> tuple[float, float, float, float]:
    """(left, top, strip_w, strip_h) no slide — espelha _apply_exif_strip_positions."""
    ph_f = float(ph)
    nxf = max(0.0, min(1.0, float(nx)))
    nyf = max(0.0, min(1.0, float(ny)))
    wf = max(EXIF_STRIP_W_FRAC_MIN, min(EXIF_STRIP_W_FRAC_MAX, float(width_frac)))
    vs = max(EXIF_STRIP_V_W_SCALE_MIN, min(EXIF_STRIP_V_W_SCALE_MAX, float(v_strip_width_scale)))
    if ek.startswith("v_"):
        strip_w = min(300.0, float(pw) * 0.42) * vs
        strip_w = max(72.0, min(float(pw) - 6.0, strip_w))
        strip_h_e = min(ph_f * 0.90, float(EXIF_VIEW_H_VERT))
    else:
        strip_w = max(120.0, min(float(pw), float(pw) * wf))
        strip_h_e = float(EXIF_VIEW_H_HORIZ)
    strip_h_e = max(48.0, min(strip_h_e, ph_f - 2.0))
    sx = nxf * max(0.0, float(pw) - strip_w)
    if (
        not ek.startswith("v_")
        and image_wh is not None
        and image_wh[0] > 0
        and image_wh[1] > 0
    ):
        free_y = _viewport_exif_strip_free_y(
            image_wh[0], image_wh[1], pw, ph_f, ek, strip_h_e
        )
        sy = nyf * free_y
    else:
        sy = nyf * max(0.0, ph_f - strip_h_e)
    return sx, sy, strip_w, strip_h_e


def _export_map_strip_norms_to_image(
    iw: int,
    ih: int,
    viewport_pw: int | None,
    viewport_ph: int | None,
    nx_ui: float,
    ny_ui: float,
    placement: str,
    strip_w_pil: int,
    bar_h_pil: int,
    *,
    strip_width_frac: float = EXIF_STRIP_WIDTH_FRAC,
    strip_v_w_scale: float = 1.0,
) -> tuple[float, float]:
    """Projecta o centro da tarja do viewport (pré-visualização) para nx/ny na bitmap."""
    if viewport_pw is None or viewport_ph is None:
        return nx_ui, ny_ui
    pw = float(viewport_pw)
    ph = float(viewport_ph)
    ek = _effective_exif_placement_ui(iw, ih, placement)
    ox, oy, dw, dh, _ = _letterbox_contain(iw, ih, pw, ph)
    if dw < 2 or dh < 2:
        return nx_ui, ny_ui
    left, top, sw_v, sh_v = _viewport_exif_strip_rect(
        nx_ui,
        ny_ui,
        pw,
        ph,
        ek,
        image_wh=(iw, ih),
        width_frac=strip_width_frac,
        v_strip_width_scale=strip_v_w_scale,
    )
    cx_v = left + sw_v / 2.0
    cy_v = top + sh_v / 2.0
    u = (cx_v - ox) / dw
    v = (cy_v - oy) / dh
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    cix = u * float(iw)
    ciy = v * float(ih)
    half_w = float(strip_w_pil) / 2.0
    half_h = float(bar_h_pil) / 2.0
    cix = max(half_w, min(cix, float(iw) - half_w))
    ciy = max(half_h, min(ciy, float(ih) - half_h))
    denom_x = max(1.0, float(iw) - strip_w_pil)
    denom_y = max(1.0, float(ih) - bar_h_pil)
    return (
        max(0.0, min(1.0, (cix - half_w) / denom_x)),
        max(0.0, min(1.0, (ciy - half_h) / denom_y)),
    )


def _viewport_strip_intrinsic_height(
    ek: str, pairs: list[tuple[str, str]], ph: float
) -> float:
    """Altura útil estimada da Column EXIF no ecrã (px), para alinhar export à pré-visualização."""
    ph_f = max(120.0, float(ph))
    pad = 22.0
    lh = 16.5
    cr = 36.0
    if ek.startswith("v_"):
        lines = len(pairs) + 1
        est = pad + float(lines) * lh + cr
        return float(min(max(est, 120.0), ph_f - 2.0, float(EXIF_VIEW_H_VERT)))
    left, right = _split_exif_pairs_two_columns(pairs)
    rows = max(len(left), len(right), 1)
    est = pad + float(rows) * lh + cr
    return float(min(max(est, float(EXIF_VIEW_H_HORIZ)), ph_f - 2.0))


def _export_strip_geometry_pixels(
    iw: int,
    ih: int,
    viewport_pw: int | None,
    viewport_ph: int | None,
    nx_ui: float,
    ny_ui: float,
    placement: str,
    pairs: list[tuple[str, str]],
    *,
    strip_width_frac: float = EXIF_STRIP_WIDTH_FRAC,
    strip_v_w_scale: float = 1.0,
) -> tuple[int, int, int, int, float] | None:
    """(sx, sy, strip_w, strip_h, scale_vp_per_img) na bitmap; None = export legado sem viewport."""
    if viewport_pw is None or viewport_ph is None or iw <= 0 or ih <= 0:
        return None
    pw, ph = float(viewport_pw), float(viewport_ph)
    ox, oy, dw, dh, s = _letterbox_contain(iw, ih, pw, ph)
    if s <= 1e-12 or dw < 2 or dh < 2:
        return None
    ek = _effective_exif_placement_ui(iw, ih, placement)
    left, top, sw_v, sh_v = _viewport_exif_strip_rect(
        nx_ui,
        ny_ui,
        pw,
        ph,
        ek,
        image_wh=(iw, ih),
        width_frac=strip_width_frac,
        v_strip_width_scale=strip_v_w_scale,
    )
    sh_use = max(float(sh_v), _viewport_strip_intrinsic_height(ek, pairs, ph))
    sh_use = min(sh_use, ph - 2.0)
    sw_img = max(1, min(iw, int(round(sw_v / s))))
    sh_img = max(1, min(ih, int(round(sh_use / s))))
    img_x = int(round((left - ox) / s))
    img_y = int(round((top - oy) / s))
    img_x = max(0, min(img_x, iw - sw_img))
    img_y = max(0, min(img_y, ih - sh_img))
    return img_x, img_y, sw_img, sh_img, float(s)


def _pillow_blend_dark_rect(
    out_rgb,
    sx: int,
    sy: int,
    sw: int,
    sh: int,
    opacity: float,
    *,
    corner_radius: int = EXIF_STRIP_CORNER_RADIUS,
    panel_rgb: tuple[int, int, int] = EXIF_STRIP_PANEL_RGB,
) -> None:
    """Sobrepor tarja semi-opaca com cantos suavizados (supersampling) e cor unificada ao ecrã."""
    from PIL import Image, ImageDraw

    op = max(0.0, min(1.0, float(opacity)))
    if op < 0.02:
        return
    w_img, h_img = out_rgb.size
    sx = max(0, min(sx, w_img - 1))
    sy = max(0, min(sy, h_img - 1))
    sw = max(1, min(sw, w_img - sx))
    sh = max(1, min(sh, h_img - sy))
    a = int(round(255 * op))
    pr, pg, pb = panel_rgb
    base_r = max(int(corner_radius), int(min(sw, sh) * 0.04))
    r = min(base_r, sw // 2, sh // 2)
    ss = 4
    lw, lh = sw * ss, sh * ss
    rr = min(max(1, r * ss), lw // 2 - 1, lh // 2 - 1)
    big = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, lw, lh), radius=rr, fill=(pr, pg, pb, a)
    )
    layer = big.resize((sw, sh), Image.Resampling.LANCZOS)
    crop = out_rgb.crop((sx, sy, sx + sw, sy + sh)).convert("RGBA")
    blended = Image.alpha_composite(crop, layer)
    out_rgb.paste(blended.convert("RGB"), (sx, sy))


def _build_exif_overlay_pairs_from_merged(
    merged: dict[str, object],
    *,
    exif_filter: ExifDisplayFilter | None = None,
) -> list[tuple[str, str]]:
    """Lista de (rótulo, valor) para o rodapé; até _MAX_EXIF_OVERLAY_PAIRS entradas."""
    if not merged:
        return [("EXIF", "Sem metadados")]

    rows: list[tuple[str, str]] = []

    fn = _ratio_to_float(merged.get("FNumber"))
    if fn is not None and fn > 0:
        fs = f"{fn:.2f}".rstrip("0").rstrip(".")
        rows.append(("Aperture", f"f/{fs}"))

    fl = _ratio_to_float(merged.get("FocalLength"))
    if fl is not None and fl > 0:
        fl_bits = [f"{fl:g} mm"]
        fl35 = merged.get("FocalLengthIn35mmFilm")
        if fl35 is not None and str(fl35).strip():
            fl_bits.append(f"eq.35mm {fl35}")
        sd_m = _ratio_to_float(merged.get("SubjectDistance"))
        if sd_m is not None and 0.01 < sd_m < 1_000_000:
            fl_bits.append(f"focus {sd_m:g}m")
        rows.append(("Focal length", " · ".join(fl_bits)))

    lens_make = _exif_tag_string(merged.get("LensMake", ""))
    lens_model = _exif_tag_string(merged.get("LensModel", ""))
    lens_spec = merged.get("LensSpecification")
    lens_line = " ".join(x for x in (lens_make, lens_model) if x).strip()
    if not lens_line and lens_spec is not None:
        lens_line = _exif_value_short(lens_spec, 80)
    if lens_line:
        rows.append(("Lens", lens_line))

    make, model = _exif_tag_string(merged.get("Make", "")), _exif_tag_string(
        merged.get("Model", "")
    )
    cam = " ".join(x for x in (make, model) if x)
    if cam:
        rows.append(("Camera", cam))

    artist = _exif_tag_string(merged.get("Artist", ""))
    copyright_ = _exif_tag_string(merged.get("Copyright", ""))
    if artist and copyright_:
        rows.append(("Owner", artist[:120]))
        if copyright_ != artist:
            rows.append(("Copyright", copyright_[:120]))
    elif artist:
        rows.append(("Owner", artist[:120]))
    elif copyright_:
        rows.append(("Owner", copyright_[:120]))

    exp = _format_exposure_seconds(merged.get("ExposureTime"))
    if exp:
        rows.append(("Speed", exp))

    iso = merged.get("PhotographicSensitivity")
    if iso is None:
        iso = merged.get("ISOSpeedRatings")
    if isinstance(iso, tuple) and iso:
        iso = iso[0]
    if iso is not None and str(iso).strip():
        rows.append(("ISO", str(iso)))

    xf = exif_filter or ExifDisplayFilter()

    dt = merged.get("DateTimeOriginal") or merged.get("DateTime")
    if dt and xf.show_date:
        rows.append(("Date", _exif_tag_string(dt)[:36]))

    sw = merged.get("Software")
    if sw and xf.show_software:
        rows.append(("Software", _exif_tag_string(sw)[:72]))

    ori = merged.get("Orientation")
    if ori is not None:
        ol = _orientation_label(ori)
        rows.append(("Orientation", ol if ol else str(ori)))

    flash = merged.get("Flash")
    if flash is not None and str(flash).strip():
        rows.append(("Flash", str(flash)))

    if xf.show_mode:
        mode_line = _capture_mode_description(merged)
        if mode_line:
            rows.append(("Mode", mode_line))

    mm = merged.get("MeteringMode")
    if mm is not None and str(mm).strip():
        rows.append(("Metering", str(mm)))

    wb = merged.get("WhiteBalance")
    if wb is not None and str(wb).strip():
        rows.append(("White balance", str(wb)))

    has_photo_meta = bool(_PHOTO_EXIF_ANY_OF & merged.keys()) or any(
        k.startswith("GPS.") for k in merged
    )

    if not rows:
        if not has_photo_meta:
            return [("EXIF", "Sem dados de câmara (só DPI)")]
        return [("EXIF", "Sem campos úteis")]

    if len(rows) < _MAX_EXIF_OVERLAY_PAIRS:
        _fb_order = (
            "Make",
            "Model",
            "LensModel",
            "FNumber",
            "ExposureTime",
            "ShutterSpeedValue",
            "ApertureValue",
            "ISOSpeedRatings",
            "PhotographicSensitivity",
            "FocalLength",
            "DigitalZoomRatio",
        )
        seen = {t[0] for t in rows}
        for key in _fb_order:
            if len(rows) >= _MAX_EXIF_OVERLAY_PAIRS:
                break
            if key in seen or key not in merged or key in _BORING_EXIF_NAMES:
                continue
            short = _exif_value_short(merged[key], 56)
            if short:
                rows.append((key, short))
                seen.add(key)

    return rows[:_MAX_EXIF_OVERLAY_PAIRS]


def _build_exif_overlay_pairs(path: Path | str) -> list[tuple[str, str]]:
    merged = _load_merged_exif_from_path(path)
    if merged is None:
        return [("EXIF", tr("exif_file_inaccessible"))]
    return _build_exif_overlay_pairs_from_merged(merged)


def _split_exif_pairs_two_columns(
    pairs: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Divide a lista ao meio: coluna esquerda + coluna direita."""
    if not pairs:
        return [], []
    mid = (len(pairs) + 1) // 2
    return pairs[:mid], pairs[mid:]


def _exif_overlay_row(lbl: str, val: str) -> ft.Row:
    """Rótulo + valor (Inter em assets/fonts, se existir)."""
    _typ = _exif_strip_typography()
    lf, lfb = _typ[0]
    vf, vfb = _typ[1]
    return ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Container(
                expand=2,
                content=ft.Text(
                    f"{lbl}:" if lbl else "",
                    size=11,
                    weight=ft.FontWeight.W_500,
                    font_family=lf,
                    font_family_fallback=lfb,
                    color=ft.Colors.with_opacity(0.78, "#b8c2d4"),
                    text_align=ft.TextAlign.RIGHT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ),
            ft.Container(
                expand=3,
                content=ft.Text(
                    val,
                    size=11,
                    weight=ft.FontWeight.W_600,
                    font_family=vf,
                    font_family_fallback=vfb,
                    color=ft.Colors.with_opacity(0.98, ft.Colors.WHITE),
                    text_align=ft.TextAlign.LEFT,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ),
        ],
    )


def _exif_strip_credit_line_ft() -> ft.Container:
    """Rodapé da tarja: crédito + linha Instagram (glifo + @); sem STRETCH no Stack."""
    _, _, (cf, cfb) = _exif_strip_typography()
    ig_controls: list[ft.Control] = []
    if _ASSET_INSTAGRAM_GLYPH.is_file():
        ig_controls.append(
            ft.Image(
                src=_flet_image_display_src(str(_ASSET_INSTAGRAM_GLYPH)),
                width=14,
                height=14,
                fit=ft.BoxFit.CONTAIN,
                filter_quality=ft.FilterQuality.MEDIUM,
            )
        )
    ig_controls.append(
        ft.Text(
            _EXIF_STRIP_INSTAGRAM_HANDLE,
            size=9,
            weight=ft.FontWeight.W_500,
            font_family=cf,
            font_family_fallback=cfb,
            color=ft.Colors.with_opacity(0.82, "#c8d4e4"),
            text_align=ft.TextAlign.LEFT,
        )
    )
    return ft.Container(
        padding=ft.Padding.only(top=6),
        content=ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[
                        ft.Text(
                            _EXIF_STRIP_APP_CREDIT,
                            size=9,
                            weight=ft.FontWeight.W_500,
                            font_family=cf,
                            font_family_fallback=cfb,
                            color=ft.Colors.with_opacity(0.82, "#c8d4e4"),
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                ),
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=ig_controls,
                ),
            ],
        ),
    )


def _exif_overlay_two_columns_from_pairs(pairs: list[tuple[str, str]]) -> ft.Column:
    """Duas colunas de linhas EXIF + linha de crédito."""
    left, right = _split_exif_pairs_two_columns(pairs)
    return ft.Column(
        spacing=6,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Row(
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Column(
                        expand=True,
                        spacing=4,
                        tight=True,
                        controls=[_exif_overlay_row(lbl, val) for lbl, val in left],
                    ),
                    ft.Column(
                        expand=True,
                        spacing=4,
                        tight=True,
                        controls=[_exif_overlay_row(lbl, val) for lbl, val in right],
                    ),
                ],
            ),
            _exif_strip_credit_line_ft(),
        ],
    )


def _exif_overlay_single_column_from_pairs(pairs: list[tuple[str, str]]) -> ft.Column:
    """Uma coluna (painel estreito nas laterais; melhor em fotos verticais)."""
    return ft.Column(
        spacing=5,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.START,
        controls=[
            *[_exif_overlay_row(lbl, val) for lbl, val in pairs],
            _exif_strip_credit_line_ft(),
        ],
    )


def _exif_overlay_two_columns(path: Path | str) -> ft.Column:
    return _exif_overlay_two_columns_from_pairs(_build_exif_overlay_pairs(path))


def _pillow_overlay_font(size: int):
    """Fonte TrueType para tarja exportada (Inter em assets, depois sistema)."""
    from PIL import ImageFont

    inter_first: list[str] = []
    for p in (INTER_FONT_MEDIUM, INTER_FONT_REGULAR, INTER_FONT_SEMIBOLD):
        if p.is_file():
            inter_first.append(str(p))
    platform: tuple[str, ...]
    if sys.platform == "darwin":
        platform = (
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/SFNSDisplay.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        )
    elif sys.platform == "win32":
        platform = (
            "C:\\Windows\\Fonts\\segoeui.ttf",
            "C:\\Windows\\Fonts\\segoeuil.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
        )
    else:
        platform = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        )
    ordered = tuple(inter_first) + platform
    for p in ordered:
        if not os.path.isfile(p):
            continue
        try:
            if p.lower().endswith((".ttc", ".otc")):
                return ImageFont.truetype(p, size, index=0)
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _exif_export_fg_rgb() -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """RGB do texto na tarja exportada.

    No ecrã, o rótulo usa #b8c2d4 @ 0,78 sobre o painel opaco da UI. Na exportação o
    texto vai por cima da tarja já misturada com a foto (semi-opaca), por isso o mesmo
    blend ficava escuro — rótulos usam cinza-azulado mais claro e mais opaco ao painel.
    """
    pr, pg, pb = EXIF_STRIP_PANEL_RGB

    def blend(c: tuple[int, int, int], a: float) -> tuple[int, int, int]:
        r, g, b = c
        return (
            int(round(a * r + (1.0 - a) * pr)),
            int(round(a * g + (1.0 - a) * pg)),
            int(round(a * b + (1.0 - a) * pb)),
        )

    # Rótulos: cinza-azulado claro sobre o painel (legível na tarja semi-opaca sobre a foto).
    # Crédito + @Instagram: tom mais claro que o antigo 0,62×#a8b0bf.
    return blend((214, 222, 236), 0.93), blend((255, 255, 255), 0.98), blend((196, 204, 218), 0.82)


def _pillow_exif_strip_fonts(body_px: int, cred_px: int):
    """Inter Medium (rótulo), SemiBold (valor), Regular/Medium (crédito) — como _exif_strip_typography."""
    from PIL import ImageFont

    def load_ttf(path: Path, px: int):
        if not path.is_file():
            return None
        try:
            if path.suffix.lower() in (".ttc", ".otc"):
                return ImageFont.truetype(str(path), px, index=0)
            return ImageFont.truetype(str(path), px)
        except Exception:
            return None

    fl = load_ttf(INTER_FONT_MEDIUM, body_px) or load_ttf(
        INTER_FONT_REGULAR, body_px
    ) or _pillow_overlay_font(body_px)
    fv = load_ttf(INTER_FONT_SEMIBOLD, body_px) or load_ttf(
        INTER_FONT_MEDIUM, body_px
    ) or _pillow_overlay_font(body_px)
    cred_path = (
        INTER_FONT_REGULAR if INTER_FONT_REGULAR.is_file() else INTER_FONT_MEDIUM
    )
    fc = load_ttf(cred_path, cred_px) or load_ttf(
        INTER_FONT_MEDIUM, cred_px
    ) or _pillow_overlay_font(cred_px)
    return fl, fv, fc


def _exif_row_label_value_widths(inner_w: int, spacing: int) -> tuple[int, int]:
    """Proporção 2:3 como expand=2 / expand=3 na Row da UI."""
    usable = max(1, inner_w - spacing)
    lw = max(8, int(round(usable * (2.0 / 5.0))))
    vw = max(8, usable - lw)
    return lw, vw


def _trunc_lbl_colon(
    draw,
    font_l,
    lbl: str,
    max_w: int,
    sw: int,
    trunc,
) -> str:
    if not lbl:
        return ""
    t = f"{lbl}:"
    bb = draw.textbbox((0, 0), t, font=font_l, stroke_width=sw)
    if bb[2] - bb[0] <= max_w:
        return t
    for n in range(len(lbl) - 1, 0, -1):
        t = f"{trunc(lbl, n)}:"
        bb = draw.textbbox((0, 0), t, font=font_l, stroke_width=sw)
        if bb[2] - bb[0] <= max_w:
            return t
    return ":"


def _tighten_exif_val_mx_split(
    d0,
    font_v,
    value_w: int,
    mx0: int,
    cols: tuple[list[tuple[str, str]], list[tuple[str, str]]],
    sw: int,
    trunc,
) -> int:
    mx = max(4, min(200, mx0))
    while mx > 3:
        ok = True
        for col in cols:
            for _lbl, val in col:
                vt = trunc(val, mx)
                bb = d0.textbbox((0, 0), vt, font=font_v, stroke_width=sw)
                if bb[2] - bb[0] > value_w:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return mx
        mx -= 1
    return 3


def _pillow_draw_exif_row_split(
    draw,
    x: int,
    y: int,
    inner_w: int,
    lbl: str,
    val: str,
    *,
    font_l,
    font_v,
    sw_l: int,
    sw_v: int,
    spacing: int,
    fg_l,
    fg_v,
    stroke_fill,
    val_mx: int,
    trunc,
) -> int:
    """Rótulo alinhado à direita na zona 2/5; valor à esquerda na zona 3/5. Devolve y inferior."""
    lw, vw = _exif_row_label_value_widths(inner_w, spacing)
    lbl_t = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, trunc)
    val_t = trunc(val, val_mx)
    while len(val_t) > 1:
        bbv = draw.textbbox((0, 0), val_t, font=font_v, stroke_width=sw_v)
        if bbv[2] - bbv[0] <= vw:
            break
        val_t = trunc(val, max(3, len(val_t) - 2))
    bb_l = draw.textbbox((0, 0), lbl_t, font=font_l, stroke_width=sw_l)
    w_l = bb_l[2] - bb_l[0]
    xl = x + lw - w_l
    xv = x + lw + spacing
    draw.text(
        (xl, y),
        lbl_t,
        fill=fg_l,
        font=font_l,
        stroke_width=sw_l,
        stroke_fill=stroke_fill,
    )
    draw.text(
        (xv, y),
        val_t,
        fill=fg_v,
        font=font_v,
        stroke_width=sw_v,
        stroke_fill=stroke_fill,
    )
    b1 = draw.textbbox((xl, y), lbl_t, font=font_l, stroke_width=sw_l)
    b2 = draw.textbbox((xv, y), val_t, font=font_v, stroke_width=sw_v)
    return max(b1[3], b2[3])


def _pillow_instagram_glyph_rgba(max_px: int):
    from PIL import Image as PILImage

    if not _ASSET_INSTAGRAM_GLYPH.is_file():
        return None
    try:
        im = PILImage.open(_ASSET_INSTAGRAM_GLYPH).convert("RGBA")
    except Exception:
        return None
    im.thumbnail((max_px, max_px), PILImage.Resampling.LANCZOS)
    return im


def _exif_footer_soft_instagram_height(
    d0,
    font_cred,
    cred_sw: int,
    gap_mid: int,
    icon_px: int,
) -> int:
    """Altura do bloco: linha software + intervalo + linha @ (sem «+ gap + 6» do rodapé)."""
    b0 = d0.textbbox((0, 0), _EXIF_STRIP_APP_CREDIT, font=font_cred, stroke_width=cred_sw)
    h0 = b0[3] - b0[1]
    b1 = d0.textbbox(
        (0, 0), _EXIF_STRIP_INSTAGRAM_HANDLE, font=font_cred, stroke_width=cred_sw
    )
    h_txt = b1[3] - b1[1]
    h_row2 = max(icon_px, h_txt)
    return h0 + gap_mid + h_row2


def _exif_footer_reserved_height(
    d0, font_cred, cred_sw: int, gap: int, icon_px: int
) -> int:
    """Reserva vertical do rodapé (como o antigo credit_h_draw): bloco + gap + 6."""
    gap_mid = max(3, gap // 2)
    return _exif_footer_soft_instagram_height(d0, font_cred, cred_sw, gap_mid, icon_px) + gap + 6


def _pillow_draw_exif_footer_soft_instagram(
    base,
    draw,
    sx: int,
    strip_w: int,
    y_block_top: int,
    *,
    font_cred,
    cred_sw: int,
    fg_cred,
    stroke_fill,
    gap_mid: int,
    icon_px: int,
) -> None:
    """Crédito + linha com glifo Instagram e @handle, centrados."""
    b0 = draw.textbbox((0, 0), _EXIF_STRIP_APP_CREDIT, font=font_cred, stroke_width=cred_sw)
    tw0 = b0[2] - b0[0]
    th0 = b0[3] - b0[1]
    tx0 = sx + max(0, (strip_w - tw0) // 2)
    draw.text(
        (tx0, y_block_top),
        _EXIF_STRIP_APP_CREDIT,
        fill=fg_cred,
        font=font_cred,
        stroke_width=cred_sw,
        stroke_fill=stroke_fill,
    )
    y2 = y_block_top + th0 + gap_mid
    gly = _pillow_instagram_glyph_rgba(icon_px)
    b1 = draw.textbbox(
        (0, 0), _EXIF_STRIP_INSTAGRAM_HANDLE, font=font_cred, stroke_width=cred_sw
    )
    tw1 = b1[2] - b1[0]
    th1 = b1[3] - b1[1]
    ih = gly.size[1] if gly is not None else 0
    iw_g = gly.size[0] if gly is not None else 0
    row_h = max(ih if gly is not None else icon_px, th1)
    spacer = 6 if gly is not None else 0
    total_w = iw_g + spacer + tw1
    x_start = sx + max(0, (strip_w - total_w) // 2)
    iy = int(y2 + max(0, (row_h - ih) // 2)) if gly is not None else y2
    ty = int(y2 + max(0, (row_h - th1) // 2))
    if gly is not None:
        base.paste(gly, (x_start, iy), gly)
        tx_hand = x_start + iw_g + spacer
    else:
        tx_hand = x_start
    draw.text(
        (tx_hand, ty),
        _EXIF_STRIP_INSTAGRAM_HANDLE,
        fill=fg_cred,
        font=font_cred,
        stroke_width=cred_sw,
        stroke_fill=stroke_fill,
    )


def _export_exif_font_px(image_width: int) -> int:
    """Pontos de fonte na exportação (legível em 4K, proporcional à largura)."""
    return max(15, min(36, int(image_width * 0.0235)))


def _compose_image_with_exif_strip(
    image_path: str,
    merged: dict[str, object],
    exif_filter: ExifDisplayFilter,
    *,
    strip_norm_x: float = 0.5,
    strip_norm_y: float = 1.0,
    strip_width_frac: float = EXIF_STRIP_WIDTH_FRAC,
    strip_v_w_scale: float = 1.0,
    strip_opacity: float = 0.58,
    strip_placement: str = "auto",
    viewport_pw: int | None = None,
    viewport_ph: int | None = None,
):
    """RGB com as mesmas dimensões da original; tarja EXIF semi-opaca sobreposta."""
    from PIL import Image, ImageDraw

    pairs = _build_exif_overlay_pairs_from_merged(merged, exif_filter=exif_filter)
    local = _local_path_for_read(image_path)
    with Image.open(local) as im0:
        im = im0.convert("RGB")
    w, h = im.size
    out = im.copy()
    draw = ImageDraw.Draw(out)

    rgb_l, rgb_v, rgb_c = _exif_export_fg_rgb()
    stroke_fill = (18, 20, 28)
    op = max(0.05, min(1.0, float(strip_opacity)))

    def _trunc(s: str, mx: int) -> str:
        s = s.strip()
        return s if len(s) <= mx else s[: mx - 1] + "…"

    branch = _exif_compose_branch(strip_placement, w, h)
    geom = _export_strip_geometry_pixels(
        w,
        h,
        viewport_pw,
        viewport_ph,
        strip_norm_x,
        strip_norm_y,
        strip_placement,
        pairs,
        strip_width_frac=strip_width_frac,
        strip_v_w_scale=strip_v_w_scale,
    )

    if branch in ("v_left", "v_right"):
        if geom is not None:
            sx, sy, sw_box, sh_box, s_vp = geom
            sw_box = max(60, min(sw_box, w - sx))
            sh_box = max(8, min(sh_box, h - sy))
            sv = max(s_vp, 1e-9)
            font_px_v = max(
                8,
                min(56, int(round(float(EXIF_STRIP_UI_BODY_PT) / sv))),
            )
            cred_px = max(
                7,
                min(44, int(round(float(EXIF_STRIP_UI_CREDIT_PT) / sv))),
            )
            pad_h = max(8, int(round(float(EXIF_STRIP_UI_PAD_H) / sv)))
            pad_v = max(8, int(round(float(EXIF_STRIP_UI_PAD_V) / sv)))
            gap = max(5, int(round(6.0 / sv)))
            spacing = gap
            inner_w = max(24, sw_box - 2 * pad_h)
            lw, vw = _exif_row_label_value_widths(inner_w, spacing)
            val_mx = 8
            for _ in range(34):
                font_l, font_v, font_cred = _pillow_exif_strip_fonts(
                    font_px_v, cred_px
                )
                sw_l = _pillow_exif_stroke_width(font_px_v)
                sw_v = sw_l
                cred_sw = _pillow_exif_stroke_width(cred_px)
                scratch = Image.new("RGB", (sw_box, max(h, 800)), (48, 48, 52))
                d0 = ImageDraw.Draw(scratch)
                val_mx = _tighten_exif_val_mx_split(
                    d0,
                    font_v,
                    vw,
                    max(8, min(120, vw // 3)),
                    (pairs, []),
                    sw_v,
                    _trunc,
                )
                gap_mid = max(3, gap // 2)
                icon_px = max(10, min(22, cred_px + 4))
                credit_h = _exif_footer_reserved_height(
                    d0, font_cred, cred_sw, gap, icon_px
                )
                col_h = 0
                for lbl, val in pairs:
                    lt = _trunc_lbl_colon(d0, font_l, lbl, lw, sw_l, _trunc)
                    vt = _trunc(val, val_mx)
                    bbl = d0.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
                    bbv = d0.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
                    col_h += max(bbl[3] - bbl[1], bbv[3] - bbv[1]) + gap
                need = pad_v * 2 + max(
                    col_h - gap + pad_v // 2 if pairs else 0,
                    font_px_v + gap * 2,
                ) + credit_h
                if need <= sh_box or font_px_v <= 7:
                    break
                font_px_v = max(7, font_px_v - 1)
                cred_px = max(6, cred_px - 1)
            bh2 = sh_box
            cr = max(
                2,
                min(
                    int(round(float(EXIF_STRIP_CORNER_RADIUS) / sv)),
                    sw_box // 2,
                    bh2 // 2,
                ),
            )
            _pillow_blend_dark_rect(
                out, sx, sy, sw_box, bh2, op, corner_radius=cr
            )
            font_l, font_v, font_cred = _pillow_exif_strip_fonts(font_px_v, cred_px)
            sw_l = _pillow_exif_stroke_width(font_px_v)
            sw_v = sw_l
            cred_sw = _pillow_exif_stroke_width(cred_px)
            scratch = Image.new("RGB", (sw_box, max(h, 800)), (48, 48, 52))
            d0 = ImageDraw.Draw(scratch)
            val_mx = _tighten_exif_val_mx_split(
                d0, font_v, vw, max(8, min(120, vw // 3)), (pairs, []), sw_v, _trunc
            )
            gap_mid = max(3, gap // 2)
            icon_px = max(10, min(22, cred_px + 4))
            credit_h_draw = _exif_footer_reserved_height(
                d0, font_cred, cred_sw, gap, icon_px
            )
            h_block = _exif_footer_soft_instagram_height(
                d0, font_cred, cred_sw, gap_mid, icon_px
            )
            y_limit = sy + bh2 - pad_v - credit_h_draw
            y = sy + pad_v
            x_row = sx + pad_h
            for lbl, val in pairs:
                lt = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, _trunc)
                vt = _trunc(val, val_mx)
                b1 = draw.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
                b2 = draw.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
                row_h = max(b1[3] - b1[1], b2[3] - b2[1])
                if y + row_h > y_limit:
                    break
                y_bottom = _pillow_draw_exif_row_split(
                    draw,
                    x_row,
                    y,
                    inner_w,
                    lbl,
                    val,
                    font_l=font_l,
                    font_v=font_v,
                    sw_l=sw_l,
                    sw_v=sw_v,
                    spacing=spacing,
                    fg_l=rgb_l,
                    fg_v=rgb_v,
                    stroke_fill=stroke_fill,
                    val_mx=val_mx,
                    trunc=_trunc,
                )
                y = y_bottom + gap
            y_footer_top = min(sy + bh2 - pad_v - h_block, y_limit - gap)
            y_footer_top = max(int(y_footer_top), sy + pad_v)
            _pillow_draw_exif_footer_soft_instagram(
                out,
                draw,
                sx,
                sw_box,
                y_footer_top,
                font_cred=font_cred,
                cred_sw=cred_sw,
                fg_cred=rgb_c,
                stroke_fill=stroke_fill,
                gap_mid=gap_mid,
                icon_px=icon_px,
            )
            return out

        font_px_v = _export_exif_font_px(w)
        bh_cap = max(8, h - 2)
        strip_w = 0
        val_mx = 8
        bar_h = 0
        for _ in range(28):
            pad = max(12, int(font_px_v * 0.37))
            gap = max(6, int(font_px_v * 0.28))
            spacing = max(4, int(round(6.0 * font_px_v / float(EXIF_STRIP_UI_BODY_PT))))
            strip_w = max(90, min(int(min(w, h) * 0.40), int(w * 0.44)))
            inner_w = max(24, strip_w - 2 * pad)
            lw, vw = _exif_row_label_value_widths(inner_w, spacing)
            font_l, font_v, font_cred = _pillow_exif_strip_fonts(
                font_px_v, max(9, font_px_v - 7)
            )
            sw_l = _pillow_exif_stroke_width(font_px_v)
            sw_v = sw_l
            cred_sw = _pillow_exif_stroke_width(max(9, font_px_v - 7))
            scratch = Image.new("RGB", (strip_w, max(h, 800)), (48, 48, 52))
            d0 = ImageDraw.Draw(scratch)
            val_mx = _tighten_exif_val_mx_split(
                d0,
                font_v,
                vw,
                max(8, min(72, vw // 3)),
                (pairs, []),
                sw_v,
                _trunc,
            )
            cred_px_v = max(9, font_px_v - 7)
            gap_mid = max(3, gap // 2)
            icon_px = max(10, min(22, cred_px_v + 4))
            credit_h = _exif_footer_reserved_height(
                d0, font_cred, cred_sw, gap, icon_px
            )
            col_h = 0
            for lbl, val in pairs:
                lt = _trunc_lbl_colon(d0, font_l, lbl, lw, sw_l, _trunc)
                vt = _trunc(val, val_mx)
                bbl = d0.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
                bbv = d0.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
                col_h += max(bbl[3] - bbl[1], bbv[3] - bbv[1]) + gap
            bar_inner = max(col_h - gap + pad // 2, font_px_v + gap * 2) + credit_h
            bar_h = pad * 2 + bar_inner
            if bar_h <= bh_cap or font_px_v <= 8:
                break
            font_px_v = max(8, font_px_v - 2)
        nx_eff, ny_eff = _export_map_strip_norms_to_image(
            w,
            h,
            viewport_pw,
            viewport_ph,
            strip_norm_x,
            strip_norm_y,
            strip_placement,
            strip_w,
            bar_h,
            strip_width_frac=strip_width_frac,
            strip_v_w_scale=strip_v_w_scale,
        )
        sx, sy, sw2, bh2 = _exif_strip_rect_vertical(
            w, h, strip_w, bar_h, nx_eff, ny_eff
        )
        _pillow_blend_dark_rect(out, sx, sy, sw2, bh2, op)
        pad = max(12, int(font_px_v * 0.37))
        gap = max(6, int(font_px_v * 0.28))
        spacing = max(4, int(round(6.0 * font_px_v / float(EXIF_STRIP_UI_BODY_PT))))
        inner_w = max(24, sw2 - 2 * pad)
        lw, vw = _exif_row_label_value_widths(inner_w, spacing)
        font_l, font_v, font_cred = _pillow_exif_strip_fonts(
            font_px_v, max(9, font_px_v - 7)
        )
        sw_l = _pillow_exif_stroke_width(font_px_v)
        sw_v = sw_l
        cred_sw = _pillow_exif_stroke_width(max(9, font_px_v - 7))
        scratch = Image.new("RGB", (sw2, max(h, 800)), (48, 48, 52))
        d0 = ImageDraw.Draw(scratch)
        val_mx = _tighten_exif_val_mx_split(
            d0, font_v, vw, max(8, min(72, vw // 3)), (pairs, []), sw_v, _trunc
        )
        cred_px_v = max(9, font_px_v - 7)
        gap_mid = max(3, gap // 2)
        icon_px = max(10, min(22, cred_px_v + 4))
        credit_h_draw = _exif_footer_reserved_height(
            d0, font_cred, cred_sw, gap, icon_px
        )
        h_block = _exif_footer_soft_instagram_height(
            d0, font_cred, cred_sw, gap_mid, icon_px
        )
        y_limit = sy + bh2 - pad - credit_h_draw
        y = sy + pad
        x_row = sx + pad
        for lbl, val in pairs:
            lt = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, _trunc)
            vt = _trunc(val, val_mx)
            b1 = draw.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
            b2 = draw.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
            row_h = max(b1[3] - b1[1], b2[3] - b2[1])
            if y + row_h > y_limit:
                break
            y = (
                _pillow_draw_exif_row_split(
                    draw,
                    x_row,
                    y,
                    inner_w,
                    lbl,
                    val,
                    font_l=font_l,
                    font_v=font_v,
                    sw_l=sw_l,
                    sw_v=sw_v,
                    spacing=spacing,
                    fg_l=rgb_l,
                    fg_v=rgb_v,
                    stroke_fill=stroke_fill,
                    val_mx=val_mx,
                    trunc=_trunc,
                )
                + gap
            )
        y_footer_top = min(sy + bh2 - pad - h_block, y_limit - gap)
        y_footer_top = max(int(y_footer_top), sy + pad)
        _pillow_draw_exif_footer_soft_instagram(
            out,
            draw,
            sx,
            sw2,
            y_footer_top,
            font_cred=font_cred,
            cred_sw=cred_sw,
            fg_cred=rgb_c,
            stroke_fill=stroke_fill,
            gap_mid=gap_mid,
            icon_px=icon_px,
        )
        return out

    # Tarja horizontal (fundo, topo ou zona central)
    left, right = _split_exif_pairs_two_columns(pairs)
    if geom is not None:
        sx, sy, strip_w, bar_h2, s_vp = geom
        strip_w = max(80, min(strip_w, w - sx))
        bar_h2 = max(48, min(bar_h2, h - sy))
        sv = max(s_vp, 1e-9)
        font_px_h = max(
            8,
            min(56, int(round(float(EXIF_STRIP_UI_BODY_PT) / sv))),
        )
        cred_px = max(
            7,
            min(44, int(round(float(EXIF_STRIP_UI_CREDIT_PT) / sv))),
        )
        pad_h = max(8, int(round(float(EXIF_STRIP_UI_PAD_H) / sv)))
        pad_v = max(8, int(round(float(EXIF_STRIP_UI_PAD_V) / sv)))
        gap = max(5, int(round(6.0 / sv)))
        col_gap = max(8, int(round(16.0 / sv)))
        spacing = gap
        val_mx = 12
        for _ in range(36):
            font_l, font_v, font_cred = _pillow_exif_strip_fonts(font_px_h, cred_px)
            stroke_w = _pillow_exif_stroke_width(font_px_h)
            sw_l = stroke_w
            sw_v = stroke_w
            cred_sw = _pillow_exif_stroke_width(cred_px)
            col_inner_w = max(40, (strip_w - pad_h * 2 - col_gap) // 2)
            lw, vw = _exif_row_label_value_widths(col_inner_w, spacing)
            scratch = Image.new("RGB", (strip_w, max(bar_h2 + 40, 200)), (48, 48, 52))
            d0 = ImageDraw.Draw(scratch)
            val_mx = _tighten_exif_val_mx_split(
                d0,
                font_v,
                vw,
                max(12, min(96, vw // 4)),
                (left, right),
                sw_v,
                _trunc,
            )

            def _column_pixel_height(col: list[tuple[str, str]]) -> int:
                if not col:
                    return font_px_h + gap
                total = 0
                for lbl, val in col:
                    lt = _trunc_lbl_colon(d0, font_l, lbl, lw, sw_l, _trunc)
                    vt = _trunc(val, val_mx)
                    bbl = d0.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
                    bbv = d0.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
                    total += max(bbl[3] - bbl[1], bbv[3] - bbv[1]) + gap
                return total - gap + pad_v // 2

            gap_mid = max(3, gap // 2)
            icon_px = max(10, min(22, cred_px + 4))
            credit_h = _exif_footer_reserved_height(
                d0, font_cred, cred_sw, gap, icon_px
            )
            bar_inner = (
                max(
                    _column_pixel_height(left),
                    _column_pixel_height(right),
                    font_px_h + gap * 2,
                )
                + credit_h
            )
            need_h = pad_v * 2 + bar_inner
            if need_h <= bar_h2 or font_px_h <= 8:
                break
            font_px_h = max(8, font_px_h - 1)
            cred_px = max(6, cred_px - 1)
        cr = max(
            2,
            min(
                int(round(float(EXIF_STRIP_CORNER_RADIUS) / sv)),
                strip_w // 2,
                bar_h2 // 2,
            ),
        )
        _pillow_blend_dark_rect(
            out, sx, sy, strip_w, bar_h2, op, corner_radius=cr
        )
        mid = sx + strip_w // 2
        col_inner_w = max(40, (strip_w - pad_h * 2 - col_gap) // 2)
        lw, vw = _exif_row_label_value_widths(col_inner_w, spacing)
        font_l, font_v, font_cred = _pillow_exif_strip_fonts(font_px_h, cred_px)
        stroke_w = _pillow_exif_stroke_width(font_px_h)
        sw_l = stroke_w
        sw_v = stroke_w
        cred_sw = _pillow_exif_stroke_width(cred_px)
        scratch = Image.new("RGB", (strip_w, max(bar_h2 + 40, 200)), (48, 48, 52))
        d0 = ImageDraw.Draw(scratch)
        val_mx = _tighten_exif_val_mx_split(
            d0,
            font_v,
            vw,
            max(12, min(96, vw // 4)),
            (left, right),
            sw_v,
            _trunc,
        )
        x_left = sx + pad_h
        x_right = mid + col_gap // 2
        yl = yr = sy + pad_v
        gap_mid = max(3, gap // 2)
        icon_px = max(10, min(22, cred_px + 4))
        credit_h_draw = _exif_footer_reserved_height(
            d0, font_cred, cred_sw, gap, icon_px
        )
        h_block = _exif_footer_soft_instagram_height(
            d0, font_cred, cred_sw, gap_mid, icon_px
        )
        y_limit = sy + bar_h2 - pad_v - credit_h_draw
        for lbl, val in left:
            lt = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, _trunc)
            vt = _trunc(val, val_mx)
            b1 = draw.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
            b2 = draw.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
            row_h = max(b1[3] - b1[1], b2[3] - b2[1])
            if yl + row_h > y_limit:
                break
            yl = (
                _pillow_draw_exif_row_split(
                    draw,
                    x_left,
                    yl,
                    col_inner_w,
                    lbl,
                    val,
                    font_l=font_l,
                    font_v=font_v,
                    sw_l=sw_l,
                    sw_v=sw_v,
                    spacing=spacing,
                    fg_l=rgb_l,
                    fg_v=rgb_v,
                    stroke_fill=stroke_fill,
                    val_mx=val_mx,
                    trunc=_trunc,
                )
                + gap
            )
        for lbl, val in right:
            lt = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, _trunc)
            vt = _trunc(val, val_mx)
            b1 = draw.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
            b2 = draw.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
            row_h = max(b1[3] - b1[1], b2[3] - b2[1])
            if yr + row_h > y_limit:
                break
            yr = (
                _pillow_draw_exif_row_split(
                    draw,
                    x_right,
                    yr,
                    col_inner_w,
                    lbl,
                    val,
                    font_l=font_l,
                    font_v=font_v,
                    sw_l=sw_l,
                    sw_v=sw_v,
                    spacing=spacing,
                    fg_l=rgb_l,
                    fg_v=rgb_v,
                    stroke_fill=stroke_fill,
                    val_mx=val_mx,
                    trunc=_trunc,
                )
                + gap
            )
        y_footer_top = min(sy + bar_h2 - pad_v - h_block, y_limit - gap)
        y_footer_top = max(int(y_footer_top), sy + pad_v)
        _pillow_draw_exif_footer_soft_instagram(
            out,
            draw,
            sx,
            strip_w,
            y_footer_top,
            font_cred=font_cred,
            cred_sw=cred_sw,
            fg_cred=rgb_c,
            stroke_fill=stroke_fill,
            gap_mid=gap_mid,
            icon_px=icon_px,
        )
        return out

    wf = max(0.38, min(1.0, float(strip_width_frac)))
    strip_w_calc = max(80, min(w, int(w * wf)))
    bh_cap = max(8, h - 2)
    font_px_h = _export_exif_font_px(w)

    bar_h = 0
    strip_w = strip_w_calc
    col_gap = 10
    col_inner_w = 40
    val_mx = 12
    pad = 10
    gap = 5
    stroke_w = 0
    spacing = 5

    for _ in range(28):
        pad = max(12, int(font_px_h * 0.37))
        gap = max(6, int(font_px_h * 0.28))
        spacing = max(4, int(round(6.0 * font_px_h / float(EXIF_STRIP_UI_BODY_PT))))
        stroke_w = _pillow_exif_stroke_width(font_px_h)
        cred_px_h = max(9, font_px_h - 7)
        font_l, font_v, font_cred = _pillow_exif_strip_fonts(font_px_h, cred_px_h)
        sw_l = stroke_w
        sw_v = stroke_w
        cred_sw = _pillow_exif_stroke_width(cred_px_h)
        strip_w = max(80, min(w, int(w * wf)))
        col_gap = max(10, strip_w // 56)
        col_inner_w = max(40, (strip_w - pad * 3 - col_gap) // 2)
        lw, vw = _exif_row_label_value_widths(col_inner_w, spacing)
        scratch = Image.new("RGB", (strip_w, 480), (48, 48, 52))
        d0 = ImageDraw.Draw(scratch)
        val_mx = _tighten_exif_val_mx_split(
            d0,
            font_v,
            vw,
            max(12, min(96, vw // 4)),
            (left, right),
            sw_v,
            _trunc,
        )

        def _column_pixel_height(col: list[tuple[str, str]]) -> int:
            if not col:
                return font_px_h + gap
            total = 0
            for lbl, val in col:
                lt = _trunc_lbl_colon(d0, font_l, lbl, lw, sw_l, _trunc)
                vt = _trunc(val, val_mx)
                bbl = d0.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
                bbv = d0.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
                total += max(bbl[3] - bbl[1], bbv[3] - bbv[1]) + gap
            return total - gap + pad // 2

        gap_mid = max(3, gap // 2)
        icon_px = max(10, min(22, cred_px_h + 4))
        credit_h = _exif_footer_reserved_height(
            d0, font_cred, cred_sw, gap, icon_px
        )
        bar_inner = (
            max(
                _column_pixel_height(left),
                _column_pixel_height(right),
                font_px_h + gap * 2,
            )
            + credit_h
        )
        bar_h = pad * 2 + bar_inner
        if bar_h <= bh_cap or font_px_h <= 9:
            break
        font_px_h = max(9, font_px_h - 2)

    nx_eff, ny_eff = _export_map_strip_norms_to_image(
        w,
        h,
        viewport_pw,
        viewport_ph,
        strip_norm_x,
        strip_norm_y,
        strip_placement,
        strip_w,
        bar_h,
        strip_width_frac=strip_width_frac,
        strip_v_w_scale=strip_v_w_scale,
    )
    sx, sy, strip_w2, bar_h2 = _exif_strip_rect_on_image(
        w, h, bar_h, nx_eff, ny_eff, strip_width_frac
    )
    strip_w = strip_w2
    mid = sx + strip_w // 2
    col_gap = max(10, strip_w // 56)
    col_inner_w = max(40, (strip_w - pad * 3 - col_gap) // 2)
    lw, vw = _exif_row_label_value_widths(col_inner_w, spacing)
    cred_px_h = max(9, font_px_h - 7)
    font_l, font_v, font_cred = _pillow_exif_strip_fonts(font_px_h, cred_px_h)
    stroke_w = _pillow_exif_stroke_width(font_px_h)
    sw_l = stroke_w
    sw_v = stroke_w
    cred_sw = _pillow_exif_stroke_width(cred_px_h)
    scratch = Image.new("RGB", (strip_w, 480), (48, 48, 52))
    d0 = ImageDraw.Draw(scratch)
    val_mx = _tighten_exif_val_mx_split(
        d0,
        font_v,
        vw,
        max(12, min(96, vw // 4)),
        (left, right),
        sw_v,
        _trunc,
    )

    _pillow_blend_dark_rect(out, sx, sy, strip_w, bar_h2, op)
    x_left = sx + pad
    x_right = mid + col_gap // 2
    yl = yr = sy + pad
    gap_mid = max(3, gap // 2)
    icon_px = max(10, min(22, cred_px_h + 4))
    credit_h_draw = _exif_footer_reserved_height(
        d0, font_cred, cred_sw, gap, icon_px
    )
    h_block = _exif_footer_soft_instagram_height(
        d0, font_cred, cred_sw, gap_mid, icon_px
    )
    y_limit = sy + bar_h2 - pad - credit_h_draw

    for lbl, val in left:
        lt = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, _trunc)
        vt = _trunc(val, val_mx)
        b1 = draw.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
        b2 = draw.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
        row_h = max(b1[3] - b1[1], b2[3] - b2[1])
        if yl + row_h > y_limit:
            break
        yl = (
            _pillow_draw_exif_row_split(
                draw,
                x_left,
                yl,
                col_inner_w,
                lbl,
                val,
                font_l=font_l,
                font_v=font_v,
                sw_l=sw_l,
                sw_v=sw_v,
                spacing=spacing,
                fg_l=rgb_l,
                fg_v=rgb_v,
                stroke_fill=stroke_fill,
                val_mx=val_mx,
                trunc=_trunc,
            )
            + gap
        )
    for lbl, val in right:
        lt = _trunc_lbl_colon(draw, font_l, lbl, lw, sw_l, _trunc)
        vt = _trunc(val, val_mx)
        b1 = draw.textbbox((0, 0), lt, font=font_l, stroke_width=sw_l)
        b2 = draw.textbbox((0, 0), vt, font=font_v, stroke_width=sw_v)
        row_h = max(b1[3] - b1[1], b2[3] - b2[1])
        if yr + row_h > y_limit:
            break
        yr = (
            _pillow_draw_exif_row_split(
                draw,
                x_right,
                yr,
                col_inner_w,
                lbl,
                val,
                font_l=font_l,
                font_v=font_v,
                sw_l=sw_l,
                sw_v=sw_v,
                spacing=spacing,
                fg_l=rgb_l,
                fg_v=rgb_v,
                stroke_fill=stroke_fill,
                val_mx=val_mx,
                trunc=_trunc,
            )
            + gap
        )
    y_footer_top = min(sy + bar_h2 - pad - h_block, y_limit - gap)
    y_footer_top = max(int(y_footer_top), sy + pad)
    _pillow_draw_exif_footer_soft_instagram(
        out,
        draw,
        sx,
        strip_w,
        y_footer_top,
        font_cred=font_cred,
        cred_sw=cred_sw,
        fg_cred=rgb_c,
        stroke_fill=stroke_fill,
        gap_mid=gap_mid,
        icon_px=icon_px,
    )
    return out


def _orientation_label(val: object) -> str | None:
    try:
        o = int(val)
    except (TypeError, ValueError):
        return None
    return {
        1: "normal",
        2: "espelhada H",
        3: "180°",
        4: "espelhada V",
        5: "90° CCW + espelho",
        6: "90° CW",
        7: "90° CW + espelho",
        8: "90° CCW",
    }.get(o)


def format_exif_footer(path: Path | str) -> str:
    """Rodapé EXIF: Aperture, Focal length, Lens, Camera, Speed, ISO (+ extras se existirem)."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return "EXIF: instale Pillow (pip install Pillow)."

    merged = _load_merged_exif_from_path(path)
    if merged is None:
        return f"EXIF: ficheiro não encontrado ou ilegível ({Path(_local_path_for_read(path)).name})."
    if not merged:
        return "Sem dados EXIF."

    def _s(val: object) -> str:
        if val is None:
            return ""
        if isinstance(val, bytes):
            try:
                return val.decode("utf-8", errors="replace").strip()
            except Exception:
                return ""
        return str(val).strip()

    # Ordem pedida: Aperture → Focal length → Lens → Camera → Speed → ISO
    main: list[str] = []

    fn = _ratio_to_float(merged.get("FNumber"))
    if fn is not None and fn > 0:
        fs = f"{fn:.2f}".rstrip("0").rstrip(".")
        main.append(f"Aperture: f/{fs}")

    fl = _ratio_to_float(merged.get("FocalLength"))
    if fl is not None and fl > 0:
        fl_bits = [f"{fl:g} mm"]
        fl35 = merged.get("FocalLengthIn35mmFilm")
        if fl35 is not None and str(fl35).strip():
            fl_bits.append(f"35mm eq. {fl35}")
        sd_m = _ratio_to_float(merged.get("SubjectDistance"))
        if sd_m is not None and 0.01 < sd_m < 1_000_000:
            fl_bits.append(f"focus {sd_m:g} m")
        main.append("Focal length: " + " · ".join(fl_bits))

    lens_make = _s(merged.get("LensMake", ""))
    lens_model = _s(merged.get("LensModel", ""))
    lens_spec = merged.get("LensSpecification")
    lens_line = " ".join(x for x in (lens_make, lens_model) if x).strip()
    if not lens_line and lens_spec is not None:
        lens_line = _exif_value_short(lens_spec, 96)
    if lens_line:
        main.append(f"Lens: {lens_line}")

    make, model = _s(merged.get("Make", "")), _s(merged.get("Model", ""))
    cam = " ".join(x for x in (make, model) if x)
    if cam:
        main.append(f"Camera: {cam}")

    artist = _s(merged.get("Artist", ""))
    copyright_ = _s(merged.get("Copyright", ""))
    if artist:
        main.append(f"Owner: {artist}")
    elif copyright_:
        main.append(f"Owner: {copyright_}")
    if artist and copyright_ and copyright_ != artist:
        main.append(f"Copyright: {copyright_}")

    exp = _format_exposure_seconds(merged.get("ExposureTime"))
    if exp:
        main.append(f"Speed: {exp}")

    iso = merged.get("PhotographicSensitivity")
    if iso is None:
        iso = merged.get("ISOSpeedRatings")
    if isinstance(iso, tuple) and iso:
        iso = iso[0]
    if iso is not None and str(iso).strip():
        main.append(f"ISO: {iso}")

    cap = _capture_mode_description(merged)
    if cap:
        main.append(f"Mode: {cap}")

    extras: list[str] = []
    dt = merged.get("DateTimeOriginal") or merged.get("DateTime")
    if dt:
        extras.append(f"Date: {_s(dt)}")
    sw = merged.get("Software")
    if sw:
        extras.append(f"Software: {_s(sw)[:64]}")
    ori = merged.get("Orientation")
    if ori is not None:
        ol = _orientation_label(ori)
        if ol:
            extras.append(f"Orientation: {ol}")

    parts = main + extras

    has_photo_meta = bool(_PHOTO_EXIF_ANY_OF & merged.keys()) or any(
        k.startswith("GPS.") for k in merged
    )

    if not main:
        # Secundário: etiquetas fotográficas por nome (se _ratio_to_float falhou noutros sítios).
        _fb_order = (
            "DateTimeOriginal",
            "DateTime",
            "Make",
            "Model",
            "LensMake",
            "LensModel",
            "LensSpecification",
            "FNumber",
            "ExposureTime",
            "ShutterSpeedValue",
            "ApertureValue",
            "ISOSpeedRatings",
            "PhotographicSensitivity",
            "FocalLength",
            "FocalLengthIn35mmFilm",
            "ExposureProgram",
            "MeteringMode",
            "Flash",
            "WhiteBalance",
        )
        fb: list[str] = []
        for key in _fb_order:
            if key not in merged or key in _BORING_EXIF_NAMES:
                continue
            val = merged[key]
            short = _exif_value_short(val, 96)
            if not short:
                continue
            fb.append(f"{key}: {short}")
            if len(fb) >= 12:
                break
        if fb:
            parts.extend(fb)
        elif not has_photo_meta:
            return (
                "Sem EXIF de câmara (só resolução/DPI). "
                "Re-exportações, capturas de ecrã, redes sociais e algumas apps removem "
                "abertura, ISO, objetiva, etc. Use o JPEG/RAW original da câmara."
            )
        else:
            skip_prefixes = ("JPEG", "Strip", "Tile", "Thumbnail", "CFA", "ReferenceBlack", "GPS.")
            for name in sorted(merged.keys()):
                if name in _BORING_EXIF_NAMES:
                    continue
                if any(name.startswith(p) for p in skip_prefixes):
                    continue
                raw = merged[name]
                short = _exif_value_short(raw, 100)
                if not short:
                    continue
                parts.append(f"{name}: {short}")
                if len(parts) >= 20:
                    break

    if not parts:
        return (
            "Sem EXIF de câmara (só resolução/DPI). "
            "Re-exportações, capturas de ecrã, redes sociais e algumas apps removem "
            "abertura, ISO, objetiva, etc. Use o JPEG/RAW original da câmara."
        )

    return "  ·  ".join(parts)


def collect_images_from_dir(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            out.append(p)
    return out


def _path_resolve_key(p: str) -> str:
    try:
        return str(Path(p).resolve())
    except Exception:
        return p


def _merge_unique_image_paths(existing: list[str], new_items: list[str]) -> list[str]:
    """Mantém ordem; evita o mesmo ficheiro duas vezes (por caminho resolvido)."""
    seen: set[str] = set()
    out: list[str] = []
    for p in existing:
        key = _path_resolve_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    for p in new_items:
        key = _path_resolve_key(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _merge_paths_and_groups(
    existing: list[str],
    existing_groups: list[str],
    new_paths: list[str],
    new_groups: list[str],
) -> tuple[list[str], list[str]]:
    """Igual a `_merge_unique_image_paths`, mas mantém o grupo por índice."""
    if len(existing) != len(existing_groups):
        raise ValueError("existing / existing_groups length mismatch")
    if len(new_paths) != len(new_groups):
        raise ValueError("new_paths / new_groups length mismatch")
    seen: set[str] = set()
    out_p: list[str] = []
    out_g: list[str] = []
    for p, g in zip(existing, existing_groups, strict=True):
        key = _path_resolve_key(p)
        if key not in seen:
            seen.add(key)
            out_p.append(p)
            out_g.append(g)
    for p, g in zip(new_paths, new_groups, strict=True):
        key = _path_resolve_key(p)
        if key not in seen:
            seen.add(key)
            out_p.append(p)
            out_g.append(g)
    return out_p, out_g


def _infer_thumb_group_roots(paths: list[str]) -> list[str]:
    """Uma chave por imagem: pasta-mãe (ficheiros soltos) ou raiz comum."""
    out: list[str] = []
    for p in paths:
        try:
            out.append(str(Path(p).resolve().parent))
        except Exception:
            out.append(str(Path(p).expanduser().parent))
    return out


def _thumb_group_heading_text(group_key: str) -> str:
    try:
        name = Path(group_key).name
        return name if name else group_key
    except Exception:
        return group_key


def _image_paths_from_argv() -> list[Path]:
    """Ficheiros de imagem em sys.argv (Abrir com / terminal), sem duplicados."""
    out: list[Path] = []
    for arg in sys.argv:
        if not arg or not arg.strip():
            continue
        if arg.startswith("-psn"):
            continue
        try:
            if arg.startswith("file:"):
                raw = _local_path_for_read(arg)
            else:
                raw = os.path.expanduser(arg)
            p = Path(raw)
        except Exception:
            continue
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        r = p.resolve()
        if r not in out:
            out.append(r)
    return out


def main(page: ft.Page) -> None:
    init_app_i18n()
    page.title = "Ed Image Preview"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.padding = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.vertical_alignment = ft.MainAxisAlignment.START

    # Desktop: janela com tamanho inicial útil para a foto usar a área disponível.
    if not page.web:
        page.window.width = 1280
        page.window.height = 840
        page.window.min_width = 560
        page.window.min_height = 420

    _inter_fonts: dict[str, str] = {}
    if INTER_FONT_REGULAR.is_file():
        _inter_fonts["Inter"] = "/fonts/Inter-Regular.ttf"
    if INTER_FONT_MEDIUM.is_file():
        _inter_fonts["Inter Medium"] = "/fonts/Inter-Medium.ttf"
    if INTER_FONT_SEMIBOLD.is_file():
        _inter_fonts["Inter SemiBold"] = "/fonts/Inter-SemiBold.ttf"
    if _inter_fonts:
        page.fonts = _inter_fonts

    thumb_refs: list[ft.Container] = []
    # Referência extra: o ServiceRegistry remove serviços com getrefcount baixo;
    # sem isto o FilePicker pode ser desregistado e pick_files falha.
    _file_picker_keepalive: list[ft.FilePicker] = []

    status = ft.Text(
        tr("status_add_hint"),
        size=13,
        opacity=0.8,
    )
    _sw_compact = {
        "label_text_style": ft.TextStyle(size=11),
        "padding": ft.Padding.symmetric(horizontal=0, vertical=0),
    }
    show_thumbs = ft.Switch(
        label=tr("label_thumbnails"),
        value=True,
        **_sw_compact,
    )

    # Painel esquerdo retrátil: menu + EXIF + miniaturas (área direita só para a foto).
    sidebar_open: list[bool] = [True]
    SIDEBAR_W_EXPANDED = 300
    SIDEBAR_W_COLLAPSED = 40

    page_view = ft.PageView(
        expand=True,
        horizontal=True,
        viewport_fraction=1.0,
        pad_ends=False,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
    )

    # Flet 0.84 não expõe `Wrap`. Usamos Column de Rows (largura do painel) para não
    # aninhar GridView+scroll com altura ilimitada.
    _thumb_inner_w = max(72, int(SIDEBAR_W_EXPANDED - 28))
    thumb_wrap = ft.Column(spacing=8, tight=True, controls=[])

    def _relayout_thumb_rows() -> None:
        thumb_wrap.controls.clear()
        n = len(thumb_refs)
        if n == 0:
            return
        gap = 8
        cell = 72 + gap
        per_row = max(1, (_thumb_inner_w + gap) // cell)
        groups = gallery_group_roots
        if len(groups) != n:
            groups = _infer_thumb_group_roots(gallery_paths)
        show_sections = len({*groups}) > 1

        def _row_chunk(start: int, end: int) -> None:
            for k in range(start, end, per_row):
                chunk = thumb_refs[k : k + per_row]
                thumb_wrap.controls.append(
                    ft.Row(
                        spacing=gap,
                        alignment=ft.MainAxisAlignment.START,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=list(chunk),
                    )
                )

        if not show_sections:
            _row_chunk(0, n)
            return

        i = 0
        while i < n:
            gkey = groups[i]
            j = i + 1
            while j < n and groups[j] == gkey:
                j += 1
            title = _thumb_group_heading_text(gkey)
            thumb_wrap.controls.append(
                ft.Container(
                    width=float(_thumb_inner_w),
                    padding=ft.Padding.only(top=6 if i > 0 else 0, bottom=2),
                    tooltip=gkey,
                    content=ft.Text(
                        title,
                        size=11,
                        weight=ft.FontWeight.W_600,
                        opacity=0.82,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        max_lines=1,
                    ),
                )
            )
            _row_chunk(i, j)
            i = j

    thumb_selected: set[int] = set()
    thumb_select_btns: list[ft.IconButton] = []

    def clear_thumb_selection(_: ft.ControlEvent | None = None) -> None:
        thumb_selected.clear()
        for b in thumb_select_btns:
            b.icon = ft.Icons.CHECK_BOX_OUTLINE_BLANK
        page.update()

    thumb_strip = ft.Container(
        visible=True,
        padding=ft.Padding.all(12),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        content=ft.Column(
            spacing=8,
            tight=True,
            controls=[
                ft.Row(
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                    controls=[
                        ft.Text(
                            tr("thumb_strip_hint"),
                            size=12,
                            weight=ft.FontWeight.W_500,
                            opacity=0.7,
                            expand=True,
                        ),
                        ft.TextButton(
                            tr("btn_clear_marks"),
                            on_click=clear_thumb_selection,
                        ),
                    ],
                ),
                thumb_wrap,
            ],
        ),
    )

    btn_prev = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT,
        tooltip=tr("tooltip_prev"),
        disabled=True,
    )
    btn_next = ft.IconButton(
        icon=ft.Icons.CHEVRON_RIGHT,
        tooltip=tr("tooltip_next"),
        disabled=True,
    )

    def sync_nav_buttons() -> None:
        n = len(page_view.controls)
        if n == 0:
            btn_prev.disabled = True
            btn_next.disabled = True
            return
        idx = page_view.selected_index
        btn_prev.disabled = idx <= 0
        btn_next.disabled = idx >= n - 1

    async def _go_prev_async() -> None:
        await page_view.previous_page()
        highlight_thumb(page_view.selected_index)
        sync_nav_buttons()
        page.update()

    async def _go_next_async() -> None:
        await page_view.next_page()
        highlight_thumb(page_view.selected_index)
        sync_nav_buttons()
        page.update()

    btn_prev.on_click = lambda _: page.run_task(_go_prev_async)
    btn_next.on_click = lambda _: page.run_task(_go_next_async)

    _KEY_PREV = frozenset({"Arrow Left", "ArrowLeft"})
    _KEY_NEXT = frozenset({"Arrow Right", "ArrowRight"})

    def on_keyboard(e: ft.KeyboardEvent) -> None:
        if not page_view.controls:
            return
        if e.key in _KEY_PREV and not btn_prev.disabled:
            page.run_task(_go_prev_async)
        elif e.key in _KEY_NEXT and not btn_next.disabled:
            page.run_task(_go_next_async)

    page.on_keyboard_event = on_keyboard

    def highlight_thumb(idx: int) -> None:
        for i, box in enumerate(thumb_refs):
            box.border = ft.Border.all(
                2,
                ft.Colors.PRIMARY if i == idx else ft.Colors.TRANSPARENT,
            )
            box.bgcolor = (
                ft.Colors.PRIMARY_CONTAINER if i == idx else ft.Colors.SURFACE_CONTAINER_HIGHEST
            )

    exif_bar_refs: list[ft.Container] = []
    slide_merges: list[dict[str, object]] = []
    exif_strip_wrap_refs: list[ft.Container] = []
    exif_strip_nx: float = 0.5
    exif_strip_ny: float = 1.0
    exif_strip_w_frac: float = EXIF_STRIP_WIDTH_FRAC
    exif_strip_v_scale: float = 1.0
    _exif_drag_prev: list[tuple[float, float] | None] = [None]
    _exif_resize_prev: list[tuple[float, float] | None] = [None]
    slide_dims: list[tuple[int, int]] = []
    exif_strip_opacity: float = 0.58
    exif_strip_placement: str = "auto"

    exif_sw_soft = ft.Switch(label=tr("label_software"), value=True, **_sw_compact)
    exif_sw_date = ft.Switch(label=tr("label_capture_date"), value=True, **_sw_compact)
    exif_sw_mode = ft.Switch(label=tr("label_mode"), value=True, **_sw_compact)
    exif_show_strip = ft.Switch(label=tr("label_exif_strip"), value=True, **_sw_compact)

    def _get_exif_filter() -> ExifDisplayFilter:
        return ExifDisplayFilter(
            show_software=exif_sw_soft.value,
            show_date=exif_sw_date.value,
            show_mode=exif_sw_mode.value,
        )

    def _effective_placement_for_index(idx: int) -> str:
        p = (exif_strip_placement or "auto").strip().lower()
        if p not in EXIF_PLACEMENT_KEYS:
            p = "auto"
        if p == "auto":
            if 0 <= idx < len(slide_dims):
                tw, th = slide_dims[idx]
                return "v_left" if th > tw else "h_bottom"
            return "h_bottom"
        return p

    def _vertical_overlay_for_index(idx: int) -> bool:
        return _effective_placement_for_index(idx).startswith("v_")

    def _exif_strip_metrics_for_slide(slide_i: int, pw: int, ph_f: float) -> tuple[str, float, float]:
        """Largura/altura para mapear ny=1 ao fundo do slide (altura ~conteúdo EXIF, sem chão excessivo)."""
        ek_i = _effective_placement_for_index(slide_i)
        merged_i = slide_merges[slide_i] if slide_i < len(slide_merges) else {}
        pairs_i = _build_exif_overlay_pairs_from_merged(
            merged_i, exif_filter=_get_exif_filter()
        )
        intr = _viewport_strip_intrinsic_height(ek_i, pairs_i, ph_f)
        wf = max(
            EXIF_STRIP_W_FRAC_MIN,
            min(EXIF_STRIP_W_FRAC_MAX, float(exif_strip_w_frac)),
        )
        vs = max(
            EXIF_STRIP_V_W_SCALE_MIN,
            min(EXIF_STRIP_V_W_SCALE_MAX, float(exif_strip_v_scale)),
        )
        if ek_i.startswith("v_"):
            strip_w = min(300.0, float(pw) * 0.42) * vs
            strip_w = max(72.0, min(float(pw) - 6.0, strip_w))
            cap = min(ph_f * 0.90, float(EXIF_VIEW_H_VERT))
            strip_h = min(ph_f - 1.0, max(48.0, min(cap, intr + 8.0)))
        else:
            strip_w = max(120.0, min(float(pw), float(pw) * wf))
            # Folga pequena: se strip_h >> altura real, ny=1 fica «acima» do fundo do slide.
            strip_h = min(ph_f - 1.0, max(48.0, intr + 6.0))
        return ek_i, strip_w, strip_h

    def _exif_strip_free_y_for_slide(slide_i: int, pw: int, ph_f: float, strip_h: float) -> float:
        """Intervalo vertical útil para ny (0=topo, 1=fundo da imagem em CONTAIN)."""
        stack_free = max(0.0, ph_f - strip_h)
        ek_i = _effective_placement_for_index(slide_i)
        if ek_i.startswith("v_"):
            return max(1.0, stack_free)
        if slide_i >= len(slide_dims):
            return stack_free
        tw_i, th_i = slide_dims[slide_i]
        if tw_i <= 0 or th_i <= 0:
            return stack_free
        ox, oy, dw, dh, _ = _letterbox_contain(tw_i, th_i, float(pw), ph_f)
        img_bot = oy + dh
        img_free = max(0.0, img_bot - strip_h)
        if img_free < 1.0:
            return max(1.0, stack_free)
        return max(1.0, min(stack_free, img_free))

    def _exif_strip_xy_for_slide(slide_i: int, pw: int, ph_f: float) -> tuple[float, float, float, float]:
        ek_i, strip_w, strip_h = _exif_strip_metrics_for_slide(slide_i, pw, ph_f)
        sx = max(0.0, min(1.0, exif_strip_nx)) * max(0.0, float(pw) - strip_w)
        free_y = _exif_strip_free_y_for_slide(slide_i, pw, ph_f, strip_h)
        sy = max(0.0, min(1.0, exif_strip_ny)) * free_y
        return sx, sy, strip_w, strip_h

    def _apply_exif_bar_opacity() -> None:
        op = max(0.08, min(1.0, float(exif_strip_opacity)))
        for c in exif_bar_refs:
            c.bgcolor = ft.Colors.with_opacity(op, ft.Colors.BLACK)

    def _refresh_exif_bars() -> None:
        if not exif_bar_refs or len(exif_bar_refs) != len(slide_merges):
            return
        filt = _get_exif_filter()
        for i in range(len(exif_bar_refs)):
            pairs = _build_exif_overlay_pairs_from_merged(
                slide_merges[i], exif_filter=filt
            )
            if _vertical_overlay_for_index(i):
                exif_bar_refs[i].content = _exif_overlay_single_column_from_pairs(
                    pairs
                )
            else:
                exif_bar_refs[i].content = _exif_overlay_two_columns_from_pairs(
                    pairs
                )
        _apply_exif_bar_opacity()
        _apply_exif_strip_positions()
        page.update()

    def _sync_exif_strip_visibility() -> None:
        v = exif_show_strip.value
        for c in exif_bar_refs:
            c.visible = v
        for outer in exif_strip_wrap_refs:
            outer.visible = v

    def _on_exif_strip_toggle(_: ft.ControlEvent) -> None:
        _sync_exif_strip_visibility()
        page.update()

    def on_exif_strip_close_click(_: ft.ControlEvent) -> None:
        exif_show_strip.value = False
        _sync_exif_strip_visibility()
        page.update()

    def _on_exif_fields_toggle(_: ft.ControlEvent) -> None:
        _refresh_exif_bars()

    exif_show_strip.on_change = _on_exif_strip_toggle
    exif_sw_soft.on_change = _on_exif_fields_toggle
    exif_sw_date.on_change = _on_exif_fields_toggle
    exif_sw_mode.on_change = _on_exif_fields_toggle

    def _estimate_exif_viewport_px() -> tuple[int, int]:
        ww = int(page.window.width or 1100)
        wh = int(page.window.height or 800)
        sw = SIDEBAR_W_EXPANDED if sidebar_open[0] else SIDEBAR_W_COLLAPSED
        # Área útil da foto à direita (sem painel lateral nem margens).
        pw = max(220, ww - 96 - sw)
        # Barra EXIF/miniaturas passou para o painel esquerdo — mais altura para o slide.
        ph = max(160, wh - 100)
        return pw, ph

    def _apply_exif_strip_positions() -> None:
        if not exif_strip_wrap_refs:
            return
        pw, ph = _estimate_exif_viewport_px()
        ph_f = float(ph)
        for i, outer in enumerate(exif_strip_wrap_refs):
            sx, sy, strip_w, _strip_h = _exif_strip_xy_for_slide(i, pw, ph_f)
            outer.right = None
            outer.bottom = None
            outer.top = sy
            outer.left = sx
            w_px = max(80, int(round(strip_w)))
            outer.width = w_px
            if i < len(exif_bar_refs):
                exif_bar_refs[i].width = w_px

    def reset_exif_strip_pos(_: ft.ControlEvent | None = None) -> None:
        nonlocal exif_strip_nx, exif_strip_ny, exif_strip_w_frac, exif_strip_v_scale
        idx = page_view.selected_index if page_view.controls else 0
        raw = (exif_strip_placement or "auto").strip().lower()
        if raw not in EXIF_PLACEMENT_KEYS:
            raw = "auto"
        if raw == "auto":
            if 0 <= idx < len(slide_dims):
                tw, th = slide_dims[idx]
                ek = "v_left" if th > tw else "h_bottom"
            else:
                ek = "h_bottom"
        else:
            ek = raw
        corner: dict[str, tuple[float, float]] = {
            "h_bottom": (0.5, 1.0),
            "h_bottom_left": (0.0, 1.0),
            "h_bottom_right": (1.0, 1.0),
            "h_top": (0.5, 0.0),
            "h_top_left": (0.0, 0.0),
            "h_top_right": (1.0, 0.0),
            "h_center": (0.5, 0.5),
            "v_left": (0.0, 0.5),
            "v_left_bottom": (0.0, 1.0),
            "v_left_top": (0.0, 0.0),
            "v_right": (1.0, 0.5),
            "v_right_bottom": (1.0, 1.0),
            "v_right_top": (1.0, 0.0),
        }
        exif_strip_nx, exif_strip_ny = corner.get(ek, (0.5, 1.0))
        exif_strip_w_frac = EXIF_STRIP_WIDTH_FRAC
        exif_strip_v_scale = 1.0
        _apply_exif_strip_positions()
        page.update()

    def on_exif_strip_pan_start(_: ft.DragStartEvent) -> None:
        _exif_drag_prev[0] = None

    def on_exif_strip_pan_update(e: ft.DragUpdateEvent) -> None:
        nonlocal exif_strip_nx, exif_strip_ny
        # Usar posição global: com pin top/bottom o sistema local do control muda e
        # local_position quebrava o arrasto perto do fundo (sem «sensibilidade» fina).
        gp = e.global_position
        cur = (float(gp.x), float(gp.y))
        prev = _exif_drag_prev[0]
        if prev is None:
            _exif_drag_prev[0] = cur
            return
        dx = cur[0] - prev[0]
        dy = cur[1] - prev[1]
        _exif_drag_prev[0] = cur
        pw, ph = _estimate_exif_viewport_px()
        ph_f = float(ph)
        idx = page_view.selected_index if page_view.controls else 0
        _ek, strip_w, strip_h_e = _exif_strip_metrics_for_slide(idx, pw, ph_f)
        denom_x = max(1.0, float(pw) - strip_w)
        denom_y = _exif_strip_free_y_for_slide(idx, pw, ph_f, strip_h_e)
        # Reforço suave só nos últimos % do intervalo (evita saltos ao aproximar do fundo).
        boost_y = 1.0
        edge = EXIF_STRIP_DRAG_BOOST_EDGE
        if exif_strip_ny >= edge and dy > 0:
            boost_y = 1.0 + EXIF_STRIP_DRAG_EDGE_BOOST * (
                (exif_strip_ny - edge) / max(1e-6, 1.0 - edge)
            )
        elif exif_strip_ny <= (1.0 - edge) and dy < 0:
            boost_y = 1.0 + EXIF_STRIP_DRAG_EDGE_BOOST * (
                ((1.0 - edge) - exif_strip_ny) / max(1e-6, 1.0 - edge)
            )
        boost_x = 1.0
        if exif_strip_nx >= edge and dx > 0:
            boost_x = 1.0 + EXIF_STRIP_DRAG_EDGE_BOOST * (
                (exif_strip_nx - edge) / max(1e-6, 1.0 - edge)
            )
        elif exif_strip_nx <= (1.0 - edge) and dx < 0:
            boost_x = 1.0 + EXIF_STRIP_DRAG_EDGE_BOOST * (
                ((1.0 - edge) - exif_strip_nx) / max(1e-6, 1.0 - edge)
            )
        exif_strip_nx += (dx / denom_x) * boost_x
        exif_strip_ny += (dy / denom_y) * boost_y
        exif_strip_nx = max(0.0, min(1.0, exif_strip_nx))
        exif_strip_ny = max(0.0, min(1.0, exif_strip_ny))
        _apply_exif_strip_positions()
        page.update()

    def on_exif_strip_pan_end(_: ft.DragEndEvent) -> None:
        nonlocal exif_strip_nx, exif_strip_ny
        _exif_drag_prev[0] = None
        sy_snap = EXIF_STRIP_DRAG_SNAP_Y
        sx_snap = EXIF_STRIP_DRAG_SNAP_X
        if sy_snap > 0.0:
            if exif_strip_ny >= 1.0 - sy_snap:
                exif_strip_ny = 1.0
            elif exif_strip_ny <= sy_snap:
                exif_strip_ny = 0.0
        if exif_strip_nx <= sx_snap:
            exif_strip_nx = 0.0
        elif exif_strip_nx >= 1.0 - sx_snap:
            exif_strip_nx = 1.0
        _apply_exif_strip_positions()
        page.update()

    def on_exif_strip_resize_start(_: ft.DragStartEvent) -> None:
        _exif_resize_prev[0] = None

    def on_exif_strip_resize_update(e: ft.DragUpdateEvent) -> None:
        nonlocal exif_strip_w_frac, exif_strip_v_scale
        gp = e.global_position
        cur = (float(gp.x), float(gp.y))
        prev = _exif_resize_prev[0]
        if prev is None:
            _exif_resize_prev[0] = cur
            return
        dx = cur[0] - prev[0]
        _exif_resize_prev[0] = cur
        pw, _ph = _estimate_exif_viewport_px()
        idx = page_view.selected_index if page_view.controls else 0
        pw_f = max(1.0, float(pw))
        if _vertical_overlay_for_index(idx):
            base = min(300.0, pw_f * 0.42)
            if base < 1.0:
                return
            exif_strip_v_scale += dx / base
            exif_strip_v_scale = max(
                EXIF_STRIP_V_W_SCALE_MIN,
                min(EXIF_STRIP_V_W_SCALE_MAX, exif_strip_v_scale),
            )
        else:
            exif_strip_w_frac += dx / pw_f
            exif_strip_w_frac = max(
                EXIF_STRIP_W_FRAC_MIN,
                min(EXIF_STRIP_W_FRAC_MAX, exif_strip_w_frac),
            )
        _apply_exif_strip_positions()
        page.update()

    def on_exif_strip_resize_end(_: ft.DragEndEvent) -> None:
        _exif_resize_prev[0] = None

    def on_exif_placement_changed(e: ft.ControlEvent) -> None:
        nonlocal exif_strip_placement
        v = getattr(e.control, "value", None) or "auto"
        exif_strip_placement = str(v)
        _refresh_exif_bars()
        reset_exif_strip_pos(None)

    def on_exif_opacity_changed(e: ft.ControlEvent) -> None:
        nonlocal exif_strip_opacity
        if e.control.value is None:
            return
        exif_strip_opacity = float(e.control.value)
        _apply_exif_bar_opacity()
        page.update()

    dd_exif_placement = ft.Dropdown(
        width=260,
        dense=True,
        value="auto",
        label=tr("strip_placement_label"),
        options=[
            ft.DropdownOption(key="auto", text=tr("pl_auto")),
            ft.DropdownOption(key="h_bottom", text=tr("pl_h_bottom")),
            ft.DropdownOption(key="h_bottom_left", text=tr("pl_h_bottom_left")),
            ft.DropdownOption(key="h_bottom_right", text=tr("pl_h_bottom_right")),
            ft.DropdownOption(key="h_top", text=tr("pl_h_top")),
            ft.DropdownOption(key="h_top_left", text=tr("pl_h_top_left")),
            ft.DropdownOption(key="h_top_right", text=tr("pl_h_top_right")),
            ft.DropdownOption(key="h_center", text=tr("pl_h_center")),
            ft.DropdownOption(key="v_left", text=tr("pl_v_left")),
            ft.DropdownOption(key="v_left_bottom", text=tr("pl_v_left_bottom")),
            ft.DropdownOption(key="v_left_top", text=tr("pl_v_left_top")),
            ft.DropdownOption(key="v_right", text=tr("pl_v_right")),
            ft.DropdownOption(key="v_right_bottom", text=tr("pl_v_right_bottom")),
            ft.DropdownOption(key="v_right_top", text=tr("pl_v_right_top")),
        ],
        on_select=on_exif_placement_changed,
    )

    exif_opacity_slider = ft.Slider(
        min=0.1,
        max=1.0,
        value=0.58,
        divisions=18,
        label=tr("label_opacity"),
        width=260,
        on_change=on_exif_opacity_changed,
    )

    gallery_paths: list[str] = []
    gallery_group_roots: list[str] = []
    _picker_temp_paths: list[str] = []

    def rebuild_gallery(
        paths: list[Path | str],
        *,
        extend: bool = False,
        group_roots: list[str] | None = None,
    ) -> None:
        nonlocal thumb_refs, gallery_paths, gallery_group_roots, _picker_temp_paths
        prior_len = len(gallery_paths)
        new_paths = [str(p) for p in paths]
        if new_paths:
            if group_roots is not None:
                if len(group_roots) != len(new_paths):
                    raise ValueError("group_roots must match paths length")
                gr = [str(g) for g in group_roots]
            else:
                gr = _infer_thumb_group_roots(new_paths)
        else:
            gr = []

        if extend:
            if not new_paths:
                return
            merged, merged_g = _merge_paths_and_groups(
                gallery_paths, gallery_group_roots, new_paths, gr
            )
            if merged == gallery_paths:
                return
            gallery_paths = merged
            gallery_group_roots = merged_g
        else:
            for tp in _picker_temp_paths:
                try:
                    os.unlink(tp)
                except OSError:
                    pass
            _picker_temp_paths.clear()
            gallery_paths = list(new_paths)
            gallery_group_roots = list(gr)

        paths = gallery_paths
        thumb_refs = []
        page_view.controls.clear()
        thumb_wrap.controls.clear()
        slide_merges.clear()
        exif_bar_refs.clear()
        exif_strip_wrap_refs.clear()
        slide_dims.clear()
        thumb_selected.clear()
        thumb_select_btns.clear()

        if not paths:
            for tp in _picker_temp_paths:
                try:
                    os.unlink(tp)
                except OSError:
                    pass
            _picker_temp_paths.clear()
            status.value = tr("status_no_images")
            sync_nav_buttons()
            page.update()
            return

        status.value = tr("status_n_images", n=len(paths))

        for i, src in enumerate(paths):
            src_str = str(src)
            img_display_src = _flet_image_display_src(src_str)
            merged_slide = _load_merged_exif_from_path(src_str)
            if merged_slide is None:
                m_for_pairs: dict[str, object] = {}
                exif_pairs_slide = [("EXIF", tr("exif_file_inaccessible"))]
                merged_for_logo: dict[str, object] = {}
            else:
                m_for_pairs = merged_slide
                merged_for_logo = merged_slide
                exif_pairs_slide = _build_exif_overlay_pairs_from_merged(
                    m_for_pairs, exif_filter=_get_exif_filter()
                )
            slide_merges.append(dict(m_for_pairs))
            try:
                from PIL import Image as PILImage

                with PILImage.open(_local_path_for_read(src_str)) as _imsz:
                    slide_dims.append(_imsz.size)
            except Exception:
                slide_dims.append((1920, 1080))
            vert = _vertical_overlay_for_index(i)
            exif_overlay_slide = (
                _exif_overlay_single_column_from_pairs(exif_pairs_slide)
                if vert
                else _exif_overlay_two_columns_from_pairs(exif_pairs_slide)
            )
            brand_slide = _camera_brand_badge_widget(merged_for_logo.get("Make"))

            def go_thumb_slide(e: ft.ControlEvent, index: int = i) -> None:
                page_view.selected_index = index
                highlight_thumb(index)
                page.update()

            def toggle_thumb_mark(e: ft.ControlEvent, index: int = i) -> None:
                if index in thumb_selected:
                    thumb_selected.discard(index)
                else:
                    thumb_selected.add(index)
                if 0 <= index < len(thumb_select_btns):
                    thumb_select_btns[index].icon = (
                        ft.Icons.CHECK_BOX
                        if index in thumb_selected
                        else ft.Icons.CHECK_BOX_OUTLINE_BLANK
                    )
                page.update()

            stack_controls: list[ft.Control] = [
                ft.Container(
                    expand=True,
                    content=ft.InteractiveViewer(
                        expand=True,
                        min_scale=0.25,
                        max_scale=6.0,
                        boundary_margin=ft.Margin.all(0),
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        constrained=True,
                        content=ft.Image(
                            src=img_display_src,
                            fit=ft.BoxFit.CONTAIN,
                            expand=True,
                            filter_quality=ft.FilterQuality.MEDIUM,
                        ),
                    ),
                ),
            ]
            if brand_slide is not None:
                stack_controls.append(
                    ft.Container(
                        top=12,
                        left=12,
                        content=brand_slide,
                    )
                )
            exif_box = ft.Container(
                padding=ft.Padding.symmetric(horizontal=14, vertical=11),
                bgcolor=ft.Colors.with_opacity(
                    max(0.12, min(1.0, exif_strip_opacity)), EXIF_STRIP_PANEL_HEX
                ),
                border_radius=EXIF_STRIP_CORNER_RADIUS,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                shadow=[
                    ft.BoxShadow(
                        spread_radius=0,
                        blur_radius=18,
                        color=ft.Colors.with_opacity(0.42, ft.Colors.BLACK),
                        offset=ft.Offset(0, 6),
                    ),
                ],
                content=exif_overlay_slide,
                visible=exif_show_strip.value,
                tooltip=tr("exif_drag_tooltip"),
            )
            exif_move_gd = ft.GestureDetector(
                mouse_cursor=ft.MouseCursor.GRAB,
                on_pan_start=on_exif_strip_pan_start,
                on_pan_update=on_exif_strip_pan_update,
                on_pan_end=on_exif_strip_pan_end,
                content=exif_box,
            )
            resize_handle = ft.GestureDetector(
                mouse_cursor=ft.MouseCursor.RESIZE_DOWN_RIGHT,
                on_pan_start=on_exif_strip_resize_start,
                on_pan_update=on_exif_strip_resize_update,
                on_pan_end=on_exif_strip_resize_end,
                content=ft.Container(width=22, height=22),
            )
            strip_stack = ft.Stack(
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                controls=[
                    ft.Container(content=exif_move_gd),
                    ft.Container(
                        top=0,
                        right=0,
                        width=40,
                        height=40,
                        content=ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=18,
                            tooltip=tr("tooltip_hide_strip"),
                            style=ft.ButtonStyle(padding=2),
                            on_click=on_exif_strip_close_click,
                        ),
                    ),
                    ft.Container(
                        right=0,
                        bottom=0,
                        width=28,
                        height=28,
                        content=resize_handle,
                    ),
                ],
            )
            exif_outer = ft.Container(
                content=strip_stack,
                visible=exif_show_strip.value,
            )
            exif_bar_refs.append(exif_box)
            exif_strip_wrap_refs.append(exif_outer)
            stack_controls.append(exif_outer)

            page_view.controls.append(
                ft.Container(
                    expand=True,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=ft.Colors.BLACK,
                    content=ft.Stack(
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                        controls=stack_controls,
                    ),
                )
            )

            sel_btn = ft.IconButton(
                icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                icon_size=18,
                tooltip=tr("tooltip_thumb_mark"),
                style=ft.ButtonStyle(padding=2),
                on_click=lambda e, ix=i: toggle_thumb_mark(e, ix),
            )
            thumb_select_btns.append(sel_btn)

            t = ft.Container(
                width=72,
                height=72,
                border_radius=8,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                border=ft.Border.all(2, ft.Colors.TRANSPARENT),
                content=ft.Stack(
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    controls=[
                        ft.Container(
                            left=0,
                            top=0,
                            right=0,
                            bottom=0,
                            content=ft.GestureDetector(
                                content=ft.Image(
                                    src=img_display_src,
                                    fit=ft.BoxFit.COVER,
                                    width=72,
                                    height=72,
                                ),
                                on_tap=lambda e, ix=i: go_thumb_slide(e, ix),
                            ),
                        ),
                        ft.Container(
                            top=0,
                            right=0,
                            width=34,
                            height=34,
                            alignment=ft.Alignment.CENTER,
                            bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
                            border_radius=ft.BorderRadius.only(
                                bottom_left=6,
                            ),
                            content=sel_btn,
                        ),
                    ],
                ),
            )
            thumb_refs.append(t)

        _relayout_thumb_rows()

        if extend and len(paths) > prior_len:
            page_view.selected_index = prior_len
        else:
            page_view.selected_index = 0
        highlight_thumb(page_view.selected_index)
        sync_nav_buttons()
        _apply_exif_strip_positions()
        _apply_exif_bar_opacity()
        page.update()
        if paths:
            _apply_exif_strip_positions()
            _apply_exif_bar_opacity()
            page.update()

    def on_page_changed(e: ft.ControlEvent) -> None:
        raw = e.data
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            try:
                idx = int(float(str(raw)))
            except (TypeError, ValueError):
                return
        if idx < 0 or idx >= len(page_view.controls):
            return
        highlight_thumb(idx)
        sync_nav_buttons()
        page.update()

    page_view.on_change = on_page_changed

    # FilePicker tem de estar registado no cliente antes de pick_files / pasta.
    # Colocá-lo primeiro em `page.services` evita TimeoutException ao invocar o diálogo.
    file_picker = ft.FilePicker()
    page.services.append(file_picker)
    _file_picker_keepalive.append(file_picker)

    _gallery_loading_layer: list[ft.Control | None] = [None]

    def _hide_gallery_loading() -> None:
        layer = _gallery_loading_layer[0]
        if layer is not None:
            try:
                if layer in page.overlay:
                    page.overlay.remove(layer)
            except ValueError:
                pass
            _gallery_loading_layer[0] = None
        page.update()

    def _show_gallery_loading(message: str) -> None:
        _hide_gallery_loading()
        layer = ft.Container(
            expand=True,
            bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
            alignment=ft.Alignment.CENTER,
            content=ft.Container(
                width=320,
                padding=ft.Padding.symmetric(horizontal=28, vertical=22),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                border_radius=12,
                shadow=[
                    ft.BoxShadow(
                        blur_radius=20,
                        spread_radius=0,
                        color=ft.Colors.with_opacity(0.35, ft.Colors.BLACK),
                        offset=ft.Offset(0, 6),
                    ),
                ],
                content=ft.Row(
                    spacing=18,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.ProgressRing(width=32, height=32),
                        ft.Text(
                            message,
                            size=14,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                ),
            ),
        )
        _gallery_loading_layer[0] = layer
        page.overlay.append(layer)
        page.update()

    def _exif_strip_export_kwargs() -> dict[str, object]:
        vpw, vph = _estimate_exif_viewport_px()
        return {
            "strip_norm_x": exif_strip_nx,
            "strip_norm_y": exif_strip_ny,
            "strip_width_frac": max(
                EXIF_STRIP_W_FRAC_MIN,
                min(EXIF_STRIP_W_FRAC_MAX, float(exif_strip_w_frac)),
            ),
            "strip_v_w_scale": max(
                EXIF_STRIP_V_W_SCALE_MIN,
                min(EXIF_STRIP_V_W_SCALE_MAX, float(exif_strip_v_scale)),
            ),
            "strip_opacity": exif_strip_opacity,
            "strip_placement": exif_strip_placement,
            "viewport_pw": vpw,
            "viewport_ph": vph,
        }

    async def _save_current_with_strip_async() -> None:
        if page.web:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_web_save"))
            )
            page.snack_bar.open = True
            page.update()
            return
        if not gallery_paths or not page_view.controls:
            return
        idx = min(page_view.selected_index, len(gallery_paths) - 1)
        path = gallery_paths[idx]
        merged = slide_merges[idx] if idx < len(slide_merges) else {}
        if _load_merged_exif_from_path(path) is None:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_file_inaccessible_save"))
            )
            page.snack_bar.open = True
            page.update()
            return
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_install_pillow"))
            )
            page.snack_bar.open = True
            page.update()
            return
        src_p = Path(path)
        ext = (src_p.suffix or ".jpg").lower()
        default_name = f"{src_p.stem}_exif{ext}"
        await asyncio.sleep(0.12)
        try:
            dest = await file_picker.save_file(
                dialog_title=tr("dialog_save_exif"),
                file_name=default_name,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=[
                    x
                    for x in (
                        ext.lstrip("."),
                        "jpg",
                        "jpeg",
                        "png",
                        "webp",
                    )
                    if x
                ],
            )
        except Exception as ex:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_dialog_error", ex=ex))
            )
            page.snack_bar.open = True
            page.update()
            return
        if not dest:
            return
        dest_path = Path(dest)
        try:
            im = _compose_image_with_exif_strip(
                path, merged, _get_exif_filter(), **_exif_strip_export_kwargs()
            )
            sfx = dest_path.suffix.lower()
            if sfx in (".jpg", ".jpeg"):
                im.save(str(dest_path), quality=92)
            elif sfx == ".png":
                im.save(str(dest_path))
            else:
                im.save(str(dest_path), quality=92)
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_saved", name=dest_path.name))
            )
            page.snack_bar.open = True
            page.update()
        except Exception as ex:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_save_error", ex=ex))
            )
            page.snack_bar.open = True
            page.update()

    async def _save_all_with_strip_async() -> None:
        if page.web:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_web_save"))
            )
            page.snack_bar.open = True
            page.update()
            return
        if not gallery_paths:
            return
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_install_pillow"))
            )
            page.snack_bar.open = True
            page.update()
            return
        await asyncio.sleep(0.12)
        try:
            folder = await file_picker.get_directory_path(
                dialog_title=tr("dialog_folder_copies")
            )
        except Exception as ex:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_folder_error", ex=ex))
            )
            page.snack_bar.open = True
            page.update()
            return
        if not folder:
            return
        base = Path(folder)
        filt = _get_exif_filter()
        n_ok = 0
        for i, p in enumerate(gallery_paths):
            src = Path(p)
            if not src.is_file():
                continue
            merged_i = slide_merges[i] if i < len(slide_merges) else {}
            dest = base / f"{src.stem}_exif{src.suffix}"
            j = 1
            while dest.exists():
                dest = base / f"{src.stem}_exif_{j}{src.suffix}"
                j += 1
            try:
                im = _compose_image_with_exif_strip(
                    str(src), merged_i, filt, **_exif_strip_export_kwargs()
                )
                if dest.suffix.lower() in (".jpg", ".jpeg"):
                    im.save(str(dest), quality=92)
                else:
                    im.save(str(dest))
                n_ok += 1
            except Exception:
                continue
        page.snack_bar = ft.SnackBar(
            content=ft.Text(tr("snack_saved_n", n=n_ok, folder=folder))
        )
        page.snack_bar.open = True
        page.update()

    async def _save_selected_with_strip_async() -> None:
        if page.web:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_web_save"))
            )
            page.snack_bar.open = True
            page.update()
            return
        if not thumb_selected:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_mark_thumbnails"))
            )
            page.snack_bar.open = True
            page.update()
            return
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_install_pillow"))
            )
            page.snack_bar.open = True
            page.update()
            return
        await asyncio.sleep(0.12)
        try:
            folder = await file_picker.get_directory_path(
                dialog_title=tr("dialog_folder_marked")
            )
        except Exception as ex:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_folder_error", ex=ex))
            )
            page.snack_bar.open = True
            page.update()
            return
        if not folder:
            return
        base = Path(folder)
        filt = _get_exif_filter()
        n_ok = 0
        for i in sorted(thumb_selected):
            if i < 0 or i >= len(gallery_paths):
                continue
            p = gallery_paths[i]
            src = Path(p)
            if not src.is_file():
                continue
            merged_i = slide_merges[i] if i < len(slide_merges) else {}
            if _load_merged_exif_from_path(p) is None:
                continue
            dest = base / f"{src.stem}_exif{src.suffix}"
            j = 1
            while dest.exists():
                dest = base / f"{src.stem}_exif_{j}{src.suffix}"
                j += 1
            try:
                im = _compose_image_with_exif_strip(
                    str(src), merged_i, filt, **_exif_strip_export_kwargs()
                )
                if dest.suffix.lower() in (".jpg", ".jpeg"):
                    im.save(str(dest), quality=92)
                else:
                    im.save(str(dest))
                n_ok += 1
            except Exception:
                continue
        page.snack_bar = ft.SnackBar(
            content=ft.Text(
                tr(
                    "snack_saved_n_marked",
                    n=n_ok,
                    total=len(thumb_selected),
                    folder=folder,
                )
            )
        )
        page.snack_bar.open = True
        page.update()

    async def _open_folder_async(extend: bool = True) -> None:
        if page.web:
            page.snack_bar = ft.SnackBar(
                content=ft.Text(tr("snack_web_use_select"))
            )
            page.snack_bar.open = True
            page.update()
            return
        # Dar tempo ao runtime Flutter para associar o listener do serviço
        await asyncio.sleep(0.15)
        try:
            path = await file_picker.get_directory_path(
                dialog_title=tr("dialog_folder_images")
            )
        except Exception as ex:
            if "session closed" in str(ex).lower():
                return
            if "timeout" in str(ex).lower():
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(tr("snack_timeout_folder"))
                )
                page.snack_bar.open = True
                page.update()
                return
            path = None
        if not path:
            return
        _show_gallery_loading(tr("loading_gallery_folder"))
        try:
            await asyncio.sleep(0)
            imgs = collect_images_from_dir(Path(path))
            if not imgs:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(tr("snack_no_images_folder"))
                )
                page.snack_bar.open = True
                page.update()
                return
            root_key = str(Path(path).resolve())
            paths_str = [str(p) for p in imgs]
            rebuild_gallery(
                paths_str,
                extend=extend,
                group_roots=[root_key] * len(paths_str),
            )
        finally:
            _hide_gallery_loading()

    async def _pick_files_async(extend: bool = True) -> None:
        nonlocal _picker_temp_paths
        await asyncio.sleep(0.15)
        try:
            # IMAGE costuma ser mais fiável que CUSTOM em iOS/macOS.
            # with_data=True garante bytes quando a plataforma não devolve `path`.
            files = await file_picker.pick_files(
                dialog_title=tr("dialog_pick_images"),
                allow_multiple=True,
                file_type=ft.FilePickerFileType.IMAGE,
                with_data=True,
            )
        except Exception as ex:
            if "session closed" in str(ex).lower():
                return
            if "timeout" in str(ex).lower():
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(tr("snack_timeout_picker"))
                )
                page.snack_bar.open = True
                page.update()
                return
            raise
        if not files:
            return
        _show_gallery_loading(tr("loading_gallery_images"))
        try:
            await asyncio.sleep(0)
            paths: list[str] = []
            for f in files:
                p = getattr(f, "path", None)
                if p:
                    paths.append(str(p))
                    continue
                raw = getattr(f, "bytes", None)
                if raw:
                    name = getattr(f, "name", "") or tr("picker_default_name")
                    sfx = (Path(str(name)).suffix or ".jpg").lower()
                    if sfx not in IMAGE_EXTENSIONS:
                        sfx = ".jpg"
                    fd, tmp_path = tempfile.mkstemp(prefix="edimg_", suffix=sfx)
                    try:
                        os.write(fd, raw)
                    finally:
                        os.close(fd)
                    paths.append(tmp_path)
                    _picker_temp_paths.append(tmp_path)
            if not paths:
                page.snack_bar = ft.SnackBar(
                    content=ft.Text(tr("snack_files_unreadable"))
                )
                page.snack_bar.open = True
                page.update()
                return
            gr = [str(Path(p).resolve().parent) for p in paths]
            rebuild_gallery(paths, extend=extend, group_roots=gr)
        finally:
            _hide_gallery_loading()

    async def _new_gallery_from_folder_async() -> None:
        await _open_folder_async(extend=False)

    async def _new_gallery_from_files_async() -> None:
        await _pick_files_async(extend=False)

    def pick_folder_click(_: ft.ControlEvent) -> None:
        page.run_task(_open_folder_async)

    def pick_files_click(_: ft.ControlEvent) -> None:
        page.run_task(_pick_files_async)

    def clear_gallery_click(_: ft.ControlEvent) -> None:
        rebuild_gallery([], extend=False)

    def open_about_dialog(_: ft.ControlEvent) -> None:
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "Ed Image Preview",
                size=20,
                weight=ft.FontWeight.W_600,
            ),
            content=ft.Container(
                width=400,
                padding=ft.Padding.only(top=4, bottom=8),
                content=ft.Text(
                    tr("about_body"),
                    size=14,
                    height=1.45,
                ),
            ),
            actions=[
                ft.TextButton(
                    tr("about_ok"),
                    on_click=lambda _e: _close_about(),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        def _close_about() -> None:
            dlg.open = False
            page.update()

        page.dialog = dlg
        dlg.open = True
        page.update()

    _menu_file = ft.PopupMenuButton(
        icon=ft.Icons.FOLDER,
        tooltip=tr("menu_file"),
        menu_position=ft.PopupMenuPosition.UNDER,
        items=[
            ft.PopupMenuItem(
                content=tr("menu_item_add_folder"),
                icon=ft.Icons.FOLDER_OPEN,
                on_click=lambda _: pick_folder_click(_),
            ),
            ft.PopupMenuItem(
                content=tr("menu_item_add_images"),
                icon=ft.Icons.ADD_PHOTO_ALTERNATE,
                on_click=lambda _: pick_files_click(_),
            ),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(
                content=tr("menu_item_clear"),
                icon=ft.Icons.DELETE_OUTLINE,
                on_click=lambda _: clear_gallery_click(_),
            ),
        ],
    )
    _menu_export = ft.PopupMenuButton(
        icon=ft.Icons.SAVE_OUTLINED,
        tooltip=tr("menu_export"),
        menu_position=ft.PopupMenuPosition.UNDER,
        items=[
            ft.PopupMenuItem(
                content=tr("menu_item_save_current"),
                icon=ft.Icons.SAVE_AS,
                on_click=lambda _: page.run_task(_save_current_with_strip_async),
            ),
            ft.PopupMenuItem(
                content=tr("menu_item_save_marked"),
                icon=ft.Icons.LIBRARY_ADD_CHECK,
                on_click=lambda _: page.run_task(_save_selected_with_strip_async),
            ),
            ft.PopupMenuItem(
                content=tr("menu_item_save_all"),
                icon=ft.Icons.FOLDER_SPECIAL,
                on_click=lambda _: page.run_task(_save_all_with_strip_async),
            ),
        ],
    )
    _menu_help = ft.PopupMenuButton(
        icon=ft.Icons.HELP_OUTLINE,
        tooltip=tr("menu_help"),
        menu_position=ft.PopupMenuPosition.UNDER,
        items=[
            ft.PopupMenuItem(
                content=tr("menu_item_about"),
                icon=ft.Icons.INFO_OUTLINE,
                on_click=open_about_dialog,
            ),
        ],
    )

    btn_new_gallery = ft.PopupMenuButton(
        icon=ft.Icons.AUTO_AWESOME_MOSAIC,
        tooltip=tr("tooltip_new_gallery"),
        menu_position=ft.PopupMenuPosition.UNDER,
        items=[
            ft.PopupMenuItem(
                content=tr("menu_new_gallery_folder"),
                icon=ft.Icons.FOLDER_OPEN,
                on_click=lambda _: page.run_task(_new_gallery_from_folder_async),
            ),
            ft.PopupMenuItem(
                content=tr("menu_new_gallery_files"),
                icon=ft.Icons.ADD_PHOTO_ALTERNATE,
                on_click=lambda _: page.run_task(_new_gallery_from_files_async),
            ),
        ],
    )

    menus_row = ft.Row(
        [_menu_file, _menu_export, _menu_help, btn_new_gallery],
        spacing=2,
        visible=True,
        wrap=True,
    )

    _tile_title_style = ft.TextStyle(size=13, weight=ft.FontWeight.W_500)

    exif_toolbar_inner = ft.Container(
        visible=True,
        padding=ft.Padding.symmetric(horizontal=0, vertical=2),
        content=ft.Column(
            tight=True,
            spacing=2,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            expand=True,
                            padding=ft.Padding.only(right=4),
                            content=status,
                        ),
                        ft.Row(
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Container(
                                    width=1,
                                    height=18,
                                    bgcolor=ft.Colors.with_opacity(
                                        0.2, ft.Colors.BLACK
                                    ),
                                ),
                                show_thumbs,
                            ],
                        ),
                    ],
                ),
                ft.ExpansionTile(
                    expanded=False,
                    dense=True,
                    tile_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                    controls_padding=ft.Padding.only(left=8, right=4, bottom=6),
                    title=ft.Text(
                        tr("expansion_toggles"),
                        style=_tile_title_style,
                    ),
                    controls=[
                        ft.Column(
                            tight=True,
                            spacing=0,
                            controls=[
                                exif_show_strip,
                                exif_sw_soft,
                                exif_sw_date,
                                exif_sw_mode,
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.END,
                                    controls=[
                                        ft.IconButton(
                                            icon=ft.Icons.VERTICAL_ALIGN_BOTTOM,
                                            tooltip=tr("tooltip_reset_strip"),
                                            on_click=reset_exif_strip_pos,
                                            icon_size=18,
                                            style=ft.ButtonStyle(padding=2),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                ft.ExpansionTile(
                    expanded=False,
                    dense=True,
                    tile_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                    controls_padding=ft.Padding.only(left=8, right=4, bottom=6),
                    title=ft.Text(
                        tr("expansion_placement"),
                        style=_tile_title_style,
                    ),
                    controls=[dd_exif_placement],
                ),
                ft.ExpansionTile(
                    expanded=False,
                    dense=True,
                    tile_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                    controls_padding=ft.Padding.only(left=8, right=4, bottom=6),
                    title=ft.Text(
                        tr("expansion_opacity"),
                        style=_tile_title_style,
                    ),
                    controls=[
                        ft.Column(
                            tight=True,
                            spacing=4,
                            controls=[
                                ft.Text(
                                    tr("label_opacity_colon"),
                                    size=11,
                                    opacity=0.72,
                                ),
                                exif_opacity_slider,
                            ],
                        ),
                    ],
                ),
            ],
        ),
    )

    thumb_block = ft.Container(
        visible=True,
        content=thumb_strip,
    )

    sidebar_toggle_btn = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT,
        tooltip=tr("tooltip_sidebar_close"),
        icon_size=22,
        style=ft.ButtonStyle(padding=4),
    )

    left_sidebar = ft.Container(
        width=SIDEBAR_W_EXPANDED,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.Border.only(
            right=ft.BorderSide(
                1, ft.Colors.with_opacity(0.12, ft.Colors.BLACK)
            )
        ),
        padding=ft.Padding.symmetric(horizontal=2, vertical=4),
        content=ft.Column(
            expand=True,
            spacing=0,
            tight=True,
            controls=[
                ft.Row(
                    [sidebar_toggle_btn, menus_row],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    expand=True,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    content=ft.Column(
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                        spacing=6,
                        tight=True,
                        controls=[
                            exif_toolbar_inner,
                            thumb_block,
                        ],
                    ),
                ),
            ],
        ),
    )

    def toggle_sidebar(_: ft.ControlEvent) -> None:
        sidebar_open[0] = not sidebar_open[0]
        o = sidebar_open[0]
        left_sidebar.width = SIDEBAR_W_EXPANDED if o else SIDEBAR_W_COLLAPSED
        menus_row.visible = o
        exif_toolbar_inner.visible = o
        thumb_block.visible = o and bool(show_thumbs.value)
        sidebar_toggle_btn.icon = (
            ft.Icons.CHEVRON_LEFT if o else ft.Icons.CHEVRON_RIGHT
        )
        sidebar_toggle_btn.tooltip = (
            tr("tooltip_sidebar_close") if o else tr("tooltip_sidebar_open")
        )
        _apply_exif_strip_positions()
        page.update()

    sidebar_toggle_btn.on_click = toggle_sidebar

    def on_toggle_thumbs(_: ft.ControlEvent) -> None:
        thumb_strip.visible = show_thumbs.value
        thumb_block.visible = sidebar_open[0] and bool(show_thumbs.value)
        page.update()

    show_thumbs.on_change = on_toggle_thumbs

    def on_page_resize(_: ft.PageResizeEvent) -> None:
        _apply_exif_strip_positions()
        page.update()

    page.on_resize = on_page_resize

    page.add(
        ft.SafeArea(
            expand=True,
            content=ft.Row(
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    left_sidebar,
                    ft.Container(
                        expand=True,
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                        bgcolor=ft.Colors.BLACK,
                        content=ft.Row(
                            expand=True,
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                btn_prev,
                                ft.Container(expand=True, content=page_view),
                                btn_next,
                            ],
                        ),
                    ),
                ],
            ),
        )
    )

    _open_with = _image_paths_from_argv()
    if _open_with:
        rebuild_gallery([str(p) for p in _open_with], extend=False)


if __name__ == "__main__":
    ft.run(main, assets_dir=str(Path(__file__).resolve().parent / "assets"))
