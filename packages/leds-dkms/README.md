# UGREEN DXP LED kernel module

This DKMS package provides the `led-ugreen` Linux kernel module for UGREEN DXP
NAS systems whose front-panel LEDs are controlled by the onboard I2C LED
controller. It exposes those LEDs through the standard Linux LED subsystem, so
they appear under `/sys/class/leds` and can use kernel LED triggers such as
`netdev` and `oneshot`.

This repository is based on a fork of
[miskcoo/ugreen_leds_controller](https://github.com/miskcoo/ugreen_leds_controller),
[v0.3](https://github.com/miskcoo/ugreen_leds_controller/tree/v0.3).

It has been intentionally reduced to only the `led-ugreen` DKMS kernel module
and reorganized as a standard Debian package source tree.

The original project includes the reverse-engineered LED protocol, CLI tools,
systemd helper scripts, packaging experiments, and platform-specific build
support. This fork keeps only the code needed to build and publish the Debian
DKMS package `ugreen-dxp-pve-leds-dkms`.

The branch layout is designed for a GitHub Pages Debian repository:

- `debian/` contains the Debian packaging metadata.
- `ugreen-dxp-leds.c`, `ugreen-dxp-leds.h`, and `Makefile` are the module
  source installed under `/usr/src`.
- `debian/ugreen-dxp-pve-leds-dkms.dkms` is the DKMS configuration template used by
  `dh-dkms`.
- `.github/workflows/debian-pages.yml` builds the `.deb`, generates the APT
  repository metadata, and publishes it through GitHub Pages.

## Install

Building from source is optional. The package is already published through the
GitHub Pages Debian repository, and the latest `.deb` is also available as a
direct download.

Install from the Debian repository:

```sh
sudo install -d -m 0755 /etc/apt/keyrings

curl -fsSL https://lpaolini.github.io/ugreen-dxp-pve/public.key | sudo gpg --dearmor -o /etc/apt/keyrings/ugreen-dxp-pve.gpg

echo 'deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] https://lpaolini.github.io/ugreen-dxp-pve stable main' | sudo tee /etc/apt/sources.list.d/ugreen-dxp-pve.list

sudo apt update
sudo apt install ugreen-dxp-pve-leds-dkms
```

Or download and install the latest `.deb` directly:

```sh
cd /tmp
curl -LO https://lpaolini.github.io/ugreen-dxp-pve/downloads/ugreen-dxp-pve-leds-dkms_latest.deb
sudo apt install ./ugreen-dxp-pve-leds-dkms_latest.deb
```

Use a world-readable directory such as `/tmp` for direct `.deb` installs. If the
file is under `/root`, `apt` may print a harmless `_apt` sandbox warning because
the `_apt` user cannot read files in `/root`.

The package registers the module with DKMS and configures `i2c-dev`,
`led-ugreen`, `ledtrig-oneshot`, and `ledtrig-netdev` to load at boot.
It installs one systemd oneshot service that binds the `led-ugreen` driver to
the LED controller at I2C address `0x3a`, and a long-running service that
monitors power LED fault flags and configures `/sys/class/leds/ugreen:white:netdev`,
plus any present disk LEDs from
`/sys/class/leds/ugreen:white:disk1` through
`/sys/class/leds/ugreen:white:disk8`.

The LED configuration lives in:

```text
/etc/ugreen-dxp-pve-leds.conf
```

By default, the power LED is set to steady green, and the network LED uses the
kernel `netdev` trigger for `vmbr0` in blue. Adjust `NETDEV_LED_DEVICE` when
your Proxmox management bridge or physical interface has a different name.
Disk LEDs are initialized to steady purple with no activity trigger so the
later TrueNAS helper starts from a quiet, known state. Set
`POWER_LED_NORMAL_COLOR`, `NETDEV_LED_COLOR`, and `DISK_LED_COLOR` to change the
RGB colors. Colors may be written as decimal RGB triplets or six-digit hex
values:

```sh
POWER_LED_NORMAL_COLOR="0 64 16"
NETDEV_LED_COLOR="0 64 255"
DISK_LED_COLOR="64 0 64"
```

The power LED is rendered by `ugreen-dxp-pve-leds.service`, which watches the
configured `POWER_LED_STATE_DIR` with inotify. Services should change power LED
state only by setting or clearing named flags with:

```sh
/usr/libexec/ugreen-dxp-pve-leds-dkms/power-led-ugreen
```

For example:

```sh
/usr/libexec/ugreen-dxp-pve-leds-dkms/power-led-ugreen set-fault fan_control
/usr/libexec/ugreen-dxp-pve-leds-dkms/power-led-ugreen clear-fault fan_control
```

Any active flag switches the power LED to the `POWER_LED_FAULT_*` state. By
default this is full-brightness red with `blink 500 500`, a 1 second period at
50% duty cycle. When the last flag clears, the monitor restores the
`POWER_LED_NORMAL_*` state.

If the module is loaded but `/sys/class/leds/ugreen:white:disk1` does not
exist, the I2C device has probably not been created yet. Run the helper
manually:

```sh
sudo /usr/libexec/ugreen-dxp-pve-leds-dkms/probe-led-ugreen
ls /sys/class/leds
```

If the helper tries the wrong bus, list adapters and force the correct bus:

```sh
i2cdetect -l
echo I2C_BUS=1 | sudo tee /etc/ugreen-dxp-pve-leds.conf
sudo /usr/libexec/ugreen-dxp-pve-leds-dkms/probe-led-ugreen
```

Replace `1` with the bus number that owns the LED controller.

## Build

Only build locally if you want to modify the package or inspect the generated
artifact yourself.

Install the Debian build dependencies:

```sh
sudo apt install build-essential debhelper dh-dkms dkms
```

Build the package from the repository root:

```sh
dpkg-buildpackage -b -us -uc -tc
```

The resulting `ugreen-dxp-pve-leds-dkms_*.deb` package is written to the parent
directory by `dpkg-buildpackage`.

## License

The upstream `ugreen_leds_controller` project includes an MIT license for the
repository, while the retained Linux kernel module source files declare
`GPL-2.0-only` through SPDX metadata. This package keeps both notices:
`LICENSE` contains the MIT text, `COPYING` contains the GPL-2 text, and
`debian/copyright` records the file-level license breakdown.

## Acknowledgments

I wish to thank [Yuhao Zhou](https://github.com/miskcoo) for his cool project!
