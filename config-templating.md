# Config Templating with Jinja2

Backup records what exists. Compliance checks whether it is correct. Templating is the third step: the repository *defines* the configuration, and devices are made to match it.

This covers the management SVI configuration on the four access switches.

---

## 1. The three pieces

```
inventory/group_vars/access.yml   the data — what each switch should have
templates/access_mgmt.j2          the pattern — how that data becomes config
playbooks/deploy_configs.yml      applies the rendered config to devices
playbooks/generate_configs.yml    renders to a file without touching devices
```

**Data:**

```yaml
mgmt_svi:
  ACC1: { vlan: 10, name: DATA,    ip: 10.0.10.4, gateway: 10.0.10.1 }
  ACC2: { vlan: 20, name: GUESTS,  ip: 10.0.20.4, gateway: 10.0.20.1 }
  ACC3: { vlan: 30, name: MGMT,    ip: 10.0.30.4, gateway: 10.0.30.1 }
  ACC4: { vlan: 40, name: SERVERS, ip: 10.0.40.4, gateway: 10.0.40.1 }
```

**Template:**

```jinja
{% set svi = mgmt_svi[inventory_hostname] %}
interface Vlan{{ svi.vlan }}
 description {{ svi.name }} - management
 ip address {{ svi.ip }} 255.255.255.0
ip route 0.0.0.0 0.0.0.0 {{ svi.gateway }}
```

`inventory_hostname` is built in — whichever device Ansible is currently working on. One template serves all four switches; adding a fifth means adding four lines of data and changing nothing else.

`{% set %}` binds the lookup once rather than repeating `mgmt_svi[inventory_hostname].` on every line.

---

## 2. Two modes

**Render to file** — generates config into `generated/<host>.txt`, touches no device. Useful for reviewing output before it goes anywhere.

```yaml
- name: Render template to file
  ansible.builtin.template:
    src: "{{ playbook_dir }}/../templates/access_mgmt.j2"
    dest: "{{ playbook_dir }}/../generated/{{ inventory_hostname }}.txt"
  delegate_to: localhost
```

**Push to device** — `ios_config` renders the template itself and sends only the lines that differ from running-config.

```yaml
- name: Apply templated config
  cisco.ios.ios_config:
    src: "{{ playbook_dir }}/../templates/access_mgmt.j2"
  register: cfg

- name: Save config
  cisco.ios.ios_config:
    save_when: always
  when: cfg.changed
```

Always dry-run first:

```bash
ansible-playbook playbooks/deploy_configs.yml --check --diff -v
```

`-v` shows the exact CLI commands that would be sent. This is the core value: edit a YAML variable, and before anything is touched, see the device-level consequence.

---

## 3. `ios_config` compares strings, not intent

`ios_config` with `src:` does a **literal string comparison** against running-config. It does not understand what a command means.

The template originally included `no shutdown`. That line queued on every single run and never converged. IOS does not store `no shutdown` in running-config — an interface that is up simply has no `shutdown` line. The module looked for the literal text, never found it, and re-sent it indefinitely.

Any command that IOS does not echo back into running-config cannot be idempotent this way. Removing `no shutdown` from the template fixed it.

Cisco's newer resource modules (`ios_l3_interfaces`, `ios_vlans`) exist for this reason — they compare structured state rather than text.

---

## 4. `save_when` breaks the `changed` signal

With `save_when: modified` set directly on the config task, every run reported `changed=true` — but with an **empty command list**. Nothing was being sent. The change flag came from the save operation itself, not from any configuration difference.

That makes `changed` useless as a signal, which matters because the rest of this project depends on it meaning something.

Fixed by splitting the save into its own task, conditional on the first having actually changed something:

```yaml
  register: cfg
...
  when: cfg.changed
```

Now a converged device reports `ok`, `changed=0`, and the save task skips. Config still persists to startup-config when it genuinely changes.

---

## 5. First push converges, then it is idempotent

The devices had SVIs with an IP address and no description. The template specified both.

- First run: the description is queued and applied — `changed`
- Every run after: device matches template — `ok`, `changed=0`, save skipped

This is the normal pattern. The first push brings the device in line with the template; from then on, any `changed` result means something genuinely drifted.

---

## 6. The data becomes the thing that can be wrong

A typo in the variables file — `10.0.30.0.4`, five octets — reached the device:

```
ip address 10.0.30.0.4 255.255.255.0
                    ^
% Invalid input detected at '^' marker.
```

IOS rejected it and Ansible surfaced the CLI error. The safety net worked, but only because the mistake was syntactically invalid.

Had the typo been `10.0.30.5` instead of `10.0.30.4`, IOS would have accepted it and quietly moved that switch's management address, breaking SSH to it.

Templating moves the risk. Config is no longer typed by hand and mistyped at the CLI — it is generated faithfully from data, which means errors in the data are reproduced exactly, on every device the template covers. `--check --diff` before every push matters more here, not less.

---

## 7. Where this sits

| Playbook | Question it answers |
|---|---|
| `backup_and_commit.yml` | What changed, and when? |
| `check_compliance.yml` | Is the network in its intended state? |
| `deploy_configs.yml` | Make the network match the intended state |

Backup observes. Compliance verifies. Templating defines. The repository is now the source of truth for the configuration it covers, rather than a mirror of what happens to be on the devices.

**Current scope:** management SVIs on the four access switches. Extending it means adding data and templates for the rest — VLAN definitions, trunk config, OSPF — each one moving more of the network from "documented after the fact" to "defined in the repository".
