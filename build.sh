#!/usr/bin/env bash
# Builds a self-contained .deb for this host's architecture.
#
# Uses uv to pin both the Python interpreter version and the package deps: uv
# downloads a relocatable standalone CPython build (python-build-standalone,
# the same thing "uv python install" always uses) and installs this project
# plus its dependencies straight into it with --break-system-packages
# (bypassing the "externally managed" guard uv itself puts on that install,
# since here *we* are the one packaging it, not a stray system pip). No venv
# layer is used: a venv's activation/launcher scripts bake in the absolute
# build-time path and break once dpkg moves the tree to /opt on the target
# machine, whereas the interpreter's own sys.prefix resolution (and imports
# under its own lib/site-packages) survive being moved/copied verbatim. This
# is also why the systemd unit invokes the interpreter with `-m` directly
# instead of relying on any generated console-script wrapper.
#
# See CLAUDE.md for the full history of why this is this careful -- this
# project's packaging is adapted line-for-line from a sibling project
# (ups-shutdown-agent) that hit, and fixed, several real production bugs in
# exactly this vendoring step.
set -euo pipefail

# Pinned to an exact patch release, not just "3.12" -- keeps the build reproducible and,
# combined with --install-dir below, guarantees there is exactly one possible interpreter
# directory to vendor from.
PYTHON_VERSION="3.12.14"
# Matches the interpreter's actual binary name (pythonX.Y, not pythonX.Y.Z) -- derived
# rather than hardcoded so bumping PYTHON_VERSION's patch component above can't
# accidentally desync it.
PYTHON_ABI="${PYTHON_VERSION%.*}"
PKG_NAME="mqtt-exec-agent"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$ROOT_DIR/build"
PKG_ROOT="$BUILD_DIR/pkgroot"
INSTALL_PREFIX="/opt/$PKG_NAME"

PKG_VERSION="$(grep -m1 '^version' "$ROOT_DIR/pyproject.toml" | sed -E 's/version = "(.*)"/\1/')"
ARCH="$(dpkg --print-architecture)"

echo "Building $PKG_NAME $PKG_VERSION ($ARCH) ..."

rm -rf "$BUILD_DIR"
mkdir -p "$PKG_ROOT$INSTALL_PREFIX" \
         "$PKG_ROOT/etc/$PKG_NAME" \
         "$PKG_ROOT/lib/systemd/system" \
         "$PKG_ROOT/DEBIAN"

echo "==> Fetching CPython $PYTHON_VERSION via uv"
# --install-dir, deliberately NOT uv's default shared/managed location. A fresh,
# dedicated --install-dir per build is a plain, ordinary extraction with no
# shared-store indirection to go wrong (confirmed on the sibling project: the default
# location can leave bin/python* as symlinks whose chain escapes the vendored tree
# entirely, which only breaks once the tree is relocated to a different machine).
UV_PYTHON_DIR="$BUILD_DIR/uv-python"
mkdir -p "$UV_PYTHON_DIR"
uv python install "$PYTHON_VERSION" --install-dir "$UV_PYTHON_DIR"
BASE_PYTHON_ROOT="$(find "$UV_PYTHON_DIR" -maxdepth 1 -type d -name "cpython-$PYTHON_VERSION-*")"
if [ -z "$BASE_PYTHON_ROOT" ] || [ "$(printf '%s\n' "$BASE_PYTHON_ROOT" | wc -l)" -ne 1 ]; then
  echo "::error::expected exactly one cpython-$PYTHON_VERSION-* directory under $UV_PYTHON_DIR, got: $BASE_PYTHON_ROOT" >&2
  exit 1
fi

echo "==> Vendoring interpreter into package tree"
# Plain -a: with a fresh, non-shared-store extraction (above), the only symlinks left are
# ordinary same-directory ones (bin/python -> bin/python3.12, lib64 -> lib) that survive
# relocation fine as-is. Do NOT reach for -L/dereference-everything here -- that broke
# lib64 on the sibling project (see CLAUDE.md).
cp -a "$BASE_PYTHON_ROOT" "$PKG_ROOT$INSTALL_PREFIX/python"
BUNDLED_PYTHON="$PKG_ROOT$INSTALL_PREFIX/python/bin/python$PYTHON_ABI"

echo "==> Installing $PKG_NAME + dependencies into the vendored interpreter"
uv pip install --python "$BUNDLED_PYTHON" --break-system-packages "$ROOT_DIR"

# Fail the build here, loudly, rather than ship a .deb whose systemd unit can only
# discover at install time (on a completely different machine) that the vendored
# interpreter doesn't actually run.
echo "==> Verifying the vendored interpreter actually runs"
"$BUNDLED_PYTHON" -c "import mqtt_exec_agent.daemon, paho.mqtt.client; print('vendored interpreter OK:', __import__('sys').version)"

echo "==> Trimming unneeded stdlib test suite / caches"
rm -rf "$PKG_ROOT$INSTALL_PREFIX/python/lib/python$PYTHON_ABI"*/test
find "$PKG_ROOT$INSTALL_PREFIX/python" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Staging config, systemd unit, control files"
cp "$ROOT_DIR/config/config.toml.example" "$PKG_ROOT/etc/$PKG_NAME/config.toml"
cp "$ROOT_DIR/systemd/$PKG_NAME.service" "$PKG_ROOT/lib/systemd/system/"

INSTALLED_SIZE_KB="$(du -sk "$PKG_ROOT" | cut -f1)"
sed -e "s/@VERSION@/$PKG_VERSION/" \
    -e "s/@ARCH@/$ARCH/" \
    -e "s/@INSTALLED_SIZE@/$INSTALLED_SIZE_KB/" \
    "$ROOT_DIR/debian/control.in" > "$PKG_ROOT/DEBIAN/control"

cp "$ROOT_DIR/debian/conffiles" "$PKG_ROOT/DEBIAN/conffiles"
cp "$ROOT_DIR/debian/postinst" "$PKG_ROOT/DEBIAN/postinst"
cp "$ROOT_DIR/debian/prerm" "$PKG_ROOT/DEBIAN/prerm"
cp "$ROOT_DIR/debian/postrm" "$PKG_ROOT/DEBIAN/postrm"
chmod 755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm" "$PKG_ROOT/DEBIAN/postrm"

OUT_DEB="$BUILD_DIR/${PKG_NAME}_${PKG_VERSION}_${ARCH}.deb"
echo "==> Building $OUT_DEB"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$OUT_DEB"

echo "Done: $OUT_DEB"
