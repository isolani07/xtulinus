#!/usr/bin/env bash
# install.sh — install XTU-Linux daemon + GUI + systemd service + desktop entry.
#
# Creates the 'xtuctl' group, adds the invoking user to it, copies the
# daemon and GUI into /usr/local/lib/xtu-linux, installs the systemd unit
# (enabled on boot), installs the .desktop entry and icon.
#
# Run as root:  sudo ./install.sh
set -euo pipefail

DAEMON_SRC="daemon/xtu_linuxd.py"
GUI_SRC="gui/main.py"
COMMON_SRC="common"
SYSTEMD_SRC="systemd/xtu-linuxd.service"
DESKTOP_SRC="packaging/xtu-linux.desktop"
ICON_SRC="icons/xtu-linux.svg"
PREFIX="${PREFIX:-/usr/local}"
LIBDIR="$PREFIX/lib/xtu-linux"
BINDIR="$PREFIX/bin"
SHARE="$PREFIX/share"
GROUP="xtuctl"
SOCKET="/run/xtu-linux.sock"

cd "$(dirname "$0")"

if [[ $EUID -ne 0 ]]; then
    echo "error: run as root (e.g. sudo ./install.sh)" >&2
    exit 1
fi

echo "==> Creating group '$GROUP'"
getent group "$GROUP" >/dev/null 2>&1 || groupadd "$GROUP"

echo "==> Adding invoking user ($SUDO_USER) to '$GROUP'"
if [[ -n "${SUDO_USER:-}" ]] && id -nG "$SUDO_USER" | grep -qw "$GROUP"; then
    echo "    already a member"
else
    [[ -n "${SUDO_USER:-}" ]] && usermod -a -G "$GROUP" "$SUDO_USER"
fi

echo "==> Installing daemon and GUI to $LIBDIR"
install -d -m 0755 "$LIBDIR/daemon"
install -m 0755 "$DAEMON_SRC" "$LIBDIR/daemon/"
install -d -m 0755 "$LIBDIR/gui"
install -m 0755 "$GUI_SRC" "$LIBDIR/gui/"
cp -r gui/*.py "$LIBDIR/gui/"
install -d -m 0755 "$LIBDIR/common"
install -m 0644 "$COMMON_SRC"/*.py "$LIBDIR/common/"
cp -r daemon/*.py "$LIBDIR/daemon/"

echo "==> Creating launcher $BINDIR/xtu-linux"
install -d -m 0755 "$BINDIR"
cat > "$BINDIR/xtu-linux" <<EOF
#!/usr/bin/env bash
exec "$LIBDIR/gui/main.py"
EOF
chmod 0755 "$BINDIR/xtu-linux"

echo "==> Installing systemd unit"
install -m 0644 "$SYSTEMD_SRC" /etc/systemd/system/xtu-linuxd.service
systemctl daemon-reload
systemctl enable xtu-linuxd.service
systemctl restart xtu-linuxd.service
echo "    daemon started (see: journalctl -u xtu-linuxd)"

echo "==> Installing safety config (only if absent)"
if [[ -f /etc/xtu-linux/config.json ]]; then
    echo "    keeping existing /etc/xtu-linux/config.json"
else
    install -d -m 0755 /etc/xtu-linux
    install -m 0644 "packaging/config.json" /etc/xtu-linux/config.json
    echo "    wrote /etc/xtu-linux/config.json"
fi

echo "==> Installing desktop entry and icon"
install -d -m 0755 "$SHARE/applications"
install -d -m 0755 "$SHARE/icons/hicolor/scalable/apps"
install -m 0644 "$DESKTOP_SRC" "$SHARE/applications/xtu-linux.desktop"
install -m 0644 "$ICON_SRC" "$SHARE/icons/hicolor/scalable/apps/xtu-linux.svg"
desktop-file-install "$SHARE/applications/xtu-linux.desktop" 2>/dev/null || true
update-desktop-database "$SHARE/applications" 2>/dev/null || true

echo
echo "Install complete. The GUI can be launched from the application menu."
echo "Note: log out and back in for the '$GROUP' group change to take effect."
