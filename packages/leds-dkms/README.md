# UGREEN DXP LED kernel module

This DKMS package provides the `led-ugreen` Linux kernel module for UGREEN DXP
NAS systems whose front-panel LEDs are controlled by the onboard I2C LED
controller. It exposes those LEDs through the standard Linux LED subsystem, so
they appear under `/sys/class/leds` and can use kernel LED triggers such as
`netdev` and `oneshot`.

This repository is based on a fork of
[miskcoo/ugreen_leds_controller](https://github.com/miskcoo/ugreen_leds_controller),
[v0.3](https://github.com/miskcoo/ugreen_leds_controller/tree/v0.3).

It keeps the `led-ugreen` DKMS kernel module, reorganized as a standard Debian
package source tree, and adds a small Python LED daemon (`ugreen_leds/`) that
renders states published by other services. The original project's CLI tools,
helper scripts and packaging experiments are not included.

The branch layout is designed for a GitHub Pages Debian repository:

- `debian/` contains the Debian packaging metadata.
- `ugreen-dxp-leds.c`, `ugreen-dxp-leds.h`, and `Makefile` are the module
  source installed under `/usr/src`.
- `ugreen_leds/`, `bin/`, `data/` and `tests/` are the LED daemon, its entry
  points, default configuration and unit tests.
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
  `/sys/class/leds/ugreen:*`. It drives the network LED itself and, for the
  power and disk LEDs, shows the lowest-priority look that other services have
  published.

### Publishing LED looks

A service shows something on the power LED or a disk LED by writing a
*contribution*: one line in a file named after the LED.

```text
/run/ugreen-dxp-leds/<producer>/<led>
```

`<led>` is `power` or `disk1` through `disk8`; the network LED is driven by
the daemon. The line holds space-separated `key=value` fields:

```text
priority=35 color=#502800 effect=blink:500:500 state=DEGRADED
```

| Key | Required | Value |
| --- | --- | --- |
| `priority` | yes | 0–999. Across all producers the lowest wins; ties go to the producer name that sorts first. |
| `color` | yes | `#rrggbb`. `#000000` switches the LED off. |
| `effect` | no | `none` (default), `blink:ON:OFF` or `breath:ON:OFF`, in milliseconds. |
| `state` | no | A label shown by `ugreen-dxp-led status`. |

A file that does not parse is ignored and listed by `ugreen-dxp-led status`.
With no valid contribution the power LED shows `[power.NORMAL]` from the
configuration and a disk LED is off.

Give the publishing unit `RuntimeDirectory=ugreen-dxp-leds/<producer>`: systemd
creates the directory (exported as `$RUNTIME_DIRECTORY`) and deletes it when
the unit stops, so the unit's contributions disappear with it. Write
atomically (write `.<led>.tmp`, then rename it over `<led>`); delete the file
to withdraw it. Python producers can use `publish()` and `clear()` from
`ugreen_leds.tree` in `/usr/lib/ugreen-dxp-pve-leds`.

From a shell, use `ugreen-dxp-led`:

```sh
ugreen-dxp-led set power FAULT          # a [power.*] look from the configuration
ugreen-dxp-led set disk2 priority=5 color=#ff00ff effect=blink:100:100
ugreen-dxp-led clear disk2
ugreen-dxp-led status                   # what each LED shows, and who asked for it
```

`set` and `clear` write into `$RUNTIME_DIRECTORY` when run from a systemd unit,
else into `/run/ugreen-dxp-leds/manual`. The daemon writes what it shows to
`/run/ugreen-dxp-leds/.status`, which `ugreen-dxp-led status` reads.

### Configuring the LEDs

`/etc/ugreen-dxp-pve-leds.toml` holds the whole configuration; the reference
copy is `/usr/share/ugreen-dxp-pve-leds/ugreen-dxp-pve-leds.toml`. Apply changes
with `systemctl reload ugreen-dxp-pve-leds.service`:

```toml
[netdev]
device_name = "vmbr1"   # the network LED follows another interface
color = "#0040ff"

[power.NORMAL]
priority = 100
color = "#004010"       # the power LED while nothing is contributed
```

`[power.*]` names looks for the power LED; `NORMAL` is required, and
`ugreen-dxp-led set power NAME` publishes any of them. Disk LED looks belong to
the services that publish them, for example `/etc/ugreen-dxp-pve-truenas.toml`.

At shutdown and reboot every LED is set to the `[shutdown]` colour (white) and
switched off, so the panel is dark while the NAS is off and the controller's
own startup sequence shows white. By default the power LED is steady green and
the network LED follows `vmbr0` in blue.

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
sudo apt install build-essential debhelper dh-dkms dkms python3
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
