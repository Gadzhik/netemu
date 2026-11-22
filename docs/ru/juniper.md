# Juniper Junos

[← Руководство](guide.md)

Модели: маршрутизатор **Juniper SRX300** (порты `ge-0/0/0`–`ge-0/0/7`) и коммутатор **Juniper EX2300-24T** (`ge-0/0/0`–`ge-0/0/23`). Порты включены сразу после добавления.

## Режимы и candidate-конфигурация

| Приглашение | Режим | Как попасть | Как выйти |
|---|---|---|---|
| `root@srx300>` | операционный | при входе | — |
| `root@srx300#` | конфигурация | `configure` | `exit`, `commit and-quit` |

В режиме конфигурации команды `set` и `delete` меняют **candidate-конфигурацию**. На устройство она применяется только командой `commit`. До этого можно посмотреть изменения (`show`) или отменить их (`rollback`). `commit check` проверяет конфигурацию без применения, например на пересечение подсетей.

Операционные команды из режима конфигурации выполняются через `run`: `run show route`.

## Пример: маршрутизатор между двумя сетями

```
configure
set system host-name SRX1
set interfaces ge-0/0/0 unit 0 family inet address 10.0.2.2/30
set interfaces ge-0/0/1 unit 0 family inet address 192.168.30.1/24
set interfaces ge-0/0/1 description LAN30
set routing-options static route 192.168.1.0/24 next-hop 10.0.2.1
set routing-options static route 0.0.0.0/0 next-hop 10.0.2.1
commit and-quit
```

Статические маршруты в Junos имеют предпочтение 5. Резервный маршрут задаётся через `qualified-next-hop` с большим предпочтением:

```
set routing-options static route 192.168.1.0/24 qualified-next-hop 10.0.3.1 preference 20
```

Выключение порта: `set interfaces ge-0/0/2 disable`, включение: `delete interfaces ge-0/0/2 disable` (и затем `commit`).

Проверка: `show interfaces terse`, `show route`, `show arp`, `show configuration`, `ping 192.168.1.10 count 3`, `traceroute 192.168.1.10`.

Ping по умолчанию отправляет 5 пакетов (`count N` задаёт другое число). Остановить можно `Ctrl+C`.

## VLAN и trunk

На EX2300 используется синтаксис ELS. VLAN задаётся именем и номером (`set vlans SALES vlan-id 10`), порт указывает VLAN по имени или номеру. Имена проверяются при `commit`: если VLAN не описана, commit завершится ошибкой.

```switch
configure
set vlans SALES vlan-id 10
set vlans HR vlan-id 20
set interfaces ge-0/0/0 unit 0 family ethernet-switching interface-mode access
set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members SALES
set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members HR
set interfaces ge-0/0/23 unit 0 family ethernet-switching interface-mode trunk
set interfaces ge-0/0/23 unit 0 family ethernet-switching vlan members [ SALES HR ]
commit and-quit
```

Native VLAN trunk: `set interfaces ge-0/0/23 native-vlan-id 99`. Проверка: `show vlans`, `show ethernet-switching interface`, `show ethernet-switching table`.

**Маршрутизация между VLAN.** На SRX300 физический порт переводится в `vlan-tagging`, каждая VLAN становится логическим unit с `vlan-id`:

```
configure
set interfaces ge-0/0/0 vlan-tagging
set interfaces ge-0/0/0 unit 10 vlan-id 10 family inet address 192.168.10.1/24
set interfaces ge-0/0/0 unit 20 vlan-id 20
set interfaces ge-0/0/0 unit 20 family inet address 192.168.20.1/24
commit and-quit
```

## OSPF

В Junos OSPF включается на логических интерфейсах (`ge-0/0/0.0`, `lo0.0`) в зоне. Router ID задаётся в `routing-options` и общий для всех протоколов. Интерфейс `lo0` есть на SRX300 всегда.

```
configure
set interfaces lo0 unit 0 family inet address 1.1.1.1/32
set routing-options router-id 1.1.1.1
set protocols ospf area 0.0.0.0 interface ge-0/0/2.0 metric 10
set protocols ospf area 0.0.0.0 interface lo0.0 passive
commit and-quit
```

Перераспределение делается через политику:

```
configure
set policy-options policy-statement EXPORT-STATIC term 1 from protocol static
set policy-options policy-statement EXPORT-STATIC term 1 then accept
set protocols ospf export EXPORT-STATIC
commit and-quit
```

У интерфейса можно задать `passive`, `metric`, `hello-interval`, `dead-interval`, `priority`. Приоритет OSPF по умолчанию в Junos 128, стоимость `lo0` равна 0, предпочтение маршрутов OSPF 10, внешних 150.

Проверка OSPF: `show ospf neighbor`, `show ospf interface`, `show ospf database`, `show route protocol ospf`.

## Фильтры (firewall filter)

Фильтр состоит из термов: в `from` условия (адреса, протокол, порты, тип ICMP), в `then` действие. `accept` пропускает, `discard` молча отбрасывает, `reject` отбрасывает с ответом ICMP. После последнего терма неявный `discard`. Фильтр привязывается к логическому интерфейсу через `family inet filter input|output`.

```
configure
set interfaces ge-0/0/3 unit 0 family inet address 192.168.50.1/24
set firewall family inet filter PROTECT term NO-PING from source-address 192.168.50.11/32
set firewall family inet filter PROTECT term NO-PING from protocol icmp
set firewall family inet filter PROTECT term NO-PING then discard
set firewall family inet filter PROTECT term REST then accept
set interfaces ge-0/0/3 unit 0 family inet filter input PROTECT
commit and-quit
```

## NAT

NAT на SRX задаётся наборами правил (`rule-set`). Контекст указывается интерфейсами (`from interface`, `to interface`), зоны безопасности в симуляторе не моделируются.

```
configure
set interfaces ge-0/0/4 unit 0 family inet address 203.0.113.1/29
set security nat source rule-set OUT from interface ge-0/0/3.0
set security nat source rule-set OUT to interface ge-0/0/4.0
set security nat source rule-set OUT rule SNAT match source-address 192.168.50.0/24
set security nat source rule-set OUT rule SNAT then source-nat interface
set security nat static rule-set IN from interface ge-0/0/4.0
set security nat static rule-set IN rule WEB match destination-address 203.0.113.3/32
set security nat static rule-set IN rule WEB then static-nat prefix 192.168.50.10/32
commit and-quit
```

Также есть `source-nat pool` (`set security nat source pool P address A/32 to B/32`) и destination NAT (`set security nat destination pool WEB address 192.168.50.20/32 port 80`, правило с `match destination-address … destination-port …` и `then destination-nat pool WEB`). Проверка: `show security nat source summary`, `show security nat static rule all`, `show security flow session`, `show firewall`.

## BGP

Сессия BGP идёт по TCP-порту 179 и маршрутизируется как обычный трафик: для пиринга через loopback нужны маршрут до loopback соседа (IGP или статика) и адрес-источник; для eBGP с соседом не в подключённой сети нужен multihop; ACL может закрыть порт 179. Если `remote-as` указан неверно, сосед отвечает NOTIFICATION, и сессия не поднимается. Маршрут, у которого next-hop недостижим, не устанавливается: для iBGP обычно нужен next-hop-self. Выбор лучшего пути: weight, local-pref, локальные маршруты, длина AS_PATH, origin, MED, eBGP предпочтительнее iBGP, IGP-метрика до next-hop.

В Junos номер AS задаётся в `routing-options`, соседи объединяются в группы. Свои сети Junos анонсирует только через политику `export`, а полученные по BGP пересылает и без неё.

```
configure
set routing-options autonomous-system 65003
set policy-options policy-statement ADV-LAN term 1 from protocol direct
set policy-options policy-statement ADV-LAN term 1 from route-filter 192.168.30.0/24 exact
set policy-options policy-statement ADV-LAN term 1 then accept
set protocols bgp group EXT type external
set protocols bgp group EXT peer-as 65002
set protocols bgp group EXT neighbor 10.0.23.1
set protocols bgp group EXT export ADV-LAN
commit and-quit
```

Для iBGP: `type internal`, `local-address <адрес loopback>`, политика с `then next-hop self`. Для eBGP через несколько хопов: `multihop ttl N`. Предпочтение маршрутов BGP 170.

Проверка BGP: `show bgp summary`, `show bgp neighbor`, `show route protocol bgp`.

## DHCP

```
configure
set interfaces ge-0/0/5 unit 0 family inet address 192.168.60.1/24
set access address-assignment pool LAN60 family inet network 192.168.60.0/24
set access address-assignment pool LAN60 family inet range R1 low 192.168.60.100 high 192.168.60.199
set access address-assignment pool LAN60 family inet dhcp-attributes router 192.168.60.1
set access address-assignment pool LAN60 family inet dhcp-attributes name-server 192.168.60.2
set system services dhcp-local-server group LAN interface ge-0/0/5.0
commit and-quit
```

DHCP-relay пересылает широковещательные запросы клиентов на сервер в другой сети, подставляя адрес интерфейса (giaddr); сервер выбирает пул по этому адресу. В Junos: `set forwarding-options dhcp-relay server-group SRV 10.0.0.5`, `active-server-group SRV`, `group RELAY interface ge-0/0/6.0`. Проверка: `show dhcp server binding`.

## RIP

RIPv2: обновления каждые 30 с и мгновенные при изменениях, метрика — число хопов (16 = недостижимо), split horizon, удаление маршрута через 180 с без обновлений. Важно: Junos не анонсирует по RIP ничего, даже подключённые сети, без export-политики. Предпочтение 100.

```
configure
set policy-options policy-statement RIP-OUT term 1 from protocol direct
set policy-options policy-statement RIP-OUT term 1 then accept
set protocols rip group RIP neighbor ge-0/0/2.0
set protocols rip group RIP export RIP-OUT
commit and-quit
```

Проверка: `show rip neighbor`, `show route protocol rip`.

## STP

STP включён на коммутаторах по умолчанию. Корень — коммутатор с наименьшими приоритетом и MAC; лишние связи блокируются (роль Alternate). Порты к ПК и маршрутизаторам работают как edge и пересылают сразу. На EX2300 по умолчанию работает RSTP.

```switch
configure
set protocols rstp bridge-priority 4k
set protocols rstp interface ge-0/0/1 edge
commit and-quit
```

Ещё: `set protocols rstp interface X cost N`, `delete protocols rstp` (выключить). Проверка: `show spanning-tree bridge`, `show spanning-tree interface`.

## IPv6

IPv6: глобальные адреса и link-local (EUI-64), обнаружение соседей (NDP), ping по IPv6, статические маршруты, объявления маршрутизатора (RA) и автонастройка адресов на ПК (SLAAC). Junos пересылает IPv6 без дополнительных команд. RA в эмуляторе не рассылаются, поэтому ПК за SRX настраиваются статически.

```
configure
set interfaces ge-0/0/0 unit 0 family inet6 address 2001:db8:1::1/64
set routing-options rib inet6.0 static route 2001:db8:50::/48 next-hop 2001:db8:1::2
commit and-quit
```

Проверка: `show route table inet6.0`, `show ipv6 neighbors`, `ping 2001:db8:1::10 count 3`.

## Маршрутизация на коммутаторе (irb)

На EX2300 маршрутизирующий интерфейс VLAN — это логический интерфейс `irb.N`, привязанный к VLAN командой `l3-interface`.

```switch
configure
set vlans V10 vlan-id 10
set vlans V20 vlan-id 20
set interfaces irb unit 10 family inet address 10.0.10.1/24
set interfaces irb unit 20 family inet address 10.0.20.1/24
set vlans V10 l3-interface irb.10
set vlans V20 l3-interface irb.20
commit and-quit
```

На EX работают также `routing-options static`, `protocols ospf` (с интерфейсами `irb.N`) и фильтры. Проверка: `show interfaces terse`, `show route`.

## Удалённый доступ (SSH и Telnet)

Устройства принимают удалённый вход по Telnet (TCP 23) и SSH (TCP 22). В режиме симуляции видно рукопожатие TCP; у Telnet в деталях пакета виден даже пароль, у SSH содержимое зашифровано. Подключение открывается прямо в текущей консоли, `exit` возвращает обратно. Из удалённой консоли можно подключаться дальше. Службы включаются в `system services`, пароль пользователя Junos спрашивает дважды.

```
configure
set system services ssh
set system services telnet
set system login user admin class super-user authentication plain-text-password
Juniper1
Juniper1
commit and-quit
```

Подключение: `ssh 10.0.0.2 user admin`, `telnet 10.0.0.2`.

## EtherChannel (агрегирование каналов)

Несколько кабелей между двумя коммутаторами объединяются в один логический канал. STP считает его одним портом и ничего не блокирует, а при обрыве одного кабеля трафик идёт по оставшимся. Канал собирается, когда режимы на концах совместимы: `on` только с `on`; LACP `active` с `active` или `passive`; PAgP `desirable` с `desirable` или `auto`. Иначе порты остаются в состоянии suspended и трафик не передают.

```switch
configure
set chassis aggregated-devices ethernet device-count 1
set interfaces ge-0/0/0 ether-options 802.3ad ae0
set interfaces ge-0/0/1 ether-options 802.3ad ae0
set interfaces ae0 aggregated-ether-options lacp active
commit and-quit
```

Без `lacp` канал работает как статический (`on`). Проверка: `show lacp interfaces`.

## VRRP (резервный шлюз)

Два маршрутизатора в одной сети делят виртуальный IP-адрес, который ПК используют как шлюз. Активный (HSRP) или master (VRRP) выбирается по приоритету, при равенстве — по большему IP. Он отвечает на ARP виртуальным MAC. Если активный пропадает, резервный забирает роль через hold time (HSRP 10 с, VRRP около 3,6 с) и рассылает gratuitous ARP. По умолчанию HSRP не отбирает роль обратно (нужен `preempt`), а VRRP отбирает.

```
configure
set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.2/24 vrrp-group 1 virtual-address 192.168.1.254
set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.2/24 vrrp-group 1 priority 200
commit and-quit
```

Ещё: `… vrrp-group 1 no-preempt`. Проверка: `show vrrp`.

## OSPFv3 и RIPng

Маршрутизаторы находят соседей по hello-пакетам на адреса ff02::5 (OSPFv3) и ff02::9 (RIPng) со своих link-local адресов и обмениваются маршрутами к IPv6-сетям. Следующим переходом в таблице становится link-local адрес соседа. DHCPv6 (stateful) раздаёт адреса из пула; шлюз по умолчанию ПК по-прежнему узнают из RA.

```
configure
set interfaces ge-0/0/1 unit 0 family inet6 address 2001:db8:12::3/64
set protocols ospf3 area 0.0.0.0 interface ge-0/0/1.0
set protocols ripng group RIP neighbor ge-0/0/2.0
commit and-quit
```

Проверка: `show ospf3 neighbor`, `show ripng neighbor`, `show route table inet6.0`.

## DHCPv6, ограничение MAC и DHCP snooping

```
configure
set interfaces ge-0/0/0 unit 0 family inet6 address 2001:db8:7::1/64
set access address-assignment pool P6 family inet6 prefix 2001:db8:7::/64
set system services dhcp-local-server dhcpv6 group G interface ge-0/0/0.0
commit and-quit
```

На EX2300: `set switch-options interface ge-0/0/1 interface-mac-limit 1 packet-action shutdown` и `set vlans V1 forwarding-options dhcp-security group G overrides trusted interface ge-0/0/23`.

## GRE, Syslog, SNMP, NTP

На SRX туннель — встроенный интерфейс `gr-0/0/0`. Настройки syslog, SNMP и NTP, как и всё в Junos, вступают в силу после `commit`. На EX голосовой VLAN задаётся так: `set switch-options voip interface ge-0/0/1 vlan VOICE`.

```
set interfaces gr-0/0/0 unit 0 tunnel source 203.0.113.2 destination 198.51.100.2
set interfaces gr-0/0/0 unit 0 family inet address 10.255.0.1/30
set system syslog host 192.168.1.100 any any
set snmp community public authorization read-only
set snmp location Server-room-1
set system ntp server 192.168.1.100
```

Проверка: `show ntp status`, `show ntp associations`, `show log messages`, `show ospf3 database`.

## IDP: защита от червей и эксплойтов

Система предотвращения вторжений Juniper (IDP) отбрасывает пакеты-эксплойты и обращения к командным серверам (подробно — в разделе [Вредоносные программы](malware.md)):

```
set security idp idp-policy protect
set security idp active-policy protect
```

Состояние — `show security idp status`.

## Полный список команд: маршрутизатор SRX300

<!-- BEGIN GENERATED COMMANDS router -->
#### Операционный режим — `root@srx300>`

| Команда | Описание |
|---|---|
| `clear arp` | Clear address resolution information |
| `clear ethernet-switching table` | Clear MAC table |
| `configure` | Manipulate software configuration information |
| `exit` | Exit the management session |
| `ping <host>` | Hostname or IP address of remote host |
| `ping <host> count <count>` | Number of ping requests to send |
| `ping <host> count <count> rapid` | Send requests rapidly |
| `ping <host> rapid` | Send requests rapidly |
| `quit` | Exit the management session |
| `request system zeroize` | Erase all data, including configuration and log files |
| `show arp` | Show system Address Resolution Protocol table entries |
| `show arp no-resolve` | Don't attempt to print addresses symbolically |
| `show bgp summary` | Show overall BGP information |
| `show bgp neighbor` | Show BGP neighbors |
| `show bgp neighbor A.B.C.D` | IP address of BGP peer |
| `show configuration` | Show current configuration |
| `show dhcp server binding` | Show DHCP server client binding information |
| `show firewall` | Show firewall information |
| `show interfaces` | Show interface information |
| `show interfaces terse` | Display terse output |
| `show interfaces <interface_name>` | Name of physical or logical interface |
| `show interfaces <interface_name> terse` | Display terse output |
| `show ipv6 neighbors` | Show IPv6 neighbor cache |
| `show ospf neighbor` | Show neighbor status |
| `show ospf interface` | Show interface status |
| `show ospf database` | Show the OSPF link-state database |
| `show ospf route` | Show OSPF routing table |
| `show rip neighbor` | Show RIP neighbor information |
| `show route` | Show routing table information |
| `show route table inet6.0` | IPv6 unicast table |
| `show route table inet.0` | IPv4 unicast table |
| `show route protocol ospf/static/direct` | Name of protocol |
| `show security nat source summary` | Display source NAT summary |
| `show security nat static rule all` | All rules |
| `show security nat destination summary` | Display destination NAT summary |
| `show security zones` | Show security zone information |
| `show security policies` | Show security policies |
| `show security flow session` | Show session information |
| `show security idp` | Show IDP information |
| `show security idp status` | Show IDP status |
| `show security idp counters` | Show IDP counters |
| `show security idp counters application-identification` | Show IDP counters |
| `show system uptime` | Show time since system and processes started |
| `show version` | Show software process revision levels |
| `show ospf3 neighbor` | Show neighbor status |
| `show ospf3 database` | Show the OSPFv3 link-state database |
| `show ripng neighbor` | Show neighbor status |
| `show isis adjacency` | Show IS-IS adjacencies |
| `show isis database` | Show IS-IS link-state database |
| `show ntp associations` | Show NTP peers |
| `show ntp status` | Show NTP status |
| `show log messages` | System messages |
| `show vrrp` | Show VRRP information |
| `show vrrp summary` | Summary |
| `traceroute <host>` | IP address of remote host |
| `ssh <host>` | Hostname or address of remote host |
| `ssh <host> user <user>` | Username to use on the remote host |
| `telnet <host>` | Hostname or address of remote host |

#### Режим конфигурации (configure) — `root@srx300#`

| Команда | Описание |
|---|---|
| `commit` | Commit current set of changes |
| `commit and-quit` | Quit configuration mode if commit succeeds |
| `commit check` | Check correctness of syntax; do not apply changes |
| `delete access address-assignment pool <pool-name>` | Pool name |
| `delete firewall policer <policer-name>` | Policer name |
| `delete firewall family inet filter <filter-name>` | Filter name |
| `delete firewall family inet filter <filter-name> term <term-name>` | Term name |
| `delete forwarding-options dhcp-relay` | DHCP relay agent configuration |
| `delete interfaces <interface_name>` | Interface name |
| `delete interfaces <interface_name> description` | Text description of interface |
| `delete interfaces <interface_name> disable` | Disable this interface |
| `delete interfaces <interface_name> vlan-tagging` | Enable 802.1q VLAN tagging |
| `delete interfaces <interface_name> unit <interface_unit_number>` | Interface unit number |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet` | IPv4 parameters |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet rpf-check` | Reverse-path-forwarding checks (anti-spoofing) |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet rpf-check mode loose` | Reverse-path-forwarding loose mode |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet address` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet address <name>` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet filter input` | Name of filter applied to received packets |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet filter input <filter-name>` | Filter name |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet filter output` | Name of filter applied to transmitted packets |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet filter output <filter-name>` | Filter name |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet6` | IPv6 parameters |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet6 address` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet6 address X:X:X:X::X/<0-128>` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> family iso` | OSI ISO protocol parameters |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id` | Virtual LAN identifier value for 802.1q VLAN tags |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id>` | VLAN ID |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet` | IPv4 parameters |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet rpf-check` | Reverse-path-forwarding checks (anti-spoofing) |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet rpf-check mode loose` | Reverse-path-forwarding loose mode |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet address` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet address <name>` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet filter input` | Name of filter applied to received packets |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet filter input <filter-name>` | Filter name |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet filter output` | Name of filter applied to transmitted packets |
| `delete interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet filter output <filter-name>` | Filter name |
| `delete policy-options policy-statement <policy-name>` | Name to identify a policy filter |
| `delete policy-options policy-statement <policy-name> term <term-name> from protocol static/direct/ospf/bgp` | Protocol name |
| `delete policy-options policy-statement <policy-name> term <term-name> from route-filter <address> exact` | exact match |
| `delete policy-options policy-statement <policy-name> term <term-name> from route-filter <address> orlonger` | orlonger match |
| `delete policy-options policy-statement <policy-name> term <term-name> then next-hop self` | Use a local address as the next-hop address |
| `delete policy-options policy-statement <policy-name> term <term-name> then accept` | Accept a route |
| `delete policy-options policy-statement <policy-name> term <term-name> then reject` | Reject a route |
| `delete protocols bgp` | BGP options |
| `delete protocols bgp group <group-name>` | Group name |
| `delete protocols rip` | RIP options |
| `delete protocols rip group <group-name>` | Group name |
| `delete protocols ospf` | OSPF configuration |
| `delete protocols ospf area <area_id>` | Area ID |
| `delete protocols ospf area <area_id> interface` | Include an interface in this area |
| `delete protocols ospf area <area_id> interface <interface_name>` | Interface name |
| `delete protocols ospf export` | Export policy |
| `delete protocols ospf export <policy-name>` | Policy name |
| `delete protocols isis` | IS-IS configuration |
| `delete protocols ospf3` | OSPFv3 configuration |
| `delete protocols ripng` | RIPng configuration |
| `delete routing-options rib inet6.0 static route X:X:X:X::X/<0-128>` | Destination prefix |
| `delete routing-options static` | Static routing information |
| `delete routing-options static route <destination>` | Destination prefix |
| `delete routing-options static route <destination> discard` | Drop packets to destination; send no ICMP unreachables |
| `delete routing-options static route <destination> next-hop <next-hop>` | Next hop to destination |
| `delete routing-options static route <destination> qualified-next-hop <next-hop>` | Next hop with qualifiers |
| `delete routing-options router-id` | Router identifier |
| `delete routing-options autonomous-system` | Autonomous system number |
| `delete security zones` | Zone configuration |
| `delete security policies` | Configure Network Security Policies |
| `delete security ike` | IKE configuration |
| `delete security ipsec` | IPsec configuration |
| `delete security nat` | Configure Network Address Translation |
| `delete security idp` | Intrusion Detection and Prevention |
| `delete system host-name` | Hostname for this router |
| `delete system services dhcp-local-server` | Dynamic Host Configuration Protocol server |
| `delete system services dhcp-local-server group <group-name>` | Group name |
| `delete system services dhcp-local-server group <group-name> interface <interface-name>` | Interface name |
| `delete system services ssh` | Allow ssh login |
| `delete system services telnet` | Allow telnet login |
| `delete system syslog host <hostname>` | Host to be notified |
| `delete system syslog host <hostname> any any` | Facility and level |
| `delete system ntp server <address>` | NTP server |
| `delete system login user <name>` | Username |
| `delete snmp community <community-name>` | Configure a community string |
| `delete snmp community <community-name> authorization read-only` | Read-only access |
| `delete snmp community <community-name> authorization read-write` | Read-write access |
| `delete snmp location` | Physical location of system |
| `delete snmp location <location>` | Physical location of system |
| `delete snmp contact` | Contact information for administrator |
| `delete snmp contact <contact>` | Contact information for administrator |
| `exit` | Exit from this level |
| `quit` | Quit from this level |
| `rollback` | Roll back to previous committed configuration |
| `rollback <0-49>` | Roll back to previous committed configuration |
| `run LINE` | Run an operational-mode command |
| `set access address-assignment pool <pool-name> family inet network/range/dhcp-attributes ...` | IPv4 |
| `set access address-assignment pool <pool-name> family inet6 prefix X:X::/64 / dhcp-attributes dns-server X` | IPv6 |
| `set firewall policer <policer-name> if-exceeding bandwidth-limit 1m burst-size-limit 15k / then discard` | Policer name |
| `set firewall family inet filter <filter-name> term <term-name> from/then ...` | Match conditions or actions |
| `set forwarding-options dhcp-relay server-group/active-server-group/group ...` | DHCP relay agent configuration |
| `set interfaces <interface_name> description <description>` | Text description of interface |
| `set interfaces <interface_name> disable` | Disable this interface |
| `set interfaces <interface_name> vlan-tagging` | Enable 802.1q VLAN tagging |
| `set interfaces <interface_name> unit <interface_unit_number> family inet rpf-check` | Reverse-path-forwarding checks (anti-spoofing) |
| `set interfaces <interface_name> unit <interface_unit_number> family inet rpf-check mode loose` | Reverse-path-forwarding loose mode |
| `set interfaces <interface_name> unit <interface_unit_number> family inet address <name>` | Interface address/destination prefix |
| `set interfaces <interface_name> unit <interface_unit_number> family inet address <name> vrrp-group <group_id> virtual-address A / priority N / preempt / no-preempt` | VRRP group identifier |
| `set interfaces <interface_name> unit <interface_unit_number> family inet filter input <filter-name>` | Filter name |
| `set interfaces <interface_name> unit <interface_unit_number> family inet filter output <filter-name>` | Filter name |
| `set interfaces <interface_name> unit <interface_unit_number> family inet6 address X:X:X:X::X/<0-128>` | Interface address/destination prefix |
| `set interfaces <interface_name> unit <interface_unit_number> family iso` | OSI ISO protocol parameters |
| `set interfaces <interface_name> unit <interface_unit_number> family iso address <address>` | NET, e.g. 49.0001.0000.0000.0001.00 |
| `set interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id>` | VLAN ID |
| `set interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet rpf-check` | Reverse-path-forwarding checks (anti-spoofing) |
| `set interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet rpf-check mode loose` | Reverse-path-forwarding loose mode |
| `set interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet address <name>` | Interface address/destination prefix |
| `set interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet filter input <filter-name>` | Filter name |
| `set interfaces <interface_name> unit <interface_unit_number> vlan-id <vlan-id> family inet filter output <filter-name>` | Filter name |
| `set interfaces <interface_name> unit <interface_unit_number> tunnel source A.B.C.D destination A.B.C.D` | Tunnel parameters |
| `set policy-options policy-statement <policy-name> term <term-name> from protocol static/direct/ospf/bgp` | Protocol name |
| `set policy-options policy-statement <policy-name> term <term-name> from route-filter <address> exact` | exact match |
| `set policy-options policy-statement <policy-name> term <term-name> from route-filter <address> orlonger` | orlonger match |
| `set policy-options policy-statement <policy-name> term <term-name> then next-hop self` | Use a local address as the next-hop address |
| `set policy-options policy-statement <policy-name> term <term-name> then accept` | Accept a route |
| `set policy-options policy-statement <policy-name> term <term-name> then reject` | Reject a route |
| `set protocols bgp group <group-name> type/peer-as/neighbor/local-address/multihop/export ...` | Group statement |
| `set protocols rip group <group-name> neighbor/export ...` | Group name |
| `set protocols ospf area <area_id> interface <interface_name>` | Interface name |
| `set protocols ospf area <area_id> interface <interface_name> passive` | Do not run OSPF, but advertise it |
| `set protocols ospf area <area_id> interface <interface_name> metric <metric>` | Interface metric |
| `set protocols ospf area <area_id> interface <interface_name> hello-interval <hello-interval>` | Hello interval |
| `set protocols ospf area <area_id> interface <interface_name> dead-interval <dead-interval>` | Dead interval |
| `set protocols ospf area <area_id> interface <interface_name> priority <priority>` | Designated router priority |
| `set protocols ospf export <policy-name>` | Policy name |
| `set protocols isis interface <name> [passive] / interface <name> level 2 metric N / level 1 disable` | IS-IS statement |
| `set protocols ospf3 area <area_id> interface <interface_name>` | Interface name |
| `set protocols ripng group <group_name> neighbor <interface_name>` | Interface name |
| `set routing-options rib inet6.0 static route X:X:X:X::X/<0-128> next-hop X:X:X:X::X` | Next hop |
| `set routing-options static route <destination> discard` | Drop packets to destination; send no ICMP unreachables |
| `set routing-options static route <destination> next-hop <next-hop>` | Next hop to destination |
| `set routing-options static route <destination> qualified-next-hop <next-hop>` | Next hop with qualifiers |
| `set routing-options static route <destination> qualified-next-hop <next-hop> preference <preference>` | Preference of this next hop |
| `set routing-options router-id <router-id>` | Router identifier |
| `set routing-options autonomous-system <as-number>` | Autonomous system number |
| `set security zones security-zone <name> interfaces <ifl> ...` | Zone configuration |
| `set security policies from-zone A to-zone B policy P match/then ...` | Configure Network Security Policies |
| `set security ike policy/gateway/proposal ...` | IKE statement |
| `set security ipsec vpn/policy/proposal ...` | IPsec statement |
| `set security nat source/static/destination ...` | NAT statement |
| `set security idp active-policy <name> / idp-policy <name> ...` | IDP statement |
| `set system host-name <host-name>` | Hostname for this router |
| `set system services dhcp-local-server group <group-name> interface <interface-name>` | Interface name |
| `set system services dhcp-local-server dhcpv6 group <group-name> interface <interface-name>` | Interface |
| `set system services ssh` | Allow ssh login |
| `set system services telnet` | Allow telnet login |
| `set system syslog host <hostname>` | Host to be notified |
| `set system syslog host <hostname> any any` | Facility and level |
| `set system ntp server <address>` | NTP server |
| `set system login user <name> class super-user authentication plain-text-password` | Username |
| `set system root-authentication plain-text-password` | Prompt for plain text password |
| `set snmp community <community-name>` | Configure a community string |
| `set snmp community <community-name> authorization read-only` | Read-only access |
| `set snmp community <community-name> authorization read-write` | Read-write access |
| `set snmp location` | Physical location of system |
| `set snmp location <location>` | Physical location of system |
| `set snmp contact` | Contact information for administrator |
| `set snmp contact <contact>` | Contact information for administrator |
| `show` | Show a parameter |
| `top` | Exit to top level of configuration |
<!-- END GENERATED COMMANDS -->

## Полный список команд: коммутатор EX2300

<!-- BEGIN GENERATED COMMANDS switch -->
#### Операционный режим — `root@ex2300>`

| Команда | Описание |
|---|---|
| `clear arp` | Clear address resolution information |
| `clear ethernet-switching table` | Clear MAC table |
| `configure` | Manipulate software configuration information |
| `exit` | Exit the management session |
| `ping <host>` | Hostname or IP address of remote host |
| `ping <host> count <count>` | Number of ping requests to send |
| `ping <host> count <count> rapid` | Send requests rapidly |
| `ping <host> rapid` | Send requests rapidly |
| `quit` | Exit the management session |
| `request system zeroize` | Erase all data, including configuration and log files |
| `show arp` | Show system Address Resolution Protocol table entries |
| `show arp no-resolve` | Don't attempt to print addresses symbolically |
| `show bgp summary` | Show overall BGP information |
| `show bgp neighbor` | Show BGP neighbors |
| `show bgp neighbor A.B.C.D` | IP address of BGP peer |
| `show configuration` | Show current configuration |
| `show dhcp server binding` | Show DHCP server client binding information |
| `show ethernet-switching table` | Show media access control table |
| `show ethernet-switching interface` | Show Ethernet-switching interface information |
| `show firewall` | Show firewall information |
| `show interfaces` | Show interface information |
| `show interfaces terse` | Display terse output |
| `show interfaces <interface_name>` | Name of physical or logical interface |
| `show interfaces <interface_name> terse` | Display terse output |
| `show ipv6 neighbors` | Show IPv6 neighbor cache |
| `show lacp interfaces` | Show LACP aggregated interface information |
| `show ospf neighbor` | Show neighbor status |
| `show ospf interface` | Show interface status |
| `show ospf database` | Show the OSPF link-state database |
| `show ospf route` | Show OSPF routing table |
| `show rip neighbor` | Show RIP neighbor information |
| `show route` | Show routing table information |
| `show route table inet6.0` | IPv6 unicast table |
| `show route table inet.0` | IPv4 unicast table |
| `show route protocol ospf/static/direct` | Name of protocol |
| `show spanning-tree bridge` | Show STP bridge information |
| `show spanning-tree interface` | Show STP interface information |
| `show system uptime` | Show time since system and processes started |
| `show version` | Show software process revision levels |
| `show vlans` | Show VLAN information |
| `show ospf3 neighbor` | Show neighbor status |
| `show ospf3 database` | Show the OSPFv3 link-state database |
| `show ripng neighbor` | Show neighbor status |
| `show ntp associations` | Show NTP peers |
| `show ntp status` | Show NTP status |
| `show log messages` | System messages |
| `show vrrp` | Show VRRP information |
| `show vrrp summary` | Summary |
| `traceroute <host>` | IP address of remote host |
| `ssh <host>` | Hostname or address of remote host |
| `ssh <host> user <user>` | Username to use on the remote host |
| `telnet <host>` | Hostname or address of remote host |

#### Режим конфигурации (configure) — `root@ex2300#`

| Команда | Описание |
|---|---|
| `commit` | Commit current set of changes |
| `commit and-quit` | Quit configuration mode if commit succeeds |
| `commit check` | Check correctness of syntax; do not apply changes |
| `delete access address-assignment pool <pool-name>` | Pool name |
| `delete firewall policer <policer-name>` | Policer name |
| `delete firewall family inet filter <filter-name>` | Filter name |
| `delete firewall family inet filter <filter-name> term <term-name>` | Term name |
| `delete forwarding-options dhcp-relay` | DHCP relay agent configuration |
| `delete interfaces <interface_name>` | Interface name |
| `delete interfaces <interface_name> description` | Text description of interface |
| `delete interfaces <interface_name> disable` | Disable this interface |
| `delete interfaces <interface_name> native-vlan-id` | Virtual LAN identifier for untagged frames |
| `delete interfaces <interface_name> unit <interface_unit_number>` | Interface unit number |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet` | IPv4 parameters |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet rpf-check` | Reverse-path-forwarding checks (anti-spoofing) |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet rpf-check mode loose` | Reverse-path-forwarding loose mode |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet address` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> family inet address <name>` | Interface address/destination prefix |
| `delete interfaces <interface_name> unit <interface_unit_number> family ethernet-switching` | Ethernet switching parameters |
| `delete interfaces <interface_name> unit <interface_unit_number> family ethernet-switching interface-mode` | Interface mode |
| `delete interfaces <interface_name> unit <interface_unit_number> family ethernet-switching interface-mode access` | Access mode |
| `delete interfaces <interface_name> unit <interface_unit_number> family ethernet-switching interface-mode trunk` | Trunk mode |
| `delete interfaces <interface_name> unit <interface_unit_number> family ethernet-switching vlan members` | VLAN membership |
| `delete interfaces <interface_name> unit <interface_unit_number> family ethernet-switching vlan members <vlan-name>` | VLAN name or ID, or [ list ] |
| `delete interfaces <interface_name> ether-options 802.3ad` | Aggregated Ethernet bundle membership |
| `delete interfaces <interface_name> aggregated-ether-options lacp` | LACP options |
| `delete interfaces irb` | Integrated routing and bridging interface |
| `delete interfaces irb unit <interface_unit_number>` | Interface unit number |
| `delete interfaces irb unit <interface_unit_number> family inet` | IPv4 parameters |
| `delete interfaces irb unit <interface_unit_number> family inet address` | Interface address/destination prefix |
| `delete policy-options policy-statement <policy-name>` | Name to identify a policy filter |
| `delete policy-options policy-statement <policy-name> term <term-name> from protocol static/direct/ospf/bgp` | Protocol name |
| `delete policy-options policy-statement <policy-name> term <term-name> from route-filter <address> exact` | exact match |
| `delete policy-options policy-statement <policy-name> term <term-name> from route-filter <address> orlonger` | orlonger match |
| `delete policy-options policy-statement <policy-name> term <term-name> then next-hop self` | Use a local address as the next-hop address |
| `delete policy-options policy-statement <policy-name> term <term-name> then accept` | Accept a route |
| `delete policy-options policy-statement <policy-name> term <term-name> then reject` | Reject a route |
| `delete protocols bgp` | BGP options |
| `delete protocols bgp group <group-name>` | Group name |
| `delete protocols rip` | RIP options |
| `delete protocols rip group <group-name>` | Group name |
| `delete protocols rstp` | RSTP options |
| `delete protocols stp` | STP options |
| `delete protocols ospf` | OSPF configuration |
| `delete protocols ospf area <area_id>` | Area ID |
| `delete protocols ospf area <area_id> interface` | Include an interface in this area |
| `delete protocols ospf area <area_id> interface <interface_name>` | Interface name |
| `delete protocols ospf export` | Export policy |
| `delete protocols ospf export <policy-name>` | Policy name |
| `delete protocols ospf3` | OSPFv3 configuration |
| `delete protocols ripng` | RIPng configuration |
| `delete routing-options rib inet6.0 static route X:X:X:X::X/<0-128>` | Destination prefix |
| `delete routing-options static` | Static routing information |
| `delete routing-options static route <destination>` | Destination prefix |
| `delete routing-options static route <destination> discard` | Drop packets to destination; send no ICMP unreachables |
| `delete routing-options static route <destination> next-hop <next-hop>` | Next hop to destination |
| `delete routing-options static route <destination> qualified-next-hop <next-hop>` | Next hop with qualifiers |
| `delete routing-options router-id` | Router identifier |
| `delete routing-options autonomous-system` | Autonomous system number |
| `delete system host-name` | Hostname for this router |
| `delete system syslog host <hostname>` | Host to be notified |
| `delete system syslog host <hostname> any any` | Facility and level |
| `delete system ntp server <address>` | NTP server |
| `delete system services ssh` | Allow ssh login |
| `delete system services telnet` | Allow telnet login |
| `delete system login user <name>` | Username |
| `delete vlans <vlan_name>` | VLAN name |
| `delete vlans <vlan_name> l3-interface` | Layer 3 interface for this VLAN |
| `delete switch-options voip interface <interface-name> vlan <vlan-name>` | Voice VLAN |
| `delete snmp community <community-name>` | Configure a community string |
| `delete snmp community <community-name> authorization read-only` | Read-only access |
| `delete snmp community <community-name> authorization read-write` | Read-write access |
| `delete snmp location` | Physical location of system |
| `delete snmp location <location>` | Physical location of system |
| `delete snmp contact` | Contact information for administrator |
| `delete snmp contact <contact>` | Contact information for administrator |
| `exit` | Exit from this level |
| `quit` | Quit from this level |
| `rollback` | Roll back to previous committed configuration |
| `rollback <0-49>` | Roll back to previous committed configuration |
| `run LINE` | Run an operational-mode command |
| `set access address-assignment pool <pool-name> family inet network/range/dhcp-attributes ...` | IPv4 |
| `set chassis aggregated-devices ethernet device-count <device-count>` | Number of aggregated Ethernet devices |
| `set firewall policer <policer-name> if-exceeding bandwidth-limit 1m burst-size-limit 15k / then discard` | Policer name |
| `set firewall family inet filter <filter-name> term <term-name> from/then ...` | Match conditions or actions |
| `set forwarding-options dhcp-relay server-group/active-server-group/group ...` | DHCP relay agent configuration |
| `set interfaces <interface_name> description <description>` | Text description of interface |
| `set interfaces <interface_name> disable` | Disable this interface |
| `set interfaces <interface_name> native-vlan-id <vlan-id>` | VLAN ID |
| `set interfaces <interface_name> unit <interface_unit_number> family inet rpf-check` | Reverse-path-forwarding checks (anti-spoofing) |
| `set interfaces <interface_name> unit <interface_unit_number> family inet rpf-check mode loose` | Reverse-path-forwarding loose mode |
| `set interfaces <interface_name> unit <interface_unit_number> family inet address <name>` | Interface address/destination prefix |
| `set interfaces <interface_name> unit <interface_unit_number> family inet address <name> vrrp-group <group_id> virtual-address A / priority N / preempt / no-preempt` | VRRP group identifier |
| `set interfaces <interface_name> unit <interface_unit_number> family ethernet-switching` | Ethernet switching parameters |
| `set interfaces <interface_name> unit <interface_unit_number> family ethernet-switching interface-mode access` | Access mode |
| `set interfaces <interface_name> unit <interface_unit_number> family ethernet-switching interface-mode trunk` | Trunk mode |
| `set interfaces <interface_name> unit <interface_unit_number> family ethernet-switching vlan members <vlan-name>` | VLAN name or ID, or [ list ] |
| `set interfaces <interface_name> ether-options 802.3ad <ae-interface>` | aeN |
| `set interfaces <interface_name> aggregated-ether-options lacp active` | Transmit LACP packets |
| `set interfaces <interface_name> aggregated-ether-options lacp passive` | Respond to LACP packets |
| `set interfaces irb unit <interface_unit_number> family inet address <name>` | Interface address |
| `set policy-options policy-statement <policy-name> term <term-name> from protocol static/direct/ospf/bgp` | Protocol name |
| `set policy-options policy-statement <policy-name> term <term-name> from route-filter <address> exact` | exact match |
| `set policy-options policy-statement <policy-name> term <term-name> from route-filter <address> orlonger` | orlonger match |
| `set policy-options policy-statement <policy-name> term <term-name> then next-hop self` | Use a local address as the next-hop address |
| `set policy-options policy-statement <policy-name> term <term-name> then accept` | Accept a route |
| `set policy-options policy-statement <policy-name> term <term-name> then reject` | Reject a route |
| `set protocols bgp group <group-name> type/peer-as/neighbor/local-address/multihop/export ...` | Group statement |
| `set protocols rip group <group-name> neighbor/export ...` | Group name |
| `set protocols rstp` | RSTP options |
| `set protocols rstp bridge-priority/interface ...` | RSTP options |
| `set protocols stp` | STP options |
| `set protocols stp bridge-priority/interface ...` | STP options |
| `set protocols ospf area <area_id> interface <interface_name>` | Interface name |
| `set protocols ospf area <area_id> interface <interface_name> passive` | Do not run OSPF, but advertise it |
| `set protocols ospf area <area_id> interface <interface_name> metric <metric>` | Interface metric |
| `set protocols ospf area <area_id> interface <interface_name> hello-interval <hello-interval>` | Hello interval |
| `set protocols ospf area <area_id> interface <interface_name> dead-interval <dead-interval>` | Dead interval |
| `set protocols ospf area <area_id> interface <interface_name> priority <priority>` | Designated router priority |
| `set protocols ospf export <policy-name>` | Policy name |
| `set protocols ospf3 area <area_id> interface <interface_name>` | Interface name |
| `set protocols ripng group <group_name> neighbor <interface_name>` | Interface name |
| `set routing-options rib inet6.0 static route X:X:X:X::X/<0-128> next-hop X:X:X:X::X` | Next hop |
| `set routing-options static route <destination> discard` | Drop packets to destination; send no ICMP unreachables |
| `set routing-options static route <destination> next-hop <next-hop>` | Next hop to destination |
| `set routing-options static route <destination> qualified-next-hop <next-hop>` | Next hop with qualifiers |
| `set routing-options static route <destination> qualified-next-hop <next-hop> preference <preference>` | Preference of this next hop |
| `set routing-options router-id <router-id>` | Router identifier |
| `set routing-options autonomous-system <as-number>` | Autonomous system number |
| `set system host-name <host-name>` | Hostname for this router |
| `set system syslog host <hostname>` | Host to be notified |
| `set system syslog host <hostname> any any` | Facility and level |
| `set system ntp server <address>` | NTP server |
| `set system services ssh` | Allow ssh login |
| `set system services telnet` | Allow telnet login |
| `set system login user <name> class super-user authentication plain-text-password` | Username |
| `set system root-authentication plain-text-password` | Prompt for plain text password |
| `set vlans <vlan_name> l3-interface <interface_name>` | irb.N |
| `set vlans <vlan_name> vlan-id <vlan-id>` | VLAN ID |
| `set vlans <vlan_name> forwarding-options dhcp-security` | DHCP snooping |
| `set vlans <vlan_name> forwarding-options dhcp-security group G overrides trusted interface ge-0/0/23` | DHCP snooping |
| `set switch-options interface <interface-name> interface-mac-limit <limit>` | Limit |
| `set switch-options interface <interface-name> interface-mac-limit <limit> packet-action drop` | drop |
| `set switch-options interface <interface-name> interface-mac-limit <limit> packet-action drop-and-log` | drop-and-log |
| `set switch-options interface <interface-name> interface-mac-limit <limit> packet-action shutdown` | shutdown |
| `set switch-options interface <interface-name> interface-mac-limit <limit> packet-action log` | log |
| `set switch-options voip interface <interface-name> vlan <vlan-name>` | Voice VLAN |
| `set snmp community <community-name>` | Configure a community string |
| `set snmp community <community-name> authorization read-only` | Read-only access |
| `set snmp community <community-name> authorization read-write` | Read-write access |
| `set snmp location` | Physical location of system |
| `set snmp location <location>` | Physical location of system |
| `set snmp contact` | Contact information for administrator |
| `set snmp contact <contact>` | Contact information for administrator |
| `show` | Show a parameter |
| `top` | Exit to top level of configuration |
<!-- END GENERATED COMMANDS -->
