"""Persistance des groupes et métadonnées (libellé, couleur, favoris...) des connexions."""
from __future__ import annotations

import json
from tkinter import messagebox

from .constants import APP_NAME, DATA_DIR, DATA_FILE, GROUP_SEPARATOR


class Store:
    def __init__(self):
        self.data: dict = {"groups": [], "hosts": {}}
        self._load_failed = False
        self._load()
        self._rebuild_group_ancestors()

    # ------------------------------------------------------------------ #
    # Chargement
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_valid(loaded) -> bool:
        return (isinstance(loaded, dict)
                and isinstance(loaded.get("groups", []), list)
                and all(isinstance(g, str) for g in loaded.get("groups", []))
                and isinstance(loaded.get("hosts", {}), dict)
                and all(isinstance(m, dict) for m in loaded.get("hosts", {}).values()))

    def _load(self) -> None:
        if DATA_FILE.exists():
            try:
                loaded = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                if not self._is_valid(loaded):
                    raise ValueError("Format de fichier de réglages invalide.")
                self.data.update(loaded)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as e:
                self._load_failed = True
                messagebox.showerror(
                    APP_NAME,
                    f"Impossible de charger les groupes et réglages depuis :\n{DATA_FILE}\n\n{e}\n\n"
                    "Le fichier est préservé et ne sera pas écrasé.")
        self.data.setdefault("groups", [])
        self.data.setdefault("hosts", {})

    def _rebuild_group_ancestors(self) -> None:
        """Garantit que chaque groupe référencé (et ses parents) existe dans `groups`."""
        known = set(self.groups)
        paths = list(self.groups)
        paths.extend(m.get("group", "") for m in self.data["hosts"].values())
        for path in paths:
            parent = self.group_parent(path)
            ancestors = []
            while parent:
                ancestors.append(parent)
                parent = self.group_parent(parent)
            for ancestor in reversed(ancestors):
                if ancestor not in known:
                    self.groups.append(ancestor)
                    known.add(ancestor)
            if path and path not in known:
                self.groups.append(path)
                known.add(path)

    # ------------------------------------------------------------------ #
    # Groupes hiérarchiques (chemin = noms séparés par GROUP_SEPARATOR)
    # ------------------------------------------------------------------ #
    @staticmethod
    def group_parent(path: str) -> str:
        return path.rpartition(GROUP_SEPARATOR)[0]

    @staticmethod
    def group_name(path: str) -> str:
        return path.rpartition(GROUP_SEPARATOR)[2] if GROUP_SEPARATOR in path else path

    @staticmethod
    def group_path(parent: str, name: str) -> str:
        return f"{parent}{GROUP_SEPARATOR}{name}" if parent else name

    @classmethod
    def group_label(cls, path: str) -> str:
        return " / ".join(path.split(GROUP_SEPARATOR))

    @property
    def groups(self) -> list[str]:
        return self.data["groups"]

    def ensure_group(self, path: str) -> None:
        parts = path.split(GROUP_SEPARATOR)
        for index in range(1, len(parts) + 1):
            sub_path = GROUP_SEPARATOR.join(parts[:index])
            if sub_path not in self.groups:
                self.groups.append(sub_path)

    # ------------------------------------------------------------------ #
    # Métadonnées des hôtes
    # ------------------------------------------------------------------ #
    def meta(self, alias: str) -> dict:
        return self.data["hosts"].setdefault(alias, {})

    def label(self, alias: str) -> str:
        return self.data["hosts"].get(alias, {}).get("label") or alias

    def rename_alias(self, old: str, new: str) -> None:
        if old in self.data["hosts"]:
            self.data["hosts"][new] = self.data["hosts"].pop(old)

    def save(self) -> None:
        if self._load_failed:
            return
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            DATA_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Impossible d'enregistrer les réglages :\n{e}")
