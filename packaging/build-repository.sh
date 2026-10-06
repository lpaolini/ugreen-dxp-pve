#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build a signed APT repository with one suite per release channel.
#
# Usage: build-repository.sh OUT_DIR BASE_URL SUITE=DEB_DIR [SUITE=DEB_DIR ...]
#   e.g. build-repository.sh public https://example.org stable=dist/stable dev=dist/dev
#
# The "stable" suite also provides downloads/<package>_latest.deb; other
# suites get downloads/<suite>/<package>_latest.deb.
set -euo pipefail

usage="usage: $0 OUT_DIR BASE_URL SUITE=DEB_DIR [SUITE=DEB_DIR ...]"
REPO_ROOT="${1:?${usage}}"
BASE_URL="${2:?${usage}}"
shift 2
[[ $# -gt 0 ]] || { echo "${usage}" >&2; exit 2; }
COMPONENT="${COMPONENT:-main}"
ARCHITECTURES="${ARCHITECTURES:-amd64 arm64 all}"

rm -rf "${REPO_ROOT}"
mkdir -p "${REPO_ROOT}"

sign_args=()
if [[ -n "${APT_SIGNING_KEY:-}" ]]; then
  GNUPGHOME="$(mktemp -d)"
  export GNUPGHOME
  trap 'rm -rf "${GNUPGHOME}"' EXIT
  chmod 700 "${GNUPGHOME}"
  printf 'allow-loopback-pinentry\n' > "${GNUPGHOME}/gpg-agent.conf"
  printf '%s' "${APT_SIGNING_KEY}" | gpg --batch --yes --no-tty --import

  fingerprint="$(
    gpg --batch --yes --no-tty --list-secret-keys --with-colons |
      awk -F: '/^fpr:/ {print $10; exit}'
  )"
  if [[ -z "${fingerprint}" ]]; then
    echo "No signing key fingerprint found after importing APT_SIGNING_KEY" >&2
    exit 1
  fi
  gpg --batch --yes --no-tty --armor --export "${fingerprint}" > "${REPO_ROOT}/public.key"
  sign_args=(--batch --yes --no-tty --pinentry-mode loopback --local-user "${fingerprint}"
             --passphrase "${APT_SIGNING_PASSPHRASE:-}")
elif [[ "${REQUIRE_APT_SIGNING:-0}" = "1" ]]; then
  echo "APT_SIGNING_KEY is required when REQUIRE_APT_SIGNING=1" >&2
  exit 1
fi

add_suite() {
  local suite="$1" deb_dir="$2"
  local pool="pool/${suite}/${COMPONENT}"
  local downloads="downloads"
  [[ "${suite}" = stable ]] || downloads="downloads/${suite}"

  if ! compgen -G "${deb_dir}/*.deb" >/dev/null; then
    echo "No .deb files for suite ${suite} in ${deb_dir}" >&2
    exit 1
  fi
  mkdir -p "${REPO_ROOT}/${pool}" "${REPO_ROOT}/${downloads}"
  for deb in "${deb_dir}"/*.deb; do
    cp "${deb}" "${REPO_ROOT}/${pool}/"
    cp "${deb}" "${REPO_ROOT}/${downloads}/$(dpkg-deb -f "${deb}" Package)_latest.deb"
  done

  local dist="${REPO_ROOT}/dists/${suite}"
  for arch in ${ARCHITECTURES}; do
    mkdir -p "${dist}/${COMPONENT}/binary-${arch}"
    (cd "${REPO_ROOT}" && apt-ftparchive packages "${pool}") \
      > "${dist}/${COMPONENT}/binary-${arch}/Packages"
    gzip -9c "${dist}/${COMPONENT}/binary-${arch}/Packages" \
      > "${dist}/${COMPONENT}/binary-${arch}/Packages.gz"
  done

  # Generate outside dists/ so the Release file never lists itself.
  apt-ftparchive \
    -o APT::FTPArchive::Release::Origin="ugreen-dxp-pve" \
    -o APT::FTPArchive::Release::Label="UGREEN DXP PVE" \
    -o APT::FTPArchive::Release::Suite="${suite}" \
    -o APT::FTPArchive::Release::Codename="${suite}" \
    -o APT::FTPArchive::Release::Architectures="${ARCHITECTURES}" \
    -o APT::FTPArchive::Release::Components="${COMPONENT}" \
    -o APT::FTPArchive::Release::Description="UGREEN DXP Proxmox VE packages (${suite} channel)" \
    release "${dist}" > "${REPO_ROOT}/Release.tmp"
  mv "${REPO_ROOT}/Release.tmp" "${dist}/Release"

  if [[ ${#sign_args[@]} -gt 0 ]]; then
    gpg "${sign_args[@]}" --clearsign --output "${dist}/InRelease" "${dist}/Release"
    gpg "${sign_args[@]}" --armor --detach-sign --output "${dist}/Release.gpg" "${dist}/Release"
  fi
}

suites=()
for spec in "$@"; do
  add_suite "${spec%%=*}" "${spec#*=}"
  suites+=("${spec%%=*}")
done

{
  cat <<EOF
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UGREEN DXP PVE Debian Repository</title>
</head>
<body>
  <h1>UGREEN DXP PVE Debian Repository</h1>
  <pre>sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL ${BASE_URL}/public.key | sudo gpg --dearmor -o /etc/apt/keyrings/ugreen-dxp-pve.gpg
echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] ${BASE_URL} stable ${COMPONENT}" | sudo tee /etc/apt/sources.list.d/ugreen-dxp-pve.list
sudo apt update
sudo apt install ugreen-dxp-pve-truenas</pre>
  <p>Channels: ${suites[*]}. To test development builds, add a second line with
  <code>dev</code> instead of <code>stable</code>.</p>
EOF
  for suite in "${suites[@]}"; do
    downloads="downloads"
    [[ "${suite}" = stable ]] || downloads="downloads/${suite}"
    echo "  <h2>${suite}</h2>"
    echo "  <ul>"
    for deb in "${REPO_ROOT}/${downloads}"/*_latest.deb; do
      echo "    <li><a href=\"${downloads}/${deb##*/}\">${deb##*/}</a></li>"
    done
    echo "  </ul>"
  done
  echo "</body>"
  echo "</html>"
} > "${REPO_ROOT}/index.html"

touch "${REPO_ROOT}/.nojekyll"
printf 'Built APT repository with suites %s in %s\n' "${suites[*]}" "${REPO_ROOT}"
