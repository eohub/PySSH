"""Panneau latéral d'exploration SFTP (paramiko) attaché à un onglet SSH."""
from __future__ import annotations

import os
import posixpath
import stat
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

import customtkinter as ctk
import paramiko

from .constants import APP_NAME, BORDER, CREATE_NO_WINDOW, HOVER, MUTED, SIDE
from .dialogs import ask_text
from .ui import btn_ghost, font, make_entry

CONNECT_TIMEOUT_S = 15
TERMINAL_DIR_TIMEOUT_S = 10


class SftpPanel(ctk.CTkFrame):
    def __init__(self, parent, app, alias: str, token: str):
        super().__init__(parent, width=480, fg_color=SIDE, corner_radius=12,
                         border_width=1, border_color=BORDER)
        self.pack_propagate(False)
        self.app, self.alias, self.token = app, alias, token
        self.client: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.password: Optional[str] = None
        self.connecting = False
        self.path = "/"
        self.entries: dict = {}
        self._build_toolbar()
        self._build_tree()
        self._build_actions()
        self.lbl = ctk.CTkLabel(self, text="", anchor="w", font=font(11), text_color=MUTED, wraplength=450)
        self.lbl.pack(fill="x", padx=12, pady=(6, 10))

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(10, 6))
        btn_ghost(bar, "↑", self.go_up, width=34).pack(side="left")
        btn_ghost(bar, "⟳", lambda: self.navigate(self.path), width=34).pack(side="left", padx=(6, 0))
        btn_ghost(bar, "⌖", self.sync_terminal_dir, width=34).pack(side="left", padx=(6, 8))
        self.e_path = make_entry(bar)
        self.e_path.pack(side="left", fill="x", expand=True)
        self.e_path.bind("<Return>", lambda e: self.navigate(self.e_path.get().strip()))

    def _build_tree(self) -> None:
        frame = tk.Frame(self, bg=SIDE, highlightthickness=0)
        frame.pack(fill="both", expand=True, padx=(10, 6))
        self.tree = ttk.Treeview(frame, style="Ssh.Treeview", show="tree", columns=("size", "date"),
                                 selectmode="browse")
        scrollbar = ctk.CTkScrollbar(frame, command=self.tree.yview, fg_color="transparent",
                                     button_color=BORDER, button_hover_color=HOVER, width=10)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.column("#0", width=210)
        self.tree.column("size", width=70, anchor="e", stretch=False)
        self.tree.column("date", width=120, anchor="e", stretch=False)
        self.tree.bind("<Double-1>", self._on_double)

    def _build_actions(self) -> None:
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=10, pady=(6, 0))
        for text, command in (("Envoyer", self.upload), ("Télécharger", self.download),
                              ("Dossier", self.mkdir), ("Renommer", self.rename),
                              ("Supprimer", self.delete)):
            btn_ghost(actions, text, command, width=60, height=30, font=font(11)).pack(
                side="left", fill="x", expand=True, padx=2)

    # ------------------------------------------------------------------ #
    # Utilitaires
    # ------------------------------------------------------------------ #
    @staticmethod
    def _size(n: float) -> str:
        for unit in ("o", "Ko", "Mo", "Go"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.1f} To"

    def _status(self, text: str) -> None:
        self.lbl.configure(text=text)

    def _async(self, work: Callable, done: Callable) -> None:
        """Exécute `work` dans un thread et appelle `done(result, error)` dans le thread Tk."""
        box: dict = {}

        def run() -> None:
            try:
                box["r"] = work()
            except Exception as e:
                box["e"] = e

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def poll() -> None:
            if thread.is_alive():
                self.after(40, poll)
            elif self.winfo_exists():
                done(box.get("r"), box.get("e"))

        poll()

    # ------------------------------------------------------------------ #
    # Connexion
    # ------------------------------------------------------------------ #
    def _connect_work(self):
        out = subprocess.run(["ssh", "-G", self.alias], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW).stdout
        info: dict = {}
        keys: list[str] = []
        for line in out.splitlines():
            key, _, value = line.partition(" ")
            if key == "identityfile":
                keys.append(os.path.expanduser(value))
            else:
                info.setdefault(key, value)
        host, port = info["hostname"], int(info.get("port", 22))
        jump = info.get("proxyjump", "none")
        sock = None if jump == "none" else paramiko.ProxyCommand(f"ssh -W {host}:{port} {jump}")
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, port, info.get("user"), password=self.password, passphrase=self.password,
                       key_filename=[k for k in keys if os.path.isfile(k)] or None,
                       sock=sock, timeout=CONNECT_TIMEOUT_S)
        return client, client.open_sftp()

    def _ensure(self, then: Callable) -> None:
        if self.sftp:
            return then()
        if self.connecting:
            return
        self.connecting = True
        self._status("Connexion…")

        def done(result, error) -> None:
            self.connecting = False
            if isinstance(error, paramiko.AuthenticationException):
                password = ask_text(self.app, APP_NAME,
                                    f"Mot de passe ou phrase secrète pour « {self.alias} » :", show="*")
                if password is None:
                    return self._status("Authentification annulée.")
                self.password = password
                return self._ensure(then)
            if error:
                return self._status(f"Connexion impossible : {error}")
            self.client, self.sftp = result
            then()

        self._async(self._connect_work, done)

    def _terminal_dir(self) -> str:
        try:
            _, out, _ = self.client.exec_command(
                f"sh -c 'readlink /proc/$(cat /tmp/.pyssh_{self.token})/cwd'",
                timeout=TERMINAL_DIR_TIMEOUT_S)
            return out.read().decode().strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def sync_terminal_dir(self) -> None:
        self.navigate()

    def go_up(self) -> None:
        self.navigate(posixpath.dirname(self.path.rstrip("/")) or "/")

    def navigate(self, path: Optional[str] = None) -> None:
        def work():
            target = self.sftp.normalize(path or self._terminal_dir() or ".")
            return target, self.sftp.listdir_attr(target)

        def done(result, error) -> None:
            if error:
                return self._status(f"Erreur : {error}")
            self.path, items = result
            self.e_path.delete(0, "end")
            self.e_path.insert(0, self.path)
            self.tree.delete(*self.tree.get_children())
            self.entries = {a.filename: a for a in items}
            for attr in sorted(items, key=lambda a: (not stat.S_ISDIR(a.st_mode), a.filename.lower())):
                is_dir = stat.S_ISDIR(attr.st_mode)
                icon = "📁" if is_dir else "🔗" if stat.S_ISLNK(attr.st_mode) else "📄"
                self.tree.insert("", "end", iid=attr.filename, text=f"  {icon}  {attr.filename}",
                                 values=("" if is_dir else self._size(attr.st_size or 0),
                                         time.strftime("%Y-%m-%d %H:%M", time.localtime(attr.st_mtime or 0))))
            self._status(f"{len(items)} élément(s)")

        self._ensure(lambda: self._async(work, done))

    def _selected(self) -> Optional[str]:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _on_double(self, event) -> None:
        name = self.tree.identify_row(event.y)
        if name and (stat.S_ISDIR(self.entries[name].st_mode) or stat.S_ISLNK(self.entries[name].st_mode)):
            self.navigate(posixpath.join(self.path, name))

    # ------------------------------------------------------------------ #
    # Actions sur les fichiers
    # ------------------------------------------------------------------ #
    def _run(self, work: Callable, message: Optional[str] = None) -> None:
        self._status("En cours…")

        def done(_, error) -> None:
            if error:
                return self._status(f"Erreur : {error}")
            if message is None:
                self.navigate(self.path)
            else:
                self._status(message)

        self._async(work, done)

    def upload(self) -> None:
        files = filedialog.askopenfilenames(parent=self.app, title="Choisir les fichiers à envoyer")
        if not files:
            return
        path = self.path

        def work() -> None:
            for local in files:
                self.sftp.put(local, posixpath.join(path, os.path.basename(local)))

        self._run(work)

    def download(self) -> None:
        name = self._selected()
        if not name or stat.S_ISDIR(self.entries[name].st_mode):
            return self._status("Sélectionnez un fichier à télécharger.")
        local = filedialog.asksaveasfilename(parent=self.app, initialfile=name)
        if local:
            remote = posixpath.join(self.path, name)
            self._run(lambda: self.sftp.get(remote, local), f"« {name} » téléchargé.")

    def mkdir(self) -> None:
        name = ask_text(self.app, "Nouveau dossier", "Nom du nouveau dossier :")
        if name:
            remote = posixpath.join(self.path, name)
            self._run(lambda: self.sftp.mkdir(remote))

    def rename(self) -> None:
        name = self._selected()
        if not name:
            return
        new = ask_text(self.app, "Renommer", "Nouveau nom :", name)
        if new and new != name:
            old_path, new_path = posixpath.join(self.path, name), posixpath.join(self.path, new)
            self._run(lambda: self.sftp.rename(old_path, new_path))

    def delete(self) -> None:
        name = self._selected()
        if not name or not messagebox.askyesno(APP_NAME, f"Supprimer « {name} » ?", parent=self.app):
            return
        remote = posixpath.join(self.path, name)
        is_dir = stat.S_ISDIR(self.entries[name].st_mode)
        self._run(lambda: self.sftp.rmdir(remote) if is_dir else self.sftp.remove(remote))

    # ------------------------------------------------------------------ #
    # Fermeture
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        client, token = self.client, self.token
        self.client = self.sftp = None
        if not client:
            return

        def work() -> None:
            try:
                client.exec_command(f"rm -f /tmp/.pyssh_{token}")[1].channel.recv_exit_status()
            except Exception:
                pass
            finally:
                client.close()

        threading.Thread(target=work, daemon=True).start()
