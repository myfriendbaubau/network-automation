# Unused Access-Port Hardening

This runbook documents how unused interfaces on the four access switches are
placed in VLAN 999 and administratively shut down with Ansible.

## 1. Objective

An unused port left in VLAN 1 and administratively enabled can provide an
unapproved connection into the network. The intended configuration is:

```text
interface Ethernet1/1
 description ** UNUSED - parked **
 switchport mode access
 switchport access vlan 999
 switchport nonegotiate
 no cdp enable
 shutdown
```

The policy provides several layers of protection:

- Access mode prevents the port from negotiating a trunk.
- VLAN 999 separates the port from production VLANs.
- `switchport nonegotiate` disables DTP negotiation.
- `no cdp enable` prevents CDP advertisements on the port.
- `shutdown` prevents traffic until the port is deliberately reassigned.

VLAN 999 has no SVI, gateway or routed service. It is not added to the trunk
allowed-VLAN lists, so parked-port traffic cannot cross the access layer.

## 2. Source of truth

The policy and per-switch port lists are defined in
`inventory/group_vars/access.yml`:

| Switch | Interfaces managed as unused |
|---|---|
| ACC1 | Ethernet1/1, Ethernet1/2, Ethernet1/3 |
| ACC2 | Ethernet1/1, Ethernet1/2, Ethernet1/3 |
| ACC3 | Ethernet1/1, Ethernet1/2, Ethernet1/3 |
| ACC4 | Ethernet1/3 |

ACC4 Ethernet1/1 and Ethernet1/2 are intentionally excluded because they are
active server-facing access ports in VLAN 40. The automation also refuses any
overlap between `unused_ports` and `host_facing_ports`.

The inventory is authoritative. Do not replace the lists with one blanket
interface range unless the physical topology proves that every listed port has
the same purpose on every switch.

## 3. VTP design

DIST1 is the VTP server and the access switches are VTP clients. The playbook
therefore creates `999 PARKING_LOT` on DIST1 rather than attempting to create
it independently on every access switch.

The playbook performs this sequence:

1. Confirm the selected access switch is a VTP client.
2. Confirm DIST1 is the VTP server.
3. Create VLAN 999 on DIST1 with `cisco.ios.ios_vlans`.
4. Wait for the access switch to learn VLAN 999 through VTP.
5. Configure the declared unused ports only after propagation succeeds.

VTP distributes the VLAN definition. VLAN 999 does not need to be added to the
allowed-VLAN list to serve as a local parking VLAN.

## 4. Safety controls

`playbooks/harden_unused_ports.yml` includes the following protections:

- `serial: 1` processes one access switch at a time.
- `any_errors_fatal: true` stops the rollout after a failure.
- Inventory assertions reject missing, duplicate or overlapping intent.
- A missing interface always stops the play.
- A physically up interface stops the play by default.
- VTP roles are checked before VLAN creation.
- A raw pre-change backup is written with mode `0600` outside Git tracking.
- Check mode shows proposed changes without saving configuration.
- Live runs verify VLAN and interface state before saving.

The raw files under `backups-pre-change/` are not scrubbed and must never be
committed.

## 5. Verify that ports are unused

Before overriding the connected-port protection, check the selected switch:

```text
show interfaces status
show cdp neighbors
show lldp neighbors
show mac address-table interface Ethernet1/1
show mac address-table interface Ethernet1/2
show mac address-table interface Ethernet1/3
```

Also inspect the CML topology and follow each virtual cable. A missing CDP or
MAC-table entry is supporting evidence, but it does not prove that a real
production port is unused; a silent or powered-off endpoint may still exist.

In this CML image, an unused virtual interface can report `up/up`. The explicit
confirmation variable exists for that lab-specific case. It must be supplied
only after the port mapping has been verified:

```text
-e "confirm_connected_unused_ports=true"
```

Never use the override as a general way to silence the safety check.

## 6. Validate the automation

On the Ansible controller:

```bash
cd ~/network-automation
source .venv/bin/activate

yamllint -c .yamllint --strict \
  inventory/group_vars/access.yml \
  playbooks/harden_unused_ports.yml \
  playbooks/check_compliance.yml

ansible-playbook playbooks/harden_unused_ports.yml --syntax-check
ansible-lint --profile production
```

Resolve every lint or syntax failure before contacting a switch.

## 7. Roll out one switch at a time

Start with a dry run. For a connected interface already confirmed as unused in
CML, run:

```bash
ansible-playbook playbooks/harden_unused_ports.yml \
  --limit ACC1 \
  --check \
  --diff \
  -e "confirm_connected_unused_ports=true" \
  -v
```

Review the proposed commands. The expected result is VLAN 999 on DIST1 and the
declared access interfaces configured with the six hardening commands.

Apply the change only when the dry run matches the design:

```bash
ansible-playbook playbooks/harden_unused_ports.yml \
  --limit ACC1 \
  --diff \
  -e "confirm_connected_unused_ports=true" \
  -v
```

Repeat the verification, dry run and live run individually for ACC2, ACC3 and
ACC4. Do not target the complete `access` group with the override until every
port on every switch has been independently verified.

If the declared ports are already physically down, omit the confirmation
variable. The normal safety check will pass without an override.

## 8. Verify the result

The playbook verifies and saves each live change automatically. Additional
device-side checks are:

```text
show vlan id 999
show running-config interface Ethernet1/1
show running-config interface Ethernet1/2
show running-config interface Ethernet1/3
show interfaces status
```

After all four switches are complete, run the access-layer compliance check:

```bash
ansible-playbook playbooks/check_compliance.yml --limit access -v
```

Then run the normal scrubbed configuration-backup workflow so the repository
captures the new device state.

Running the hardening playbook again should report `already compliant`. That
second run is the idempotency test.

## 9. Failure handling

### Port reported as physically up

Stop and verify its CML link, neighbors and learned MAC addresses. Use the
confirmation variable only after proving the port is safe to shut down.

### Incorrect VTP role

Run `show vtp status` on the access switch and DIST1. Do not bypass this check;
correct the VTP design or the inventory value first.

### VLAN propagation timeout

Check the VTP domain, version, password, revision and trunk connectivity. Do
not add VLAN 999 to production trunk allowed lists merely to fix propagation;
VTP advertisements and VLAN 999 user traffic are separate concerns.

### Paramiko fallback warning

The warning that `ansible-pylibssh` is not installed is informational when the
Paramiko connection succeeds. It is unrelated to port hardening.

## 10. Rollback

Use the matching file in `backups-pre-change/` to identify the exact original
interface state. Restore only the affected switch and ports; do not use a
blanket `default interface` command without reviewing what it would remove.

Update `unused_ports` before restoring a port for production use, otherwise the
next playbook run will park it again. VLAN 999 should remain while any parked
port uses it. If it is no longer required anywhere, remove it from DIST1, the
VTP server, only after all ports have been reassigned.
