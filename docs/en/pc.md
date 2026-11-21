# PC and server

[← User guide](guide.md)

PCs and servers are end devices with one network port, `FastEthernet0`. They send and receive IP packets, answer pings and run ping and tracert. 

## IP configuration

In the **Config** tab: IP address, mask, default gateway, DNS server, port on/off. If you leave the mask empty, the classful default mask is filled in.

Or in the **Command Prompt**:

```
ipconfig 192.168.1.10 255.255.255.0 192.168.1.1
```

## Checking the network

```
ipconfig /all
ping 192.168.2.10
ping -n 10 192.168.2.10
ping -t 192.168.2.10        (runs until Ctrl+C)
tracert 192.168.2.10
arp -a
```

## DHCP, DNS and web

- **DHCP client.** Tick "Obtain an address via DHCP" on the Config tab or run `ipconfig /renew`. Release the address with `ipconfig /release`. When no server answers, the PC gives itself a 169.254.x.x address (APIPA), like Windows.
- **DNS.** The DNS server comes from DHCP or is set by hand (`ipconfig /dns <address>`). `ping` and `tracert` accept names; `nslookup <name>` shows the server's answer.
- **Web.** `curl http://<name or address>/` or the Web Browser tab.
- **Server services.** The server's Services tab turns on DHCP (pool: gateway, DNS, start address, mask, number of addresses), DNS (A records) and HTTP (index.html content; on by default).

Simulation mode shows the DHCP exchange (DISCOVER, OFFER, REQUEST, ACK), DNS queries and the TCP handshake before HTTP.

```
ipconfig /renew
ipconfig /all
ipconfig /dns 192.168.1.2
nslookup www.lab.local
ping www.lab.local
curl http://www.lab.local/
```

## IPv6

Every PC always has a link-local fe80::… address (shown by `ipconfig` and in the settings window). Set a global address in the **IPv6** group on the Config tab or with:

```
ipconfig /ipv6 2001:db8:1::10/64 2001:db8:1::1
ipconfig /ipv6 auto
ping 2001:db8:2::10
```

`/ipv6 auto` (or the "Autoconfiguration (SLAAC)" box) takes the prefix and gateway from router advertisements. `/ipv6 off` removes the address.

## Linux PC and server

The palette has **PC (Linux)** and **Server (Linux)**: the same devices, but the port is `eth0`, the default TTL is 64 and the **Terminal (Linux)** tab works like an Ubuntu shell. Only root can change the configuration: prefix commands with `sudo` or run `sudo -i`; otherwise Linux answers `Operation not permitted`. IPv6 autoconfiguration (SLAAC) is on from the start, as on real Linux. When no DHCP server answers, no 169.254.x.x address is assigned.

```linux
sudo ip addr add 192.168.1.10/24 dev eth0
sudo ip route add default via 192.168.1.1
echo nameserver 192.168.1.2 | sudo tee /etc/resolv.conf
ip a
ip route
ping -c 4 192.168.1.1
```

The old net-tools style works too: `sudo ifconfig eth0 192.168.1.10 netmask 255.255.255.0`, `sudo route add default gw 192.168.1.1`, `route -n`, `arp -n`.

DHCP: `sudo dhclient -v eth0`, release with `sudo dhclient -r eth0`. Diagnostics: `ip route get <address>`, `ip neigh`, `traceroute`, `dig`, `host`, `nslookup`, `curl`, `wget`, `ss -tuln`, `ethtool eth0`.

Linux `ping` without `-c` runs until Ctrl+C.

## More Windows commands

```
route print
route -p add 10.0.0.0 mask 255.0.0.0 192.168.1.254
netsh interface ipv4 set address "FastEthernet0" static 192.168.1.10 255.255.255.0 192.168.1.1
netsh interface ipv4 set dnsservers "FastEthernet0" static 192.168.1.2 primary
netsh interface ipv4 show config
getmac
netstat -an
ipconfig /displaydns
```

Also: `netsh interface ipv6 add address|route`, `netsh interface set interface "FastEthernet0" admin=disabled`, `arp -s`, `ping -l 1000 -i 5`, `tracert -d -h 10`, `nslookup <name> <server>`, `ipconfig /flushdns`.

## Telnet and SSH from a PC

Both kinds of PC have the clients: `ssh admin@10.0.0.1`, `ssh -l admin 10.0.0.1`, `telnet 10.0.0.1`. Passwords are not echoed. `exit` closes the connection.

**Server (Linux)** runs its SSH server from the start: user `user`, password `netemu`. On **PC (Linux)** start it with `sudo systemctl start ssh`. Change the password with `passwd`. Check: `systemctl status ssh`.

Address over DHCPv6: `ipconfig /renew6` (Windows), `sudo dhclient -6 -v eth0` (Linux).

## SNMP manager and the syslog file (Linux)

A Linux host can poll the SNMP agents of routers and switches. On a Linux server, messages from the devices that use it as their syslog server are in `/var/log/syslog`.

```linux
snmpget -v2c -c public 192.168.1.1 sysName.0
snmpwalk -v2c -c public 192.168.1.1 system
snmpset -v2c -c private 192.168.1.1 sysLocation.0 s Lab-2
cat /var/log/syslog
```

## All commands

<!-- BEGIN GENERATED COMMANDS host -->
```
Available Commands
?          Display the list of available commands
arp        Display or modify the ARP cache
           arp -a [ip] | arp -d [ip|*] | arp -s <ip> <mac>
cls        Clear the screen
curl       Fetch a web page: curl [-I] http://<host>[/page]
date       Display the date: date /t
echo       Display a message
exit       Quit the command prompt
getmac     Display the MAC address: getmac [/v]
help       Display the list of available commands
hostname   Display the host name
ipconfig   Display or set IP configuration
           ipconfig [/all]
           ipconfig <ip> <mask> [<gateway>]
           ipconfig /ipv6 <addr>/<len> [<gw>] | /ipv6 auto | /ipv6 off
           ipconfig /renew | /release   (DHCP)
           ipconfig /renew6   (DHCPv6)
           ipconfig /dns <server>
           ipconfig /displaydns | /flushdns
netsh      Configure the network adapter (netsh interface ipv4|ipv6 ..., netsh ?)
           netsh wlan show interfaces | show networks | connect name=<SSID> key=<key> | disconnect
netstat    Network statistics: netstat [-a] [-n] [-r] [-e]
nslookup   Query the DNS server: nslookup <name> [<server>]
pathping   Trace route and loss: pathping [-n] <ip or name>
ping       Send echo messages: ping [-n count] [-t] [-l size] [-i TTL] [-w timeout] [-4|-6] <ip or name>
route      Routing table: route print [-4|-6] | route [-p] add <net> mask <mask> <gw> | route delete <net>
time       Display the time: time /t
tracert    Trace route to destination: tracert [-d] [-h max_hops] [-w timeout] [-4|-6] <ip or name>
ssh        Secure shell client: ssh [-l user] [-p port] [user@]<ip>
telnet     Telnet client: telnet <ip> [port]
ver        Display the Windows version
whoami     Display the current user
```
<!-- END GENERATED COMMANDS -->

### Linux

<!-- BEGIN GENERATED COMMANDS linux -->
```
NetEmu Linux shell (Ubuntu 24.04 style). Changing the configuration needs root: prefix with sudo.
ip         iproute2:
           ip [-4|-6] [-br] addr [show] | ip addr add|del <addr>/<len> dev eth0 | ip addr flush dev eth0
           ip link [show] | ip link set eth0 up|down
           ip [-6] route [show] | ip route add|del default|<net> via <gw> [dev eth0] | ip route get <ip>
           ip [-6] neigh [show] | ip neigh flush all
ifconfig   ifconfig [eth0] | ifconfig eth0 <ip> netmask <mask> | ifconfig eth0 <ip>/<len> | ifconfig eth0 up|down
           ifconfig eth0 inet6 add|del <addr>/<len>
route      route -n | route add default gw <gw> | route add -net <net> netmask <mask> gw <gw> | route del ...
arp        arp -n | arp -a | arp -d <ip> | arp -s <ip> <mac>
ping       ping [-c count] [-s size] [-t ttl] [-i interval] [-W timeout] [-4|-6] <host>   (Ctrl+C stops)
ping6      ping -6
traceroute traceroute [-4|-6] [-n] [-m max_ttl] [-w wait] <host>     (also traceroute6, tracepath)
dig        dig [@server] <name> [+short]
host       host <name> [server]
nslookup   nslookup <name> [server]
curl       curl [-I] [-s] http://<host>[/page]
wget       wget http://<host>[/page]
dhclient   dhclient [-v] eth0 | dhclient -r eth0 | dhclient -6 [-v] eth0 (DHCPv6)
resolvectl resolvectl status | resolvectl dns eth0 <server>
cat        cat /etc/resolv.conf | /etc/hostname | /etc/hosts | /proc/sys/net/ipv4/ip_forward
echo       echo nameserver <ip> > /etc/resolv.conf   (also: ... | sudo tee /etc/resolv.conf)
ss         ss -tuln            (also netstat -tuln, netstat -rn, netstat -i)
sysctl     sysctl net.ipv4.ip_forward | sysctl -w net.ipv6.conf.eth0.accept_ra=0|1
ethtool    ethtool eth0
hostname   hostname [new-name] | hostnamectl [set-hostname <name>]
snmp       snmpget|snmpwalk -v2c -c <community> <agent> [oid] | snmpset -v2c -c <c> <agent> sysName.0 s <value>
syslog     cat /var/log/syslog   (Linux server: messages from network devices)
ssh        ssh [-l user] [-p port] [user@]<ip>  |  telnet <ip> [port]
wifi       nmcli dev status | nmcli dev wifi list | nmcli dev wifi connect <SSID> password <key> | iw dev wlan0 link
sshd       sudo systemctl start|stop|enable ssh | systemctl status ssh | passwd [user]
other      sudo, sudo -i, su, exit, logout, whoami, id, uname [-a], pwd, date, uptime, clear, help
```
<!-- END GENERATED COMMANDS -->
