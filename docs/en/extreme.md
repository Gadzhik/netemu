# Extreme Networks ExtremeXOS

[← User guide](guide.md)

Model: the **Extreme X440-G2-24t** layer 3 switch, ports `1`–`24`. Out of the box every port is an untagged member of VLAN `Default` (tag 1). STP (domain `s0`) is disabled by default and so is routing between VLANs, as on the real switch.

## How the CLI works

EXOS has no configuration modes: every command starts with a verb, such as `create`, `configure`, `enable`, `disable`, `unconfigure`, `delete` or `show`. The prompt looks like `X440G2-24t.5 #`, where the number counts the commands in the session. A leading asterisk (`* X440G2-24t.6 #`) means there are unsaved changes. Save with `save`.

VLANs are referenced by name and ports by number: `1`, `1-4`, `1,3,5-8`, `all`. A port can be untagged in one VLAN only: remove it from `Default` before adding it untagged to another VLAN.

## Example: routing between two VLANs

```
create vlan v10 tag 10
create vlan v20 tag 20
configure vlan default delete ports 1-2
configure vlan v10 add ports 1 untagged
configure vlan v20 add ports 2 untagged
configure vlan v10 ipaddress 10.0.10.1 255.255.255.0
configure vlan v20 ipaddress 10.0.20.1/24
enable ipforwarding
save
```

Give the PC on port 1 the gateway 10.0.10.1 and the PC on port 2 the gateway 10.0.20.1. Check: `show vlan`, `show iproute`, `show iparp`.

## Trunk and static route

```
create vlan users tag 30
configure vlan users add ports 24 tagged
configure vlan users ipaddress 10.0.30.1/24
configure iproute add default 10.0.30.254
```

`tagged` adds the port to the VLAN with an 802.1Q tag, like a Cisco trunk. Remove with `configure vlan users delete ports 24` and `configure iproute delete default 10.0.30.254`.

## OSPF

```
configure ospf routerid 1.1.1.1
configure ospf add vlan v10 area 0.0.0.0
configure ospf add vlan v20 area 0.0.0.0 passive
enable ospf
```

Check: `show ospf`, `show ospf neighbor`, `show iproute` (OSPF routes are marked #oa).

## STP

```
configure stpd s0 mode dot1w
configure stpd s0 priority 4096
configure stpd s0 ports link-type edge 1-20
enable stpd s0
```

`dot1d` is classic STP (30 s before forwarding), `dot1w` is RSTP and `mstp` is MSTP. Check: `show stpd s0`, `show stpd s0 ports`.

## Other commands

`configure snmp sysName CORE1` sets the switch name (and the canvas label), `configure ports 5 display-string "to server"` sets a port description, `disable ports 5` / `enable ports 5`, `show ports no-refresh`, `show fdb` (MAC table), `ping 10.0.10.5`, `ping count 10 10.0.10.5`, `traceroute`, and `unconfigure switch all` resets to factory defaults.

## Remote access (Telnet and SSH)

Devices accept remote logins over Telnet (TCP 23) and SSH (TCP 22). Simulation mode shows the TCP handshake; Telnet packet details reveal even the password, SSH contents are encrypted. The connection opens in the current console and `exit` returns. You can hop on from a remote console. EXOS has Telnet on from the start; SSH is turned on with `enable ssh2`. User `admin` has no password by default.

```
enable ssh2
create account admin noc noc123
disable telnet
```

`configure account admin password` asks for the new password twice. Connect with `ssh2 admin@10.0.0.2` or `telnet 10.0.0.2`.

## EtherChannel (link aggregation)

Several cables between two switches are combined into one logical channel. STP treats it as a single port and blocks nothing, and when one cable fails the traffic moves to the others. The channel forms when the modes at both ends match: `on` only with `on`; LACP `active` with `active` or `passive`; PAgP `desirable` with `desirable` or `auto`. Otherwise the ports stay suspended and carry no traffic.

```
enable sharing 1 grouping 1-2 algorithm address-based L2 lacp
```

Port 1 becomes the group's master: VLAN settings of the group are made through it (`configure vlan … add ports 1`). Without `lacp` the group is static. Turn off with `disable sharing 1`. Check: `show sharing`.

## VRRP (gateway redundancy)

Two routers on one network share a virtual IP address that PCs use as their gateway. The active (HSRP) or master (VRRP) router is elected by priority, then by the higher IP. It answers ARP with the virtual MAC. When it disappears, the backup takes over after the hold time (HSRP 10 s, VRRP about 3.6 s) and sends a gratuitous ARP. By default HSRP does not take the role back (`preempt` is needed), VRRP does.

```
create vlan office tag 40
configure vlan office ipaddress 192.168.40.2/24
create vrrp vlan office vrid 1
configure vrrp vlan office vrid 1 priority 200
configure vrrp vlan office vrid 1 add 192.168.40.254
enable vrrp
```

Check: `show vrrp`.

## IPv6, OSPFv3, RIPng and port protection

```
create vlan lab6 tag 60
configure vlan lab6 ipaddress 2001:db8:60::1/64
enable ipforwarding ipv6
configure ospfv3 routerid 1.1.1.1
configure ospfv3 add vlan lab6 area 0.0.0.0
enable ospfv3
configure ports 5 vlan Default limit-learning 1 action blackhole
enable ip-security dhcp-snooping vlan Default ports all violation-action drop-packet
configure trusted-ports 24 trust-for dhcp-server
```

RIPng: `configure ripng add vlan lab6`, `enable ripng`. Check: `show ospfv3 neighbor`, `show fdb limit-learning`, `show ip-security dhcp-snooping entries`.

## Syslog, SNMP and NTP

```
configure syslog add 192.168.1.100 local0
enable syslog
configure snmp add community readonly public
configure snmp sysLocation Server-room-1
configure ntp server add 192.168.1.100
enable ntp
```

Check: `show ntp`, `show log`.

## All commands

<!-- BEGIN GENERATED COMMANDS switch -->
#### All commands (EXOS has no configuration modes) — `X440G2-24t.1 #`

| Command | Description |
|---|---|
| `clear fdb` | MAC table |
| `clear iparp` | ARP table |
| `configure vlan <vlan_name> tag <tag>` | VLAN tag |
| `configure vlan <vlan_name> add ports <port_list>` | Port list |
| `configure vlan <vlan_name> add ports <port_list> tagged` | Tagged |
| `configure vlan <vlan_name> add ports <port_list> untagged` | Untagged |
| `configure vlan <vlan_name> delete ports <port_list>` | Port list |
| `configure vlan <vlan_name> ipaddress A.B.C.D A.B.C.D` | Subnet mask |
| `configure vlan <vlan_name> ipaddress <ipaddress>/<mask_length>` | IP address/prefix |
| `configure vlan <vlan_name> ipaddress X:X:X:X::X/<0-128>` | IPv6 address/prefix |
| `configure vlan <vlan_name> name <new_name>` | New name |
| `configure iproute add default A.B.C.D` | Gateway |
| `configure iproute add <ipNetmask> A.B.C.D` | Gateway |
| `configure iproute add A.B.C.D A.B.C.D A.B.C.D` | Gateway |
| `configure iproute delete default A.B.C.D` | Gateway |
| `configure iproute delete <ipNetmask> A.B.C.D` | Gateway |
| `configure iproute delete A.B.C.D A.B.C.D A.B.C.D` | Gateway |
| `configure stpd s0 mode dot1d` | 802.1D STP |
| `configure stpd s0 mode dot1w` | 802.1w RSTP |
| `configure stpd s0 mode mstp` | 802.1s MSTP |
| `configure stpd s0 priority <priority>` | Priority (multiple of 4096) |
| `configure stpd s0 ports link-type edge <port_list>` | Port list |
| `configure stpd s0 ports link-type point-to-point <port_list>` | Port list |
| `configure stpd s0 ports cost <cost> <port_list>` | Port list |
| `configure ospf routerid A.B.C.D` | Router ID |
| `configure ospf add vlan <vlan_name> area <area_identifier>` | Area ID |
| `configure ospf add vlan <vlan_name> area <area_identifier> passive` | Passive interface |
| `configure ospf delete vlan <vlan_name>` | VLAN name |
| `configure ports <port_list> vlan <vlan_name> limit-learning <number>` | Number of MACs |
| `configure ports <port_list> vlan <vlan_name> limit-learning <number> action blackhole/stop-learning` | Number of MACs |
| `configure ports <port_list> display-string <string>` | Description |
| `configure ports <port_list> rate-limit egress no-limit` | Remove the limit |
| `configure ports <port_list> rate-limit egress <rate> Kbps` | Kilobits per second |
| `configure ports <port_list> rate-limit egress <rate> Mbps` | Megabits per second |
| `configure ports <port_list> rate-limit egress <rate> Gbps` | Gigabits per second |
| `configure account <name> password` | Change the password |
| `configure ospfv3 routerid A.B.C.D` | Router ID |
| `configure ospfv3 add vlan <vlan_name> area <area>` | Area ID |
| `configure ospfv3 delete vlan <vlan_name>` | VLAN name |
| `configure ripng add vlan <vlan_name>` | VLAN name |
| `configure ripng delete vlan <vlan_name>` | VLAN name |
| `configure trusted-ports <port_list> trust-for dhcp-server` | DHCP server |
| `configure vrrp vlan <vlan_name> vrid <vrid> priority <priority>` | Priority |
| `configure vrrp vlan <vlan_name> vrid <vrid> add A.B.C.D` | Virtual IP |
| `configure vrrp vlan <vlan_name> vrid <vrid> preempt` | Preempt |
| `configure vrrp vlan <vlan_name> vrid <vrid> dont-preempt` | Do not preempt |
| `configure snmp sysName <name>` | System name |
| `configure snmp add community readonly <community>` | Read-only |
| `configure snmp add community readwrite <community>` | Read-write |
| `configure snmp delete community readonly <community>` | Read-only |
| `configure snmp delete community readwrite <community>` | Read-write |
| `configure snmp sysContact <contact>` | System contact |
| `configure snmp sysLocation <location>` | System location |
| `configure lldp ports <port_list> advertise vendor-specific med policy application voice vlan <vlan_name>` | Voice VLAN |
| `configure lldp ports <port_list> advertise vendor-specific med policy application voice vlan <vlan_name> dscp <dscp>` | DSCP |
| `configure lldp ports <port_list> no-advertise vendor-specific med policy application voice` | Voice |
| `configure syslog add <ip>` | Syslog host |
| `configure syslog add <ip> local0..local7` | Facility |
| `configure syslog delete <ip>` | Syslog host |
| `configure syslog delete <ip> local0..local7` | Facility |
| `configure ntp server add <ip>` | Server |
| `configure ntp server delete <ip>` | Server |
| `create vrrp vlan <vlan_name> vrid <vrid>` | VRID |
| `create account admin <name>` | Account name |
| `create account admin <name> <password>` | Password |
| `create vlan <vlan_name>` | VLAN name |
| `create vlan <vlan_name> tag <tag>` | VLAN tag |
| `delete account <name>` | Account name |
| `delete vlan <vlan_name>` | VLAN name |
| `disable ipforwarding` | IP forwarding (routing between VLANs) |
| `disable ipforwarding vlan <vlan_name>` | VLAN name |
| `disable ipforwarding ipv6` | IPv6 forwarding |
| `disable ospfv3` | OSPFv3 |
| `disable ripng` | RIPng |
| `disable ip-security dhcp-snooping vlan <vlan_name>` | VLAN name |
| `disable ip-security dhcp-snooping vlan <vlan_name> ports all violation-action drop-packet` | Options |
| `disable ports <port_list>` | Port list |
| `disable ospf` | OSPF |
| `disable stpd` | Spanning tree |
| `disable stpd s0` | STP domain |
| `disable ssh2` | SSH server |
| `disable vrrp` | VRRP |
| `disable vrrp vlan <vlan_name> vrid <vrid>` | VRID |
| `disable sharing <master_port>` | Master port |
| `disable telnet` | Telnet server |
| `disable syslog` | Remote syslog |
| `disable ntp` | NTP client |
| `disable snmp access` | Access |
| `enable ipforwarding` | IP forwarding (routing between VLANs) |
| `enable ipforwarding vlan <vlan_name>` | VLAN name |
| `enable ipforwarding ipv6` | IPv6 forwarding |
| `enable ospfv3` | OSPFv3 |
| `enable ripng` | RIPng |
| `enable ip-security dhcp-snooping vlan <vlan_name>` | VLAN name |
| `enable ip-security dhcp-snooping vlan <vlan_name> ports all violation-action drop-packet` | Options |
| `enable ports <port_list>` | Port list |
| `enable ospf` | OSPF |
| `enable stpd` | Spanning tree |
| `enable stpd s0` | STP domain |
| `enable ssh2` | SSH server |
| `enable vrrp` | VRRP |
| `enable vrrp vlan <vlan_name> vrid <vrid>` | VRID |
| `enable sharing <master_port> grouping <port_list>` | Port list |
| `enable sharing <master_port> grouping <port_list> algorithm address-based L2 lacp` | Options |
| `enable telnet` | Telnet server |
| `enable syslog` | Remote syslog |
| `enable ntp` | NTP client |
| `enable snmp access` | Access |
| `exit` | Log out |
| `logout` | Log out |
| `ping count <count> <host>` | Destination |
| `ping <host>` | Destination |
| `quit` | Log out |
| `save` | Save the configuration |
| `save configuration` | Configuration |
| `save configuration primary` | Primary configuration |
| `show vlan` | VLAN information |
| `show vlan <vlan_name>` | VLAN name |
| `show ports` | Port information |
| `show ports no-refresh` | Static output |
| `show ports information` | Port configuration |
| `show iproute` | IP routing table |
| `show iparp` | ARP table |
| `show ipconfig` | IP configuration |
| `show configuration` | Current configuration |
| `show switch` | Switch information |
| `show version` | Software version |
| `show sharing` | Load sharing groups |
| `show vrrp` | VRRP status |
| `show ospfv3 neighbor` | Neighbors |
| `show ripng` | RIPng |
| `show ip-security dhcp-snooping entries` | Binding entries |
| `show fdb` | Forwarding database (MAC table) |
| `show fdb limit-learning` | Learning limits |
| `show ospf` | OSPF information |
| `show ospf neighbor` | OSPF neighbors |
| `show stpd` | Spanning tree |
| `show stpd s0` | STP domain |
| `show stpd s0 ports` | Port states |
| `show ntp` | NTP |
| `show ntp association` | Associations |
| `show log` | Log |
| `ssh2 <user>@<host>` | user@host |
| `telnet <host>` | Destination |
| `traceroute <host>` | Destination |
| `unconfigure vlan <vlan_name> ipaddress` | Remove the IP address |
| `unconfigure switch all` | All configuration |
<!-- END GENERATED COMMANDS -->
