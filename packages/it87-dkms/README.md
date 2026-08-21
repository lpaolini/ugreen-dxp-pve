# UGREEN DXP PVE it87 DKMS

This package wraps the out-of-tree `it87` hwmon driver as
`ugreen-dxp-pve-it87-dkms` for UGREEN DXP systems running Proxmox VE.

The Debian package installs only the files needed by DKMS:

- `it87.c`
- `compat.h`
- `Makefile`
- `debian/ugreen-dxp-pve-it87-dkms.dkms`

The full upstream research archive, non-UGREEN sensor examples, patch snapshots,
and manual DKMS helper scripts are intentionally not carried in this monorepo.

## UGREEN Sensor Config

The retained UGREEN-specific lm-sensors example is:

```text
sensors/UGreen-DXP6800-Pro.conf
```

It is kept as reference material and is not installed automatically by the DKMS
package.

## Build

From this package directory:

```bash
dpkg-buildpackage -b -us -uc -tc
```

From the monorepo root, build all packages with:

```bash
./packaging/build-all.sh 0.4.0 dist
```

## Upstream

The driver source is based on the `it87` project. See
`debian/copyright` for provenance and licensing.

## License

The retained `it87` driver files are distributed under `GPL-2.0-or-later`,
matching the SPDX metadata in `it87.c`. The GPL-2 text is included in
`COPYING`, and `debian/copyright` records the file-level attribution.
