# Network Automation — Ansible

[![lint](https://github.com/myfriendbaubau/network-automation/actions/workflows/lint.yml/badge.svg)](https://github.com/myfriendbaubau/network-automation/actions/workflows/lint.yml)

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
├── .ansible-lint                 # exclusions only; no rules skipped globally
├── .yamllint
├── .github/workflows/lint.yml    # yamllint --strict + ansible-lint on push
├── collections/
│   └── requirements.yml          # cisco.asa is NOT in the ansible bundle
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
├── generated/                    # rendered config, for review — gitignored
├── backups-pre-change/           # raw, UNSCRUBBED — gitignored, see §7
└── backups/                      # one scrubbed running-config per device
```

Setting up a controller from a clean checkout:

```bash
pip install ansible-core ansible-lint yamllint
ansible-galaxy collection install -r collections/requirements.yml
echo 'your-vault-password' > ~/.vault_pass && chmod 600 ~/.vault_pass
yamllint --strict . && ansible-lint          # same two commands CI runs
```

The collections file exists because `pip install ansible` — the batteries-included
bundle — ships `cisco.ios` but **not** `cisco.asa`. Skip it and every IOS playbook
runs fine while `backup_configs.yml` fails on the single ASA play, which is an
irritating way to discover a missing dependency from cron at 02:00.

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

`become` is **required**. The `cisco` account is defined as `username cisco secret 9 ...` with no privilege level attached, so it authenticates at level 1 — logins land at `>` and `show running-config` is rejected as invalid input. Escalation to privileged EXEC goes through the enable secret, which is what `ansible.netcommon.enable` does.

The vault password is read from `~/.vault_pass`, deliberately outside the project directory so it cannot be committed even if `.gitignore` is edited. In production this would come from a secrets manager.

---

## 4. Credential scrubbing

`backup_configs.yml` strips secrets from every backup before it is written to Git:

```yaml
- name: Scrub credential hashes
  ansible.builtin.replace:
    path: "{{ backup_file }}"
    regexp: "{{ item }}"
    replace: '\1 <REDACTED>'
  loop:
    - '(enable secret(?: \d)?) \S+'
    - '(enable password(?: \d)?) \S+'
    - '(username \S+ (?:privilege \d+ )?secret(?: \d)?) \S+'
    - '(username \S+ password(?: \d)?) \S+'
    - '(key-string(?: \d)?) \S+'
    - '(pre-shared-key) \S+'
    - '(snmp-server community) \S+'
  delegate_to: localhost
```

Everything to keep goes in group 1; everything after it is replaced. The
`(?: \d)?` is load-bearing and was missing. Without it, `enable password 7
0822455D0A16` matches only as far as the `7` — the pattern rewrites
`enable password 7` to `enable password <REDACTED>` and leaves the hash sitting
on the end of the line. No device here currently uses type-7, so nothing leaked;
it was a trap set for the first person to turn on a type-7 password.

That near miss is the argument for the second half, which matters more than the
patterns:

```yaml
- name: Verify nothing secret-shaped survived the scrub
  ansible.builtin.assert:
    that:
      - backup_text is not search('\$\d\$')
      - backup_text is not search('(?i)\b7\s+[0-9A-Fa-f]{10,}\b')
      - backup_text is not search('pre-shared-key (?!<REDACTED>)')
```

The loop is an allowlist: it removes the credential shapes somebody thought of.
The assert is a denylist on *form* rather than keyword, so a secret under a
command nobody wrote a pattern for still stops the run — `\b7\s+<hex>` catches
type-7 anywhere, including `ip ospf message-digest-key 1 md5 7 …`, which none of
the patterns above touch. Checked against all eight real backups, IOS certificate
chains included, with no false positives.

Both backup plays now carry `any_errors_fatal: true`, so a failed scrub check
stops the whole run — including the commit-and-push play that imports them.
Without it, the device with the unrecognised credential would have been committed
and pushed while the run was still marked failed.

Verify by hand with:

```bash
grep -rn "secret 9\|pre-shared-key\|Serial Number" backups/
```

Every match should read `<REDACTED>`.

**Tradeoff:** scrubbed backups are not restore artifacts. They remain useful for drift detection — a redacted line still changes when the underlying secret changes — but a rebuild needs the credentials supplied separately.

**Second tradeoff, less obvious:** redaction also hides rotation. Every enable
secret reads `<REDACTED>` whether it was changed yesterday or two years ago, so
the one thing an audit most wants from a config history — when did this
credential last move — is exactly what these backups cannot answer.

---

## 5. Drift detection

`backup_and_commit.yml` imports the backup playbook, then commits only if something changed:

```yaml
- name: Check for staged changes
  ansible.builtin.command:
    cmd: git diff --cached --quiet
  register: git_status
  failed_when: git_status.rc not in [0, 1]

- name: Commit if changed
  ansible.builtin.command:
    cmd: git commit -m "Config backup {{ ansible_date_time.iso8601 }}"
  when: git_status.rc == 1
```

`git diff --cached --quiet` exits 0 for "nothing staged" and 1 for "something
staged". Anything else — 128 for a broken repository — is a real error. The
original `failed_when: false` with `when: rc != 0` read every non-zero code as
"there are changes", so a broken repo would have been handed to `git commit`.

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

This ran for 25 days without pushing anything. See §10, *Twenty-five days of
backups that never left the building*.

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

For a while this repository claimed that while `deploy_configs.yml` could not
deliver it — see §10, *The playbook that could never report changed=0*.

`deploy_configs.yml` also applies three further guards, in ascending order of
usefulness:

1. **Pre-flight asserts** run before any device is touched — including one that
   refuses to proceed if the address the template would set differs from the
   address Ansible is connected on. That template rewrites the management SVI,
   so a wrong value in `group_vars/access.yml` moves the address out from under
   the session and leaves the switch reachable by console only.
2. **`serial: 1` with `any_errors_fatal: true`** — stop at the first failure
   rather than repeating the same mistake across the whole access layer.
3. **Nothing is saved until the device answers afterwards.** `save_when` is
   gated behind a post-change reachability check, so a change that breaks
   management survives only until the next reload.

The pre-change backup it takes lands in `backups-pre-change/`, which is
gitignored — those files come straight off the device with no scrub step, so
committing them would put back exactly what a history rewrite once took out.

---

## 8. Compliance checking

Backup answers *what changed*. Compliance answers *is the network in its intended state* — a different question, and the one that catches config which was never applied in the first place.

`check_compliance.yml` runs 13 checks across four layer-scoped plays (baseline security on all IOS devices, then access, distribution and core specifics), reporting pass/fail per device.

Its first run found `service password-encryption` missing on all eight devices — specified in the original build document, never applied.

**It also exited 0 while doing it.** Every check carries `ignore_errors: true` —
deliberately, because an audit that stops at the first finding is not an audit —
but an ignored error sets no exit code. The playbook printed `FAIL` on screen and
told cron, CI, and anything else driving it that the run had passed. A compliance
check that cannot fail is a report, not a gate.

The fix is a fifth play. Each of the four check plays records its own findings as
a host fact; the verdict play collects them on localhost and fails once, at the
end, with the whole list:

```yaml
- name: Collect findings from every audited device
  ansible.builtin.set_fact:
    all_failures: >-
      {{ (all_failures | default([]))
         + (hostvars[item].compliance_failures
            if hostvars[item].compliance_failures is defined
            else [item ~ ': checks did not run (unreachable, or the play errored)']) }}
  loop: "{{ audited_hosts }}"
```

Two details in that snippet are the whole point. Aggregating *at the end* rather
than per-play means a device that fails the baseline still gets its access,
distribution and core checks run instead of being dropped from the remaining
plays. And a host that never reported is counted as a **failure**, not skipped —
an unreachable device produces no findings, and reading "no findings" as
"compliant" is precisely how a monitoring system reports a healthy estate while
half of it is off the network.

The verdict play targets `hosts: ios` with `run_once`, not `hosts: localhost`.
The first version targeted localhost, which passed a full run and then did this:

```
$ ansible-playbook playbooks/check_compliance.yml --limit ACC1 ; echo "exit=$?"
...
PLAY [Compliance verdict] ***
skipping: no hosts matched
exit=0
```

`--limit ACC1` matches no host in a localhost-targeted play, so the gate was
skipped and the run reported success — the exact "cannot fail" behaviour the
play was added to remove, reintroduced by a flag §7 of this README recommends
using. It was one line in a long output and it exits 0, so nothing about it
looks wrong. Targeting the audited group fixes it, and looping over
`ansible_play_hosts_all` rather than `groups['ios']` means limiting to one
switch audits one switch instead of reporting seven phantom "did not run"
findings — while still including hosts that were unreachable, so the guard
above survives.

The lesson is narrower than "test your code". A gate that passes has not been
shown to be capable of failing, and those are different claims. This one was
verified against four cases — full run clean, full run with a finding, a host
that never reported, and `--limit` — because the first three all passed while
the fourth was silently broken.

**Known gap:** `audited_hosts` is the eight IOS devices. ASAV-0 has no checks in
this playbook at all. Putting it in the list would produce a permanent false
finding and pretending it is audited would be worse, so the firewall is named as
unaudited here rather than papered over in the code.

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

The account is `username cisco secret 9 ...` with no privilege level attached, so it authenticates at level 1. `login local` on the VTY lines applies that level to the session, and level 1 is user EXEC.

The earlier fact-gathering run had masked this, because `ios_facts` only issues `show version` — valid in user EXEC, so it gave no hint that privilege escalation was missing.

> **An earlier version of this README explained this wrongly**, claiming that `no aaa new-model` prevents a `privilege 15` username from applying its level. Two things were wrong with that. No IOS device here has ever carried `privilege 15` on a username — checked across every commit in this repository's history — so there was nothing for that mechanism to act on. And the mechanism runs the other way: with `login local` and no AAA, a username's privilege level *is* applied at login; it is *with* `aaa new-model` that `aaa authorization exec default local` becomes necessary. The fix below is correct either way — escalating explicitly through the enable secret is better practice than logging straight into privileged EXEC.

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

---

### Twenty-five days of backups that never left the building

The hourly cron job was working. It connected to nine devices, pulled every
running-config, scrubbed them, committed the changes with a timestamp, and wrote
a clean log. The repository on GitHub had not moved in 25 days.

Two commits made in the GitHub web UI had diverged the branch. Every `git push`
since had been rejected:

```
 ! [rejected]        main -> main (fetch first)
error: failed to push some refs to 'https://github.com/myfriendbaubau/network-automation'
```

Eleven commits were stranded locally. The tell in the log was the timing — the
push task failed roughly half a second after starting, far too fast for a network
or authentication problem. My first diagnosis was that the credential helper was
unavailable under cron; that was wrong, and running `git push` by hand is what
showed it.

Three changes came out of it:

* `git pull --no-rebase` before the push, so a diverged branch merges instead of
  rejecting — wrapped in `block`/`rescue` that runs `git merge --abort` on
  conflict, because a half-finished merge would fail every subsequent nightly run
  on the same wreckage;
* the push no longer runs only `when` this run committed something. A commit
  stranded by Monday's failed push has to be able to leave on Tuesday;
* an explicit check afterwards that nothing is still local:

```yaml
- name: Count commits still only on this machine
  ansible.builtin.command:
    cmd: git rev-list --count @{u}..HEAD
  register: git_ahead

- name: Fail if anything did not reach GitHub
  ansible.builtin.fail:
    msg: "{{ git_ahead.stdout }} commit(s) are still local after the push."
  when: git_ahead.stdout | int > 0
```

The first two make this particular failure go away. Only the third makes the
*next* one visible, and it is the one worth copying: `git push` returning 0 is a
different claim from "the remote has my commits", and the playbook now checks the
claim it actually cares about.

What it still cannot do is reach a person. It fails a playbook, and the playbook
writes to a log file. That is a louder version of the same silence — which is the
third time this repository has hit the same wall. See below.

---

### The playbook that could never report changed=0

`deploy_configs.yml` reported `changed` on every run, on every switch, forever.
`--check --diff` printed no diff. Nothing was actually being sent to the devices.

The cause was one line:

```yaml
- name: Apply templated config
  cisco.ios.ios_config:
    src: "{{ playbook_dir }}/../templates/access_mgmt.j2"
    backup: true        # <-- this
```

`cisco.ios.ios_config` with `backup: true` sets `changed=True` unconditionally,
because writing a backup file *is* a change from the module's point of view. It
does not matter that the device was untouched. `changed=0` was unreachable, the
`save_when` task fired on every run against switches that had not changed, and
§7 of this README claimed idempotency the code could not deliver.

I got the diagnosis wrong first: a stray whitespace-only line in the Jinja
template looked like an obvious culprit for a phantom diff. `--check --diff`
printing nothing ruled it out, and commenting out the one `backup: true` line
proved the real cause in a single run.

The fix separates the read from the write. A backup is a read as far as the
device is concerned, so it belongs in its own task with `changed_when: false`:

```yaml
- name: Back up running-config before any change
  cisco.ios.ios_config:
    backup: true
    backup_options:
      dir_path: "{{ playbook_dir }}/../backups-pre-change"
      filename: "{{ inventory_hostname }}.txt"
  changed_when: false

- name: Apply templated config
  cisco.ios.ios_config:
    src: "{{ playbook_dir }}/../templates/access_mgmt.j2"
  register: cfg
```

Downstream tasks then gate on `cfg.updates | length > 0` — the list of commands
the module would actually send — rather than on `cfg.changed`, which conflates
"the device changed" with "the module did something".

The general shape is worth keeping: a `changed` flag is a claim about the
*module*, not about the network. This repository already had a section about the
gap between "the playbook succeeded" and "the network is in the intended state";
this is the same gap seen from the other side, where a task reports a change that
never happened.

---

## 11. Linting and CI

```bash
yamllint --strict .
ansible-lint
```

Both run in CI on every push. The repository is clean at ansible-lint's
`production` profile, which is its strictest.

Nothing is skipped globally. The two rules this repository legitimately breaks are
silenced with inline `# noqa` on the individual tasks, so the rules stay live
everywhere else:

* `command-instead-of-module` on the git tasks in `backup_and_commit.yml` —
  `ansible.builtin.git` clones, fetches and checks out; it does not stage, commit
  or push, so there is no module for what that play does.
* `no-relative-paths` on the template render in `generate_configs.yml`, where
  playbooks live in `playbooks/` and templates in `templates/`.

The CI job writes a placeholder to `~/.vault_pass` before linting. `ansible.cfg`
points at that path, and without it every playbook fails to load and lint reports
`internal-error` on all eight instead of anything useful. Nothing is decrypted:
the real vault file is gitignored and is not in the checkout.

---

## 12. License

[MIT](./LICENSE).

The inventory, addressing and device names throughout are specific to this lab.
The playbooks are worth reading for their structure; none of them are drop-in.
