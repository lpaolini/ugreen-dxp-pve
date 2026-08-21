# Licensing

This repository combines independently licensed components. The package-specific
license metadata is authoritative for each Debian package.

## Package Summary

- `ugreen-dxp-pve-leds-dkms`
  - `ugreen-dxp-leds.c` and `ugreen-dxp-leds.h`: GPL-2.0-only.
  - Debian packaging, service units, helper scripts, and documentation: MIT.
  - Upstream project license text: `packages/leds-dkms/LICENSE`.
  - GPL-2 license text: `packages/leds-dkms/COPYING`.
  - Debian metadata: `packages/leds-dkms/debian/copyright`.
- `ugreen-dxp-pve-it87-dkms`
  - Driver source and retained upstream files: GPL-2.0-or-later.
  - Debian packaging: GPL-2.0-or-later.
  - GPL-2 license text: `packages/it87-dkms/COPYING`.
  - Debian metadata: `packages/it87-dkms/debian/copyright`.
- `ugreen-dxp-pve-truenas`
  - Proxmox/TrueNAS helper scripts, service units, packaging, and
    documentation: MIT.
  - License text: `packages/truenas/LICENSE`.

## Upstream Credits

This work builds on:

- `frankcrawford/it87`: https://github.com/frankcrawford/it87
- `miskcoo/ugreen_leds_controller`: https://github.com/miskcoo/ugreen_leds_controller
