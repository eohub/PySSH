"""Lecture / écriture non destructive de ~/.ssh/config (et de ses `Include`)."""
from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from .constants import BACKUP_SUFFIX, CONFIG_PATH, LOWER_MAIN, MAIN_KEYS, SSH_DIR

HEADER_RE = re.compile(r"^\s*(host|match)\s+(.+?)\s*$", re.I)
KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*)(?:\s*=\s*|\s+)(.*?)\s*$")
INCLUDE_RE = re.compile(r"^\s*include\s+(.+?)\s*$", re.I)
MAX_INCLUDE_DEPTH = 5


class HostBlock:
    """Un bloc `Host ...` ou `Match ...` avec ses commentaires et ses lignes."""

    def __init__(self, kind: str, header: str, patterns: list[str], cfile: "ConfigFile"):
        self.kind = kind
        self.header = header
        self.patterns = patterns
        self.lead: list[str] = []   # commentaires placés juste avant l'en-tête
        self.body: list[str] = []
        self.file = cfile

    @property
    def alias(self) -> str:
        return self.patterns[0] if self.patterns else ""

    @property
    def is_real(self) -> bool:
        """Vrai pour un `Host` concret (sans joker `*`, `?` ou `!`)."""
        return (self.kind == "Host" and bool(self.patterns)
                and not any(c in p for p in self.patterns for c in "*?!"))

    def split_body(self) -> tuple[dict[str, str], list[str]]:
        """Sépare les options principales (éditées via l'UI) des lignes avancées."""
        main: dict[str, str] = {}
        adv: list[str] = []
        for line in self.body:
            match = None if line.lstrip().startswith("#") else KV_RE.match(line)
            key = LOWER_MAIN.get(match.group(1).lower()) if match else None
            if key and key not in main:
                main[key] = match.group(2)
            else:
                adv.append(line)
        while adv and not adv[-1].strip():
            adv.pop()
        return main, adv

    def update(self, patterns: list[str], main: dict[str, str], adv: list[str]) -> None:
        self.patterns = patterns
        self.header = "Host " + " ".join(patterns)
        body = [f"    {k} {main[k].strip()}" for k in MAIN_KEYS if main.get(k, "").strip()]
        body += [line.rstrip() for line in adv]
        body.append("")
        self.body = body


class ConfigFile:
    """Un fichier de configuration SSH découpé en préambule + blocs."""

    _backed_up: set[str] = set()

    def __init__(self, path):
        self.path = Path(path)
        self.preamble: list[str] = []
        self.blocks: list[HostBlock] = []
        self.newline = "\n"

    def load(self) -> None:
        self.preamble, self.blocks = [], []
        if not self.path.exists():
            return
        raw = self.path.read_bytes().decode("utf-8-sig", errors="replace")
        self.newline = "\r\n" if "\r\n" in raw else "\n"
        current: Optional[HostBlock] = None
        for line in raw.splitlines():
            match = HEADER_RE.match(line)
            if match:
                target = current.body if current else self.preamble
                lead: list[str] = []
                while target and target[-1].lstrip().startswith("#"):
                    lead.insert(0, target.pop())
                kind = match.group(1).capitalize()
                patterns = ([p.strip('"') for p in match.group(2).split()]
                            if kind == "Host" else [match.group(2)])
                current = HostBlock(kind, line, patterns, self)
                current.lead = lead
                self.blocks.append(current)
            elif current:
                current.body.append(line)
            else:
                self.preamble.append(line)

    def save(self) -> None:
        lines = list(self.preamble)
        for block in self.blocks:
            lines += block.lead + [block.header] + block.body
        text = self.newline.join(lines)
        if not text.endswith(self.newline):
            text += self.newline
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and str(self.path) not in ConfigFile._backed_up:
            shutil.copy2(self.path, self.path.with_name(self.path.name + BACKUP_SUFFIX))
            ConfigFile._backed_up.add(str(self.path))
        self.path.write_bytes(text.encode("utf-8"))


class SSHConfig:
    """Ensemble du fichier config principal et des fichiers inclus."""

    def __init__(self):
        self.files: list[ConfigFile] = []
        self.load()

    def load(self) -> None:
        main = ConfigFile(CONFIG_PATH)
        main.load()
        self.files = [main]
        visited = {str(CONFIG_PATH.resolve())}
        self._load_includes(main, visited, 0)

    def _load_includes(self, cfile: ConfigFile, visited: set[str], depth: int) -> None:
        if depth > MAX_INCLUDE_DEPTH:
            return
        for line in cfile.preamble:
            match = INCLUDE_RE.match(line)
            if not match:
                continue
            for token in match.group(1).split():
                pattern = os.path.expanduser(token.strip('"'))
                if not os.path.isabs(pattern):
                    pattern = str(SSH_DIR / pattern)
                for path in sorted(glob.glob(pattern)):
                    resolved = str(Path(path).resolve())
                    if resolved in visited or not os.path.isfile(path):
                        continue
                    visited.add(resolved)
                    sub = ConfigFile(path)
                    sub.load()
                    self.files.append(sub)
                    self._load_includes(sub, visited, depth + 1)

    def hosts(self) -> list[HostBlock]:
        return [b for f in self.files for b in f.blocks if b.is_real]

    def find(self, alias: str) -> Optional[HostBlock]:
        return next((b for b in self.hosts() if b.alias == alias), None)

    def new_block(self, alias: str) -> HostBlock:
        cfile = self.files[0]
        if cfile.blocks:
            last = cfile.blocks[-1]
            if not last.body or last.body[-1].strip():
                last.body.append("")
        elif cfile.preamble and cfile.preamble[-1].strip():
            cfile.preamble.append("")
        block = HostBlock("Host", f"Host {alias}", [alias], cfile)
        cfile.blocks.append(block)
        return block

    def remove(self, block: HostBlock) -> None:
        block.file.blocks.remove(block)
        block.file.save()

    def stamp(self) -> tuple:
        """Empreinte (chemin, mtime) des fichiers, pour détecter les modifications externes."""
        out = []
        for cfile in self.files:
            try:
                out.append((str(cfile.path), cfile.path.stat().st_mtime_ns))
            except OSError:
                out.append((str(cfile.path), 0))
        return tuple(out)
