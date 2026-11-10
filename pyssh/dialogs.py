"""Boîtes de dialogue : saisie de texte et édition d'une connexion."""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Optional

import customtkinter as ctk

from .constants import (ACCENT, BG, BORDER, CARD, COLORS, DEFAULT_DOT, FIELD_BG, HOVER,
                        MUTED, SSH_DIR, TEXT)
from .ui import btn_ghost, btn_primary, font, make_entry


# --------------------------------------------------------------------------- #
# Saisie simple
# --------------------------------------------------------------------------- #
class InputDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, text: str, initial: str = "", show: str = ""):
        super().__init__(parent)
        self.configure(fg_color=BG)
        self.title(title)
        self.geometry("440x190")
        self.resizable(False, False)
        self.transient(parent)
        self.result: Optional[str] = None
        ctk.CTkLabel(self, text=text, font=font(13), text_color=TEXT, anchor="w", justify="left",
                     wraplength=390).pack(fill="x", padx=26, pady=(22, 8))
        self.entry = make_entry(self, show=show)
        self.entry.pack(fill="x", padx=26)
        self.entry.insert(0, initial)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=26, pady=(18, 18))
        btn_primary(bar, "OK", self._ok, width=110).pack(side="right")
        btn_ghost(bar, "Annuler", self.destroy, width=100, height=38).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(200, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.entry.focus_set()
        except Exception:
            pass

    def _ok(self) -> None:
        self.result = self.entry.get()
        self.destroy()


def ask_text(parent, title: str, text: str, initial: str = "", show: str = "") -> Optional[str]:
    dialog = InputDialog(parent, title, text, initial, show)
    dialog.wait_window()
    return dialog.result


# --------------------------------------------------------------------------- #
# Édition d'une connexion
# --------------------------------------------------------------------------- #
class HostDialog(ctk.CTkToplevel):
    NO_GROUP = "(Sans groupe)"

    def __init__(self, app, block=None, preset_group: str = ""):
        super().__init__(app)
        self.configure(fg_color=BG)
        self.app, self.block = app, block
        title = "Modifier la connexion" if block else "Nouvelle connexion"
        self.title(title)
        self.geometry("560x740")
        self.resizable(False, False)
        self.transient(app)

        main: dict = {}
        adv: list = []
        meta: dict = {}
        if block:
            main, adv = block.split_body()
            meta = app.store.meta(block.alias)

        ctk.CTkLabel(self, text=title, font=font(20, "bold"), text_color=TEXT).pack(
            anchor="w", padx=26, pady=(22, 2))
        ctk.CTkLabel(self, text="Ces options sont écrites dans votre fichier ~/.ssh/config.",
                     font=font(12), text_color=MUTED).pack(anchor="w", padx=26, pady=(0, 6))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=BORDER,
                                        scrollbar_button_hover_color=HOVER)
        scroll.pack(fill="both", expand=True, padx=14)

        self._build_identity(scroll, block, meta)
        self._build_connection(scroll, main)
        self._build_organisation(scroll, meta, preset_group)
        self._build_advanced(scroll, adv)

        self.lbl_err = ctk.CTkLabel(self, text="", text_color="#f87171", wraplength=500, font=font(12))
        self.lbl_err.pack(pady=(4, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=26, pady=(6, 18))
        btn_primary(bar, "Enregistrer", self._save, width=150).pack(side="right")
        btn_ghost(bar, "Annuler", self.destroy, width=100, height=38).pack(side="right", padx=(0, 8))

        self.after(200, self._grab)

    # ------------------------------------------------------------------ #
    # Construction du formulaire
    # ------------------------------------------------------------------ #
    def _build_identity(self, parent, block, meta: dict) -> None:
        self._section(parent, "IDENTITÉ")
        self.e_label = self._field(parent, "Nom d'affichage", meta.get("label", ""),
                                   "Ex : Serveur web production")
        self.e_alias = self._field(parent, "Alias SSH  (utilisé par « ssh alias »)",
                                   block.alias if block else "")

    def _build_connection(self, parent, main: dict) -> None:
        self._section(parent, "CONNEXION")
        self.e_host = self._field(parent, "HostName  (IP ou nom DNS)", main.get("HostName", ""))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12)
        row.columnconfigure(0, weight=3)
        row.columnconfigure(1, weight=1)
        self.e_user = self._field(row, "User", main.get("User", ""), grid=(0, 0, (0, 8)))
        self.e_port = self._field(row, "Port", main.get("Port", ""), "22", grid=(0, 1, (0, 0)))

        self._label(parent, "IdentityFile  (clé privée)")
        key_row = ctk.CTkFrame(parent, fg_color="transparent")
        key_row.pack(fill="x", padx=12)
        self.e_key = make_entry(key_row)
        self.e_key.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.e_key.insert(0, main.get("IdentityFile", ""))
        btn_ghost(key_row, "Parcourir…", self._browse_key, width=100).pack(side="left")
        self.e_proxy = self._field(parent, "ProxyJump  (rebond)", main.get("ProxyJump", ""), "ex : bastion")

    def _build_organisation(self, parent, meta: dict, preset_group: str) -> None:
        store = self.app.store
        self._section(parent, "ORGANISATION")
        self._label(parent, "Groupe")
        self.group_values = {store.group_label(path): path for path in store.groups}
        self.cb_group = ctk.CTkComboBox(
            parent, values=[self.NO_GROUP] + list(self.group_values), fg_color=FIELD_BG,
            border_color=BORDER, button_color=CARD, button_hover_color=HOVER,
            dropdown_fg_color=CARD, dropdown_hover_color=HOVER, dropdown_text_color=TEXT,
            dropdown_font=font(12), corner_radius=8, height=34, font=font(12),
            text_color=TEXT, state="readonly")
        self.cb_group.pack(fill="x", padx=12)
        selected_group = meta.get("group") or preset_group
        self.cb_group.set(store.group_label(selected_group) if selected_group else self.NO_GROUP)

        self._label(parent, "Couleur")
        self.color = meta.get("color", "")
        color_row = ctk.CTkFrame(parent, fg_color="transparent")
        color_row.pack(fill="x", padx=12)
        self.swatches: dict = {}
        for hexv in COLORS.values():
            button = ctk.CTkButton(color_row, text="", width=28, height=28, corner_radius=14,
                                   fg_color=hexv or DEFAULT_DOT, hover_color=hexv or "#6b7280",
                                   text_color="#0d1016", font=font(13, "bold"),
                                   command=lambda h=hexv: self._pick_color(h))
            button.pack(side="left", padx=(0, 6))
            self.swatches[hexv] = button
        self._pick_color(self.color)

        self.var_fav = ctk.BooleanVar(value=bool(meta.get("favorite")))
        ctk.CTkSwitch(parent, text="Ajouter aux favoris ★", variable=self.var_fav,
                      progress_color=ACCENT, font=font(12), text_color=TEXT).pack(
            anchor="w", padx=14, pady=(14, 2))
        self.e_note = self._field(parent, "Note", meta.get("note", ""))

    def _build_advanced(self, parent, adv: list) -> None:
        self._section(parent, "AVANCÉ")
        self._label(parent, "Autres options (lignes ssh_config brutes)")
        self.t_adv = ctk.CTkTextbox(parent, height=100, font=("Consolas", 12), fg_color=FIELD_BG,
                                    border_color=BORDER, border_width=1, corner_radius=8)
        self.t_adv.pack(fill="x", padx=12, pady=(0, 10))
        self.t_adv.insert("1.0", "\n".join(adv))

    # ------------------------------------------------------------------ #
    # Helpers de formulaire
    # ------------------------------------------------------------------ #
    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus()
        except Exception:
            pass

    @staticmethod
    def _section(parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=font(10, "bold"), text_color=ACCENT, anchor="w").pack(
            fill="x", padx=14, pady=(16, 0))

    @staticmethod
    def _label(parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, anchor="w", font=font(12), text_color=MUTED).pack(
            fill="x", padx=14, pady=(8, 3))

    def _field(self, parent, label: str, value: str = "", placeholder: str = "", grid=None):
        if grid is None:
            self._label(parent, label)
            entry = make_entry(parent, placeholder_text=placeholder)
            entry.pack(fill="x", padx=12)
        else:
            column = ctk.CTkFrame(parent, fg_color="transparent")
            column.grid(row=grid[0], column=grid[1], sticky="ew", padx=grid[2])
            self._label(column, label)
            entry = make_entry(column, placeholder_text=placeholder)
            entry.pack(fill="x", padx=(2, 2))
        if value:
            entry.insert(0, value)
        return entry

    def _browse_key(self) -> None:
        path = filedialog.askopenfilename(parent=self, initialdir=SSH_DIR if SSH_DIR.exists() else Path.home(),
                                          title="Sélectionner la clé privée")
        if path:
            self.e_key.delete(0, "end")
            self.e_key.insert(0, path.replace("\\", "/"))

    def _pick_color(self, hexv: str) -> None:
        self.color = hexv
        for h, button in self.swatches.items():
            button.configure(text="✓" if h == hexv else "")

    # ------------------------------------------------------------------ #
    # Enregistrement
    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        group_value = self.cb_group.get().strip()
        group = "" if group_value in ("", self.NO_GROUP) else self.group_values.get(group_value, group_value)
        adv_raw = self.t_adv.get("1.0", "end").rstrip("\n")
        data = {
            "alias": self.e_alias.get().strip(),
            "label": self.e_label.get().strip(),
            "main": {"HostName": self.e_host.get().strip(), "User": self.e_user.get().strip(),
                     "Port": self.e_port.get().strip(), "IdentityFile": self.e_key.get().strip(),
                     "ProxyJump": self.e_proxy.get().strip()},
            "adv": adv_raw.split("\n") if adv_raw.strip() else [],
            "group": group,
            "color": self.color,
            "note": self.e_note.get().strip(),
            "favorite": self.var_fav.get(),
        }
        error = self.app.save_host(self.block, data)
        if error:
            self.lbl_err.configure(text=error)
        else:
            self.destroy()
