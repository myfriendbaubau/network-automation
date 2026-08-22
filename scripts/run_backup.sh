#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/.." && pwd)"
lock_dir="${XDG_RUNTIME_DIR:-/tmp}"
lock_file="${lock_dir}/network-automation-backup-${UID}.lock"

exec 9>"${lock_file}"
if ! flock -n 9; then
  printf 'Another network-automation backup is already running.\n' >&2
  exit 75
fi

cd -- "${repo_dir}"
exec ansible-playbook playbooks/backup_and_commit.yml
