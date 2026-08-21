#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${1:-"${ROOT}/dist"}"
REPO_ROOT="${2:-"${ROOT}/public"}"
BASE_URL="${3:-https://lpaolini.github.io/ugreen-dxp-pve}"
SUITE="${SUITE:-stable}"
COMPONENT="${COMPONENT:-main}"
ARCHITECTURES="${ARCHITECTURES:-amd64 arm64 all}"

if ! compgen -G "${DIST_DIR}/*.deb" >/dev/null; then
  echo "No .deb files found in ${DIST_DIR}" >&2
  exit 1
fi

rm -rf "${REPO_ROOT}"
mkdir -p "${REPO_ROOT}/pool/${COMPONENT}/u/ugreen-dxp" "${REPO_ROOT}/downloads"

for deb in "${DIST_DIR}"/*.deb; do
  package_name="$(dpkg-deb -f "${deb}" Package)"
  cp "${deb}" "${REPO_ROOT}/pool/${COMPONENT}/u/ugreen-dxp/"
  cp "${deb}" "${REPO_ROOT}/downloads/${package_name}_latest.deb"
done

for arch in ${ARCHITECTURES}; do
  binary_dir="${REPO_ROOT}/dists/${SUITE}/${COMPONENT}/binary-${arch}"
  mkdir -p "${binary_dir}"
  (
    cd "${REPO_ROOT}"
    apt-ftparchive packages "pool/${COMPONENT}/u/ugreen-dxp"
  ) > "${binary_dir}/Packages"
  gzip -9c "${binary_dir}/Packages" > "${binary_dir}/Packages.gz"
done

cat > "${REPO_ROOT}/apt-release.conf" <<EOF
APT::FTPArchive::Release {
  Origin "ugreen-dxp-pve";
  Label "UGREEN DXP PVE";
  Suite "${SUITE}";
  Codename "${SUITE}";
  Architectures "${ARCHITECTURES}";
  Components "${COMPONENT}";
  Description "UGREEN DXP Proxmox VE DKMS and TrueNAS helper packages";
};
EOF

apt-ftparchive -c "${REPO_ROOT}/apt-release.conf" release "${REPO_ROOT}/dists/${SUITE}" \
  > "${REPO_ROOT}/dists/${SUITE}/Release"
rm "${REPO_ROOT}/apt-release.conf"

if [[ -n "${APT_SIGNING_KEY:-}" ]]; then
  GNUPGHOME="$(mktemp -d)"
  export GNUPGHOME
  trap 'rm -rf "${GNUPGHOME}"' EXIT
  chmod 700 "${GNUPGHOME}"
  printf 'allow-loopback-pinentry\n' > "${GNUPGHOME}/gpg-agent.conf"
  chmod 600 "${GNUPGHOME}/gpg-agent.conf"
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

  sign_args=(
    --batch
    --yes
    --no-tty
    --pinentry-mode loopback
    --local-user "${fingerprint}"
  )
  if [[ -n "${APT_SIGNING_PASSPHRASE:-}" ]]; then
    sign_args+=(--passphrase "${APT_SIGNING_PASSPHRASE}")
  else
    sign_args+=(--passphrase "")
  fi

  gpg "${sign_args[@]}" --clearsign \
    --output "${REPO_ROOT}/dists/${SUITE}/InRelease" \
    "${REPO_ROOT}/dists/${SUITE}/Release"

  gpg "${sign_args[@]}" --armor --detach-sign \
    --output "${REPO_ROOT}/dists/${SUITE}/Release.gpg" \
    "${REPO_ROOT}/dists/${SUITE}/Release"
elif [[ "${REQUIRE_APT_SIGNING:-0}" = "1" ]]; then
  echo "APT_SIGNING_KEY is required when REQUIRE_APT_SIGNING=1" >&2
  exit 1
fi

cat > "${REPO_ROOT}/index.html" <<EOF
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
echo "deb [signed-by=/etc/apt/keyrings/ugreen-dxp-pve.gpg] ${BASE_URL} ${SUITE} ${COMPONENT}" | sudo tee /etc/apt/sources.list.d/ugreen-dxp-pve.list
sudo apt update
sudo apt install ugreen-dxp-pve-truenas</pre>
  <ul>
    <li><a href="downloads/ugreen-dxp-pve-leds-dkms_latest.deb">ugreen-dxp-pve-leds-dkms_latest.deb</a></li>
    <li><a href="downloads/ugreen-dxp-pve-it87-dkms_latest.deb">ugreen-dxp-pve-it87-dkms_latest.deb</a></li>
    <li><a href="downloads/ugreen-dxp-pve-truenas_latest.deb">ugreen-dxp-pve-truenas_latest.deb</a></li>
  </ul>
</body>
</html>
EOF

touch "${REPO_ROOT}/.nojekyll"
printf 'Built APT repository in %s\n' "${REPO_ROOT}"
