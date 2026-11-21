# Huawei VRP

[← Руководство](guide.md)

Модели: маршрутизатор **Huawei AR2220** (порты `GigabitEthernet0/0/0`–`0/0/2`, кратко `GE0/0/0`) и коммутатор **Huawei S5720-28P** (`GigabitEthernet0/0/1`–`0/0/24`). Порты включены сразу после добавления.

## Виды (режимы)

| Приглашение | Вид | Как попасть | Как выйти |
|---|---|---|---|
| `<Huawei>` | пользовательский | при входе | — |
| `[Huawei]` | системный | `system-view` | `quit`, `return` |
| `[Huawei-GigabitEthernet0/0/0]` | интерфейс | `interface GigabitEthernet0/0/0` | `quit`, `return` |

Слово `undo` перед командой отменяет её: `undo shutdown`, `undo ip route-static …`, `undo sysname`.

## Пример: маршрутизатор между двумя сетями

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

Маску можно указывать полностью (`255.255.255.0`) или длиной (`24`). Статические маршруты в VRP имеют предпочтение 60. Резервный маршрут задаётся так: `ip route-static 0.0.0.0 0 10.0.4.1 preference 100`.

Проверка: `display ip interface brief`, `display ip routing-table`, `display arp`, `display current-configuration`, `ping -c 3 192.168.1.10`, `tracert 192.168.1.10`.

## VLAN и trunk

На S5720 VLAN создаются командой `vlan batch 10 20` (или `vlan 10` с переходом в вид VLAN). Порт доступа: `port link-type access` и `port default vlan N`; VLAN должна уже существовать. Trunk: `port link-type trunk` и `port trunk allow-pass vlan …`. Важно: trunk в VRP по умолчанию пропускает только VLAN 1, остальные VLAN нужно разрешить явно.

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

Native VLAN trunk: `port trunk pvid vlan 99`. Запретить VLAN: `undo port trunk allow-pass vlan 20`. Проверка: `display vlan`, `display port vlan`, `display mac-address`.

**Маршрутизация между VLAN.** На AR2220 создаётся подынтерфейс-терминатор на каждую VLAN. Без `arp broadcast enable` маршрутизатор не отправляет ARP-запросы в эту VLAN, и ping не пройдёт: частая ошибка в лабораторных.

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

OSPF настраивается в виде `ospf <процесс> router-id <id>`, затем в виде зоны `area <зона>` командами `network <адрес> <обратная маска>`.

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

Ещё: `import-route static`, `import-route direct`, `bandwidth-reference N`, на интерфейсе `ospf timer hello`, `ospf timer dead`, `ospf dr-priority`. В VRP внутренние маршруты OSPF имеют приоритет 10, внешние (O_ASE) 150, статические 60. Поэтому OSPF-маршрут на Huawei побеждает статический, в отличие от Cisco.

Проверка OSPF: `display ospf peer brief`, `display ospf peer`, `display ospf interface`, `display ospf lsdb`, `display ip routing-table`.

## ACL

Базовые ACL (2000–2999) проверяют источник, расширенные (3000–3999) проверяют протокол, адреса, порты и тип ICMP. Обратная маска `0` означает один адрес. Правила нумеруются с шагом 5. Важное отличие от Cisco: `traffic-filter` пропускает пакеты, не попавшие ни под одно правило.

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

NAT настраивается на внешнем интерфейсе. `nat outbound <ACL>` транслирует адреса в адрес интерфейса (Easy IP), `nat outbound <ACL> address-group N` транслирует в пул, `nat static` задаёт статическое соответствие, `nat server` пробрасывает порт внутрь.

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

Проверка: `display acl all`, `display nat outbound`, `display nat static`, `display nat server`, `display nat address-group`, `display nat session all`.

## BGP

Сессия BGP идёт по TCP-порту 179 и маршрутизируется как обычный трафик: для пиринга через loopback нужны маршрут до loopback соседа (IGP или статика) и адрес-источник; для eBGP с соседом не в подключённой сети нужен multihop; ACL может закрыть порт 179. Если `remote-as` указан неверно, сосед отвечает NOTIFICATION, и сессия не поднимается. Маршрут, у которого next-hop недостижим, не устанавливается: для iBGP обычно нужен next-hop-self. Выбор лучшего пути: weight, local-pref, локальные маршруты, длина AS_PATH, origin, MED, eBGP предпочтительнее iBGP, IGP-метрика до next-hop.

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

Ещё: `peer X ebgp-max-hop N`, `peer X preferred-value N`, `peer X default-route-advertise`, `peer X reflect-client`, `peer X ignore`. Маршруты BGP в VRP имеют приоритет 255 (EBGP и IBGP).

Проверка BGP: `display bgp peer`, `display bgp routing-table`, `display ip routing-table`.

## DHCP

В VRP DHCP включается глобально (`dhcp enable`), а адреса выдаются только на интерфейсах с `dhcp select global`.

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

DHCP-relay пересылает широковещательные запросы клиентов на сервер в другой сети, подставляя адрес интерфейса (giaddr); сервер выбирает пул по этому адресу. На Huawei: `dhcp select relay` и `dhcp relay server-ip <адрес>` на интерфейсе. Проверка: `display ip pool`, `display ip pool name LAN80 used`.

## RIP

RIPv2: обновления каждые 30 с и мгновенные при изменениях, метрика — число хопов (16 = недостижимо), split horizon, удаление маршрута через 180 с без обновлений. Приоритет 100.

```
system-view
rip 1
version 2
undo summary
network 10.0.0.0
silent-interface GigabitEthernet0/0/2
return
```

Ещё: `default-route originate`, `import-route direct|static|ospf 1`. Проверка: `display rip 1 route`, `display ip routing-table`.

## STP

STP включён на коммутаторах по умолчанию. Корень — коммутатор с наименьшими приоритетом и MAC; лишние связи блокируются (роль Alternate). Порты к ПК и маршрутизаторам работают как edge и пересылают сразу. Режим по умолчанию MSTP (быстрая сходимость).

```switch
system-view
stp mode rstp
stp priority 4096
interface GigabitEthernet0/0/1
stp edged-port enable
return
```

Ещё: `stp root primary|secondary`, `stp cost N`, `stp disable`. Проверка: `display stp brief`, `display stp`.

## IPv6

IPv6: глобальные адреса и link-local (EUI-64), обнаружение соседей (NDP), ping по IPv6, статические маршруты, объявления маршрутизатора (RA) и автонастройка адресов на ПК (SLAAC). IPv6 включается глобально (`ipv6`) и на каждом интерфейсе (`ipv6 enable`). RA по умолчанию не рассылаются: для SLAAC нужна команда `undo ipv6 nd ra halt`.

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

Проверка: `display ipv6 interface brief`, `display ipv6 routing-table`, `display ipv6 neighbors`, `ping ipv6 2001:db8:1::10`.

## Маршрутизация на коммутаторе (Vlanif)

S5720 маршрутизирует между интерфейсами `Vlanif` сразу, отдельная команда не нужна.

```switch
system-view
vlan batch 10 20
interface Vlanif10
ip address 10.0.10.1 24
interface Vlanif20
ip address 10.0.20.1 24
return
```

На коммутаторе доступны также `ip route-static`, `ospf`, `rip`, `ip pool` и `dhcp select global` на Vlanif. Проверка: `display ip interface brief`, `display ip routing-table`.

## Удалённый доступ (Telnet и STelnet)

Устройства принимают удалённый вход по Telnet (TCP 23) и SSH (TCP 22). В режиме симуляции видно рукопожатие TCP; у Telnet в деталях пакета виден даже пароль, у SSH содержимое зашифровано. Подключение открывается прямо в текущей консоли, `exit` возвращает обратно. Из удалённой консоли можно подключаться дальше. Серверы Telnet и STelnet (SSH) по умолчанию выключены.

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

Подключение: `telnet 10.0.0.2`, `stelnet 10.0.0.2` (спросит имя пользователя). Только пароль: `authentication-mode password` и `set authentication password cipher <пароль>`.

## EtherChannel (агрегирование каналов)

Несколько кабелей между двумя коммутаторами объединяются в один логический канал. STP считает его одним портом и ничего не блокирует, а при обрыве одного кабеля трафик идёт по оставшимся. Канал собирается, когда режимы на концах совместимы: `on` только с `on`; LACP `active` с `active` или `passive`; PAgP `desirable` с `desirable` или `auto`. Иначе порты остаются в состоянии suspended и трафик не передают.

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

Режимы Eth-Trunk: `mode lacp` (LACP) и `mode manual load-balance` (без протокола). Проверка: `display eth-trunk 1`.

## VRRP (резервный шлюз)

Два маршрутизатора в одной сети делят виртуальный IP-адрес, который ПК используют как шлюз. Активный (HSRP) или master (VRRP) выбирается по приоритету, при равенстве — по большему IP. Он отвечает на ARP виртуальным MAC. Если активный пропадает, резервный забирает роль через hold time (HSRP 10 с, VRRP около 3,6 с) и рассылает gratuitous ARP. По умолчанию HSRP не отбирает роль обратно (нужен `preempt`), а VRRP отбирает.

```
system-view
interface GigabitEthernet0/0/0
ip address 192.168.1.2 24
vrrp vrid 1 virtual-ip 192.168.1.254
vrrp vrid 1 priority 120
return
```

Отключить отбор роли: `vrrp vrid 1 preempt-mode disable`. Проверка: `display vrrp brief`.

## OSPFv3, RIPng и DHCPv6

Маршрутизаторы находят соседей по hello-пакетам на адреса ff02::5 (OSPFv3) и ff02::9 (RIPng) со своих link-local адресов и обмениваются маршрутами к IPv6-сетям. Следующим переходом в таблице становится link-local адрес соседа. DHCPv6 (stateful) раздаёт адреса из пула; шлюз по умолчанию ПК по-прежнему узнают из RA.

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

RIPng: `ripng 1` и `ripng 1 enable` на интерфейсе. Проверка: `display ospfv3 peer`, `display ipv6 routing-table`, `display dhcpv6 pool`.

## Port security и DHCP snooping

**Port security** ограничивает число MAC-адресов на access-порту. Первые адреса запоминаются как разрешённые; чужой MAC вызывает нарушение. `shutdown` (по умолчанию) переводит порт в err-disabled, вернуть его можно командой выключения и включения порта. `restrict` и `protect` просто отбрасывают кадры. **DHCP snooping** пропускает ответы DHCP-сервера только с доверенных портов, поэтому поддельный сервер в сети ничего не раздаст, и ведёт таблицу выданных адресов.

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

Проверка: `display port-security`, `display dhcp snooping user-bind all`. Вернуть порт: `shutdown`, затем `undo shutdown`.

## GRE, QoS, NAT64, Syslog/SNMP/NTP

GRE-туннель: интерфейс `Tunnel0/0/0` с `tunnel-protocol gre`, `source` и `destination`. QoS задаётся тройкой классификатор, поведение и политика; политика применяется на интерфейсе командой `traffic-policy … inbound|outbound`. NAT64 включается глобально и на интерфейсе со стороны IPv6.

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

Голосовой VLAN на коммутаторе: `voice-vlan 20 enable` в интерфейсе access-порта.

Проверка: `display traffic policy statistics`, `display nat64 session all`, `display logbuffer`, `display ntp-service status`, `display snmp-agent community`, `display ospfv3 lsdb`.

## Полный список команд: маршрутизатор AR2220

<!-- BEGIN GENERATED COMMANDS router -->
#### Пользовательский вид — `<Huawei>`

| Команда | Описание |
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
| `display isis peer` | Neighbours |
| `display isis lsdb` | Link state database |
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

#### Системный вид (system-view) — `[Huawei]`

| Команда | Описание |
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
| `display isis peer` | Neighbours |
| `display isis lsdb` | Link state database |
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
| `[undo] isis` | IS-IS |
| `[undo] isis INTEGER<1-65535>` | Process ID |
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

#### Вид интерфейса (interface …) — `[Huawei-GigabitEthernet0/0/1]`

| Команда | Описание |
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
| `display isis peer` | Neighbours |
| `display isis lsdb` | Link state database |
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
| `ip address A.B.C.D MASK/LEN sub` | Indicate a subordinate address |
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
| `[undo] isis enable` | Enable IS-IS on the interface |
| `[undo] isis enable INTEGER<1-65535>` | Process ID |
| `[undo] isis cost INTEGER<1-16777214>` | Cost |
| `[undo] isis ipv6 enable` | Enable IS-IS for IPv6 on the interface |
| `[undo] isis ipv6 enable INTEGER<1-65535>` | Process ID |
| `[undo] isis circuit-type LINE` | Circuit type |
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

## Полный список команд: коммутатор S5720

<!-- BEGIN GENERATED COMMANDS switch -->
#### Пользовательский вид — `<HUAWEI>`

| Команда | Описание |
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

#### Системный вид (system-view) — `[HUAWEI]`

| Команда | Описание |
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

#### Вид интерфейса (interface …) — `[HUAWEI-GigabitEthernet0/0/1]`

| Команда | Описание |
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
| `ip address A.B.C.D MASK/LEN sub` | Indicate a subordinate address |
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
