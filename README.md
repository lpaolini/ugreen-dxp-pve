# UGREEN DXP PVE Debian Repository

![UGREEN DXP LED state demo](packages/truenas/docs/assets/led-mixed-state-demo.gif)

This repository packages the UGREEN DXP Proxmox VE support stack as a signed
Debian/APT repository for UGREEN NAS systems running Proxmox VE.

It builds and publishes:

- `ugreen-dxp-pve-leds-dkms`: DKMS package for the `led-ugreen` kernel module.
  It exposes `/sys/class/leds/ugreen:white:*` class devices for the power,
  network, and disk LEDs.
- `ugreen-dxp-pve-it87-dkms`: DKMS package for the `it87` hwmon kernel module.
  It exposes the CPU and main fan class devices used by the fan-control helper.
- `ugreen-dxp-pve-truenas`: Proxmox host services for fan control and
  front-panel LED status when TrueNAS is running as a Proxmox VM with SATA
  controller passthrough.

Installing `ugreen-dxp-pve-truenas` pulls in both DKMS packages.

This work builds on the original packages by
[frankcrawford/it87](https://github.com/frankcrawford/it87) and
[miskcoo/ugreen_leds_controller](https://github.com/miskcoo/ugreen_leds_controller).

## How It Works

The DKMS packages expose the UGREEN hardware through normal Linux sysfs
interfaces on the Proxmox host. The LED package binds the onboard I2C LED
controller at `0x3a`, loads the standard LED triggers, and initializes the
power, network, and disk LEDs from `/etc/ugreen-dxp-pve-leds.conf`.

The TrueNAS helper services also run on the Proxmox host. They query a TrueNAS
Scale VM through `qm guest exec` and the QEMU guest agent, then apply the VM
state to host hardware:

- `ugreen-truenas-fan.service` reads disk temperatures from `sensors -j` inside
  TrueNAS, combines them with the host CPU temperature, and writes the selected
  PWM value to the UGREEN fan sysfs path.
- `ugreen-truenas-zfs.service` reads `lsblk`, `zpool status -pj`, and optional
  disk standby data, maps VM disks back to physical UGREEN bays, and drives
  `/sys/class/leds/ugreen:white:disk1` through `disk4`.

The TrueNAS package is aimed at Proxmox hosts running TrueNAS Scale as a VM with
SATA controller passthrough. It has been tested on a DXP 4800 PRO; other DXP
models may need adjusted fan paths, LED paths, VM ID, or bay mappings.

## LED Showcase

The ZFS helper uses color and blink patterns to make disk state visible on the
front panel:

|  | State | Meaning | Color | Effect |
| --- | --- | --- | --- | --- |
| <img src="packages/truenas/docs/assets/led-states/off.gif" alt="OFF LED" width="36"> | `OFF` | Empty bay or cleared LED | `0 0 0` | `none` |
| <img src="packages/truenas/docs/assets/led-states/checking.gif" alt="CHECKING LED" width="36"> | `CHECKING` | Querying TrueNAS; previous color is preserved | unchanged | `blink 100 100` |
| <img src="packages/truenas/docs/assets/led-states/online.gif" alt="ONLINE LED" width="36"> | `ONLINE` | Healthy online disk | `0 40 0` | `none` |
| <img src="packages/truenas/docs/assets/led-states/online-alert.gif" alt="ONLINE_ALERT LED" width="36"> | `ONLINE_ALERT` | Healthy pool at or above alert threshold | `0 40 0` | `blink 500 500` |
| <img src="packages/truenas/docs/assets/led-states/spindown.gif" alt="SPINDOWN LED" width="36"> | `SPINDOWN` | Healthy disk in standby/spindown | `0 20 60` | `none` |
| <img src="packages/truenas/docs/assets/led-states/degraded.gif" alt="DEGRADED LED" width="36"> | `DEGRADED` | ZFS reports a degraded leaf vdev | `80 40 0` | `blink 500 500` |
| <img src="packages/truenas/docs/assets/led-states/faulted.gif" alt="FAULTED LED" width="36"> | `FAULTED` / `UNAVAIL` / `REMOVED` / `OFFLINE` | ZFS reports a failed, unavailable, removed, or offline leaf vdev | `80 0 0` | `blink 500 500` |
| <img src="packages/truenas/docs/assets/led-states/resilver.gif" alt="RESILVER LED" width="36"> | `RESILVER` | An associated pool is resilvering | `80 80 80` | `blink 500 500` |
| <img src="packages/truenas/docs/assets/led-states/missing.gif" alt="MISSING LED" width="36"> | `MISSING` | A configured pool leaf is not present in any mapped bay | `40 0 40` | `blink 500 500` |
| <img src="packages/truenas/docs/assets/led-states/error.gif" alt="ERROR LED" width="36"> | `ERROR` | TrueNAS status could not be queried or parsed | `80 0 0` | `none` |

## Install From The APT Repository

After GitHub Pages is enabled and the publishing workflow has completed:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://lpaolini.github.io/ugreen-dxp/public.key | sudo gpg --dearmor -o /etc/apt/keyrings/ugreen-dxp.gpg
echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp.gpg] https://lpaolini.github.io/ugreen-dxp stable main" | sudo tee /etc/apt/sources.list.d/ugreen-dxp.list
sudo apt update
sudo apt install ugreen-dxp-pve-truenas
```

To install only the kernel modules:

```bash
sudo apt install ugreen-dxp-pve-leds-dkms ugreen-dxp-pve-it87-dkms
```

## Package Layout

```text
packages/
  leds-dkms/     UGREEN DXP LED DKMS package
  it87-dkms/     it87 hwmon DKMS package
  truenas/       Proxmox/TrueNAS helper package
packaging/
  build-all.sh         Build all .deb packages into dist/
  build-repository.sh  Build an APT repository from dist/
```

## Build Locally

Install Debian packaging tools:

```bash
sudo apt-get update
sudo apt-get install -y build-essential debhelper dh-dkms dkms dpkg-dev fakeroot apt-utils gnupg
```

Build all packages using one Debian version:

```bash
./packaging/build-all.sh 0.4.0 dist
```

Build an unsigned local APT repository:

```bash
./packaging/build-repository.sh dist public
```

The package files will be in `dist/`, and the APT repository tree will be in
`public/`.

## Publish

The `Build Debian packages` workflow runs on every `v*` tag push and uploads
the generated `.deb` files as GitHub Actions artifacts. It can also be run
manually with an explicit Debian version.

The `Publish Debian repository` workflow runs on pushes to `main`, `v*` tags,
and manual dispatch. It builds all three packages, creates signed APT metadata,
publishes the repository to GitHub Pages, and uploads `.deb` files to GitHub
Releases for tagged builds.

Create these repository secrets before publishing:

- `APT_SIGNING_KEY`: ASCII-armored private key used to sign the APT repository.
- `APT_SIGNING_PASSPHRASE`: optional passphrase for the private key.

For stable releases:

```bash
git tag v0.4.0
git push origin v0.4.0
```

## Upgrade Notes

The new packages include compatibility metadata for the previous package names:

- `ugreen-dxp-pve-leds-dkms` provides/conflicts/replaces
  `ugreen-dxp-leds-dkms`.
- `ugreen-dxp-pve-it87-dkms` provides/conflicts/replaces `it87-dkms`.
- `ugreen-dxp-pve-truenas` provides/conflicts/replaces
  `ugreen-dxp-proxmox-truenas`.

## License

This repository combines components with different licenses. See
[LICENSE.md](LICENSE.md) and each package's Debian copyright metadata for the
license breakdown.
