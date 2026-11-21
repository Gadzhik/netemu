# NetEmu User Guide

NetEmu is a network simulator for learning. You build a network from routers, switches, hubs, PCs and servers, configure it through each vendor's own CLI or through forms, and watch packets travel across it.

Help topics:

- [User guide](guide.md): this page
- [Cisco IOS](cisco.md)
- [MikroTik RouterOS](mikrotik.md)
- [Juniper Junos](juniper.md)
- [Huawei VRP](huawei.md)
- [Extreme ExtremeXOS](extreme.md)
- [PC and server](pc.md)

## Quick start

1. **File → Open demo topology.** A ready network opens: PC0 — Cisco — MikroTik — Juniper — Huawei — PC1.
2. Double-click **PC0**, open the **Command Prompt** tab and type `ping 192.168.2.10`.
3. Type `tracert 192.168.2.10`: the replies come from routers of all four vendors.
4. Double-click a router, for example **MT1**, and look at its settings: `/ip address print`, `/ip route print`.

## Main window

- **Devices palette** on the left, grouped into routers, switches, hubs and end devices. Drag a model onto the workspace or double-click it.
- **Workspace** in the centre. The mouse wheel zooms, the middle button pans.
- **Toolbar**: Select, Cable, Delete, Simple PDU, Realtime, Simulation.
- **Status bar** at the bottom: hints, PDU results and the simulation's virtual time.

### Devices

| Type | Models | Configured through |
|---|---|---|
| Routers | Cisco 2911, MikroTik RB4011, Juniper SRX300, Huawei AR2220 | vendor CLI and form |
| Switches | Cisco 2960-24TT, MikroTik CRS326, Juniper EX2300, Huawei S5720, Extreme X440-G2 | vendor CLI and form |
| Hub | Hub (8 ports) | no settings, repeats frames to all ports |
| End devices | PC and server (Windows), PC (Linux), Server (Linux) | command prompt or Linux terminal and IP form |
| Wireless | Access Point, Laptop (Wi-Fi), Laptop (Linux, Wi-Fi) | Wi-Fi form and `netsh wlan` / `nmcli` commands |
| Internet | Internet (ISP) | not configurable: provider, public DNS and web sites |

Switches work at layer 2: they learn MAC addresses (entries live 300 s), forward frames to the right port and flood broadcasts. The ports themselves have no IP addresses, but a switch can route through VLAN interfaces (SVIs) — see the vendor pages.

Cisco router ports start shut down (`shutdown`), as on real hardware. Ports of the other vendors are enabled at once.

### Cables and port indicators

1. Choose the **Cable** tool (key `C`).
2. Click the first device and pick a port from the menu. Ports in use are greyed out.
3. Click the second device and pick a port.

The dots on a cable next to each port show the link state:

- **bright green dot**: the link is up (cable plugged in, both ports enabled, both devices running);
- **red triangle**: the link is down (a port is disabled on one of the sides).

Port labels are toggled in **View → Port labels**.

### Configuring a device

Double-click a device to open its window.

- **Config** tab: name, interface IP addresses and masks, switch port VLANs (`10` = access VLAN 10, `trunk` = all VLANs, `trunk 10,20` = a list), port on/off, static routes (routers); IP, mask, gateway and DNS (PCs and servers). Press **Apply**.
- **CLI** tab ("CLI (Cisco IOS)", "Terminal (RouterOS)", "CLI (Junos)", "CLI (Huawei VRP)", "CLI (ExtremeXOS)", "Command Prompt" on Windows PCs, "Terminal (Linux)" on Linux PCs): the device console.

The form and the CLI share one configuration: a change in one is visible in the other at once.

**The label on the canvas** follows the name set in the CLI (`hostname`, `/system identity set name=`, `set system host-name` + `commit`, `sysname`). A name typed by hand into "Name on canvas" stays even when the hostname changes later.

### Undo, notes and export

- **Undo and redo** (`Ctrl+Z`, `Ctrl+Y`) cover canvas edits: adding and deleting devices and cables, moving them. A deleted device comes back with its whole configuration. Commands typed in a console are not undone, just as on real equipment.
- **Note** (`N`) and **Frame** (`F`) are toolbar tools. Click a spot on the canvas to place one. Double-click changes the text. Drag a frame's bottom-right corner to resize it. Frames are handy for marking zones: "Office 192.168.1.0/24", "Area 0", "DMZ". Notes and frames are saved with the project.
- **File → Export diagram** (`Ctrl+E`) saves the canvas as PNG (double resolution) or SVG for a report.

### Console

- `?`: help for the commands and arguments at the cursor;
- `Tab`: complete the command;
- `↑` / `↓`: command history;
- `Ctrl+C`: stop a running ping or traceroute;
- `Ctrl+Z`: leave configuration mode (Cisco);
- unambiguous abbreviations work: `sh ip ro`, `conf t`, `int g0/0`;
- you can paste several configuration lines at once; they run one after another.

### Simple PDU

The **Simple PDU** tool (key `P`) sends one ICMP echo: click the source, then the destination. The result appears in the status bar as "Successful" or "Failed" with the reason.

## Internet (ISP cloud)

The **Internet** tab of the palette has the **Internet (ISP)** cloud. It stands for a provider and the public Internet; your computer's real network is not used.

- The cloud has 8 ports, each a separate connection. Port `ISP0` gives the customer the address `203.0.113.2/30` and the gateway `203.0.113.1`, port `ISP1` gives `203.0.113.6/30` and `203.0.113.5`, and so on. Double-click the cloud to see the table.
- Inside the cloud run the public DNS servers `8.8.8.8` and `1.1.1.1` and the web sites `www.example.com` (93.184.216.34) and `www.netemu.lab` (198.51.100.80). These addresses answer ping and appear in traceroute.
- Like a real provider, the cloud drops packets from private addresses (10.x, 172.16–31.x, 192.168.x). The router itself reaches the Internet right away; the computers behind it only through NAT.

Cisco example (the router's Gi0/1 is connected to `ISP0`, the LAN 192.168.1.0/24 is on Gi0/0):

```
interface GigabitEthernet0/1
 ip address 203.0.113.2 255.255.255.252
 ip nat outside
interface GigabitEthernet0/0
 ip nat inside
ip route 0.0.0.0 0.0.0.0 203.0.113.1
access-list 1 permit 192.168.1.0 0.0.0.255
ip nat inside source list 1 interface GigabitEthernet0/1 overload
```

Set the PC's DNS server to 8.8.8.8 and check with `ping 8.8.8.8`, `nslookup www.example.com` and the browser at `http://www.netemu.lab`.

## Wireless (Wi-Fi)

The **Wireless** tab of the palette has the **Access Point** and the laptops **Laptop (Wi-Fi)** and **Laptop (Linux, Wi-Fi)**.

- Cable the access point (port `Ethernet0`) to a switch. Its window sets the SSID, security (WPA2-Personal or open), password, channel and range. Defaults: SSID `NetEmu`, password `netemu123`.
- A laptop joins by itself when the SSID and password match and it is within the access point's range on the canvas. The link is drawn dashed. Move the laptop too far away or change the password on the AP and the link goes away.
- Set up Wi-Fi on a laptop in the **Wi-Fi** group of the Config tab or with commands. Windows: `netsh wlan show networks`, `netsh wlan connect name=NetEmu key=netemu123`, `netsh wlan show interfaces`, `netsh wlan disconnect`. Linux: `nmcli dev wifi list`, `nmcli dev wifi connect NetEmu password netemu123`, `iw dev wlan0 link`.
- The access point is a bridge: laptops land in the same network as its Ethernet0 port and get an address over DHCP just like cabled PCs.

## Labs with automatic checking

A project file can contain a lab: a task and its checks. When such a file is opened the **Lab** panel appears on the right. **Check** (or `F6`) tests the network as it is now: every check gets ✔ or ✘ and the total is shown below. Hover over a line for the detail, for example "0/3 replies".

### Ready-made labs

**Lab → Ready-made labs** opens one of the built-in labs as a new project (the shipped file is not changed):

| Lab | The student… |
|---|---|
| VLAN: separating departments | creates VLANs and assigns ports |
| Inter-VLAN routing | trunk and dot1Q subinterfaces (router-on-a-stick) |
| STP: choosing the root bridge | makes the core switch the root |
| Single-area OSPF | adjacencies and routes between three routers |
| DHCP server on a router | pool, exclusions, DHCP on the PCs |
| NAT/PAT and Internet access | ISP address, default route, overload |
| Access lists (ACL) | blocks one PC from the server |
| Gateway redundancy (HSRP) | active and standby routers |
| IPv6: addressing and routing | addresses and `ipv6 unicast-routing` |
| Wireless network | SSID, WPA2, laptops joining |
| 🔧 Troubleshooting: VLAN, OSPF | two faults planted in a working network |

Every lab is verified when it is built: before the solution the checks fail, after it they all pass.

### Student report

**Save report…** (on the lab panel or in the menu) asks for the student's name, runs the checks and saves an HTML report: date, every check with its result and the score. The attempt is also stored in the project; the teacher sees the attempts in the lab editor.

### Teacher mode

The teacher writes a lab in **Lab → Edit lab…**:

- **Task**: title, task text in Markdown and the checks, one per line, as `type arguments | text for the student`;
- **Solution**: teacher's notes, the solution script (a `[R1]` header followed by CLI commands; for PCs and access points `@ip 192.168.1.10/24 gw 192.168.1.1`, `@dhcp`, `@ip6 …`, `@wifi SSID key`, `@ssid SSID key KEY`) and the list of planted faults;
- **Access and results**: the teacher password and the students' attempts.

```text
ping PC0 192.168.2.10 | PC0 pings PC1
noping PC1 192.168.2.10 | the ACL keeps PC1 away from the server
route R1 192.168.2.0/24 | R1 knows PC1's network
ospf R1 1 | R1 has an OSPF neighbour
hostname R1 CORE | the router is named CORE
vlan SW1 10 | SW1 has VLAN 10
access SW1 Fa0/1 10 | Fa0/1 is in VLAN 10
ip PC0 192.168.1.10 | PC0 has the right address
dhcp PC2 | PC2 got an address by DHCP
nat R1 | R1 translates addresses
ssh R1 | SSH is enabled on R1
stproot SW1 | SW1 is the root bridge
fhrp R1 active | R1 is active in HSRP/VRRP
wifi Laptop1 OFFICE | the laptop joined OFFICE
```

With a password, the solution, the script and the fault list are stored encrypted in the file (PBKDF2 key from the password) and the lab can be edited only after **Lab → Teacher mode** asks for the password. In teacher mode the lab panel shows a **Solution** tab and two more commands are available:

- **Apply the solution** runs the script on the devices and checks the network;
- **Plant a random fault** breaks something in a working network: shuts an interface in use, removes a static route, changes a PC's gateway or mask, moves a port to another VLAN. The description goes to the fault list, visible to the teacher only. Any working network becomes a troubleshooting task this way.

Save the project: the lab is stored in the same `.netemu` file.

## IP telephony

The **IP Phone 7960** is in the palette among the end devices. Connect its **Switch** port to a switch and its **PC** port to a computer: the phone passes the PC's traffic untagged (data VLAN) and tags its own with the voice VLAN it learns from the switch port (`switchport voice vlan` on Cisco, `voice-vlan … enable` on Huawei, `switch-options voip` on Juniper EX). It then gets an address by DHCP and registers with a Cisco CME router (`telephony-service`, address from DHCP option 150 or the gateway). The **Phone** tab has a display with the number and state, a keypad, Call / Answer / Hang up and the call history. During a call the phones exchange RTP packets marked DSCP EF; QoS and ACLs on the path show in the received-packet counter. See the Cisco page, "IP telephony", for a configuration.

## Management services: Syslog, SNMP, NTP

Servers (Windows and Linux) are syslog and NTP servers by default; the services and the log are on the **Services** tab, and on a Linux server the log is `cat /var/log/syslog`. Routers and switches of every vendor send their link messages there, synchronise their clocks by NTP and answer SNMP (`snmpget`, `snmpwalk`, `snmpset` from Linux hosts).

## Appearance

**View → Theme**: light, dark or system. **View → Interface scale**: 100–200 %; window, terminal and canvas fonts grow, which helps on a projector. The choice is remembered.

## Collaboration over the LAN

Several copies of NetEmu on one network can work on the same project.

1. The host chooses **Collaboration → Start a session…** and tells the others the address, the port (47800 by default) and the 4-digit PIN.
2. Participants choose **Collaboration → Join…**, enter the address (`IP` or `IP:port`) and the PIN; their project is replaced by the host's.

Devices, positions, cables, configurations (CLI changes included), notes and the lab are shared. Each participant runs the simulation locally, so packets, ARP tables and DHCP leases are their own. Simultaneous edits of the same device: the last one wins. **Leave / stop** ends the session. The port must be open in the host's firewall.

## Simulation mode

The **Simulation** switch (`Ctrl+M`) stops time. A panel opens on the right:

- **Step** (`F10`): run the next event;
- **Play** (`F5`): play events at the chosen speed;
- **Reset**: drop the packets in transit and clear the event list;
- **Filter**: show only ARP or only ICMP.

Packets travel along cables as envelopes: ARP yellow, ICMP pink. Clicking an envelope, or double-clicking a row of the event list, opens the **PDU contents by layer**: Ethernet, ARP/IPv4, ICMP.

**Realtime** (`Ctrl+R`) returns to normal time.

## Projects

- **File → Save / Open**: projects in `*.netemu` format (JSON). The topology, positions and the configuration of every device, including the startup-config, are saved.
- Ready examples are in the `examples` folder: `multivendor-demo.netemu` and `complex-lab.netemu` (22 devices, redundant paths).

## Language

**View → Language / Язык**: Russian or English. The interface language changes after a restart. CLI output is always English, as on real hardware.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| `F1` | Help |
| `Ctrl+N` / `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` | New / open / save / save as |
| `S` / `C` / `D` / `P` | Tools: select / cable / delete / simple PDU |
| `Del` | Delete selected |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo a canvas edit |
| `N` / `F` | Tools: note / frame |
| `Ctrl+E` | Export the diagram to PNG or SVG |
| `F6` | Check the lab |
| `Ctrl+A` | Select all |
| `Ctrl++` / `Ctrl+-` / `Ctrl+0` / `Ctrl+9` | Zoom in / zoom out / zoom 100% / fit whole topology |
| `Ctrl+R` / `Ctrl+M` | Realtime / simulation |
| `F10` / `F5` | Step / play or pause |
| `Esc`, right mouse button | Cancel the current tool |

## What is supported

Ethernet, MAC-learning switching, 802.1Q VLANs (access, trunk, native VLAN, inter-VLAN routing on subinterfaces), ARP (cache, proxy ARP on routers), IPv4, connected and static routing (longest prefix match, recursive next hop, administrative distance, floating backup routes), OSPFv2 (adjacencies and DR/BDR, multiple areas, ABRs, `O IA`, `O E2`, default route, redistribution, ECMP), BGP-4 (eBGP and iBGP, loopback peering, multihop, next-hop-self, route reflection, best-path selection, AS_PATH loop prevention), IPv6 (addresses, link-local, NDP, ping, static routes, RA and SLAAC, OSPFv3 with LSAs and areas, RIPng, DHCPv6, NAT64), GRE and IPsec tunnels, QoS (classes, DSCP, policing), Syslog, SNMP v2c, NTP, IP telephony (voice VLAN, CME, RTP), routing on switches (SVI, Vlanif, irb, RouterOS VLAN interfaces, EXOS VLAN IP addresses), EtherChannel/LACP, HSRP/VRRP, RIPv2, STP (PVST, RSTP, MSTP: root election, loop blocking, edge ports), loopback interfaces, ACLs (standard and extended, Junos filters, RouterOS firewall), NAT (static, dynamic pools, PAT, port forwarding; ping and traceroute through NAT), TTL, ICMP echo, destination unreachable, time exceeded, ping and traceroute, DHCP (server, relay, APIPA), DNS, HTTP (web server and browser), Telnet and SSH logins between devices, TCP and UDP.

NAT64 translates ICMP echo only. QoS is configured on Cisco and Huawei; CME exists on Cisco only. One IPv4 address per interface.
