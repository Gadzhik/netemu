# ПК и сервер

[← Руководство](guide.md)

ПК и сервер — конечные устройства с одним сетевым портом `FastEthernet0`. Они отправляют и принимают IP-пакеты, отвечают на ping и выполняют ping и tracert. 

## Настройка IP

Через вкладку **«Настройка»**: IP-адрес, маска, шлюз по умолчанию, DNS-сервер, включение порта. Маска подставляется автоматически по классу адреса, если её не ввести.

Или в **«Командной строке»**:

```
ipconfig 192.168.1.10 255.255.255.0 192.168.1.1
```

## Пример проверки сети

```
ipconfig /all
ping 192.168.2.10
ping -n 10 192.168.2.10
ping -t 192.168.2.10        (бесконечно, остановка Ctrl+C)
tracert 192.168.2.10
arp -a
```

## DHCP, DNS и веб

- **DHCP-клиент.** Отметьте «Получать адрес по DHCP» на вкладке «Настройка» или выполните `ipconfig /renew`. Освободить адрес: `ipconfig /release`. Если сервер не ответил, ПК, как Windows, назначает себе адрес 169.254.x.x (APIPA).
- **DNS.** Адрес DNS-сервера приходит по DHCP или задаётся вручную (`ipconfig /dns <адрес>`). `ping` и `tracert` принимают имена; `nslookup <имя>` показывает ответ сервера.
- **Веб.** `curl http://<имя или адрес>/` или вкладка «Браузер».
- **Службы сервера.** На вкладке «Службы» сервера включаются DHCP (пул: шлюз, DNS, начальный адрес, маска, число адресов), DNS (A-записи) и HTTP (содержимое index.html; включён по умолчанию).

В режиме симуляции видны обмен DHCP (DISCOVER, OFFER, REQUEST, ACK), запросы DNS и TCP-рукопожатие перед HTTP.

```
ipconfig /renew
ipconfig /all
ipconfig /dns 192.168.1.2
nslookup www.lab.local
ping www.lab.local
curl http://www.lab.local/
```

## IPv6

У каждого ПК всегда есть link-local адрес fe80::… (виден в `ipconfig` и в окне настройки). Глобальный адрес задаётся в группе **IPv6** на вкладке «Настройка» или командой:

```
ipconfig /ipv6 2001:db8:1::10/64 2001:db8:1::1
ipconfig /ipv6 auto
ping 2001:db8:2::10
```

`/ipv6 auto` (или галочка «Автонастройка (SLAAC)») берёт префикс и шлюз из объявлений маршрутизатора. `/ipv6 off` удаляет адрес.

## ПК и сервер на Linux

В палитре есть **PC (Linux)** и **Server (Linux)**: те же устройства, но порт называется `eth0`, TTL по умолчанию 64, а вкладка **«Терминал (Linux)»** работает как оболочка Ubuntu. Изменять настройки может только root: используйте `sudo` перед командой или `sudo -i`. Без этого Linux ответит `Operation not permitted`. IPv6-автонастройка (SLAAC) включена сразу, как в настоящем Linux. Если DHCP-сервер не ответил, адрес 169.254.x.x не назначается.

```linux
sudo ip addr add 192.168.1.10/24 dev eth0
sudo ip route add default via 192.168.1.1
echo nameserver 192.168.1.2 | sudo tee /etc/resolv.conf
ip a
ip route
ping -c 4 192.168.1.1
```

Старый стиль (net-tools) тоже работает: `sudo ifconfig eth0 192.168.1.10 netmask 255.255.255.0`, `sudo route add default gw 192.168.1.1`, `route -n`, `arp -n`.

Адрес по DHCP: `sudo dhclient -v eth0`, освободить: `sudo dhclient -r eth0`. Диагностика: `ip route get <адрес>`, `ip neigh`, `traceroute`, `dig`, `host`, `nslookup`, `curl`, `wget`, `ss -tuln`, `ethtool eth0`.

Команда `ping` в Linux без `-c` работает бесконечно, остановка Ctrl+C.

## Дополнительные команды Windows

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

Ещё: `netsh interface ipv6 add address|route`, `netsh interface set interface "FastEthernet0" admin=disabled`, `arp -s`, `ping -l 1000 -i 5`, `tracert -d -h 10`, `nslookup <имя> <сервер>`, `ipconfig /flushdns`.

## Telnet и SSH с ПК

Клиенты есть на обоих видах ПК: `ssh admin@10.0.0.1`, `ssh -l admin 10.0.0.1`, `telnet 10.0.0.1`. Пароль при вводе не отображается. `exit` закрывает соединение.

У **Server (Linux)** SSH-сервер запущен сразу: пользователь `user`, пароль `netemu`. На **PC (Linux)** его нужно запустить: `sudo systemctl start ssh`. Сменить пароль: `passwd`. Проверка: `systemctl status ssh`.

Адрес по DHCPv6: `ipconfig /renew6` (Windows), `sudo dhclient -6 -v eth0` (Linux).

## SNMP-менеджер и журнал syslog (Linux)

С Linux-машины можно опрашивать SNMP-агенты маршрутизаторов и коммутаторов. На Linux-сервере сообщения от устройств, у которых он указан syslog-сервером, лежат в `/var/log/syslog`.

```linux
snmpget -v2c -c public 192.168.1.1 sysName.0
snmpwalk -v2c -c public 192.168.1.1 system
snmpset -v2c -c private 192.168.1.1 sysLocation.0 s Lab-2
cat /var/log/syslog
```

## Защита от вредоносов

Подробно о вредоносах и защите — в разделе [Вредоносные программы](malware.md). Команды Windows для вкладки «Безопасность»:

```
wusa KB4012212.msu /quiet
wmic qfe list brief
sc query lanmanserver
sc config lanmanserver start= disabled
sc stop lanmanserver
netsh advfirewall firewall add rule name="Block SMB" dir=in action=block protocol=TCP localport=445
netsh advfirewall firewall show rule name=all
MpCmdRun -Scan -ScanType 1
wbadmin start backup
wbadmin get versions
```

- `wusa` устанавливает обновление (KB4012212 закрывает MS17-010), `wmic qfe list` показывает установленные.
- `sc … lanmanserver` управляет службой «Сервер» (общий доступ к файлам, SMB, TCP 445).
- `netsh advfirewall firewall` добавляет, удаляет и показывает правила межсетевого экрана Windows.
- `MpCmdRun -SignatureUpdate` скачивает базы Microsoft Defender с update.example (нужен доступ в Интернет), `MpCmdRun -Scan` проверяет компьютер.
- `wbadmin start backup|recovery` создаёт резервную копию и восстанавливает из неё, `shutdown /r` перезагружает.

## Все команды

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
sysctl     sysctl net.ipv4.ip_forward | sysctl -w net.ipv6.conf.eth0.accept_ra=0|1 | sysctl -w net.ipv4.tcp_syncookies=1
ethtool    ethtool eth0
hostname   hostname [new-name] | hostnamectl [set-hostname <name>]
snmp       snmpget|snmpwalk -v2c -c <community> <agent> [oid] | snmpset -v2c -c <c> <agent> sysName.0 s <value>
syslog     cat /var/log/syslog   (Linux server: messages from network devices)
ssh        ssh [-l user] [-p port] [user@]<ip>  |  telnet <ip> [port]
security   sudo ufw deny|allow <port> | ufw status | sudo apt install clamav | sudo apt upgrade
           sudo freshclam | clamscan -r / | sudo reboot
wifi       nmcli dev status | nmcli dev wifi list | nmcli dev wifi connect <SSID> password <key> | iw dev wlan0 link
sshd       sudo systemctl start|stop|enable ssh | systemctl status ssh | passwd [user]
other      sudo, sudo -i, su, exit, logout, whoami, id, uname [-a], pwd, date, uptime, clear, help
```
<!-- END GENERATED COMMANDS -->
