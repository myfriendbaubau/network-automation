# Current CML Device Remediation

The files in `backups/` are evidence of device state, not configuration source
files. Do not hand-edit a snapshot to hide a finding. Apply the correction to
the CML device, verify it, and run the backup playbook again.

The disposable ASA details and the lab's public-looking loopbacks are accepted
design choices for this isolated home lab and are deliberately excluded here.

## 1. Protect every actual endpoint port

The compliance scope now matches the CML topology rather than assuming that
every numbered interface is connected to an endpoint:

* `ACC1`: `Ethernet1/0`
* `ACC2`: `Ethernet1/0`
* `ACC3`: `Ethernet1/0`
* `ACC4`: `Ethernet1/0`, `Ethernet1/1`, `Ethernet1/2`

`ACC4` was the only access switch with endpoint-facing `Ethernet1/1` and
`Ethernet1/2`. Those two ports were missing port security and BPDU Guard. They
were remediated with:

```text
configure terminal
interface range Ethernet1/1-2
 switchport access vlan 40
 switchport mode access
 spanning-tree portfast
 spanning-tree bpduguard enable
 switchport port-security
 switchport port-security maximum 3
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

The backup review found `standby 0 preempt` under `Vlan30` on `DIST1`. The
intended group on that SVI is group 3, so the stray group 0 command was removed
with:

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
