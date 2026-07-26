# Management Network — Ansible Controller

Foundation for network automation. Adds an in-band management path so an Ansible controller can reach every device over SSH.

> Passwords in this document are placeholders. Substitute your own.

---

## 1. Design decision — in-band vs out-of-band

Out-of-band was the intended design: a dedicated management NIC on every device, bridged to a separate segment. It was rejected for a hard constraint.

**Constraint:** DIST1, DIST2 and ACC1–4 (`ioll2-xe`) had **zero free interfaces** — 12/12 and 5/5 in use. CML locks a node's physical configuration while it is running:

```
HTTP 400: Physical configuration of node is locked
```

Adding NICs would have required stopping six devices mid-fabric. Given the config-persistence issues already encountered in this build, that was judged higher risk than the alternative.

**Adopted:** in-band management. The switches are already reachable across the routed fabric — what was missing was a controller *inside* it. CORE1 had `Gi7` free, so the controller attaches there.

**Tradeoff accepted:** in-band management shares fate with the data plane. If OSPF breaks, management is lost with it. Out-of-band survives control-plane failure. Acceptable for a lab; a production design would use a dedicated OOB network.

---

## 2. Topology addition

```
ANSIBLE (Ubuntu 24.04) ── ens2 ── Gi7 ── CORE1 ── [OSPF area 0] ── entire fabric
                                            │
                                            └── asav-0 ── internet (NAT, for apt/git)
```

| Element | Value |
|---|---|
| Node type | `ubuntu` (`ubuntu-24-04`) |
| Management subnet | `10.0.50.0/24` |
| CORE1 `Gi7` | `10.0.50.1/24`, passive in OSPF |
| ANSIBLE `ens2` | `10.0.50.10/24`, gw `10.0.50.1` |

---

## 3. Device management addresses

| Device | Address | Source |
|---|---|---|
| CORE1 | `10.0.50.1` / `1.1.1.1` | Gi7 / Loopback0 |
| CORE2 | `2.2.2.2` | Loopback0 via OSPF |
| DIST1 | `3.3.3.3` | Loopback0 via OSPF |
| DIST2 | `4.4.4.4` | Loopback0 via OSPF |
| ACC1 | `10.0.10.4` | new SVI, VLAN 10 |
| ACC2 | `10.0.20.4` | new SVI, VLAN 20 |
| ACC3 | `10.0.30.4` | new SVI, VLAN 30 |
| ACC4 | `10.0.40.4` | new SVI, VLAN 40 |
| asav-0 | `10.0.12.21` | inside-core1 |

Access switches are managed on the VLAN they already carry rather than a dedicated management VLAN. This required **no trunk changes**, minimising risk to the working fabric. Migrating to a dedicated management VLAN is a candidate follow-up — and a good first real change to drive through the automation itself.

`.4` sits inside the existing DHCP exclusion range (`.1`–`.10`), so no lease collision.

---

## 4. Configuration

### CORE1
```
interface GigabitEthernet7
 description ANSIBLE controller / management
 ip address 10.0.50.1 255.255.255.0
 no shutdown
!
router ospf 1
 network 10.0.50.1 0.0.0.0 area 0
 passive-interface GigabitEthernet7
```

`passive-interface` advertises the subnet without sending hellos to a host that does not run OSPF.

### ANSIBLE controller — `/etc/netplan/50-cloud-init.yaml`
```yaml
network:
  version: 2
  ethernets:
    ens2:
      addresses: [10.0.50.10/24]
      routes:
        - to: default
          via: 10.0.50.1
      nameservers:
        addresses: [8.8.8.8, 1.1.1.1]
```
```bash
sudo netplan apply
sudo apt install ansible sshpass -y
ansible-galaxy collection install cisco.ios cisco.asa
```

Internet access for `apt` and `git push` traverses CORE1 → asav-0 → NAT. The ASA's existing `10.0.0.0/8` PAT rule already covers `10.0.50.0/24`.

### All IOS devices
```
ip domain-name lab.local
crypto key generate rsa modulus 2048
ip ssh version 2
!
username cisco privilege 15 secret <PASSWORD>
!
line vty 0 4
 login local
 transport input ssh
```

ACC2, ACC3 and ACC4 had **no local credentials at all** prior to this — the `username` line is new on those, not a duplicate.

### Access switches — SVI and return route
```
! ACC1 (repeat per switch with its own VLAN and VIP)
interface Vlan10
 ip address 10.0.10.4 255.255.255.0
 no shutdown
!
ip route 0.0.0.0 0.0.0.0 10.0.10.1
```

| Switch | VLAN | Address | Default route |
|---|---|---|---|
| ACC1 | 10 | `10.0.10.4` | `10.0.10.1` |
| ACC2 | 20 | `10.0.20.4` | `10.0.20.1` |
| ACC3 | 30 | `10.0.30.4` | `10.0.30.1` |
| ACC4 | 40 | `10.0.40.4` | `10.0.40.1` |

Next hops are the existing HSRP VIPs.

### asav-0
```
crypto key generate rsa modulus 2048
username cisco password <PASSWORD> privilege 15
aaa authentication ssh console LOCAL
ssh 10.0.50.0 255.255.255.0 inside-core1
ssh version 2
```

`aaa authentication ssh console LOCAL` is mandatory. Without it the ASA accepts the TCP connection and rejects every login.

### SSH client — `~/.ssh/config` on the controller
```
Host 10.0.* 1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4
    HostKeyAlgorithms +ssh-rsa
    PubkeyAcceptedAlgorithms +ssh-rsa
    KexAlgorithms +diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1
    Ciphers +aes128-cbc,aes192-cbc,aes256-cbc
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
```

`+` appends to modern defaults rather than replacing them, so strong algorithms are still preferred where the device supports them. `StrictHostKeyChecking no` prevents Ansible stalling on host-key prompts — acceptable in a lab, not in production.

---

## 5. Verification

```bash
# From the controller
ping 10.0.50.1      # CORE1, directly connected
ping 3.3.3.3        # DIST1 loopback — proves OSPF reachability
ping 8.8.8.8        # internet via ASA NAT

ssh cisco@10.0.50.1     # CORE1
ssh cisco@3.3.3.3       # DIST1
ssh cisco@10.0.10.4     # ACC1 — proves the new SVI and return route
ssh cisco@10.0.12.21    # asav-0
```

---

## 6. Lessons

**`ip default-gateway` is silently ignored when `ip routing` is enabled.**
Applied to the access switches, accepted by the CLI without error, and had no effect. `ioll2-xe` (IOS-XE) images enable `ip routing` by default, unlike the classic IOSvL2 images most tutorials use. The symptom gave nothing away: SVI up, SSH enabled, gateway pingable, connection times out. Diagnosis was `show ip route` — a populated routing table with `Gateway of last resort is not set` rather than the `Default gateway is …` line an L2-mode switch prints. Fix: a real static default route.

**OpenSSH 9.x refuses `ssh-rsa` host keys.**
`no matching host key type found. Their offer: ssh-rsa` — SHA-1 signature algorithms were disabled by default. The devices are not at fault; the client is. Fixed once in `~/.ssh/config` rather than per-command, so Ansible inherits it too.

**`crypto key generate rsa` requires `ip domain-name` first.**
The key name derives from `hostname.domain`. Without a domain the command is rejected, and SSH silently never starts.

**CML locks physical node configuration while running.**
This constraint, not preference, drove the in-band design. Worth checking interface availability *before* planning a topology change — the alternative was a six-device maintenance window.
