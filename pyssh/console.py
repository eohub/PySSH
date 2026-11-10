"""Console Windows (conhost) intégrée dans un widget Tk."""
from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import time
import uuid
from ctypes import wintypes
from typing import Callable, Optional

from .constants import CREATE_NO_WINDOW
from .win32 import (
    COORD, CONSOLE_FONT_INFOEX, CONSOLE_SCREEN_BUFFER_INFO, ENABLE_EXTENDED_FLAGS,
    ENABLE_QUICK_EDIT_MODE, GWL_EXSTYLE, GWL_STYLE, ID_CONSOLE_COPY, ID_CONSOLE_PASTE,
    SMALL_RECT, SMTO_ABORTIFHUNG, SM_CXVSCROLL, SW_HIDE, SW_SHOW, SWP_FRAMECHANGED,
    SWP_NOACTIVATE, SWP_NOZORDER, SWP_SHOWWINDOW, WM_CLOSE, WM_SYSCOMMAND, WNDENUMPROC,
    WS_CHILD, WS_EX_STRIP, WS_STRIP, WS_VISIBLE, console_handles, descendant_pids,
    gdi32, kernel32, user32,
)

TERMINAL_INSET, TERMINAL_CORNER = 12, 18
FIT_DELAY_MS, FIT_ATTEMPTS = 80, 6
ATTACH_TIMEOUT_S = 10
CONSOLE_FONT_NAME = "Cascadia Mono"
CONSOLE_FONT_HEIGHT = 20
FALLBACK_CELL_RATIO = 0.45  # largeur/hauteur approximative d'une cellule monospace


def _debug(message: str) -> None:
    """Journal optionnel : définir PYSSH_DEBUG=1 pour écrire %TEMP%\\pyssh_fit.log."""
    if os.environ.get("PYSSH_DEBUG"):
        with open(os.path.join(tempfile.gettempdir(), "pyssh_fit.log"), "a", encoding="utf-8") as f:
            f.write(message + "\n")


class EmbeddedConsole:
    """Lance un script .cmd dans un conhost et ré-attache sa fenêtre à `host`."""

    def __init__(self, host, lines: list[str], pause_on_success: bool = False):
        self.host = host
        self.lines = lines
        self.pause_on_success = pause_on_success
        self.token = "INNSSH_" + uuid.uuid4().hex[:10]
        self.proc: Optional[subprocess.Popen] = None
        self.hwnd: Optional[int] = None
        self.script: Optional[str] = None
        self.closed = False
        self._tid: Optional[int] = None
        self._deadline = 0.0
        self._on_fail: Optional[Callable[[str], None]] = None
        self._resize_job = None
        self._fit_job = None

    # ------------------------------------------------------------------ #
    # Démarrage
    # ------------------------------------------------------------------ #
    def _write_script(self) -> None:
        head = ["@echo off", "chcp 65001 >nul", f"title {self.token}"]
        if self.pause_on_success:
            tail = ["if errorlevel 1 (", "  echo.", "  echo [Erreur - appuyez sur une touche pour fermer]",
                    ") else (", "  echo.", "  echo [Termine - appuyez sur une touche pour fermer]", ")",
                    "pause >nul"]
        else:
            tail = ["if errorlevel 1 (", "  echo.",
                    "  echo [Session terminee avec une erreur - appuyez sur une touche]",
                    "  pause >nul", ")"]
        fd, path = tempfile.mkstemp(suffix=".cmd", prefix="innssh_")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(head + self.lines + tail) + "\n")
        self.script = path

    def start(self, on_fail: Callable[[str], None]) -> None:
        self._on_fail = on_fail
        self._write_script()
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        self.proc = subprocess.Popen(f'conhost.exe cmd.exe /c ""{self.script}""',
                                     startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)
        self._deadline = time.time() + ATTACH_TIMEOUT_S
        self.host.after(120, self._try_attach)

    def _find_window(self) -> Optional[int]:
        found: list[int] = []

        @WNDENUMPROC
        def callback(hwnd, _):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value == "ConsoleWindowClass":
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if self.token in title.value or (self.proc and pid.value == self.proc.pid):
                    found.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        return found[0] if found else None

    def _try_attach(self) -> None:
        if self.closed:
            return
        hwnd = self._find_window()
        if hwnd:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            self._set_console_font(pid.value)
            self._embed(hwnd)
        elif time.time() > self._deadline:
            if self._on_fail:
                self._on_fail("Impossible d'intégrer la console Windows.\n\n"
                              "Sous Windows 11, vérifiez : Paramètres > Système > Pour les développeurs > "
                              "Terminal > « Hôte de la console Windows ».")
        else:
            self.host.after(100, self._try_attach)

    @staticmethod
    def _set_console_font(pid: int) -> None:
        with console_handles([pid], "CONOUT$") as handles:
            if not handles:
                return
            handle = handles[0]
            info = CONSOLE_FONT_INFOEX()
            info.cbSize = ctypes.sizeof(info)
            if kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(info)):
                info.FaceName = CONSOLE_FONT_NAME
                info.FontFamily = 0x31
                info.FontWeight = 400
                info.dwFontSize = COORD(0, CONSOLE_FONT_HEIGHT)  # largeur 0 = laissée à conhost
                kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(info))

    def _embed(self, hwnd: int) -> None:
        self.host.update_idletasks()
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        style = (style & ~WS_STRIP) | WS_CHILD | WS_VISIBLE
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & 0x7FFFFFFF)
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex_style & ~WS_EX_STRIP) & 0x7FFFFFFF)
        user32.SetParent(hwnd, self.host.winfo_id())
        self.hwnd = hwnd
        self._tid = user32.GetWindowThreadProcessId(hwnd, None)
        user32.AttachThreadInput(kernel32.GetCurrentThreadId(), self._tid, True)
        self.host.bind("<Configure>", self._on_resize, add="+")
        self._on_resize()
        visible = bool(self.host.winfo_ismapped())
        self.set_visible(visible)
        if visible:
            self.focus()
        for delay in (700, 2000, 5000):
            self.host.after(delay, self.ensure_quickedit)

    # ------------------------------------------------------------------ #
    # Redimensionnement
    # ------------------------------------------------------------------ #
    def _on_resize(self, _event=None) -> None:
        if self.closed:
            return
        width, height = self.host.winfo_width(), self.host.winfo_height()
        if width <= 1 or height <= 1:
            return
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1,
                                          TERMINAL_CORNER * 2, TERMINAL_CORNER * 2)
        if region and not user32.SetWindowRgn(self.host.winfo_id(), region, True):
            gdi32.DeleteObject(region)
        if self.hwnd and self._resize_job is None:
            self._resize_job = self.host.after_idle(self._start_fit)

    def _start_fit(self) -> None:
        self._resize_job = None
        self._cancel_fit()
        self._fit_console(0)

    def _cancel_fit(self) -> None:
        if self._fit_job is not None:
            self.host.after_cancel(self._fit_job)
            self._fit_job = None

    def _schedule_fit(self, attempt: int) -> None:
        self._cancel_fit()
        self._fit_job = self.host.after(FIT_DELAY_MS, lambda: self._fit_console(attempt))

    def _fit_console(self, attempt: int = 0) -> None:
        """Ajuste la console (colonnes/lignes) à l'espace disponible.

        On part de la taille de cellule annoncée par conhost, puis on mesure la fenêtre
        console réelle pour corriger l'écart. La police est demandée avec une largeur 0
        (voir `_set_console_font`) pour que conhost utilise la même largeur de cellule
        pour dessiner et pour calculer le nombre de colonnes.
        """
        self._fit_job = None
        if self.closed or not self.hwnd or not user32.IsWindow(self.hwnd) or not self.proc:
            return
        host_w, host_h = self.host.winfo_width(), self.host.winfo_height()
        if host_w <= 1 or host_h <= 1:
            return
        inset = min(TERMINAL_INSET, (host_w - 1) // 2, (host_h - 1) // 2)
        avail_w, avail_h = host_w - 2 * inset, host_h - 2 * inset
        if attempt == 0 and not user32.MoveWindow(self.hwnd, inset, inset, avail_w, avail_h, True):
            return

        settled = False
        pids = descendant_pids(self.proc.pid)[::-1]
        with console_handles(pids, "CONOUT$") as handles:
            if handles:
                rect = wintypes.RECT()
                if not user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
                    return
                settled = self._fit_step(handles[0], attempt, avail_w, avail_h,
                                         rect.right - rect.left, rect.bottom - rect.top)
                _debug(f"attempt={attempt} host={host_w}x{host_h} avail={avail_w}x{avail_h} "
                       f"win={rect.right - rect.left}x{rect.bottom - rect.top} settled={settled}")
        if not settled and attempt < FIT_ATTEMPTS:
            self._schedule_fit(attempt + 1)

    @staticmethod
    def _fit_step(handle, attempt: int, avail_w: int, avail_h: int, win_w: int, win_h: int) -> bool:
        """Une itération d'ajustement. Retourne True quand la taille est stable."""
        info = CONSOLE_SCREEN_BUFFER_INFO()
        cell = CONSOLE_FONT_INFOEX()
        cell.cbSize = ctypes.sizeof(cell)
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)) or \
                not kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(cell)):
            return False
        cell_w, cell_h = cell.dwFontSize.X, cell.dwFontSize.Y
        if cell_h <= 0:
            return False
        if cell_w <= 0:
            cell_w = max(1, round(cell_h * FALLBACK_CELL_RATIO))

        cols = info.srWindow.Right - info.srWindow.Left + 1
        rows = info.srWindow.Bottom - info.srWindow.Top + 1
        if attempt == 0:
            new_cols = (avail_w - user32.GetSystemMetrics(SM_CXVSCROLL)) // cell_w
            new_rows = avail_h // cell_h
        else:
            new_cols = cols + (avail_w - win_w) // cell_w
            new_rows = rows + (avail_h - win_h) // cell_h
        new_cols, new_rows = max(1, new_cols), max(1, new_rows)
        _debug(f"  cols={cols} rows={rows} cell={cell_w}x{cell_h} -> new={new_cols}x{new_rows}")
        if (new_cols, new_rows) == (cols, rows):
            return attempt >= 2

        size = COORD(max(info.dwSize.X, new_cols), max(info.dwSize.Y, new_rows))
        if (size.X, size.Y) != (info.dwSize.X, info.dwSize.Y) and \
                not kernel32.SetConsoleScreenBufferSize(handle, size):
            return False
        window = SMALL_RECT(0, 0, new_cols - 1, new_rows - 1)
        if not kernel32.SetConsoleWindowInfo(handle, True, ctypes.byref(window)):
            return False
        if size.X > new_cols and info.dwCursorPosition.X < new_cols:
            kernel32.SetConsoleScreenBufferSize(handle, COORD(new_cols, size.Y))
        return False

    # ------------------------------------------------------------------ #
    # Focus / visibilité / presse-papiers
    # ------------------------------------------------------------------ #
    def focus(self) -> None:
        if self.hwnd and user32.IsWindow(self.hwnd):
            user32.SetFocus(self.hwnd)

    def set_visible(self, visible: bool) -> None:
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            return
        if not visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            return
        self.host.update_idletasks()
        width, height = self.host.winfo_width(), self.host.winfo_height()
        if width > 1 and height > 1:
            inset = min(TERMINAL_INSET, (width - 1) // 2, (height - 1) // 2)
            user32.SetWindowPos(self.hwnd, None, inset, inset, width - 2 * inset,
                                height - 2 * inset,
                                SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        user32.ShowWindow(self.hwnd, SW_SHOW)
        self._on_resize()

    def has_focus(self) -> bool:
        return bool(self.hwnd) and user32.GetFocus() == self.hwnd

    def _post_edit_command(self, command: int) -> None:
        if self.hwnd and user32.IsWindow(self.hwnd):
            user32.PostMessageW(self.hwnd, WM_SYSCOMMAND, command, 0)

    def copy(self) -> None:
        self._post_edit_command(ID_CONSOLE_COPY)

    def paste(self) -> None:
        self._post_edit_command(ID_CONSOLE_PASTE)

    def ensure_quickedit(self) -> None:
        if self.closed or not self.proc:
            return
        try:
            with console_handles(descendant_pids(self.proc.pid), "CONIN$") as handles:
                if handles:
                    mode = wintypes.DWORD()
                    if kernel32.GetConsoleMode(handles[0], ctypes.byref(mode)):
                        kernel32.SetConsoleMode(
                            handles[0], mode.value | ENABLE_QUICK_EDIT_MODE | ENABLE_EXTENDED_FLAGS)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # État / fermeture
    # ------------------------------------------------------------------ #
    def is_alive(self) -> bool:
        if self.hwnd:
            return bool(user32.IsWindow(self.hwnd))
        return self.proc is not None and self.proc.poll() is None

    def contains_cursor(self) -> bool:
        if not (self.hwnd and user32.IsWindow(self.hwnd)):
            return False
        point, rect = wintypes.POINT(), wintypes.RECT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        return rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._cancel_fit()
            if self.hwnd and self._tid:
                user32.AttachThreadInput(kernel32.GetCurrentThreadId(), self._tid, False)
            if self.hwnd and user32.IsWindow(self.hwnd):
                user32.ShowWindow(self.hwnd, SW_HIDE)
                result = ctypes.c_size_t()
                user32.SendMessageTimeoutW(self.hwnd, WM_CLOSE, 0, 0, SMTO_ABORTIFHUNG, 300,
                                           ctypes.byref(result))
            if self.proc:
                try:
                    self.proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                                   stdin=subprocess.DEVNULL, capture_output=True,
                                   creationflags=CREATE_NO_WINDOW, timeout=3)
            if self.hwnd and user32.IsWindow(self.hwnd):
                user32.ShowWindow(self.hwnd, SW_HIDE)
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
        try:
            if self.script:
                os.remove(self.script)
        except OSError:
            pass
