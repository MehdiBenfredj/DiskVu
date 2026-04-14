#!/usr/bin/env python3
"""
DiskVu — A DaisyDisk-like TUI disk analyzer for macOS/Linux.

Usage:
    python3 diskvu.py [path]        # defaults to current directory
    python3 diskvu.py /home
    python3 diskvu.py /

Navigation:
    ↑/↓  or k/j    Move cursor
    Enter or →/l    Enter folder
    Backspace/←/h   Go to parent
    r               Rescan current directory
    d               Delete selected file/folder (with confirmation)
    q               Quit
"""

import os
import sys
import curses
import time
import shutil
import threading
import subprocess
import random
import platform
import argparse
import signal
import locale
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

__version__ = "1.1.0"

# Set to True via --ascii or auto-detected when terminal doesn't support Unicode
ASCII_MODE = False

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_ASCII = r"|/-\\"

# Sentinel for "directory exists but size is unknown due to access restrictions"
UNKNOWN_SIZE = -1

SCAN_QUIPS = [
    "🔍 Hunting for byte monsters...",
    "🕵️  Investigating your digital hoard...",
    "🐘 Checking for elephants in the room...",
    "🧹 Assessing the chaos...",
    "💾 Counting every single bit...",
    "🏋️  Lifting heavy directories...",
    "🤿 Deep-diving into your filesystem...",
    "🦔 Carefully scanning, do not disturb...",
    "🚀 Going fast, hold tight...",
    "📦 Unpacking the truth...",
    "🐢 Your disk is large... or is it just slow? 👀",
    "🌊 Surfing the inode wave...",
]

CREDIT = "Made with love ❤️  from Mehdi Benfredj"

# File extension → emoji icon
EXT_ICONS: dict[str, str] = {
    # Archives
    ".zip": "📦", ".tar": "📦", ".gz": "📦", ".bz2": "📦",
    ".xz": "📦", ".rar": "📦", ".7z": "📦", ".tgz": "📦",
    # Video
    ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬", ".mkv": "🎬",
    ".wmv": "🎬", ".webm": "🎬", ".m4v": "🎬",
    # Audio
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".aac": "🎵",
    ".ogg": "🎵", ".m4a": "🎵",
    # Images
    ".jpg": "🖼️ ", ".jpeg": "🖼️ ", ".png": "🖼️ ", ".gif": "🖼️ ",
    ".svg": "🖼️ ", ".webp": "🖼️ ", ".heic": "🖼️ ", ".bmp": "🖼️ ",
    # Documents
    ".pdf": "📄", ".doc": "📝", ".docx": "📝", ".txt": "📝",
    ".md": "📝", ".pages": "📝", ".odt": "📝",
    # Spreadsheets
    ".xls": "📊", ".xlsx": "📊", ".csv": "📊", ".numbers": "📊",
    # macOS / disk images
    ".dmg": "💿", ".iso": "💿", ".pkg": "🍺", ".app": "🖥️ ",
    # Code
    ".py": "🐍", ".js": "🟨", ".ts": "🟦", ".go": "🐹",
    ".rs": "🦀", ".c": "⚙️ ", ".cpp": "⚙️ ", ".h": "⚙️ ",
    ".java": "☕", ".rb": "💎", ".sh": "🐚", ".bash": "🐚",
    # Databases / data
    ".db": "🗄️ ", ".sqlite": "🗄️ ", ".json": "🗂️ ", ".xml": "🗂️ ",
    ".yaml": "🗂️ ", ".yml": "🗂️ ",
    # Fonts
    ".ttf": "🔤", ".otf": "🔤", ".woff": "🔤",
    # Special macOS junk 😈
    ".DS_Store": "🗑️ ",
}

SIZE_EMOJIS = [
    (1024 ** 4,       "🐋"),  # > 1 TB  — absolute whale
    (100 * 1024 ** 3, "🦕"),  # > 100 GB — dinosaur
    (10  * 1024 ** 3, "🐘"),  # > 10 GB  — elephant
    (1   * 1024 ** 3, "🦁"),  # > 1 GB   — lion
    (100 * 1024 ** 2, "🐻"),  # > 100 MB — bear
    (10  * 1024 ** 2, "🦊"),  # > 10 MB  — fox
    (1   * 1024 ** 2, "🐦"),  # > 1 MB   — bird
    (0,               "🐜"),  # anything else — ant
]

_ASCII_SIZE_LABELS = ["[TB]", "[HG]", "[10G]","[1G]", "[HM]", "[10M]","[1M]", "[sm]"]

def size_emoji(nbytes: int) -> str:
    if nbytes == UNKNOWN_SIZE:
        return "[?]" if ASCII_MODE else "🔒"
    for i, (threshold, emoji) in enumerate(SIZE_EMOJIS):
        if nbytes >= threshold:
            return _ASCII_SIZE_LABELS[i] if ASCII_MODE else emoji
    return "[.]" if ASCII_MODE else "🐜"

def file_icon(name: str, is_dir: bool) -> str:
    if ASCII_MODE:
        return "[/]" if is_dir else "[F]"
    if is_dir:
        return "📁"
    ext = os.path.splitext(name)[1].lower()
    return EXT_ICONS.get(name, EXT_ICONS.get(ext, "📄"))


# ─── Sizing helpers ──────────────────────────────────────────────────────────

def human_size(nbytes: int) -> str:
    """Convert bytes to a human-readable string."""
    if nbytes == UNKNOWN_SIZE:
        return "   ???  "
    if nbytes < 0:
        return "ERR"
    for unit in ("B", "K", "M", "G", "T", "P"):
        if abs(nbytes) < 1024:
            if unit == "B":
                return f"{nbytes:>4d} {unit}"
            return f"{nbytes:>6.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:>6.1f} E"


# ─── Skip list — virtual / special FS paths that hang or loop ─────────────────

# Directory *names* (basename) to skip on both platforms
_SKIP_DIR_NAMES = frozenset({
    # Linux virtual / pseudo filesystems
    "proc", "sys", "dev", "run", "snap",
    # Linux debugfs / tracing (can be infinite)
    "debug", "tracing",
    # macOS automount / synthetic links
    "net", "home",
    # Linux containers / cgroups
    "cgroup", "cgroup2", "cgroupv2",
    # Kernel special dirs
    "configfs", "securityfs", "pstore", "efivarfs",
})

# Absolute paths — platform-specific
_SKIP_ABS_PATHS: frozenset = frozenset(
    [
        "/private/var/vm",       # macOS swap — huge sparse file, du hangs
        "/private/var/folders",  # macOS per-user temp dirs
        "/cores",                # macOS kernel crash cores
    ] if IS_MACOS else [
        "/proc", "/sys", "/dev",         # Linux virtual FS roots
        "/run/user",                     # per-user runtime dirs
        "/sys/kernel/debug",             # debugfs
        "/sys/kernel/tracing",           # tracefs
    ]
)

# ─── Network/remote filesystem detection ─────────────────────────────────────

_NETWORK_FS_TYPES = frozenset({
    "nfs", "nfs4", "nfs3",
    "cifs", "smb", "smbfs",
    "afs", "coda",
    "ncpfs", "ncp",
    "davfs", "sshfs", "ftpfs",
    "s3fs", "s3fuse",           # S3 FUSE mounts
    "efs",                      # AWS EFS (shows as "nfs4" on Linux but just in case)
})

# Built once at startup: mountpoint → fstype
def _build_mount_table() -> dict[str, str]:
    table: dict[str, str] = {}
    try:
        if IS_LINUX:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        table[parts[1]] = parts[2].lower()
        elif IS_MACOS:
            r = subprocess.run(["mount"], capture_output=True, text=True, timeout=2)
            for line in r.stdout.splitlines():
                # Format: device on /mountpoint (fstype, ...)
                m = re.match(r".+ on (.+) \((\w+)", line)
                if m:
                    table[m.group(1)] = m.group(2).lower()
    except Exception:
        pass
    return table

_MOUNT_TABLE: dict[str, str] = _build_mount_table()


def _fs_type_for_path(path: str) -> str:
    """Return filesystem type for *path* using cached mount table (best-effort)."""
    try:
        norm = os.path.normpath(os.path.realpath(path))
    except OSError:
        return ""
    best = ""
    fs_type = ""
    for mount, typ in _MOUNT_TABLE.items():
        if (norm == mount or norm.startswith(mount + "/")) and len(mount) > len(best):
            best = mount
            fs_type = typ
    return fs_type


def _is_network_fs(path: str) -> bool:
    return _fs_type_for_path(path) in _NETWORK_FS_TYPES


# Whether to skip network FS directories (set by --skip-network flag)
SKIP_NETWORK = False

# Human-readable fix hint for inaccessible directories — platform-aware
if IS_MACOS:
    _ACCESS_HINT = (
        "grant Full Disk Access to Terminal in "
        "System Settings › Privacy & Security › Full Disk Access"
    )
else:
    _ACCESS_HINT = (
        "run as root (sudo python3 diskvu.py /) "
        "or fix directory permissions (chmod/ACLs)"
    )


def _should_skip(path: str) -> bool:
    """Return True for paths that would hang du or cause infinite loops."""
    norm = os.path.normpath(path)
    if norm in _SKIP_ABS_PATHS:
        return True
    for prefix in _SKIP_ABS_PATHS:
        if norm.startswith(prefix + "/"):
            return True
    if os.path.basename(norm) in _SKIP_DIR_NAMES:
        return True
    if SKIP_NETWORK and _is_network_fs(path):
        return True
    return False


# ─── Scanning ────────────────────────────────────────────────────────────────

class DirEntry:
    """Represents one file or directory with its computed size."""
    __slots__ = ("name", "path", "is_dir", "size", "item_count", "error")

    def __init__(self, name: str, path: str, is_dir: bool,
                 size: int = 0, item_count: int = 0, error: str = ""):
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.size = size
        self.item_count = item_count
        self.error = error


# ─── Cancellation ────────────────────────────────────────────────────────────
# Set this event to abort all in-flight du subprocesses immediately (q / SIGTERM).
_cancel_scan = threading.Event()

# ─── Cache ───────────────────────────────────────────────────────────────────
# path → (dir_mtime, entries)
_dir_cache: dict[str, tuple[float, list]] = {}

def _cache_get(path: str) -> Optional[list]:
    entry = _dir_cache.get(path)
    if entry is None:
        return None
    cached_mtime, entries = entry
    try:
        if os.stat(path).st_mtime == cached_mtime:
            return entries
    except OSError:
        pass
    del _dir_cache[path]
    return None

def _cache_put(path: str, entries: list) -> None:
    try:
        _dir_cache[path] = (os.stat(path).st_mtime, entries)
    except OSError:
        pass

def _cache_invalidate(path: str) -> None:
    _dir_cache.pop(path, None)


# ─── Per-directory size worker ────────────────────────────────────────────────

def _shallow_count(path: str) -> int:
    """Count immediate children without recursion."""
    try:
        with os.scandir(path) as s:
            return sum(1 for _ in s)
    except OSError:
        return 0

def _compute_dir_size(path: str, depth: int = 0, max_depth: int = 64) -> tuple[int, int]:
    """Recursively compute total size and item count. Fallback when du is absent."""
    if _should_skip(path) or depth > max_depth:
        return 0, 0
    total, count = 0, 0
    try:
        with os.scandir(path) as scanner:
            for item in scanner:
                count += 1
                try:
                    if item.is_symlink():
                        total += item.stat(follow_symlinks=False).st_size
                    elif item.is_dir(follow_symlinks=False):
                        s, c = _compute_dir_size(item.path, depth + 1, max_depth)
                        total += s
                        count += c
                    else:
                        total += item.stat(follow_symlinks=False).st_size
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError):
        pass
    return total, count

def _worker_size_dir(
    item,                          # os.DirEntry
    on_scanning: Optional[Callable],
) -> tuple:
    """Compute the size of one directory.

    Strategy: run `du -s -k <path>` in a subprocess (fast, C-level walk),
    fall back to Python recursive walk if du is not available.
    Each worker runs independently so all top-level dirs are sized in parallel.
    """
    path = item.path
    if _should_skip(path):
        return item, 0, 0, "skipped"

    # Mark network FS directories — du on NFS/EFS can be very slow
    is_net = _is_network_fs(path)

    # Report just the basename so the status line shows "Downloads" not "/Users/mehdi/Downloads".
    # Skip hidden entries (dotfiles) — they're noise in a progress display.
    if on_scanning:
        name = os.path.basename(path)
        if name and not name.startswith("."):
            on_scanning(name)

    # ── du -s fast path ───────────────────────────────────────────────────
    # 3 minutes covers large directories on spinning disks.
    # Network mounts get the same budget — users with slow mounts should use --skip-network.
    du_timeout = 180
    try:
        proc = subprocess.Popen(
            ["du", "-s", "-k", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",
        )
        # Poll in 100ms slices so we can react to cancellation and enforce the
        # timeout without blocking the thread for the full duration.
        deadline = time.monotonic() + du_timeout
        while True:
            if _cancel_scan.is_set():
                proc.kill()
                proc.wait()
                return item, UNKNOWN_SIZE, 0, "cancelled"
            try:
                proc.wait(timeout=0.1)
                break           # process exited normally
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    proc.kill()
                    proc.wait()
                    return item, UNKNOWN_SIZE, 0, "timeout >3min"

        stdout = proc.stdout.read()
        stderr = proc.stderr.read()

        # macOS/BSD du exits with 1 when it hits permission errors inside a dir.
        # Crucially it still outputs "0\t<path>" — a *false* zero.  Detect this
        # by checking stderr for access-denial keywords.
        stderr_lower = stderr.lower()
        access_denied = any(kw in stderr_lower for kw in (
            "permission denied", "operation not permitted", "not permitted",
        ))

        if proc.returncode in (0, 1):
            for line in stdout.splitlines():
                if "\t" not in line:
                    continue
                kb_str, _, _ = line.partition("\t")
                try:
                    size = int(kb_str) * 1024
                    if size == 0 and access_denied:
                        # du couldn't read the directory — returned 0 falsely
                        return item, UNKNOWN_SIZE, 0, "inaccessible"
                    count = _shallow_count(path)
                    return item, size, count, "network" if is_net else ""
                except ValueError:
                    pass
            # du ran but produced no output at all
            if access_denied:
                return item, UNKNOWN_SIZE, 0, "inaccessible"

    except (FileNotFoundError, OSError):
        pass

    # ── Python fallback ───────────────────────────────────────────────────
    # Quick permission check before the expensive recursive walk
    try:
        next(os.scandir(path), None)
    except PermissionError:
        return item, UNKNOWN_SIZE, 0, "inaccessible"
    except OSError:
        pass

    size, count = _compute_dir_size(path)
    return item, size, count, "network" if is_net else ""


def scan_directory(
    path: str,
    on_entry: Optional[Callable] = None,
    on_scanning: Optional[Callable] = None,
    on_dirs_known: Optional[Callable] = None,
) -> list[DirEntry]:
    """Scan *path* and return entries sorted by size descending.

    Calls on_entry(DirEntry) as soon as each item is ready (streaming).
    Calls on_scanning(subpath) when descending into a subdirectory.
    Cache hit → returns instantly, replaying entries through on_entry.
    """
    cached = _cache_get(path)
    if cached is not None:
        if on_entry:
            for e in cached:
                on_entry(e)
        return cached

    with _scanning_now_lock:
        _scanning_now.add(path)

    entries: list[DirEntry] = []
    dir_items: list = []

    # ── List the directory ────────────────────────────────────────────────
    try:
        raw_items = list(os.scandir(path))
    except (PermissionError, OSError):
        with _scanning_now_lock:
            _scanning_now.discard(path)
        return []

    # ── Emit files / symlinks immediately (stat is free from scandir) ─────
    for item in raw_items:
        try:
            if item.is_symlink():
                st = item.stat(follow_symlinks=False)
                e = DirEntry(name=item.name, path=item.path, is_dir=False,
                             size=st.st_size, error="symlink")
                entries.append(e)
                if on_entry:
                    on_entry(e)
            elif item.is_dir(follow_symlinks=False):
                if _should_skip(item.path):
                    e = DirEntry(name=item.name, path=item.path, is_dir=True,
                                 size=0, error="skipped")
                    entries.append(e)
                    if on_entry:
                        on_entry(e)
                else:
                    dir_items.append(item)
            else:
                st = item.stat(follow_symlinks=False)
                e = DirEntry(name=item.name, path=item.path, is_dir=False,
                             size=st.st_size)
                entries.append(e)
                if on_entry:
                    on_entry(e)
        except PermissionError:
            e = DirEntry(name=item.name, path=item.path,
                         is_dir=item.is_dir(follow_symlinks=False),
                         error="permission denied")
            entries.append(e)
            if on_entry:
                on_entry(e)
        except OSError as exc:
            e = DirEntry(name=item.name, path=item.path, is_dir=False, error=str(exc))
            entries.append(e)
            if on_entry:
                on_entry(e)

    # ── Size directories in parallel, stream results via as_completed ─────
    if on_dirs_known:
        on_dirs_known(len(dir_items))
    if dir_items:
        workers = min(16, len(dir_items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_worker_size_dir, item, on_scanning): item
                for item in dir_items
            }
            for fut in as_completed(futures):
                try:
                    item, size, count, error = fut.result()
                except Exception:
                    item = futures[fut]
                    size, count, error = 0, 0, "error"
                e = DirEntry(name=item.name, path=item.path, is_dir=True,
                             size=size, item_count=count, error=error)
                entries.append(e)
                if on_entry:
                    on_entry(e)

    entries.sort(key=lambda e: e.size, reverse=True)
    _cache_put(path, entries)
    with _scanning_now_lock:
        _scanning_now.discard(path)
    return entries


# Paths currently being scanned in the background.
# Used to avoid launching a duplicate worker when the user navigates away and back.
_scanning_now: set[str] = set()
_scanning_now_lock = threading.Lock()


# ─── RAM check for prefetch ───────────────────────────────────────────────────

_PREFETCH_MIN_FREE_MB = 300  # only prefetch when this much RAM is free

def _free_ram_mb() -> int:
    """Return approximate free+reclaimable RAM in MB without third-party deps.
    Returns 0 when it cannot be determined (safe: disables prefetch)."""
    try:
        if IS_LINUX:
            # MemAvailable accounts for reclaimable pages — most accurate metric
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024   # kB → MB

        elif IS_MACOS:
            r = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=2
            )
            # Header: "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
            page_size = 4096  # Apple Silicon is 16 KB, Intel is 4 KB — parse it
            for line in r.stdout.splitlines():
                if "page size of" in line:
                    try:
                        page_size = int(line.split("page size of")[1].split()[0])
                    except (IndexError, ValueError):
                        pass
                    break

            free_pages = 0
            for line in r.stdout.splitlines():
                # "Pages free:" and "Pages inactive:" are safely reclaimable
                for key in ("Pages free:", "Pages inactive:"):
                    if line.strip().startswith(key):
                        try:
                            free_pages += int(
                                line.split(":")[1].strip().rstrip(".")
                            )
                        except ValueError:
                            pass
            return (free_pages * page_size) // (1024 * 1024)

    except Exception:
        pass
    return 0   # unknown → conservative, don't prefetch


# ─── Color scheme ────────────────────────────────────────────────────────────

C_HEADER = 1
C_SELECTED = 2
C_DIR = 3
C_FILE = 4
C_BAR = 5
C_ERROR = 6
C_FOOTER = 7
C_SIZE_BIG = 8
C_SIZE_MED = 9
C_SIZE_SML = 10
C_PERCENT = 11
C_BAR_BG = 12
C_BORDER = 13
C_CREDIT = 14
C_STATUS = 15

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_HEADER,   curses.COLOR_BLACK,   curses.COLOR_CYAN)
    curses.init_pair(C_SELECTED, curses.COLOR_BLACK,   curses.COLOR_WHITE)
    curses.init_pair(C_DIR,      curses.COLOR_CYAN,    -1)
    curses.init_pair(C_FILE,     curses.COLOR_WHITE,   -1)
    curses.init_pair(C_BAR,      curses.COLOR_GREEN,   -1)
    curses.init_pair(C_ERROR,    curses.COLOR_RED,     -1)
    curses.init_pair(C_FOOTER,   curses.COLOR_BLACK,   curses.COLOR_CYAN)
    curses.init_pair(C_SIZE_BIG, curses.COLOR_RED,     -1)
    curses.init_pair(C_SIZE_MED, curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_SIZE_SML, curses.COLOR_GREEN,   -1)
    curses.init_pair(C_PERCENT,  curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_BAR_BG,   curses.COLOR_BLACK,   -1)
    curses.init_pair(C_BORDER,   curses.COLOR_BLUE,    -1)
    curses.init_pair(C_CREDIT,   curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_STATUS,   curses.COLOR_YELLOW,  -1)


# ─── TUI ─────────────────────────────────────────────────────────────────────

class DiskVuApp:
    def __init__(self, stdscr, start_path: str):
        self.stdscr = stdscr
        self.current_path = os.path.abspath(start_path)
        self.entries: list[DirEntry] = []
        self.cursor = 0
        self.scroll_offset = 0
        self.total_size = 0
        self.scan_time = 0.0
        self.history: list[tuple[str, int, int]] = []
        self.message = ""
        self.message_time = 0.0

        # Async scan state
        self._scan_lock = threading.Lock()
        self._pending_result: Optional[tuple[list[DirEntry], float]] = None
        self._pending_nav: Optional[tuple[int, int]] = None
        self._current_scan_id = 0
        self.scanning = False
        self._spinner_frame = 0
        self._after_scan_message = ""
        self._quip = random.choice(SCAN_QUIPS)

        # Live progress state (written by worker thread, read by main thread)
        self._scan_status = ""      # name of directory currently being sized
        self._scan_count = 0        # entries completed so far
        self._scan_dirs_done = 0    # directories fully sized
        self._scan_dirs_total = 0   # total directories to size (set after first scandir)
        self._partial_entries: list[DirEntry] = []
        self._partial_lock = threading.Lock()

        # Inaccessible directory tracking (for honest totals)
        self._inaccessible_count = 0

        # Prefetch state
        self._prefetch_thread: Optional[threading.Thread] = None

        # Partial results saved per path so navigating back restores progress
        self._partial_by_path: dict[str, list[DirEntry]] = {}

        curses.curs_set(0)
        init_colors()
        self.stdscr.timeout(100)    # 100 ms tick — drives spinner + partial refresh

        self._start_scan(clear_immediately=True)

    # ── Async scan ────────────────────────────────────────────────────────────

    def _start_scan(self, clear_immediately: bool = True) -> None:
        path = self.current_path

        # ── Restore partial results saved when we last left this path ─────────
        saved = self._partial_by_path.pop(path, [])
        if clear_immediately:
            self.cursor = 0
            self.scroll_offset = 0
        if saved:
            # Show what we had before immediately — user sees data, not a blank screen
            self.entries = sorted(saved, key=lambda e: e.size, reverse=True)
            self.total_size = sum(e.size for e in self.entries if e.size != UNKNOWN_SIZE)
        elif clear_immediately:
            self.entries = []
            self.total_size = 0

        # Pre-populate the accumulation buffer ONLY when re-attaching to a still-
        # running worker (see below).  For a fresh scan we must start the buffer
        # empty — otherwise the saved items would be duplicated because the new
        # worker's on_entry (or a cache-replay) will emit every entry again.
        # self.entries already holds the saved snapshot for visual continuity;
        # _poll_scan only overwrites it once the first real entry arrives.
        self._scan_count = 0

        self.scanning = True
        self._spinner_frame = 0
        self._quip = random.choice(SCAN_QUIPS)

        # ── Re-attach to an existing background scan for this path ────────────
        # If a worker is already running for this path (user navigated away and
        # back before it finished), don't spawn a duplicate — just let the
        # original worker resume updating the display via current_path checks.
        with _scanning_now_lock:
            already_running = path in _scanning_now
        if already_running:
            # Restore saved items into the buffer so the live-appending worker
            # builds on top of them rather than starting from an empty list.
            with self._partial_lock:
                self._partial_entries = list(saved)
            self._scan_count = len(saved)
            # Reset visible counters so the progress row looks live again
            self._scan_status = ""
            self._scan_dirs_done = 0
            self._scan_dirs_total = 0
            return  # original worker will apply its result when done

        # ── Fresh scan — accumulation buffer starts empty ─────────────────────
        with self._partial_lock:
            self._partial_entries = []
        self._scan_status = ""
        self._scan_dirs_done = 0
        self._scan_dirs_total = 0
        self._current_scan_id += 1
        scan_path = path   # captured in closures below

        # All callbacks gate on current_path == scan_path so they become
        # no-ops if the user navigates away, but resume automatically if they
        # come back to this path.

        def on_entry(e: DirEntry) -> None:
            if self.current_path != scan_path:
                return
            with self._partial_lock:
                self._partial_entries.append(e)
            self._scan_count += 1

        def on_scanning(name: str) -> None:
            if self.current_path != scan_path:
                return
            self._scan_status = name
            self._scan_dirs_done += 1

        def on_dirs_known(total: int) -> None:
            if self.current_path != scan_path:
                return
            self._scan_dirs_total = total

        def _worker() -> None:
            t0 = time.monotonic()
            result = scan_directory(
                scan_path,
                on_entry=on_entry,
                on_scanning=on_scanning,
                on_dirs_known=on_dirs_known,
            )
            elapsed = time.monotonic() - t0
            with self._scan_lock:
                # Apply result if the user is still on (or came back to) this path
                if self.current_path == scan_path:
                    self._pending_result = (result, elapsed)

        threading.Thread(target=_worker, daemon=True).start()

    def _poll_scan(self) -> None:
        """Called every tick. Updates partial results and finalises when done."""
        # Push live partial results into self.entries so the list grows in real time
        if self.scanning:
            with self._partial_lock:
                partial = list(self._partial_entries)
            if partial:
                self.entries = sorted(partial, key=lambda e: e.size, reverse=True)
                self.total_size = sum(e.size for e in self.entries if e.size != UNKNOWN_SIZE)

        # Check whether the scan has finished
        with self._scan_lock:
            if self._pending_result is None:
                return
            entries, elapsed = self._pending_result
            self._pending_result = None

        self.entries = entries
        self.scan_time = elapsed
        # Exclude UNKNOWN_SIZE sentinels from the total so the number is honest
        self.total_size = sum(e.size for e in entries if e.size != UNKNOWN_SIZE)
        self._inaccessible_count = sum(1 for e in entries if e.size == UNKNOWN_SIZE)
        self.scanning = False

        if self._pending_nav is not None:
            cursor, scroll = self._pending_nav
            self._pending_nav = None
            lh = self.list_height
            self.cursor = min(cursor, max(0, len(entries) - 1))
            self.scroll_offset = min(scroll, max(0, len(entries) - lh))
        else:
            self.cursor = 0
            self.scroll_offset = 0

        if self._after_scan_message:
            self._set_message(self._after_scan_message)
            self._after_scan_message = ""
        elif self._inaccessible_count:
            self._set_message(
                f"🔒 {self._inaccessible_count} dir(s) blocked — {_ACCESS_HINT}"
            )

        self._maybe_prefetch()

    def _maybe_prefetch(self) -> None:
        """Silently pre-scan the largest uncached subdirectory if RAM allows.

        Runs in a daemon thread with no UI callbacks — result goes straight
        into _dir_cache so the user gets instant navigation when they open it.
        """
        if self.scanning:
            return
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            return  # a previous prefetch is still running

        # Pick the largest accessible, not-yet-cached subdirectory
        candidates = [
            e for e in self.entries
            if (e.is_dir
                and e.size > 0
                and e.size != UNKNOWN_SIZE
                and not e.error
                and _cache_get(e.path) is None)
        ]
        if not candidates:
            return

        target = max(candidates, key=lambda e: e.size)

        # Gate on available RAM — vm_stat / /proc/meminfo, no external libs
        free_mb = _free_ram_mb()
        if free_mb != 0 and free_mb < _PREFETCH_MIN_FREE_MB:
            return  # system under memory pressure, back off

        def _prefetch() -> None:
            try:
                scan_directory(target.path)   # populates _dir_cache silently
            except Exception:
                pass

        self._prefetch_thread = threading.Thread(
            target=_prefetch, daemon=True, name="diskvu-prefetch"
        )
        self._prefetch_thread.start()

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _save_partials(self) -> None:
        """Persist the current partial results so _start_scan can restore them
        if the user comes back to this path before the scan finishes."""
        with self._partial_lock:
            snapshot = list(self._partial_entries)
        if snapshot:
            self._partial_by_path[self.current_path] = snapshot

    def _set_message(self, msg: str) -> None:
        self.message = msg
        self.message_time = time.monotonic()

    @property
    def list_height(self) -> int:
        h, _ = self.stdscr.getmaxyx()
        return max(1, h - 4)

    def _scan_time_label(self) -> str:
        if self.scan_time < 0.5:
            return f"⚡ {self.scan_time:.2f}s"
        elif self.scan_time < 2.0:
            return f"🏃 {self.scan_time:.2f}s"
        elif self.scan_time < 5.0:
            return f"🐢 {self.scan_time:.2f}s"
        else:
            return f"🦥 {self.scan_time:.2f}s"

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 6 or w < 40:
            self.stdscr.addnstr(0, 0, "Terminal too small 😬", w)
            self.stdscr.refresh()
            return

        list_h = self.list_height

        # ── Row 0: title / spinner ────────────────────────────────────────
        header_attr = curses.color_pair(C_HEADER) | curses.A_BOLD
        self.stdscr.attron(header_attr)
        self.stdscr.addnstr(0, 0, " " * w, w)
        if self.scanning:
            sp_chars = _SPINNER_ASCII if ASCII_MODE else SPINNER
            spin = sp_chars[self._spinner_frame % len(sp_chars)]
            self._spinner_frame += 1
            if ASCII_MODE:
                quip = self._quip.encode("ascii", "replace").decode("ascii")
                title = f" {spin} {quip}"
            else:
                title = f" {spin} {self._quip}"
        else:
            title = f" DiskVu -- {self.current_path}" if ASCII_MODE else f" 🗂️  DiskVu — {self.current_path}"
        self.stdscr.addnstr(0, 0, title[:w], w)
        if not self.scanning:
            known = human_size(self.total_size).strip()
            prefix = "≥ " if self._inaccessible_count else ""
            size_str = f" {prefix}{known} total "
            if len(title) + len(size_str) + 2 < w:
                self.stdscr.addnstr(0, w - len(size_str), size_str, len(size_str))
        self.stdscr.attroff(header_attr)

        # ── Row 1: live progress while scanning / column headers otherwise ─
        self.stdscr.attron(curses.color_pair(C_BORDER))
        if self.scanning:
            done  = self._scan_dirs_done
            total = self._scan_dirs_total
            name  = self._scan_status

            # Progress fraction — shows "?" until first scandir completes
            if total:
                frac = f"{done}/{total} dirs"
                # Mini ASCII progress bar: ████░░░░  (10 chars)
                bar_w = 10
                filled = int(done / total * bar_w)
                mini_bar = "█" * filled + "░" * (bar_w - filled)
                progress = f" [{mini_bar}] {frac}"
            else:
                progress = ""

            # Last completed directory name (basename only, hidden already filtered)
            label = f"  ← {name}" if name else ""
            row1 = f" 📂 Sizing directories…{progress}{label}"
            self.stdscr.addnstr(1, 0, row1[:w].ljust(w), w,
                                curses.color_pair(C_STATUS) | curses.A_BOLD)
        else:
            col_header = "   {:>8s}  {:>5s}  {:<20s}  {}".format(
                "SIZE", "%", "USAGE", "NAME"
            )
            self.stdscr.addnstr(1, 0, col_header[:w].ljust(w), w)
        self.stdscr.attroff(curses.color_pair(C_BORDER))

        # ── Rows 2…h-3: entry list ────────────────────────────────────────
        if not self.entries:
            if not self.scanning:
                self.stdscr.addnstr(3, 0, "  ✨ Nothing here! Squeaky clean 🧹", w,
                                    curses.color_pair(C_DIR) | curses.A_BOLD)
        else:
            visible = self.entries[self.scroll_offset:self.scroll_offset + list_h]
            for i, entry in enumerate(visible):
                y = i + 2
                if y >= h - 2:
                    break
                self._draw_entry(y, w, entry, (i + self.scroll_offset) == self.cursor)

        # ── Scrollbar ────────────────────────────────────────────────────
        if len(self.entries) > list_h:
            sb_top, sb_h = 2, list_h
            total = len(self.entries)
            thumb_h = max(1, sb_h * list_h // total)
            thumb_pos = (sb_top
                         + (self.scroll_offset * (sb_h - thumb_h))
                         // max(1, total - list_h))
            for y in range(sb_top, sb_top + sb_h):
                if y >= h - 2:
                    break
                ch = "█" if thumb_pos <= y < thumb_pos + thumb_h else "│"
                try:
                    self.stdscr.addstr(y, w - 1, ch, curses.color_pair(C_BORDER))
                except curses.error:
                    pass

        # ── Footer ───────────────────────────────────────────────────────
        footer_y = h - 2
        self.stdscr.attron(curses.color_pair(C_FOOTER) | curses.A_BOLD)
        self.stdscr.addnstr(footer_y, 0, " " * w, w)
        pos_str = f"  [{self.cursor + 1}/{len(self.entries)}]" if self.entries else ""
        locked_str = f"  🔒 {self._inaccessible_count} inaccessible" if self._inaccessible_count else ""
        footer_left = f" {len(self.entries)} items  {self._scan_time_label()}{pos_str}{locked_str}"
        self.stdscr.addnstr(footer_y, 0, footer_left[:w], w)
        self.stdscr.attroff(curses.color_pair(C_FOOTER) | curses.A_BOLD)

        # ── Help / credit bar ─────────────────────────────────────────────
        help_y = h - 1
        safe_w = max(0, w - 1)
        self.stdscr.addnstr(help_y, 0, " " * safe_w, safe_w,
                            curses.color_pair(C_BORDER))
        help_text = " ↑↓/jk:move  ↵/→/l:open  ←/h/BS:back  r:rescan  d:delete  ~:home  o:open  q:quit"
        self.stdscr.addnstr(help_y, 0, help_text[:safe_w], safe_w,
                            curses.color_pair(C_BORDER))
        credit = f" {CREDIT} "
        credit_x = safe_w - len(credit)
        if credit_x > len(help_text) + 2:
            self.stdscr.addnstr(help_y, credit_x, credit, len(credit),
                                curses.color_pair(C_CREDIT) | curses.A_BOLD)

        # ── Overlay toast message ─────────────────────────────────────────
        if self.message and (time.monotonic() - self.message_time < 2.5):
            msg = f"  {self.message}  "
            mx = max(0, (w - len(msg)) // 2)
            self.stdscr.addnstr(h // 2, mx, msg[:w], w,
                                curses.color_pair(C_ERROR) | curses.A_BOLD)

        self.stdscr.refresh()

    def _draw_entry(self, y: int, w: int, entry: DirEntry, selected: bool) -> None:
        unknown = entry.size == UNKNOWN_SIZE
        size_str = human_size(entry.size)
        semoji = size_emoji(entry.size)

        pct = (entry.size / self.total_size * 100
               if (self.total_size > 0 and not unknown) else 0.0)
        pct_str = "  ???%" if unknown else f"{pct:5.1f}%"

        # Smooth bar with sub-character precision
        bar_max = 20
        if unknown:
            bar_str = "?" * bar_max
        elif ASCII_MODE:
            filled = int(pct / 100 * bar_max) if self.total_size > 0 else 0
            bar_str = "#" * filled + "-" * (bar_max - filled)
        else:
            filled = int(pct / 100 * bar_max) if self.total_size > 0 else 0
            partial_chars = " ▏▎▍▌▋▊▉"
            sub = int((pct / 100 * bar_max - filled) * 8) if self.total_size > 0 else 0
            bar_str = "█" * filled
            if filled < bar_max:
                bar_str += partial_chars[sub]
                bar_str += "░" * (bar_max - filled - 1)

        icon = file_icon(entry.name, entry.is_dir)
        if entry.is_dir:
            name = f"{icon} {entry.name}/"
            if entry.item_count > 0:
                name += f"  ({entry.item_count} items)"
        else:
            name = f"{icon} {entry.name}"
        # Annotate entries with notable flags
        if entry.error == "network":
            name += " 🌐" if not ASCII_MODE else " [NET]"
        elif entry.error and entry.error not in ("symlink",):
            name += f" ⚠️  [{entry.error}]" if not ASCII_MODE else f" [!] [{entry.error}]"
        elif entry.error == "symlink":
            name += " 🔗" if not ASCII_MODE else " [->]"

        size_color = (C_ERROR if unknown
                      else C_SIZE_BIG if entry.size > 1024 ** 3
                      else C_SIZE_MED if entry.size > 100 * 1024 ** 2
                      else C_SIZE_SML)
        name_color = C_DIR if entry.is_dir else C_FILE

        if selected:
            attr = curses.color_pair(C_SELECTED) | curses.A_BOLD
            try:
                self.stdscr.addnstr(y, 0, " " * (w - 1), w - 1, attr)
            except curses.error:
                pass
            col = 1
            self.stdscr.addnstr(y, col, semoji,   min(2, w - col), attr); col += 3
            self.stdscr.addnstr(y, col, size_str, min(len(size_str), w - col), attr); col += 9
            self.stdscr.addnstr(y, col, pct_str,  min(len(pct_str), w - col), attr); col += 7
            self.stdscr.addnstr(y, col, bar_str,  min(len(bar_str), w - col), attr); col += bar_max + 2
            remaining = w - col - 2
            if remaining > 0:
                self.stdscr.addnstr(y, col, name, remaining, attr)
        else:
            col = 1
            try:
                self.stdscr.addnstr(y, col, semoji, min(2, w - col),
                                    curses.color_pair(size_color))
            except curses.error:
                return
            col += 3
            try:
                self.stdscr.addnstr(y, col, size_str, min(len(size_str), w - col),
                                    curses.color_pair(size_color) | curses.A_BOLD)
            except curses.error:
                return
            col += 9
            try:
                self.stdscr.addnstr(y, col, pct_str, min(len(pct_str), w - col),
                                    curses.color_pair(C_PERCENT))
            except curses.error:
                return
            col += 7
            try:
                self.stdscr.addnstr(y, col, bar_str, min(len(bar_str), w - col),
                                    curses.color_pair(C_BAR))
            except curses.error:
                return
            col += bar_max + 2
            remaining = w - col - 2
            if remaining > 0:
                err_attr = (curses.color_pair(C_ERROR)
                            if entry.error and entry.error not in ("symlink",)
                            else curses.color_pair(name_color))
                try:
                    self.stdscr.addnstr(y, col, name, remaining, err_attr)
                except curses.error:
                    pass

    # ── Navigation ────────────────────────────────────────────────────────────

    def _move_cursor(self, delta: int) -> None:
        if not self.entries:
            return
        self.cursor = max(0, min(len(self.entries) - 1, self.cursor + delta))
        if self.cursor < self.scroll_offset:
            self.scroll_offset = self.cursor
        elif self.cursor >= self.scroll_offset + self.list_height:
            self.scroll_offset = self.cursor - self.list_height + 1

    def _enter_dir(self) -> None:
        if not self.entries:
            return
        entry = self.entries[self.cursor]
        if not entry.is_dir:
            self._set_message("🚫 That's a file, not a folder!")
            return
        if entry.error in ("permission denied", "skipped"):
            self._set_message("🔒 Access denied — this folder doesn't want visitors")
            return
        self._save_partials()
        self.history.append((self.current_path, self.cursor, self.scroll_offset))
        self.current_path = entry.path
        self._pending_nav = None
        self._start_scan(clear_immediately=True)

    def _go_back(self) -> None:
        parent = os.path.dirname(self.current_path)
        if parent == self.current_path:
            self._set_message("🌍 Already at root — nowhere left to go!")
            return
        self._save_partials()
        if self.history:
            prev_path, prev_cursor, prev_scroll = self.history.pop()
            if prev_path == parent:
                self.current_path = parent
                self._pending_nav = (prev_cursor, prev_scroll)
                self._start_scan(clear_immediately=True)
                return
        self.history.clear()
        self.current_path = parent
        self._pending_nav = None
        self._start_scan(clear_immediately=True)

    def _delete_selected(self) -> None:
        if not self.entries:
            return
        entry = self.entries[self.cursor]
        h, w = self.stdscr.getmaxyx()
        size_hint = human_size(entry.size).strip()
        prompt = f" 💀 Nuke '{entry.name}' ({size_hint})? This is permanent! (y/N) "
        self.stdscr.addnstr(h - 2, 0, prompt[:w].ljust(w), w,
                            curses.color_pair(C_ERROR) | curses.A_BOLD)
        self.stdscr.refresh()
        self.stdscr.timeout(-1)
        ch = self.stdscr.getch()
        self.stdscr.timeout(100)
        if ch in (ord('y'), ord('Y')):
            try:
                if entry.is_dir:
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
                _cache_invalidate(self.current_path)
                self._after_scan_message = f"💥 Obliterated: {entry.name}"
                self._start_scan(clear_immediately=False)
            except Exception as e:
                self._set_message(f"😬 Error: {e}")
        else:
            self._set_message("😅 Phew! Cancelled, nothing was harmed")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        while True:
            self._poll_scan()
            self._draw()
            key = self.stdscr.getch()

            if key == -1:
                continue
            elif key == curses.KEY_RESIZE:
                continue
            elif key in (ord('q'), ord('Q'), 27):
                break
            elif key in (curses.KEY_UP, ord('k')):
                self._move_cursor(-1)
            elif key in (curses.KEY_DOWN, ord('j')):
                self._move_cursor(1)
            elif key in (curses.KEY_NPAGE,):
                self._move_cursor(self.list_height)
            elif key in (curses.KEY_PPAGE,):
                self._move_cursor(-self.list_height)
            elif key in (curses.KEY_HOME, ord('g')):
                self._move_cursor(-len(self.entries))
            elif key in (curses.KEY_END, ord('G')):
                self._move_cursor(len(self.entries))
            elif key in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord('l')):
                self._enter_dir()
            elif key in (curses.KEY_BACKSPACE, 127, curses.KEY_LEFT, ord('h')):
                self._go_back()
            elif key in (ord('r'), ord('R')):
                _cache_invalidate(self.current_path)
                self._after_scan_message = "Rescan complete!" if ASCII_MODE else "🔄 Fresh scan complete!"
                self._start_scan(clear_immediately=False)
            elif key in (ord('d'), ord('D')):
                self._delete_selected()
            elif key == ord('~'):
                home = os.path.expanduser("~")
                if os.path.isdir(home) and home != self.current_path:
                    self._save_partials()
                    self.history.clear()
                    self.current_path = home
                    self._pending_nav = None
                    self._start_scan(clear_immediately=True)
            elif key in (ord('o'), ord('O')):
                self._open_in_manager()

    def _open_in_manager(self) -> None:
        """Open the selected entry (or current dir) in the platform file manager."""
        if self.entries:
            target = self.entries[self.cursor].path
        else:
            target = self.current_path
        try:
            if IS_MACOS:
                subprocess.Popen(["open", "-R", target],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif IS_LINUX:
                # Try common file managers; fall back to xdg-open
                for cmd in (["nautilus", "--select", target],
                            ["dolphin", "--select", target],
                            ["thunar", target],
                            ["xdg-open", os.path.dirname(target)]):
                    try:
                        subprocess.Popen(cmd,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except FileNotFoundError:
                        continue
            msg = "Opened in file manager" if ASCII_MODE else "📂 Opened in file manager"
            self._set_message(msg)
        except Exception as exc:
            self._set_message(f"Could not open: {exc}")


def main(stdscr, path: str) -> None:
    app = DiskVuApp(stdscr, path)
    try:
        app.run()
    finally:
        # Signal all du subprocesses to terminate immediately so that Python's
        # atexit/ThreadPoolExecutor shutdown doesn't block waiting for them.
        _cancel_scan.set()


def _detect_unicode_support() -> bool:
    """Return True when the terminal likely renders Unicode/emoji correctly."""
    # Explicit env override
    if os.environ.get("DISKVU_ASCII", "").lower() in ("1", "true", "yes"):
        return False
    # Check locale encoding
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        val = os.environ.get(var, "")
        if "utf" in val.lower():
            return True
    try:
        if "utf" in locale.getpreferredencoding(False).lower():
            return True
    except Exception:
        pass
    return False


def _cli() -> None:
    global ASCII_MODE, SKIP_NETWORK

    parser = argparse.ArgumentParser(
        prog="diskvu",
        description="DiskVu — interactive TUI disk analyzer (macOS / Linux / EC2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Navigation:
  ↑/↓  or k/j     move cursor
  Enter / → / l   enter directory
  ← / h / BS      go to parent
  ~                jump to home directory
  r                rescan current directory
  d                delete selected (with confirmation)
  o                reveal in file manager (desktop only)
  q / Esc          quit

Examples:
  diskvu                    # scan current directory
  diskvu /                  # scan root
  diskvu --ascii ~          # ASCII mode for SSH/EC2 sessions
  diskvu --skip-network /   # skip NFS/EFS mounts
""",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        metavar="PATH",
        help="directory to scan (default: current directory)",
    )
    parser.add_argument(
        "-a", "--ascii",
        action="store_true",
        help="force ASCII output (auto-enabled when locale is not UTF-8)",
    )
    parser.add_argument(
        "--no-ascii",
        action="store_true",
        help="force emoji/Unicode output even on non-UTF-8 terminals",
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="skip NFS, CIFS, and other network/remote filesystems",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"diskvu {__version__}",
    )

    args = parser.parse_args()

    # Resolve ASCII mode: explicit flag beats auto-detection
    if args.ascii:
        ASCII_MODE = True
    elif args.no_ascii:
        ASCII_MODE = False
    else:
        ASCII_MODE = not _detect_unicode_support()

    if args.skip_network:
        SKIP_NETWORK = True

    target = os.path.abspath(args.path)
    if not os.path.isdir(target):
        print(f"Error: '{target}' is not a directory", file=sys.stderr)
        sys.exit(1)

    # Handle SIGTERM cleanly — curses needs to restore the terminal
    def _sigterm_handler(sig, frame):  # noqa: ANN001
        curses.endwin()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        curses.wrapper(main, target)
    except KeyboardInterrupt:
        # Ctrl+C outside the curses event loop — ensure workers stop immediately
        # so that ThreadPoolExecutor's atexit handler doesn't hang on t.join().
        _cancel_scan.set()


if __name__ == "__main__":
    _cli()
