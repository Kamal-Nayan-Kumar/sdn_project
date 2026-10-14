#!/usr/bin/env bash
#
# Clone the vendored Ryu and apply the lab's local patches.
#
# `network-lab/controller/ryu-src` is gitignored (it is a third-party checkout,
# not project source), so the patches that make it interoperate with
# `of13switch.py` live in `network-lab/patches/` and are applied here.
# Re-run this after deleting the checkout, or after pulling a new Ryu release.
#
# Usage:  bash scripts/setup-ryu.sh [--force]
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RYU_REPO="${RYU_REPO:-https://github.com/faucetsdn/ryu.git}"
RYU_BRANCH="${RYU_BRANCH:-master}"
RYU_SRC="${PROJECT_ROOT}/network-lab/controller/ryu-src"
PATCH_DIR="${PROJECT_ROOT}/network-lab/patches"

if [ "${1:-}" = "--force" ]; then
  echo "==> removing ${RYU_SRC}"
  rm -rf "${RYU_SRC}"
fi

if [ -d "${RYU_SRC}/.git" ]; then
  echo "==> ryu already present at ${RYU_SRC}"
  echo "    (use --force to re-clone)"
else
  echo "==> cloning ${RYU_REPO} @ ${RYU_BRANCH}"
  rm -rf "${RYU_SRC}"
  git clone --depth 1 --branch "${RYU_BRANCH}" "${RYU_REPO}" "${RYU_SRC}"
fi

# The lab runs Ryu from the source tree (ryu/cmd/manager.py), so the package
# only has to exist -- it is put on sys.path via the .pth file below.
echo "==> applying local patches from ${PATCH_DIR}"
shopt -s nullglob
patches=("${PATCH_DIR}"/*.patch)
if [ ${#patches[@]} -eq 0 ]; then
  echo "    (none found)"
else
  for patch in "${patches[@]}"; do
    echo "    - $(basename "$patch")"
    if git -C "${RYU_SRC}" apply --check "${patch}" 2>/dev/null; then
      git -C "${RYU_SRC}" apply "${patch}"
      echo "      applied"
    elif git -C "${RYU_SRC}" apply --reverse --check "${patch}" 2>/dev/null; then
      echo "      already applied"
    else
      echo "      FAILED to apply cleanly -- resolve by hand" >&2
      exit 1
    fi
  done
fi

# Make `import ryu` resolve to the checkout without a pip install.
SITE_PACKAGES="$("${PROJECT_ROOT}/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
PTH="${SITE_PACKAGES}/_vendored_ryu.pth"
mkdir -p "${SITE_PACKAGES}"
printf '%s\n' "${RYU_SRC}" > "${PTH}"
echo "==> registered ${PTH}"

echo "==> done. Verify with:"
echo "      ${PROJECT_ROOT}/.venv/bin/python -c 'import ryu; print(ryu.__file__)'"