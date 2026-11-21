# Extreme Networks ExtremeXOS

[← Руководство](guide.md)

Модель: коммутатор третьего уровня **Extreme X440-G2-24t**, порты `1`–`24`. Все порты при старте входят в VLAN `Default` (тег 1) без тегов. STP (домен `s0`) по умолчанию выключен, маршрутизация между VLAN тоже, как на настоящем коммутаторе.

## Как устроен CLI

У EXOS нет режимов конфигурации: каждая команда начинается с глагола — `create`, `configure`, `enable`, `disable`, `unconfigure`, `delete`, `show`. Приглашение выглядит как `X440G2-24t.5 #`: число — номер команды в сеансе. Звёздочка в начале (`* X440G2-24t.6 #`) значит, что есть несохранённые изменения. Сохранение: `save`.

VLAN указываются по имени, порты — номерами: `1`, `1-4`, `1,3,5-8`, `all`. Порт может быть без тега только в одном VLAN: прежде чем добавить его в новый VLAN без тега, удалите его из `Default`.

## Пример: маршрутизация между двумя VLAN

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

На ПК в порту 1 укажите шлюз 10.0.10.1, в порту 2 — 10.0.20.1. Проверка: `show vlan`, `show iproute`, `show iparp`.

## Trunk и статический маршрут

```
create vlan users tag 30
configure vlan users add ports 24 tagged
configure vlan users ipaddress 10.0.30.1/24
configure iproute add default 10.0.30.254
```

`tagged` добавляет порт в VLAN с тегом 802.1Q, как trunk у Cisco. Удаление: `configure vlan users delete ports 24`, `configure iproute delete default 10.0.30.254`.

## OSPF

```
configure ospf routerid 1.1.1.1
configure ospf add vlan v10 area 0.0.0.0
configure ospf add vlan v20 area 0.0.0.0 passive
enable ospf
```

Проверка: `show ospf`, `show ospf neighbor`, `show iproute` (маршруты OSPF отмечены #oa).

## STP

```
configure stpd s0 mode dot1w
configure stpd s0 priority 4096
configure stpd s0 ports link-type edge 1-20
enable stpd s0
```

`dot1d` — классический STP (30 с до пересылки), `dot1w` — RSTP, `mstp` — MSTP. Проверка: `show stpd s0`, `show stpd s0 ports`.

## Прочее

`configure snmp sysName CORE1` — имя коммутатора (и подпись на схеме), `configure ports 5 display-string "к серверу"` — описание порта, `disable ports 5` / `enable ports 5`, `show ports no-refresh`, `show fdb` (MAC-таблица), `ping 10.0.10.5`, `ping count 10 10.0.10.5`, `traceroute`, `unconfigure switch all` — сброс к заводским настройкам.

## Удалённый доступ (Telnet и SSH)

Устройства принимают удалённый вход по Telnet (TCP 23) и SSH (TCP 22). В режиме симуляции видно рукопожатие TCP; у Telnet в деталях пакета виден даже пароль, у SSH содержимое зашифровано. Подключение открывается прямо в текущей консоли, `exit` возвращает обратно. Из удалённой консоли можно подключаться дальше. В EXOS Telnet включён сразу, SSH включается командой `enable ssh2`. Пользователь `admin` по умолчанию без пароля.

```
enable ssh2
create account admin noc noc123
disable telnet
```

`configure account admin password` спросит новый пароль дважды. Подключение: `ssh2 admin@10.0.0.2`, `telnet 10.0.0.2`.

## EtherChannel (агрегирование каналов)

Несколько кабелей между двумя коммутаторами объединяются в один логический канал. STP считает его одним портом и ничего не блокирует, а при обрыве одного кабеля трафик идёт по оставшимся. Канал собирается, когда режимы на концах совместимы: `on` только с `on`; LACP `active` с `active` или `passive`; PAgP `desirable` с `desirable` или `auto`. Иначе порты остаются в состоянии suspended и трафик не передают.

```
enable sharing 1 grouping 1-2 algorithm address-based L2 lacp
```

Порт 1 становится главным портом группы: настройки VLAN для группы задаются через него (`configure vlan … add ports 1`). Без `lacp` группа статическая. Выключить: `disable sharing 1`. Проверка: `show sharing`.

## VRRP (резервный шлюз)

Два маршрутизатора в одной сети делят виртуальный IP-адрес, который ПК используют как шлюз. Активный (HSRP) или master (VRRP) выбирается по приоритету, при равенстве — по большему IP. Он отвечает на ARP виртуальным MAC. Если активный пропадает, резервный забирает роль через hold time (HSRP 10 с, VRRP около 3,6 с) и рассылает gratuitous ARP. По умолчанию HSRP не отбирает роль обратно (нужен `preempt`), а VRRP отбирает.

```
create vlan office tag 40
configure vlan office ipaddress 192.168.40.2/24
create vrrp vlan office vrid 1
configure vrrp vlan office vrid 1 priority 200
configure vrrp vlan office vrid 1 add 192.168.40.254
enable vrrp
```

Проверка: `show vrrp`.

## IPv6, OSPFv3, RIPng и защита портов

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

RIPng: `configure ripng add vlan lab6`, `enable ripng`. Проверка: `show ospfv3 neighbor`, `show fdb limit-learning`, `show ip-security dhcp-snooping entries`.

## Syslog, SNMP и NTP

```
configure syslog add 192.168.1.100 local0
enable syslog
configure snmp add community readonly public
configure snmp sysLocation Server-room-1
configure ntp server add 192.168.1.100
enable ntp
```

Проверка: `show ntp`, `show log`.

## Полный список команд

<!-- BEGIN GENERATED COMMANDS switch -->
#### Все команды (у EXOS нет режимов конфигурации) — `X440G2-24t.1 #`

| Команда | Описание |
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
