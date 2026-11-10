"""Fabriques de widgets CustomTkinter partagées par toute l'interface."""
from __future__ import annotations

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from .constants import ACCENT, BORDER, CARD, FIELD_BG, HOVER, TEXT


def font(size: int = 12, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def btn_primary(parent, text: str, command, **kw) -> ctk.CTkButton:
    opts = dict(fg_color=ACCENT, hover_color=ACCENT, text_color="white",
                corner_radius=8, height=38, font=font(13, "bold"))
    opts.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def btn_ghost(parent, text: str, command, **kw) -> ctk.CTkButton:
    opts = dict(fg_color=CARD, hover_color=HOVER, text_color=TEXT, border_width=1,
                border_color=BORDER, corner_radius=8, height=34, font=font(12))
    opts.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def make_entry(parent, **kw) -> ctk.CTkEntry:
    opts = dict(fg_color=FIELD_BG, border_color=BORDER, border_width=1,
                corner_radius=8, height=34, text_color=TEXT, font=font(12))
    opts.update(kw)
    return ctk.CTkEntry(parent, **opts)


def make_dot(color: str, size: int = 16) -> ImageTk.PhotoImage:
    """Pastille de couleur anti-aliasée (dessinée en 4x puis réduite)."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pad = int(big * 0.28)
    ImageDraw.Draw(img).ellipse((pad, pad, big - pad - 1, big - pad - 1), fill=color)
    return ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
