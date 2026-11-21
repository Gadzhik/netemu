# MikroTik RouterOS

[← User guide](guide.md)

Models: the **MikroTik RB4011** router (ports `ether1`–`ether10`) and the **MikroTik CRS326-24G** switch (`ether1`–`ether24`). The console follows RouterOS v7. Ports are enabled as soon as the device is added.

## How the console works

Commands are organised in menus. You can enter a menu and run commands without the path:

```
[admin@MikroTik] > /ip address
[admin@MikroTik] /ip address> print
[admin@MikroTik] /ip address> ..
[admin@MikroTik] /ip> /
[admin@MikroTik] >
```

Or give the full path from any menu: `/ip address print`. Arguments are `key=value` pairs. Items are removed by their number from `print`: `/ip route remove 0`.

## Example: a router between two networks

```
/system identity set name=MT1
/ip address add address=10.0.1.2/30 interface=ether1
/ip address add address=192.168.20.1/24 interface=ether3
/ip route add dst-address=192.168.1.0/24 gateway=10.0.1.1
/ip route add gateway=10.0.2.2
```

A route without `dst-address` is the default route (0.0.0.0/0). A backup route takes a larger distance: `/ip route add dst-address=192.168.1.0/24 gateway=10.0.3.1 distance=10`.

Disable and enable a port: `/interface disable ether2`, `/interface enable ether2`. Comment: `/interface set ether1 comment=uplink`.

Check: `/ip address print`, `/ip route print` (flags `A` active, `s` static, `c` connected), `/ip arp print`, `/ping 192.168.2.10 count=3`, `/tool traceroute 192.168.2.10`, `/export`.

Ping sends 4 packets by default (`count=` sets another number) so the console is not busy forever. `Ctrl+C` stops it.

## VLANs and trunks

On the CRS326 all ports belong to the bridge `bridge`. VLANs work as in RouterOS v7: each port has a `pvid` (the VLAN of untagged frames), and the `/interface bridge vlan` table lists tagged and untagged members. Filtering is turned on with `vlan-filtering=yes`; until then the bridge forwards frames without looking at VLAN tags.

```switch
/interface bridge port set ether1 pvid=10
/interface bridge port set ether2 pvid=20
/interface bridge vlan add bridge=bridge vlan-ids=10 tagged=ether24 untagged=ether1
/interface bridge vlan add bridge=bridge vlan-ids=20 tagged=ether24 untagged=ether2
/interface bridge set bridge vlan-filtering=yes
```

A port listed in `tagged=` works as a trunk. Check: `/interface bridge vlan print`, `/interface bridge port print`, `/interface bridge host print`, `/export`.

**Inter-VLAN routing.** On the RB4011, create a VLAN interface on top of the physical port and give it the address:

```
/interface vlan add name=vlan30 vlan-id=30 interface=ether2
/interface vlan add name=vlan40 vlan-id=40 interface=ether2
/ip address add address=192.168.30.1/24 interface=vlan30
/ip address add address=192.168.40.1/24 interface=vlan40
```

Check: `/interface vlan print`, `/ip address print`.

## OSPF

OSPF in RouterOS v7 uses three tables: the instance (`instance`), areas (`area`) and interface templates (`interface-template`). A template selects interfaces by network (`networks=`) or by name (`interfaces=`). A loopback on a router is an empty bridge.

```
/interface bridge add name=loopback
/ip address add address=1.1.1.1/32 interface=loopback
/routing ospf instance add name=default-v2 router-id=1.1.1.1
/routing ospf area add name=backbone-v2 area-id=0.0.0.0 instance=default-v2
/routing ospf interface-template add networks=10.0.1.0/30 area=backbone-v2
/routing ospf interface-template add networks=1.1.1.1/32 area=backbone-v2 passive
```

The default route and redistribution are set on the instance: `/routing ospf instance set default-v2 originate-default=always redistribute=static,connected`. A template accepts `cost=`, `priority=`, `hello-interval=`, `dead-interval=`. The default interface cost is 1.

Check OSPF: `/routing ospf neighbor print`, `/routing ospf interface print`, `/routing ospf lsa print`, `/ip route print` (OSPF routes are marked `o`).

## Firewall and NAT

Filter rules work in chains: `input` for traffic to the router itself, `forward` for traffic passing through. A packet that matches no rule is accepted. Actions: `accept`, `drop`, `reject` (with an ICMP reply).

```
/ip address add address=203.0.113.1/29 interface=ether5
/ip firewall filter add chain=forward src-address=192.168.20.11 protocol=icmp action=drop
/ip firewall filter add chain=input protocol=tcp dst-port=23 action=reject
/ip firewall nat add chain=srcnat out-interface=ether5 action=masquerade
/ip firewall nat add chain=dstnat dst-address=203.0.113.1 protocol=tcp dst-port=8080 action=dst-nat to-addresses=192.168.20.10 to-ports=80
```

`masquerade` replaces the source with the outside interface address; `action=src-nat to-addresses=A-B` translates to a range. Check: `/ip firewall filter print stats`, `/ip firewall nat print`, `/ip firewall connection print`.

## BGP

A BGP session runs over TCP port 179 and is routed like any traffic: peering between loopbacks needs a route to the peer's loopback (IGP or static) and a source address; eBGP with a peer that is not on a connected subnet needs multihop; an ACL can block port 179. With a wrong `remote-as` the peer answers with a NOTIFICATION and the session stays down. A route whose next hop is unreachable is not installed: iBGP usually needs next-hop-self. Best path: weight, local-pref, locally originated, AS_PATH length, origin, MED, eBGP over iBGP, IGP metric to the next hop.

In RouterOS v7 every neighbor is a `/routing bgp connection` entry. Advertised networks come from an address list (`output.network`).

```
/ip firewall address-list add list=bgp-networks address=192.168.20.0/24
/routing bgp connection add name=to-isp remote.address=10.0.24.2 remote.as=65004 as=65001 local.role=ebgp router-id=2.2.2.2 output.network=bgp-networks
```

Also available: `local.address=` (source address), `multihop=yes`, `nexthop-choice=force-self`, `output.redistribute=connected,static`, `output.default-originate=always`, `local.role=ibgp`. Check BGP: `/routing bgp session print`, `/routing bgp advertisements print`, `/ip route print` (BGP routes are marked `b`).

## DHCP

```
/ip address add address=192.168.70.1/24 interface=ether6
/ip pool add name=dhcp_pool ranges=192.168.70.100-192.168.70.200
/ip dhcp-server add name=dhcp1 interface=ether6 address-pool=dhcp_pool
/ip dhcp-server network add address=192.168.70.0/24 gateway=192.168.70.1 dns-server=192.168.70.2
```

A DHCP relay forwards the clients' broadcast requests to a server in another network, adding the interface address (giaddr); the server picks the pool by that address. In RouterOS: `/ip dhcp-relay add name=relay1 interface=ether7 dhcp-server=10.0.0.5 local-address=<ether7 address>`. Check: `/ip dhcp-server lease print`.

## RIP

RIPv2: updates every 30 s plus triggered updates, hop-count metric (16 = unreachable), split horizon, a route is invalidated after 180 s without updates. In RouterOS v7 networks are advertised only through `redistribute`. Distance 120.

```
/routing rip instance add name=rip1 redistribute=connected
/routing rip interface-template add interfaces=ether1 instance=rip1
```

Check: `/ip route print` (RIP routes are marked `r`).

## STP

STP is on by default on switches. The root is the switch with the lowest priority and MAC; redundant links are blocked (Alternate role). Ports towards PCs and routers act as edge ports and forward at once. The CRS326 bridge runs RSTP by default.

```switch
/interface bridge set bridge protocol-mode=rstp priority=0x1000
/interface bridge port set ether1 edge=yes
```

Also: `path-cost=N` on a port, `protocol-mode=none` (turn off). Check: `/interface bridge monitor`, `/interface bridge port print` (ROLE column).

## IPv6

IPv6: global and link-local (EUI-64) addresses, neighbor discovery (NDP), IPv6 ping, static routes, router advertisements (RA) and address autoconfiguration on PCs (SLAAC). RouterOS forwards IPv6 and sends RAs as soon as an address with a /64 prefix is added.

```
/ipv6 address add address=2001:db8:1::1/64 interface=ether1
/ipv6 route add dst-address=2001:db8:50::/48 gateway=2001:db8:1::2
```

Also: `advertise=no` on an address, `eui-64=yes`, `/ipv6 nd set disabled=yes`. Check: `/ipv6 address print`, `/ipv6 route print`, `/ipv6 neighbor print`, `/ping 2001:db8:1::10`.

## Routing on the switch (CRS)

The CRS326 runs full RouterOS: an IP address goes on the bridge itself (`interface=bridge`) or on a VLAN interface on top of the bridge.

```switch
/interface vlan add interface=bridge vlan-id=10 name=vlan10
/interface vlan add interface=bridge vlan-id=20 name=vlan20
/ip address add address=10.0.10.1/24 interface=vlan10
/ip address add address=10.0.20.1/24 interface=vlan20
```

The CRS also supports `/ip route`, `/routing ospf`, `/ip dhcp-server` and `/ip firewall`. Check: `/ip address print`, `/ip route print`.

## Remote access (SSH and Telnet)

Devices accept remote logins over Telnet (TCP 23) and SSH (TCP 22). Simulation mode shows the TCP handshake; Telnet packet details reveal even the password, SSH contents are encrypted. The connection opens in the current console and `exit` returns. You can hop on from a remote console. RouterOS has SSH and Telnet on from the start, with user `admin` and an empty password. Set a password and turn Telnet off if you like:

```
/user set admin password=mt123
/user add name=noc password=noc123 group=full
/ip service disable telnet
```

Connect with `/system ssh address=10.0.0.2 user=admin` or `/system telnet 10.0.0.2`. Check: `/ip service print`, `/user print`.

## EtherChannel (link aggregation)

Several cables between two switches are combined into one logical channel. STP treats it as a single port and blocks nothing, and when one cable fails the traffic moves to the others. The channel forms when the modes at both ends match: `on` only with `on`; LACP `active` with `active` or `passive`; PAgP `desirable` with `desirable` or `auto`. Otherwise the ports stay suspended and carry no traffic.

```switch
/interface bonding add name=bond1 slaves=ether1,ether2 mode=802.3ad
```

`mode=802.3ad` is LACP, `balance-rr` is a static bundle. Check: `/interface bonding print`.

## VRRP (gateway redundancy)

Two routers on one network share a virtual IP address that PCs use as their gateway. The active (HSRP) or master (VRRP) router is elected by priority, then by the higher IP. It answers ARP with the virtual MAC. When it disappears, the backup takes over after the hold time (HSRP 10 s, VRRP about 3.6 s) and sends a gratuitous ARP. By default HSRP does not take the role back (`preempt` is needed), VRRP does.

```
/ip address add address=192.168.1.2/24 interface=ether1
/interface vrrp add interface=ether1 vrid=1 priority=200
/ip address add address=192.168.1.254/32 interface=vrrp1
```

The virtual address goes on the VRRP interface. Check: `/interface vrrp print` (flag M is master).

## OSPFv3, RIPng, DHCPv6 and DHCP snooping

```
/ipv6 address add address=2001:db8:61::1/64 interface=ether3
/routing ospf instance add name=v3 version=3 router-id=1.1.1.1
/routing ospf area add name=bb6 area-id=0.0.0.0 instance=v3
/routing ospf interface-template add area=bb6 interfaces=ether3
/ipv6 pool add name=p6 prefix=2001:db8:61::/64 prefix-length=64
/ipv6 dhcp-server add name=d6 interface=ether3 address-pool=p6
```

RIPng: `/routing rip instance add name=rng afi=ipv6` and `/routing rip interface-template add instance=rng interfaces=ether1`. DHCP snooping on a CRS: `/interface bridge set bridge dhcp-snooping=yes`, `/interface bridge port set ether24 trusted=yes`. RouterOS has no separate port security feature. Check: `/ipv6 route print`, `/ipv6 dhcp-server binding print`.

## GRE and IPsec, Syslog, SNMP, NTP

`/interface gre add` creates a tunnel; with `ipsec-secret` the traffic is encrypted and both ends need the same secret.

```
/interface gre add name=gre1 local-address=203.0.113.2 remote-address=198.51.100.2 ipsec-secret=S3cret!
/ip address add address=10.255.0.1/30 interface=gre1
/system logging action add name=remote1 target=remote remote=192.168.1.100
/snmp set enabled=yes location=Server-room-1
/snmp community add name=private write-access=yes
/system ntp client set enabled=yes servers=192.168.1.100
```

Check: `/interface gre print`, `/system ntp client print`, `/snmp community print`, `/log print`.

## All commands: RB4011 router

<!-- BEGIN GENERATED COMMANDS router -->
**`/`**

| Command | Description |
|---|---|
| `/export` | Print or save an export script |
| `/ping <A.B.C.D> [count=<n>] [size=<bytes>] [ttl=<n>]` | Send ICMP Echo packets |
| `/quit` | Quit console |
| `/traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/interface`**

| Command | Description |
|---|---|
| `/interface comment <name> comment=<text>` | Set comment for items |
| `/interface disable <name>` | Disable items |
| `/interface enable <name>` | Enable items |
| `/interface print` | Print values of item properties |
| `/interface set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface bridge`**

| Command | Description |
|---|---|
| `/interface bridge add` | Create a new item |
| `/interface bridge print` | Print values of item properties |
| `/interface bridge remove` | Remove item |

**`/interface ethernet`**

| Command | Description |
|---|---|
| `/interface ethernet disable <name>` | Disable items |
| `/interface ethernet enable <name>` | Enable items |
| `/interface ethernet print` | Print values of item properties |
| `/interface ethernet set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface gre`**

| Command | Description |
|---|---|
| `/interface gre add` | Create a new item |
| `/interface gre print` | Print values of item properties |
| `/interface gre remove` | Remove item |

**`/interface vlan`**

| Command | Description |
|---|---|
| `/interface vlan add name=<name> vlan-id=<1-4094> interface=<name>` | Create a new item |
| `/interface vlan print` | Print values of item properties |
| `/interface vlan remove <number>` | Remove item |

**`/interface vrrp`**

| Command | Description |
|---|---|
| `/interface vrrp add` | Create a new item |
| `/interface vrrp print` | Print values of item properties |
| `/interface vrrp remove` | Remove item |

**`/ip address`**

| Command | Description |
|---|---|
| `/ip address add address=<A.B.C.D/len> interface=<name> [comment=<text>]` | Create a new item |
| `/ip address print` | Print values of item properties |
| `/ip address remove <number>` | Remove item |

**`/ip arp`**

| Command | Description |
|---|---|
| `/ip arp print` | Print values of item properties |

**`/ip dhcp-relay`**

| Command | Description |
|---|---|
| `/ip dhcp-relay add` | Create a new item |
| `/ip dhcp-relay print` | Print values of item properties |
| `/ip dhcp-relay remove` | Remove item |

**`/ip dhcp-server`**

| Command | Description |
|---|---|
| `/ip dhcp-server add` | Create a new item |
| `/ip dhcp-server print` | Print values of item properties |
| `/ip dhcp-server remove` | Remove item |

**`/ip dhcp-server lease`**

| Command | Description |
|---|---|
| `/ip dhcp-server lease print` | Print values of item properties |

**`/ip dhcp-server network`**

| Command | Description |
|---|---|
| `/ip dhcp-server network add` | Create a new item |
| `/ip dhcp-server network print` | Print values of item properties |
| `/ip dhcp-server network remove` | Remove item |

**`/ip firewall address-list`**

| Command | Description |
|---|---|
| `/ip firewall address-list add` | Create a new item |
| `/ip firewall address-list print` | Print values of item properties |
| `/ip firewall address-list remove` | Remove item |

**`/ip firewall connection`**

| Command | Description |
|---|---|
| `/ip firewall connection print` | Print values of item properties |

**`/ip firewall filter`**

| Command | Description |
|---|---|
| `/ip firewall filter add` | Create a new item |
| `/ip firewall filter print` | Print values of item properties |
| `/ip firewall filter remove` | Remove item |

**`/ip firewall nat`**

| Command | Description |
|---|---|
| `/ip firewall nat add` | Create a new item |
| `/ip firewall nat print` | Print values of item properties |
| `/ip firewall nat remove` | Remove item |

**`/ip pool`**

| Command | Description |
|---|---|
| `/ip pool add` | Create a new item |
| `/ip pool print` | Print values of item properties |
| `/ip pool remove` | Remove item |

**`/ip route`**

| Command | Description |
|---|---|
| `/ip route add [dst-address=<A.B.C.D/len>] gateway=<A.B.C.D> [distance=<n>] [comment=<text>]` | Create a new item |
| `/ip route print` | Print values of item properties |
| `/ip route remove <number>` | Remove item |

**`/ip service`**

| Command | Description |
|---|---|
| `/ip service disable` | Disable items |
| `/ip service enable` | Enable items |
| `/ip service print` | Print values of item properties |
| `/ip service set` | Change item properties |

**`/ipv6 address`**

| Command | Description |
|---|---|
| `/ipv6 address add` | Create a new item |
| `/ipv6 address print` | Print values of item properties |
| `/ipv6 address remove` | Remove item |

**`/ipv6 dhcp-server`**

| Command | Description |
|---|---|
| `/ipv6 dhcp-server add` | Create a new item |
| `/ipv6 dhcp-server print` | Print values of item properties |

**`/ipv6 dhcp-server binding`**

| Command | Description |
|---|---|
| `/ipv6 dhcp-server binding print` | Print values of item properties |

**`/ipv6 nd`**

| Command | Description |
|---|---|
| `/ipv6 nd set` | Change item properties |

**`/ipv6 neighbor`**

| Command | Description |
|---|---|
| `/ipv6 neighbor print` | Print values of item properties |

**`/ipv6 pool`**

| Command | Description |
|---|---|
| `/ipv6 pool add` | Create a new item |
| `/ipv6 pool print` | Print values of item properties |

**`/ipv6 route`**

| Command | Description |
|---|---|
| `/ipv6 route add` | Create a new item |
| `/ipv6 route print` | Print values of item properties |

**`/log`**

| Command | Description |
|---|---|
| `/log print` | Print values of item properties |

**`/routing bgp advertisements`**

| Command | Description |
|---|---|
| `/routing bgp advertisements print` | Print values of item properties |

**`/routing bgp connection`**

| Command | Description |
|---|---|
| `/routing bgp connection add` | Create a new item |
| `/routing bgp connection print` | Print values of item properties |
| `/routing bgp connection remove` | Remove item |

**`/routing bgp session`**

| Command | Description |
|---|---|
| `/routing bgp session print` | Print values of item properties |

**`/routing ospf area`**

| Command | Description |
|---|---|
| `/routing ospf area add` | Create a new item |
| `/routing ospf area print` | Print values of item properties |
| `/routing ospf area remove` | Remove item |

**`/routing ospf instance`**

| Command | Description |
|---|---|
| `/routing ospf instance add` | Create a new item |
| `/routing ospf instance print` | Print values of item properties |
| `/routing ospf instance remove` | Remove item |
| `/routing ospf instance set` | Change item properties |

**`/routing ospf interface`**

| Command | Description |
|---|---|
| `/routing ospf interface print` | Print values of item properties |

**`/routing ospf interface-template`**

| Command | Description |
|---|---|
| `/routing ospf interface-template add` | Create a new item |
| `/routing ospf interface-template print` | Print values of item properties |
| `/routing ospf interface-template remove` | Remove item |

**`/routing ospf lsa`**

| Command | Description |
|---|---|
| `/routing ospf lsa print` | Print values of item properties |

**`/routing ospf neighbor`**

| Command | Description |
|---|---|
| `/routing ospf neighbor print` | Print values of item properties |

**`/routing rip instance`**

| Command | Description |
|---|---|
| `/routing rip instance add` | Create a new item |
| `/routing rip instance print` | Print values of item properties |
| `/routing rip instance remove` | Remove item |

**`/routing rip interface-template`**

| Command | Description |
|---|---|
| `/routing rip interface-template add` | Create a new item |
| `/routing rip interface-template print` | Print values of item properties |
| `/routing rip interface-template remove` | Remove item |

**`/snmp`**

| Command | Description |
|---|---|
| `/snmp print` | Print values of item properties |
| `/snmp set` | Change item properties |

**`/snmp community`**

| Command | Description |
|---|---|
| `/snmp community add` | Create a new item |
| `/snmp community print` | Print values of item properties |

**`/system`**

| Command | Description |
|---|---|
| `/system reboot` | Reboot the system |
| `/system reset-configuration` | Reset configuration |
| `/system ssh` | Open an SSH session |
| `/system telnet` | Open a Telnet session |

**`/system clock`**

| Command | Description |
|---|---|
| `/system clock print` | Print values of item properties |

**`/system identity`**

| Command | Description |
|---|---|
| `/system identity print` | Print values of item properties |
| `/system identity set name=<name>` | Change item properties |

**`/system logging`**

| Command | Description |
|---|---|
| `/system logging print` | Print values of item properties |

**`/system logging action`**

| Command | Description |
|---|---|
| `/system logging action add` | Create a new item |
| `/system logging action print` | Print values of item properties |

**`/system ntp client`**

| Command | Description |
|---|---|
| `/system ntp client print` | Print values of item properties |
| `/system ntp client set` | Change item properties |

**`/system resource`**

| Command | Description |
|---|---|
| `/system resource print` | Print values of item properties |

**`/tool`**

| Command | Description |
|---|---|
| `/tool traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/user`**

| Command | Description |
|---|---|
| `/user add` | Create a new item |
| `/user print` | Print values of item properties |
| `/user remove` | Remove item |
| `/user set` | Change item properties |
<!-- END GENERATED COMMANDS -->

## All commands: CRS326 switch

<!-- BEGIN GENERATED COMMANDS switch -->
**`/`**

| Command | Description |
|---|---|
| `/export` | Print or save an export script |
| `/ping <A.B.C.D> [count=<n>] [size=<bytes>] [ttl=<n>]` | Send ICMP Echo packets |
| `/quit` | Quit console |
| `/traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/interface`**

| Command | Description |
|---|---|
| `/interface comment <name> comment=<text>` | Set comment for items |
| `/interface disable <name>` | Disable items |
| `/interface enable <name>` | Enable items |
| `/interface print` | Print values of item properties |
| `/interface set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface bonding`**

| Command | Description |
|---|---|
| `/interface bonding add` | Create a new item |
| `/interface bonding print` | Print values of item properties |
| `/interface bonding remove` | Remove item |

**`/interface bridge`**

| Command | Description |
|---|---|
| `/interface bridge monitor` | Monitor bridge status |
| `/interface bridge print` | Print values of item properties |
| `/interface bridge set bridge vlan-filtering=yes/no` | Change item properties |

**`/interface bridge host`**

| Command | Description |
|---|---|
| `/interface bridge host print` | Print values of item properties |

**`/interface bridge port`**

| Command | Description |
|---|---|
| `/interface bridge port print` | Print values of item properties |
| `/interface bridge port set <name>/[find interface=<name>] pvid=<1-4094>` | Change item properties |

**`/interface bridge vlan`**

| Command | Description |
|---|---|
| `/interface bridge vlan add bridge=bridge vlan-ids=<list> [tagged=<ports>] [untagged=<ports>]` | Create a new item |
| `/interface bridge vlan print` | Print values of item properties |
| `/interface bridge vlan remove <number>` | Remove item |

**`/interface ethernet`**

| Command | Description |
|---|---|
| `/interface ethernet disable <name>` | Disable items |
| `/interface ethernet enable <name>` | Enable items |
| `/interface ethernet print` | Print values of item properties |
| `/interface ethernet set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface gre`**

| Command | Description |
|---|---|
| `/interface gre add` | Create a new item |
| `/interface gre print` | Print values of item properties |
| `/interface gre remove` | Remove item |

**`/interface vlan`**

| Command | Description |
|---|---|
| `/interface vlan add name=<name> vlan-id=<1-4094> interface=<name>` | Create a new item |
| `/interface vlan print` | Print values of item properties |
| `/interface vlan remove <number>` | Remove item |

**`/interface vrrp`**

| Command | Description |
|---|---|
| `/interface vrrp add` | Create a new item |
| `/interface vrrp print` | Print values of item properties |
| `/interface vrrp remove` | Remove item |

**`/ip address`**

| Command | Description |
|---|---|
| `/ip address add address=<A.B.C.D/len> interface=<name> [comment=<text>]` | Create a new item |
| `/ip address print` | Print values of item properties |
| `/ip address remove <number>` | Remove item |

**`/ip arp`**

| Command | Description |
|---|---|
| `/ip arp print` | Print values of item properties |

**`/ip dhcp-relay`**

| Command | Description |
|---|---|
| `/ip dhcp-relay add` | Create a new item |
| `/ip dhcp-relay print` | Print values of item properties |
| `/ip dhcp-relay remove` | Remove item |

**`/ip dhcp-server`**

| Command | Description |
|---|---|
| `/ip dhcp-server add` | Create a new item |
| `/ip dhcp-server print` | Print values of item properties |
| `/ip dhcp-server remove` | Remove item |

**`/ip dhcp-server lease`**

| Command | Description |
|---|---|
| `/ip dhcp-server lease print` | Print values of item properties |

**`/ip dhcp-server network`**

| Command | Description |
|---|---|
| `/ip dhcp-server network add` | Create a new item |
| `/ip dhcp-server network print` | Print values of item properties |
| `/ip dhcp-server network remove` | Remove item |

**`/ip firewall address-list`**

| Command | Description |
|---|---|
| `/ip firewall address-list add` | Create a new item |
| `/ip firewall address-list print` | Print values of item properties |
| `/ip firewall address-list remove` | Remove item |

**`/ip firewall connection`**

| Command | Description |
|---|---|
| `/ip firewall connection print` | Print values of item properties |

**`/ip firewall filter`**

| Command | Description |
|---|---|
| `/ip firewall filter add` | Create a new item |
| `/ip firewall filter print` | Print values of item properties |
| `/ip firewall filter remove` | Remove item |

**`/ip firewall nat`**

| Command | Description |
|---|---|
| `/ip firewall nat add` | Create a new item |
| `/ip firewall nat print` | Print values of item properties |
| `/ip firewall nat remove` | Remove item |

**`/ip pool`**

| Command | Description |
|---|---|
| `/ip pool add` | Create a new item |
| `/ip pool print` | Print values of item properties |
| `/ip pool remove` | Remove item |

**`/ip route`**

| Command | Description |
|---|---|
| `/ip route add [dst-address=<A.B.C.D/len>] gateway=<A.B.C.D> [distance=<n>] [comment=<text>]` | Create a new item |
| `/ip route print` | Print values of item properties |
| `/ip route remove <number>` | Remove item |

**`/ip service`**

| Command | Description |
|---|---|
| `/ip service disable` | Disable items |
| `/ip service enable` | Enable items |
| `/ip service print` | Print values of item properties |
| `/ip service set` | Change item properties |

**`/ipv6 address`**

| Command | Description |
|---|---|
| `/ipv6 address add` | Create a new item |
| `/ipv6 address print` | Print values of item properties |
| `/ipv6 address remove` | Remove item |

**`/ipv6 dhcp-server`**

| Command | Description |
|---|---|
| `/ipv6 dhcp-server add` | Create a new item |
| `/ipv6 dhcp-server print` | Print values of item properties |

**`/ipv6 dhcp-server binding`**

| Command | Description |
|---|---|
| `/ipv6 dhcp-server binding print` | Print values of item properties |

**`/ipv6 nd`**

| Command | Description |
|---|---|
| `/ipv6 nd set` | Change item properties |

**`/ipv6 neighbor`**

| Command | Description |
|---|---|
| `/ipv6 neighbor print` | Print values of item properties |

**`/ipv6 pool`**

| Command | Description |
|---|---|
| `/ipv6 pool add` | Create a new item |
| `/ipv6 pool print` | Print values of item properties |

**`/ipv6 route`**

| Command | Description |
|---|---|
| `/ipv6 route add` | Create a new item |
| `/ipv6 route print` | Print values of item properties |

**`/log`**

| Command | Description |
|---|---|
| `/log print` | Print values of item properties |

**`/routing bgp advertisements`**

| Command | Description |
|---|---|
| `/routing bgp advertisements print` | Print values of item properties |

**`/routing bgp connection`**

| Command | Description |
|---|---|
| `/routing bgp connection add` | Create a new item |
| `/routing bgp connection print` | Print values of item properties |
| `/routing bgp connection remove` | Remove item |

**`/routing bgp session`**

| Command | Description |
|---|---|
| `/routing bgp session print` | Print values of item properties |

**`/routing ospf area`**

| Command | Description |
|---|---|
| `/routing ospf area add` | Create a new item |
| `/routing ospf area print` | Print values of item properties |
| `/routing ospf area remove` | Remove item |

**`/routing ospf instance`**

| Command | Description |
|---|---|
| `/routing ospf instance add` | Create a new item |
| `/routing ospf instance print` | Print values of item properties |
| `/routing ospf instance remove` | Remove item |
| `/routing ospf instance set` | Change item properties |

**`/routing ospf interface`**

| Command | Description |
|---|---|
| `/routing ospf interface print` | Print values of item properties |

**`/routing ospf interface-template`**

| Command | Description |
|---|---|
| `/routing ospf interface-template add` | Create a new item |
| `/routing ospf interface-template print` | Print values of item properties |
| `/routing ospf interface-template remove` | Remove item |

**`/routing ospf lsa`**

| Command | Description |
|---|---|
| `/routing ospf lsa print` | Print values of item properties |

**`/routing ospf neighbor`**

| Command | Description |
|---|---|
| `/routing ospf neighbor print` | Print values of item properties |

**`/routing rip instance`**

| Command | Description |
|---|---|
| `/routing rip instance add` | Create a new item |
| `/routing rip instance print` | Print values of item properties |
| `/routing rip instance remove` | Remove item |

**`/routing rip interface-template`**

| Command | Description |
|---|---|
| `/routing rip interface-template add` | Create a new item |
| `/routing rip interface-template print` | Print values of item properties |
| `/routing rip interface-template remove` | Remove item |

**`/snmp`**

| Command | Description |
|---|---|
| `/snmp print` | Print values of item properties |
| `/snmp set` | Change item properties |

**`/snmp community`**

| Command | Description |
|---|---|
| `/snmp community add` | Create a new item |
| `/snmp community print` | Print values of item properties |

**`/system`**

| Command | Description |
|---|---|
| `/system reboot` | Reboot the system |
| `/system reset-configuration` | Reset configuration |
| `/system ssh` | Open an SSH session |
| `/system telnet` | Open a Telnet session |

**`/system clock`**

| Command | Description |
|---|---|
| `/system clock print` | Print values of item properties |

**`/system identity`**

| Command | Description |
|---|---|
| `/system identity print` | Print values of item properties |
| `/system identity set name=<name>` | Change item properties |

**`/system logging`**

| Command | Description |
|---|---|
| `/system logging print` | Print values of item properties |

**`/system logging action`**

| Command | Description |
|---|---|
| `/system logging action add` | Create a new item |
| `/system logging action print` | Print values of item properties |

**`/system ntp client`**

| Command | Description |
|---|---|
| `/system ntp client print` | Print values of item properties |
| `/system ntp client set` | Change item properties |

**`/system resource`**

| Command | Description |
|---|---|
| `/system resource print` | Print values of item properties |

**`/tool`**

| Command | Description |
|---|---|
| `/tool traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/user`**

| Command | Description |
|---|---|
| `/user add` | Create a new item |
| `/user print` | Print values of item properties |
| `/user remove` | Remove item |
| `/user set` | Change item properties |
<!-- END GENERATED COMMANDS -->
