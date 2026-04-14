# DiskVu

A fast, interactive TUI disk analyzer for the terminal — inspired by DaisyDisk, built for the command line. Works on macOS, Linux, and headless EC2/SSH sessions.

```
 ⠹ 🕵️  Investigating your digital hoard...
 📂 Sizing directories… [████████░░] 8/10 dirs  ← node_modules
───────────────────────────────────────────────────────────────────────────────
   🐘  10.2 G  72.3%  ████████████████████░░░░  📁 node_modules/  (4821 items)
   🦊   1.4 G  10.1%  ██░░░░░░░░░░░░░░░░░░░░░░  📁 .venv/         (1203 items)
   🐦 512.0 M   3.6%  ▊░░░░░░░░░░░░░░░░░░░░░░░  🎬 demo.mp4
   🐦 210.4 M   1.5%  ▎░░░░░░░░░░░░░░░░░░░░░░░  📦 archive.tar.gz
   🐜   4.2 K   0.0%  ░░░░░░░░░░░░░░░░░░░░░░░░  🐍 diskvu.py
───────────────────────────────────────────────────────────────────────────────
 5 items  ⚡ 0.41s  [1/5]          ↑↓/jk:move  ↵:open  ←/h:back  d:delete  q:quit
```

---

## Features

- **Real-time streaming** — entries appear as they are found, no waiting for a full scan
- **Parallel scanning** — top-level directories are sized concurrently with `du` (falls back to pure Python when `du` is unavailable)
- **Smart caching** — revisiting a directory is instant; cache is invalidated when the directory's mtime changes
- **Background prefetch** — the largest uncached subdirectory is silently pre-scanned while you browse
- **Size bars** — smooth Unicode block-fill bars (or ASCII `###---` in fallback mode) that show each item's share of the total
- **Emoji icons** — file type indicators, size animal emojis, and access-status badges (`🔒`, `🌐`, `🔗`)
- **ASCII mode** — full fallback for SSH/EC2 sessions where the terminal doesn't support Unicode
- **Network FS detection** — identifies NFS, EFS, CIFS, and SSHFS mounts; optionally skips them with `--skip-network`
- **Safe delete** — remove files and folders with a confirmation prompt; cache is updated immediately
- **Cross-platform** — macOS (personal), Linux (work), EC2 (servers)

---

## Requirements

- Python **3.8 or later** (uses `__slots__`, walrus operator patterns, and `f-strings`)
- No third-party packages — only the Python standard library
- `du` (coreutils) — used for fast directory sizing; the tool falls back to a pure-Python recursive walk if `du` is absent (e.g., minimal containers)

---

## Installation

### One-liner (recommended)

```bash
# System-wide (requires sudo on Linux)
sudo ./install.sh

# User-only (no sudo required)
./install.sh ~/.local/bin
```

The script copies `diskvu.py` to the target directory, names it `diskvu`, and makes it executable. It also warns you if the directory is not in your `PATH`.

### Makefile

```bash
# System-wide install (/usr/local/bin/diskvu)
sudo make install

# User-local install (~/.local/bin/diskvu)
make install-user

# Uninstall
sudo make uninstall
# or
make uninstall-user
```

### Manual

```bash
cp diskvu.py /usr/local/bin/diskvu
chmod +x /usr/local/bin/diskvu
```

### EC2 / remote server

```bash
# From your local machine:
scp diskvu.py ec2-user@your-instance:~/.local/bin/diskvu
ssh ec2-user@your-instance "chmod +x ~/.local/bin/diskvu"

# On the instance (ASCII mode is auto-enabled when locale is not UTF-8):
diskvu --ascii /
diskvu --ascii --skip-network /
```

---

## Usage

```
diskvu [OPTIONS] [PATH]
```

`PATH` defaults to the current directory if omitted.

### Options

| Flag | Description |
|------|-------------|
| `-a`, `--ascii` | Force ASCII output — no emoji, no Unicode bars. Auto-enabled when the terminal locale is not UTF-8 (typical on minimal EC2 AMIs). |
| `--no-ascii` | Force emoji/Unicode output even if locale detection would suggest ASCII mode. |
| `--skip-network` | Skip NFS, EFS, CIFS, SSHFS, and other remote/network filesystems during scanning. Useful when scanning `/` on a machine with slow EFS mounts. |
| `-V`, `--version` | Print version and exit. |
| `-h`, `--help` | Show help and exit. |

### Environment variable

| Variable | Effect |
|----------|--------|
| `DISKVU_ASCII=1` | Same as `--ascii`. Useful in shell profiles or wrapper scripts. |

### Examples

```bash
diskvu                          # scan current directory
diskvu ~                        # scan home directory
diskvu /                        # scan root (run with sudo for full access)
sudo diskvu /                   # see everything, including root-owned dirs
diskvu --ascii ~                # ASCII mode for SSH sessions
diskvu --ascii --skip-network / # scan root, skip NFS/EFS mounts
diskvu /var/log                 # investigate a specific path
DISKVU_ASCII=1 diskvu /         # ASCII via env var
```

---

## Navigation

| Key | Action |
|-----|--------|
| `↑` / `↓` or `k` / `j` | Move cursor up / down |
| `Enter` / `→` / `l` | Open selected directory |
| `←` / `h` / `Backspace` | Go to parent directory |
| `~` | Jump to your home directory from anywhere |
| `Page Up` / `Page Down` | Scroll one page |
| `g` / `Home` | Jump to top of list |
| `G` / `End` | Jump to bottom of list |
| `r` | Force rescan of current directory (clears cache) |
| `d` | Delete selected file or folder (asks for confirmation) |
| `o` | Reveal selected item in file manager (Finder on macOS, Nautilus/Dolphin/xdg-open on Linux) |
| `q` / `Esc` | Quit |

---

## Understanding the Display

### Column layout

```
  [SIZE-EMOJI]  [SIZE]  [PERCENT]  [USAGE BAR]  [ICON] [NAME]
```

Example row:

```
  🐘   10.2 G   72.3%  ████████████████████░░░░  📁 node_modules/  (4821 items)
```

### Size emojis

The animal at the left gives an at-a-glance size category:

| Emoji | ASCII | Threshold |
|-------|-------|-----------|
| 🐋 | `[TB]` | > 1 TB |
| 🦕 | `[HG]` | > 100 GB |
| 🐘 | `[10G]` | > 10 GB |
| 🦁 | `[1G]` | > 1 GB |
| 🐻 | `[HM]` | > 100 MB |
| 🦊 | `[10M]` | > 10 MB |
| 🐦 | `[1M]` | > 1 MB |
| 🐜 | `[sm]` | < 1 MB |
| 🔒 | `[?]` | unknown (access denied) |

### Status badges

| Badge | Meaning |
|-------|---------|
| `🔒` | Directory exists but is inaccessible (permission denied) |
| `🌐 [NET]` | Directory lives on a network filesystem (NFS, EFS, CIFS…) |
| `🔗 [->]` | Symlink — size shown is the link itself, not the target |
| `⚠️ [!]` | Error during scan (shown with error type) |

### Header

While scanning: a spinner, a random quip, and a live progress bar showing how many top-level directories have been sized.

After scanning: the current path and the total size of all accessible items. The `≥` prefix appears when at least one directory was inaccessible, meaning the true total is higher.

### Footer

Shows the item count, scan time (with a speed emoji), current cursor position, and the count of inaccessible directories.

---

## Scanning behaviour

### How sizes are computed

1. For each top-level directory, a worker thread runs `du -s -k <path>` in a subprocess. This is the same strategy used by system tools — it's a C-level walk and is orders of magnitude faster than Python's `os.walk`.
2. If `du` exits with a permission error, DiskVu detects the false zero in its output and marks the directory as `UNKNOWN_SIZE` instead of showing `0`.
3. If `du` is not installed, DiskVu falls back to a Python recursive `os.scandir` walk.

### Timeouts

All filesystems share a single 3-minute (180 s) timeout per directory. This covers realistic worst cases on spinning disks (e.g. a large `/home` tree). If a directory genuinely can't be sized in 3 minutes it is marked `timeout >3min` and shown with `UNKNOWN_SIZE`.

For slow network mounts (NFS, EFS) the right solution is `--skip-network`, not a longer timeout.

### Directories that are always skipped

DiskVu skips virtual and pseudo-filesystems that would hang `du` or cause infinite loops:

**Linux:** `/proc`, `/sys`, `/dev`, `/run/user`, `/sys/kernel/debug`, `/sys/kernel/tracing`, and directories named `proc`, `sys`, `dev`, `run`, `snap`, `debug`, `tracing`, `cgroup`, `cgroup2`, `configfs`, `securityfs`, `pstore`, `efivarfs`.

**macOS:** `/private/var/vm` (swap), `/private/var/folders` (temp), `/cores` (crash dumps), and directories named `net`, `home`.

### Network filesystem skip (`--skip-network`)

DiskVu reads `/proc/mounts` (Linux) or parses `mount` output (macOS) at startup to build a mount table. Directories on the following filesystem types are tagged `[NET]` in the display, and skipped entirely when `--skip-network` is passed:

`nfs`, `nfs3`, `nfs4`, `cifs`, `smb`, `smbfs`, `afs`, `coda`, `davfs`, `sshfs`, `ftpfs`, `s3fs`, `s3fuse`

> On AWS, EFS mounts appear as `nfs4` in the mount table.

### Caching

- Each scanned directory is cached in memory as `(mtime, entries)`.
- Navigating back to a directory is instant if the mtime hasn't changed.
- `r` (rescan) explicitly invalidates the cache for the current directory.
- Deleting an entry invalidates the cache for the current directory automatically.

### Prefetch

After a scan completes, DiskVu silently pre-scans the largest uncached subdirectory in a background daemon thread. When you open that directory, results appear instantly. Prefetch is skipped when less than 300 MB of RAM is available.

---

## Access and permissions

### macOS — Full Disk Access

To scan protected directories like `~/Library`, `/System`, or `/private`:

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Add your terminal application (Terminal.app, iTerm2, Ghostty, etc.)

### Linux — running as root

To scan directories owned by root or other system users:

```bash
sudo diskvu /
sudo diskvu /var
```

When DiskVu cannot read a directory it marks it `🔒 inaccessible` and shows a hint at the bottom of the screen:

- **macOS:** `grant Full Disk Access to Terminal in System Settings`
- **Linux:** `run as root (sudo diskvu /) or fix directory permissions`

---

## Delete

Press `d` on any entry to delete it. A confirmation prompt appears:

```
 💀 Nuke 'node_modules' (10.2 G)? This is permanent! (y/N)
```

Type `y` or `Y` to confirm. Any other key cancels. After deletion the current directory is rescanned automatically.

> **Warning:** deletion is immediate and permanent — there is no trash/recycle bin. Directories are removed with the equivalent of `rm -rf`.

DiskVu refuses to delete protected system paths (`/`, `/etc`, `/usr`, `/bin`, `/System`, `~`, etc.) even when confirmed.

---

## Signal handling

| Signal | Behaviour |
|--------|-----------|
| `Ctrl+C` (`SIGINT`) | Exits cleanly; curses restores the terminal. |
| `SIGTERM` | Calls `curses.endwin()` before exiting — safe for container orchestration (Docker, systemd, ECS task stop). |

---

## Platform notes

### macOS

- `du` is BSD `du` — flags `-s -k` are used (POSIX-compatible).
- Apple Silicon page size (16 KB) is detected correctly for the RAM check in prefetch.
- `o` key opens the selected item in Finder via `open -R`.

### Linux

- `du` is GNU `du` — same flags work.
- RAM availability is read from `MemAvailable` in `/proc/meminfo` (accounts for reclaimable pages).
- `o` key tries Nautilus, Dolphin, Thunar, then falls back to `xdg-open`.
- On headless servers / EC2 without a display, `o` will fail silently with an error toast — this is expected.

### EC2 / SSH sessions

- ASCII mode is **auto-enabled** when the locale is not UTF-8 (common on minimal Amazon Linux 2 / Amazon Linux 2023 AMIs).
- Force it explicitly with `--ascii` or `DISKVU_ASCII=1` to be safe.
- EFS mounts are detected as `nfs4` and tagged `[NET]`. Use `--skip-network` to exclude them from the scan.
- The tool has no dependencies beyond Python's stdlib — it runs on any EC2 instance that has Python 3.8+.

---

## Version history

| Version | Changes |
|---------|---------|
| 1.2.0 | Bug fixes: scan no longer hangs on unexpected errors; subprocess cleanup on exception; Python fallback walk respects cancellation; partial-results cache capped at 50 entries; delete blocked on protected system paths |
| 1.1.0 | ASCII mode, network FS detection, argparse CLI, SIGTERM handler, `~` and `o` keys, expanded Linux skip list, reduced `du` timeout |
| 1.0.0 | Initial release — streaming scan, parallel workers, cache, prefetch, delete |

---

*Made with love ❤️ by Mehdi Benfredj*
