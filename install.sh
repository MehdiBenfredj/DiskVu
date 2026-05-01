#!/usr/bin/env sh
set -e

REPO="MehdiBenfredj/DiskVu"
BINARY="diskvu"
INSTALL_DIR="/usr/local/bin"

# ── Detect OS ────────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux)  OS="linux" ;;
  Darwin) OS="darwin" ;;
  *)
    echo "Unsupported OS: $OS"
    exit 1
    ;;
esac

# ── Detect Architecture ──────────────────────────────────────────────────────
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)          ARCH="amd64" ;;
  amd64)           ARCH="amd64" ;;
  arm64|aarch64)   ARCH="arm64" ;;
  *)
    echo "Unsupported architecture: $ARCH"
    exit 1
    ;;
esac

# ── Fetch latest version from GitHub API ────────────────────────────────────
echo "Fetching latest release..."
VERSION="$(curl -sSf "https://api.github.com/repos/${REPO}/releases/latest" \
  | grep '"tag_name"' \
  | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')"

if [ -z "$VERSION" ]; then
  echo "Could not determine latest version. Check your internet connection."
  exit 1
fi

echo "Latest version: $VERSION"

# ── Build download URL ───────────────────────────────────────────────────────
ARCHIVE="${BINARY}_${VERSION#v}_${OS}_${ARCH}.tar.gz"
BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"
DOWNLOAD_URL="${BASE_URL}/${ARCHIVE}"
CHECKSUM_URL="${BASE_URL}/checksums.txt"

# ── Download to temp dir ─────────────────────────────────────────────────────
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading $ARCHIVE..."
curl -sSfL "$DOWNLOAD_URL" -o "$TMP/$ARCHIVE"
curl -sSfL "$CHECKSUM_URL" -o "$TMP/checksums.txt"

# ── Verify checksum ──────────────────────────────────────────────────────────
echo "Verifying checksum..."
cd "$TMP"
if [ "$OS" = "darwin" ] && command -v shasum > /dev/null 2>&1; then
  grep "$ARCHIVE" checksums.txt | shasum -a 256 --check --status
elif command -v sha256sum > /dev/null 2>&1; then
  grep "$ARCHIVE" checksums.txt | sha256sum --check --status
elif command -v shasum > /dev/null 2>&1; then
  grep "$ARCHIVE" checksums.txt | shasum -a 256 --check --status
else
  echo "Warning: no sha256 tool found, skipping checksum verification."
fi
cd - > /dev/null

# ── Extract & install ────────────────────────────────────────────────────────
echo "Installing to $INSTALL_DIR/$BINARY..."
tar -xzf "$TMP/$ARCHIVE" -C "$TMP"

if [ -w "$INSTALL_DIR" ]; then
  mv "$TMP/$BINARY" "$INSTALL_DIR/$BINARY"
else
  sudo mv "$TMP/$BINARY" "$INSTALL_DIR/$BINARY"
fi

chmod +x "$INSTALL_DIR/$BINARY"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "diskvu $VERSION installed successfully!"
echo "Run: diskvu [path]"
