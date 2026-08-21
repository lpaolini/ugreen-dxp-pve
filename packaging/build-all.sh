#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"
OUT_DIR="${2:-"${ROOT}/dist"}"

if [[ -z "${VERSION}" ]]; then
  if git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    VERSION="$(git -C "${ROOT}" describe --tags --always --dirty 2>/dev/null || true)"
  fi
  VERSION="${VERSION:-0.1.0}"
fi

VERSION="${VERSION#v}"

if ! dpkg --compare-versions "${VERSION}" gt 0; then
  echo "Invalid Debian package version: ${VERSION}" >&2
  exit 1
fi

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "${BUILD_DIR}"' EXIT

mkdir -p "${OUT_DIR}"

set_changelog_version() {
  local package_dir="$1"
  local source_name="$2"

  SOURCE_NAME="${source_name}" PACKAGE_VERSION="${VERSION}" perl -0pi -e \
    's/\A(\Q$ENV{SOURCE_NAME}\E \()[^)]*(\))/$1$ENV{PACKAGE_VERSION}$2/' \
    "${package_dir}/debian/changelog"
}

build_dh_package() {
  local source_rel="$1"
  local source_name="$2"
  local package_dir="${BUILD_DIR}/${source_name}"

  cp -a "${ROOT}/${source_rel}" "${package_dir}"
  set_changelog_version "${package_dir}" "${source_name}"

  (
    cd "${package_dir}"
    dpkg-buildpackage -b -us -uc -tc
  )

  mv "${BUILD_DIR}/${source_name}_${VERSION}_all.deb" "${OUT_DIR}/"
}

build_dh_package "packages/leds-dkms" "ugreen-dxp-pve-leds-dkms"
build_dh_package "packages/it87-dkms" "ugreen-dxp-pve-it87-dkms"

"${ROOT}/packages/truenas/packaging/build-deb.sh" "${VERSION}" "${OUT_DIR}"

printf 'Built packages in %s\n' "${OUT_DIR}"
