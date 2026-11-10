"""Fenêtre principale de PySSH : liste des connexions, onglets de terminal, menus."""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import tkinter as tk
import uuid
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional
from urllib.parse import quote

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from .console import EmbeddedConsole
from .constants import (ACCENT, APP_NAME, BG, BORDER, CARD, CONFIG_PATH, DANGER, DANGER_H,
                        DEFAULT_DOT, GROUP_SEPARATOR, HOVER, IS_WIN, MUTED, PANEL,
                        REMOTE_DEPLOY_CMD, SELECT_BG, SIDE, SSH_DIR, TERM_BG, TEXT)
from .dialogs import HostDialog
from .sftp_panel import SftpPanel
from .ssh_config import SSHConfig
from .store import Store
from .ui import btn_ghost, btn_primary, font, make_dot, make_entry
from .utils import bat_quote, resource_path
from .win32 import user32

CONFIG_POLL_MS = 2500
SESSION_POLL_MS = 800
POINTER_POLL_MS = 50
MENU_ITEM_HEIGHT, MENU_SEPARATOR_HEIGHT = 38, 9


class App(ctk.CTk):
    HOME_TAB = "Accueil"
    FAV = "fav"
    FAV_KEY = "__fav__"

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(fg_color=BG)
        self.geometry("1360x800")
        self.minsize(1000, 600)
        try:
            self.iconbitmap(resource_path("logo.ico"))
        except Exception:
            pass

        self.store = Store()
        self.cfg = SSHConfig()
        self._stamp = self.cfg.stamp()
        self.sessions: dict[str, EmbeddedConsole] = {}
        self.remotes: dict[str, tuple[str, str]] = {}
        self.sftp_panels: dict[str, SftpPanel] = {}
        self._active_dropdown: Optional[ctk.CTkFrame] = None
        self._active_submenu: Optional[ctk.CTkFrame] = None
        self._active_submenu_anchor = None
        self.closed_groups: set[str] = set()
        self._gtext: dict[str, str] = {}
        self._gpath: dict[str, str] = {}
        self._drag: Optional[dict] = None
        self._dots: dict[str, ImageTk.PhotoImage] = {}

        self.bind_all("<ButtonPress-1>", self._dismiss_dropdown, add="+")
        self._build_sidebar()
        self._build_main()
        self.refresh_tree()
        self._build_home()

        self.bind("<Control-f>", lambda e: self.e_search.focus_set())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if IS_WIN:
            self.after(SESSION_POLL_MS, self._poll_sessions)
            self.after(POINTER_POLL_MS, self._pointer_poll)
        self.after(CONFIG_POLL_MS, self._poll_config)

    def _dot(self, hexv: str) -> ImageTk.PhotoImage:
        hexv = hexv or DEFAULT_DOT
        if hexv not in self._dots:
            self._dots[hexv] = make_dot(hexv)
        return self._dots[hexv]

    # ================================================================== #
    # Construction de l'interface
    # ================================================================== #
    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, width=340, corner_radius=0, fg_color=SIDE)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(18, 12))
        ctk.CTkLabel(brand, text="PySSH", font=font(16, "bold"), text_color=ACCENT).pack(anchor="w")
        ctk.CTkLabel(brand, text="Gestionnaire de connexions SSH", font=font(9, "bold"),
                     text_color=MUTED).pack(anchor="w", pady=(1, 0))

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        self.e_search = make_entry(side, textvariable=self.search_var,
                                   placeholder_text="Rechercher une connexion...",
                                   height=38, corner_radius=10)
        self.e_search.pack(fill="x", padx=16, pady=(0, 10))

        btn_primary(side, "Nouvelle connexion", lambda: self.new_host()).pack(fill="x", padx=16)

        row = ctk.CTkFrame(side, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 8))
        btn_ghost(row, "Groupe", self.new_group).pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_settings = btn_ghost(row, "⚙", self._settings_menu, width=40)
        self.btn_settings.pack(side="left")

        ctk.CTkFrame(side, height=1, fg_color=BORDER).pack(fill="x", padx=16, pady=(8, 8))
        ctk.CTkLabel(side, text="SESSIONS", font=font(9, "bold"), text_color=MUTED).pack(
            anchor="w", padx=18, pady=(0, 4))

        self._configure_tree_style()
        self._build_tree(side)
        self._build_detail_card(side)
        self._bind_tree_events()

    def _configure_tree_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        normal_row = Image.new("RGBA", (100, 36), (0, 0, 0, 0))
        selected_row = Image.new("RGBA", (100, 36), (0, 0, 0, 0))
        ImageDraw.Draw(selected_row).rounded_rectangle((0, 2, 99, 33), radius=9, fill=SELECT_BG)
        self._tree_row_images = (ImageTk.PhotoImage(normal_row), ImageTk.PhotoImage(selected_row))
        style.element_create("Ssh.Treeitem.background", "image", self._tree_row_images[0],
                             ("selected", self._tree_row_images[1]), border=(10, 0, 10, 0), sticky="nswe")
        style.configure("Ssh.Treeview", background=SIDE, fieldbackground=SIDE, foreground=TEXT,
                        rowheight=36, borderwidth=0, relief="flat", font=("Segoe UI", 11),
                        selectborderwidth=0)
        style.map("Ssh.Treeview", background=[("selected", SIDE)], foreground=[("selected", "#ffffff")])
        style.layout("Ssh.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.layout("Ssh.Treeview.Item", [("Ssh.Treeitem.background", {"sticky": "nswe", "children": [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.text", {"side": "left", "sticky": ""})]})]})])

    def _build_tree(self, side) -> None:
        frame = tk.Frame(side, bg=SIDE, highlightthickness=0)
        frame.pack(fill="both", expand=True, padx=(12, 8), pady=(0, 8))
        self.tree = ttk.Treeview(frame, style="Ssh.Treeview", show="tree", selectmode="browse")
        scrollbar = ctk.CTkScrollbar(frame, command=self.tree.yview, fg_color="transparent",
                                     button_color=BORDER, button_hover_color=HOVER, width=10)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 2))
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("group", font=("Segoe UI", 10, "bold"), foreground=ACCENT)

    def _build_detail_card(self, side) -> None:
        card = ctk.CTkFrame(side, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=14, pady=(0, 16))
        self.d_title = ctk.CTkLabel(card, text="Aucune sélection", font=font(14, "bold"), text_color=TEXT,
                                    anchor="w", wraplength=270)
        self.d_title.pack(fill="x", padx=16, pady=(14, 0))
        self.d_info = ctk.CTkLabel(card, text="Sélectionnez une connexion dans la liste.", font=font(11),
                                   text_color=MUTED, anchor="w", justify="left", wraplength=270)
        self.d_info.pack(fill="x", padx=16, pady=(4, 10))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        self.btn_d_connect = btn_primary(buttons, "Connecter", self._connect_selected,
                                         height=34, state="disabled")
        self.btn_d_connect.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_d_edit = btn_ghost(buttons, "✎", self._edit_selected, width=40, state="disabled")
        self.btn_d_edit.pack(side="left")

    def _bind_tree_events(self) -> None:
        tree = self.tree
        tree.bind("<<TreeviewSelect>>", self._on_select)
        tree.bind("<<TreeviewOpen>>", lambda e: self._sync_group(tree.focus()))
        tree.bind("<<TreeviewClose>>", lambda e: self._sync_group(tree.focus()))
        tree.bind("<Double-1>", self._on_double)
        tree.bind("<Button-3>", self._on_right_click)
        tree.bind("<Return>", lambda e: self._connect_selected())
        tree.bind("<F2>", lambda e: self.rename_selected())
        tree.bind("<Delete>", lambda e: self.delete_selected())
        tree.bind("<ButtonPress-1>", self._dnd_press, add="+")
        tree.bind("<B1-Motion>", self._dnd_motion, add="+")
        tree.bind("<ButtonRelease-1>", self._dnd_release, add="+")

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=(14, 18), pady=16)

        top = ctk.CTkFrame(main, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10))
        self.btn_term = btn_ghost(top, "Terminal local  ▾", self._local_terminal_menu)
        self.btn_term.pack(side="left")
        btn_ghost(top, "Fermer l'onglet", self.close_current_tab, fg_color=DANGER, hover_color=DANGER_H,
                  border_color="#ad1212").pack(side="right")
        btn_ghost(top, "Explorateur SFTP", self.toggle_sftp).pack(side="right", padx=(0, 8))

        self.tabs = ctk.CTkTabview(
            main, anchor="nw", command=self._on_tab_change, fg_color=PANEL, corner_radius=12,
            border_width=1, border_color=BORDER, segmented_button_fg_color=PANEL,
            segmented_button_selected_color=CARD, segmented_button_selected_hover_color=HOVER,
            segmented_button_unselected_color=SIDE, segmented_button_unselected_hover_color=HOVER,
            text_color=TEXT)
        self.tabs.pack(fill="both", expand=True)
        try:
            self.tabs._segmented_button.configure(font=font(12, "bold"), corner_radius=8, height=34)
        except Exception:
            pass
        self.home = self.tabs.add(self.HOME_TAB)
        self._bind_tab_close(self.HOME_TAB)

    def _bind_tab_close(self, name: str) -> None:
        """Ferme l'onglet au clic du milieu."""
        try:
            button = self.tabs._segmented_button._buttons_dict[name]
            button.bind("<Button-2>", lambda event, tab=name: self._close_tab_from_middle(event, tab), add="+")
        except (AttributeError, KeyError):
            pass

    def _close_tab_from_middle(self, event, name: str) -> str:
        if name in self.sessions:
            self._close_tab(name)
        return "break"

    def _build_home(self) -> None:
        frame = self.home
        for widget in frame.winfo_children():
            widget.destroy()
        hosts = self.cfg.hosts()
        favs = [b for b in hosts if self.store.meta(b.alias).get("favorite")]

        ctk.CTkLabel(frame, text="Vos connexions SSH", font=font(26, "bold"), text_color=TEXT).pack(
            anchor="w", padx=30, pady=(28, 0))
        ctk.CTkLabel(frame, text="Double-cliquez sur une connexion pour l'ouvrir dans un onglet.",
                     font=font(13), text_color=MUTED).pack(anchor="w", padx=30, pady=(2, 20))

        stats = ctk.CTkFrame(frame, fg_color="transparent")
        stats.pack(fill="x", padx=30)
        for i, (value, label) in enumerate([(len(hosts), "Connexions"), (len(self.store.groups), "Groupes"),
                                            (len(favs), "Favoris")]):
            card = ctk.CTkFrame(stats, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
            card.grid(row=0, column=i, padx=(0, 12), sticky="w")
            ctk.CTkLabel(card, text=str(value), font=font(26, "bold"), text_color=ACCENT).pack(
                anchor="w", padx=22, pady=(14, 0))
            ctk.CTkLabel(card, text=label, font=font(12), text_color=MUTED).pack(
                anchor="w", padx=22, pady=(0, 14))

        if favs:
            ctk.CTkLabel(frame, text="FAVORIS", font=font(10, "bold"), text_color=ACCENT).pack(
                anchor="w", padx=32, pady=(26, 8))
            grid = ctk.CTkFrame(frame, fg_color="transparent")
            grid.pack(fill="x", padx=30)
            for i in range(3):
                grid.columnconfigure(i, weight=1, uniform="fav")
            for i, block in enumerate(sorted(favs, key=lambda x: self.store.label(x.alias).lower())[:9]):
                ctk.CTkButton(grid, text=f"★   {self.store.label(block.alias)}", anchor="w", height=42,
                              fg_color=CARD, hover_color=HOVER, border_width=1, border_color=BORDER,
                              corner_radius=10, text_color=TEXT, font=font(12),
                              command=lambda a=block.alias: self.connect(a)).grid(
                    row=i // 3, column=i % 3, padx=(0, 10), pady=(0, 10), sticky="ew")

    # ================================================================== #
    # Arbre des connexions
    # ================================================================== #
    def _matches(self, block, query: str) -> bool:
        if not query:
            return True
        meta, main = self.store.meta(block.alias), block.split_body()[0]
        haystack = " ".join([block.alias, meta.get("label", ""),
                             self.store.group_label(meta.get("group", "")), meta.get("note", ""),
                             main.get("HostName", ""), main.get("User", "")]).lower()
        return query in haystack

    @staticmethod
    def _is_open(value) -> bool:
        return str(value).lower() in ("1", "true")

    def _group_key(self, iid: str) -> str:
        if iid == self.FAV:
            return self.FAV_KEY
        return self._gpath.get(iid, iid[2:])

    def _group_text(self, iid: str) -> str:
        opened = self._is_open(self.tree.item(iid, "open"))
        return f"{'▾' if opened else '▸'}   {self._gtext[iid]}"

    def _sync_group(self, iid: str) -> None:
        if iid not in self._gtext or not self.tree.exists(iid):
            return
        opened = self._is_open(self.tree.item(iid, "open"))
        (self.closed_groups.discard if opened else self.closed_groups.add)(self._group_key(iid))
        self.tree.item(iid, text=self._group_text(iid))

    def _toggle_group(self, iid: str) -> None:
        opened = self._is_open(self.tree.item(iid, "open"))
        self.tree.item(iid, open=not opened)
        self._sync_group(iid)

    def refresh_tree(self) -> None:
        tree = self.tree
        selection = tree.selection()
        tree.delete(*tree.get_children())
        self._gtext = {}
        self._gpath = {}
        query = self.search_var.get().strip().lower()
        hosts = [b for b in self.cfg.hosts() if self._matches(b, query)]
        by_group: dict[str, list] = {}
        for block in hosts:
            by_group.setdefault(self.store.meta(block.alias).get("group", ""), []).append(block)

        children: dict[str, list[str]] = {}
        counts = {path: len(by_group.get(path, [])) for path in self.store.groups}
        for path in self.store.groups:
            children.setdefault(self.store.group_parent(path), []).append(path)
        for path in sorted(self.store.groups, key=lambda item: item.count(GROUP_SEPARATOR), reverse=True):
            parent = self.store.group_parent(path)
            if parent in counts:
                counts[parent] += counts[path]

        visible_groups: set[str] = set()
        if query:
            for path, items in by_group.items():
                if items:
                    while path:
                        visible_groups.add(path)
                        path = self.store.group_parent(path)

        def sorted_by_label(blocks):
            return sorted(blocks, key=lambda x: self.store.label(x.alias).lower())

        def add_group(path: str, parent_iid: str = "") -> None:
            if query and path not in visible_groups:
                return
            iid = f"g:{quote(path, safe='')}"
            self._gpath[iid] = path
            opened = path not in self.closed_groups or bool(query)
            items = by_group.get(path, [])
            self._gtext[iid] = f"{self.store.group_name(path)}  ·  {counts.get(path, len(items))}"
            tree.insert(parent_iid, "end", iid=iid, open=opened, tags=("group",))
            tree.item(iid, text=self._group_text(iid))
            for block in sorted_by_label(items):
                color = self.store.meta(block.alias).get("color", "")
                tree.insert(iid, "end", iid=f"h:{block.alias}", text="  " + self.store.label(block.alias),
                            image=self._dot(color))
            for child in children.get(path, []):
                add_group(child, iid)

        favs = [b for b in hosts if self.store.meta(b.alias).get("favorite")]
        if favs:
            iid = self.FAV
            self._gtext[iid] = f"★ Favoris  ·  {len(favs)}"
            tree.insert("", "end", iid=iid, open=self.FAV_KEY not in self.closed_groups, tags=("group",))
            tree.item(iid, text=self._group_text(iid))
            for block in sorted_by_label(favs):
                tree.insert(iid, "end", iid=f"f:{block.alias}", text="  " + self.store.label(block.alias),
                            image=self._dot(self.store.meta(block.alias).get("color", "")))
        for path in children.get("", []):
            add_group(path)
        if by_group.get(""):
            iid = "g:"
            self._gtext[iid] = f"Sans groupe  ·  {len(by_group[''])}"
            tree.insert("", "end", iid=iid, open=True, tags=("group",))
            tree.item(iid, text=self._group_text(iid))
            for block in sorted_by_label(by_group[""]):
                tree.insert(iid, "end", iid=f"h:{block.alias}", text="  " + self.store.label(block.alias),
                            image=self._dot(self.store.meta(block.alias).get("color", "")))

        for item in selection:
            if tree.exists(item):
                tree.selection_set(item)
        self._on_select()

    @staticmethod
    def _alias_of(iid: str) -> Optional[str]:
        return iid[2:] if iid[:2] in ("h:", "f:") else None

    def _selected(self) -> str:
        selection = self.tree.selection()
        return selection[0] if selection else ""

    def _on_select(self, _event=None) -> None:
        iid = self._selected()
        alias = self._alias_of(iid) if iid else None
        block = self.cfg.find(alias) if alias else None
        if not block:
            self.d_title.configure(text="Aucune sélection")
            self.d_info.configure(text="Sélectionnez une connexion dans la liste.")
            self.btn_d_connect.configure(state="disabled")
            self.btn_d_edit.configure(state="disabled")
            return
        main, meta = block.split_body()[0], self.store.meta(alias)
        lines = [f"{main.get('User', '?')}@{main.get('HostName', alias)}:{main.get('Port', '22')}"]
        if alias != self.store.label(alias):
            lines.insert(0, f"alias : {alias}")
        if main.get("IdentityFile"):
            lines.append("clé : " + main["IdentityFile"])
        if main.get("ProxyJump"):
            lines.append("via : " + main["ProxyJump"])
        if meta.get("note"):
            lines.append("📝 " + meta["note"])
        self.d_title.configure(text=self.store.label(alias))
        self.d_info.configure(text="\n".join(lines))
        self.btn_d_connect.configure(state="normal")
        self.btn_d_edit.configure(state="normal")

    def _after_change(self) -> None:
        self._stamp = self.cfg.stamp()
        self.store.save()
        self.refresh_tree()
        self._build_home()

    def _poll_config(self) -> None:
        """Recharge la configuration si un fichier ssh a été modifié depuis l'extérieur."""
        try:
            if self.cfg.stamp() != self._stamp:
                self.cfg.load()
                self._stamp = self.cfg.stamp()
                self.refresh_tree()
                self._build_home()
        finally:
            self.after(CONFIG_POLL_MS, self._poll_config)

    # ================================================================== #
    # Gestion des connexions
    # ================================================================== #
    def new_host(self, group: str = "") -> None:
        HostDialog(self, None, group)

    def edit_host(self, alias: str) -> None:
        block = self.cfg.find(alias)
        if block:
            HostDialog(self, block)

    def _edit_selected(self) -> None:
        alias = self._alias_of(self._selected())
        if alias:
            self.edit_host(alias)

    def save_host(self, block, data: dict) -> Optional[str]:
        """Valide et enregistre une connexion. Retourne un message d'erreur, ou None."""
        alias = data["alias"]
        if not re.fullmatch(r"[\w.@:+\-]+", alias):
            return "Alias invalide (lettres, chiffres, . _ - @ : + uniquement, sans espace ni joker)."
        if any(b is not block and alias in b.patterns for b in self.cfg.hosts()):
            return "Cet alias existe déjà dans votre configuration."
        port = data["main"].get("Port", "")
        if port and not port.isdigit():
            return "Le port doit être un nombre."
        is_new = block is None
        old = None if is_new else block.alias
        try:
            if is_new:
                block = self.cfg.new_block(alias)
            patterns = [alias] + ([] if is_new else block.patterns[1:])
            block.update(patterns, data["main"], data["adv"])
            block.file.save()
        except OSError as e:
            return f"Impossible d'écrire le fichier config : {e}"
        if old and old != alias:
            self.store.rename_alias(old, alias)
        meta = self.store.meta(alias)
        meta.update(label=data["label"], group=data["group"], color=data["color"],
                    note=data["note"], favorite=data["favorite"])
        if data["group"] and data["group"] not in self.store.groups:
            self.store.ensure_group(data["group"])
        self._after_change()
        return None

    def duplicate_host(self, alias: str) -> None:
        block = self.cfg.find(alias)
        if not block:
            return
        new = alias + "-copie"
        n = 2
        while self.cfg.find(new):
            new, n = f"{alias}-copie{n}", n + 1
        main, adv = block.split_body()
        new_block = self.cfg.new_block(new)
        new_block.update([new], main, adv)
        new_block.file.save()
        self.store.data["hosts"][new] = dict(self.store.meta(alias))
        self.store.meta(new)["label"] = self.store.label(alias) + " (copie)"
        self._after_change()

    def delete_host(self, alias: str) -> None:
        block = self.cfg.find(alias)
        if block and messagebox.askyesno(APP_NAME, f"Supprimer la connexion « {self.store.label(alias)} » "
                                                   f"du fichier config ?\n(une sauvegarde .innssh.bak est conservée)"):
            try:
                self.cfg.remove(block)
            except OSError as e:
                return messagebox.showerror(APP_NAME, str(e))
            self.store.data["hosts"].pop(alias, None)
            self._after_change()

    def rename_host(self, alias: str) -> None:
        dialog = ctk.CTkInputDialog(text=f"Nouveau nom d'affichage pour « {self.store.label(alias)} »\n"
                                         f"(vide = utiliser l'alias « {alias} ») :", title="Renommer")
        value = dialog.get_input()
        if value is not None:
            self.store.meta(alias)["label"] = value.strip()
            self._after_change()

    def rename_selected(self) -> None:
        iid = self._selected()
        if self._alias_of(iid):
            self.rename_host(self._alias_of(iid))
        elif iid in self._gpath:
            self.rename_group(self._gpath[iid])

    def delete_selected(self) -> None:
        iid = self._selected()
        if self._alias_of(iid):
            self.delete_host(self._alias_of(iid))
        elif iid in self._gpath:
            self.delete_group(self._gpath[iid])

    def toggle_favorite(self, alias: str) -> None:
        meta = self.store.meta(alias)
        meta["favorite"] = not meta.get("favorite")
        self._after_change()

    def move_host(self, alias: str, group: str) -> None:
        self.store.meta(alias)["group"] = group
        if group:
            self.store.ensure_group(group)
        self._after_change()

    def _deploy_to(self, alias: str) -> None:
        pub = filedialog.askopenfilename(
            initialdir=SSH_DIR if SSH_DIR.exists() else Path.home(),
            title="Sélectionner la clé publique à envoyer",
            filetypes=(("Clés publiques SSH", "*.pub"), ("Tous les fichiers", "*.*")))
        if pub:
            line = f'type "{pub.replace("%", "%%")}" | ssh {bat_quote(alias)} "{REMOTE_DEPLOY_CMD}"'
            self.open_script_tab(f"Clé → {self.store.label(alias)}", [line], pause_on_success=True)

    # ================================================================== #
    # Groupes
    # ================================================================== #
    def new_group(self, then_move: Optional[str] = None, parent_group: str = "") -> None:
        title = "Nouveau sous-groupe" if parent_group else "Nouveau groupe"
        name = ctk.CTkInputDialog(text="Nom du nouveau groupe :", title=title).get_input()
        name = (name or "").strip()
        if not name:
            return
        if GROUP_SEPARATOR in name:
            return messagebox.showerror(APP_NAME, "Ce caractère ne peut pas être utilisé dans un nom de groupe.")
        path = self.store.group_path(parent_group, name)
        self.store.ensure_group(path)
        if then_move:
            self.store.meta(then_move)["group"] = path
        self._after_change()

    def rename_group(self, old: str) -> None:
        old_label = self.store.group_label(old)
        new = ctk.CTkInputDialog(text=f"Nouveau nom pour le groupe « {old_label} » :",
                                 title="Renommer le groupe").get_input()
        new = (new or "").strip()
        if not new or GROUP_SEPARATOR in new:
            return
        parent = self.store.group_parent(old)
        new_path = self.store.group_path(parent, new)
        if new_path == old or new_path in self.store.groups:
            return
        prefix = old + GROUP_SEPARATOR
        renamed = {path: new_path + path[len(old):] for path in self.store.groups
                   if path == old or path.startswith(prefix)}
        self.store.groups[:] = [renamed.get(path, path) for path in self.store.groups]
        for meta in self.store.data["hosts"].values():
            meta["group"] = renamed.get(meta.get("group", ""), meta.get("group", ""))
        self.closed_groups = {renamed.get(path, path) for path in self.closed_groups}
        self._after_change()

    def delete_group(self, name: str) -> None:
        subtree = {path for path in self.store.groups
                   if path == name or path.startswith(name + GROUP_SEPARATOR)}
        parent = self.store.group_parent(name)
        destination = self.store.group_label(parent) if parent else "Sans groupe"
        if not messagebox.askyesno(
                APP_NAME, f"Supprimer le groupe « {self.store.group_label(name)} » et ses sous-groupes ?\n"
                          f"Leurs connexions seront déplacées dans « {destination} ». "):
            return
        self.store.groups[:] = [path for path in self.store.groups if path not in subtree]
        self.closed_groups.difference_update(subtree)
        for meta in self.store.data["hosts"].values():
            if meta.get("group") in subtree:
                meta["group"] = parent
        self._after_change()

    def move_group(self, name: str, delta: int) -> None:
        siblings = [path for path in self.store.groups
                    if self.store.group_parent(path) == self.store.group_parent(name)]
        i = siblings.index(name)
        j = i + delta
        if 0 <= j < len(siblings):
            groups = self.store.groups
            source_index, target_index = groups.index(siblings[i]), groups.index(siblings[j])
            groups[source_index], groups[target_index] = groups[target_index], groups[source_index]
            self._after_change()

    def move_group_to(self, name: str, parent: str) -> None:
        if parent == name or parent.startswith(name + GROUP_SEPARATOR):
            return
        new_path = self.store.group_path(parent, self.store.group_name(name))
        if new_path == name:
            return
        prefix = name + GROUP_SEPARATOR
        subtree = {path for path in self.store.groups if path == name or path.startswith(prefix)}
        if new_path in self.store.groups and new_path not in subtree:
            return messagebox.showerror(APP_NAME, "Un groupe portant ce nom existe déjà dans cette destination.")
        moved = {path: new_path + path[len(name):] for path in subtree}
        self.store.groups[:] = [moved.get(path, path) for path in self.store.groups]
        for meta in self.store.data["hosts"].values():
            meta["group"] = moved.get(meta.get("group", ""), meta.get("group", ""))
        self.closed_groups = {moved.get(path, path) for path in self.closed_groups}
        self._after_change()

    # ================================================================== #
    # Menus déroulants / contextuels
    # ================================================================== #
    def _dismiss_dropdown(self, event) -> None:
        for popup in (self._active_dropdown, self._active_submenu):
            if popup is None:
                continue
            try:
                if popup.winfo_exists():
                    left, top = popup.winfo_rootx(), popup.winfo_rooty()
                    if left <= event.x_root < left + popup.winfo_width() and \
                            top <= event.y_root < top + popup.winfo_height():
                        return
            except tk.TclError:
                pass
        self._close_dropdowns()

    def _close_dropdowns(self) -> None:
        popups = (self._active_submenu, self._active_dropdown)
        self._active_submenu = None
        self._active_submenu_anchor = None
        self._active_dropdown = None
        for popup in popups:
            if popup is not None:
                try:
                    popup.destroy()
                except tk.TclError:
                    pass

    def _show_dropdown(self, button, actions: list[tuple[str, Callable]], width: int = 250) -> None:
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height() + 5
        items = [{"label": label, "command": action} for label, action in actions]
        self._show_menu_at(x, y, items, width)

    def _show_menu_at(self, x: int, y: int, items: list, width: int = 260,
                      submenu: bool = False, anchor=None) -> None:
        if submenu:
            if self._active_submenu is not None:
                try:
                    self._active_submenu.destroy()
                except tk.TclError:
                    pass
        else:
            self._close_dropdowns()
        content_height = sum(MENU_SEPARATOR_HEIGHT if item is None else MENU_ITEM_HEIGHT for item in items)
        height = min(content_height + 8, max(120, self.winfo_height() - 16))
        popup = ctk.CTkFrame(self, width=width, height=height, fg_color=CARD,
                             corner_radius=10, border_width=1, border_color=BORDER)
        popup.pack_propagate(False)
        if submenu:
            self._active_submenu = popup
            self._active_submenu_anchor = anchor
        else:
            self._active_dropdown = popup
        scroll = ctk.CTkScrollableFrame(popup, width=width - 14, height=height - 14,
                                        fg_color="transparent", scrollbar_button_color=BORDER,
                                        scrollbar_button_hover_color=HOVER, corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        def invoke(action: Callable) -> None:
            self._close_dropdowns()
            self.after_idle(action)

        for item in items:
            if item is None:
                ctk.CTkFrame(scroll, height=1, fg_color=BORDER).pack(fill="x", padx=8, pady=4)
                continue
            label = item["label"]
            if "submenu" in item:
                button = ctk.CTkButton(
                    scroll, text=f"{label}    ›", anchor="w", fg_color="transparent",
                    hover_color=HOVER, text_color=TEXT, corner_radius=6, height=34, font=font(12))

                def open_submenu(button=button, children=item["submenu"]) -> None:
                    if self._active_submenu_anchor is button and self._active_submenu is not None:
                        return
                    submenu_x = button.winfo_rootx() + button.winfo_width() - 4
                    submenu_y = button.winfo_rooty() - 4
                    self._show_menu_at(submenu_x, submenu_y, children, width, submenu=True, anchor=button)

                button.bind("<Enter>", lambda _event, fn=open_submenu: fn(), add="+")
                button.configure(command=open_submenu)
                button.pack(fill="x", padx=5, pady=2)
                continue

            danger = item.get("danger", False)
            ctk.CTkButton(
                scroll, text=label, command=lambda action=item["command"]: invoke(action), anchor="w",
                fg_color=DANGER if danger else "transparent",
                hover_color=DANGER_H if danger else HOVER,
                text_color="#ffadb5" if danger else TEXT,
                corner_radius=6, height=34, font=font(12)).pack(fill="x", padx=5, pady=2)

        root_x, root_y = self.winfo_rootx(), self.winfo_rooty()
        local_x, local_y = x - root_x, y - root_y
        if local_x + width > self.winfo_width():
            local_x -= width
        if local_y + height > self.winfo_height():
            local_y -= height
        popup.place(x=max(8, local_x), y=max(8, local_y))
        popup.lift()

    def _host_menu_items(self, alias: str) -> list:
        fav = self.store.meta(alias).get("favorite")
        groups = [{"label": "Sans groupe", "command": lambda: self.move_host(alias, "")}] + [
            {"label": self.store.group_label(group), "command": lambda group=group: self.move_host(alias, group)}
            for group in self.store.groups]
        groups += [{"label": "Nouveau groupe…", "command": lambda: self.new_group(then_move=alias)}]
        return [
            {"label": "Connecter (nouvel onglet)", "command": lambda: self.connect(alias)},
            {"label": "Ouvrir dans une fenêtre externe", "command": lambda: self.connect_external(alias)},
            None,
            {"label": "Renommer l'affichage  (F2)", "command": lambda: self.rename_host(alias)},
            {"label": "Modifier…", "command": lambda: self.edit_host(alias)},
            {"label": "Dupliquer", "command": lambda: self.duplicate_host(alias)},
            {"label": "Retirer des favoris" if fav else "Ajouter aux favoris",
             "command": lambda: self.toggle_favorite(alias)},
            {"label": "Déplacer vers", "submenu": groups},
            {"label": "Déployer une clé publique…", "command": lambda: self._deploy_to(alias)},
            None,
            {"label": "Supprimer", "command": lambda: self.delete_host(alias), "danger": True},
        ]

    def _group_menu_items(self, name: str) -> list:
        items = [
            {"label": "Nouvelle connexion dans ce groupe", "command": lambda: self.new_host(name)},
            {"label": "Nouveau sous-groupe…", "command": lambda: self.new_group(parent_group=name)},
        ]
        parent = self.store.group_parent(name)
        if parent:
            grandparent = self.store.group_parent(parent)
            items.append({"label": "Remonter d'un niveau",
                          "command": lambda: self.move_group_to(name, grandparent)})
        items += [
            {"label": "Renommer le groupe", "command": lambda: self.rename_group(name)},
            {"label": "Monter", "command": lambda: self.move_group(name, -1)},
            {"label": "Descendre", "command": lambda: self.move_group(name, 1)},
            None,
            {"label": "Supprimer le groupe", "command": lambda: self.delete_group(name), "danger": True},
        ]
        return items

    def _on_right_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        alias = self._alias_of(iid) if iid else None
        if alias:
            items = self._host_menu_items(alias)
        elif iid in self._gpath:
            items = self._group_menu_items(self._gpath[iid])
        else:
            items = [
                {"label": "Nouvelle connexion", "command": lambda: self.new_host()},
                {"label": "Nouveau groupe", "command": self.new_group},
            ]
        self._show_menu_at(event.x_root, event.y_root, items, width=290)

    def _settings_menu(self) -> None:
        self._show_dropdown(self.btn_settings, [
            ("Recharger la configuration", lambda: (self.cfg.load(), self._after_change())),
            ("Modifier le fichier config (Bloc-notes)",
             lambda: subprocess.Popen(["notepad", str(CONFIG_PATH)])),
            ("Ouvrir le dossier .ssh",
             lambda: os.startfile(SSH_DIR) if IS_WIN and SSH_DIR.exists() else None),
        ], width=270)

    def _local_terminal_menu(self) -> None:
        self._show_dropdown(self.btn_term, [
            ("PowerShell", lambda: self.open_script_tab(
                "PowerShell", ['cd /d "%USERPROFILE%"', "powershell -NoLogo"])),
            ("Invite de commandes", lambda: self.open_script_tab(
                "Invite de commandes", ['cd /d "%USERPROFILE%"', "cmd /k"])),
        ])

    # ================================================================== #
    # Glisser-déposer dans l'arbre
    # ================================================================== #
    def _dnd_press(self, event) -> None:
        self._drag = {"iid": self.tree.identify_row(event.y), "x": event.x, "y": event.y, "on": False}

    def _dnd_motion(self, event) -> None:
        drag = self._drag
        if not drag or not drag["iid"] or drag["on"]:
            return
        draggable = drag["iid"].startswith("h:") or (drag["iid"].startswith("g:") and drag["iid"] != "g:")
        if abs(event.x - drag["x"]) + abs(event.y - drag["y"]) > 8 and draggable:
            drag["on"] = True
            self.tree.configure(cursor="hand2")

    def _dnd_release(self, event) -> None:
        drag, self._drag = self._drag, None
        self.tree.configure(cursor="")
        if not drag:
            return
        if drag["on"]:
            target = self.tree.identify_row(event.y)
            if target and target != drag["iid"]:
                self._drop(drag["iid"], target)
        elif drag["iid"] in self._gtext:
            self._toggle_group(drag["iid"])

    def _drop(self, src: str, target: str) -> None:
        if src.startswith("h:"):
            alias = src[2:]
            if target == self.FAV:
                self.store.meta(alias)["favorite"] = True
                return self._after_change()
            if target == "g:":
                group = ""
            elif target in self._gpath:
                group = self._gpath[target]
            elif target[:2] in ("h:", "f:"):
                group = self.store.meta(target[2:]).get("group", "")
            else:
                return
            self.move_host(alias, group)
        elif src in self._gpath and target in self._gpath:
            self.move_group_to(self._gpath[src], self._gpath[target])

    def _on_double(self, event) -> str:
        iid = self.tree.identify_row(event.y)
        alias = self._alias_of(iid) if iid else None
        if alias:
            self.connect(alias)
        return "break"

    # ================================================================== #
    # Sessions / onglets de terminal
    # ================================================================== #
    def _connect_selected(self) -> None:
        alias = self._alias_of(self._selected())
        if alias:
            self.connect(alias)

    def connect(self, alias: str) -> None:
        token = uuid.uuid4().hex[:10]
        line = (f"ssh -t {bat_quote(alias)} "
                f"\"exec sh -c 'echo $$ > /tmp/.pyssh_{token}; exec ${{SHELL:-sh}} -l'\"")
        self.open_script_tab(self.store.label(alias), [line], alias=alias, token=token)

    def connect_external(self, alias: str) -> None:
        if IS_WIN:
            subprocess.Popen(f'start "{self.store.label(alias)}" ssh {bat_quote(alias)}', shell=True)

    def open_script_tab(self, title: str, lines: list[str], pause_on_success: bool = False,
                        alias: Optional[str] = None, token: Optional[str] = None) -> None:
        if not IS_WIN:
            return messagebox.showerror(APP_NAME, "Le terminal intégré n'est disponible que sous Windows.")
        name, n = title, 2
        while name in self.sessions or name == self.HOME_TAB:
            name, n = f"{title} ({n})", n + 1
        frame = self.tabs.add(name)
        self._bind_tab_close(name)
        host = tk.Frame(frame, bg=TERM_BG, highlightthickness=0, bd=0)
        host.pack(fill="both", expand=True)
        self.tabs.set(name)
        self.update_idletasks()
        console = EmbeddedConsole(host, lines, pause_on_success)
        self.sessions[name] = console
        if alias:
            self.remotes[name] = (alias, token)
        try:
            console.start(on_fail=lambda msg, n=name: self._session_failed(n, msg))
        except Exception as ex:
            self._close_tab(name)
            messagebox.showerror(APP_NAME, f"Impossible de lancer la console :\n{ex}")

    def toggle_sftp(self) -> None:
        name = self.tabs.get()
        if name not in self.remotes:
            return messagebox.showinfo(APP_NAME, "L'explorateur SFTP est disponible dans un onglet de connexion SSH.")
        panel = self.sftp_panels.get(name)
        if panel is None:
            alias, token = self.remotes[name]
            panel = self.sftp_panels[name] = SftpPanel(self.tabs.tab(name), self, alias, token)
        elif panel.winfo_ismapped():
            return panel.pack_forget()
        panel.pack(side="right", fill="y", padx=(10, 0), before=self.sessions[name].host)
        panel.sync_terminal_dir()

    def _session_failed(self, name: str, msg: str) -> None:
        self._close_tab(name)
        messagebox.showerror(APP_NAME, msg)

    def _close_tab(self, name: str) -> None:
        if name not in self.sessions:
            return
        tab_names = [self.HOME_TAB, *self.sessions]
        current = self.tabs.get()
        target = current
        if current == name:
            remaining_tabs = [tab for tab in tab_names if tab != name]
            if remaining_tabs:
                target = remaining_tabs[max(0, tab_names.index(name) - 1)]

        self.remotes.pop(name, None)
        panel = self.sftp_panels.pop(name, None)
        if panel:
            panel.close()
        console = self.sessions.pop(name, None)
        if console:
            console.close()
            try:
                console.host.destroy()
            except tk.TclError:
                pass
        self.tabs.delete(name)
        self.after(120, lambda tab=target: self._restore_tab_after_close(tab))

    def _restore_tab_after_close(self, name: str) -> None:
        if name not in self.tabs._tab_dict:
            return
        self.tabs.set(name)
        self.after(80, self._focus_current)

    def close_current_tab(self) -> None:
        name = self.tabs.get()
        if name in self.sessions:
            self._close_tab(name)

    def _current_console(self) -> Optional[EmbeddedConsole]:
        return self.sessions.get(self.tabs.get())

    def copy_current(self) -> None:
        console = self._current_console()
        if console:
            console.copy()

    def paste_current(self) -> None:
        console = self._current_console()
        if console:
            console.paste()
            console.focus()

    def _on_tab_change(self) -> None:
        self.after(80, self._focus_current)

    def _focus_current(self) -> None:
        current = self.tabs.get()
        for name, console in self.sessions.items():
            console.set_visible(name == current)
        console = self.sessions.get(current)
        if console:
            console.focus()

    def _poll_sessions(self) -> None:
        """Ferme les onglets dont la console a été terminée."""
        try:
            for name, console in list(self.sessions.items()):
                if not console.is_alive():
                    self._close_tab(name)
        finally:
            self.after(SESSION_POLL_MS, self._poll_sessions)

    @staticmethod
    def _app_is_foreground() -> bool:
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        return pid.value == os.getpid()

    def _pointer_poll(self) -> None:
        """Donne le focus à la console quand le pointeur la survole."""
        try:
            console = self._current_console()
            if (console and console.hwnd and not self.grab_current() and self._app_is_foreground()
                    and console.contains_cursor() and not console.has_focus()):
                console.focus()
        except Exception:
            pass
        finally:
            self.after(POINTER_POLL_MS, self._pointer_poll)

    def _on_close(self) -> None:
        if self.sessions and not messagebox.askyesno(
                APP_NAME, f"{len(self.sessions)} session(s) ouverte(s). Tout fermer et quitter ?"):
            return
        for name in list(self.sessions):
            self._close_tab(name)
        self.destroy()
