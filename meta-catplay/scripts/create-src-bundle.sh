#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <git-repository-root>" >&2
    exit 2
fi

repo_root="$1"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
layer_dir="$(cd "${script_dir}/.." && pwd)"
bundle_dir="${layer_dir}/recipes-apps/files"
module_path="usb/catplay_iap2_usb_host/g_iphone"

if [ ! -e "${repo_root}/.git" ]; then
    echo "Not a git repository root: ${repo_root}" >&2
    exit 2
fi
if [ ! -f "${repo_root}/${module_path}/Makefile" ]; then
    echo "Missing g_iphone sources: ${repo_root}/${module_path}" >&2
    exit 2
fi

mkdir -p "${bundle_dir}"
staging_dir=$(mktemp -d "${bundle_dir}/.src-bundle.XXXXXX")
trap 'rm -rf "${staging_dir}"' EXIT
export LC_ALL=C

tar_options=(
    --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner
    --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime
)

tar "${tar_options[@]}" \
    --exclude='./git' \
    --exclude='.git' \
    --exclude='./.git' \
    --exclude='./target' \
    --exclude='./firmware' \
    -C "${repo_root}" \
    -cf - . | gzip -n > "${staging_dir}/catplay.tar.gz"

# Keep local edits and new sources, but exclude ignored build products (dist/,
# *.o, *.ko, etc.). A Git archive of HEAD would discard development changes.
git -C "${repo_root}" ls-files --cached --others --exclude-standard -z -- "${module_path}" |
    while IFS= read -r -d '' file; do
        if [ -e "${repo_root}/${file}" ] || [ -L "${repo_root}/${file}" ]; then
            printf '%s\0' "${file}"
        fi
    done | sort -zu > "${staging_dir}/module-files"

tar "${tar_options[@]}" --null --no-recursion \
    -C "${repo_root}" -cf - -T "${staging_dir}/module-files" |
    gzip -n > "${staging_dir}/catplay-g-iphone.tar.gz"

# Publish only complete archives; preserve mtime when the content is unchanged.
for name in catplay.tar.gz catplay-g-iphone.tar.gz; do
    if ! cmp -s "${staging_dir}/${name}" "${bundle_dir}/${name}"; then
        mv "${staging_dir}/${name}" "${bundle_dir}/${name}"
    fi
done
