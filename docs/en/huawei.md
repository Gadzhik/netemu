# Huawei VRP

[← User guide](guide.md)

Models: the **Huawei AR2220** router (ports `GigabitEthernet0/0/0`–`0/0/2`, short `GE0/0/0`) and the **Huawei S5720-28P** switch (`GigabitEthernet0/0/1`–`0/0/24`). Ports are enabled as soon as the device is added.

## Views (modes)

| Prompt | View | Enter with | Leave with |
|---|---|---|---|
| `<Huawei>` | user view | on login | — |
| `[Huawei]` | system view | `system-view` | `quit`, `return` |
| `[Huawei-GigabitEthernet0/0/0]` | interface view | `interface GigabitEthernet0/0/0` | `quit`, `return` |

`undo` in front of a command undoes it: `undo shutdown`, `undo ip route-static …`, `undo sysname`.

## Example: a router between two networks

```
system-view
sysname AR1
interface GigabitEthernet0/0/0
ip address 10.0.3.2 255.255.255.252
quit
interface GigabitEthernet0/0/1
ip address 192.168.2.1 24
quit
ip route-static 0.0.0.0 0 10.0.3.1
return
save
```

A mask can be given in full (`255.255.255.0`) or as a length (`24`). Static routes in VRP have preference 60. A backup route: `ip route-static 0.0.0.0 0 10.0.4.1 preference 100`.

Check: `display ip interface brief`, `display ip routing-table`, `display arp`, `display current-configuration`, `ping -c 3 192.168.1.10`, `tracert 192.168.1.10`.

## VLANs and trunks

On the S5720, VLANs are created with `vlan batch 10 20` (or `vlan 10`, which enters the VLAN view). Access port: `port link-type access` and `port default vlan N`; the VLAN must already exist. Trunk: `port link-type trunk` and `port trunk allow-pass vlan …`. Note that a VRP trunk passes only VLAN 1 by default: other VLANs must be allowed explicitly.

```switch
system-view
vlan batch 10 20
interface GigabitEthernet0/0/1
port link-type access
port default vlan 10
quit
interface GigabitEthernet0/0/2
port link-type access
port default vlan 20
quit
interface GigabitEthernet0/0/24
port link-type trunk
port trunk allow-pass vlan 10 20
return
```

Trunk native VLAN: `port trunk pvid vlan 99`. Remove a VLAN: `undo port trunk allow-pass vlan 20`. Check: `display vlan`, `display port vlan`, `display mac-address`.

**Inter-VLAN routing.** On the AR2220, create one termination sub-interface per VLAN. Without `arp broadcast enable` the router sends no ARP requests into that VLAN and pings fail, a common lab mistake.

```
system-view
interface GigabitEthernet0/0/0.10
dot1q termination vid 10
ip address 192.168.10.1 24
arp broadcast enable
quit
interface GigabitEthernet0/0/0.20
dot1q termination vid 20
ip address 192.168.20.1 24
arp broadcast enable
return
```

## OSPF

Configure OSPF in the `ospf <process> router-id <id>` view, then in the `area <area>` view with `network <address> <wildcard>`.

```
system-view
interface LoopBack0
ip address 1.1.1.1 32
quit
interface GigabitEthernet0/0/1
ip address 10.0.12.1 30
ospf cost 10
quit
ospf 1 router-id 1.1.1.1
silent-interface GigabitEthernet0/0/2
default-route-advertise
area 0
network 10.0.12.0 0.0.0.3
network 1.1.1.1 0.0.0.0
return
```

Also available: `import-route static`, `import-route direct`, `bandwidth-reference N`, and on interfaces `ospf timer hello`, `ospf timer dead`, `ospf dr-priority`. In VRP, internal OSPF routes have preference 10, external ones (O_ASE) 150, static routes 60. So on Huawei an OSPF route beats a static one, unlike on Cisco.

Check OSPF: `display ospf peer brief`, `display ospf peer`, `display ospf interface`, `display ospf lsdb`, `display ip routing-table`.

## ACLs

Basic ACLs (2000–2999) check the source; advanced ACLs (3000–3999) check the protocol, addresses, ports and ICMP type. Wildcard `0` means a single address. Rules are numbered in steps of 5. Unlike Cisco, `traffic-filter` permits packets that match no rule.

```
system-view
acl number 2000
rule 5 permit source 192.168.1.0 0.0.0.255
quit
acl 3000
rule 5 deny icmp source 192.168.1.11 0 destination 8.8.8.8 0 icmp-type echo
rule 10 deny tcp destination-port eq 80
quit
interface GigabitEthernet0/0/0
traffic-filter inbound acl 3000
return
```

## NAT

NAT is configured on the outside interface. `nat outbound <ACL>` translates to the interface address (Easy IP), `nat outbound <ACL> address-group N` translates to a pool, `nat static` sets a one-to-one mapping and `nat server` forwards a port inside.

```
system-view
acl 2000
rule 5 permit source 192.168.1.0 0.0.0.255
quit
nat address-group 1 203.0.113.4 203.0.113.5
interface GigabitEthernet0/0/1
nat outbound 2000
nat static global 203.0.113.3 inside 192.168.1.10
nat server protocol tcp global 203.0.113.1 8080 inside 192.168.1.20 80
return
```

Check: `display acl all`, `display nat outbound`, `display nat static`, `display nat server`, `display nat address-group`, `display nat session all`.

## BGP

A BGP session runs over TCP port 179 and is routed like any traffic: peering between loopbacks needs a route to the peer's loopback (IGP or static) and a source address; eBGP with a peer that is not on a connected subnet needs multihop; an ACL can block port 179. With a wrong `remote-as` the peer answers with a NOTIFICATION and the session stays down. A route whose next hop is unreachable is not installed: iBGP usually needs next-hop-self. Best path: weight, local-pref, locally originated, AS_PATH length, origin, MED, eBGP over iBGP, IGP metric to the next hop.

```
system-view
bgp 65001
router-id 1.1.1.1
peer 10.0.12.2 as-number 65002
peer 2.2.2.2 as-number 65001
peer 2.2.2.2 connect-interface LoopBack0
ipv4-family unicast
network 192.168.1.0 255.255.255.0
import-route direct
peer 2.2.2.2 next-hop-local
return
```

Also available: `peer X ebgp-max-hop N`, `peer X preferred-value N`, `peer X default-route-advertise`, `peer X reflect-client`, `peer X ignore`. In VRP, BGP routes have preference 255 (EBGP and IBGP).

Check BGP: `display bgp peer`, `display bgp routing-table`, `display ip routing-table`.

## DHCP

In VRP, DHCP is enabled globally (`dhcp enable`) and addresses are handed out only on interfaces with `dhcp select global`.

```
system-view
dhcp enable
ip pool LAN80
gateway-list 192.168.80.1
network 192.168.80.0 mask 255.255.255.0
dns-list 192.168.80.2
excluded-ip-address 192.168.80.2 192.168.80.9
quit
interface GigabitEthernet0/0/2
ip address 192.168.80.1 24
dhcp select global
return
```

A DHCP relay forwards the clients' broadcast requests to a server in another network, adding the interface address (giaddr); the server picks the pool by that address. On Huawei: `dhcp select relay` and `dhcp relay server-ip <address>` on the interface. Check: `display ip pool`, `display ip pool name LAN80 used`.

## RIP

RIPv2: updates every 30 s plus triggered updates, hop-count metric (16 = unreachable), split horizon, a route is invalidated after 180 s without updates. Preference 100.

```
system-view
rip 1
version 2
undo summary
network 10.0.0.0
silent-interface GigabitEthernet0/0/2
return
```

Also: `default-route originate`, `import-route direct|static|ospf 1`. Check: `display rip 1 route`, `display ip routing-table`.

## STP

STP is on by default on switches. The root is the switch with the lowest priority and MAC; redundant links are blocked (Alternate role). Ports towards PCs and routers act as edge ports and forward at once. The default mode is MSTP (fast convergence).

```switch
system-view
stp mode rstp
stp priority 4096
interface GigabitEthernet0/0/1
stp edged-port enable
return
```

Also: `stp root primary|secondary`, `stp cost N`, `stp disable`. Check: `display stp brief`, `display stp`.

## IPv6

IPv6: global and link-local (EUI-64) addresses, neighbor discovery (NDP), IPv6 ping, static routes, router advertisements (RA) and address autoconfiguration on PCs (SLAAC). IPv6 is switched on globally (`ipv6`) and on every interface (`ipv6 enable`). RAs are not sent by default: SLAAC needs `undo ipv6 nd ra halt`.

```
system-view
ipv6
interface GigabitEthernet0/0/0
ipv6 enable
ipv6 address 2001:db8:1::1 64
undo ipv6 nd ra halt
quit
ipv6 route-static 2001:db8:50:: 48 2001:db8:1::2
return
```

Check: `display ipv6 interface brief`, `display ipv6 routing-table`, `display ipv6 neighbors`, `ping ipv6 2001:db8:1::10`.

## Routing on the switch (Vlanif)

The S5720 routes between `Vlanif` interfaces right away; no extra command is needed.

```switch
system-view
vlan batch 10 20
interface Vlanif10
ip address 10.0.10.1 24
interface Vlanif20
ip address 10.0.20.1 24
return
```

The switch also supports `ip route-static`, `ospf`, `rip`, `ip pool` and `dhcp select global` on a Vlanif. Check: `display ip interface brief`, `display ip routing-table`.

## Remote access (Telnet and STelnet)

Devices accept remote logins over Telnet (TCP 23) and SSH (TCP 22). Simulation mode shows the TCP handshake; Telnet packet details reveal even the password, SSH contents are encrypted. The connection opens in the current console and `exit` returns. You can hop on from a remote console. The Telnet and STelnet (SSH) servers are off by default.

```
system-view
telnet server enable
stelnet server enable
aaa
local-user admin password cipher Admin@123
local-user admin service-type telnet ssh
quit
user-interface vty 0 4
authentication-mode aaa
return
```

Connect with `telnet 10.0.0.2` or `stelnet 10.0.0.2` (asks for the user name). Password only: `authentication-mode password` and `set authentication password cipher <password>`.

## EtherChannel (link aggregation)

Several cables between two switches are combined into one logical channel. STP treats it as a single port and blocks nothing, and when one cable fails the traffic moves to the others. The channel forms when the modes at both ends match: `on` only with `on`; LACP `active` with `active` or `passive`; PAgP `desirable` with `desirable` or `auto`. Otherwise the ports stay suspended and carry no traffic.

```switch
system-view
interface Eth-Trunk 1
mode lacp
quit
interface GigabitEthernet0/0/1
eth-trunk 1
interface GigabitEthernet0/0/2
eth-trunk 1
return
```

Eth-Trunk modes: `mode lacp` (LACP) and `mode manual load-balance` (no protocol). Check: `display eth-trunk 1`.

## VRRP (gateway redundancy)

Two routers on one network share a virtual IP address that PCs use as their gateway. The active (HSRP) or master (VRRP) router is elected by priority, then by the higher IP. It answers ARP with the virtual MAC. When it disappears, the backup takes over after the hold time (HSRP 10 s, VRRP about 3.6 s) and sends a gratuitous ARP. By default HSRP does not take the role back (`preempt` is needed), VRRP does.

```
system-view
interface GigabitEthernet0/0/0
ip address 192.168.1.2 24
vrrp vrid 1 virtual-ip 192.168.1.254
vrrp vrid 1 priority 120
return
```

Turn off preemption with `vrrp vrid 1 preempt-mode disable`. Check: `display vrrp brief`.

## OSPFv3, RIPng and DHCPv6

Routers find neighbours with hellos to ff02::5 (OSPFv3) and ff02::9 (RIPng) sent from their link-local addresses and exchange routes to IPv6 networks. The next hop in the table is the neighbour's link-local address. Stateful DHCPv6 leases addresses from a pool; PCs still learn the default gateway from RAs.

```
system-view
ipv6
ospfv3 1
router-id 2.2.2.2
quit
interface GigabitEthernet0/0/1
ipv6 enable
ipv6 address 2001:db8:12::2/64
ospfv3 1 area 0.0.0.0
quit
dhcpv6 pool LAN6
address prefix 2001:db8:2::/64
quit
interface GigabitEthernet0/0/0
ipv6 enable
ipv6 address 2001:db8:2::1/64
dhcpv6 server LAN6
return
```

RIPng: `ripng 1` and `ripng 1 enable` on the interface. Check: `display ospfv3 peer`, `display ipv6 routing-table`, `display dhcpv6 pool`.

## Port security and DHCP snooping

**Port security** limits the number of MAC addresses on an access port. The first addresses are remembered as allowed; a foreign MAC is a violation. `shutdown` (the default) puts the port into err-disabled, and shutting the port down and up again brings it back. `restrict` and `protect` just drop the frames. **DHCP snooping** passes DHCP server replies only from trusted ports, so a rogue server on the network hands out nothing, and keeps a table of the leased addresses.

```switch
system-view
interface GigabitEthernet0/0/1
port link-type access
port-security enable
port-security max-mac-num 2
port-security protect-action error-down
quit
dhcp snooping enable vlan 1
interface GigabitEthernet0/0/24
dhcp snooping trusted
return
```

Check: `display port-security`, `display dhcp snooping user-bind all`. Bring a port back with `shutdown`, then `undo shutdown`.

## GRE, QoS, NAT64, Syslog/SNMP/NTP

A GRE tunnel is the `Tunnel0/0/0` interface with `tunnel-protocol gre`, `source` and `destination`. QoS is a classifier, a behavior and a traffic policy that binds them; the policy is applied with `traffic-policy … inbound|outbound` on an interface. NAT64 is enabled globally and on the IPv6-side interface.

```
system-view
interface Tunnel0/0/0
tunnel-protocol gre
source 203.0.113.2
destination 198.51.100.2
ip address 10.255.0.1 30
quit
traffic classifier PING operator or
if-match protocol icmp
quit
traffic behavior EF
remark dscp ef
quit
traffic policy MARK
classifier PING behavior EF
quit
interface GigabitEthernet0/0/0
traffic-policy MARK inbound
quit
nat64 enable
nat64 prefix 64:ff9b:: prefix-length 96
info-center loghost 192.168.1.100
snmp-agent
snmp-agent community read public
snmp-agent sys-info location Server-room-1
ntp-service unicast-server 192.168.1.100
return
```

Voice VLAN on a switch: `voice-vlan 20 enable` on the access port.

Check: `display traffic policy statistics`, `display nat64 session all`, `display logbuffer`, `display ntp-service status`, `display snmp-agent community`, `display ospfv3 lsdb`.

## All commands: AR2220 router

<!-- BEGIN GENERATED COMMANDS router -->
#### User view — `<Huawei>`

| Command | Description |
|---|---|
| `display acl all` | All ACLs |
| `display acl INTEGER<2000-3999>` | ACL number |
| `display arp` | Display ARP entries |
| `display bgp peer` | Peers |
| `display bgp routing-table` | BGP routing table |
| `display clock` | Clock status and configuration information |
| `display current-configuration` | Current configuration |
| `display interface` | Interface status and configuration information |
| `display interface brief` | Brief information of status and configuration |
| `display interface IFACE` | Interface name |
| `display ip interface brief` | Brief information |
| `display ip routing-table` | Information about routing table |
| `display ip pool` | IP address pool |
| `display ip pool name STRING<1-64>` | Pool name |
| `display ip pool name STRING<1-64> used` | Used addresses |
| `display ipv6 interface brief` | Brief |
| `display ipv6 routing-table` | IPv6 routing table |
| `display ipv6 neighbors` | IPv6 neighbors |
| `display nat outbound` | Outbound NAT |
| `display nat static` | Static NAT |
| `display nat server` | NAT server |
| `display nat address-group` | Address group |
| `display nat session` | NAT sessions |
| `display nat session all` | All sessions |
| `display ospf` | OSPF information |
| `display ospf peer` | Specify a neighbor router |
| `display ospf peer brief` | Brief information of neighbor |
| `display ospf interface` | Interface information |
| `display ospf lsdb` | Link state database |
| `display ospf routing` | OSPF routing table |
| `display rip INTEGER<1-65535>` | Process ID |
| `display rip INTEGER<1-65535> route` | Routing table |
| `display saved-configuration` | Saved configuration |
| `display version` | Display version information |
| `display nat64 session all` | All |
| `display logbuffer` | Log buffer |
| `display info-center` | Information center |
| `display ntp-service status` | Status |
| `display ntp-service sessions` | Sessions |
| `display snmp-agent community` | Community |
| `display snmp-agent sys-info` | System information |
| `display traffic policy statistics` | Statistics |
| `display dhcpv6 pool` | Pools |
| `display ospfv3 lsdb` | Link state database |
| `display ospfv3 peer` | Neighbours |
| `display ripng` | RIPng |
| `display vrrp` | VRRP information |
| `display vrrp brief` | Brief |
| `ping ipv6 X:X:X:X::X` | IPv6 address |
| `ping -c INTEGER<1-4294967295> A.B.C.D` | IP address of remote system |
| `ping A.B.C.D` | IP address of remote system |
| `quit` | Exit from current mode and enter prior mode |
| `reset arp` | Reset ARP |
| `reset arp all` | All ARP entries |
| `return` | Enter the privileged mode |
| `save` | Save current configuration |
| `system-view` | Enter the system view |
| `tracert ipv6 X:X:X:X::X` | IPv6 address of the remote system |
| `tracert A.B.C.D` | IP address of the remote system |
| `telnet A.B.C.D` | IP address of the remote system |
| `telnet server enable` | Enable the telnet server |
| `stelnet A.B.C.D` | IP address of the remote system |
| `stelnet server enable` | Enable the STelnet server |

#### System view (system-view) — `[Huawei]`

| Command | Description |
|---|---|
| `[undo] acl number INTEGER<2000-3999>` | ACL number |
| `[undo] acl INTEGER<2000-3999>` | ACL number |
| `[undo] bgp INTEGER<1-4294967295>` | AS number |
| `[undo] dhcp enable` | Enable DHCP |
| `display acl all` | All ACLs |
| `display acl INTEGER<2000-3999>` | ACL number |
| `display arp` | Display ARP entries |
| `display bgp peer` | Peers |
| `display bgp routing-table` | BGP routing table |
| `display clock` | Clock status and configuration information |
| `display current-configuration` | Current configuration |
| `display interface` | Interface status and configuration information |
| `display interface brief` | Brief information of status and configuration |
| `display interface IFACE` | Interface name |
| `display ip interface brief` | Brief information |
| `display ip routing-table` | Information about routing table |
| `display ip pool` | IP address pool |
| `display ip pool name STRING<1-64>` | Pool name |
| `display ip pool name STRING<1-64> used` | Used addresses |
| `display ipv6 interface brief` | Brief |
| `display ipv6 routing-table` | IPv6 routing table |
| `display ipv6 neighbors` | IPv6 neighbors |
| `display nat outbound` | Outbound NAT |
| `display nat static` | Static NAT |
| `display nat server` | NAT server |
| `display nat address-group` | Address group |
| `display nat session` | NAT sessions |
| `display nat session all` | All sessions |
| `display ospf` | OSPF information |
| `display ospf peer` | Specify a neighbor router |
| `display ospf peer brief` | Brief information of neighbor |
| `display ospf interface` | Interface information |
| `display ospf lsdb` | Link state database |
| `display ospf routing` | OSPF routing table |
| `display rip INTEGER<1-65535>` | Process ID |
| `display rip INTEGER<1-65535> route` | Routing table |
| `display saved-configuration` | Saved configuration |
| `display version` | Display version information |
| `display nat64 session all` | All |
| `display logbuffer` | Log buffer |
| `display info-center` | Information center |
| `display ntp-service status` | Status |
| `display ntp-service sessions` | Sessions |
| `display snmp-agent community` | Community |
| `display snmp-agent sys-info` | System information |
| `display traffic policy statistics` | Statistics |
| `display dhcpv6 pool` | Pools |
| `display ospfv3 lsdb` | Link state database |
| `display ospfv3 peer` | Neighbours |
| `display ripng` | RIPng |
| `display vrrp` | VRRP information |
| `display vrrp brief` | Brief |
| `[undo] interface IFACE` | Interface name |
| `[undo] interface IFACE.N` | Sub-interface number |
| `[undo] interface <0-2147483647>` | LoopBack interface |
| `[undo] interface Tunnel <slot/card/port>` | Tunnel interface |
| `[undo] ip route-static A.B.C.D MASK/LEN` | IP address mask or mask length |
| `[undo] ip route-static A.B.C.D MASK/LEN A.B.C.D` | Next hop IP address |
| `[undo] ip route-static A.B.C.D MASK/LEN A.B.C.D preference <1-255>` | Preference value |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE` | Outbound interface |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE A.B.C.D` | Next hop IP address |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE A.B.C.D preference <1-255>` | Preference value |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE preference <1-255>` | Preference value |
| `[undo] ip pool STRING<1-64>` | Pool name |
| `[undo] ipv6` | IPv6 |
| `[undo] ipv6 route-static X:X:X:X::X INTEGER<0-128> X:X:X:X::X` | Next hop |
| `[undo] nat address-group INTEGER<0-4095>` | Index of address-group |
| `[undo] nat address-group INTEGER<0-4095> A.B.C.D A.B.C.D` | End address |
| `[undo] ospf` | OSPF protocol |
| `[undo] ospf INTEGER<1-65535>` | Process ID |
| `[undo] ospf INTEGER<1-65535> router-id A.B.C.D` | Router identifier, in IP address format |
| `[undo] ospf router-id A.B.C.D` | Router identifier, in IP address format |
| `ping ipv6 X:X:X:X::X` | IPv6 address |
| `ping -c INTEGER<1-4294967295> A.B.C.D` | IP address of remote system |
| `ping A.B.C.D` | IP address of remote system |
| `quit` | Exit from current mode and enter prior mode |
| `return` | Enter the privileged mode |
| `[undo] rip` | RIP |
| `[undo] rip INTEGER<1-65535>` | Process ID |
| `[undo] sysname` | Set the host name |
| `[undo] sysname TEXT` | Host name (1 to 246 characters) |
| `tracert ipv6 X:X:X:X::X` | IPv6 address of the remote system |
| `tracert A.B.C.D` | IP address of the remote system |
| `[undo] telnet A.B.C.D` | IP address of the remote system |
| `[undo] telnet server enable` | Enable the telnet server |
| `[undo] stelnet A.B.C.D` | IP address of the remote system |
| `[undo] stelnet server enable` | Enable the STelnet server |
| `user-interface vty INTEGER<0-4>` | First number |
| `user-interface vty INTEGER<0-4> INTEGER<0-4>` | Last number |
| `aaa` | AAA view |
| `ssh user STRING authentication-type password` | Password |
| `ssh user STRING service-type stelnet` | STelnet |
| `[undo] ipsec proposal STRING<1-15>` | Name |
| `[undo] ipsec profile STRING<1-15>` | Name |
| `[undo] ike peer STRING<1-15>` | Name |
| `[undo] ospfv3 INTEGER<1-65535>` | Process ID |
| `[undo] ripng INTEGER<1-65535>` | Process ID |
| `[undo] dhcpv6 pool STRING<1-31>` | Pool name |
| `[undo] nat64 enable` | Enable NAT64 |
| `[undo] nat64 prefix X:X:X:X::X prefix-length INTEGER<32-96>` | Length |
| `[undo] info-center loghost A.B.C.D` | IP address |
| `[undo] info-center enable` | Enable the information center |
| `[undo] snmp-agent` | SNMP agent |
| `[undo] snmp-agent community read STRING<1-32>` | Community name |
| `[undo] snmp-agent community write STRING<1-32>` | Community name |
| `[undo] snmp-agent sys-info location` | Location |
| `[undo] snmp-agent sys-info location LINE` | Location |
| `[undo] snmp-agent sys-info contact` | Contact |
| `[undo] snmp-agent sys-info contact LINE` | Contact |
| `[undo] snmp-agent sys-info version LINE` | v1/v2c/v3/all |
| `[undo] ntp-service unicast-server A.B.C.D` | IP address |
| `[undo] ntp-service refclock-master` | Local reference clock |
| `[undo] ntp-service refclock-master INTEGER<1-15>` | Stratum |
| `[undo] ntp-service enable` | Enable NTP |
| `traffic classifier STRING<1-31>` | Classifier name |
| `traffic classifier STRING<1-31> operator or` | OR |
| `traffic classifier STRING<1-31> operator and` | AND |
| `traffic behavior STRING<1-31>` | Behavior name |
| `traffic policy STRING<1-31>` | Policy name |

#### Interface view (interface …) — `[Huawei-GigabitEthernet0/0/1]`

| Command | Description |
|---|---|
| `[undo] arp broadcast enable` | Enable ARP broadcast on the termination sub-interface |
| `[undo] description` | Describe the interface |
| `[undo] description LINE` | Up to 242 characters |
| `[undo] dhcp select global` | Allocate addresses from a global pool |
| `[undo] dhcp select relay` | Act as a DHCP relay |
| `[undo] dhcp relay server-ip A.B.C.D` | Server IP address |
| `display acl all` | All ACLs |
| `display acl INTEGER<2000-3999>` | ACL number |
| `display arp` | Display ARP entries |
| `display bgp peer` | Peers |
| `display bgp routing-table` | BGP routing table |
| `display clock` | Clock status and configuration information |
| `display current-configuration` | Current configuration |
| `display interface` | Interface status and configuration information |
| `display interface brief` | Brief information of status and configuration |
| `display interface IFACE` | Interface name |
| `display ip interface brief` | Brief information |
| `display ip routing-table` | Information about routing table |
| `display ip pool` | IP address pool |
| `display ip pool name STRING<1-64>` | Pool name |
| `display ip pool name STRING<1-64> used` | Used addresses |
| `display ipv6 interface brief` | Brief |
| `display ipv6 routing-table` | IPv6 routing table |
| `display ipv6 neighbors` | IPv6 neighbors |
| `display nat outbound` | Outbound NAT |
| `display nat static` | Static NAT |
| `display nat server` | NAT server |
| `display nat address-group` | Address group |
| `display nat session` | NAT sessions |
| `display nat session all` | All sessions |
| `display ospf` | OSPF information |
| `display ospf peer` | Specify a neighbor router |
| `display ospf peer brief` | Brief information of neighbor |
| `display ospf interface` | Interface information |
| `display ospf lsdb` | Link state database |
| `display ospf routing` | OSPF routing table |
| `display rip INTEGER<1-65535>` | Process ID |
| `display rip INTEGER<1-65535> route` | Routing table |
| `display saved-configuration` | Saved configuration |
| `display version` | Display version information |
| `display nat64 session all` | All |
| `display logbuffer` | Log buffer |
| `display info-center` | Information center |
| `display ntp-service status` | Status |
| `display ntp-service sessions` | Sessions |
| `display snmp-agent community` | Community |
| `display snmp-agent sys-info` | System information |
| `display traffic policy statistics` | Statistics |
| `display dhcpv6 pool` | Pools |
| `display ospfv3 lsdb` | Link state database |
| `display ospfv3 peer` | Neighbours |
| `display ripng` | RIPng |
| `display vrrp` | VRRP information |
| `display vrrp brief` | Brief |
| `[undo] dot1q termination vid` | VLAN ID |
| `[undo] dot1q termination vid INTEGER<1-4094>` | VLAN ID |
| `[undo] interface IFACE` | Interface name |
| `[undo] interface IFACE.N` | Sub-interface number |
| `[undo] interface <0-2147483647>` | LoopBack interface |
| `[undo] interface Tunnel <slot/card/port>` | Tunnel interface |
| `ip address` | Configure the IP address of an interface |
| `ip address A.B.C.D MASK/LEN` | IP mask or mask length |
| `[undo] ipv6 enable` | Enable IPv6 on the interface |
| `[undo] ipv6 address X:X:X:X::X/<0-128>` | IPv6 address/prefix length |
| `[undo] ipv6 address X:X:X:X::X INTEGER<1-128>` | Prefix length |
| `[undo] ipv6 address auto link-local` | Link-local |
| `[undo] ipv6 nd ra halt` | Stop sending RAs |
| `[undo] nat outbound INTEGER<2000-3999>` | ACL number |
| `[undo] nat outbound INTEGER<2000-3999> address-group INTEGER<0-4095>` | Index of address-group |
| `[undo] nat outbound INTEGER<2000-3999> address-group INTEGER<0-4095> no-pat` | Not use PAT |
| `[undo] nat static global A.B.C.D inside A.B.C.D` | Inside IP address |
| `[undo] nat server protocol tcp global A.B.C.D INTEGER<1-65535> inside A.B.C.D INTEGER<1-65535>` | Inside port |
| `[undo] nat server protocol udp global A.B.C.D INTEGER<1-65535> inside A.B.C.D INTEGER<1-65535>` | Inside port |
| `[undo] ospf cost` | Interface cost |
| `[undo] ospf cost INTEGER<1-65535>` | Cost value |
| `[undo] ospf timer hello` | Specify interval at which hello packet sends |
| `[undo] ospf timer hello INTEGER<1-65535>` | Second(s) |
| `[undo] ospf timer dead` | Specify interval after which a neighbor is declared dead |
| `[undo] ospf timer dead INTEGER<1-235926000>` | Second(s) |
| `[undo] ospf dr-priority` | Router priority |
| `[undo] ospf dr-priority INTEGER<0-255>` | Router priority value |
| `ping ipv6 X:X:X:X::X` | IPv6 address |
| `ping -c INTEGER<1-4294967295> A.B.C.D` | IP address of remote system |
| `ping A.B.C.D` | IP address of remote system |
| `quit` | Exit from current mode and enter prior mode |
| `return` | Enter the privileged mode |
| `[undo] shutdown` | Shutdown the interface |
| `[undo] traffic-filter inbound acl INTEGER<2000-3999>` | ACL number |
| `[undo] traffic-filter outbound acl INTEGER<2000-3999>` | ACL number |
| `[undo] ipsec profile STRING` | Profile |
| `tunnel-protocol gre` | GRE |
| `tunnel-protocol ipsec` | IPsec |
| `tunnel-protocol none` | None |
| `source A.B.C.D` | IP address |
| `source IFACE` | Interface |
| `destination A.B.C.D` | IP address |
| `[undo] vrrp vrid INTEGER<1-255>` | VRID |
| `[undo] vrrp vrid INTEGER<1-255> virtual-ip A.B.C.D` | IP address |
| `[undo] vrrp vrid INTEGER<1-255> priority INTEGER<1-254>` | Priority |
| `[undo] vrrp vrid INTEGER<1-255> preempt-mode disable` | Disable preemption |
| `[undo] ospfv3 INTEGER<1-65535> area IPADDR<X.X.X.X>/INTEGER` | Area ID |
| `[undo] ospfv3 cost INTEGER<1-65535>` | Cost |
| `[undo] ripng INTEGER<1-65535> enable` | Enable RIPng on the interface |
| `[undo] nat64 enable` | Enable NAT64 on the interface |
| `[undo] traffic-policy STRING inbound` | Inbound |
| `[undo] traffic-policy STRING outbound` | Outbound |
| `[undo] dhcpv6 server STRING<1-31>` | Pool name |
<!-- END GENERATED COMMANDS -->

## All commands: S5720 switch

<!-- BEGIN GENERATED COMMANDS switch -->
#### User view — `<HUAWEI>`

| Command | Description |
|---|---|
| `display arp` | Display ARP entries |
| `display clock` | Clock status and configuration information |
| `display current-configuration` | Current configuration |
| `display interface` | Interface status and configuration information |
| `display interface brief` | Brief information of status and configuration |
| `display interface IFACE` | Interface name |
| `display ip interface brief` | Brief information |
| `display ip routing-table` | Information about routing table |
| `display ip pool` | IP address pool |
| `display ip pool name STRING<1-64>` | Pool name |
| `display ip pool name STRING<1-64> used` | Used addresses |
| `display mac-address` | MAC address table |
| `display ospf` | OSPF information |
| `display ospf peer` | Specify a neighbor router |
| `display ospf peer brief` | Brief information of neighbor |
| `display ospf interface` | Interface information |
| `display ospf lsdb` | Link state database |
| `display ospf routing` | OSPF routing table |
| `display port vlan` | VLAN information of port |
| `display saved-configuration` | Saved configuration |
| `display stp` | Spanning tree |
| `display stp brief` | Brief information |
| `display version` | Display version information |
| `display vlan` | VLAN information |
| `display vlan INTEGER<1-4094>` | VLAN ID |
| `display eth-trunk` | Eth-Trunk information |
| `display eth-trunk INTEGER<0-63>` | Eth-Trunk ID |
| `display port-security` | Port security |
| `display dhcp snooping` | DHCP snooping |
| `display dhcp snooping user-bind all` | All |
| `display logbuffer` | Log buffer |
| `display info-center` | Information center |
| `display ntp-service status` | Status |
| `display ntp-service sessions` | Sessions |
| `display snmp-agent community` | Community |
| `display snmp-agent sys-info` | System information |
| `display dhcpv6 pool` | Pools |
| `display ospfv3 lsdb` | Link state database |
| `display ospfv3 peer` | Neighbours |
| `display ripng` | RIPng |
| `display vrrp` | VRRP information |
| `display vrrp brief` | Brief |
| `ping ipv6 X:X:X:X::X` | IPv6 address |
| `ping -c INTEGER<1-4294967295> A.B.C.D` | IP address of remote system |
| `ping A.B.C.D` | IP address of remote system |
| `quit` | Exit from current mode and enter prior mode |
| `reset arp` | Reset ARP |
| `reset arp all` | All ARP entries |
| `reset mac-address` | MAC address table |
| `return` | Enter the privileged mode |
| `save` | Save current configuration |
| `system-view` | Enter the system view |
| `tracert ipv6 X:X:X:X::X` | IPv6 address of the remote system |
| `tracert A.B.C.D` | IP address of the remote system |
| `telnet A.B.C.D` | IP address of the remote system |
| `telnet server enable` | Enable the telnet server |
| `stelnet A.B.C.D` | IP address of the remote system |
| `stelnet server enable` | Enable the STelnet server |

#### System view (system-view) — `[HUAWEI]`

| Command | Description |
|---|---|
| `[undo] acl number INTEGER<2000-3999>` | ACL number |
| `[undo] acl INTEGER<2000-3999>` | ACL number |
| `[undo] bgp INTEGER<1-4294967295>` | AS number |
| `[undo] dhcp enable` | Enable DHCP |
| `[undo] dhcp snooping enable` | Enable DHCP snooping |
| `[undo] dhcp snooping enable vlan WORD` | VLAN list |
| `display arp` | Display ARP entries |
| `display clock` | Clock status and configuration information |
| `display current-configuration` | Current configuration |
| `display interface` | Interface status and configuration information |
| `display interface brief` | Brief information of status and configuration |
| `display interface IFACE` | Interface name |
| `display ip interface brief` | Brief information |
| `display ip routing-table` | Information about routing table |
| `display ip pool` | IP address pool |
| `display ip pool name STRING<1-64>` | Pool name |
| `display ip pool name STRING<1-64> used` | Used addresses |
| `display mac-address` | MAC address table |
| `display ospf` | OSPF information |
| `display ospf peer` | Specify a neighbor router |
| `display ospf peer brief` | Brief information of neighbor |
| `display ospf interface` | Interface information |
| `display ospf lsdb` | Link state database |
| `display ospf routing` | OSPF routing table |
| `display port vlan` | VLAN information of port |
| `display saved-configuration` | Saved configuration |
| `display stp` | Spanning tree |
| `display stp brief` | Brief information |
| `display version` | Display version information |
| `display vlan` | VLAN information |
| `display vlan INTEGER<1-4094>` | VLAN ID |
| `display eth-trunk` | Eth-Trunk information |
| `display eth-trunk INTEGER<0-63>` | Eth-Trunk ID |
| `display port-security` | Port security |
| `display dhcp snooping` | DHCP snooping |
| `display dhcp snooping user-bind all` | All |
| `display logbuffer` | Log buffer |
| `display info-center` | Information center |
| `display ntp-service status` | Status |
| `display ntp-service sessions` | Sessions |
| `display snmp-agent community` | Community |
| `display snmp-agent sys-info` | System information |
| `display dhcpv6 pool` | Pools |
| `display ospfv3 lsdb` | Link state database |
| `display ospfv3 peer` | Neighbours |
| `display ripng` | RIPng |
| `display vrrp` | VRRP information |
| `display vrrp brief` | Brief |
| `[undo] interface IFACE` | Interface name |
| `[undo] interface Vlanif <1-4094>` | VLAN interface |
| `[undo] interface <0-2147483647>` | LoopBack interface |
| `[undo] interface Eth-Trunk <0-63>` | Ethernet trunk interface |
| `[undo] ip route-static A.B.C.D MASK/LEN` | IP address mask or mask length |
| `[undo] ip route-static A.B.C.D MASK/LEN A.B.C.D` | Next hop IP address |
| `[undo] ip route-static A.B.C.D MASK/LEN A.B.C.D preference <1-255>` | Preference value |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE` | Outbound interface |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE A.B.C.D` | Next hop IP address |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE A.B.C.D preference <1-255>` | Preference value |
| `[undo] ip route-static A.B.C.D MASK/LEN IFACE preference <1-255>` | Preference value |
| `[undo] ip pool STRING<1-64>` | Pool name |
| `[undo] ipv6` | IPv6 |
| `[undo] ipv6 route-static X:X:X:X::X INTEGER<0-128> X:X:X:X::X` | Next hop |
| `[undo] nat address-group INTEGER<0-4095>` | Index of address-group |
| `[undo] nat address-group INTEGER<0-4095> A.B.C.D A.B.C.D` | End address |
| `[undo] ospf` | OSPF protocol |
| `[undo] ospf INTEGER<1-65535>` | Process ID |
| `[undo] ospf INTEGER<1-65535> router-id A.B.C.D` | Router identifier, in IP address format |
| `[undo] ospf router-id A.B.C.D` | Router identifier, in IP address format |
| `ping ipv6 X:X:X:X::X` | IPv6 address |
| `ping -c INTEGER<1-4294967295> A.B.C.D` | IP address of remote system |
| `ping A.B.C.D` | IP address of remote system |
| `quit` | Exit from current mode and enter prior mode |
| `return` | Enter the privileged mode |
| `[undo] rip` | RIP |
| `[undo] rip INTEGER<1-65535>` | Process ID |
| `[undo] stp enable` | Enable STP |
| `[undo] stp disable` | Disable STP |
| `[undo] stp mode stp` | STP mode |
| `[undo] stp mode rstp` | RSTP mode |
| `[undo] stp mode mstp` | MSTP mode |
| `[undo] stp priority INTEGER<0-61440>` | Priority in steps of 4096 |
| `[undo] stp root primary` | Primary root |
| `[undo] stp root secondary` | Secondary root |
| `[undo] sysname` | Set the host name |
| `[undo] sysname TEXT` | Host name (1 to 246 characters) |
| `tracert ipv6 X:X:X:X::X` | IPv6 address of the remote system |
| `tracert A.B.C.D` | IP address of the remote system |
| `[undo] vlan batch INTEGER<1-4094>` | VLAN IDs, e.g. 10 20 30 to 40 |
| `[undo] vlan INTEGER<1-4094>` | VLAN ID |
| `[undo] telnet A.B.C.D` | IP address of the remote system |
| `[undo] telnet server enable` | Enable the telnet server |
| `[undo] stelnet A.B.C.D` | IP address of the remote system |
| `[undo] stelnet server enable` | Enable the STelnet server |
| `user-interface vty INTEGER<0-4>` | First number |
| `user-interface vty INTEGER<0-4> INTEGER<0-4>` | Last number |
| `aaa` | AAA view |
| `ssh user STRING authentication-type password` | Password |
| `ssh user STRING service-type stelnet` | STelnet |
| `[undo] ospfv3 INTEGER<1-65535>` | Process ID |
| `[undo] ripng INTEGER<1-65535>` | Process ID |
| `[undo] dhcpv6 pool STRING<1-31>` | Pool name |
| `[undo] info-center loghost A.B.C.D` | IP address |
| `[undo] info-center enable` | Enable the information center |
| `[undo] snmp-agent` | SNMP agent |
| `[undo] snmp-agent community read STRING<1-32>` | Community name |
| `[undo] snmp-agent community write STRING<1-32>` | Community name |
| `[undo] snmp-agent sys-info location` | Location |
| `[undo] snmp-agent sys-info location LINE` | Location |
| `[undo] snmp-agent sys-info contact` | Contact |
| `[undo] snmp-agent sys-info contact LINE` | Contact |
| `[undo] snmp-agent sys-info version LINE` | v1/v2c/v3/all |
| `[undo] ntp-service unicast-server A.B.C.D` | IP address |
| `[undo] ntp-service refclock-master` | Local reference clock |
| `[undo] ntp-service refclock-master INTEGER<1-15>` | Stratum |
| `[undo] ntp-service enable` | Enable NTP |

#### Interface view (interface …) — `[HUAWEI-GigabitEthernet0/0/1]`

| Command | Description |
|---|---|
| `[undo] arp broadcast enable` | Enable ARP broadcast on the termination sub-interface |
| `[undo] description` | Describe the interface |
| `[undo] description LINE` | Up to 242 characters |
| `[undo] dhcp select global` | Allocate addresses from a global pool |
| `[undo] dhcp select relay` | Act as a DHCP relay |
| `[undo] dhcp relay server-ip A.B.C.D` | Server IP address |
| `[undo] dhcp snooping trusted` | Trusted interface |
| `display arp` | Display ARP entries |
| `display clock` | Clock status and configuration information |
| `display current-configuration` | Current configuration |
| `display interface` | Interface status and configuration information |
| `display interface brief` | Brief information of status and configuration |
| `display interface IFACE` | Interface name |
| `display ip interface brief` | Brief information |
| `display ip routing-table` | Information about routing table |
| `display ip pool` | IP address pool |
| `display ip pool name STRING<1-64>` | Pool name |
| `display ip pool name STRING<1-64> used` | Used addresses |
| `display mac-address` | MAC address table |
| `display ospf` | OSPF information |
| `display ospf peer` | Specify a neighbor router |
| `display ospf peer brief` | Brief information of neighbor |
| `display ospf interface` | Interface information |
| `display ospf lsdb` | Link state database |
| `display ospf routing` | OSPF routing table |
| `display port vlan` | VLAN information of port |
| `display saved-configuration` | Saved configuration |
| `display stp` | Spanning tree |
| `display stp brief` | Brief information |
| `display version` | Display version information |
| `display vlan` | VLAN information |
| `display vlan INTEGER<1-4094>` | VLAN ID |
| `display eth-trunk` | Eth-Trunk information |
| `display eth-trunk INTEGER<0-63>` | Eth-Trunk ID |
| `display port-security` | Port security |
| `display dhcp snooping` | DHCP snooping |
| `display dhcp snooping user-bind all` | All |
| `display logbuffer` | Log buffer |
| `display info-center` | Information center |
| `display ntp-service status` | Status |
| `display ntp-service sessions` | Sessions |
| `display snmp-agent community` | Community |
| `display snmp-agent sys-info` | System information |
| `display dhcpv6 pool` | Pools |
| `display ospfv3 lsdb` | Link state database |
| `display ospfv3 peer` | Neighbours |
| `display ripng` | RIPng |
| `display vrrp` | VRRP information |
| `display vrrp brief` | Brief |
| `[undo] dot1q termination vid` | VLAN ID |
| `[undo] dot1q termination vid INTEGER<1-4094>` | VLAN ID |
| `[undo] interface IFACE` | Interface name |
| `[undo] interface Vlanif <1-4094>` | VLAN interface |
| `[undo] interface <0-2147483647>` | LoopBack interface |
| `[undo] interface Eth-Trunk <0-63>` | Ethernet trunk interface |
| `ip address` | Configure the IP address of an interface |
| `ip address A.B.C.D MASK/LEN` | IP mask or mask length |
| `[undo] ipv6 enable` | Enable IPv6 on the interface |
| `[undo] ipv6 address X:X:X:X::X/<0-128>` | IPv6 address/prefix length |
| `[undo] ipv6 address X:X:X:X::X INTEGER<1-128>` | Prefix length |
| `[undo] ipv6 address auto link-local` | Link-local |
| `[undo] ipv6 nd ra halt` | Stop sending RAs |
| `[undo] nat outbound INTEGER<2000-3999>` | ACL number |
| `[undo] nat outbound INTEGER<2000-3999> address-group INTEGER<0-4095>` | Index of address-group |
| `[undo] nat outbound INTEGER<2000-3999> address-group INTEGER<0-4095> no-pat` | Not use PAT |
| `[undo] nat static global A.B.C.D inside A.B.C.D` | Inside IP address |
| `[undo] nat server protocol tcp global A.B.C.D INTEGER<1-65535> inside A.B.C.D INTEGER<1-65535>` | Inside port |
| `[undo] nat server protocol udp global A.B.C.D INTEGER<1-65535> inside A.B.C.D INTEGER<1-65535>` | Inside port |
| `[undo] ospf cost` | Interface cost |
| `[undo] ospf cost INTEGER<1-65535>` | Cost value |
| `[undo] ospf timer hello` | Specify interval at which hello packet sends |
| `[undo] ospf timer hello INTEGER<1-65535>` | Second(s) |
| `[undo] ospf timer dead` | Specify interval after which a neighbor is declared dead |
| `[undo] ospf timer dead INTEGER<1-235926000>` | Second(s) |
| `[undo] ospf dr-priority` | Router priority |
| `[undo] ospf dr-priority INTEGER<0-255>` | Router priority value |
| `ping ipv6 X:X:X:X::X` | IPv6 address |
| `ping -c INTEGER<1-4294967295> A.B.C.D` | IP address of remote system |
| `ping A.B.C.D` | IP address of remote system |
| `[undo] port link-type` | Link type |
| `[undo] port link-type access` | Access port |
| `[undo] port link-type trunk` | Trunk port |
| `[undo] port default vlan` | Virtual LAN |
| `[undo] port default vlan INTEGER<1-4094>` | VLAN ID |
| `[undo] port trunk allow-pass vlan all` | All |
| `[undo] port trunk allow-pass vlan INTEGER<1-4094>` | VLAN IDs, e.g. 10 20 30 to 40 |
| `[undo] port trunk pvid vlan` | Virtual LAN |
| `[undo] port trunk pvid vlan INTEGER<1-4094>` | VLAN ID |
| `quit` | Exit from current mode and enter prior mode |
| `return` | Enter the privileged mode |
| `[undo] shutdown` | Shutdown the interface |
| `[undo] stp edged-port enable` | Enable |
| `[undo] stp cost INTEGER<1-200000000>` | Cost |
| `[undo] traffic-filter inbound acl INTEGER<2000-3999>` | ACL number |
| `[undo] traffic-filter outbound acl INTEGER<2000-3999>` | ACL number |
| `[undo] eth-trunk` | Add the interface to an Eth-Trunk |
| `[undo] eth-trunk INTEGER<0-63>` | Eth-Trunk ID |
| `mode lacp` | LACP mode |
| `mode lacp-static` | Static LACP mode |
| `mode manual` | Manual load balance mode |
| `mode manual load-balance` | Load balance |
| `[undo] port-security enable` | Enable port security |
| `[undo] port-security max-mac-num INTEGER<1-4096>` | Number |
| `[undo] port-security protect-action protect` | protect |
| `[undo] port-security protect-action restrict` | restrict |
| `[undo] port-security protect-action error-down` | error-down |
| `[undo] port-security mac-address sticky` | Sticky MAC |
| `[undo] vrrp vrid INTEGER<1-255>` | VRID |
| `[undo] vrrp vrid INTEGER<1-255> virtual-ip A.B.C.D` | IP address |
| `[undo] vrrp vrid INTEGER<1-255> priority INTEGER<1-254>` | Priority |
| `[undo] vrrp vrid INTEGER<1-255> preempt-mode disable` | Disable preemption |
| `[undo] ospfv3 INTEGER<1-65535> area IPADDR<X.X.X.X>/INTEGER` | Area ID |
| `[undo] ospfv3 cost INTEGER<1-65535>` | Cost |
| `[undo] ripng INTEGER<1-65535> enable` | Enable RIPng on the interface |
| `[undo] voice-vlan INTEGER<1-4094> enable` | Enable voice VLAN on the port |
| `[undo] dhcpv6 server STRING<1-31>` | Pool name |
<!-- END GENERATED COMMANDS -->
