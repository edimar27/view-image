"""UI strings and locale detection (macOS primary language + POSIX env fallbacks)."""

from __future__ import annotations

import os
import re
import subprocess
import sys

_LANG: str | None = None

_MESSAGES: dict[str, dict[str, str]] = {
    "pt": {
        "status_add_hint": "Adicione pastas ou ficheiros (imagens); use limpar para recomeçar.",
        "label_thumbnails": "Miniaturas",
        "thumb_strip_hint": (
            "Miniaturas — toque na foto para o slide; ícone à direita "
            "marca para guardar várias com tarja."
        ),
        "btn_clear_marks": "Limpar marcações",
        "tooltip_prev": "Anterior (← ou seta esquerda)",
        "tooltip_next": "Seguinte (→ ou seta direita)",
        "label_software": "Software",
        "label_capture_date": "Data capt.",
        "label_mode": "Modo",
        "label_exif_strip": "Tarja EXIF",
        "strip_placement_label": "Disposição da tarja",
        "pl_auto": "Automático (formato da foto)",
        "pl_h_bottom": "Horizontal — fundo (centro)",
        "pl_h_bottom_left": "Horizontal — fundo esquerdo",
        "pl_h_bottom_right": "Horizontal — fundo direito",
        "pl_h_top": "Horizontal — topo (centro)",
        "pl_h_top_left": "Horizontal — topo esquerdo",
        "pl_h_top_right": "Horizontal — topo direito",
        "pl_h_center": "Horizontal — meio",
        "pl_v_left": "Vertical — esquerda (centro)",
        "pl_v_left_bottom": "Vertical — esquerda inferior",
        "pl_v_left_top": "Vertical — esquerda superior",
        "pl_v_right": "Vertical — direita (centro)",
        "pl_v_right_bottom": "Vertical — direita inferior",
        "pl_v_right_top": "Vertical — direita superior",
        "label_opacity": "Opacidade",
        "label_opacity_colon": "Opacidade:",
        "exif_drag_tooltip": (
            "Arraste para mover; canto inferior direito para largura. "
            "Opacidade e disposição (horizontal/vertical) afetam também a exportação."
        ),
        "tooltip_hide_strip": "Ocultar tarja (ligue «Tarja EXIF» para voltar a mostrar)",
        "tooltip_thumb_mark": "Marcar / desmarcar para guardar com tarja (várias)",
        "tooltip_reset_strip": "Repor posição da tarja (consoante a disposição escolhida)",
        "snack_web_save": "Guardar com tarja só no ambiente de trabalho (não no modo web).",
        "snack_file_inaccessible_save": "Ficheiro inacessível; não é possível guardar.",
        "snack_install_pillow": "Instale Pillow (requirements.txt) para guardar com tarja.",
        "dialog_save_exif": "Guardar imagem com tarja EXIF",
        "snack_dialog_error": "Erro ao abrir o diálogo: {ex}",
        "snack_saved": "Guardado: {name}",
        "snack_save_error": "Erro ao guardar: {ex}",
        "dialog_folder_copies": "Pasta onde guardar cópias com tarja EXIF",
        "snack_folder_error": "Erro ao escolher pasta: {ex}",
        "snack_saved_n": "Guardadas {n} imagem(ns) em {folder}",
        "snack_mark_thumbnails": (
            "Marque miniaturas com o ícone da caixa (canto superior direito)."
        ),
        "dialog_folder_marked": "Pasta onde guardar as miniaturas marcadas com tarja EXIF",
        "snack_saved_n_marked": "Guardadas {n} de {total} marcada(s) em {folder}",
        "snack_web_use_select": "No modo web, use «Selecionar imagens».",
        "dialog_folder_images": "Pasta com imagens",
        "snack_timeout_folder": (
            "Tempo esgotado ao abrir a pasta. Tente de novo ou use «Selecionar imagens»."
        ),
        "snack_no_images_folder": "Nenhuma imagem nessa pasta.",
        "dialog_pick_images": "Selecionar imagens",
        "snack_timeout_picker": (
            "Tempo esgotado ao abrir o seletor. Atualize o Flet, "
            "reinicie a app ou tente outra vez."
        ),
        "snack_files_unreadable": (
            "Não foi possível ler os ficheiros (sem caminho nem dados). "
            "Tente «Adicionar pasta» ou atualize o Flet."
        ),
        "tooltip_add_folder": "Adicionar pasta à galeria (várias pastas permitidas)",
        "tooltip_add_images": "Adicionar imagens à galeria",
        "tooltip_clear": "Limpar galeria",
        "tooltip_save_current": "Guardar o slide atual com tarja EXIF (como no ecrã)…",
        "tooltip_save_marked": (
            "Guardar só as miniaturas marcadas (caixa) com tarja numa pasta…"
        ),
        "tooltip_save_all": (
            "Guardar toda a galeria com tarja numa pasta (ignora marcações)…"
        ),
        "status_no_images": "Nenhuma imagem encontrada.",
        "status_n_images": "{n} imagem(ns)",
        "exif_file_inaccessible": "Ficheiro inacessível",
        "picker_default_name": "imagem",
        "menu_file": "Ficheiro",
        "menu_export": "Exportar",
        "menu_help": "Ajuda",
        "menu_item_add_folder": "Adicionar pasta…",
        "menu_item_add_images": "Adicionar imagens…",
        "menu_item_clear": "Limpar galeria",
        "tooltip_new_gallery": "Nova galeria (substitui as imagens atuais)",
        "menu_new_gallery_folder": "Carregar pasta…",
        "menu_new_gallery_files": "Carregar imagens…",
        "loading_gallery_folder": "A carregar imagens da pasta…",
        "loading_gallery_images": "A carregar imagens selecionadas…",
        "menu_item_save_current": "Guardar slide atual com tarja…",
        "menu_item_save_marked": "Guardar miniaturas marcadas…",
        "menu_item_save_all": "Guardar toda a galeria…",
        "menu_item_about": "Acerca do Ed Image Preview",
        "about_body": (
            "Visualizador de imagens com tarja EXIF integrada.\n\n"
            "© 2026 Edimar Barbosa. Todos os direitos reservados."
        ),
        "about_ok": "OK",
        "toolbar_section_exif": "Tarja EXIF",
        "toolbar_section_layout": "Disposição e opacidade",
        "exif_panel_hint": "Pré-visualização na foto e na exportação com tarja.",
        "exif_fields_label": "Campos na tarja",
        "layout_panel_hint": "Posição, tamanho e transparência da tarja no ecrã.",
        "tooltip_sidebar_open": "Mostrar painel (menu e miniaturas)",
        "tooltip_sidebar_close": "Ocultar painel",
        "expansion_toggles": "Interruptores",
        "expansion_placement": "Disposição da tarja",
        "expansion_opacity": "Opacidade da tarja",
    },
    "en": {
        "status_add_hint": "Add folders or image files; use clear to start over.",
        "label_thumbnails": "Thumbnails",
        "thumb_strip_hint": (
            "Thumbnails — tap the photo for that slide; the icon on the right "
            "marks multiple images to save with the strip."
        ),
        "btn_clear_marks": "Clear marks",
        "tooltip_prev": "Previous (← or left arrow)",
        "tooltip_next": "Next (→ or right arrow)",
        "label_software": "Software",
        "label_capture_date": "Capture date",
        "label_mode": "Mode",
        "label_exif_strip": "EXIF strip",
        "strip_placement_label": "Strip placement",
        "pl_auto": "Automatic (photo orientation)",
        "pl_h_bottom": "Horizontal — bottom (center)",
        "pl_h_bottom_left": "Horizontal — bottom left",
        "pl_h_bottom_right": "Horizontal — bottom right",
        "pl_h_top": "Horizontal — top (center)",
        "pl_h_top_left": "Horizontal — top left",
        "pl_h_top_right": "Horizontal — top right",
        "pl_h_center": "Horizontal — middle",
        "pl_v_left": "Vertical — left (center)",
        "pl_v_left_bottom": "Vertical — left bottom",
        "pl_v_left_top": "Vertical — left top",
        "pl_v_right": "Vertical — right (center)",
        "pl_v_right_bottom": "Vertical — right bottom",
        "pl_v_right_top": "Vertical — right top",
        "label_opacity": "Opacity",
        "label_opacity_colon": "Opacity:",
        "exif_drag_tooltip": (
            "Drag to move; bottom-right corner for width. "
            "Opacity and placement (horizontal/vertical) also affect export."
        ),
        "tooltip_hide_strip": "Hide strip (turn “EXIF strip” back on to show it again)",
        "tooltip_thumb_mark": "Mark / unmark to save with strip (batch)",
        "tooltip_reset_strip": "Reset strip position (for the selected placement)",
        "snack_web_save": "Saving with strip is only available on desktop (not in web mode).",
        "snack_file_inaccessible_save": "File is not accessible; cannot save.",
        "snack_install_pillow": "Install Pillow (requirements.txt) to save with strip.",
        "dialog_save_exif": "Save image with EXIF strip",
        "snack_dialog_error": "Could not open the dialog: {ex}",
        "snack_saved": "Saved: {name}",
        "snack_save_error": "Error while saving: {ex}",
        "dialog_folder_copies": "Folder for copies with EXIF strip",
        "snack_folder_error": "Error choosing folder: {ex}",
        "snack_saved_n": "Saved {n} image(s) to {folder}",
        "snack_mark_thumbnails": "Mark thumbnails with the checkbox (top-right).",
        "dialog_folder_marked": "Folder for marked thumbnails with EXIF strip",
        "snack_saved_n_marked": "Saved {n} of {total} marked to {folder}",
        "snack_web_use_select": "In web mode, use “Select images”.",
        "dialog_folder_images": "Folder with images",
        "snack_timeout_folder": (
            "Timed out opening the folder. Try again or use “Select images”."
        ),
        "snack_no_images_folder": "No images in that folder.",
        "dialog_pick_images": "Select images",
        "snack_timeout_picker": (
            "Timed out opening the file picker. Update Flet, restart the app, or try again."
        ),
        "snack_files_unreadable": (
            "Could not read the files (no path or data). "
            "Try “Add folder” or update Flet."
        ),
        "tooltip_add_folder": "Add folder to gallery (multiple folders allowed)",
        "tooltip_add_images": "Add images to gallery",
        "tooltip_clear": "Clear gallery",
        "tooltip_save_current": "Save current slide with EXIF strip (as on screen)…",
        "tooltip_save_marked": "Save only marked thumbnails with strip to a folder…",
        "tooltip_save_all": "Save entire gallery with strip to a folder (ignores marks)…",
        "status_no_images": "No images found.",
        "status_n_images": "{n} image(s)",
        "exif_file_inaccessible": "File not accessible",
        "picker_default_name": "image",
        "menu_file": "File",
        "menu_export": "Export",
        "menu_help": "Help",
        "menu_item_add_folder": "Add folder…",
        "menu_item_add_images": "Add images…",
        "menu_item_clear": "Clear gallery",
        "tooltip_new_gallery": "New gallery (replaces current images)",
        "menu_new_gallery_folder": "Load folder…",
        "menu_new_gallery_files": "Load images…",
        "loading_gallery_folder": "Loading images from folder…",
        "loading_gallery_images": "Loading selected images…",
        "menu_item_save_current": "Save current slide with strip…",
        "menu_item_save_marked": "Save marked thumbnails…",
        "menu_item_save_all": "Save entire gallery…",
        "menu_item_about": "About Ed Image Preview",
        "about_body": (
            "Image viewer with an integrated EXIF overlay strip.\n\n"
            "© 2026 Edimar Barbosa. All rights reserved."
        ),
        "about_ok": "OK",
        "toolbar_section_exif": "EXIF strip",
        "toolbar_section_layout": "Placement & opacity",
        "exif_panel_hint": "Preview on the image and on export with the strip.",
        "exif_fields_label": "Fields in strip",
        "layout_panel_hint": "Position, size, and opacity of the strip on screen.",
        "tooltip_sidebar_open": "Show panel (menu and thumbnails)",
        "tooltip_sidebar_close": "Hide panel",
        "expansion_toggles": "Switches",
        "expansion_placement": "Strip placement",
        "expansion_opacity": "Strip opacity",
    },
}


def _normalize_lang_tag(tag: str) -> str:
    return tag.replace("_", "-").strip().split("@")[0].split(".")[0].lower()


def _is_portuguese(tag: str) -> bool:
    return _normalize_lang_tag(tag).startswith("pt")


def _macos_apple_primary_language() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.check_output(
            ["defaults", "read", "-g", "AppleLanguages"],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    for m in re.finditer(r'"([^"]+)"', out):
        s = m.group(1).strip()
        if s and s != "C":
            return s
    for m in re.finditer(r"\b([a-zA-Z]{2,3}(?:-[A-Za-z0-9]+)?)\b", out):
        s = m.group(1)
        low = s.lower()
        if low in ("array", "dict", "true", "false", "none"):
            continue
        return s
    return None


def _env_language_candidates() -> list[str]:
    out: list[str] = []
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(key)
        if not val:
            continue
        for part in val.split(":"):
            part = part.strip()
            if part and part != "C":
                out.append(part)
    return out


def detect_ui_lang() -> str:
    """Return a supported UI language code: ``pt`` or ``en``."""
    t = _macos_apple_primary_language()
    if t and _is_portuguese(t):
        return "pt"
    if t:
        return "en"
    for cand in _env_language_candidates():
        if _is_portuguese(cand):
            return "pt"
    try:
        import locale

        loc = locale.getlocale(locale.LC_MESSAGES)
        if loc and loc[0] and _is_portuguese(str(loc[0])):
            return "pt"
    except (OSError, ValueError, Exception):
        pass
    return "en"


def init_app_i18n(lang: str | None = None) -> str:
    """Call once at startup; fixes the UI language for ``tr()``."""
    global _LANG
    code = (lang or detect_ui_lang()).lower()
    _LANG = "pt" if code.startswith("pt") else "en"
    return _LANG


def tr(key: str, **kwargs: object) -> str:
    """Localized string; ``kwargs`` are passed to ``str.format``."""
    lang = _LANG or detect_ui_lang()
    if lang not in _MESSAGES:
        lang = "en"
    template = _MESSAGES[lang].get(key) or _MESSAGES["en"].get(key) or key
    if kwargs:
        return template.format(**kwargs)
    return template
