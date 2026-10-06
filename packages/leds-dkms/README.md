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
`led-ugreen`, `ledtrig-oneshot`, and `ledtrig-netdev` to load at boot. It
installs two systemd services:

- `ugreen-dxp-pve-leds-bind.service` binds the `led-ugreen` driver to the LED
  controller at I2C address `0x3a`, and unbinds it when stopped.
- `ugreen-dxp-pve-leds.service` is the only process that writes to
  `/sys/class/leds/ugreen:*`. For each LED it shows the highest-priority state
  that other services have published, or the LED's default when none has.

### Publishing LED states

A service publishes a state by writing its name into a file:

```text
/run/ugreen-dxp-pve/<producer>/<led>
```

`<led>` is `power`, `netdev`, or `disk1` through `disk8`; the file holds one
state name such as `FAULT` or `DEGRADED`. Give the publishing unit
`RuntimeDirectory=ugreen-dxp-pve/<producer>`: systemd creates the directory
(exported as `$RUNTIME_DIRECTORY`) and deletes it when the unit stops, so the
unit's states disappear with it. Write atomically (write `.<led>.tmp`, then
rename it over `<led>`); delete the file to withdraw the state.

From a shell, use `ugreen-dxp-led`, which checks LED and state names:

```sh
ugreen-dxp-led set power FAULT   # into $RUNTIME_DIRECTORY, else /run/ugreen-dxp-pve/manual
ugreen-dxp-led clear power
ugreen-dxp-led status            # what each LED shows, and which producers asked for it
```

### Configuring the LEDs

LEDs and states are defined in `/usr/share/ugreen-dxp-pve-leds/leds.toml`.
Override any key in `/etc/ugreen-dxp-pve-leds.toml`, then run
`systemctl reload ugreen-dxp-pve-leds.service`:

```toml
[states.NETDEV]
device_name = "vmbr1"   # network LED follows another interface

[states.NORMAL]
color = "#004010"       # "R G B" or "#rrggbb"
```

By default the power LED is steady green (`NORMAL`) and blinks red while any
service publishes `FAULT`; the network LED uses the kernel `netdev` trigger for
`vmbr0` in blue; disk LEDs stay `OFF` until a service publishes a state for
them.

### Troubleshooting

If `/sys/class/leds/ugreen:white:power` does not exist, the controller is not
bound yet:

```sh
systemctl status ugreen-dxp-pve-leds-bind.service
ls /sys/class/leds
```

The controller's bus is found by adapter name at every start. Linux numbers
I2C buses in driver load order, so the number can change between boots; pin
it only if auto-detection fails. List the adapters (`i2cdetect -l` from
`i2c-tools`) and force the bus in `/etc/ugreen-dxp-pve-leds.toml`:

```toml
[bind]
i2c_bus = 1
```

Then run `systemctl restart ugreen-dxp-pve-leds-bind.service`.

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
