"""Petites fonctions utilitaires sans dépendance graphique."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", None) or str(Path(__file__).resolve().parent.parent)
    return os.path.join(base_path, relative_path)


def bat_quote(value: str) -> str:
    """Échappe une valeur pour l'insérer dans un script .cmd."""
    value = value.replace("%", "%%")
    return f'"{value}"' if re.search(r'[\s&|<>^()"]', value) else value
