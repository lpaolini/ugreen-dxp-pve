# UGREEN DXP Proxmox-TrueNAS LED/fan helper

![](docs/assets/led-mixed-state-demo.gif)

Systemd services and Python helpers for driving UGREEN DXP fan PWM and
front-panel disk LEDs from a TrueNAS VM running under Proxmox.

This project is targeted at UGREEN DXP 4800 PRO users who run TrueNAS Scale as
a VM on a Proxmox host. It has only been tested on the DXP 4800 PRO. Other
UGREEN DXP models may work with minimal changes, but you should expect to
verify the fan PWM sysfs paths, LED sysfs paths, VM ID, and bay-to-disk mapping
before relying on it.

## How it works

The helpers run on the Proxmox host, not inside TrueNAS. They use the Proxmox
`qm guest exec` command to query a TrueNAS Scale VM through the QEMU guest agent,
then apply the result to the UGREEN hardware exposed on the Proxmox host.

There are two services:

- `ugreen-truenas-fan.service` polls disk temperatures from `sensors -j` inside
  the TrueNAS VM, reads the host CPU temperature from sysfs, chooses the higher
  PWM value from the configured disk and CPU fan curves, and writes that value to
  the UGREEN fan PWM sysfs path on the Proxmox host. If temperature collection
  fails, it uses the configured failsafe PWM. If the fan PWM path cannot be
  controlled, it publishes a blinking red look for the power LED until a later
  PWM write succeeds.
- `ugreen-truenas-zfs.service` polls `lsblk` and `zpool status -pj` inside the
  TrueNAS VM, maps the VM's disks back to the four physical UGREEN bays, and
  publishes a look for LEDs `disk1` through `disk4` to show ZFS
  health, spindown/standby state, missing disks, and resilvering for any pool
  associated with those bays.

Both services poll every 30 seconds. Their configuration lives in one file,
`/etc/ugreen-dxp-pve-truenas.toml` (reference copy:
`/usr/share/ugreen-dxp-pve-truenas/ugreen-dxp-pve-truenas.toml`):

- `vmid`: the Proxmox VM ID of your TrueNAS Scale VM. It is commented out on a
  fresh install; set it before the services can run. Without it they exit
  instead of guessing.
- `[fan]`: poll interval, the disk and CPU fan curves, PWM limits and the
  hwmon device. The fan helper finds the `/sys/class/hwmon/hwmon*` directory
  whose `name` matches `hwmon_regex` (an ITE chip exposed by the it87 driver,
  such as `it8613`) and uses `pwm_channel` to build the PWM, PWM enable and fan
  RPM paths. If that does not fit your system, pin `pwm_path`,
  `pwm_enable_path` and `input_path`.
- `[zfs]`: poll interval, `alert_threshold` and `bays`, the disk path inside
  TrueNAS of each front-panel bay.
- `[power.FAULT]` and `[disk.*]`: how each state looks on the LEDs (see the
  table below).

Upgrading from the two `.conf` files of earlier releases creates this file
from them; edited old files are kept as `*.conf.migrated` (dpkg deletes
unedited ones).

## Requirements

- Proxmox running directly on the UGREEN DXP host.
- TrueNAS Scale 25 or newer running as a Proxmox VM.
- `ugreen-dxp-pve-leds-dkms` from the same release installed on the Proxmox host; its
  `ugreen-dxp-pve-leds.service` shows the LED looks these services publish.
- `ugreen-dxp-pve-it87-dkms` installed on the Proxmox host, so fan PWM controls
  are exposed through hwmon sysfs.
- `hdparm` available inside the TrueNAS VM if you want the ZFS LED service to
  show spun-down/standby disks with the separate spindown breathing pattern. If it is not
  available, healthy disks remain shown as normal online disks.

## Preliminary step: install the UGREEN DKMS packages

Before installing this package directly, install the UGREEN DXP LED and it87
DKMS packages from [`lpaolini/ugreen-dxp`](https://github.com/lpaolini/ugreen-dxp)
on the Proxmox host. This is required because both services publish LED looks
to `ugreen-dxp-pve-leds.service`, and `ugreen-truenas-fan.service` writes to
the fan PWM sysfs controls exposed by the it87 package.

The DKMS packages are published in the unified signed Debian repository and as
direct `.deb` downloads:

```bash
cd /tmp
curl -LO https://lpaolini.github.io/ugreen-dxp-pve/downloads/ugreen-dxp-pve-leds-dkms_latest.deb
curl -LO https://lpaolini.github.io/ugreen-dxp-pve/downloads/ugreen-dxp-pve-it87-dkms_latest.deb
sudo apt install ./ugreen-dxp-pve-leds-dkms_latest.deb ./ugreen-dxp-pve-it87-dkms_latest.deb
```

Use a world-readable directory such as `/tmp` for direct `.deb` installs. If the
file is under `/root`, `apt` may print a harmless `_apt` sandbox warning because
the `_apt` user cannot read files in `/root`.

## Install from signed Debian repository (provided by GitHub Pages)

On the Proxmox host, install the repository signing key, add the signed apt
repository, and install this package:

```bash
sudo install -d -m 0755 /etc/apt/keyrings

curl -fsSL https://lpaolini.github.io/ugreen-dxp-pve/public.key | sudo gpg --dearmor -o /etc/apt/keyrings/ugreen-dxp-pve.gpg

echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] https://lpaolini.github.io/ugreen-dxp-pve stable main" | sudo tee /etc/apt/sources.list.d/ugreen-dxp-pve.list

sudo apt update
sudo apt install ugreen-dxp-pve-truenas
```

Or download and install the latest `.deb` directly:

```bash
cd /tmp
curl -LO https://lpaolini.github.io/ugreen-dxp-pve/downloads/ugreen-dxp-pve-truenas_latest.deb
sudo apt install ./ugreen-dxp-pve-truenas_latest.deb
```

The package installs the helpers to `/usr/bin`, the systemd units to
`/lib/systemd/system`, creates `/etc/ugreen-dxp-pve-truenas.toml` if it does
not exist, reloads systemd, and enables the fan and ZFS services. An existing
`/etc/ugreen-dxp-pve-truenas.toml` is never overwritten. The services will not
run successfully until `vmid` is configured.

So, after installing, set `vmid` to the Proxmox VM ID of your TrueNAS Scale VM:

```bash
sudo nano /etc/ugreen-dxp-pve-truenas.toml
```

```toml
vmid = 100
```

Then restart the services so they read the updated configuration:

```bash
sudo systemctl restart ugreen-truenas-fan.service ugreen-truenas-zfs.service
```

To inspect the host fan-control paths manually:

```bash
for h in /sys/class/hwmon/hwmon*; do
  printf '%s: %s\n' "$h" "$(cat "$h/name")"
done

ls -l /sys/class/hwmon/hwmon*/pwm* /sys/class/hwmon/hwmon*/fan*_input
```

If the service logs `Permission denied` for a `pwmN` path, first verify that
the selected hwmon directory is the ITE controller, for example `it8613`, and
that the matching `pwmN_enable` file exists. Try a different `pwm_channel`,
usually `2` or `3`, before pinning absolute `hwmonN` paths.

## LED showcase

The ZFS service publishes one look per bay to `ugreen-dxp-pve-leds.service`,
which shows the lowest priority look any service published for an LED. The
looks are defined in `[disk.*]` of `/etc/ugreen-dxp-pve-truenas.toml`:

|  | State | Description | Priority | Color | Effect |
| --- | --- | --- | --- | --- | --- |
| <img src="docs/assets/led-states/faulted.gif" alt="FAULTED LED" width="36"> | `FAULTED` | ZFS reports a failed leaf vdev | 10 | `#500000` | `blink:500:500` |
| <img src="docs/assets/led-states/error.gif" alt="ERROR LED" width="36"> | `ERROR` | The service could not query or parse the TrueNAS VM status | 15 | `#500000` | `none` |
| <img src="docs/assets/led-states/unavail.gif" alt="UNAVAIL LED" width="36"> | `UNAVAIL` | ZFS reports an unavailable leaf vdev | 20 | `#500000` | `blink:500:500` |
| <img src="docs/assets/led-states/removed.gif" alt="REMOVED LED" width="36"> | `REMOVED` | ZFS reports a removed leaf vdev | 25 | `#500000` | `blink:500:500` |
| <img src="docs/assets/led-states/missing.gif" alt="MISSING LED" width="36"> | `MISSING` | A configured pool leaf is not present in any mapped bay | 30 | `#280028` | `blink:500:500` |
| <img src="docs/assets/led-states/degraded.gif" alt="DEGRADED LED" width="36"> | `DEGRADED` | ZFS reports a degraded leaf vdev | 35 | `#502800` | `blink:500:500` |
| <img src="docs/assets/led-states/resilver.gif" alt="RESILVER LED" width="36"> | `RESILVER` | Any associated pool is resilvering | 40 | `#505050` | `blink:500:500` |
| <img src="docs/assets/led-states/offline.gif" alt="OFFLINE LED" width="36"> | `OFFLINE` | ZFS reports an offline leaf vdev | 45 | `#500000` | `blink:500:500` |
| <img src="docs/assets/led-states/checking.gif" alt="CHECKING LED" width="36"> | `CHECKING` | Querying the TrueNAS VM; the bay keeps its colour | 50 | unchanged | `blink:100:100` |
| <img src="docs/assets/led-states/online-alert.gif" alt="ONLINE_ALERT LED" width="36"> | `ONLINE_ALERT` | Healthy disk in a pool at or above `alert_threshold` | 60 | `#002800` | `blink:500:500` |
| <img src="docs/assets/led-states/spindown.gif" alt="SPINDOWN LED" width="36"> | `SPINDOWN` | Healthy disk in standby/spindown | 65 | `#002800` | `breath:2000:0` |
| <img src="docs/assets/led-states/online.gif" alt="ONLINE LED" width="36"> | `ONLINE` | Healthy online disk | 70 | `#002800` | `none` |
| <img src="docs/assets/led-states/off.gif" alt="OFF LED" width="36"> | `OFF` | Empty bay | 90 | `#000000` | `none` |

The fan service publishes `[power.FAULT]` (blinking red) to the power LED while
it cannot control the fan.

## Build locally

```bash
./packaging/build-deb.sh 0.1.0 dist
```

The resulting package will be written to:

```text
dist/ugreen-dxp-pve-truenas_0.1.0_all.deb
```

## Build a test package on GitHub

For development builds, run the `Build test deb` workflow manually from the
Actions tab. Set `version` to a prerelease Debian version that the final release
will upgrade over, for example:

```text
0.2.0~test20260611
0.2.0~rc1
```

Download the workflow artifact and install the `.deb` directly on the Proxmox
host:

```bash
cd /tmp
sudo apt install ./ugreen-dxp-pve-truenas_0.2.0~test20260611_all.deb
```

The test workflow only uploads an artifact. It does not publish anything to the
apt repository.

## Forking this project

If you fork this repository and want to publish your own apt repository, set up
GitHub Pages, recreate the signing secrets, and publish releases with `v*` tags.

1. Enable GitHub Pages for the fork:

   - Open `Settings -> Pages`.
   - Set the build/deploy source to GitHub Actions.

2. Create a signing key for your fork:

   ```bash
   gpg --quick-generate-key "ugreen-dxp apt <you@example.com>" ed25519 sign 2y
   gpg --list-secret-keys --keyid-format=long
   gpg --armor --export-secret-keys <fingerprint>
   ```

3. Add repository secrets in `Settings -> Secrets and variables -> Actions`:

   - `APT_SIGNING_KEY`: paste the full ASCII-armored private key exported above.
   - `APT_SIGNING_PASSPHRASE`: set this only if the signing key has a passphrase.

4. Check the GitHub Pages environment:

   - Open `Settings -> Environments -> github-pages`.
   - If deployments from tags are restricted, allow tags matching `v*`.

5. Publish a stable version:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

   The `Publish Debian repository` workflow builds the Debian package, creates and
   signs the apt repository metadata, publishes the public signing key, and
   deploys everything to:

   ```text
   https://<github-user-or-org>.github.io/<repository-name>/
   ```

6. Build a test package without publishing it:

   - Open `Actions -> Build test deb -> Run workflow`.
   - Set `version` to a prerelease version lower than the final release, for
     example `0.2.0~test20260611` or `0.2.0~rc1`.
   - Download the `.deb` artifact and install it manually.

After the first successful deployment, use your fork's GitHub Pages URL in the
apt setup commands instead of `https://lpaolini.github.io/ugreen-dxp-pve`.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
