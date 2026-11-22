# Cisco IOS

[← User guide](guide.md)

Models: the **Cisco 2911** router (ports `GigabitEthernet0/0`–`0/2`, short `Gi0/0`) and the **Cisco 2960-24TT** switch (`FastEthernet0/1`–`0/24` and `GigabitEthernet0/1`–`0/2`).

## Modes

| Prompt | Mode | Enter with | Leave with |
|---|---|---|---|
| `Router>` | user EXEC | on login | — |
| `Router#` | privileged EXEC | `enable` | `disable` |
| `Router(config)#` | global configuration | `configure terminal` | `exit`, `end`, `Ctrl+Z` |
| `Router(config-if)#` | interface configuration | `interface Gi0/0` | `exit`, `end` |

`no` in front of a configuration command undoes it: `no shutdown`, `no ip route …`, `no hostname`. In configuration mode, privileged commands run through `do`: `do show ip route`.

## Example: a router between two networks

```
enable
configure terminal
hostname R1
interface GigabitEthernet0/0
 description LAN
 ip address 192.168.1.1 255.255.255.0
 no shutdown
interface GigabitEthernet0/1
 ip address 10.0.1.1 255.255.255.252
 no shutdown
exit
ip route 192.168.2.0 255.255.255.0 10.0.1.2
ip route 0.0.0.0 0.0.0.0 10.0.1.2
end
write memory
```

A backup (floating) route takes a distance at the end: `ip route 192.168.2.0 255.255.255.0 10.0.2.2 10`. It becomes active when the primary next hop is unreachable.

Check: `show ip interface brief`, `show ip route`, `show arp`, `ping 192.168.2.10`, `traceroute 192.168.2.10`.

## VLANs and trunks

On the 2960 switch, VLANs are created with `vlan N` (named with `name`), a port joins a VLAN with `switchport access vlan N`, and a trunk is enabled with `switchport mode trunk`. By default a trunk carries all VLANs and the native VLAN is 1.

```switch
enable
configure terminal
vlan 10
name SALES
exit
vlan 20
name HR
exit
interface FastEthernet0/1
switchport mode access
switchport access vlan 10
interface FastEthernet0/2
switchport mode access
switchport access vlan 20
interface GigabitEthernet0/1
switchport mode trunk
switchport trunk allowed vlan 10,20
end
```

Change the allowed list with `switchport trunk allowed vlan add 30`, `… remove 20`, `… all`. Set the native VLAN with `switchport trunk native vlan 99`. Assigning a port to a VLAN that does not exist creates it, as IOS does. Check: `show vlan brief`, `show interfaces trunk`, `show interfaces status`, `show mac address-table`.

**Inter-VLAN routing (router-on-a-stick).** The router gets one subinterface per VLAN. Set `encapsulation dot1Q` first, then the IP address; IOS rejects the address otherwise.

```
enable
configure terminal
interface GigabitEthernet0/0
no shutdown
interface GigabitEthernet0/0.10
encapsulation dot1Q 10
ip address 192.168.10.1 255.255.255.0
interface GigabitEthernet0/0.20
encapsulation dot1Q 20
ip address 192.168.20.1 255.255.255.0
end
```

Delete a subinterface with `no interface GigabitEthernet0/0.20`.

## OSPF

Enable OSPF with `router ospf N`. Interfaces join OSPF through `network <address> <wildcard> area <area>`. The router ID comes from `router-id`; without it, the highest loopback address is used, otherwise the highest interface address.

```
enable
configure terminal
interface loopback0
ip address 1.1.1.1 255.255.255.255
interface GigabitEthernet0/1
ip address 10.0.12.1 255.255.255.252
no shutdown
ip ospf cost 10
router ospf 1
router-id 1.1.1.1
network 10.0.12.0 0.0.0.3 area 0
network 1.1.1.1 0.0.0.0 area 0
passive-interface GigabitEthernet0/0
default-information originate
end
```

Also available: `redistribute static subnets`, `redistribute connected subnets`, `auto-cost reference-bandwidth N`, `passive-interface default`, and on interfaces `ip ospf hello-interval`, `ip ospf dead-interval`, `ip ospf priority`. As on real routers, no adjacency forms when the area, subnet mask or hello/dead timers differ. IOS reports adjacency changes on the console (`%OSPF-5-ADJCHG`).

Check OSPF: `show ip ospf neighbor`, `show ip ospf interface brief`, `show ip ospf database`, `show ip protocols`, `show ip route ospf`. OSPF routes are marked `O`, inter-area `O IA`, external `O E2`; administrative distance 110.

## ACLs

Standard lists (1–99) check only the source address; extended lists (100–199) check the protocol, addresses, TCP/UDP ports and ICMP type. Every list ends with an implicit `deny any`. The router drops a denied packet and answers with ICMP "administratively prohibited", so ping shows `U`.

```
enable
configure terminal
access-list 10 permit 192.168.1.0 0.0.0.255
access-list 100 deny icmp host 192.168.1.11 host 8.8.8.8 echo
access-list 100 deny tcp any any eq www
access-list 100 permit ip any any
ip access-list extended MGMT
10 permit tcp 192.168.1.0 0.0.0.255 any eq 22
20 deny ip any any
exit
interface GigabitEthernet0/0
ip access-group 100 in
end
```

An address is `any`, `host A.B.C.D` or `A.B.C.D <wildcard>`. Ports: `eq`, `neq`, `gt`, `lt`, `range`, also by name (`www`, `ssh`, `domain`…). Match counters: `show access-lists`; reset with `clear access-list counters`.

## NAT

Mark interfaces with `ip nat inside` and `ip nat outside`. PAT (overload) on the outside interface address:

```
enable
configure terminal
access-list 1 permit 192.168.1.0 0.0.0.255
interface GigabitEthernet0/0
ip nat inside
interface GigabitEthernet0/1
ip nat outside
exit
ip nat inside source list 1 interface GigabitEthernet0/1 overload
ip nat inside source static 192.168.1.10 203.0.113.3
ip nat inside source static tcp 192.168.1.20 80 203.0.113.1 8080
ip nat pool PUBLIC 203.0.113.4 203.0.113.5 netmask 255.255.255.248
end
```

Dynamic NAT from a pool: `ip nat inside source list 1 pool PUBLIC [overload]`. Without `overload` each inside address needs its own pool address; when the pool runs out, packets are dropped. Check NAT: `show ip nat translations`, `show ip nat statistics`, `clear ip nat translation *`.

## BGP

A BGP session runs over TCP port 179 and is routed like any traffic: peering between loopbacks needs a route to the peer's loopback (IGP or static) and a source address; eBGP with a peer that is not on a connected subnet needs multihop; an ACL can block port 179. With a wrong `remote-as` the peer answers with a NOTIFICATION and the session stays down. A route whose next hop is unreachable is not installed: iBGP usually needs next-hop-self. Best path: weight, local-pref, locally originated, AS_PATH length, origin, MED, eBGP over iBGP, IGP metric to the next hop.

```
enable
configure terminal
router bgp 65001
bgp router-id 1.1.1.1
neighbor 10.0.12.2 remote-as 65002
neighbor 10.0.12.2 description ISP
neighbor 2.2.2.2 remote-as 65001
neighbor 2.2.2.2 update-source Loopback0
neighbor 2.2.2.2 next-hop-self
network 192.168.1.0
network 10.10.0.0 mask 255.255.0.0
redistribute connected
end
```

Also available: `neighbor X ebgp-multihop N`, `neighbor X weight N`, `neighbor X default-originate`, `neighbor X route-reflector-client`, `neighbor X shutdown`, `clear ip bgp *`. `network` advertises a network only when it exactly matches a route in the routing table. Administrative distance: eBGP 20, iBGP 200. IOS reports sessions going up and down on the console (`%BGP-5-ADJCHANGE`).

Check BGP: `show ip bgp summary`, `show ip bgp`, `show ip bgp neighbors`, `show ip route bgp`.

## DHCP

```
enable
configure terminal
ip dhcp excluded-address 192.168.10.1 192.168.10.9
ip dhcp pool LAN10
network 192.168.10.0 255.255.255.0
default-router 192.168.10.1
dns-server 192.168.10.2
domain-name lab.local
exit
interface GigabitEthernet0/2
ip helper-address 10.0.0.5
end
```

A DHCP relay forwards the clients' broadcast requests to a server in another network, adding the interface address (giaddr); the server picks the pool by that address. On Cisco the relay is `ip helper-address` on the client-facing interface. Check DHCP: `show ip dhcp binding`, `show ip dhcp pool`, `clear ip dhcp binding *`.

## RIP

RIPv2: updates every 30 s plus triggered updates, hop-count metric (16 = unreachable), split horizon, a route is invalidated after 180 s without updates. Distance 120.

```
enable
configure terminal
router rip
version 2
network 10.0.0.0
network 192.168.1.0
passive-interface GigabitEthernet0/2
no auto-summary
end
```

Also: `default-information originate`, `redistribute static|connected|ospf 1 metric 1`. Check: `show ip rip database`, `show ip route rip`.

## STP

STP is on by default on switches. The root is the switch with the lowest priority and MAC; redundant links are blocked (Alternate role). Ports towards PCs and routers act as edge ports and forward at once. The default mode is PVST (802.1D): a new port between switches spends 30 s in listening and learning; `spanning-tree mode rapid-pvst` switches over at once.

```switch
enable
configure terminal
spanning-tree mode rapid-pvst
spanning-tree vlan 1 priority 4096
interface FastEthernet0/1
spanning-tree portfast
end
```

Also: `spanning-tree vlan 1 root primary`, `spanning-tree cost N`, `no spanning-tree vlan 1`. Check: `show spanning-tree`.

## IPv6

IPv6: global and link-local (EUI-64) addresses, neighbor discovery (NDP), IPv6 ping, static routes, router advertisements (RA) and address autoconfiguration on PCs (SLAAC). As on real IOS, the router forwards IPv6 and sends RAs only after `ipv6 unicast-routing`.

```
enable
configure terminal
ipv6 unicast-routing
interface GigabitEthernet0/0
ipv6 address 2001:db8:1::1/64
no shutdown
exit
ipv6 route 2001:db8:50::/48 2001:db8:1::2
end
```

Also: `ipv6 enable` (link-local only), `ipv6 nd ra suppress`. Check: `show ipv6 interface brief`, `show ipv6 route`, `show ipv6 neighbors`, `ping 2001:db8:1::10`.

## Routing on the switch (SVI)

A Cisco switch routes between VLANs through `interface vlan N`. As on a Catalyst, routing has to be switched on with `ip routing`; until then the switch only answers its own addresses and uses `ip default-gateway`. Switchports take no IP address: IOS answers `% IP addresses may not be configured on L2 links.`

```switch
enable
configure terminal
vlan 10
vlan 20
exit
interface vlan 10
ip address 10.0.10.1 255.255.255.0
no shutdown
interface vlan 20
ip address 10.0.20.1 255.255.255.0
no shutdown
exit
ip routing
end
```

The switch also supports `ip route`, `router ospf`, `router rip`, `ip dhcp pool`, ACLs and `ip helper-address` on SVIs. A VLAN interface comes up when at least one port of its VLAN is active. Check: `show ip interface brief`, `show ip route`.

## Remote access (Telnet and SSH)

Devices accept remote logins over Telnet (TCP 23) and SSH (TCP 22). Simulation mode shows the TCP handshake; Telnet packet details reveal even the password, SSH contents are encrypted. The connection opens in the current console and `exit` returns. You can hop on from a remote console. By default the vty lines have no password and IOS answers `Password required, but none set`. SSH needs a hostname, a domain, RSA keys and a local user.

```
enable
configure terminal
hostname R2
ip domain-name lab.local
crypto key generate rsa modulus 1024
username admin secret Cisco123
enable secret class
line vty 0 4
login local
transport input ssh telnet
end
```

Connect from another device with `ssh -l admin 10.0.0.2` or `telnet 10.0.0.2`. For a line password instead of users, use `password cisco` and `login` under `line vty 0 4`. Check: `show ip ssh`, `show users`. Once `enable secret` is set, `enable` asks for it on the console too, as on real IOS (three wrong tries give `% Bad secrets`).

## EtherChannel (link aggregation)

Several cables between two switches are combined into one logical channel. STP treats it as a single port and blocks nothing, and when one cable fails the traffic moves to the others. The channel forms when the modes at both ends match: `on` only with `on`; LACP `active` with `active` or `passive`; PAgP `desirable` with `desirable` or `auto`. Otherwise the ports stay suspended and carry no traffic.

```switch
enable
configure terminal
interface FastEthernet0/1
channel-group 1 mode active
interface FastEthernet0/2
channel-group 1 mode active
interface port-channel 1
switchport mode trunk
end
```

Check: `show etherchannel summary`.

## HSRP and VRRP (gateway redundancy)

Two routers on one network share a virtual IP address that PCs use as their gateway. The active (HSRP) or master (VRRP) router is elected by priority, then by the higher IP. It answers ARP with the virtual MAC. When it disappears, the backup takes over after the hold time (HSRP 10 s, VRRP about 3.6 s) and sends a gratuitous ARP. By default HSRP does not take the role back (`preempt` is needed), VRRP does.

```
enable
configure terminal
interface GigabitEthernet0/0
ip address 192.168.1.2 255.255.255.0
no shutdown
standby 1 ip 192.168.1.254
standby 1 priority 110
standby 1 preempt
end
```

On the second router use the same `standby 1 ip`, its own address and the default priority (100). VRRP: `vrrp 1 ip …`, `vrrp 1 priority …`. Check: `show standby brief`, `show vrrp brief`.

## OSPFv3, RIPng and DHCPv6

Routers find neighbours with hellos to ff02::5 (OSPFv3) and ff02::9 (RIPng) sent from their link-local addresses and exchange routes to IPv6 networks. The next hop in the table is the neighbour's link-local address. Stateful DHCPv6 leases addresses from a pool; PCs still learn the default gateway from RAs.

```
enable
configure terminal
ipv6 unicast-routing
ipv6 router ospf 1
router-id 1.1.1.1
exit
interface GigabitEthernet0/1
ipv6 address 2001:db8:12::1/64
ipv6 ospf 1 area 0
no shutdown
exit
ipv6 dhcp pool LAN6
address prefix 2001:db8:1::/64
dns-server 2001:db8:1::53
exit
interface GigabitEthernet0/0
ipv6 address 2001:db8:1::1/64
ipv6 ospf 1 area 0
ipv6 dhcp server LAN6
no shutdown
end
```

RIPng: `ipv6 router rip LAB` and `ipv6 rip LAB enable` on the interface. Check: `show ipv6 ospf neighbor`, `show ipv6 rip`, `show ipv6 route`, `show ipv6 dhcp binding`.

## Port security and DHCP snooping

**Port security** limits the number of MAC addresses on an access port. The first addresses are remembered as allowed; a foreign MAC is a violation. `shutdown` (the default) puts the port into err-disabled, and shutting the port down and up again brings it back. `restrict` and `protect` just drop the frames. **DHCP snooping** passes DHCP server replies only from trusted ports, so a rogue server on the network hands out nothing, and keeps a table of the leased addresses.

```switch
enable
configure terminal
interface FastEthernet0/1
switchport mode access
switchport port-security
switchport port-security maximum 2
switchport port-security mac-address sticky
exit
ip dhcp snooping
ip dhcp snooping vlan 1
interface GigabitEthernet0/1
ip dhcp snooping trust
end
```

Check: `show port-security`, `show port-security address`, `show ip dhcp snooping binding`. Bring a port back from err-disabled with `shutdown`, then `no shutdown`.

## GRE and IPsec (site-to-site VPN)

A tunnel is a virtual interface: packets routed into it are wrapped in GRE (IP protocol 47) and sent to the `tunnel destination` through the normal routing table, for example across the Internet cloud. Static routes and OSPF run over the tunnel. `tunnel protection ipsec profile` adds ESP encryption: the packet contents are hidden in the simulation, and with different keys at the two ends no traffic passes.

```
enable
configure terminal
crypto isakmp policy 10
encryption aes 256
authentication pre-share
group 14
exit
crypto isakmp key S3cret! address 198.51.100.2
crypto ipsec transform-set TS esp-aes 256 esp-sha-hmac
exit
crypto ipsec profile VPN
set transform-set TS
exit
interface Tunnel0
ip address 10.255.0.1 255.255.255.252
tunnel source GigabitEthernet0/1
tunnel destination 198.51.100.2
tunnel protection ipsec profile VPN
exit
ip route 192.168.2.0 255.255.255.0 Tunnel0
end
```

Check: `show crypto ipsec sa`, `show crypto isakmp sa`, `show ip interface brief`.

## QoS: classes, marking and policing

A policy (MQC) is a list of classes. The first matching class sets DSCP (`set dscp`) and/or polices to a rate (`police`): packets above the rate are dropped. The DSCP value appears in the IPv4 packet details in simulation mode; `show policy-map interface` shows the counters.

```
enable
configure terminal
class-map match-any VOICE
match protocol udp
exit
class-map match-any PING
match protocol icmp
exit
policy-map WAN-OUT
class VOICE
set dscp ef
exit
class PING
police 64000
exit
class class-default
set dscp default
exit
exit
interface GigabitEthernet0/1
service-policy output WAN-OUT
end
```

Check: `show policy-map`, `show policy-map interface`.

## NAT64

Stateful NAT64 lets IPv6-only hosts reach IPv4 servers. The destination is written as the 64:ff9b::/96 prefix plus the IPv4 address (`ping 64:ff9b::198.51.100.20`); the router puts its own IPv4 address on the packet and keeps a session table. ICMP echo is translated.

```
enable
configure terminal
ipv6 unicast-routing
interface GigabitEthernet0/2
ipv6 address 2001:db8:64::1/64
nat64 enable
no shutdown
exit
nat64 prefix stateful 64:ff9b::/96
end
```

Check: `show nat64 translations`.

## Syslog, SNMP and NTP

Interface state changes and leaving configuration mode are logged to the buffer (`show logging`) and sent to the syslog server: a Server device (Services tab) or a Linux server (`cat /var/log/syslog`). The SNMP agent answers `snmpget`, `snmpwalk` and `snmpset` from Linux hosts when the community matches. The NTP client synchronises the clock (`show clock` loses its asterisk); `ntp master` makes the router a time source.

```
enable
configure terminal
logging host 192.168.1.100
logging trap informational
snmp-server community public RO
snmp-server community private RW
snmp-server location Server-room-1
snmp-server contact admin@example.com
ntp server 192.168.1.100
end
```

Check: `show logging`, `show ntp status`, `show ntp associations`, `show snmp`, `show clock`.

## IP telephony: CME and voice VLAN

The IP Phone 7960 (palette, End devices) has two ports: **Switch** goes to the switch, **PC** to a computer. The phone learns the voice VLAN from the switch port (what CDP/LLDP-MED do), gets an address by DHCP in that VLAN and registers with the Cisco CME router, whose address comes in DHCP option 150. Numbers are handed out from the `ephone-dn`s by `auto assign`. Calls are made on the Phone tab (dial, Call, Answer, Hang up); during a call the phones exchange RTP packets marked DSCP EF.

```
enable
configure terminal
ip dhcp pool VOICE
network 192.168.20.0 255.255.255.0
default-router 192.168.20.1
option 150 ip 192.168.20.1
exit
telephony-service
max-ephones 10
max-dn 10
ip source-address 192.168.20.1 port 2000
auto assign 1 to 10
exit
ephone-dn 1
number 1001
exit
ephone-dn 2
number 1002
end
```

On the switch the phone port stays an access port in the data VLAN, with the voice VLAN on top. `interface range` configures several ports at once:

```switch
enable
configure terminal
vlan 10
vlan 20
exit
interface range fa0/1 - 4
switchport mode access
switchport access vlan 10
switchport voice vlan 20
end
```

Check: `show ephone`, `show ephone registered`, `show ephone-dn summary`, `show telephony-service`.

## DDoS protection

Attacks and defences are explained in [DDoS attacks and defence](ddos.md). The Cisco commands used there:

```
enable
configure terminal
interface gi0/2
ip verify unicast source reachable-via rx
exit
ip route 203.0.113.99 255.255.255.255 Null0
access-list 150 permit tcp any host 203.0.113.80 eq 80
ip tcp intercept list 150
ip tcp intercept mode intercept
access-list 151 permit udp any any eq 123
class-map match-all NTP-FLOOD
match access-group 151
exit
policy-map DDOS-IN
class NTP-FLOOD
police 2000000
exit
exit
end
```

- `ip verify unicast source reachable-via rx`: strict uRPF (BCP 38); packets whose route back to the source leaves through another interface are dropped; `reachable-via any` is loose mode.
- `ip route … Null0`: a null route, all traffic to the address is dropped (RTBH, sinkhole).
- `ip tcp intercept list`: the router completes the TCP handshake with clients itself and lets only real connections through to the server (SYN flood protection).
- A `police` in a policy-map limits a traffic class to a rate.

## IPS: against worms and C2 servers

The intrusion prevention system drops the training worms' exploit packets and the trojans' contacts with their command-and-control server (see [Malware](malware.md)):

```
enable
configure terminal
ip ips name OFFICE
interface gi0/0
ip ips OFFICE in
end
show ip ips statistics
```

## All commands: Cisco 2911 router

Descriptions come from the CLI's own help (`?`). `A.B.C.D` is an IP address or mask, `IFACE` an interface name (abbreviations work: `g0/0`), `WORD` a word, `LINE` the rest of the line.

<!-- BEGIN GENERATED COMMANDS router -->
#### User EXEC mode — `Router>`

| Command | Description |
|---|---|
| `enable` | Turn on privileged commands |
| `exit` | Exit from the EXEC |
| `logout` | Exit from the EXEC |
| `ping ipv6 X:X:X:X::X` | Ping destination address |
| `ping WORD` | Ping destination address |
| `ping WORD repeat <1-2147483647>` | Repeat count |
| `ping vrf WORD A.B.C.D` | Destination address |
| `show access-lists` | List access lists |
| `show access-lists <1-2699>/WORD` | Access list number or name |
| `show arp` | Arp table |
| `show clock` | Display the system clock |
| `show history` | Display the session command history |
| `show interfaces` | Interface status and configuration |
| `show interfaces IFACE` | Interface name |
| `show ip route` | IP routing table |
| `show ip route ospf` | Open Shortest Path First (OSPF) |
| `show ip route static` | Static routes |
| `show ip route connected` | Connected networks |
| `show ip route bgp` | Border Gateway Protocol (BGP) |
| `show ip route rip` | Routing Information Protocol (RIP) |
| `show ip interface brief` | Brief summary of IP status and configuration |
| `show ip ospf` | OSPF information |
| `show ip ospf neighbor` | Neighbor list |
| `show ip ospf neighbor detail` | detail of all neighbors |
| `show ip ospf interface` | Interface information |
| `show ip ospf interface brief` | Brief summary of OSPF interfaces |
| `show ip ospf database` | Database summary |
| `show ip protocols` | IP routing protocol process parameters and statistics |
| `show ip rip database` | IP RIP database |
| `show ip dhcp binding` | DHCP address bindings |
| `show ip dhcp pool` | DHCP pools information |
| `show ip bgp` | BGP information |
| `show ip bgp summary` | Summary of BGP neighbor status |
| `show ip bgp neighbors` | Detailed information on TCP and BGP neighbor connections |
| `show ip bgp neighbors A.B.C.D` | Neighbor to display information about |
| `show ip nat translations` | Translation entries |
| `show ip nat translations verbose` | Show extra information |
| `show ip nat statistics` | Translation statistics |
| `show ip access-lists` | List IP access lists |
| `show ip access-lists <1-2699>/WORD` | Access list number or name |
| `show ip arp` | IP ARP table |
| `show ipv6 interface brief` | Brief summary of IPv6 status and configuration |
| `show ipv6 route` | Show IPv6 route table entries |
| `show ipv6 neighbors` | Show IPv6 neighbor cache entries |
| `show version` | System hardware and software status |
| `ssh -l WORD A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D <1-65535>` | Port number |
| `traceroute ipv6 X:X:X:X::X` | Trace route to destination address |
| `traceroute WORD` | Trace route to destination address |
| `traceroute vrf WORD A.B.C.D` | Destination address |

#### Privileged EXEC mode (enable) — `Router#`

| Command | Description |
|---|---|
| `clear arp-cache` | Clear the entire ARP cache |
| `clear ip dhcp binding *` | Clear all automatic bindings |
| `clear ip bgp *` | Clear all peers |
| `clear ip bgp A.B.C.D` | BGP neighbor address to clear |
| `clear ip nat translation *` | Delete all dynamic translations |
| `clear access-list counters` | Clear access list counters |
| `configure` | Enter configuration mode |
| `configure terminal` | Configure from the terminal |
| `copy running-config startup-config` | Copy to startup configuration |
| `copy running-config tftp: / ftp: / tftp://A.B.C.D/file / ftp://user:pass@A.B.C.D/file` | Copy to a remote server |
| `copy startup-config tftp: / ftp: / URL` | Copy to a remote server |
| `copy tftp: / ftp: / tftp://A.B.C.D/file running-config` | Update (merge with) current system configuration |
| `copy tftp: / ftp: / tftp://A.B.C.D/file startup-config` | Copy to startup configuration |
| `disable` | Turn off privileged commands |
| `enable` | Turn on privileged commands |
| `erase startup-config` | Erase contents of configuration memory |
| `exit` | Exit from the EXEC |
| `logout` | Exit from the EXEC |
| `ping ipv6 X:X:X:X::X` | Ping destination address |
| `ping WORD` | Ping destination address |
| `ping WORD repeat <1-2147483647>` | Repeat count |
| `ping vrf WORD A.B.C.D` | Destination address |
| `reload` | Halt and perform a cold restart |
| `show access-lists` | List access lists |
| `show access-lists <1-2699>/WORD` | Access list number or name |
| `show arp` | Arp table |
| `show clock` | Display the system clock |
| `show history` | Display the session command history |
| `show interfaces` | Interface status and configuration |
| `show interfaces IFACE` | Interface name |
| `show ip route` | IP routing table |
| `show ip route ospf` | Open Shortest Path First (OSPF) |
| `show ip route static` | Static routes |
| `show ip route connected` | Connected networks |
| `show ip route bgp` | Border Gateway Protocol (BGP) |
| `show ip route rip` | Routing Information Protocol (RIP) |
| `show ip route vrf WORD` | VPN Routing/Forwarding instance name |
| `show ip route isis` | ISO IS-IS |
| `show ip route eigrp` | Enhanced Interior Gateway Routing Protocol (EIGRP) |
| `show ip interface brief` | Brief summary of IP status and configuration |
| `show ip ospf` | OSPF information |
| `show ip ospf neighbor` | Neighbor list |
| `show ip ospf neighbor detail` | detail of all neighbors |
| `show ip ospf interface` | Interface information |
| `show ip ospf interface brief` | Brief summary of OSPF interfaces |
| `show ip ospf database` | Database summary |
| `show ip protocols` | IP routing protocol process parameters and statistics |
| `show ip rip database` | IP RIP database |
| `show ip dhcp binding` | DHCP address bindings |
| `show ip dhcp pool` | DHCP pools information |
| `show ip bgp` | BGP information |
| `show ip bgp summary` | Summary of BGP neighbor status |
| `show ip bgp neighbors` | Detailed information on TCP and BGP neighbor connections |
| `show ip bgp neighbors A.B.C.D` | Neighbor to display information about |
| `show ip bgp vpnv4 all` | Display information about all VPNv4 NLRIs |
| `show ip nat translations` | Translation entries |
| `show ip nat translations verbose` | Show extra information |
| `show ip nat statistics` | Translation statistics |
| `show ip access-lists` | List IP access lists |
| `show ip access-lists <1-2699>/WORD` | Access list number or name |
| `show ip arp` | IP ARP table |
| `show ip ssh` | Information on SSH |
| `show ip vrf` | VPN Routing/Forwarding instance information |
| `show ip vrf brief` | Brief VPN Routing/Forwarding instance information |
| `show ip ips` | IPS information |
| `show ip ips statistics` | IPS statistics |
| `show ip ips all` | IPS configuration and statistics |
| `show ip rsvp interface` | RSVP interface information |
| `show ip explicit-paths` | Show the explicit paths |
| `show ip eigrp neighbors` | IP-EIGRP neighbors |
| `show ip eigrp topology` | IP-EIGRP Topology Table |
| `show ip eigrp interfaces` | IP-EIGRP interfaces |
| `show ipv6 interface brief` | Brief summary of IPv6 status and configuration |
| `show ipv6 route` | Show IPv6 route table entries |
| `show ipv6 route eigrp` | EIGRP routes |
| `show ipv6 route isis` | IS-IS routes |
| `show ipv6 route ospf` | OSPF routes |
| `show ipv6 route static` | Static routes |
| `show ipv6 route connected` | Connected routes |
| `show ipv6 neighbors` | Show IPv6 neighbor cache entries |
| `show ipv6 dhcp binding` | Show DHCPv6 bindings |
| `show ipv6 ospf neighbor` | Neighbor list |
| `show ipv6 ospf database` | Database summary |
| `show ipv6 rip` | RIP routing protocol status |
| `show ipv6 eigrp neighbors` | IPv6 EIGRP neighbors |
| `show running-config` | Current operating configuration |
| `show startup-config` | Contents of startup configuration |
| `show version` | System hardware and software status |
| `show users` | Display information about terminal lines |
| `show bgp vpnv4 unicast all` | Display information about all VPNv4 NLRIs |
| `show bgp vpnv4 all` | Display information about all VPNv4 NLRIs |
| `show isis neighbors` | IS-IS neighbors |
| `show isis database` | IS-IS link state database |
| `show clns neighbors` | CLNS neighbor adjacencies |
| `show mpls traffic-eng tunnels` | Traffic Engineering tunnels |
| `show mpls traffic-eng tunnels brief` | Brief summary of tunnel status and configuration |
| `show mpls ldp neighbor` | Display LDP neighbor information |
| `show mpls ldp bindings` | Show the LDP Label Information Base (LIB) |
| `show mpls forwarding-table` | Show the Label Forwarding Information Base (LFIB) |
| `show mpls interfaces` | Per-interface MPLS forwarding information |
| `show ephone` | Show ephone status |
| `show ephone registered` | Registered ephones |
| `show ephone-dn summary` | Summary |
| `show telephony-service` | Show telephony-service |
| `show crypto ipsec sa` | IPSEC SA table |
| `show crypto isakmp sa` | ISAKMP SA table |
| `show policy-map` | Show QoS Policy Map |
| `show policy-map interface` | Show Qos Policy Map on interfaces |
| `show policy-map interface IFACE` | Interface |
| `show policy-map type inspect zone-pair` | zone-pair |
| `show policy-map type inspect zone-pair sessions` | Inspect sessions |
| `show nat64 translations` | Translations |
| `show logging` | Show the contents of logging buffers |
| `show ntp status` | NTP status |
| `show ntp associations` | NTP associations |
| `show snmp` | snmp statistics |
| `show snmp community` | show snmp communities |
| `show standby` | Hot standby protocol information |
| `show standby brief` | Brief output |
| `show vrrp` | VRRP information |
| `show vrrp brief` | Brief output |
| `show zone security` | Security zone |
| `show zone-pair security` | Security zone-pair |
| `show aaa servers` | Server information |
| `ssh -l WORD A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D <1-65535>` | Port number |
| `traceroute ipv6 X:X:X:X::X` | Trace route to destination address |
| `traceroute WORD` | Trace route to destination address |
| `traceroute vrf WORD A.B.C.D` | Destination address |
| `write` | Write running configuration to memory |
| `write memory` | Write to NV memory |
| `write erase` | Erase NV memory |

#### Global configuration (configure terminal) — `Router(config)#`

| Command | Description |
|---|---|
| `[no] access-list <1-2699>` | IP access list number |
| `[no] access-list <1-2699> permit LINE` | Source/protocol specification |
| `[no] access-list <1-2699> deny LINE` | Source/protocol specification |
| `[no] access-list <1-2699> remark LINE` | Comment up to 100 characters |
| `[no] class-map match-any WORD` | class-map name |
| `[no] class-map match-all WORD` | class-map name |
| `[no] class-map WORD` | class-map name |
| `[no] class-map type inspect match-any WORD` | class-map name |
| `[no] class-map type inspect match-all WORD` | class-map name |
| `[no] class-map type inspect WORD` | class-map name |
| `[no] crypto key generate rsa` | Generate RSA keys |
| `[no] crypto key generate rsa general-keys` | Generate a general purpose RSA key pair |
| `[no] crypto key generate rsa general-keys modulus <360-4096>` | size of the key modulus |
| `[no] crypto key generate rsa modulus <360-4096>` | size of the key modulus |
| `[no] crypto key zeroize rsa` | Remove RSA keys |
| `[no] crypto isakmp policy <1-10000>` | Priority |
| `[no] crypto isakmp key WORD address A.B.C.D` | Peer IP address |
| `[no] crypto ipsec transform-set WORD esp-aes 256 esp-sha-hmac` | Transforms |
| `[no] crypto ipsec profile WORD` | Profile name |
| `do LINE` | Exec Command |
| `[no] enable secret` | Assign the privileged level secret |
| `[no] enable secret LINE` | The UNENCRYPTED (cleartext) 'enable' secret |
| `[no] enable password LINE` | The UNENCRYPTED (cleartext) 'enable' password |
| `end` | Exit from configure mode |
| `[no] ephone <1-240>` | ephone tag |
| `[no] ephone-dn <1-288>` | ephone-dn tag |
| `exit` | Exit from configure mode |
| `[no] hostname` | Set system's network name |
| `[no] hostname WORD` | This system's network name |
| `[no] interface range fa0/1 - 4 , gi0/1` | Interface list |
| `[no] interface IFACE` | Interface name |
| `[no] interface IFACE.N` | Subinterface number |
| `[no] interface <0-2147483647>` | Loopback interface |
| `[no] interface Tunnel <0-2147483647>` | Tunnel interface |
| `ip dhcp pool WORD` | Pool name |
| `ip dhcp excluded-address A.B.C.D` | Low IP address |
| `ip dhcp excluded-address A.B.C.D A.B.C.D` | High IP address |
| `ip dhcp relay information trust-all` | Received DHCP packets may contain relay info option with zero giaddr |
| `ip access-list standard <1-99>/WORD` | Access-list name |
| `ip access-list extended <100-199>/WORD` | Access-list name |
| `ip nat inside source list <1-2699>/WORD interface IFACE` | Interface name |
| `ip nat inside source list <1-2699>/WORD interface IFACE overload` | Overload an address translation |
| `ip nat inside source list <1-2699>/WORD pool WORD` | Pool name for global addresses |
| `ip nat inside source list <1-2699>/WORD pool WORD overload` | Overload an address translation |
| `ip nat inside source static A.B.C.D A.B.C.D` | Inside global IP address |
| `ip nat inside source static tcp A.B.C.D <1-65535> A.B.C.D <1-65535>` | Global UDP/TCP port |
| `ip nat inside source static udp A.B.C.D <1-65535> A.B.C.D <1-65535>` | Global UDP/TCP port |
| `ip nat pool WORD` | Pool name |
| `ip nat pool WORD A.B.C.D A.B.C.D netmask A.B.C.D` | Network mask |
| `ip nat pool WORD A.B.C.D A.B.C.D prefix-length <1-32>` | Prefix length |
| `ip route A.B.C.D A.B.C.D` | Destination prefix mask |
| `ip route A.B.C.D A.B.C.D Null0` | Null interface (discard: RTBH, sinkhole) |
| `ip route A.B.C.D A.B.C.D Null0 <1-255>` | Distance metric for this route |
| `ip route A.B.C.D A.B.C.D A.B.C.D` | Forwarding router's address |
| `ip route A.B.C.D A.B.C.D A.B.C.D <1-255>` | Distance metric for this route |
| `ip route A.B.C.D A.B.C.D IFACE` | Exit interface |
| `ip route A.B.C.D A.B.C.D IFACE A.B.C.D` | Forwarding router's address |
| `ip route A.B.C.D A.B.C.D IFACE <1-255>` | Distance metric for this route |
| `ip route vrf WORD A.B.C.D A.B.C.D A.B.C.D` | Forwarding router's address |
| `ip route vrf WORD A.B.C.D A.B.C.D IFACE` | Exit interface |
| `ip routing` | Enable IP routing |
| `ip classless` | Follow classless routing forwarding rules |
| `ip domain-name WORD` | Default domain name |
| `ip ssh version <1-2>` | Protocol version |
| `ip vrf WORD` | VRF name |
| `ip tcp intercept list` | Specify access-list to use |
| `ip tcp intercept list <100-199>` | Access list number or name |
| `ip tcp intercept mode intercept` | Intercept connections |
| `ip tcp intercept mode watch` | Watch connections |
| `ip ips name WORD` | IPS rule name |
| `ip explicit-path name WORD` | Explicit path name |
| `ip explicit-path name WORD enable` | Enable the path |
| `[no] ipv6 unicast-routing` | Enable unicast routing |
| `[no] ipv6 route X:X:X:X::X/<0-128>` | IPv6 prefix |
| `[no] ipv6 route X:X:X:X::X/<0-128> X:X:X:X::X` | IPv6 address of next-hop |
| `[no] ipv6 route X:X:X:X::X/<0-128> IFACE` | Outgoing interface |
| `[no] ipv6 route X:X:X:X::X/<0-128> IFACE X:X:X:X::X` | IPv6 address of next-hop |
| `[no] ipv6 router ospf <1-65535>` | Process ID |
| `[no] ipv6 router rip WORD` | User selected string identifying this process |
| `[no] ipv6 router eigrp <1-65535>` | Autonomous System |
| `[no] ipv6 dhcp pool WORD` | DHCP pool name |
| `line vty <0-15>` | First Line number |
| `line vty <0-15> <1-15>` | Last Line number |
| `line console <0-0>` | First Line number |
| `[no] logging host A.B.C.D` | IP address of the syslog server |
| `[no] logging trap emergencies` | Emergencies conditions (0) |
| `[no] logging trap alerts` | Alerts conditions (1) |
| `[no] logging trap critical` | Critical conditions (2) |
| `[no] logging trap errors` | Errors conditions (3) |
| `[no] logging trap warnings` | Warnings conditions (4) |
| `[no] logging trap notifications` | Notifications conditions (5) |
| `[no] logging trap informational` | Informational conditions (6) |
| `[no] logging trap debugging` | Debugging conditions (7) |
| `[no] logging buffered` | Set buffered logging parameters |
| `[no] logging A.B.C.D` | IP address of the logging host |
| `[no] mpls label protocol ldp` | Use LDP (default) |
| `[no] mpls ldp router-id IFACE` | Interface |
| `[no] mpls ldp router-id IFACE force` | Forcibly change the LDP router id |
| `[no] mpls ip` | Dynamic MPLS forwarding for IP |
| `[no] mpls traffic-eng tunnels` | Enable MPLS Traffic Engineering tunnels |
| `[no] mpls traffic-eng router-id IFACE` | Interface |
| `[no] mpls traffic-eng area <0-4294967295>` | OSPF area ID |
| `[no] nat64 prefix stateful X:X:X:X::X/<0-128>` | Stateful prefix (/96) |
| `[no] nat64 v4 pool WORD A.B.C.D A.B.C.D` | End |
| `[no] nat64 v6v4 list ACL pool POOL overload` | IPv6 to IPv4 mapping |
| `[no] ntp server A.B.C.D` | IP address of peer |
| `[no] ntp master` | Act as NTP master clock |
| `[no] ntp master <1-15>` | Stratum number |
| `[no] policy-map WORD` | policy-map name |
| `[no] policy-map type inspect WORD` | policy-map name |
| `[no] router ospf <1-65535>` | Process ID |
| `[no] router rip` | Routing Information Protocol (RIP) |
| `[no] router isis` | ISO IS-IS |
| `[no] router isis WORD` | ISO routing area tag |
| `[no] router eigrp <1-65535>` | Autonomous System |
| `[no] router bgp <1-4294967295>` | Autonomous system number |
| `[no] snmp-server community WORD` | SNMP community string |
| `[no] snmp-server community WORD RO` | Read-only access with this community string |
| `[no] snmp-server community WORD RW` | Read-write access with this community string |
| `[no] snmp-server location` | Text for mib object sysLocation |
| `[no] snmp-server location LINE` | The physical location of this node |
| `[no] snmp-server contact` | Text for mib object sysContact |
| `[no] snmp-server contact LINE` | identification of the contact person |
| `[no] snmp-server enable LINE` | traps |
| `[no] telephony-service` | Configure Cisco Unified Communications Manager Express |
| `[no] username WORD` | User name |
| `[no] username WORD privilege <0-15> secret LINE` | The UNENCRYPTED (cleartext) user secret |
| `[no] username WORD privilege <0-15> password LINE` | The UNENCRYPTED (cleartext) user password |
| `[no] username WORD secret LINE` | The UNENCRYPTED (cleartext) user secret |
| `[no] username WORD password LINE` | The UNENCRYPTED (cleartext) user password |
| `[no] vrf definition WORD` | VRF name |
| `[no] zone security WORD` | Zone name |
| `[no] zone-pair security WORD` | Zone-pair name |
| `[no] zone-pair security WORD source WORD destination WORD` | Destination zone |
| `[no] aaa new-model` | Enable NEW access control commands and functions |
| `[no] aaa authentication login default group tacacs+ / group radius / local ...` | Method list |
| `[no] aaa authentication dot1x default group radius` | Method list |
| `[no] tacacs server WORD` | Name for the tacacs server configuration |
| `[no] radius server WORD` | Name for the radius server configuration |
| `[no] tacacs-server host A.B.C.D` | IP address |
| `[no] tacacs-server host A.B.C.D key LINE` | The UNENCRYPTED server key |
| `[no] radius-server host A.B.C.D` | IP address |
| `[no] radius-server host A.B.C.D key LINE` | The UNENCRYPTED server key |
| `[no] radius-server host A.B.C.D auth-port 1812 acct-port 1813 key K` | options |
| `[no] dot1x system-auth-control` | Enable or Disable SysAuthControl |

#### Interface configuration (interface …) — `Router(config-if)#`

| Command | Description |
|---|---|
| `[no] bandwidth` | Set bandwidth informational parameter |
| `[no] bandwidth <1-10000000>` | Bandwidth in kilobits |
| `[no] delay` | Specify interface throughput delay |
| `[no] delay <1-16777215>` | Throughput delay (tens of microseconds) |
| `[no] description` | Interface specific description |
| `[no] description LINE` | Up to 240 characters describing this interface |
| `do LINE` | Exec Command |
| `duplex auto/full/half` | Configure duplex operation. |
| `end` | Exit from configure mode |
| `exit` | Exit from interface configuration mode |
| `[no] interface range fa0/1 - 4 , gi0/1` | Interface list |
| `[no] interface IFACE` | Interface name |
| `[no] interface IFACE.N` | Subinterface number |
| `[no] interface <0-2147483647>` | Loopback interface |
| `[no] interface Tunnel <0-2147483647>` | Tunnel interface |
| `ip helper-address` | Specify a destination address for UDP broadcasts |
| `ip helper-address A.B.C.D` | IP destination address |
| `ip access-group <1-2699>/WORD in` | inbound packets |
| `ip access-group <1-2699>/WORD out` | outbound packets |
| `ip nat inside` | Inside interface for address translation |
| `ip nat outside` | Outside interface for address translation |
| `ip ospf cost` | Interface cost |
| `ip ospf cost <1-65535>` | Interface cost |
| `ip ospf hello-interval` | Time between HELLO packets |
| `ip ospf hello-interval <1-65535>` | Time between HELLO packets |
| `ip ospf dead-interval` | Interval after which a neighbor is declared dead |
| `ip ospf dead-interval <1-65535>` | Interval after which a neighbor is declared dead |
| `ip ospf priority` | Router priority |
| `ip ospf priority <0-255>` | Router priority |
| `ip address` | Set the IP address of an interface |
| `ip address A.B.C.D A.B.C.D` | IP subnet mask |
| `ip address A.B.C.D A.B.C.D secondary` | Make this IP address a secondary address |
| `ip vrf forwarding` | Configure forwarding table |
| `ip vrf forwarding WORD` | VRF name |
| `ip verify unicast source reachable-via` | Specify reachability check to apply to the source address |
| `ip verify unicast source reachable-via rx` | Source is reachable via interface on which packet was received (strict) |
| `ip verify unicast source reachable-via rx LINE` | allow-default / allow-self-ping |
| `ip verify unicast source reachable-via any` | Source is reachable via any interface (loose) |
| `ip ips WORD in` | Inbound IPS |
| `ip ips WORD out` | Outbound IPS (inspected on input in NetEmu) |
| `ip router isis` | IS-IS Routing for IP |
| `ip router isis WORD` | Routing process tag |
| `ip rsvp bandwidth` | RSVP reservable bandwidth (KBPS) |
| `ip rsvp bandwidth <1-10000000>` | Reservable bandwidth (KBPS) on interface |
| `ip unnumbered IFACE` | Interface |
| `[no] ipv6 address X:X:X:X::X/<0-128>` | IPv6 prefix |
| `[no] ipv6 enable` | Enable IPv6 on interface |
| `[no] ipv6 nd ra suppress` | Suppress IPv6 Router Advertisements |
| `[no] ipv6 ospf <1-65535> area <0-4294967295>/A.B.C.D` | OSPF area ID |
| `[no] ipv6 ospf cost <1-65535>` | Cost |
| `[no] ipv6 rip WORD enable` | Enable RIP routing on interface |
| `[no] ipv6 eigrp <1-65535>` | Autonomous system number |
| `[no] ipv6 router isis` | IS-IS Routing for IPv6 |
| `[no] ipv6 router isis WORD` | Routing process tag |
| `[no] ipv6 dhcp server WORD` | Name of IPv6 DHCP pool |
| `[no] isis metric <1-16777214>` | Default metric |
| `[no] isis metric <1-16777214> LINE` | level |
| `[no] isis circuit-type` | Configure circuit type for interface |
| `[no] isis circuit-type LINE` | Configure circuit type for interface |
| `[no] mpls traffic-eng tunnels` | Enable MPLS Traffic Engineering tunnels |
| `[no] mpls traffic-eng router-id IFACE` | Interface |
| `[no] mpls traffic-eng area <0-4294967295>` | OSPF area ID |
| `[no] mpls ip` | Configure dynamic MPLS forwarding for IP |
| `[no] mpls label protocol ldp` | Use LDP (default) |
| `[no] nat64 enable` | Enable NAT64 on the interface |
| `[no] service-policy input WORD` | policy-map name |
| `[no] service-policy output WORD` | policy-map name |
| `[no] shutdown` | Shutdown the selected interface |
| `speed 10/100/1000/auto` | Configure speed operation. |
| `[no] standby <0-255>` | group number |
| `[no] standby <0-255> ip A.B.C.D` | Virtual IP address |
| `[no] standby <0-255> priority <0-255>` | Priority value |
| `[no] standby <0-255> preempt` | Overthrow lower priority Active routers |
| `[no] tunnel source A.B.C.D` | ip address |
| `[no] tunnel source IFACE` | interface |
| `[no] tunnel destination A.B.C.D` | ip address |
| `[no] tunnel mode gre` | generic route encapsulation protocol |
| `[no] tunnel mode gre ip` | over IP |
| `[no] tunnel mode mpls traffic-eng` | Traffic engineering tunnels |
| `[no] tunnel mpls traffic-eng bandwidth <0-4294967295>` | Bandwidth requirement in kbps |
| `[no] tunnel mpls traffic-eng path-option <1-1000>` | The preference for this path option |
| `[no] tunnel mpls traffic-eng path-option <1-1000> dynamic` | setup based on dynamically calculated path |
| `[no] tunnel mpls traffic-eng path-option <1-1000> explicit name WORD` | Explicit path name |
| `[no] tunnel mpls traffic-eng autoroute announce` | announce tunnel tail as an IGP shortcut |
| `[no] tunnel mpls traffic-eng priority LINE` | setup and hold priority |
| `[no] tunnel protection ipsec profile WORD` | Name |
| `[no] vrf forwarding` | Configure forwarding table |
| `[no] vrf forwarding WORD` | VRF name |
| `[no] vrrp <1-255>` | group number |
| `[no] vrrp <1-255> ip A.B.C.D` | Virtual IP address |
| `[no] vrrp <1-255> priority <0-255>` | Priority value |
| `[no] vrrp <1-255> preempt` | Overthrow lower priority Active routers |
| `[no] zone-member security` | Security zone |
| `[no] zone-member security WORD` | Zone name |
<!-- END GENERATED COMMANDS -->

## All commands: Cisco 2960 switch

<!-- BEGIN GENERATED COMMANDS switch -->
#### User EXEC mode — `Switch>`

| Command | Description |
|---|---|
| `enable` | Turn on privileged commands |
| `exit` | Exit from the EXEC |
| `logout` | Exit from the EXEC |
| `ping ipv6 X:X:X:X::X` | Ping destination address |
| `ping WORD` | Ping destination address |
| `ping WORD repeat <1-2147483647>` | Repeat count |
| `show access-lists` | List access lists |
| `show access-lists <1-2699>/WORD` | Access list number or name |
| `show arp` | Arp table |
| `show clock` | Display the system clock |
| `show history` | Display the session command history |
| `show interfaces` | Interface status and configuration |
| `show interfaces IFACE` | Interface name |
| `show interfaces status` | Show interface line status |
| `show interfaces trunk` | Show interface trunk information |
| `show ip route` | IP routing table |
| `show ip route ospf` | Open Shortest Path First (OSPF) |
| `show ip route static` | Static routes |
| `show ip route connected` | Connected networks |
| `show ip route bgp` | Border Gateway Protocol (BGP) |
| `show ip route rip` | Routing Information Protocol (RIP) |
| `show ip interface brief` | Brief summary of IP status and configuration |
| `show ip ospf` | OSPF information |
| `show ip ospf neighbor` | Neighbor list |
| `show ip ospf neighbor detail` | detail of all neighbors |
| `show ip ospf interface` | Interface information |
| `show ip ospf interface brief` | Brief summary of OSPF interfaces |
| `show ip ospf database` | Database summary |
| `show ip protocols` | IP routing protocol process parameters and statistics |
| `show ip rip database` | IP RIP database |
| `show ip dhcp binding` | DHCP address bindings |
| `show ip dhcp pool` | DHCP pools information |
| `show ip bgp` | BGP information |
| `show ip bgp summary` | Summary of BGP neighbor status |
| `show ip bgp neighbors` | Detailed information on TCP and BGP neighbor connections |
| `show ip bgp neighbors A.B.C.D` | Neighbor to display information about |
| `show ip nat translations` | Translation entries |
| `show ip nat translations verbose` | Show extra information |
| `show ip nat statistics` | Translation statistics |
| `show ip access-lists` | List IP access lists |
| `show ip access-lists <1-2699>/WORD` | Access list number or name |
| `show ip arp` | IP ARP table |
| `show ipv6 interface brief` | Brief summary of IPv6 status and configuration |
| `show ipv6 route` | Show IPv6 route table entries |
| `show ipv6 neighbors` | Show IPv6 neighbor cache entries |
| `show mac address-table` | MAC forwarding table |
| `show spanning-tree` | Spanning tree topology |
| `show spanning-tree vlan <1-4094>` | VLAN |
| `show version` | System hardware and software status |
| `show vlan` | VTP VLAN status |
| `show vlan brief` | VTP all VLAN status in brief |
| `ssh -l WORD A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D <1-65535>` | Port number |
| `traceroute ipv6 X:X:X:X::X` | Trace route to destination address |
| `traceroute WORD` | Trace route to destination address |

#### Privileged EXEC mode (enable) — `Switch#`

| Command | Description |
|---|---|
| `clear mac-address-table` | MAC forwarding table |
| `clear mac-address-table dynamic` | dynamic entry type |
| `clear mac address-table` | MAC forwarding table |
| `clear mac address-table dynamic` | dynamic entry type |
| `configure` | Enter configuration mode |
| `configure terminal` | Configure from the terminal |
| `copy running-config startup-config` | Copy to startup configuration |
| `copy running-config tftp: / ftp: / tftp://A.B.C.D/file / ftp://user:pass@A.B.C.D/file` | Copy to a remote server |
| `copy startup-config tftp: / ftp: / URL` | Copy to a remote server |
| `copy tftp: / ftp: / tftp://A.B.C.D/file running-config` | Update (merge with) current system configuration |
| `copy tftp: / ftp: / tftp://A.B.C.D/file startup-config` | Copy to startup configuration |
| `disable` | Turn off privileged commands |
| `enable` | Turn on privileged commands |
| `erase startup-config` | Erase contents of configuration memory |
| `exit` | Exit from the EXEC |
| `logout` | Exit from the EXEC |
| `ping ipv6 X:X:X:X::X` | Ping destination address |
| `ping WORD` | Ping destination address |
| `ping WORD repeat <1-2147483647>` | Repeat count |
| `reload` | Halt and perform a cold restart |
| `show access-lists` | List access lists |
| `show access-lists <1-2699>/WORD` | Access list number or name |
| `show arp` | Arp table |
| `show clock` | Display the system clock |
| `show history` | Display the session command history |
| `show interfaces` | Interface status and configuration |
| `show interfaces IFACE` | Interface name |
| `show interfaces status` | Show interface line status |
| `show interfaces trunk` | Show interface trunk information |
| `show ip route` | IP routing table |
| `show ip route ospf` | Open Shortest Path First (OSPF) |
| `show ip route static` | Static routes |
| `show ip route connected` | Connected networks |
| `show ip route bgp` | Border Gateway Protocol (BGP) |
| `show ip route rip` | Routing Information Protocol (RIP) |
| `show ip interface brief` | Brief summary of IP status and configuration |
| `show ip ospf` | OSPF information |
| `show ip ospf neighbor` | Neighbor list |
| `show ip ospf neighbor detail` | detail of all neighbors |
| `show ip ospf interface` | Interface information |
| `show ip ospf interface brief` | Brief summary of OSPF interfaces |
| `show ip ospf database` | Database summary |
| `show ip protocols` | IP routing protocol process parameters and statistics |
| `show ip rip database` | IP RIP database |
| `show ip dhcp binding` | DHCP address bindings |
| `show ip dhcp pool` | DHCP pools information |
| `show ip dhcp snooping` | DHCP snooping |
| `show ip dhcp snooping binding` | DHCP snooping binding |
| `show ip bgp` | BGP information |
| `show ip bgp summary` | Summary of BGP neighbor status |
| `show ip bgp neighbors` | Detailed information on TCP and BGP neighbor connections |
| `show ip bgp neighbors A.B.C.D` | Neighbor to display information about |
| `show ip nat translations` | Translation entries |
| `show ip nat translations verbose` | Show extra information |
| `show ip nat statistics` | Translation statistics |
| `show ip access-lists` | List IP access lists |
| `show ip access-lists <1-2699>/WORD` | Access list number or name |
| `show ip arp` | IP ARP table |
| `show ip ssh` | Information on SSH |
| `show ipv6 interface brief` | Brief summary of IPv6 status and configuration |
| `show ipv6 route` | Show IPv6 route table entries |
| `show ipv6 route eigrp` | EIGRP routes |
| `show ipv6 route isis` | IS-IS routes |
| `show ipv6 route ospf` | OSPF routes |
| `show ipv6 route static` | Static routes |
| `show ipv6 route connected` | Connected routes |
| `show ipv6 neighbors` | Show IPv6 neighbor cache entries |
| `show ipv6 dhcp binding` | Show DHCPv6 bindings |
| `show ipv6 ospf neighbor` | Neighbor list |
| `show ipv6 ospf database` | Database summary |
| `show ipv6 rip` | RIP routing protocol status |
| `show ipv6 eigrp neighbors` | IPv6 EIGRP neighbors |
| `show mac address-table` | MAC forwarding table |
| `show running-config` | Current operating configuration |
| `show spanning-tree` | Spanning tree topology |
| `show spanning-tree vlan <1-4094>` | VLAN |
| `show startup-config` | Contents of startup configuration |
| `show version` | System hardware and software status |
| `show vlan` | VTP VLAN status |
| `show vlan brief` | VTP all VLAN status in brief |
| `show users` | Display information about terminal lines |
| `show logging` | Show the contents of logging buffers |
| `show ntp status` | NTP status |
| `show ntp associations` | NTP associations |
| `show snmp` | snmp statistics |
| `show snmp community` | show snmp communities |
| `show standby` | Hot standby protocol information |
| `show standby brief` | Brief output |
| `show vrrp` | VRRP information |
| `show vrrp brief` | Brief output |
| `show port-security` | Show secure port information |
| `show port-security interface IFACE` | Interface |
| `show port-security address` | Show secure address |
| `show etherchannel summary` | One-line summary per channel-group |
| `show dot1x all` | Show Dot1x information for all interfaces |
| `show authentication sessions` | Display sessions |
| `show aaa servers` | Server information |
| `ssh -l WORD A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D` | IP address of a remote system |
| `telnet A.B.C.D <1-65535>` | Port number |
| `traceroute ipv6 X:X:X:X::X` | Trace route to destination address |
| `traceroute WORD` | Trace route to destination address |
| `write` | Write running configuration to memory |
| `write memory` | Write to NV memory |
| `write erase` | Erase NV memory |

#### Global configuration (configure terminal) — `Switch(config)#`

| Command | Description |
|---|---|
| `[no] access-list <1-2699>` | IP access list number |
| `[no] access-list <1-2699> permit LINE` | Source/protocol specification |
| `[no] access-list <1-2699> deny LINE` | Source/protocol specification |
| `[no] access-list <1-2699> remark LINE` | Comment up to 100 characters |
| `crypto key generate rsa` | Generate RSA keys |
| `crypto key generate rsa general-keys` | Generate a general purpose RSA key pair |
| `crypto key generate rsa general-keys modulus <360-4096>` | size of the key modulus |
| `crypto key generate rsa modulus <360-4096>` | size of the key modulus |
| `crypto key zeroize rsa` | Remove RSA keys |
| `do LINE` | Exec Command |
| `[no] enable secret` | Assign the privileged level secret |
| `[no] enable secret LINE` | The UNENCRYPTED (cleartext) 'enable' secret |
| `[no] enable password LINE` | The UNENCRYPTED (cleartext) 'enable' password |
| `end` | Exit from configure mode |
| `exit` | Exit from configure mode |
| `[no] hostname` | Set system's network name |
| `[no] hostname WORD` | This system's network name |
| `[no] interface range fa0/1 - 4 , gi0/1` | Interface list |
| `[no] interface IFACE` | Interface name |
| `[no] interface Vlan <1-4094>` | Vlan interface |
| `[no] interface <0-2147483647>` | Loopback interface |
| `[no] interface Port-channel <1-48>` | Ethernet Channel of interfaces |
| `ip dhcp pool WORD` | Pool name |
| `ip dhcp excluded-address A.B.C.D` | Low IP address |
| `ip dhcp excluded-address A.B.C.D A.B.C.D` | High IP address |
| `ip dhcp snooping` | DHCP Snooping |
| `ip dhcp snooping information option` | DHCP Snooping information option (option 82) |
| `ip dhcp snooping vlan WORD` | DHCP Snooping vlan range |
| `ip access-list standard <1-99>/WORD` | Access-list name |
| `ip access-list extended <100-199>/WORD` | Access-list name |
| `ip nat inside source list <1-2699>/WORD interface IFACE` | Interface name |
| `ip nat inside source list <1-2699>/WORD interface IFACE overload` | Overload an address translation |
| `ip nat inside source list <1-2699>/WORD pool WORD` | Pool name for global addresses |
| `ip nat inside source list <1-2699>/WORD pool WORD overload` | Overload an address translation |
| `ip nat inside source static A.B.C.D A.B.C.D` | Inside global IP address |
| `ip nat inside source static tcp A.B.C.D <1-65535> A.B.C.D <1-65535>` | Global UDP/TCP port |
| `ip nat inside source static udp A.B.C.D <1-65535> A.B.C.D <1-65535>` | Global UDP/TCP port |
| `ip nat pool WORD` | Pool name |
| `ip nat pool WORD A.B.C.D A.B.C.D netmask A.B.C.D` | Network mask |
| `ip nat pool WORD A.B.C.D A.B.C.D prefix-length <1-32>` | Prefix length |
| `ip route A.B.C.D A.B.C.D` | Destination prefix mask |
| `ip route A.B.C.D A.B.C.D Null0` | Null interface (discard: RTBH, sinkhole) |
| `ip route A.B.C.D A.B.C.D Null0 <1-255>` | Distance metric for this route |
| `ip route A.B.C.D A.B.C.D A.B.C.D` | Forwarding router's address |
| `ip route A.B.C.D A.B.C.D A.B.C.D <1-255>` | Distance metric for this route |
| `ip route A.B.C.D A.B.C.D IFACE` | Exit interface |
| `ip route A.B.C.D A.B.C.D IFACE A.B.C.D` | Forwarding router's address |
| `ip route A.B.C.D A.B.C.D IFACE <1-255>` | Distance metric for this route |
| `ip routing` | Enable IP routing |
| `ip default-gateway` | Specify default gateway (if not routing IP) |
| `ip default-gateway A.B.C.D` | IP address of default gateway |
| `ip classless` | Follow classless routing forwarding rules |
| `ip domain-name WORD` | Default domain name |
| `ip ssh version <1-2>` | Protocol version |
| `[no] ipv6 unicast-routing` | Enable unicast routing |
| `[no] ipv6 route X:X:X:X::X/<0-128>` | IPv6 prefix |
| `[no] ipv6 route X:X:X:X::X/<0-128> X:X:X:X::X` | IPv6 address of next-hop |
| `[no] ipv6 route X:X:X:X::X/<0-128> IFACE` | Outgoing interface |
| `[no] ipv6 route X:X:X:X::X/<0-128> IFACE X:X:X:X::X` | IPv6 address of next-hop |
| `[no] ipv6 router ospf <1-65535>` | Process ID |
| `[no] ipv6 router rip WORD` | User selected string identifying this process |
| `[no] ipv6 router eigrp <1-65535>` | Autonomous System |
| `[no] ipv6 dhcp pool WORD` | DHCP pool name |
| `line vty <0-15>` | First Line number |
| `line vty <0-15> <1-15>` | Last Line number |
| `line console <0-0>` | First Line number |
| `[no] logging host A.B.C.D` | IP address of the syslog server |
| `[no] logging trap emergencies` | Emergencies conditions (0) |
| `[no] logging trap alerts` | Alerts conditions (1) |
| `[no] logging trap critical` | Critical conditions (2) |
| `[no] logging trap errors` | Errors conditions (3) |
| `[no] logging trap warnings` | Warnings conditions (4) |
| `[no] logging trap notifications` | Notifications conditions (5) |
| `[no] logging trap informational` | Informational conditions (6) |
| `[no] logging trap debugging` | Debugging conditions (7) |
| `[no] logging buffered` | Set buffered logging parameters |
| `[no] logging A.B.C.D` | IP address of the logging host |
| `[no] ntp server A.B.C.D` | IP address of peer |
| `[no] ntp master` | Act as NTP master clock |
| `[no] ntp master <1-15>` | Stratum number |
| `[no] router ospf <1-65535>` | Process ID |
| `[no] router rip` | Routing Information Protocol (RIP) |
| `[no] router isis` | ISO IS-IS |
| `[no] router isis WORD` | ISO routing area tag |
| `[no] router eigrp <1-65535>` | Autonomous System |
| `[no] router bgp <1-4294967295>` | Autonomous system number |
| `[no] snmp-server community WORD` | SNMP community string |
| `[no] snmp-server community WORD RO` | Read-only access with this community string |
| `[no] snmp-server community WORD RW` | Read-write access with this community string |
| `[no] snmp-server location` | Text for mib object sysLocation |
| `[no] snmp-server location LINE` | The physical location of this node |
| `[no] snmp-server contact` | Text for mib object sysContact |
| `[no] snmp-server contact LINE` | identification of the contact person |
| `[no] snmp-server enable LINE` | traps |
| `[no] spanning-tree mode pvst` | Per-Vlan spanning tree mode |
| `[no] spanning-tree mode rapid-pvst` | Per-Vlan rapid spanning tree mode |
| `[no] spanning-tree vlan WORD` | Spanning tree VLAN id(s) |
| `[no] spanning-tree vlan WORD priority <0-61440>` | bridge priority in increments of 4096 |
| `[no] spanning-tree vlan WORD root primary` | Configure this switch as primary root for this spanning tree |
| `[no] spanning-tree vlan WORD root secondary` | Configure switch as secondary root |
| `[no] username WORD` | User name |
| `[no] username WORD privilege <0-15> secret LINE` | The UNENCRYPTED (cleartext) user secret |
| `[no] username WORD privilege <0-15> password LINE` | The UNENCRYPTED (cleartext) user password |
| `[no] username WORD secret LINE` | The UNENCRYPTED (cleartext) user secret |
| `[no] username WORD password LINE` | The UNENCRYPTED (cleartext) user password |
| `[no] vlan <1-4094>` | ISL VLAN IDs 1-1005 |
| `[no] aaa new-model` | Enable NEW access control commands and functions |
| `[no] aaa authentication login default group tacacs+ / group radius / local ...` | Method list |
| `[no] aaa authentication dot1x default group radius` | Method list |
| `[no] tacacs server WORD` | Name for the tacacs server configuration |
| `[no] radius server WORD` | Name for the radius server configuration |
| `[no] tacacs-server host A.B.C.D` | IP address |
| `[no] tacacs-server host A.B.C.D key LINE` | The UNENCRYPTED server key |
| `[no] radius-server host A.B.C.D` | IP address |
| `[no] radius-server host A.B.C.D key LINE` | The UNENCRYPTED server key |
| `[no] radius-server host A.B.C.D auth-port 1812 acct-port 1813 key K` | options |
| `[no] dot1x system-auth-control` | Enable or Disable SysAuthControl |

#### Interface configuration (interface …) — `Switch(config-if)#`

| Command | Description |
|---|---|
| `[no] channel-group` | Etherchannel/port bundling configuration |
| `[no] channel-group <1-48>` | Channel group number |
| `[no] channel-group <1-48> mode active` | Enable LACP unconditionally |
| `[no] channel-group <1-48> mode passive` | Enable LACP only if a LACP device is detected |
| `[no] channel-group <1-48> mode on` | Enable Etherchannel only |
| `[no] channel-group <1-48> mode desirable` | Enable PAgP unconditionally |
| `[no] channel-group <1-48> mode auto` | Enable PAgP only if a PAgP device is detected |
| `[no] description` | Interface specific description |
| `[no] description LINE` | Up to 240 characters describing this interface |
| `do LINE` | Exec Command |
| `duplex auto/full/half` | Configure duplex operation. |
| `end` | Exit from configure mode |
| `exit` | Exit from interface configuration mode |
| `[no] interface range fa0/1 - 4 , gi0/1` | Interface list |
| `[no] interface IFACE` | Interface name |
| `[no] interface Vlan <1-4094>` | Vlan interface |
| `[no] interface <0-2147483647>` | Loopback interface |
| `[no] interface Port-channel <1-48>` | Ethernet Channel of interfaces |
| `ip helper-address` | Specify a destination address for UDP broadcasts |
| `ip helper-address A.B.C.D` | IP destination address |
| `ip access-group <1-2699>/WORD in` | inbound packets |
| `ip access-group <1-2699>/WORD out` | outbound packets |
| `ip nat inside` | Inside interface for address translation |
| `ip nat outside` | Outside interface for address translation |
| `ip ospf cost` | Interface cost |
| `ip ospf cost <1-65535>` | Interface cost |
| `ip ospf hello-interval` | Time between HELLO packets |
| `ip ospf hello-interval <1-65535>` | Time between HELLO packets |
| `ip ospf dead-interval` | Interval after which a neighbor is declared dead |
| `ip ospf dead-interval <1-65535>` | Interval after which a neighbor is declared dead |
| `ip ospf priority` | Router priority |
| `ip ospf priority <0-255>` | Router priority |
| `ip address` | Set the IP address of an interface |
| `ip address A.B.C.D A.B.C.D` | IP subnet mask |
| `ip address A.B.C.D A.B.C.D secondary` | Make this IP address a secondary address |
| `ip dhcp snooping trust` | DHCP Snooping trust config |
| `[no] ipv6 address X:X:X:X::X/<0-128>` | IPv6 prefix |
| `[no] ipv6 enable` | Enable IPv6 on interface |
| `[no] ipv6 nd ra suppress` | Suppress IPv6 Router Advertisements |
| `[no] ipv6 ospf <1-65535> area <0-4294967295>/A.B.C.D` | OSPF area ID |
| `[no] ipv6 ospf cost <1-65535>` | Cost |
| `[no] ipv6 rip WORD enable` | Enable RIP routing on interface |
| `[no] ipv6 eigrp <1-65535>` | Autonomous system number |
| `[no] ipv6 router isis` | IS-IS Routing for IPv6 |
| `[no] ipv6 router isis WORD` | Routing process tag |
| `[no] ipv6 dhcp server WORD` | Name of IPv6 DHCP pool |
| `[no] shutdown` | Shutdown the selected interface |
| `[no] spanning-tree portfast` | Portfast mode |
| `[no] spanning-tree cost <1-200000000>` | port path cost |
| `speed 10/100/1000/auto` | Configure speed operation. |
| `[no] standby <0-255>` | group number |
| `[no] standby <0-255> ip A.B.C.D` | Virtual IP address |
| `[no] standby <0-255> priority <0-255>` | Priority value |
| `[no] standby <0-255> preempt` | Overthrow lower priority Active routers |
| `[no] switchport mode` | Set trunking mode of the interface |
| `[no] switchport mode access` | Set trunking mode to ACCESS unconditionally |
| `[no] switchport mode trunk` | Set trunking mode to TRUNK unconditionally |
| `[no] switchport access vlan` | Set VLAN when interface is in access mode |
| `[no] switchport access vlan <1-4094>` | VLAN ID of the VLAN when this port is in access mode |
| `[no] switchport trunk allowed vlan` | Set allowed VLANs when interface is in trunking mode |
| `[no] switchport trunk allowed vlan WORD` | VLAN IDs of the allowed VLANs when this port is in trunking mode |
| `[no] switchport trunk allowed vlan add WORD` | VLAN IDs of the allowed VLANs |
| `[no] switchport trunk allowed vlan remove WORD` | VLAN IDs of disallowed VLANS |
| `[no] switchport trunk allowed vlan all` | all VLANs |
| `[no] switchport trunk allowed vlan none` | no VLANs |
| `[no] switchport trunk allowed vlan except WORD` | VLAN IDs of disallowed VLANS |
| `[no] switchport trunk native vlan` | Set native VLAN when interface is in trunking mode |
| `[no] switchport trunk native vlan <1-4094>` | VLAN ID of the native VLAN when this port is in trunking mode |
| `[no] switchport trunk encapsulation dot1q` | Interface uses only 802.1q trunking encapsulation when trunking |
| `[no] switchport nonegotiate` | Device will not engage in negotiation protocol on this interface |
| `[no] switchport voice vlan` | Vlan for voice traffic |
| `[no] switchport voice vlan <1-4094>` | Vlan for voice traffic |
| `[no] switchport port-security` | Security related command |
| `[no] switchport port-security maximum <1-132>` | Maximum addresses |
| `[no] switchport port-security violation protect` | Security violation protect mode |
| `[no] switchport port-security violation restrict` | Security violation restrict mode |
| `[no] switchport port-security violation shutdown` | Security violation shutdown mode |
| `[no] switchport port-security mac-address sticky` | Configure dynamic secure addresses as sticky |
| `[no] switchport port-security mac-address H.H.H` | 48 bit mac address |
| `[no] vrrp <1-255>` | group number |
| `[no] vrrp <1-255> ip A.B.C.D` | Virtual IP address |
| `[no] vrrp <1-255> priority <0-255>` | Priority value |
| `[no] vrrp <1-255> preempt` | Overthrow lower priority Active routers |
| `[no] authentication port-control auto` | PortState will be set to AUTO |
| `[no] authentication port-control force-authorized` | PortState set to Authorized |
| `[no] dot1x pae authenticator` | Set pae type as Authenticator |
<!-- END GENERATED COMMANDS -->
