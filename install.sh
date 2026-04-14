#!/usr/bin/env bash
# DiskVu installer — works on macOS, Linux, and EC2 instances
# Usage: ./install.sh [install_dir]
#        sudo ./install.sh /usr/local/bin   (system-wide)
#        ./install.sh ~/.local/bin          (user-only)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${1:-/usr/local/bin}"

# Verify Python 3.8+
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.8+ first." >&2
    exit 1
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print(sys.version_info[:2])')
if "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>/dev/null; then
    : # OK
else
    echo "Error: Python 3.8+ required (found $("$PYTHON" --version))" >&2
    exit 1
fi

# Create install dir if needed (for ~/.local/bin)
mkdir -p "$INSTALL_DIR"

DEST="$INSTALL_DIR/diskvu"
cp "$SCRIPT_DIR/diskvu.py" "$DEST"
chmod +x "$DEST"

# Patch shebang to use the detected python3
if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "1s|.*|#!/usr/bin/env python3|" "$DEST"
else
    sed -i "1s|.*|#!/usr/bin/env python3|" "$DEST"
fi

echo "✓ Installed to $DEST"
echo "  Run: diskvu [path]"

# Warn if install dir is not in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$INSTALL_DIR"; then
    echo ""
    echo "  Note: $INSTALL_DIR is not in your PATH."
    echo "  Add this to your shell config (~/.bashrc, ~/.zshrc, etc.):"
    echo "    export PATH=\"$INSTALL_DIR:\$PATH\""
fi
