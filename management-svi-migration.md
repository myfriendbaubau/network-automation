# Access-Switch Management SVI Migration

This runbook moves the access switches from management addresses on their
local user VLANs to the dedicated in-band management VLAN 30.

The Ansible controller remains on `10.0.50.10/24`. It reaches VLAN 30 through
the routed fabric; it does not need to share the management-device subnet.

**Migration status: complete.** ACC1, ACC2 and ACC4 were migrated
successfully. ACC3 already used VLAN 30 and required no address change.

## Design

| Device | Old management | New management | Status |
|---|---|---|---|
| ACC1 | `10.0.10.4`, VLAN 10 | `10.0.30.5`, VLAN 30 | Migrated |
| ACC2 | `10.0.20.4`, VLAN 20 | `10.0.30.6`, VLAN 30 | Migrated |
| ACC3 | `10.0.30.4`, VLAN 30 | No change | Already correct |
| ACC4 | `10.0.40.4`, VLAN 40 | `10.0.30.7`, VLAN 30 | Migrated |

VLAN 30 uses these infrastructure addresses:

| Function | Address |
|---|---|
| HSRP gateway | `10.0.30.1` |
| DIST1 SVI | `10.0.30.2` |
| DIST2 SVI | `10.0.30.3` |
| ACC3 management | `10.0.30.4` |

VLAN 30 is already active and allowed across the relevant distribution and
access-switch trunks, so the migration requires no trunk changes.

The endpoint VLAN is stored separately in `access_vlan`. Moving a management
SVI to VLAN 30 must not make the compliance playbook check DHCP snooping on
the wrong client VLAN.

## Safety model

The migration changes the IP address used by Ansible itself. A mistake can
remove remote access, so the workflow applies these protections:

1. Exactly one switch must be selected with `--limit`.
2. Check mode is rejected because it cannot test a new address that does not
   exist yet.
3. The old address must match the current inventory.
4. VLAN 30 must already be active and carried on the trunks.
5. A raw pre-change backup is stored locally with mode `0600` in the
   Git-ignored `backups-pre-change/` directory.
6. The current configuration is saved as the recovery configuration.
7. A timed recovery reload must be armed from the CML console.
8. The old management SVI remains available while the new path is tested.
9. Ansible reconnects through the new address before removing the old path.
10. A second fresh SSH connection verifies the final routing state.
11. The new configuration is saved only after all verification passes.

### Why the reload is armed from the console

This CML IOS image rejects `reload in 15` over SSH with:

```text
Reload to the ROM monitor only allowed from console line
unless the configuration register boot bits are non-zero.
```

The command must therefore be entered from the CML console. The playbook runs
`show reload` and refuses to change the switch unless the output contains a
real scheduled reload. The negative result `No reload is scheduled` is
explicitly rejected.

## Files involved

| File | Purpose |
|---|---|
| `inventory/group_vars/all/management_migration.yml` | Old and new migration addresses |
| `playbooks/migrate_management_svi.yml` | One-time protected transition |
| `inventory/hosts.yml` | Permanent Ansible connection address |
| `inventory/group_vars/access.yml` | Permanent management and client VLAN intent |
| `playbooks/deploy_configs.yml` | Normal post-migration deployment and verification |
| `playbooks/check_compliance.yml` | Security and topology compliance checks |

## 1. Update and validate the controller

```bash
cd ~/network-automation
source .venv/bin/activate
git pull --rebase
```

```bash
yamllint -c .yamllint --strict .
python -m unittest discover -s tests -v
ansible-playbook playbooks/migrate_management_svi.yml --syntax-check
ansible-lint --profile production
```

Do not proceed if any validation fails.

## 2. Confirm the current inventory

The completed procedure used ACC2 as the example below. Replace `ACC2` and
its addresses if this runbook is reused for another device:

```bash
ansible-inventory --host ACC2 | grep ansible_host
```

At the start of the ACC2 migration, the result had to show:

```text
ansible_host: 10.0.20.4
```

Confirm current connectivity:

```bash
ansible ACC2 -m cisco.ios.ios_command \
  -a "commands='show ip interface brief'"
```

Create a sanitized repository backup:

```bash
ansible-playbook playbooks/backup_configs.yml --limit ACC2
```

## 3. Confirm that the target address is unused

For ACC2, the proposed address is `10.0.30.6`:

```bash
ping -c 2 10.0.30.6
```

No reply is expected. A failed ping is not conclusive, so also check both
distribution-switch ARP tables:

```bash
ansible DIST1 -m cisco.ios.ios_command \
  -a "commands='show ip arp 10.0.30.6'"

ansible DIST2 -m cisco.ios.ios_command \
  -a "commands='show ip arp 10.0.30.6'"
```

Stop if the target address has an existing ARP entry or responds to ping.

## 4. Arm recovery from the CML console

Open the target switch console in CML and leave it visible.

```text
enable
write memory
reload in 15
```

Press Enter at the confirmation prompt, then verify:

```text
show reload
```

The output must say that a reload is scheduled. Do not start the playbook if
the output says `No reload is scheduled`.

## 5. Run the migration

Run against exactly one switch:

```bash
ansible-playbook playbooks/migrate_management_svi.yml \
  --limit ACC2 \
  -v
```

Never omit `--limit`, and do not add `--check`.

For ACC2, the playbook performs this sequence:

1. Connect to `10.0.20.4`.
2. Confirm VLAN 30 and the trunks.
3. Confirm the console recovery reload.
4. Configure `Vlan30` with `10.0.30.6/24`.
5. Add a temporary `/32` route to controller `10.0.50.10` through
   `10.0.30.1`.
6. Wait for TCP/22 on `10.0.30.6`.
7. Reconnect through `10.0.30.6`.
8. Install the new default route through `10.0.30.1`.
9. Remove the old route through `10.0.20.1`.
10. Remove `10.0.20.4` from `Vlan20`.
11. Remove the temporary controller route.
12. Establish another fresh SSH connection through `10.0.30.6`.
13. Verify VLAN 30 is `up/up` and the default route is correct.
14. Save the verified configuration.

Removing the IP address from `Vlan20` does not remove VLAN 20 or its access
ports. Endpoint switching continues to use the original client VLAN.

## 6. Success procedure

After the playbook reports success, immediately return to the CML console:

```text
reload cancel
show reload
```

Expected result:

```text
No reload is scheduled.
```

Test the new address:

```bash
ping -c 4 10.0.30.6
ssh cisco@10.0.30.6
```

Until the permanent inventory is updated, use an Ansible runtime override:

```bash
ansible ACC2 \
  -e ansible_host=10.0.30.6 \
  -m cisco.ios.ios_command \
  -a "commands='show ip interface brief | include Vlan30'"
```

```bash
ansible ACC2 \
  -e ansible_host=10.0.30.6 \
  -m cisco.ios.ios_command \
  -a "commands='show ip route 0.0.0.0'"
```

## 7. Failure and recovery procedure

If the migration fails after changing the switch:

- Do not run `write memory`.
- Do not copy the running configuration to startup configuration.
- Do not cancel the reload.
- Monitor the CML console.
- Wait for the timer to expire.

The switch should boot the saved old configuration and return on its old
management address.

After recovery, collect this state before retrying:

```text
show reload
show ip interface brief
show ip route 0.0.0.0
show running-config | section ^interface Vlan30
```

## 8. Update the permanent source of truth

Only after successful migration and reload cancellation, update
`inventory/hosts.yml`.

For ACC2:

```yaml
ACC2: {ansible_host: 10.0.30.6}
```

Update its `mgmt_svi` entry in `inventory/group_vars/access.yml`:

```yaml
ACC2:
  vlan: 30
  name: MGMT
  ip: 10.0.30.6
  gateway: 10.0.30.1
```

Do not change this endpoint intent:

```yaml
access_vlan:
  ACC2: 20
```

That separation ensures DHCP-snooping compliance continues to check VLAN 20.

## 9. Post-migration verification

After committing, pushing and pulling the inventory update:

```bash
ansible-inventory --host ACC2 | grep ansible_host
```

```bash
ansible-playbook playbooks/deploy_configs.yml \
  --limit ACC2 \
  --check \
  --diff
```

The dry run should report that the intended management configuration is
already present.

```bash
ansible-playbook playbooks/check_compliance.yml --limit ACC2
ansible-playbook playbooks/backup_and_commit.yml --limit ACC2
```

The backup job records the new address and removal of the old management SVI
address in Git history.

## 10. Completion record

Each device was handled as a separate maintenance operation:

1. ACC1: `10.0.10.4` to `10.0.30.5` - completed
2. ACC2: `10.0.20.4` to `10.0.30.6` - completed
3. ACC3: `10.0.30.4` - no migration required
4. ACC4: `10.0.40.4` to `10.0.30.7` - completed

The one-device-at-a-time restriction remains part of the playbook so that any
future use stops at the first failure instead of affecting multiple switches.

## Final state

The deployed management state is:

```text
ACC1  10.0.30.5
ACC2  10.0.30.6
ACC3  10.0.30.4
ACC4  10.0.30.7
Gateway 10.0.30.1
```

Run the complete validation suite:

```bash
yamllint -c .yamllint --strict .
python -m unittest discover -s tests -v
ansible-lint --profile production
ansible-playbook playbooks/deploy_configs.yml --check --diff
ansible-playbook playbooks/check_compliance.yml
ansible-playbook playbooks/backup_and_commit.yml
```
