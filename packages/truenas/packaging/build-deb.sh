#!/usr/bin/env bash
set -euo pipefail

PACKAGE="ugreen-dxp-pve-truenas"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"
OUT_DIR="${2:-dist}"

if [[ -z "${VERSION}" ]]; then
  VERSION="$(git -C "${PACKAGE_ROOT}" describe --tags --always --dirty 2>/dev/null || echo "0.0.0")"
fi

# Debian versions cannot include the common Git tag prefix.
VERSION="${VERSION#v}"

(cd "${PACKAGE_ROOT}" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=../leds-dkms:src python3 -m unittest discover -s tests -t .)

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

PKG_DIR="${BUILD_DIR}/${PACKAGE}_${VERSION}_all"

install -d -m 0755 \
  "${PKG_DIR}/DEBIAN" \
  "${PKG_DIR}/usr/bin" \
  "${PKG_DIR}/usr/lib/${PACKAGE}/ugreen_truenas" \
  "${PKG_DIR}/usr/libexec/${PACKAGE}" \
  "${PKG_DIR}/usr/share/doc/${PACKAGE}" \
  "${PKG_DIR}/usr/share/${PACKAGE}" \
  "${PKG_DIR}/lib/systemd/system" \
  "${OUT_DIR}"

install -m 0755 "${PACKAGE_ROOT}/src/fan/ugreen-truenas-fan.py" "${PKG_DIR}/usr/bin/ugreen-truenas-fan.py"
install -m 0755 "${PACKAGE_ROOT}/src/zfs/ugreen-truenas-zfs.py" "${PKG_DIR}/usr/bin/ugreen-truenas-zfs.py"
install -m 0644 "${PACKAGE_ROOT}"/src/ugreen_truenas/*.py "${PKG_DIR}/usr/lib/${PACKAGE}/ugreen_truenas/"
install -m 0755 "${PACKAGE_ROOT}/src/migrate/ugreen-dxp-pve-truenas-migrate" "${PKG_DIR}/usr/libexec/${PACKAGE}/ugreen-dxp-pve-truenas-migrate"
install -m 0644 "${PACKAGE_ROOT}/src/ugreen-dxp-pve-truenas.toml" "${PKG_DIR}/usr/share/${PACKAGE}/ugreen-dxp-pve-truenas.toml"

for unit in \
  "${PACKAGE_ROOT}/src/fan/ugreen-truenas-fan.service" \
  "${PACKAGE_ROOT}/src/zfs/ugreen-truenas-zfs.service"
do
  sed 's#/usr/local/bin/#/usr/bin/#g' "${unit}" > "${PKG_DIR}/lib/systemd/system/$(basename "${unit}")"
  chmod 0644 "${PKG_DIR}/lib/systemd/system/$(basename "${unit}")"
done

cat > "${PKG_DIR}/DEBIAN/control" <<CONTROL
Package: ${PACKAGE}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: lpaolini <lpaolini@users.noreply.github.com>
Depends: python3 (>= 3.11), systemd, qemu-server, ugreen-dxp-pve-leds-dkms (>= ${VERSION}), ugreen-dxp-pve-it87-dkms
Provides: ugreen-dxp-proxmox-truenas
Conflicts: ugreen-dxp-proxmox-truenas
Replaces: ugreen-dxp-proxmox-truenas
Description: UGREEN DXP Proxmox/TrueNAS fan and ZFS LED helpers
 Systemd units and Python helpers for driving UGREEN fan PWM and front-panel
 disk LEDs from a TrueNAS VM running under Proxmox.
CONTROL

cat > "${PKG_DIR}/DEBIAN/preinst" <<'PREINST'
#!/bin/sh
set -e

# The 0.9.10 .conf files are replaced by /etc/ugreen-dxp-pve-truenas.toml. An
# edited one is kept as .dpkg-bak for the migration in postinst.
dpkg-maintscript-helper rm_conffile /etc/ugreen-dxp-pve-truenas-fan.conf -- "$@"
dpkg-maintscript-helper rm_conffile /etc/ugreen-dxp-pve-truenas-zfs.conf -- "$@"
PREINST

cat > "${PKG_DIR}/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e

dpkg-maintscript-helper rm_conffile /etc/ugreen-dxp-pve-truenas-fan.conf -- "$@"
dpkg-maintscript-helper rm_conffile /etc/ugreen-dxp-pve-truenas-zfs.conf -- "$@"

CONF=/etc/ugreen-dxp-pve-truenas.toml
TEMPLATE=/usr/share/ugreen-dxp-pve-truenas/ugreen-dxp-pve-truenas.toml
MIGRATE=/usr/libexec/ugreen-dxp-pve-truenas/ugreen-dxp-pve-truenas-migrate

# The old config file to migrate: dpkg's copy of an edited conffile, else the file.
old_conf() {
  for candidate in "$1.dpkg-bak" "$1"; do
    if [ -e "$candidate" ]; then
      echo "$candidate"
      return
    fi
  done
}

if [ "${1:-}" = "configure" ]; then
  if [ -e "$CONF" ]; then
    # Add the tables a newer release introduced; settings are never changed.
    "$MIGRATE" "$TEMPLATE" "$CONF" || echo "Could not update $CONF; compare it with $TEMPLATE."
  else
    fan="$(old_conf /etc/ugreen-dxp-pve-truenas-fan.conf)"
    zfs="$(old_conf /etc/ugreen-dxp-pve-truenas-zfs.conf)"
    if [ -n "$fan$zfs" ] && "$MIGRATE" ${fan:+--fan "$fan"} ${zfs:+--zfs "$zfs"} "$TEMPLATE" "$CONF"; then
      for old in "$fan" "$zfs"; do
        if [ -n "$old" ]; then
          mv "$old" "${old%.dpkg-bak}.migrated"
        fi
      done
    else
      if [ -n "$fan$zfs" ]; then
        echo "Could not migrate the old settings; they are kept in $fan $zfs. Set vmid in $CONF."
      fi
      install -m 0644 "$TEMPLATE" "$CONF"
    fi
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl enable --now ugreen-truenas-zfs.service || true
  systemctl enable --now ugreen-truenas-fan.service || true
  if [ -n "${2:-}" ]; then
    # Upgrade: replace the running processes with the new code.
    systemctl try-restart ugreen-truenas-zfs.service ugreen-truenas-fan.service || true
    # The old services published under /run/ugreen-dxp-pve; nothing reads it now.
    rm -rf /run/ugreen-dxp-pve
  fi
fi
POSTINST

cat > "${PKG_DIR}/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e

if [ "${1:-}" = "remove" ] || [ "${1:-}" = "deconfigure" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now ugreen-truenas-zfs.service || true
    systemctl disable --now ugreen-truenas-fan.service || true
  fi
  /usr/bin/ugreen-truenas-fan.py --stop || true
fi
PRERM

cat > "${PKG_DIR}/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e

dpkg-maintscript-helper rm_conffile /etc/ugreen-dxp-pve-truenas-fan.conf -- "$@"
dpkg-maintscript-helper rm_conffile /etc/ugreen-dxp-pve-truenas-zfs.conf -- "$@"

if [ "${1:-}" = "purge" ]; then
  rm -f /etc/ugreen-dxp-pve-truenas.toml \
    /etc/ugreen-dxp-pve-truenas-fan.conf.migrated \
    /etc/ugreen-dxp-pve-truenas-zfs.conf.migrated
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
POSTRM

chmod 0755 \
  "${PKG_DIR}/DEBIAN/preinst" \
  "${PKG_DIR}/DEBIAN/postinst" \
  "${PKG_DIR}/DEBIAN/prerm" \
  "${PKG_DIR}/DEBIAN/postrm"

cat > "${PKG_DIR}/usr/share/doc/${PACKAGE}/changelog" <<CHANGELOG
${PACKAGE} (${VERSION}) stable; urgency=medium

  * Read all settings and the LED looks from /etc/ugreen-dxp-pve-truenas.toml,
    created from the old .conf files on upgrade.
  * Publish LED looks under /run/ugreen-dxp-leds/truenas-zfs and truenas-fan.
  * Show the hottest disk temperature on the power LED: orange from
    [fan] temp_warning, red from temp_alert.

 -- Luca Paolini <lookap@gmail.com>  Sat, 10 Oct 2026 12:00:00 +0200
CHANGELOG
gzip -n -9 "${PKG_DIR}/usr/share/doc/${PACKAGE}/changelog"

cat > "${PKG_DIR}/usr/share/doc/${PACKAGE}/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ugreen-dxp-pve-truenas
Source: https://github.com/lpaolini/ugreen-dxp

Files: *
Copyright: 2026 Luca Paolini <lookap@gmail.com>
License: MIT

License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
COPYRIGHT

(
  cd "${PKG_DIR}"
  find usr lib -type f -exec md5sum {} + > DEBIAN/md5sums
)

dpkg-deb --build --root-owner-group "${PKG_DIR}" "${OUT_DIR}/${PACKAGE}_${VERSION}_all.deb"
