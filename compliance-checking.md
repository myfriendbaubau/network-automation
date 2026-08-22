# Compliance Checking

Configuration backup answers *what changed*. Compliance checking answers a
different question: *is each device in the state intended for its role?*

The audit is deliberately read-only. It gathers configuration and operational
state, reports every finding, then returns a non-zero exit status if anything is
wrong or if an expected device did not report.

## Design

Rules are separated by device role rather than repeated with `when` statements:

```text
Play 1  hosts: ios            five baseline checks on all eight IOS devices
Play 2  hosts: access         three access-layer checks
Play 3  hosts: distribution   three distribution-layer checks
Play 4  hosts: core           two core-layer checks
Play 5  hosts: ios            run-once aggregate verdict and exit status
```

That is 13 checks. ASAV-0 is intentionally outside this playbook until an ASA
baseline is defined; the firewall is not presented as audited.

## What is verified

| Layer | Check | Exact condition |
|---|---|---|
| IOS baseline | NTP | Configured set exactly matches both intended servers |
| IOS baseline | VTY transport | Every VTY range has exactly `transport input ssh` |
| IOS baseline | Enable secret | Type 8 or type 9 `enable secret` is present |
| IOS baseline | Password encryption | `service password-encryption` is present |
| IOS baseline | SSH | `show ip ssh` reports version 2 |
| Access | Port security | Every interface listed in `host_facing_ports` is an access port with port security |
| Access | BPDU Guard | Every interface listed in `host_facing_ports` has BPDU Guard |
| Access | DHCP snooping | Enabled globally and for that switch's access VLAN |
| Distribution | ACL rules | Ordered `MGMT-RESTRICT` rules exactly match inventory intent |
| Distribution | ACL placement | Applied inbound on exactly the three intended SVIs |
| Distribution | HSRP | Priority-110 groups match the design and no group 0 exists |
| Core | OSPF process | OSPF process 1 is configured |
| Core | OSPF neighbours | Exact expected neighbour count and every state begins `FULL/` |

The intended port list, ACL, HSRP groups and neighbour counts live in
`inventory/group_vars/`. This makes the expected state reviewable instead of
embedding topology assumptions inside regular expressions.

## Collect everything, then fail once

Individual assertions use `ignore_errors: true` so one finding does not prevent
the remaining rules from running. Each play appends its failures and a phase
completion marker to host facts:

```yaml
- name: Record access failures
  ansible.builtin.set_fact:
    compliance_failures: >-
      {{ (compliance_failures | default([]))
         + ([inventory_hostname ~ ': port-security missing on a host-facing port']
            if c_portsec.failed else []) }}
    compliance_phases_completed: >-
      {{ (compliance_phases_completed | default([])) + ['access'] }}
```

The final play collects those facts and compares completed phases with the
phases required for each role. This also catches a layer gather task that failed
after the baseline fact had already been created:

```yaml
- name: Collect findings from every audited device
  ansible.builtin.set_fact:
    all_failures: >-
      {{ (all_failures | default([]))
         + (hostvars[item].compliance_failures | default([]))
         + ([item ~ ': incomplete audit phase(s): '
             ~ (missing_phases | join(', '))]
            if missing_phases | length > 0 else []) }}
  loop: "{{ ansible_play_hosts_all }}"
```

The last assertion fails if `all_failures` is not empty. That distinction matters
for cron and CI: printed `FAIL` text with exit code zero is still automation
success to the calling system.

The verdict targets `ios`, and its aggregation block uses `run_once`. It does
not target `localhost`, so it remains present when a command limits the audit:

```bash
ansible-playbook playbooks/check_compliance.yml --limit ACC1
```

`ansible_play_hosts_all` is the roster after `--limit`, so the command audits
ACC1 without inventing seven absent-device findings.

## Why exact checks matter

Presence alone is usually insufficient:

- One protected interface does not prove every endpoint port is protected.
- Three `deny` words do not prove an ACL contains the intended addresses or order.
- No `EXSTART` text does not prove an OSPF neighbour exists or is FULL.
- An existing SSH session does not prove the configured management state.

The playbook therefore compares ordered ACL rules, evaluates every intended
interface and checks both OSPF neighbour count and state.

## Deliberate failure tests

The first version was tested by removing DHCP snooping on ACC1 and removing the
ACL from one DIST1 SVI:

```text
ACC1(config)# no ip dhcp snooping
DIST1(config)# interface vlan 20
DIST1(config-if)# no ip access-group MGMT-RESTRICT in
```

Both faults were detected on the intended devices:

![DHCP snooping failure detected on ACC1](./screenshots/compliance-dhcp-snooping-fail.png)

![ACL application failure detected on DIST1](./screenshots/compliance-acl-fail.png)

After changing a compliance rule, test at least these cases again:

1. Clean full run returns zero.
2. Deliberate finding returns non-zero after reporting all checks.
3. Unreachable device is reported as a finding.
4. `--limit` still executes the verdict.

An all-green run only proves value after the suite has demonstrated that it can
fail for the right reason.
