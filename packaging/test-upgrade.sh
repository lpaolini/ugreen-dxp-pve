#!/usr/bin/env bash
# shellcheck disable=SC2016  # ${Package} and ${Version} are dpkg-query fields
# SPDX-License-Identifier: MIT
# Upgrade test: install the published packages of a channel, edit their
# configuration like an admin would, upgrade to the debs in DIST_DIR and check
# that the settings survived. Run it in a throwaway Debian container:
#
#   docker run --rm -v "$PWD":/src -w /src debian:bookworm packaging/test-upgrade.sh stable dist
#
# CHANNEL is "stable" (0.9.10) or "dev". dkms, pve-headers and qemu-server are
# replaced by stub packages, so no kernel module is built.
set -euo pipefail

usage="usage: $0 stable|dev DIST_DIR"
CHANNEL="${1:?${usage}}"
DIST="$(cd "${2:?${usage}}" && pwd)"
REPO=https://lpaolini.github.io/ugreen-dxp-pve
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg equivs systemd python3 >/dev/null

stub() {  # stub NAME [SOURCE DEST]...: build and install an empty package NAME
  local name="$1" dir
  shift
  dir="$(mktemp -d)"
  {
    echo "Package: ${name}"
    echo "Version: 99"
    echo "Maintainer: upgrade test <test@example.org>"
    echo "Description: stub for the upgrade test"
    if [[ $# -gt 0 ]]; then
      echo "Files: $1 $2"
      shift 2
      while [[ $# -gt 0 ]]; do
        echo " $1 $2"
        shift 2
      done
    fi
  } > "${dir}/control"
  (cd "${dir}" && equivs-build control >/dev/null)
  dpkg -i "${dir}"/*.deb >/dev/null
}

stubs="$(mktemp -d)"
printf '#!/bin/sh\nexit 0\n' > "${stubs}/dkms"
cp "${stubs}/dkms" "${stubs}/common.postinst"
chmod 0755 "${stubs}/dkms" "${stubs}/common.postinst"
stub dkms "${stubs}/dkms" /usr/sbin/ "${stubs}/common.postinst" /usr/lib/dkms/
stub pve-headers
stub qemu-server

install -d /etc/apt/keyrings
curl -fsSL "${REPO}/public.key" | gpg --dearmor -o /etc/apt/keyrings/ugreen-dxp-pve.gpg
echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] ${REPO} ${CHANNEL} main" \
  > /etc/apt/sources.list.d/ugreen-dxp-pve.list
apt-get update -qq
apt-get install -y -qq ugreen-dxp-pve-truenas >/dev/null
echo "installed: $(dpkg-query -W -f='${Package} ${Version}  ' 'ugreen-dxp-pve-*')"

# Edit the configuration like an admin would.
for conf in /etc/ugreen-dxp-pve-truenas-fan.conf /etc/ugreen-dxp-pve-truenas-zfs.conf; do
  sed -i 's/^# *VMID=.*/VMID=105/' "${conf}"
done
sed -i 's/^MIN_PWM=.*/MIN_PWM=80/' /etc/ugreen-dxp-pve-truenas-fan.conf
if [[ -e /etc/ugreen-dxp-pve-leds.conf ]]; then  # 0.9.10 shell config
  sed -i -e 's/^# *I2C_BUS=.*/I2C_BUS=1/' -e 's/^NETDEV_LED_DEVICE=.*/NETDEV_LED_DEVICE=vmbr1/' \
    /etc/ugreen-dxp-pve-leds.conf
else  # dev channel: named-state overrides
  printf '[bind]\ni2c_bus = 1\n\n[states.NETDEV]\ndevice_name = "vmbr1"\n' \
    > /etc/ugreen-dxp-pve-leds.toml
fi

apt-get install -y -qq "${DIST}"/ugreen-dxp-pve-*.deb
echo "upgraded: $(dpkg-query -W -f='${Package} ${Version}  ' 'ugreen-dxp-pve-*')"

check() {
  python3 - <<'EOF'
import sys
sys.path[:0] = ["/usr/lib/ugreen-dxp-pve-truenas", "/usr/lib/ugreen-dxp-pve-leds"]
from ugreen_leds.config import load_config
from ugreen_truenas.config import load

leds = load_config()
assert leds.i2c_bus == 1, leds
assert dict(leds.netdev)["device_name"] == "vmbr1", leds.netdev
truenas = load()
assert truenas.vmid == "105", truenas
assert truenas.fan.min_pwm == 80, truenas.fan
EOF
  for conf in /etc/ugreen-dxp-pve-truenas-fan.conf /etc/ugreen-dxp-pve-truenas-zfs.conf; do
    test ! -e "${conf}"
    test -e "${conf}.migrated"
  done
  test -d /run/ugreen-dxp-leds
}

check
sums="$(sha256sum /etc/ugreen-dxp-pve-leds.toml /etc/ugreen-dxp-pve-truenas.toml)"
apt-get install -y -qq --reinstall "${DIST}"/ugreen-dxp-pve-*.deb >/dev/null
check
[[ "$(sha256sum /etc/ugreen-dxp-pve-leds.toml /etc/ugreen-dxp-pve-truenas.toml)" == "${sums}" ]]
echo "upgrade from ${CHANNEL}: OK"
