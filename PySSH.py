import ctypes
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

IS_WIN = sys.platform == "win32"
APP_NAME = "PySSH"
SSH_DIR = Path.home() / ".ssh"
CONFIG_PATH = SSH_DIR / "config"
APP_DATA_ROOT = Path(os.environ.get("APPDATA", str(Path.home())))
DATA_DIR = APP_DATA_ROOT / "PySSH"
DATA_FILE = DATA_DIR / "data.json"
LEGACY_DATA_FILE = APP_DATA_ROOT / "InnSSHManager" / "data.json"
if not DATA_FILE.exists() and LEGACY_DATA_FILE.exists():
    DATA_FILE = LEGACY_DATA_FILE
    DATA_DIR = DATA_FILE.parent
CREATE_NO_WINDOW = 0x08000000

MAIN_KEYS = ["HostName", "User", "Port", "IdentityFile", "ProxyJump"]
LOWER_MAIN = {k.lower(): k for k in MAIN_KEYS}
REMOTE_DEPLOY_CMD = ("mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && "
                     "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys")
GROUP_SEPARATOR = "\x1f"

BG, SIDE, PANEL = "#101316", "#15191e", "#191e25"
CARD, HOVER, BORDER = "#20262e", "#2a323d", "#303944"
TEXT, MUTED = "#edf1f4", "#98a3ad"
ACCENT = "#00d9ff"
DANGER, DANGER_H = "#3a1d24", "#5a2530"
SELECT_BG, TERM_BG = "#21443a", "#0c0c0c"

COLORS = {
    "Aucune": "", "Vert": "#4ade80", "Bleu": "#60a5fa", "Rouge": "#f87171",
    "Orange": "#fb923c", "Jaune": "#fbbf24", "Violet": "#c084fc",
    "Rose": "#f472b6", "Cyan": "#22d3ee",
}
DEFAULT_DOT = "#4b5563"

def font(size=12, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def btn_primary(parent, text, command, **kw):
    opts = dict(fg_color=ACCENT, hover_color=ACCENT, text_color="white",
                corner_radius=8, height=38, font=font(13, "bold"))
    opts.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def btn_ghost(parent, text, command, **kw):
    opts = dict(fg_color=CARD, hover_color=HOVER, text_color=TEXT, border_width=1,
                border_color=BORDER, corner_radius=8, height=34, font=font(12))
    opts.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **opts)


def make_entry(parent, **kw):
    opts = dict(fg_color="#171c26", border_color=BORDER, border_width=1,
                corner_radius=8, height=34, text_color=TEXT, font=font(12))
    opts.update(kw)
    return ctk.CTkEntry(parent, **opts)

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def make_rounded_image(pil_img, radius=12):
    pil_img = pil_img.convert("RGBA")
    mask = Image.new("L", pil_img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), pil_img.size], radius=radius, fill=255)
    pil_img.putalpha(mask)
    return pil_img


def make_dot(hexv, size=16):
    """Pastille de couleur anti-aliasée pour l'arbre."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pad = int(s * 0.28)
    ImageDraw.Draw(img).ellipse((pad, pad, s - pad - 1, s - pad - 1), fill=hexv)
    return ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))


def bat_quote(s):
    """Protège une valeur pour un script .cmd."""
    s = s.replace("%", "%%")
    return f'"{s}"' if re.search(r'[\s&|<>^()"]', s) else s

HEADER_RE = re.compile(r"^\s*(host|match)\s+(.+?)\s*$", re.I)
KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)(?:\s*=\s*|\s+)(.*?)\s*$")
INCLUDE_RE = re.compile(r"^\s*include\s+(.+?)\s*$", re.I)


class HostBlock:
    def __init__(self, kind, header, patterns, cfile):
        self.kind = kind
        self.header = header
        self.patterns = patterns
        self.lead = []
        self.body = []
        self.file = cfile

    @property
    def alias(self):
        return self.patterns[0] if self.patterns else ""

    @property
    def is_real(self):
        return (self.kind == "Host" and bool(self.patterns)
                and not any(c in p for p in self.patterns for c in "*?!"))

    def split_body(self):
        """-> (options principales, lignes 'avancées' conservées telles quelles)."""
        main, adv = {}, []
        for line in self.body:
            m = None if line.lstrip().startswith("#") else KV_RE.match(line)
            key = LOWER_MAIN.get(m.group(1).lower()) if m else None
            if key and key not in main:
                main[key] = m.group(2)
            else:
                adv.append(line)
        while adv and not adv[-1].strip():
            adv.pop()
        return main, adv

    def update(self, patterns, main, adv):
        self.patterns = patterns
        self.header = "Host " + " ".join(patterns)
        body = [f"    {k} {main[k].strip()}" for k in MAIN_KEYS if main.get(k, "").strip()]
        body += [l.rstrip() for l in adv]
        body.append("")
        self.body = body


class ConfigFile:
    _backed_up = set()

    def __init__(self, path):
        self.path = Path(path)
        self.preamble, self.blocks = [], []
        self.newline = "\n"

    def load(self):
        self.preamble, self.blocks = [], []
        if not self.path.exists():
            return
        raw = self.path.read_bytes().decode("utf-8-sig", errors="replace")
        self.newline = "\r\n" if "\r\n" in raw else "\n"
        cur = None
        for line in raw.splitlines():
            m = HEADER_RE.match(line)
            if m:
                target = cur.body if cur else self.preamble
                lead = []
                while target and target[-1].lstrip().startswith("#"):
                    lead.insert(0, target.pop())
                kind = m.group(1).capitalize()
                pats = [p.strip('"') for p in m.group(2).split()] if kind == "Host" else [m.group(2)]
                cur = HostBlock(kind, line, pats, self)
                cur.lead = lead
                self.blocks.append(cur)
            elif cur:
                cur.body.append(line)
            else:
                self.preamble.append(line)

    def save(self):
        lines = list(self.preamble)
        for b in self.blocks:
            lines += b.lead + [b.header] + b.body
        text = self.newline.join(lines)
        if not text.endswith(self.newline):
            text += self.newline
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and str(self.path) not in ConfigFile._backed_up:
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".innssh.bak"))
            ConfigFile._backed_up.add(str(self.path))
        self.path.write_bytes(text.encode("utf-8"))


class SSHConfig:
    def __init__(self):
        self.files = []
        self.load()

    def load(self):
        main = ConfigFile(CONFIG_PATH)
        main.load()
        self.files = [main]
        visited = {str(CONFIG_PATH.resolve())}
        self._load_includes(main, visited, 0)

    def _load_includes(self, cfile, visited, depth):
        if depth > 5:
            return
        for line in cfile.preamble:
            m = INCLUDE_RE.match(line)
            if not m:
                continue
            for token in m.group(1).split():
                pattern = os.path.expanduser(token.strip('"'))
                if not os.path.isabs(pattern):
                    pattern = str(SSH_DIR / pattern)
                for p in sorted(glob.glob(pattern)):
                    rp = str(Path(p).resolve())
                    if rp in visited or not os.path.isfile(p):
                        continue
                    visited.add(rp)
                    sub = ConfigFile(p)
                    sub.load()
                    self.files.append(sub)
                    self._load_includes(sub, visited, depth + 1)

    def hosts(self):
        return [b for f in self.files for b in f.blocks if b.is_real]

    def find(self, alias):
        return next((b for b in self.hosts() if b.alias == alias), None)

    def new_block(self, alias):
        f = self.files[0]
        if f.blocks:
            last = f.blocks[-1]
            if not last.body or last.body[-1].strip():
                last.body.append("")
        elif f.preamble and f.preamble[-1].strip():
            f.preamble.append("")
        b = HostBlock("Host", f"Host {alias}", [alias], f)
        f.blocks.append(b)
        return b

    def remove(self, block):
        block.file.blocks.remove(block)
        block.file.save()

    def stamp(self):
        out = []
        for f in self.files:
            try:
                out.append((str(f.path), f.path.stat().st_mtime_ns))
            except OSError:
                out.append((str(f.path), 0))
        return tuple(out)

class Store:
    def __init__(self):
        self.data = {"groups": [], "hosts": {}}
        self._load_failed = False
        if DATA_FILE.exists():
            try:
                loaded = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                if (not isinstance(loaded, dict)
                        or not isinstance(loaded.get("groups", []), list)
                        or not all(isinstance(group, str) for group in loaded.get("groups", []))
                        or not isinstance(loaded.get("hosts", {}), dict)
                        or not all(isinstance(meta, dict) for meta in loaded.get("hosts", {}).values())):
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
        known = set(self.data["groups"])
        paths = list(self.data["groups"])
        paths.extend(m.get("group", "") for m in self.data["hosts"].values())
        for path in paths:
            parent = self.group_parent(path)
            ancestors = []
            while parent:
                ancestors.append(parent)
                parent = self.group_parent(parent)
            for ancestor in reversed(ancestors):
                if ancestor not in known:
                    self.data["groups"].append(ancestor)
                    known.add(ancestor)
            if path and path not in known:
                self.data["groups"].append(path)
                known.add(path)

    @staticmethod
    def group_parent(path):
        return path.rpartition(GROUP_SEPARATOR)[0]

    @staticmethod
    def group_name(path):
        return path.rpartition(GROUP_SEPARATOR)[2] if GROUP_SEPARATOR in path else path

    @staticmethod
    def group_path(parent, name):
        return f"{parent}{GROUP_SEPARATOR}{name}" if parent else name

    def ensure_group(self, path):
        parts = path.split(GROUP_SEPARATOR)
        for index in range(1, len(parts) + 1):
            parent_path = GROUP_SEPARATOR.join(parts[:index])
            if parent_path not in self.groups:
                self.groups.append(parent_path)

    @classmethod
    def group_label(cls, path):
        return " / ".join(path.split(GROUP_SEPARATOR))

    @property
    def groups(self):
        return self.data["groups"]

    def meta(self, alias):
        return self.data["hosts"].setdefault(alias, {})

    def label(self, alias):
        return self.data["hosts"].get(alias, {}).get("label") or alias

    def rename_alias(self, old, new):
        if old in self.data["hosts"]:
            self.data["hosts"][new] = self.data["hosts"].pop(old)

    def save(self):
        if self._load_failed:
            return
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            DATA_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Impossible d'enregistrer les réglages :\n{e}")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class CONSOLE_FONT_INFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("nFont", wintypes.DWORD),
                ("dwFontSize", COORD), ("FontFamily", wintypes.UINT),
                ("FontWeight", wintypes.UINT), ("FaceName", wintypes.WCHAR * 32)]


if IS_WIN:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    gdi32 = ctypes.windll.gdi32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.BOOL]
    user32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int]
    gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetFocus.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.FreeConsole.restype = wintypes.BOOL
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.AttachConsole.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                                  ctypes.POINTER(CONSOLE_FONT_INFOEX)]
    kernel32.GetCurrentConsoleFontEx.restype = wintypes.BOOL
    kernel32.SetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                                  ctypes.POINTER(CONSOLE_FONT_INFOEX)]
    kernel32.SetCurrentConsoleFontEx.restype = wintypes.BOOL
    kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_STRIP = 0x80000000 | 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
WS_EX_STRIP = 0x00040000 | 0x00000100 | 0x00000200
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x4, 0x10, 0x20, 0x40
WM_CLOSE, WM_SYSCOMMAND = 0x0010, 0x0112
SMTO_ABORTIFHUNG = 0x0002
ID_CONSOLE_COPY, ID_CONSOLE_PASTE = 0xFFF0, 0xFFF1
ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
SW_HIDE, SW_SHOW = 0, 5
TERMINAL_INSET, TERMINAL_CORNER = 12, 18

def descendant_pids(root):
    """PID de tous les processus enfants (récursif) de `root`."""
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    children = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    out, stack = [], [root]
    while stack:
        for c in children.get(stack.pop(), []):
            out.append(c)
            stack.append(c)
    return out


class EmbeddedConsole:
    """Lance une vraie console Windows (conhost) et l'intègre dans `host` (tk.Frame)."""

    def __init__(self, host, lines, pause_on_success=False):
        self.host = host
        self.lines = lines
        self.pause_on_success = pause_on_success
        self.token = "INNSSH_" + uuid.uuid4().hex[:10]
        self.proc = None
        self.hwnd = None
        self.script = None
        self.closed = False
        self._tid = None
        self._deadline = 0
        self._on_fail = None

    def _write_script(self):
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

    def start(self, on_fail):
        self._on_fail = on_fail
        self._write_script()
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        self.proc = subprocess.Popen(f'conhost.exe cmd.exe /c ""{self.script}""',
                                     startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)
        self._deadline = time.time() + 10
        self.host.after(120, self._try_attach)

    def _find_window(self):
        found = []

        @WNDENUMPROC
        def cb(hwnd, _):
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

        user32.EnumWindows(cb, 0)
        return found[0] if found else None

    def _try_attach(self):
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
    def _set_console_font(pid):
        parent_console = bool(kernel32.GetConsoleWindow())
        if parent_console:
            kernel32.FreeConsole()
        try:
            if not kernel32.AttachConsole(pid):
                return
            handle = kernel32.CreateFileW("CONOUT$", 0xC0000000, 0x3, None, 3, 0, None)
            if not handle or handle == ctypes.c_void_p(-1).value:
                return
            try:
                info = CONSOLE_FONT_INFOEX()
                info.cbSize = ctypes.sizeof(info)
                if kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(info)):
                    info.FaceName = "Cascadia Mono"
                    info.FontFamily = 0x31
                    info.FontWeight = 400
                    info.dwFontSize = COORD(10, 20)
                    kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(info))
            finally:
                kernel32.CloseHandle(handle)
        finally:
            kernel32.FreeConsole()
            if parent_console:
                kernel32.AttachConsole(0xFFFFFFFF)

    def _embed(self, hwnd):
        self.host.update_idletasks()
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        style = (style & ~WS_STRIP) | WS_CHILD | WS_VISIBLE
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & 0x7FFFFFFF)
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex & ~WS_EX_STRIP) & 0x7FFFFFFF)
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

    def _on_resize(self, _event=None):
        if self.closed:
            return
        w, h = self.host.winfo_width(), self.host.winfo_height()
        if w <= 1 or h <= 1:
            return
        region = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1,
                                          TERMINAL_CORNER * 2, TERMINAL_CORNER * 2)
        if region and not user32.SetWindowRgn(self.host.winfo_id(), region, True):
            gdi32.DeleteObject(region)
        if self.hwnd:
            inset = min(TERMINAL_INSET, (w - 1) // 2, (h - 1) // 2)
            user32.MoveWindow(self.hwnd, inset, inset, w - 2 * inset, h - 2 * inset, True)

    def focus(self):
        if self.hwnd and user32.IsWindow(self.hwnd):
            user32.SetFocus(self.hwnd)

    def set_visible(self, visible):
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

    def has_focus(self):
        return bool(self.hwnd) and user32.GetFocus() == self.hwnd

    def _post_edit_command(self, cmd_id):
        if self.hwnd and user32.IsWindow(self.hwnd):
            user32.PostMessageW(self.hwnd, WM_SYSCOMMAND, cmd_id, 0)

    def copy(self):
        """Copie la sélection courante (équivalent du menu Modifier > Copier de la console)."""
        self._post_edit_command(ID_CONSOLE_COPY)

    def paste(self):
        """Colle le presse-papiers (équivalent du menu Modifier > Coller de la console)."""
        self._post_edit_command(ID_CONSOLE_PASTE)

    def ensure_quickedit(self):
        """Active le mode QuickEdit de la console : c'est lui qui donne le comportement natif
        « sélection à la souris, clic droit = copier si sélection, sinon coller »."""
        if self.closed or not self.proc:
            return
        try:
            for pid in descendant_pids(self.proc.pid):
                kernel32.FreeConsole()
                if not kernel32.AttachConsole(pid):
                    continue
                try:
                    h = kernel32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
                    if h and h != ctypes.c_void_p(-1).value:
                        mode = wintypes.DWORD()
                        if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                            kernel32.SetConsoleMode(h, mode.value | ENABLE_QUICK_EDIT_MODE | ENABLE_EXTENDED_FLAGS)
                        kernel32.CloseHandle(h)
                finally:
                    kernel32.FreeConsole()
                return
        except Exception:
            pass

    def is_alive(self):
        if self.hwnd:
            return bool(user32.IsWindow(self.hwnd))
        return self.proc is not None and self.proc.poll() is None

    def contains_cursor(self):
        if not (self.hwnd and user32.IsWindow(self.hwnd)):
            return False
        pt, rc = wintypes.POINT(), wintypes.RECT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.GetWindowRect(self.hwnd, ctypes.byref(rc))
        return rc.left <= pt.x < rc.right and rc.top <= pt.y < rc.bottom

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
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
                                   capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=3)
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

class HostDialog(ctk.CTkToplevel):
    NO_GROUP = "(Sans groupe)"

    def __init__(self, app, block=None, preset_group=""):
        super().__init__(app)
        self.configure(fg_color=BG)
        self.app, self.block = app, block
        self.title("Modifier la connexion" if block else "Nouvelle connexion")
        self.geometry("560x740")
        self.resizable(False, False)
        self.transient(app)

        main, adv, meta = {}, [], {}
        if block:
            main, adv = block.split_body()
            meta = app.store.meta(block.alias)

        ctk.CTkLabel(self, text="Modifier la connexion" if block else "Nouvelle connexion",
                     font=font(20, "bold"), text_color=TEXT).pack(anchor="w", padx=26, pady=(22, 2))
        ctk.CTkLabel(self, text="Ces options sont écrites dans votre fichier ~/.ssh/config.",
                     font=font(12), text_color=MUTED).pack(anchor="w", padx=26, pady=(0, 6))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=BORDER,
                                        scrollbar_button_hover_color=HOVER)
        scroll.pack(fill="both", expand=True, padx=14)

        self._section(scroll, "IDENTITÉ")
        self.e_label = self._field(scroll, "Nom d'affichage", meta.get("label", ""), "Ex : Serveur web production")
        self.e_alias = self._field(scroll, "Alias SSH  (utilisé par « ssh alias »)", block.alias if block else "")

        self._section(scroll, "CONNEXION")
        self.e_host = self._field(scroll, "HostName  (IP ou nom DNS)", main.get("HostName", ""))
        row2 = ctk.CTkFrame(scroll, fg_color="transparent")
        row2.pack(fill="x", padx=12)
        row2.columnconfigure(0, weight=3)
        row2.columnconfigure(1, weight=1)
        self.e_user = self._field(row2, "User", main.get("User", ""), grid=(0, 0, (0, 8)))
        self.e_port = self._field(row2, "Port", main.get("Port", ""), "22", grid=(0, 1, (0, 0)))

        self._label(scroll, "IdentityFile  (clé privée)")
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", padx=12)
        self.e_key = make_entry(row)
        self.e_key.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.e_key.insert(0, main.get("IdentityFile", ""))
        btn_ghost(row, "Parcourir…", self._browse_key, width=100).pack(side="left")
        self.e_proxy = self._field(scroll, "ProxyJump  (rebond)", main.get("ProxyJump", ""), "ex : bastion")

        self._section(scroll, "ORGANISATION")
        self._label(scroll, "Groupe")
        self.group_values = {app.store.group_label(path): path for path in app.store.groups}
        self.cb_group = ctk.CTkComboBox(scroll, values=[self.NO_GROUP] + list(self.group_values), fg_color="#171c26",
                                        border_color=BORDER, button_color=CARD, button_hover_color=HOVER,
                                        dropdown_fg_color=CARD, dropdown_hover_color=HOVER,
                                        dropdown_text_color=TEXT,
                                        dropdown_font=font(12), corner_radius=8,
                                        height=34, font=font(12), text_color=TEXT, state="readonly")
        self.cb_group.pack(fill="x", padx=12)
        selected_group = meta.get("group") or preset_group
        self.cb_group.set(app.store.group_label(selected_group) if selected_group else self.NO_GROUP)

        self._label(scroll, "Couleur")
        self.color = meta.get("color", "")
        crow = ctk.CTkFrame(scroll, fg_color="transparent")
        crow.pack(fill="x", padx=12)
        self.swatches = {}
        for name, hexv in COLORS.items():
            b = ctk.CTkButton(crow, text="", width=28, height=28, corner_radius=14,
                              fg_color=hexv or DEFAULT_DOT, hover_color=hexv or "#6b7280",
                              text_color="#0d1016", font=font(13, "bold"),
                              command=lambda h=hexv: self._pick_color(h))
            b.pack(side="left", padx=(0, 6))
            self.swatches[hexv] = b
        self._pick_color(self.color)

        self.var_fav = ctk.BooleanVar(value=bool(meta.get("favorite")))
        ctk.CTkSwitch(scroll, text="Ajouter aux favoris ★", variable=self.var_fav, progress_color=ACCENT,
                      font=font(12), text_color=TEXT).pack(anchor="w", padx=14, pady=(14, 2))
        self.e_note = self._field(scroll, "Note", meta.get("note", ""))

        self._section(scroll, "AVANCÉ")
        self._label(scroll, "Autres options (lignes ssh_config brutes)")
        self.t_adv = ctk.CTkTextbox(scroll, height=100, font=("Consolas", 12), fg_color="#171c26",
                                    border_color=BORDER, border_width=1, corner_radius=8)
        self.t_adv.pack(fill="x", padx=12, pady=(0, 10))
        self.t_adv.insert("1.0", "\n".join(adv))

        self.lbl_err = ctk.CTkLabel(self, text="", text_color="#f87171", wraplength=500, font=font(12))
        self.lbl_err.pack(pady=(4, 0))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=26, pady=(6, 18))
        btn_primary(bar, "Enregistrer", self._save, width=150).pack(side="right")
        btn_ghost(bar, "Annuler", self.destroy, width=100, height=38).pack(side="right", padx=(0, 8))

        self.after(200, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus()
        except Exception:
            pass

    def _section(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=font(10, "bold"), text_color=ACCENT, anchor="w").pack(
            fill="x", padx=14, pady=(16, 0))

    def _label(self, parent, text):
        ctk.CTkLabel(parent, text=text, anchor="w", font=font(12), text_color=MUTED).pack(
            fill="x", padx=14, pady=(8, 3))

    def _field(self, parent, label, value="", placeholder="", grid=None):
        if grid is None:
            self._label(parent, label)
            e = make_entry(parent, placeholder_text=placeholder)
            e.pack(fill="x", padx=12)
        else:
            col_frame = ctk.CTkFrame(parent, fg_color="transparent")
            col_frame.grid(row=grid[0], column=grid[1], sticky="ew", padx=grid[2])
            self._label(col_frame, label)
            e = make_entry(col_frame, placeholder_text=placeholder)
            e.pack(fill="x", padx=(2, 2))
        if value:
            e.insert(0, value)
        return e

    def _browse_key(self):
        path = filedialog.askopenfilename(parent=self, initialdir=SSH_DIR if SSH_DIR.exists() else Path.home(),
                                          title="Sélectionner la clé privée")
        if path:
            self.e_key.delete(0, "end")
            self.e_key.insert(0, path.replace("\\", "/"))

    def _pick_color(self, hexv):
        self.color = hexv
        for h, b in self.swatches.items():
            b.configure(text="✓" if h == hexv else "")

    def _save(self):
        group_value = self.cb_group.get().strip()
        group = "" if group_value in ("", self.NO_GROUP) else self.group_values.get(group_value, group_value)
        adv_raw = self.t_adv.get("1.0", "end").rstrip("\n")
        d = {
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
        err = self.app.save_host(self.block, d)
        if err:
            self.lbl_err.configure(text=err)
        else:
            self.destroy()

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
        self.sessions = {}
        self._active_dropdown = None
        self._active_submenu = None
        self._active_submenu_anchor = None
        self.closed_groups = set()
        self._gtext = {}
        self._gpath = {}
        self._drag = None
        self._dots = {}

        self.bind_all("<ButtonPress-1>", self._dismiss_dropdown, add="+")
        self._build_sidebar()
        self._build_main()
        self.refresh_tree()
        self._build_home()

        self.bind("<Control-f>", lambda e: self.e_search.focus_set())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if IS_WIN:
            self.after(800, self._poll_sessions)
            self.after(50, self._pointer_poll)
        self.after(2500, self._poll_config)

    def _dot(self, hexv):
        hexv = hexv or DEFAULT_DOT
        if hexv not in self._dots:
            self._dots[hexv] = make_dot(hexv)
        return self._dots[hexv]

    def _build_sidebar(self):
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
        self.e_search = make_entry(side, textvariable=self.search_var, placeholder_text="Rechercher une connexion...",
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

        tf = tk.Frame(side, bg=SIDE, highlightthickness=0)
        tf.pack(fill="both", expand=True, padx=(12, 8), pady=(0, 8))
        self.tree = ttk.Treeview(tf, style="Ssh.Treeview", show="tree", selectmode="browse")
        sb = ctk.CTkScrollbar(tf, command=self.tree.yview, fg_color="transparent",
                              button_color=BORDER, button_hover_color=HOVER, width=10)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 2))
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("group", font=("Segoe UI", 10, "bold"), foreground=ACCENT)

        card = ctk.CTkFrame(side, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=14, pady=(0, 16))
        self.d_title = ctk.CTkLabel(card, text="Aucune sélection", font=font(14, "bold"), text_color=TEXT,
                                    anchor="w", wraplength=270)
        self.d_title.pack(fill="x", padx=16, pady=(14, 0))
        self.d_info = ctk.CTkLabel(card, text="Sélectionnez une connexion dans la liste.", font=font(11),
                                   text_color=MUTED, anchor="w", justify="left", wraplength=270)
        self.d_info.pack(fill="x", padx=16, pady=(4, 10))
        brow = ctk.CTkFrame(card, fg_color="transparent")
        brow.pack(fill="x", padx=16, pady=(0, 14))
        self.btn_d_connect = btn_primary(brow, "Connecter", self._connect_selected, height=34, state="disabled")
        self.btn_d_connect.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_d_edit = btn_ghost(brow, "✎", self._edit_selected, width=40, state="disabled")
        self.btn_d_edit.pack(side="left")

        t = self.tree
        t.bind("<<TreeviewSelect>>", self._on_select)
        t.bind("<<TreeviewOpen>>", lambda e: self._sync_group(t.focus()))
        t.bind("<<TreeviewClose>>", lambda e: self._sync_group(t.focus()))
        t.bind("<Double-1>", self._on_double)
        t.bind("<Button-3>", self._on_right_click)
        t.bind("<Return>", lambda e: self._connect_selected())
        t.bind("<F2>", lambda e: self.rename_selected())
        t.bind("<Delete>", lambda e: self.delete_selected())
        t.bind("<ButtonPress-1>", self._dnd_press, add="+")
        t.bind("<B1-Motion>", self._dnd_motion, add="+")
        t.bind("<ButtonRelease-1>", self._dnd_release, add="+")

    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=(14, 18), pady=16)

        top = ctk.CTkFrame(main, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10))
        self.btn_term = btn_ghost(top, "Terminal local  ▾", self._local_terminal_menu)
        self.btn_term.pack(side="left")
        btn_ghost(top, "Fermer l'onglet", self.close_current_tab, fg_color=DANGER, hover_color=DANGER_H,
                  border_color="#ad1212").pack(side="right")

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

    def _bind_tab_close(self, name):
        try:
            button = self.tabs._segmented_button._buttons_dict[name]
            button.bind("<Button-2>", lambda event, tab=name: self._close_tab_from_middle(event, tab), add="+")
        except (AttributeError, KeyError):
            pass

    def _close_tab_from_middle(self, event, name):
        if name in self.sessions:
            self._close_tab(name)
        return "break"

    def _build_home(self):
        f = self.home
        for w in f.winfo_children():
            w.destroy()
        hosts = self.cfg.hosts()
        favs = [b for b in hosts if self.store.meta(b.alias).get("favorite")]

        ctk.CTkLabel(f, text="Vos connexions SSH", font=font(26, "bold"), text_color=TEXT).pack(
            anchor="w", padx=30, pady=(28, 0))
        ctk.CTkLabel(f, text="Double-cliquez sur une connexion pour l'ouvrir dans un onglet.",
                     font=font(13), text_color=MUTED).pack(anchor="w", padx=30, pady=(2, 20))

        stats = ctk.CTkFrame(f, fg_color="transparent")
        stats.pack(fill="x", padx=30)
        for i, (value, label) in enumerate([(len(hosts), "Connexions"), (len(self.store.groups), "Groupes"),
                                            (len(favs), "Favoris")]):
            card = ctk.CTkFrame(stats, fg_color=CARD, corner_radius=10, border_width=1, border_color=BORDER)
            card.grid(row=0, column=i, padx=(0, 12), sticky="w")
            ctk.CTkLabel(card, text=str(value), font=font(26, "bold"), text_color=ACCENT).pack(
                anchor="w", padx=22, pady=(14, 0))
            ctk.CTkLabel(card, text=label, font=font(12), text_color=MUTED).pack(anchor="w", padx=22, pady=(0, 14))

        if favs:
            ctk.CTkLabel(f, text="FAVORIS", font=font(10, "bold"), text_color=ACCENT).pack(
                anchor="w", padx=32, pady=(26, 8))
            grid = ctk.CTkFrame(f, fg_color="transparent")
            grid.pack(fill="x", padx=30)
            for i in range(3):
                grid.columnconfigure(i, weight=1, uniform="fav")
            for i, b in enumerate(sorted(favs, key=lambda x: self.store.label(x.alias).lower())[:9]):
                ctk.CTkButton(grid, text=f"★   {self.store.label(b.alias)}", anchor="w", height=42,
                              fg_color=CARD, hover_color=HOVER, border_width=1, border_color=BORDER,
                              corner_radius=10, text_color=TEXT, font=font(12),
                              command=lambda a=b.alias: self.connect(a)).grid(
                    row=i // 3, column=i % 3, padx=(0, 10), pady=(0, 10), sticky="ew")

    def _matches(self, b, q):
        if not q:
            return True
        m, main = self.store.meta(b.alias), b.split_body()[0]
        hay = " ".join([b.alias, m.get("label", ""), self.store.group_label(m.get("group", "")), m.get("note", ""),
                        main.get("HostName", ""), main.get("User", "")]).lower()
        return q in hay

    @staticmethod
    def _is_open(v):
        return str(v).lower() in ("1", "true")

    def _group_key(self, iid):
        if iid == self.FAV:
            return self.FAV_KEY
        return self._gpath.get(iid, iid[2:])

    def _group_text(self, iid):
        opened = self._is_open(self.tree.item(iid, "open"))
        return f"{'▾' if opened else '▸'}   {self._gtext[iid]}"

    def _sync_group(self, iid):
        if iid not in self._gtext or not self.tree.exists(iid):
            return
        opened = self._is_open(self.tree.item(iid, "open"))
        (self.closed_groups.discard if opened else self.closed_groups.add)(self._group_key(iid))
        self.tree.item(iid, text=self._group_text(iid))

    def _toggle_group(self, iid):
        opened = self._is_open(self.tree.item(iid, "open"))
        self.tree.item(iid, open=not opened)
        self._sync_group(iid)

    def refresh_tree(self):
        tree = self.tree
        sel = tree.selection()
        tree.delete(*tree.get_children())
        self._gtext = {}
        self._gpath = {}
        q = self.search_var.get().strip().lower()
        hosts = [b for b in self.cfg.hosts() if self._matches(b, q)]
        by_group = {}
        for b in hosts:
            by_group.setdefault(self.store.meta(b.alias).get("group", ""), []).append(b)

        children = {}
        counts = {path: len(by_group.get(path, [])) for path in self.store.groups}
        for path in self.store.groups:
            children.setdefault(self.store.group_parent(path), []).append(path)
        for path in sorted(self.store.groups, key=lambda item: item.count(GROUP_SEPARATOR), reverse=True):
            parent = self.store.group_parent(path)
            if parent in counts:
                counts[parent] += counts[path]

        visible_groups = set()
        if q:
            for path, items in by_group.items():
                if items:
                    while path:
                        visible_groups.add(path)
                        path = self.store.group_parent(path)

        def add_group(path, parent_iid=""):
            if q and path not in visible_groups:
                return
            iid = f"g:{quote(path, safe='')}"
            self._gpath[iid] = path
            opened = path not in self.closed_groups or bool(q)
            items = by_group.get(path, [])
            self._gtext[iid] = f"{self.store.group_name(path)}  ·  {counts.get(path, len(items))}"
            tree.insert(parent_iid, "end", iid=iid, open=opened, tags=("group",))
            tree.item(iid, text=self._group_text(iid))
            for b in sorted(items, key=lambda x: self.store.label(x.alias).lower()):
                color = self.store.meta(b.alias).get("color", "")
                tree.insert(iid, "end", iid=f"h:{b.alias}", text="  " + self.store.label(b.alias),
                            image=self._dot(color))

            for child in children.get(path, []):
                add_group(child, iid)

        favs = [b for b in hosts if self.store.meta(b.alias).get("favorite")]
        if favs:
            iid = self.FAV
            self._gtext[iid] = f"★ Favoris  ·  {len(favs)}"
            tree.insert("", "end", iid=iid, open=self.FAV_KEY not in self.closed_groups, tags=("group",))
            tree.item(iid, text=self._group_text(iid))
            for b in sorted(favs, key=lambda x: self.store.label(x.alias).lower()):
                tree.insert(iid, "end", iid=f"f:{b.alias}", text="  " + self.store.label(b.alias),
                            image=self._dot(self.store.meta(b.alias).get("color", "")))
        for path in children.get("", []):
            add_group(path)
        if by_group.get(""):
            iid = "g:"
            self._gtext[iid] = f"Sans groupe  ·  {len(by_group[''])}"
            tree.insert("", "end", iid=iid, open=True, tags=("group",))
            tree.item(iid, text=self._group_text(iid))
            for b in sorted(by_group[""], key=lambda x: self.store.label(x.alias).lower()):
                tree.insert(iid, "end", iid=f"h:{b.alias}", text="  " + self.store.label(b.alias),
                            image=self._dot(self.store.meta(b.alias).get("color", "")))

        for s in sel:
            if tree.exists(s):
                tree.selection_set(s)
        self._on_select()

    @staticmethod
    def _alias_of(iid):
        return iid[2:] if iid[:2] in ("h:", "f:") else None

    def _selected(self):
        s = self.tree.selection()
        return s[0] if s else ""

    def _on_select(self, _e=None):
        iid = self._selected()
        alias = self._alias_of(iid) if iid else None
        b = self.cfg.find(alias) if alias else None
        if not b:
            self.d_title.configure(text="Aucune sélection")
            self.d_info.configure(text="Sélectionnez une connexion dans la liste.")
            self.btn_d_connect.configure(state="disabled")
            self.btn_d_edit.configure(state="disabled")
            return
        main, m = b.split_body()[0], self.store.meta(alias)
        lines = [f"{main.get('User', '?')}@{main.get('HostName', alias)}:{main.get('Port', '22')}"]
        if alias != self.store.label(alias):
            lines.insert(0, f"alias : {alias}")
        if main.get("IdentityFile"):
            lines.append("clé : " + main["IdentityFile"])
        if main.get("ProxyJump"):
            lines.append("via : " + main["ProxyJump"])
        if m.get("note"):
            lines.append("📝 " + m["note"])
        self.d_title.configure(text=self.store.label(alias))
        self.d_info.configure(text="\n".join(lines))
        self.btn_d_connect.configure(state="normal")
        self.btn_d_edit.configure(state="normal")

    def _after_change(self):
        self._stamp = self.cfg.stamp()
        self.store.save()
        self.refresh_tree()
        self._build_home()

    def _poll_config(self):
        try:
            if self.cfg.stamp() != self._stamp:
                self.cfg.load()
                self._stamp = self.cfg.stamp()
                self.refresh_tree()
                self._build_home()
        finally:
            self.after(2500, self._poll_config)

    def new_host(self, group=""):
        HostDialog(self, None, group)

    def edit_host(self, alias):
        b = self.cfg.find(alias)
        if b:
            HostDialog(self, b)

    def _edit_selected(self):
        alias = self._alias_of(self._selected())
        if alias:
            self.edit_host(alias)

    def save_host(self, block, d):
        alias = d["alias"]
        if not re.fullmatch(r"[\w.@:+\-]+", alias):
            return "Alias invalide (lettres, chiffres, . _ - @ : + uniquement, sans espace ni joker)."
        if any(b is not block and alias in b.patterns for b in self.cfg.hosts()):
            return "Cet alias existe déjà dans votre configuration."
        port = d["main"].get("Port", "")
        if port and not port.isdigit():
            return "Le port doit être un nombre."
        is_new = block is None
        old = None if is_new else block.alias
        try:
            if is_new:
                block = self.cfg.new_block(alias)
            patterns = [alias] + ([] if is_new else block.patterns[1:])
            block.update(patterns, d["main"], d["adv"])
            block.file.save()
        except OSError as e:
            return f"Impossible d'écrire le fichier config : {e}"
        if old and old != alias:
            self.store.rename_alias(old, alias)
        m = self.store.meta(alias)
        m.update(label=d["label"], group=d["group"], color=d["color"], note=d["note"], favorite=d["favorite"])
        if d["group"] and d["group"] not in self.store.groups:
            self.store.ensure_group(d["group"])
        self._after_change()
        return None

    def duplicate_host(self, alias):
        b = self.cfg.find(alias)
        if not b:
            return
        new = alias + "-copie"
        n = 2
        while self.cfg.find(new):
            new, n = f"{alias}-copie{n}", n + 1
        main, adv = b.split_body()
        nb = self.cfg.new_block(new)
        nb.update([new], main, adv)
        nb.file.save()
        self.store.data["hosts"][new] = dict(self.store.meta(alias))
        self.store.meta(new)["label"] = self.store.label(alias) + " (copie)"
        self._after_change()

    def delete_host(self, alias):
        b = self.cfg.find(alias)
        if b and messagebox.askyesno(APP_NAME, f"Supprimer la connexion « {self.store.label(alias)} » "
                                               f"du fichier config ?\n(une sauvegarde .innssh.bak est conservée)"):
            try:
                self.cfg.remove(b)
            except OSError as e:
                return messagebox.showerror(APP_NAME, str(e))
            self.store.data["hosts"].pop(alias, None)
            self._after_change()

    def rename_host(self, alias):
        dlg = ctk.CTkInputDialog(text=f"Nouveau nom d'affichage pour « {self.store.label(alias)} »\n"
                                      f"(vide = utiliser l'alias « {alias} ») :", title="Renommer")
        val = dlg.get_input()
        if val is not None:
            self.store.meta(alias)["label"] = val.strip()
            self._after_change()

    def rename_selected(self):
        iid = self._selected()
        if self._alias_of(iid):
            self.rename_host(self._alias_of(iid))
        elif iid in self._gpath:
            self.rename_group(self._gpath[iid])

    def delete_selected(self):
        iid = self._selected()
        if self._alias_of(iid):
            self.delete_host(self._alias_of(iid))
        elif iid in self._gpath:
            self.delete_group(self._gpath[iid])

    def toggle_favorite(self, alias):
        m = self.store.meta(alias)
        m["favorite"] = not m.get("favorite")
        self._after_change()

    def move_host(self, alias, group):
        self.store.meta(alias)["group"] = group
        if group:
            self.store.ensure_group(group)
        self._after_change()

    def _deploy_to(self, alias):
        pub = filedialog.askopenfilename(
            initialdir=SSH_DIR if SSH_DIR.exists() else Path.home(),
            title="Sélectionner la clé publique à envoyer",
            filetypes=(("Clés publiques SSH", "*.pub"), ("Tous les fichiers", "*.*")))
        if pub:
            line = f'type "{pub.replace("%", "%%")}" | ssh {bat_quote(alias)} "{REMOTE_DEPLOY_CMD}"'
            self.open_script_tab(f"Clé → {self.store.label(alias)}", [line], pause_on_success=True)

    def new_group(self, then_move=None, parent_group=""):
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

    def rename_group(self, old):
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
        for m in self.store.data["hosts"].values():
            m["group"] = renamed.get(m.get("group", ""), m.get("group", ""))
        self.closed_groups = {renamed.get(path, path) for path in self.closed_groups}
        self._after_change()

    def delete_group(self, name):
        subtree = {path for path in self.store.groups
                   if path == name or path.startswith(name + GROUP_SEPARATOR)}
        parent = self.store.group_parent(name)
        destination = self.store.group_label(parent) if parent else "Sans groupe"
        if not messagebox.askyesno(APP_NAME, f"Supprimer le groupe « {self.store.group_label(name)} » et ses sous-groupes ?\n"
                                             f"Leurs connexions seront déplacées dans « {destination} ». "):
            return
        self.store.groups[:] = [path for path in self.store.groups if path not in subtree]
        self.closed_groups.difference_update(subtree)
        for meta in self.store.data["hosts"].values():
            if meta.get("group") in subtree:
                meta["group"] = parent
        self._after_change()

    def move_group(self, name, delta):
        siblings = [path for path in self.store.groups if self.store.group_parent(path) ==
                    self.store.group_parent(name)]
        i = siblings.index(name)
        j = i + delta
        if 0 <= j < len(siblings):
            groups = self.store.groups
            source_index, target_index = groups.index(siblings[i]), groups.index(siblings[j])
            groups[source_index], groups[target_index] = groups[target_index], groups[source_index]
            self._after_change()

    def move_group_to(self, name, parent):
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

    def _menu(self):
        return tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT, activeforeground="white",
                       bd=0, relief="flat", font=("Segoe UI", 10))

    def _dismiss_dropdown(self, event):
        popups = (self._active_dropdown, self._active_submenu)
        for popup in popups:
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

    def _close_dropdowns(self):
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

    def _show_dropdown(self, button, actions, width=250):
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height() + 5
        items = [{"label": label, "command": action} for label, action in actions]
        self._show_menu_at(x, y, items, width)

    def _show_menu_at(self, x, y, items, width=260, submenu=False, anchor=None):
        if submenu:
            if self._active_submenu is not None:
                try:
                    self._active_submenu.destroy()
                except tk.TclError:
                    pass
        else:
            self._close_dropdowns()
        content_height = sum(9 if item is None else 38 for item in items)
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

        def close():
            self._close_dropdowns()

        def invoke(action):
            close()
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

                def open_submenu(button=button, children=item["submenu"]):
                    if self._active_submenu_anchor is button and self._active_submenu is not None:
                        return
                    submenu_x = button.winfo_rootx() + button.winfo_width() - 4
                    submenu_y = button.winfo_rooty() - 4
                    self._show_menu_at(submenu_x, submenu_y, children, width, submenu=True, anchor=button)

                button.bind("<Enter>", lambda _event: open_submenu(), add="+")
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

    def _on_right_click(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        alias = self._alias_of(iid) if iid else None
        if alias:
            fav = self.store.meta(alias).get("favorite")
            groups = [{"label": "Sans groupe", "command": lambda: self.move_host(alias, "")}] + [
                {"label": self.store.group_label(group), "command": lambda group=group: self.move_host(alias, group)}
                for group in self.store.groups]
            groups += [{"label": "Nouveau groupe…", "command": lambda: self.new_group(then_move=alias)}]
            items = [
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
        elif iid in self._gpath:
            name = self._gpath[iid]
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
        else:
            items = [
                {"label": "Nouvelle connexion", "command": lambda: self.new_host()},
                {"label": "Nouveau groupe", "command": self.new_group},
            ]
        self._show_menu_at(e.x_root, e.y_root, items, width=290)

    def _dnd_press(self, e):
        self._drag = {"iid": self.tree.identify_row(e.y), "x": e.x, "y": e.y, "on": False}

    def _dnd_motion(self, e):
        d = self._drag
        if not d or not d["iid"] or d["on"]:
            return
        if abs(e.x - d["x"]) + abs(e.y - d["y"]) > 8 and (d["iid"].startswith("h:") or
                                                           (d["iid"].startswith("g:") and d["iid"] != "g:")):
            d["on"] = True
            self.tree.configure(cursor="hand2")

    def _dnd_release(self, e):
        d, self._drag = self._drag, None
        self.tree.configure(cursor="")
        if not d:
            return
        if d["on"]:
            tgt = self.tree.identify_row(e.y)
            if tgt and tgt != d["iid"]:
                self._drop(d["iid"], tgt)
        elif d["iid"] in self._gtext:
            self._toggle_group(d["iid"])

    def _drop(self, src, tgt):
        if src.startswith("h:"):
            alias = src[2:]
            if tgt == self.FAV:
                self.store.meta(alias)["favorite"] = True
                return self._after_change()
            if tgt == "g:":
                group = ""
            elif tgt in self._gpath:
                group = self._gpath[tgt]
            elif tgt[:2] in ("h:", "f:"):
                group = self.store.meta(tgt[2:]).get("group", "")
            else:
                return
            self.move_host(alias, group)
        elif src in self._gpath and tgt in self._gpath:
            self.move_group_to(self._gpath[src], self._gpath[tgt])

    def _on_double(self, e):
        iid = self.tree.identify_row(e.y)
        alias = self._alias_of(iid) if iid else None
        if alias:
            self.connect(alias)
        return "break"

    def _connect_selected(self):
        alias = self._alias_of(self._selected())
        if alias:
            self.connect(alias)

    def _settings_menu(self):
        self._show_dropdown(self.btn_settings, [
            ("Recharger la configuration", lambda: (self.cfg.load(), self._after_change())),
            ("Modifier le fichier config (Bloc-notes)",
             lambda: subprocess.Popen(["notepad", str(CONFIG_PATH)])),
            ("Ouvrir le dossier .ssh",
             lambda: os.startfile(SSH_DIR) if IS_WIN and SSH_DIR.exists() else None),
        ], width=270)

    def _local_terminal_menu(self):
        self._show_dropdown(self.btn_term, [
            ("PowerShell", lambda: self.open_script_tab(
                "PowerShell", ['cd /d "%USERPROFILE%"', "powershell -NoLogo"])),
            ("Invite de commandes (cmd)", lambda: self.open_script_tab(
                "cmd", ['cd /d "%USERPROFILE%"', "cmd /k"])),
        ])

    def connect(self, alias):
        self.open_script_tab(self.store.label(alias), [f"ssh {bat_quote(alias)}"])

    def connect_external(self, alias):
        if IS_WIN:
            subprocess.Popen(f'start "{self.store.label(alias)}" ssh {bat_quote(alias)}', shell=True)

    def open_script_tab(self, title, lines, pause_on_success=False):
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
        try:
            console.start(on_fail=lambda msg, n=name: self._session_failed(n, msg))
        except Exception as ex:
            self._close_tab(name)
            messagebox.showerror(APP_NAME, f"Impossible de lancer la console :\n{ex}")

    def _session_failed(self, name, msg):
        self._close_tab(name)
        messagebox.showerror(APP_NAME, msg)

    def _close_tab(self, name):
        if name not in self.sessions:
            return
        tab_names = [self.HOME_TAB, *self.sessions]
        current = self.tabs.get()
        target = current
        if current == name:
            remaining_tabs = [tab for tab in tab_names if tab != name]
            if remaining_tabs:
                target = remaining_tabs[max(0, tab_names.index(name) - 1)]

        c = self.sessions.pop(name, None)
        if c:
            c.close()
            try:
                c.host.destroy()
            except tk.TclError:
                pass
        self.tabs.delete(name)
        self.after(120, lambda tab=target: self._restore_tab_after_close(tab))

    def _restore_tab_after_close(self, name):
        if name not in self.tabs._tab_dict:
            return
        self.tabs.set(name)
        self.after(80, self._focus_current)

    def close_current_tab(self):
        name = self.tabs.get()
        if name in self.sessions:
            self._close_tab(name)

    def _current_console(self):
        return self.sessions.get(self.tabs.get())

    def copy_current(self):
        c = self._current_console()
        if c:
            c.copy()

    def paste_current(self):
        c = self._current_console()
        if c:
            c.paste()
            c.focus()

    def _on_tab_change(self):
        self.after(80, self._focus_current)

    def _focus_current(self):
        current = self.tabs.get()
        for name, console in self.sessions.items():
            console.set_visible(name == current)
        c = self.sessions.get(current)
        if c:
            c.focus()
            c.ensure_quickedit()

    def _poll_sessions(self):
        try:
            for name, c in list(self.sessions.items()):
                if not c.is_alive():
                    self._close_tab(name)
        finally:
            self.after(800, self._poll_sessions)

    def _app_is_foreground(self):
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        return pid.value == os.getpid()

    def _pointer_poll(self):
        """La console prend le focus dès que la souris la survole : la sélection à la souris et le
        clic droit (copier / coller) sont ainsi traités par Windows exactement comme dans un terminal natif."""
        try:
            c = self._current_console()
            if (c and c.hwnd and not self.grab_current() and self._app_is_foreground()
                    and c.contains_cursor() and not c.has_focus()):
                c.focus()
        except Exception:
            pass
        finally:
            self.after(50, self._pointer_poll)

    def _on_close(self):
        if self.sessions and not messagebox.askyesno(
                APP_NAME, f"{len(self.sessions)} session(s) ouverte(s). Tout fermer et quitter ?"):
            return
        for name in list(self.sessions):
            self._close_tab(name)
        self.destroy()


if __name__ == "__main__":
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("innlog_ssh_manager")
        except Exception:
            pass
    App().mainloop()