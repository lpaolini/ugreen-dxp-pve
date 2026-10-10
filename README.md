# UGREEN DXP PVE Debian Repository

![UGREEN DXP LED state demo](packages/truenas/docs/assets/led-mixed-state-demo.gif)

## Scenario
You own a UGREEN DXP-series NAS running a custom stack:

- Proxmox VE as OS
- TrueNAS v25.10 as a VM, with full SATA passthrough (for performance reasons)

This repository packages a signed Debian/APT repository providing support for controlling LEDs on the front panel and the main fan.

- Power and network LEDs are controlled directly by Proxmox VE
- Disk LEDs are controlled by Proxmox VE by querying TrueNAS for ZFS disk status
- Main fan is controlled by Proxmox VE by querying TrueNAS for the highest disk temperature.

Tested on UGREEN DXP-4800 PRO, but should work with minimal/no changes on other devices of the same family.

### Debian packages

- `ugreen-dxp-pve-leds-dkms`

  DKMS package for the `led-ugreen` kernel module.
  It exposes `/sys/class/leds/ugreen:white:*` class devices for the power,
  network, and disk LEDs.

  Derived from [miskcoo/ugreen_leds_controller](https://github.com/miskcoo/ugreen_leds_controller/tree/v0.3) v0.3.

- `ugreen-dxp-pve-it87-dkms`

  DKMS package for the `it87` hwmon kernel module.
  It exposes the CPU and main fan class devices used by the fan-control helper.

  Derived from [frankcrawford/it87](https://github.com/frankcrawford/it87).

- `ugreen-dxp-pve-truenas`

  Proxmox host services for fan control and front-panel LED status when TrueNAS is running as a Proxmox VM with SATA controller passthrough.

  Note: disk LEDs are used to show ZFS disk status, not disk activity.

## How It Works

The DKMS packages expose the UGREEN hardware through normal Linux sysfs
interfaces on the Proxmox host. The LED package binds the onboard I2C LED
controller at `0x3a`, loads the standard LED triggers, and runs
`ugreen-dxp-pve-leds.service`, the only writer to the LEDs: other services
publish looks (priority, colour, effect) under `/run/ugreen-dxp-leds/` and the
daemon shows the lowest priority one on each LED. It drives the network LED
itself; its settings live in `/etc/ugreen-dxp-pve-leds.toml`.
Both DKMS packages load their kernel modules after installation and configure
systemd modules-load entries so they are loaded again on reboot.

The TrueNAS helper services also run on the Proxmox host. They query a TrueNAS
Scale VM through `qm guest exec` and the QEMU guest agent, then apply the VM
state to host hardware:

- `ugreen-truenas-fan.service` reads disk temperatures from `sensors -j` inside
  TrueNAS, combines them with the host CPU temperature, and writes the selected
  PWM value to the UGREEN fan sysfs path. If the fan PWM write fails, it
  publishes a blinking red look for the power LED until a later write succeeds.
- `ugreen-truenas-zfs.service` reads `lsblk`, `zpool status -pj`, and optional
  disk standby data, maps VM disks back to physical UGREEN bays, and publishes
  a look for LEDs `disk1` through `disk4`, as defined in `/etc/ugreen-dxp-pve-truenas.toml`.

The TrueNAS package is aimed at Proxmox hosts running TrueNAS Scale as a VM with
SATA controller passthrough. It has been tested on a DXP 4800 PRO; other DXP
models may need adjusted fan paths, LED paths, VM ID, or bay mappings.

## LED Showcase

The ZFS helper uses color and blink patterns to make disk state visible on the
front panel:

|  | State | Meaning | Color | Effect |
| --- | --- | --- | --- | --- |
| <img src="packages/truenas/docs/assets/led-states/off.gif" alt="OFF LED" width="36"> | `OFF` | Empty bay or cleared LED | `#000000` | `none` |
| <img src="packages/truenas/docs/assets/led-states/checking.gif" alt="CHECKING LED" width="36"> | `CHECKING` | Querying TrueNAS; previous color is preserved | unchanged | `blink:100:100` |
| <img src="packages/truenas/docs/assets/led-states/online.gif" alt="ONLINE LED" width="36"> | `ONLINE` | Healthy online disk | `#002800` | `none` |
| <img src="packages/truenas/docs/assets/led-states/online-alert.gif" alt="ONLINE_ALERT LED" width="36"> | `ONLINE_ALERT` | Healthy pool at or above alert threshold | `#002800` | `blink:500:500` |
| <img src="packages/truenas/docs/assets/led-states/spindown.gif" alt="SPINDOWN LED" width="36"> | `SPINDOWN` | Healthy disk in standby/spindown | `#002800` | `breath:2000:0` |
| <img src="packages/truenas/docs/assets/led-states/degraded.gif" alt="DEGRADED LED" width="36"> | `DEGRADED` | ZFS reports a degraded leaf vdev | `#502800` | `blink:500:500` |
| <img src="packages/truenas/docs/assets/led-states/faulted.gif" alt="FAULTED LED" width="36"> | `FAULTED` / `UNAVAIL` / `REMOVED` / `OFFLINE` | ZFS reports a failed, unavailable, removed, or offline leaf vdev | `#500000` | `blink:500:500` |
| <img src="packages/truenas/docs/assets/led-states/resilver.gif" alt="RESILVER LED" width="36"> | `RESILVER` | An associated pool is resilvering | `#505050` | `blink:500:500` |
| <img src="packages/truenas/docs/assets/led-states/missing.gif" alt="MISSING LED" width="36"> | `MISSING` | A configured pool leaf is not present in any mapped bay | `#280028` | `blink:500:500` |
| <img src="packages/truenas/docs/assets/led-states/error.gif" alt="ERROR LED" width="36"> | `ERROR` | TrueNAS status could not be queried or parsed | `#500000` | `none` |

## Install From The APT Repository

Enter Proxmox VE shell as root, and install the Debian repository:

```bash
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://lpaolini.github.io/ugreen-dxp-pve/public.key | gpg --dearmor -o /etc/apt/keyrings/ugreen-dxp-pve.gpg
echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] https://lpaolini.github.io/ugreen-dxp-pve stable main" | tee /etc/apt/sources.list.d/ugreen-dxp-pve.list
apt update
```

To install the kernel modules and the support services for TrueNAS:

```bash
apt install ugreen-dxp-pve-truenas
```

To install only the kernel modules:

```bash
apt install ugreen-dxp-pve-leds-dkms ugreen-dxp-pve-it87-dkms
```

### Testing development builds

Every commit to the `dev` branch is published to a separate `dev` channel of the
same repository. To try it on a test host, add a second source line:

```bash
echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] https://lpaolini.github.io/ugreen-dxp-pve dev main" | tee /etc/apt/sources.list.d/ugreen-dxp-pve-dev.list
apt update && apt upgrade
```

Development versions look like `0.9.10+dev5.gabc1234` (5 commits after
`v0.9.10`): newer than the last release, older than the next one. To go back to
stable, delete `ugreen-dxp-pve-dev.list`, run `apt update`, and reinstall the
stable versions explicitly (apt never downgrades on its own), for example
`apt install ugreen-dxp-pve-truenas=0.9.10 ugreen-dxp-pve-leds-dkms=0.9.10 ugreen-dxp-pve-it87-dkms=0.9.10`.
The stable 0.9.10 package reads the old `.conf` files again, so restore your
settings from the `*.conf.migrated` files (or set `VMID` again). The old LED settings
are in `/etc/ugreen-dxp-pve-leds.conf.dpkg-bak`.

### Configuration files

- `/etc/ugreen-dxp-pve-leds.toml` (LED daemon: network LED, power LED looks)
- `/etc/ugreen-dxp-pve-truenas.toml` (`vmid` needs to be set; fan, ZFS and disk LED looks)

After setting `vmid` in `/etc/ugreen-dxp-pve-truenas.toml`, restart the services:

```bash
systemctl restart ugreen-truenas-zfs.service ugreen-truenas-fan.service
```

Then, check the service status:

```bash
systemctl status ugreen-truenas-zfs.service
systemctl status ugreen-truenas-fan.service
```

## Package Layout

```text
packages/
  leds-dkms/     UGREEN DXP LED DKMS package
  it87-dkms/     it87 hwmon DKMS package
  truenas/       Proxmox/TrueNAS helper package
packaging/
  build-all.sh         Build all .deb packages into dist/
  build-repository.sh  Build an APT repository with one suite per channel
  version.sh           Derive the package version from git tags
```

## Build Locally

Install Debian packaging tools:

```bash
apt-get update
apt-get install -y build-essential debhelper dh-dkms dkms dpkg-dev fakeroot apt-utils gnupg
```

Build all packages; the version defaults to the one derived from git
(`packaging/version.sh`), or pass one explicitly:

```bash
./packaging/build-all.sh "" dist
./packaging/build-all.sh 0.10.0 dist
```

Build an unsigned local APT repository with a `stable` suite:

```bash
./packaging/build-repository.sh public http://localhost:8000 stable=dist
```

The package files will be in `dist/`, and the APT repository tree will be in
`public/`.

## Forking

The `Debian packages` workflow (`.github/workflows/debian.yml`) builds, tests
and lints all packages on every push and pull request. It publishes only from
two places:

| Trigger | Version | GitHub release | APT suite |
| --- | --- | --- | --- |
| Push to `dev` | `X.Y.Z+devN.g<sha>` | rolling `dev` pre-release, replaced on every push | `dev` |
| Tag `vX.Y.Z` on `main` | `X.Y.Z` | `vX.Y.Z` | `stable` |

Every deployment rebuilds the whole GitHub Pages site from the latest stable
release and the `dev` pre-release, so publishing one channel never drops the
other. Running the workflow manually rebuilds the site without publishing
anything new.

Before the publish workflow can create the signed APT repository, configure
GitHub Pages and add the signing key secrets:

- `APT_SIGNING_KEY`: ASCII-armored private key used to sign the APT repository.
- `APT_SIGNING_PASSPHRASE`: optional passphrase for the private key.

In GitHub, open `Settings -> Pages` and set `Build and deployment -> Source` to
`GitHub Actions`. Then open `Settings -> Environments -> github-pages` and make
sure `Deployment branches and tags` allows the `dev` branch and `v*` tags.

Create a signing key locally:

```bash
gpg --quick-generate-key "ugreen-dxp-pve apt <you@example.com>" ed25519 sign 2y
gpg --list-secret-keys --keyid-format=long
gpg --armor --export-secret-keys <fingerprint>
```

Paste the exported private key into `Settings -> Secrets and variables ->
Actions -> New repository secret` as `APT_SIGNING_KEY`. If the key has a
passphrase, add it as `APT_SIGNING_PASSPHRASE`.

Day to day, merge work into `dev` and test it from the `dev` channel. To
release a tested `dev` commit as stable:

```bash
git switch main
git merge --ff-only dev
git tag v0.10.0
git push origin main v0.10.0
```

The workflow refuses release tags that are not on `main`.

After the workflow completes, the repository is available at:

```text
https://<you>.github.io/ugreen-dxp-pve
```

## License

This repository combines components with different licenses.

See [LICENSE.md](LICENSE.md) and each package's Debian copyright metadata for the
license breakdown.

## Credits

This project builds on earlier work by @miskcoo and @frankcrawford.

Many thanks to the original authors!
