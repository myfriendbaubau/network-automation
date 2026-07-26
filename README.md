# Network Automation

Ansible config-as-code for a 3-tier enterprise lab built in Cisco Modeling Labs.

Manages 9 devices (Cisco IOS + ASA) over SSH: config backup with automatic
credential scrubbing, and drift detection via Git.

The network itself is documented here:
https://github.com/myfriendbaubau/3-Tier-Enterprise-Network-Lab

## Playbooks

| Playbook | Purpose |
|---|---|
| `gather_facts.yml` | Read-only inventory across all devices |
| `backup_configs.yml` | Pull running-configs, scrub secrets |
| `backup_and_commit.yml` | Backup + commit to Git if changed |
| `configure_ntp.yml` | Configure NTP servers |

Credentials live in an `ansible-vault` encrypted file, excluded from this repo.
Config backups are automatically scrubbed of password hashes before commit.
