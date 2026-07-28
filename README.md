# Network Automation — Ansible

Config-as-code for the 3-tier lab. An Ansible controller inside the fabric manages all nine devices over SSH, backs up their configs to Git, detects drift, verifies compliance, and generates device configuration from templates.

Builds on the management network described in [`management-network.md`](./management-network.md).
The network itself is documented here:
https://github.com/myfriendbaubau/3-Tier-Enterprise-Network-Lab

---

## 1. What it does

| Capability | Playbook |
|---|---|
| Inventory and fact gathering across 9 devices | `gather_facts.yml` |
| Config backup with credential scrubbing | `backup_configs.yml` |
| Backup + Git commit on change (drift detection) | `backup_and_commit.yml` |
| Compliance checking across 4 device layers | `check_compliance.yml` |
| Config generation from Jinja2 templates | `generate_configs.yml` |
| Deploy templated config to devices | `deploy_configs.yml` |
| NTP configuration | `configure_ntp.yml` |

Controller: Ubuntu 24.04, `10.0.50.10`, attached to CORE1 `Gi7`.
Managed devices: CORE1, CORE2, DIST1, DIST2, ACC1–4 (`cisco.ios`), asav-0 (`cisco.asa`).

Three capabilities, three different questions:

| Playbook | Question it answers |
|---|---|
| `backup_and_commit.yml` | What changed, and when? |
| `check_compliance.yml` | Is the network in its intended state? |
| `deploy_configs.yml` | Make the network match the intended state |

---

## 2. Project layout

```
network-automation/
├── ansible.cfg
├── inventory/
│   ├── hosts.yml
│   └── group_vars/
│       ├── all/
│       │   ├── vars.yml          # references vault variables
│       │   └── vault.yml         # ansible-vault encrypted, git-ignored
│       ├── ios.yml
│       ├── asa.yml
│       └── access.yml            # data model for templated config
├── playbooks/
│   ├── gather_facts.yml
│   ├── backup_configs.yml
│   ├── backup_and_commit.yml
│   ├── check_compliance.yml
│   ├── generate_configs.yml
│   ├── deploy_configs.yml
│   └── configure_ntp.yml
├── templates/
│   └── access_mgmt.j2            # Jinja2 config template
├── generated/                    # rendered config, for review
└── backups/                      # one scrubbed running-config per device
```

Inventory groups mirror the topology — `core`, `distribution`, `access` under `ios`, plus `asa` — so playbooks can target a layer without editing inventory:

```bash
ansible-playbook playbooks/configure_ntp.yml --limit access
ansible-playbook playbooks/configure_ntp.yml --limit 'ios:!access'
```

---

## 3. Credential handling

Credentials live in `inventory/group_vars/all/vault.yml`, encrypted with `ansible-vault` and excluded from Git. IOS and ASA use separate credentials — ASA 9.12+ enforces an 8–127 character password policy that the IOS devices do not.

```yaml
# inventory/group_vars/ios.yml
ansible_become: true
ansible_become_method: ansible.netcommon.enable
ansible_become_password: "{{ vault_enable_password }}"
```

`become` is **required**. The devices run `no aaa new-model`, so a `privilege 15` username does not apply its privilege level to the EXEC session — logins land at `>` and `show running-config` is rejected as invalid input.

The vault password is read from `~/.vault_pass`, deliberately outside the project directory so it cannot be committed even if `.gitignore` is edited. In production this would come from a secrets manager.

---

## 4. Credential scrubbing

`backup_configs.yml` strips secrets from every backup before it is written to Git:

```yaml
- name: Scrub credential hashes
  ansible.builtin.replace:
    path: "{{ playbook_dir }}/../backups/{{ inventory_hostname }}.txt"
    regexp: "{{ item }}"
    replace: '\1 <REDACTED>'
  loop:
    - '(enable secret \d) \S+'
    - '(username \S+ (?:privilege \d+ )?secret \d) \S+'
    - '(pre-shared-key) \S+'
  delegate_to: localhost
```

Covers IOS enable secrets and local users, and the ASA's IKEv2 pre-shared keys. Verify with:

```bash
grep -rn "secret 9\|pre-shared-key" backups/
```

Every match should read `<REDACTED>`.

**Tradeoff:** scrubbed backups are not restore artifacts. They remain useful for drift detection — a redacted line still changes when the underlying secret changes — but a rebuild needs the credentials supplied separately.

---

## 5. Drift detection

`backup_and_commit.yml` imports the backup playbook, then commits only if something changed:

```yaml
- name: Check for staged changes
  ansible.builtin.command:
    cmd: git diff --cached --quiet
  register: git_status
  failed_when: false

- name: Commit if changed
  ansible.builtin.command:
    cmd: git commit -m "Config backup {{ ansible_date_time.iso8601 }}"
  when: git_status.rc != 0
```

Verified by adding a banner on ACC1 **without** saving it, then running the playbook:

```
+ Last configuration change at 03:49:56 UTC Sun Jul 26 2026 by cisco
+ banner motd ^CDrift test^C
```

Committed automatically with timestamp and author. Because the playbook reads **running-config**, it catches changes that were never written to startup-config — the exact failure mode that caused repeated config loss earlier in this build.

Scheduled hourly via cron:

```
0 * * * * cd /home/cisco/network-automation && /usr/bin/ansible-playbook playbooks/backup_and_commit.yml >> /home/cisco/backup.log 2>&1
```

---

## 6. Finding: 36-hour clock skew

The first read-only fact-gathering run surfaced a problem that had been present, unnoticed, for weeks:

| Device group | Reported time |
|---|---|
| CORE1, CORE2 | 12:00 Fri Jul 24 |
| DIST1/2, ACC1–4 | 23:45 Sat Jul 25 |
| asav-0 | 10:09 Sat Jul 25 |

Three separate clocks, roughly 36 hours apart. This made cross-device log correlation impossible and explained the `PKI-2-NON_AUTHORITATIVE_CLOCK` warnings in the boot logs.

Fixed with `configure_ntp.yml`, pointing at Google and Cloudflare NTP by IP (the devices have no DNS configured):

```yaml
- name: Set NTP servers
  cisco.ios.ios_config:
    lines:
      - ntp server 216.239.35.0
      - ntp server 162.159.200.123
    save_when: modified
```

`save_when: modified` writes to startup-config only when something actually changed.

This is the strongest argument for the project: one read-only command across nine devices surfaced a real fault that manual inspection had missed for weeks, and the fix was a nine-device change applied in seconds.

---

## 7. Deployment discipline

Write playbooks are applied in stages, never to all devices at once:

```bash
ansible-playbook playbooks/configure_ntp.yml --check --diff --limit ACC1   # dry run
ansible-playbook playbooks/configure_ntp.yml --limit ACC1                  # one device
ansible ACC1 -m cisco.ios.ios_command -a "commands='show ntp status'"      # verify
ansible-playbook playbooks/configure_ntp.yml                               # all devices
```

Re-running should report `changed=0`. Idempotency is what separates configuration management from a script that blindly retypes commands, and it is what makes drift detection meaningful.

---

## 8. Compliance checking

Backup answers *what changed*. Compliance answers *is the network in its intended state* — a different question, and the one that catches config which was never applied in the first place.

`check_compliance.yml` runs 12 checks across four layer-scoped plays (baseline security on all devices, then access, distribution and core specifics), reporting pass/fail per device.

Its first run found `service password-encryption` missing on all eight devices — specified in the original build document, never applied.

📄 Full detail: [`compliance-checking.md`](./compliance-checking.md)

---

## 9. Config templating

Backup observes, compliance verifies, templating **defines**. Management SVI configuration for the access switches is generated from a Jinja2 template and a YAML data model, with `ios_config` sending only the lines that differ from running-config.

```jinja
{% raw %}{% set svi = mgmt_svi[inventory_hostname] %}
interface Vlan{{ svi.vlan }}
 description {{ svi.name }} - management
 ip address {{ svi.ip }} 255.255.255.0
ip route 0.0.0.0 0.0.0.0 {{ svi.gateway }}{% endraw %}
```

One template serves all four switches; adding a fifth means adding four lines of data and changing nothing else.

A dry run shows the exact CLI commands a variable change would produce, before anything is touched:

```bash
ansible-playbook playbooks/deploy_configs.yml --check --diff -v
```

This also relocates risk. Config is no longer mistyped at the CLI — it is generated faithfully from data, which means an error in the data is reproduced exactly across every device the template covers.

📄 Full detail: [`config-templating.md`](./config-templating.md)

---

## 10. Problems encountered and how they were solved

### A successful playbook run doesn't prove the config is there

Ran `configure_ntp.yml` against all eight IOS devices. Ansible reported success — no failures, nothing to investigate. A spot check afterwards told a different story:

```bash
ansible core -m cisco.ios.ios_command -a "commands='show run | include ntp'"
# empty on both CORE1 and CORE2
```

Six devices had the configuration. Two didn't, and nothing in the output said so.

Ansible answers one question: did its tasks complete. That is not the same question as whether the network ended up in the intended state, and the gap between the two is where silent failures live.

Re-running with `--limit core` fixed the immediate problem. The broader fix is a compliance playbook — one that asserts intended state and reports pass or fail per device, rather than pushing config and trusting the exit code. Verifying state is a separate capability from changing it.

---

### Privilege 15 was not enough to read a config

Fact gathering worked across all nine devices. The first backup attempt failed on all eight IOS devices:

```
show running-config
      ^
% Invalid input detected at '^' marker.

CORE1>
```

The prompt is the tell — `>`, not `#`. The session was in user EXEC mode, where `show running-config` isn't a valid command.

The devices run `no aaa new-model`. Without AAA authorization, IOS does not apply a username's configured privilege level to the EXEC session, so `username cisco privilege 15` grants nothing at login. The earlier fact-gathering run had masked this, because `ios_facts` only issues `show version` — which is valid in user EXEC and gave no hint that privilege escalation was missing.

Fixed by enabling `become` in `group_vars/ios.yml`, with the enable password supplied from the vault:

```yaml
ansible_become: true
ansible_become_method: ansible.netcommon.enable
ansible_become_password: "{{ vault_enable_password }}"
```

The alternative — `privilege level 15` under `line vty` — also works, but relies on line configuration rather than an explicit escalation step, which is harder to audit.

---

### A setup script that was safe to run once, destructive to run twice

A bootstrap script written to rebuild the controller used `cat > file <<EOF` for every config file — which replaces unconditionally, with no check for whether the file already exists.

Running it a second time silently overwrote the vault file back to placeholder values and removed the credential-scrubbing task from the backup playbook. Neither failure announced itself.

The hourly cron job then failed **32 consecutive times** into a log nobody was reading, and unscrubbed configuration reached the public repository in the meantime.

The lesson is not about `cat >`. It is that automation which fails silently is worse than no automation, because you stop checking manually once you believe something is handled. Setup scripts should be idempotent — create if missing, never clobber — and scheduled jobs need a failure signal that is visible without going looking for it.
