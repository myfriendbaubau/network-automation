# Current CML Device Remediation

The files in `backups/` are evidence of device state, not configuration source
files. Do not hand-edit a snapshot to hide a finding. Apply the correction to
the CML device, verify it, and run the backup playbook again.

The disposable ASA details and the lab's public-looking loopbacks are accepted
design choices for this isolated home lab and are deliberately excluded here.

## 1. Protect every endpoint port

The current snapshots show port security and BPDU Guard on `Ethernet1/0`, but
not on every endpoint-facing `Ethernet1/1` and `Ethernet1/2`. The compliance
intent now lists all three endpoint ports per access switch, so the audit will
correctly fail until they are protected.

Apply the following on each access switch, replacing `<VLAN>` with 10, 20, 30
or 40 for ACC1, ACC2, ACC3 or ACC4 respectively:

```text
configure terminal
interface range Ethernet1/1-2
 switchport access vlan <VLAN>
 switchport mode access
 spanning-tree portfast
 spanning-tree bpduguard enable
 switchport port-security
 switchport port-security maximum 2
 switchport port-security mac-address sticky
end
write memory
```

Verify with:

```text
show port-security interface Ethernet1/1
show port-security interface Ethernet1/2
show running-config interface Ethernet1/1
show running-config interface Ethernet1/2
```

## 2. Remove the unintended HSRP group 0 command

`DIST1` currently contains `standby 0 preempt` under `Vlan30`. The intended
group on that SVI is group 3. Remove only the stray group 0 command:

```text
configure terminal
interface Vlan30
 no standby 0 preempt
end
write memory
show running-config interface Vlan30
show standby brief
```

The compliance playbook now checks the exact priority-110 groups and explicitly
rejects any `standby 0` line.

## 3. Remove ignored legacy default-gateway commands

These IOS-XE switches route at Layer 3, so `ip default-gateway` is ignored; the
working path is the static `ip route 0.0.0.0 0.0.0.0 ...`. Keeping an ignored
command is misleading, and ACC2 also contains the typo `10.20.0.1`.

```text
! ACC2
configure terminal
 no ip default-gateway 10.20.0.1
end
write memory

! ACC3
configure terminal
 no ip default-gateway 10.0.30.1
end
write memory

! ACC4
configure terminal
 no ip default-gateway 10.0.40.1
end
write memory
```

Verify the real default route on each switch:

```text
show running-config | include ^ip (default-gateway|route 0.0.0.0)
show ip route 0.0.0.0
```

## Close the loop

After the device changes:

```bash
ansible-playbook playbooks/check_compliance.yml
ansible-playbook playbooks/backup_and_commit.yml
```

The first command must return zero. The second replaces the snapshots with the
new device state and versions the change.
