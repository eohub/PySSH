"""Constantes globales : chemins, paramètres SSH et thème graphique."""
from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"

APP_NAME = "PySSH"
APP_USER_MODEL_ID = "innlog_ssh_manager"

# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
SSH_DIR = Path.home() / ".ssh"
CONFIG_PATH = SSH_DIR / "config"
BACKUP_SUFFIX = ".innssh.bak"


def _resolve_data_paths() -> tuple[Path, Path]:
    """Retourne (dossier, fichier) des réglages, avec repli sur l'ancien emplacement."""
    root = Path(os.environ.get("APPDATA", str(Path.home())))
    data_file = root / APP_NAME / "data.json"
    legacy_file = root / "InnSSHManager" / "data.json"
    if not data_file.exists() and legacy_file.exists():
        data_file = legacy_file
    return data_file.parent, data_file


DATA_DIR, DATA_FILE = _resolve_data_paths()

CREATE_NO_WINDOW = 0x08000000

# --------------------------------------------------------------------------- #
# SSH
# --------------------------------------------------------------------------- #
MAIN_KEYS = ["HostName", "User", "Port", "IdentityFile", "ProxyJump"]
LOWER_MAIN = {key.lower(): key for key in MAIN_KEYS}
REMOTE_DEPLOY_CMD = ("mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && "
                     "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys")
GROUP_SEPARATOR = "\x1f"

# --------------------------------------------------------------------------- #
# Thème
# --------------------------------------------------------------------------- #
BG, SIDE, PANEL = "#101316", "#15191e", "#191e25"
CARD, HOVER, BORDER = "#20262e", "#2a323d", "#303944"
TEXT, MUTED = "#edf1f4", "#98a3ad"
ACCENT = "#00d9ff"
DANGER, DANGER_H = "#3a1d24", "#5a2530"
SELECT_BG, TERM_BG = "#21443a", "#0c0c0c"
FIELD_BG = "#171c26"

COLORS = {
    "Aucune": "", "Vert": "#4ade80", "Bleu": "#60a5fa", "Rouge": "#f87171",
    "Orange": "#fb923c", "Jaune": "#fbbf24", "Violet": "#c084fc",
    "Rose": "#f472b6", "Cyan": "#22d3ee",
}
DEFAULT_DOT = "#4b5563"
