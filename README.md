# DiskVu

A fast, interactive terminal UI for visualizing disk usage — built with Go and [tcell](https://github.com/gdamore/tcell).

Navigate your filesystem, see directory sizes sorted by largest first, and delete space-hogs — all from the terminal.

## Features

- **Concurrent scanning** — directories are sized in parallel using `du`, with a pure-Go fallback
- **Live progress** — spinner, progress bar, and per-directory status as scanning happens
- **Visual usage bars** — proportional bars and percentages relative to the current directory total
- **Emoji size indicators** — quick visual cue for file/folder sizes (🐋 TB, 🐘 GB, 🦊 MB…)
- **File type icons** — recognizes common extensions (archives, video, audio, images, code, etc.)
- **Smart caching** — revisiting a directory is instant; cache is invalidated when mtime changes
- **Background prefetch** — silently pre-scans the largest uncached subdirectory while you browse
- **Delete support** — remove files or folders with a confirmation prompt (`d`)
- **Open in Finder/file manager** — jump to any item in your GUI file manager (`o`)
- **Network FS skip** — optionally skip NFS/CIFS/SSHFS mounts with `--skip-network`
- **Protected paths** — system directories and home root are guarded against accidental deletion
- **Cross-platform** — macOS and Linux

## Installation

**macOS / Linux — one-liner (recommended):**

```bash
curl -sSL https://raw.githubusercontent.com/MehdiBenfredj/DiskVu/main/install.sh | sh
```

The script will:
- Auto-detect your OS (macOS/Linux) and architecture (amd64/arm64)
- Download the latest pre-built binary from GitHub Releases
- Verify the SHA-256 checksum before installing
- Install to `/usr/local/bin/diskvu` (uses `sudo` only if needed)

**From source (requires Go 1.21+):**

```bash
git clone https://github.com/MehdiBenfredj/DiskVu
cd DiskVu
go build -o diskvu .
sudo mv diskvu /usr/local/bin/
```

**Via `go install` (requires Go 1.21+):**

```bash
go install github.com/MehdiBenfredj/diskvu@latest
```

**Quick run without installing:**

```bash
go run . [path]
```

## Usage

```
diskvu [flags] [path]
```

| Flag | Description |
|------|-------------|
| `--skip-network` | Skip NFS, CIFS, and other network/remote filesystems |
| `-V` | Print version and exit |

If `path` is omitted, the current directory (`.`) is used.

**macOS tip:** for full disk access grant Terminal (or your terminal app) Full Disk Access in  
`System Settings › Privacy & Security › Full Disk Access`.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` / `k` / `Ctrl-P` | Move cursor up |
| `↓` / `j` / `Ctrl-N` | Move cursor down |
| `PgUp` / `PgDn` | Page up / down |
| `Home` / `g` | Jump to top |
| `End` / `G` | Jump to bottom |
| `Enter` / `→` / `l` | Enter directory |
| `Backspace` / `←` / `h` | Go back (parent directory) |
| `~` | Jump to home directory |
| `r` | Rescan current directory (bypass cache) |
| `d` | Delete selected file or directory |
| `o` | Open selected item in Finder / file manager |
| `q` / `Esc` / `Q` | Quit |

## Uninstall

```bash
sudo rm /usr/local/bin/diskvu
```

## Releasing a new version

Releases are fully automated via [GoReleaser](https://goreleaser.com/) and GitHub Actions.  
To publish a new release, just tag and push:

```bash
git tag v2.0.0
git push origin v2.0.0
```

GitHub Actions will automatically:
1. Build binaries for macOS (arm64/amd64) and Linux (arm64/amd64)
2. Package each into a `.tar.gz` archive
3. Generate a `checksums.txt` file
4. Create a GitHub Release with all assets and a changelog

## Dependencies

- [gdamore/tcell](https://github.com/gdamore/tcell) — terminal rendering
- [mattn/go-runewidth](https://github.com/mattn/go-runewidth) — correct width for Unicode/emoji characters

## License

MIT
