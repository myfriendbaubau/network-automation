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
ansible_playbook="${repo_dir}/.venv/bin/ansible-playbook"

if [[ ! -x "${ansible_playbook}" ]]; then
  printf 'Ansible executable not found: %s\n' "${ansible_playbook}" >&2
  exit 127
fi

exec "${ansible_playbook}" playbooks/backup_and_commit.yml
