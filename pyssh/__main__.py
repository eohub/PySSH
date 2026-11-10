"""Point d'entrée : `python -m pyssh`."""
from __future__ import annotations

import ctypes

import customtkinter as ctk

from .constants import APP_USER_MODEL_ID, IS_WIN


def main() -> None:
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass
    from .app import App 
    App().mainloop()


if __name__ == "__main__":
    main()
