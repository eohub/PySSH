"""Accès bas niveau à l'API Windows (ctypes) : fenêtres, consoles et processus.

Sur les autres plateformes, `user32`, `kernel32` et `gdi32` valent None : ce module reste
importable, mais aucune fonction ne doit être appelée.
"""
from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
from typing import Iterator, Optional, Sequence

from .constants import IS_WIN

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_STRIP = 0x80000000 | 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
WS_EX_STRIP = 0x00040000 | 0x00000100 | 0x00000200
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x4, 0x10, 0x20, 0x40
WM_CLOSE = 0x0010
WM_SYSCOMMAND = 0x0112
SMTO_ABORTIFHUNG = 0x0002
ID_CONSOLE_COPY, ID_CONSOLE_PASTE = 0xFFF0, 0xFFF1
ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
SW_HIDE, SW_SHOW = 0, 5
SM_CXVSCROLL = 2
GENERIC_READ_WRITE = 0xC0000000
OPEN_EXISTING = 3
SHARE_READ_WRITE = 3
ATTACH_PARENT_PROCESS = 0xFFFFFFFF
INVALID_HANDLE = ctypes.c_void_p(-1).value
TH32CS_SNAPPROCESS = 0x2


# --------------------------------------------------------------------------- #
# Structures
# --------------------------------------------------------------------------- #
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD), ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD)]


class CONSOLE_FONT_INFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("nFont", wintypes.DWORD),
                ("dwFontSize", COORD), ("FontFamily", wintypes.UINT),
                ("FontWeight", wintypes.UINT), ("FaceName", wintypes.WCHAR * 32)]


# --------------------------------------------------------------------------- #
# Chargement des DLL et prototypes
# --------------------------------------------------------------------------- #
def _configure_prototypes() -> None:
    u, k, g = user32, kernel32, gdi32
    u.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    u.SetParent.restype = wintypes.HWND
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u.SetWindowLongW.restype = ctypes.c_long
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    u.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    u.SetWindowRgn.restype = ctypes.c_int
    g.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    g.CreateRoundRectRgn.restype = ctypes.c_void_p
    g.DeleteObject.argtypes = [ctypes.c_void_p]
    u.IsWindow.argtypes = [wintypes.HWND]
    u.SetFocus.argtypes = [wintypes.HWND]
    u.SetFocus.restype = wintypes.HWND
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.GetFocus.restype = wintypes.HWND
    u.GetForegroundWindow.restype = wintypes.HWND
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                      wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    u.SendMessageTimeoutW.restype = ctypes.c_size_t
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

    k.GetCurrentThreadId.restype = wintypes.DWORD
    k.GetConsoleWindow.restype = wintypes.HWND
    k.FreeConsole.restype = wintypes.BOOL
    k.AttachConsole.argtypes = [wintypes.DWORD]
    k.AttachConsole.restype = wintypes.BOOL
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    k.GetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                          ctypes.POINTER(CONSOLE_FONT_INFOEX)]
    k.GetCurrentConsoleFontEx.restype = wintypes.BOOL
    k.GetConsoleScreenBufferInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
    k.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    k.SetConsoleScreenBufferSize.argtypes = [ctypes.c_void_p, COORD]
    k.SetConsoleScreenBufferSize.restype = wintypes.BOOL
    k.SetConsoleWindowInfo.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.POINTER(SMALL_RECT)]
    k.SetConsoleWindowInfo.restype = wintypes.BOOL
    k.SetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                          ctypes.POINTER(CONSOLE_FONT_INFOEX)]
    k.SetCurrentConsoleFontEx.restype = wintypes.BOOL
    k.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    k.SetConsoleMode.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]


if IS_WIN:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    gdi32 = ctypes.windll.gdi32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _configure_prototypes()
else:
    user32 = kernel32 = gdi32 = None
    WNDENUMPROC = None


# --------------------------------------------------------------------------- #
# Fonctions
# --------------------------------------------------------------------------- #
def descendant_pids(root: int) -> list[int]:
    """PID de tous les processus descendants de `root` (enfants, petits-enfants...)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return []
    children: dict[int, list[int]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    out: list[int] = []
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            out.append(child)
            stack.append(child)
    return out


@contextlib.contextmanager
def console_handles(pids: Sequence[int], *devices: str) -> Iterator[Optional[list[int]]]:
    """Attache temporairement ce processus à la console du premier PID valide.

    Fournit un handle par périphérique demandé (`"CONOUT$"`, `"CONIN$"`...), ou None
    si l'attachement ou l'ouverture d'un des périphériques échoue.
    """
    had_console = bool(kernel32.GetConsoleWindow())
    if had_console:
        kernel32.FreeConsole()
    handles: list[int] = []
    try:
        if any(kernel32.AttachConsole(pid) for pid in pids):
            for device in devices:
                handle = kernel32.CreateFileW(device, GENERIC_READ_WRITE, SHARE_READ_WRITE,
                                              None, OPEN_EXISTING, 0, None)
                if not handle or handle == INVALID_HANDLE:
                    break
                handles.append(handle)
        yield handles if len(handles) == len(devices) else None
    finally:
        for handle in handles:
            kernel32.CloseHandle(handle)
        kernel32.FreeConsole()
        if had_console:
            kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
