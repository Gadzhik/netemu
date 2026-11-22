# MikroTik RouterOS

[← Руководство](guide.md)

Модели: маршрутизатор **MikroTik RB4011** (порты `ether1`–`ether10`) и коммутатор **MikroTik CRS326-24G** (`ether1`–`ether24`). Консоль повторяет RouterOS v7. Порты включены сразу после добавления.

## Как устроена консоль

Команды организованы в меню. В меню можно перейти и выполнять команды без пути:

```
[admin@MikroTik] > /ip address
[admin@MikroTik] /ip address> print
[admin@MikroTik] /ip address> ..
[admin@MikroTik] /ip> /
[admin@MikroTik] >
```

Или указать полный путь из любого меню: `/ip address print`. Аргументы задаются как `ключ=значение`. Записи удаляются по номеру из `print`: `/ip route remove 0`.

## Пример: маршрутизатор между двумя сетями

```
/system identity set name=MT1
/ip address add address=10.0.1.2/30 interface=ether1
/ip address add address=192.168.20.1/24 interface=ether3
/ip route add dst-address=192.168.1.0/24 gateway=10.0.1.1
/ip route add gateway=10.0.2.2
```

Маршрут без `dst-address` — маршрут по умолчанию (0.0.0.0/0). Резервный маршрут задаётся большей дистанцией: `/ip route add dst-address=192.168.1.0/24 gateway=10.0.3.1 distance=10`.

Выключение и включение порта: `/interface disable ether2`, `/interface enable ether2`. Комментарий: `/interface set ether1 comment=uplink`.

Проверка: `/ip address print`, `/ip route print` (флаги `A` — активный, `s` — статический, `c` — подключённый), `/ip arp print`, `/ping 192.168.2.10 count=3`, `/tool traceroute 192.168.2.10`, `/export`.

Ping по умолчанию отправляет 4 пакета (`count=` задаёт другое число), чтобы консоль не занималась бесконечно. Остановить можно `Ctrl+C`.

## VLAN и trunk

На CRS326 все порты входят в бридж `bridge`. VLAN настраиваются так же, как в RouterOS v7: у порта задаётся `pvid` (VLAN для нетегированных кадров), в таблицу `/interface bridge vlan` добавляются тегированные и нетегированные участники. Фильтрация включается `vlan-filtering=yes`; пока она выключена, бридж передаёт кадры, не глядя на метки VLAN.

```switch
/interface bridge port set ether1 pvid=10
/interface bridge port set ether2 pvid=20
/interface bridge vlan add bridge=bridge vlan-ids=10 tagged=ether24 untagged=ether1
/interface bridge vlan add bridge=bridge vlan-ids=20 tagged=ether24 untagged=ether2
/interface bridge set bridge vlan-filtering=yes
```

Порт, указанный в `tagged=`, работает как trunk. Проверка: `/interface bridge vlan print`, `/interface bridge port print`, `/interface bridge host print`, `/export`.

**Маршрутизация между VLAN.** На RB4011 создаётся VLAN-интерфейс поверх физического порта, и адрес назначается ему:

```
/interface vlan add name=vlan30 vlan-id=30 interface=ether2
/interface vlan add name=vlan40 vlan-id=40 interface=ether2
/ip address add address=192.168.30.1/24 interface=vlan30
/ip address add address=192.168.40.1/24 interface=vlan40
```

Проверка: `/interface vlan print`, `/ip address print`.

## OSPF

OSPF в RouterOS v7 настраивается тремя таблицами: экземпляр (`instance`), зона (`area`) и шаблоны интерфейсов (`interface-template`). Шаблон выбирает интерфейсы по сети (`networks=`) или по имени (`interfaces=`). Loopback на маршрутизаторе делается пустым бриджем.

```
/interface bridge add name=loopback
/ip address add address=1.1.1.1/32 interface=loopback
/routing ospf instance add name=default-v2 router-id=1.1.1.1
/routing ospf area add name=backbone-v2 area-id=0.0.0.0 instance=default-v2
/routing ospf interface-template add networks=10.0.1.0/30 area=backbone-v2
/routing ospf interface-template add networks=1.1.1.1/32 area=backbone-v2 passive
```

Маршрут по умолчанию и перераспределение задаются у экземпляра: `/routing ospf instance set default-v2 originate-default=always redistribute=static,connected`. В шаблоне можно указать `cost=`, `priority=`, `hello-interval=`, `dead-interval=`. Стоимость интерфейса по умолчанию 1.

Проверка OSPF: `/routing ospf neighbor print`, `/routing ospf interface print`, `/routing ospf lsa print`, `/ip route print` (маршруты OSPF отмечены `o`).

## Firewall и NAT

Правила фильтра работают в цепочках: `input` для трафика к самому маршрутизатору, `forward` для проходящего. Если ни одно правило не сработало, пакет пропускается. Действия: `accept`, `drop`, `reject` (с ответом ICMP).

```
/ip address add address=203.0.113.1/29 interface=ether5
/ip firewall filter add chain=forward src-address=192.168.20.11 protocol=icmp action=drop
/ip firewall filter add chain=input protocol=tcp dst-port=23 action=reject
/ip firewall nat add chain=srcnat out-interface=ether5 action=masquerade
/ip firewall nat add chain=dstnat dst-address=203.0.113.1 protocol=tcp dst-port=8080 action=dst-nat to-addresses=192.168.20.10 to-ports=80
```

`masquerade` подменяет источник адресом внешнего интерфейса; `action=src-nat to-addresses=A-B` транслирует в диапазон. Проверка: `/ip firewall filter print stats`, `/ip firewall nat print`, `/ip firewall connection print`.

## BGP

Сессия BGP идёт по TCP-порту 179 и маршрутизируется как обычный трафик: для пиринга через loopback нужны маршрут до loopback соседа (IGP или статика) и адрес-источник; для eBGP с соседом не в подключённой сети нужен multihop; ACL может закрыть порт 179. Если `remote-as` указан неверно, сосед отвечает NOTIFICATION, и сессия не поднимается. Маршрут, у которого next-hop недостижим, не устанавливается: для iBGP обычно нужен next-hop-self. Выбор лучшего пути: weight, local-pref, локальные маршруты, длина AS_PATH, origin, MED, eBGP предпочтительнее iBGP, IGP-метрика до next-hop.

В RouterOS v7 каждый сосед описывается записью `/routing bgp connection`. Анонсируемые сети берутся из списка адресов (`output.network`).

```
/ip firewall address-list add list=bgp-networks address=192.168.20.0/24
/routing bgp connection add name=to-isp remote.address=10.0.24.2 remote.as=65004 as=65001 local.role=ebgp router-id=2.2.2.2 output.network=bgp-networks
```

Ещё: `local.address=` (адрес-источник), `multihop=yes`, `nexthop-choice=force-self`, `output.redistribute=connected,static`, `output.default-originate=always`, `local.role=ibgp`. Проверка BGP: `/routing bgp session print`, `/routing bgp advertisements print`, `/ip route print` (маршруты BGP отмечены `b`).

## DHCP

```
/ip address add address=192.168.70.1/24 interface=ether6
/ip pool add name=dhcp_pool ranges=192.168.70.100-192.168.70.200
/ip dhcp-server add name=dhcp1 interface=ether6 address-pool=dhcp_pool
/ip dhcp-server network add address=192.168.70.0/24 gateway=192.168.70.1 dns-server=192.168.70.2
```

DHCP-relay пересылает широковещательные запросы клиентов на сервер в другой сети, подставляя адрес интерфейса (giaddr); сервер выбирает пул по этому адресу. В RouterOS: `/ip dhcp-relay add name=relay1 interface=ether7 dhcp-server=10.0.0.5 local-address=<адрес ether7>`. Проверка: `/ip dhcp-server lease print`.

## RIP

RIPv2: обновления каждые 30 с и мгновенные при изменениях, метрика — число хопов (16 = недостижимо), split horizon, удаление маршрута через 180 с без обновлений. В RouterOS v7 сети анонсируются только через `redistribute`. Дистанция 120.

```
/routing rip instance add name=rip1 redistribute=connected
/routing rip interface-template add interfaces=ether1 instance=rip1
```

Проверка: `/ip route print` (маршруты RIP отмечены `r`).

## STP

STP включён на коммутаторах по умолчанию. Корень — коммутатор с наименьшими приоритетом и MAC; лишние связи блокируются (роль Alternate). Порты к ПК и маршрутизаторам работают как edge и пересылают сразу. Бридж CRS326 по умолчанию работает в режиме RSTP.

```switch
/interface bridge set bridge protocol-mode=rstp priority=0x1000
/interface bridge port set ether1 edge=yes
```

Ещё: `path-cost=N` у порта, `protocol-mode=none` (выключить). Проверка: `/interface bridge monitor`, `/interface bridge port print` (колонка ROLE).

## IPv6

IPv6: глобальные адреса и link-local (EUI-64), обнаружение соседей (NDP), ping по IPv6, статические маршруты, объявления маршрутизатора (RA) и автонастройка адресов на ПК (SLAAC). RouterOS пересылает IPv6 и рассылает RA сразу после добавления адреса с префиксом /64.

```
/ipv6 address add address=2001:db8:1::1/64 interface=ether1
/ipv6 route add dst-address=2001:db8:50::/48 gateway=2001:db8:1::2
```

Ещё: `advertise=no` у адреса, `eui-64=yes`, `/ipv6 nd set disabled=yes`. Проверка: `/ipv6 address print`, `/ipv6 route print`, `/ipv6 neighbor print`, `/ping 2001:db8:1::10`.

## Маршрутизация на коммутаторе (CRS)

CRS326 работает на полной RouterOS: IP-адрес назначается самому бриджу (`interface=bridge`) или VLAN-интерфейсу поверх бриджа.

```switch
/interface vlan add interface=bridge vlan-id=10 name=vlan10
/interface vlan add interface=bridge vlan-id=20 name=vlan20
/ip address add address=10.0.10.1/24 interface=vlan10
/ip address add address=10.0.20.1/24 interface=vlan20
```

На CRS доступны также `/ip route`, `/routing ospf`, `/ip dhcp-server` и `/ip firewall`. Проверка: `/ip address print`, `/ip route print`.

## Удалённый доступ (SSH и Telnet)

Устройства принимают удалённый вход по Telnet (TCP 23) и SSH (TCP 22). В режиме симуляции видно рукопожатие TCP; у Telnet в деталях пакета виден даже пароль, у SSH содержимое зашифровано. Подключение открывается прямо в текущей консоли, `exit` возвращает обратно. Из удалённой консоли можно подключаться дальше. В RouterOS службы SSH и Telnet включены сразу, пользователь `admin` без пароля. Задайте пароль и при желании выключите Telnet:

```
/user set admin password=mt123
/user add name=noc password=noc123 group=full
/ip service disable telnet
```

Подключение: `/system ssh address=10.0.0.2 user=admin`, `/system telnet 10.0.0.2`. Проверка: `/ip service print`, `/user print`.

## EtherChannel (агрегирование каналов)

Несколько кабелей между двумя коммутаторами объединяются в один логический канал. STP считает его одним портом и ничего не блокирует, а при обрыве одного кабеля трафик идёт по оставшимся. Канал собирается, когда режимы на концах совместимы: `on` только с `on`; LACP `active` с `active` или `passive`; PAgP `desirable` с `desirable` или `auto`. Иначе порты остаются в состоянии suspended и трафик не передают.

```switch
/interface bonding add name=bond1 slaves=ether1,ether2 mode=802.3ad
```

`mode=802.3ad` — LACP, `balance-rr` — статический канал. Проверка: `/interface bonding print`.

## VRRP (резервный шлюз)

Два маршрутизатора в одной сети делят виртуальный IP-адрес, который ПК используют как шлюз. Активный (HSRP) или master (VRRP) выбирается по приоритету, при равенстве — по большему IP. Он отвечает на ARP виртуальным MAC. Если активный пропадает, резервный забирает роль через hold time (HSRP 10 с, VRRP около 3,6 с) и рассылает gratuitous ARP. По умолчанию HSRP не отбирает роль обратно (нужен `preempt`), а VRRP отбирает.

```
/ip address add address=192.168.1.2/24 interface=ether1
/interface vrrp add interface=ether1 vrid=1 priority=200
/ip address add address=192.168.1.254/32 interface=vrrp1
```

Виртуальный адрес назначается VRRP-интерфейсу. Проверка: `/interface vrrp print` (флаг M — master).

## OSPFv3, RIPng, DHCPv6 и DHCP snooping

```
/ipv6 address add address=2001:db8:61::1/64 interface=ether3
/routing ospf instance add name=v3 version=3 router-id=1.1.1.1
/routing ospf area add name=bb6 area-id=0.0.0.0 instance=v3
/routing ospf interface-template add area=bb6 interfaces=ether3
/ipv6 pool add name=p6 prefix=2001:db8:61::/64 prefix-length=64
/ipv6 dhcp-server add name=d6 interface=ether3 address-pool=p6
```

RIPng: `/routing rip instance add name=rng afi=ipv6` и `/routing rip interface-template add instance=rng interfaces=ether1`. DHCP snooping на CRS: `/interface bridge set bridge dhcp-snooping=yes`, `/interface bridge port set ether24 trusted=yes`. Port security в RouterOS отдельной функции не имеет. Проверка: `/ipv6 route print`, `/ipv6 dhcp-server binding print`.

## GRE и IPsec, Syslog, SNMP, NTP

`/interface gre add` создаёт туннель; с параметром `ipsec-secret` трафик шифруется, и ключи на обоих концах должны совпадать.

```
/interface gre add name=gre1 local-address=203.0.113.2 remote-address=198.51.100.2 ipsec-secret=S3cret!
/ip address add address=10.255.0.1/30 interface=gre1
/system logging action add name=remote1 target=remote remote=192.168.1.100
/snmp set enabled=yes location=Server-room-1
/snmp community add name=private write-access=yes
/system ntp client set enabled=yes servers=192.168.1.100
```

Проверка: `/interface gre print`, `/system ntp client print`, `/snmp community print`, `/log print`.

## Полный список команд: маршрутизатор RB4011

<!-- BEGIN GENERATED COMMANDS router -->
**`/`**

| Команда | Описание |
|---|---|
| `/export` | Print or save an export script |
| `/ping <A.B.C.D> [count=<n>] [size=<bytes>] [ttl=<n>]` | Send ICMP Echo packets |
| `/quit` | Quit console |
| `/traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/interface`**

| Команда | Описание |
|---|---|
| `/interface comment <name> comment=<text>` | Set comment for items |
| `/interface disable <name>` | Disable items |
| `/interface enable <name>` | Enable items |
| `/interface print` | Print values of item properties |
| `/interface set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface bridge`**

| Команда | Описание |
|---|---|
| `/interface bridge add` | Create a new item |
| `/interface bridge print` | Print values of item properties |
| `/interface bridge remove` | Remove item |

**`/interface ethernet`**

| Команда | Описание |
|---|---|
| `/interface ethernet disable <name>` | Disable items |
| `/interface ethernet enable <name>` | Enable items |
| `/interface ethernet print` | Print values of item properties |
| `/interface ethernet set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface gre`**

| Команда | Описание |
|---|---|
| `/interface gre add` | Create a new item |
| `/interface gre print` | Print values of item properties |
| `/interface gre remove` | Remove item |

**`/interface vlan`**

| Команда | Описание |
|---|---|
| `/interface vlan add name=<name> vlan-id=<1-4094> interface=<name>` | Create a new item |
| `/interface vlan print` | Print values of item properties |
| `/interface vlan remove <number>` | Remove item |

**`/interface vrrp`**

| Команда | Описание |
|---|---|
| `/interface vrrp add` | Create a new item |
| `/interface vrrp print` | Print values of item properties |
| `/interface vrrp remove` | Remove item |

**`/ip address`**

| Команда | Описание |
|---|---|
| `/ip address add address=<A.B.C.D/len> interface=<name> [comment=<text>]` | Create a new item |
| `/ip address print` | Print values of item properties |
| `/ip address remove <number>` | Remove item |

**`/ip arp`**

| Команда | Описание |
|---|---|
| `/ip arp print` | Print values of item properties |

**`/ip dhcp-relay`**

| Команда | Описание |
|---|---|
| `/ip dhcp-relay add` | Create a new item |
| `/ip dhcp-relay print` | Print values of item properties |
| `/ip dhcp-relay remove` | Remove item |

**`/ip dhcp-server`**

| Команда | Описание |
|---|---|
| `/ip dhcp-server add` | Create a new item |
| `/ip dhcp-server print` | Print values of item properties |
| `/ip dhcp-server remove` | Remove item |

**`/ip dhcp-server lease`**

| Команда | Описание |
|---|---|
| `/ip dhcp-server lease print` | Print values of item properties |

**`/ip dhcp-server network`**

| Команда | Описание |
|---|---|
| `/ip dhcp-server network add` | Create a new item |
| `/ip dhcp-server network print` | Print values of item properties |
| `/ip dhcp-server network remove` | Remove item |

**`/ip firewall address-list`**

| Команда | Описание |
|---|---|
| `/ip firewall address-list add` | Create a new item |
| `/ip firewall address-list print` | Print values of item properties |
| `/ip firewall address-list remove` | Remove item |

**`/ip firewall connection`**

| Команда | Описание |
|---|---|
| `/ip firewall connection print` | Print values of item properties |

**`/ip firewall filter`**

| Команда | Описание |
|---|---|
| `/ip firewall filter add` | Create a new item |
| `/ip firewall filter print` | Print values of item properties |
| `/ip firewall filter remove` | Remove item |

**`/ip firewall mangle`**

| Команда | Описание |
|---|---|
| `/ip firewall mangle add` | Create a new item |
| `/ip firewall mangle print` | Print values of item properties |
| `/ip firewall mangle remove` | Remove item |

**`/ip firewall nat`**

| Команда | Описание |
|---|---|
| `/ip firewall nat add` | Create a new item |
| `/ip firewall nat print` | Print values of item properties |
| `/ip firewall nat remove` | Remove item |

**`/ip neighbor`**

| Команда | Описание |
|---|---|
| `/ip neighbor print` | Print values of item properties |

**`/ip neighbor discovery-settings`**

| Команда | Описание |
|---|---|
| `/ip neighbor discovery-settings print` | Print values of item properties |
| `/ip neighbor discovery-settings set` | Change item properties |

**`/ip pool`**

| Команда | Описание |
|---|---|
| `/ip pool add` | Create a new item |
| `/ip pool print` | Print values of item properties |
| `/ip pool remove` | Remove item |

**`/ip route`**

| Команда | Описание |
|---|---|
| `/ip route add [dst-address=<A.B.C.D/len>] gateway=<A.B.C.D> [distance=<n>] [comment=<text>]` | Create a new item |
| `/ip route print` | Print values of item properties |
| `/ip route remove <number>` | Remove item |

**`/ip service`**

| Команда | Описание |
|---|---|
| `/ip service disable` | Disable items |
| `/ip service enable` | Enable items |
| `/ip service print` | Print values of item properties |
| `/ip service set` | Change item properties |

**`/ip settings`**

| Команда | Описание |
|---|---|
| `/ip settings print` | Print values of item properties |
| `/ip settings set` | Change item properties |

**`/ipv6 address`**

| Команда | Описание |
|---|---|
| `/ipv6 address add` | Create a new item |
| `/ipv6 address print` | Print values of item properties |
| `/ipv6 address remove` | Remove item |

**`/ipv6 dhcp-server`**

| Команда | Описание |
|---|---|
| `/ipv6 dhcp-server add` | Create a new item |
| `/ipv6 dhcp-server print` | Print values of item properties |

**`/ipv6 dhcp-server binding`**

| Команда | Описание |
|---|---|
| `/ipv6 dhcp-server binding print` | Print values of item properties |

**`/ipv6 nd`**

| Команда | Описание |
|---|---|
| `/ipv6 nd set` | Change item properties |

**`/ipv6 neighbor`**

| Команда | Описание |
|---|---|
| `/ipv6 neighbor print` | Print values of item properties |

**`/ipv6 pool`**

| Команда | Описание |
|---|---|
| `/ipv6 pool add` | Create a new item |
| `/ipv6 pool print` | Print values of item properties |

**`/ipv6 route`**

| Команда | Описание |
|---|---|
| `/ipv6 route add` | Create a new item |
| `/ipv6 route print` | Print values of item properties |

**`/log`**

| Команда | Описание |
|---|---|
| `/log print` | Print values of item properties |

**`/queue simple`**

| Команда | Описание |
|---|---|
| `/queue simple add` | Create a new item |
| `/queue simple print` | Print values of item properties |
| `/queue simple remove` | Remove item |

**`/routing bgp advertisements`**

| Команда | Описание |
|---|---|
| `/routing bgp advertisements print` | Print values of item properties |

**`/routing bgp connection`**

| Команда | Описание |
|---|---|
| `/routing bgp connection add` | Create a new item |
| `/routing bgp connection print` | Print values of item properties |
| `/routing bgp connection remove` | Remove item |

**`/routing bgp session`**

| Команда | Описание |
|---|---|
| `/routing bgp session print` | Print values of item properties |

**`/routing ospf area`**

| Команда | Описание |
|---|---|
| `/routing ospf area add` | Create a new item |
| `/routing ospf area print` | Print values of item properties |
| `/routing ospf area remove` | Remove item |

**`/routing ospf instance`**

| Команда | Описание |
|---|---|
| `/routing ospf instance add` | Create a new item |
| `/routing ospf instance print` | Print values of item properties |
| `/routing ospf instance remove` | Remove item |
| `/routing ospf instance set` | Change item properties |

**`/routing ospf interface`**

| Команда | Описание |
|---|---|
| `/routing ospf interface print` | Print values of item properties |

**`/routing ospf interface-template`**

| Команда | Описание |
|---|---|
| `/routing ospf interface-template add` | Create a new item |
| `/routing ospf interface-template print` | Print values of item properties |
| `/routing ospf interface-template remove` | Remove item |

**`/routing ospf lsa`**

| Команда | Описание |
|---|---|
| `/routing ospf lsa print` | Print values of item properties |

**`/routing ospf neighbor`**

| Команда | Описание |
|---|---|
| `/routing ospf neighbor print` | Print values of item properties |

**`/routing rip instance`**

| Команда | Описание |
|---|---|
| `/routing rip instance add` | Create a new item |
| `/routing rip instance print` | Print values of item properties |
| `/routing rip instance remove` | Remove item |

**`/routing rip interface-template`**

| Команда | Описание |
|---|---|
| `/routing rip interface-template add` | Create a new item |
| `/routing rip interface-template print` | Print values of item properties |
| `/routing rip interface-template remove` | Remove item |

**`/snmp`**

| Команда | Описание |
|---|---|
| `/snmp print` | Print values of item properties |
| `/snmp set` | Change item properties |

**`/snmp community`**

| Команда | Описание |
|---|---|
| `/snmp community add` | Create a new item |
| `/snmp community print` | Print values of item properties |

**`/system`**

| Команда | Описание |
|---|---|
| `/system reboot` | Reboot the system |
| `/system reset-configuration` | Reset configuration |
| `/system ssh` | Open an SSH session |
| `/system telnet` | Open a Telnet session |

**`/system clock`**

| Команда | Описание |
|---|---|
| `/system clock print` | Print values of item properties |

**`/system identity`**

| Команда | Описание |
|---|---|
| `/system identity print` | Print values of item properties |
| `/system identity set name=<name>` | Change item properties |

**`/system logging`**

| Команда | Описание |
|---|---|
| `/system logging print` | Print values of item properties |

**`/system logging action`**

| Команда | Описание |
|---|---|
| `/system logging action add` | Create a new item |
| `/system logging action print` | Print values of item properties |

**`/system ntp client`**

| Команда | Описание |
|---|---|
| `/system ntp client print` | Print values of item properties |
| `/system ntp client set` | Change item properties |

**`/system resource`**

| Команда | Описание |
|---|---|
| `/system resource print` | Print values of item properties |

**`/tool`**

| Команда | Описание |
|---|---|
| `/tool traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/user`**

| Команда | Описание |
|---|---|
| `/user add` | Create a new item |
| `/user print` | Print values of item properties |
| `/user remove` | Remove item |
| `/user set` | Change item properties |
<!-- END GENERATED COMMANDS -->

## Полный список команд: коммутатор CRS326

<!-- BEGIN GENERATED COMMANDS switch -->
**`/`**

| Команда | Описание |
|---|---|
| `/export` | Print or save an export script |
| `/ping <A.B.C.D> [count=<n>] [size=<bytes>] [ttl=<n>]` | Send ICMP Echo packets |
| `/quit` | Quit console |
| `/traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/interface`**

| Команда | Описание |
|---|---|
| `/interface comment <name> comment=<text>` | Set comment for items |
| `/interface disable <name>` | Disable items |
| `/interface enable <name>` | Enable items |
| `/interface print` | Print values of item properties |
| `/interface set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface bonding`**

| Команда | Описание |
|---|---|
| `/interface bonding add` | Create a new item |
| `/interface bonding print` | Print values of item properties |
| `/interface bonding remove` | Remove item |

**`/interface bridge`**

| Команда | Описание |
|---|---|
| `/interface bridge monitor` | Monitor bridge status |
| `/interface bridge print` | Print values of item properties |
| `/interface bridge set bridge vlan-filtering=yes/no` | Change item properties |

**`/interface bridge host`**

| Команда | Описание |
|---|---|
| `/interface bridge host print` | Print values of item properties |

**`/interface bridge port`**

| Команда | Описание |
|---|---|
| `/interface bridge port print` | Print values of item properties |
| `/interface bridge port set <name>/[find interface=<name>] pvid=<1-4094>` | Change item properties |

**`/interface bridge vlan`**

| Команда | Описание |
|---|---|
| `/interface bridge vlan add bridge=bridge vlan-ids=<list> [tagged=<ports>] [untagged=<ports>]` | Create a new item |
| `/interface bridge vlan print` | Print values of item properties |
| `/interface bridge vlan remove <number>` | Remove item |

**`/interface ethernet`**

| Команда | Описание |
|---|---|
| `/interface ethernet disable <name>` | Disable items |
| `/interface ethernet enable <name>` | Enable items |
| `/interface ethernet print` | Print values of item properties |
| `/interface ethernet set <name> [comment=<text>] [disabled=yes/no]` | Change item properties |

**`/interface gre`**

| Команда | Описание |
|---|---|
| `/interface gre add` | Create a new item |
| `/interface gre print` | Print values of item properties |
| `/interface gre remove` | Remove item |

**`/interface vlan`**

| Команда | Описание |
|---|---|
| `/interface vlan add name=<name> vlan-id=<1-4094> interface=<name>` | Create a new item |
| `/interface vlan print` | Print values of item properties |
| `/interface vlan remove <number>` | Remove item |

**`/interface vrrp`**

| Команда | Описание |
|---|---|
| `/interface vrrp add` | Create a new item |
| `/interface vrrp print` | Print values of item properties |
| `/interface vrrp remove` | Remove item |

**`/ip address`**

| Команда | Описание |
|---|---|
| `/ip address add address=<A.B.C.D/len> interface=<name> [comment=<text>]` | Create a new item |
| `/ip address print` | Print values of item properties |
| `/ip address remove <number>` | Remove item |

**`/ip arp`**

| Команда | Описание |
|---|---|
| `/ip arp print` | Print values of item properties |

**`/ip dhcp-relay`**

| Команда | Описание |
|---|---|
| `/ip dhcp-relay add` | Create a new item |
| `/ip dhcp-relay print` | Print values of item properties |
| `/ip dhcp-relay remove` | Remove item |

**`/ip dhcp-server`**

| Команда | Описание |
|---|---|
| `/ip dhcp-server add` | Create a new item |
| `/ip dhcp-server print` | Print values of item properties |
| `/ip dhcp-server remove` | Remove item |

**`/ip dhcp-server lease`**

| Команда | Описание |
|---|---|
| `/ip dhcp-server lease print` | Print values of item properties |

**`/ip dhcp-server network`**

| Команда | Описание |
|---|---|
| `/ip dhcp-server network add` | Create a new item |
| `/ip dhcp-server network print` | Print values of item properties |
| `/ip dhcp-server network remove` | Remove item |

**`/ip firewall address-list`**

| Команда | Описание |
|---|---|
| `/ip firewall address-list add` | Create a new item |
| `/ip firewall address-list print` | Print values of item properties |
| `/ip firewall address-list remove` | Remove item |

**`/ip firewall connection`**

| Команда | Описание |
|---|---|
| `/ip firewall connection print` | Print values of item properties |

**`/ip firewall filter`**

| Команда | Описание |
|---|---|
| `/ip firewall filter add` | Create a new item |
| `/ip firewall filter print` | Print values of item properties |
| `/ip firewall filter remove` | Remove item |

**`/ip firewall mangle`**

| Команда | Описание |
|---|---|
| `/ip firewall mangle add` | Create a new item |
| `/ip firewall mangle print` | Print values of item properties |
| `/ip firewall mangle remove` | Remove item |

**`/ip firewall nat`**

| Команда | Описание |
|---|---|
| `/ip firewall nat add` | Create a new item |
| `/ip firewall nat print` | Print values of item properties |
| `/ip firewall nat remove` | Remove item |

**`/ip neighbor`**

| Команда | Описание |
|---|---|
| `/ip neighbor print` | Print values of item properties |

**`/ip neighbor discovery-settings`**

| Команда | Описание |
|---|---|
| `/ip neighbor discovery-settings print` | Print values of item properties |
| `/ip neighbor discovery-settings set` | Change item properties |

**`/ip pool`**

| Команда | Описание |
|---|---|
| `/ip pool add` | Create a new item |
| `/ip pool print` | Print values of item properties |
| `/ip pool remove` | Remove item |

**`/ip route`**

| Команда | Описание |
|---|---|
| `/ip route add [dst-address=<A.B.C.D/len>] gateway=<A.B.C.D> [distance=<n>] [comment=<text>]` | Create a new item |
| `/ip route print` | Print values of item properties |
| `/ip route remove <number>` | Remove item |

**`/ip service`**

| Команда | Описание |
|---|---|
| `/ip service disable` | Disable items |
| `/ip service enable` | Enable items |
| `/ip service print` | Print values of item properties |
| `/ip service set` | Change item properties |

**`/ip settings`**

| Команда | Описание |
|---|---|
| `/ip settings print` | Print values of item properties |
| `/ip settings set` | Change item properties |

**`/ipv6 address`**

| Команда | Описание |
|---|---|
| `/ipv6 address add` | Create a new item |
| `/ipv6 address print` | Print values of item properties |
| `/ipv6 address remove` | Remove item |

**`/ipv6 dhcp-server`**

| Команда | Описание |
|---|---|
| `/ipv6 dhcp-server add` | Create a new item |
| `/ipv6 dhcp-server print` | Print values of item properties |

**`/ipv6 dhcp-server binding`**

| Команда | Описание |
|---|---|
| `/ipv6 dhcp-server binding print` | Print values of item properties |

**`/ipv6 nd`**

| Команда | Описание |
|---|---|
| `/ipv6 nd set` | Change item properties |

**`/ipv6 neighbor`**

| Команда | Описание |
|---|---|
| `/ipv6 neighbor print` | Print values of item properties |

**`/ipv6 pool`**

| Команда | Описание |
|---|---|
| `/ipv6 pool add` | Create a new item |
| `/ipv6 pool print` | Print values of item properties |

**`/ipv6 route`**

| Команда | Описание |
|---|---|
| `/ipv6 route add` | Create a new item |
| `/ipv6 route print` | Print values of item properties |

**`/log`**

| Команда | Описание |
|---|---|
| `/log print` | Print values of item properties |

**`/queue simple`**

| Команда | Описание |
|---|---|
| `/queue simple add` | Create a new item |
| `/queue simple print` | Print values of item properties |
| `/queue simple remove` | Remove item |

**`/routing bgp advertisements`**

| Команда | Описание |
|---|---|
| `/routing bgp advertisements print` | Print values of item properties |

**`/routing bgp connection`**

| Команда | Описание |
|---|---|
| `/routing bgp connection add` | Create a new item |
| `/routing bgp connection print` | Print values of item properties |
| `/routing bgp connection remove` | Remove item |

**`/routing bgp session`**

| Команда | Описание |
|---|---|
| `/routing bgp session print` | Print values of item properties |

**`/routing ospf area`**

| Команда | Описание |
|---|---|
| `/routing ospf area add` | Create a new item |
| `/routing ospf area print` | Print values of item properties |
| `/routing ospf area remove` | Remove item |

**`/routing ospf instance`**

| Команда | Описание |
|---|---|
| `/routing ospf instance add` | Create a new item |
| `/routing ospf instance print` | Print values of item properties |
| `/routing ospf instance remove` | Remove item |
| `/routing ospf instance set` | Change item properties |

**`/routing ospf interface`**

| Команда | Описание |
|---|---|
| `/routing ospf interface print` | Print values of item properties |

**`/routing ospf interface-template`**

| Команда | Описание |
|---|---|
| `/routing ospf interface-template add` | Create a new item |
| `/routing ospf interface-template print` | Print values of item properties |
| `/routing ospf interface-template remove` | Remove item |

**`/routing ospf lsa`**

| Команда | Описание |
|---|---|
| `/routing ospf lsa print` | Print values of item properties |

**`/routing ospf neighbor`**

| Команда | Описание |
|---|---|
| `/routing ospf neighbor print` | Print values of item properties |

**`/routing rip instance`**

| Команда | Описание |
|---|---|
| `/routing rip instance add` | Create a new item |
| `/routing rip instance print` | Print values of item properties |
| `/routing rip instance remove` | Remove item |

**`/routing rip interface-template`**

| Команда | Описание |
|---|---|
| `/routing rip interface-template add` | Create a new item |
| `/routing rip interface-template print` | Print values of item properties |
| `/routing rip interface-template remove` | Remove item |

**`/snmp`**

| Команда | Описание |
|---|---|
| `/snmp print` | Print values of item properties |
| `/snmp set` | Change item properties |

**`/snmp community`**

| Команда | Описание |
|---|---|
| `/snmp community add` | Create a new item |
| `/snmp community print` | Print values of item properties |

**`/system`**

| Команда | Описание |
|---|---|
| `/system reboot` | Reboot the system |
| `/system reset-configuration` | Reset configuration |
| `/system ssh` | Open an SSH session |
| `/system telnet` | Open a Telnet session |

**`/system clock`**

| Команда | Описание |
|---|---|
| `/system clock print` | Print values of item properties |

**`/system identity`**

| Команда | Описание |
|---|---|
| `/system identity print` | Print values of item properties |
| `/system identity set name=<name>` | Change item properties |

**`/system logging`**

| Команда | Описание |
|---|---|
| `/system logging print` | Print values of item properties |

**`/system logging action`**

| Команда | Описание |
|---|---|
| `/system logging action add` | Create a new item |
| `/system logging action print` | Print values of item properties |

**`/system ntp client`**

| Команда | Описание |
|---|---|
| `/system ntp client print` | Print values of item properties |
| `/system ntp client set` | Change item properties |

**`/system resource`**

| Команда | Описание |
|---|---|
| `/system resource print` | Print values of item properties |

**`/tool`**

| Команда | Описание |
|---|---|
| `/tool traceroute <A.B.C.D> [count=<n>] [max-hops=<n>]` | Traceroute |

**`/user`**

| Команда | Описание |
|---|---|
| `/user add` | Create a new item |
| `/user print` | Print values of item properties |
| `/user remove` | Remove item |
| `/user set` | Change item properties |
<!-- END GENERATED COMMANDS -->
