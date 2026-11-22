# NetEmu

[English version](README.md) · **Русский**

Учебный симулятор компьютерных сетей в духе Cisco Packet Tracer. Устройства разных производителей
настраиваются через их «родной» CLI: Cisco IOS, MikroTik RouterOS, Juniper Junos, Huawei VRP.

Написан на Python + PySide6 (Qt). Сейчас собирается под Windows; код кроссплатформенный
(никакого WinAPI, пути через `pathlib`, иконки рисуются кодом), поэтому Linux/macOS
собираются тем же `netemu.spec` на целевой ОС.

## Возможности

- **Новое в 0.5** (обучение безопасности): DDoS-атаки любой мощности с устройства «Ботнет» — UDP/ICMP/SYN/HTTP-флуд,
  Slowloris, отражённые атаки через DNS/NTP; монитор нагрузки и красные перегруженные кабели показывают, где рвётся
  сеть; защита от DDoS на всех уровнях — ACL, полисинг, uRPF (BCP 38) и null-маршруты у всех вендоров, TCP intercept,
  SYN cookies, лимиты, JS-проверка и лимит соединений на сервере, очистка трафика, RTBH и FlowSpec у провайдера,
  переключение DNS на сервис защиты с учётом TTL ([руководство](docs/ru/ddos.md)); вредоносы — сетевой червь SMB,
  шифровальщик-червь с kill switch, IoT-ботнет в духе Mirai и троян с командным сервером — распространяются
  настоящими пакетами через маршрутизацию, ACL, NAT и IPS; защита на узлах (обновления, антивирус, службы,
  межсетевой экран хоста, резервные копии: вкладка «Безопасность», `netsh advfirewall`, `sc`, `wusa`, `MpCmdRun`,
  `wbadmin`, `ufw`, ClamAV) и в сети (сегментация, IPS Cisco IOS, фильтрация исходящего трафика, DNS sinkhole),
  монитор заражения ([руководство](docs/ru/malware.md)); 11 новых лабораторных (20–30): шесть по DDoS и пять по
  вредоносам; новые проверки `available`, `ddos`, `clean`, `immune`, `outbreak`, `c2blocked`.
- **Новое в 0.4**: EIGRP и IS-IS (в том числе для IPv6), MPLS/LDP, MPLS L3VPN (MP-BGP VPNv4), MPLS TE с RSVP-TE,
  VRF, вторичные IPv4-адреса на интерфейсах; межсетевой экран с отслеживанием состояния (Cisco ZBF,
  зоны Junos SRX), 802.1X с RADIUS, AAA/TACACS+; серверы TFTP и FTP, почта SMTP/POP3 с почтовым клиентом,
  DHCP option 82; новые устройства — Cisco ASA 5506-X, FortiGate 60F, контроллер Cisco WLC 2504 с лёгкими
  точками доступа, датчики и исполнители IoT с IoT-сервером; 19 готовых лабораторных (новые: BGP, EtherChannel,
  port security, VPN, IP-телефония, IPv6/OSPFv3, экзамен); режим класса (преподаватель раздаёт задание по
  локальной сети и видит проверки учеников вживую); подсказки, почему проверка не прошла; анализатор пакетов
  с фильтрами и экспортом в `.pcap` для Wireshark; физический вид (здание / этаж / стойка, тип и длина кабеля,
  медь до 100 м); подсветка синтаксиса в терминале, подсказка следующих слов и поиск команд (`Ctrl+F`);
  проверка обновлений; проверено на Python 3.11–3.15; закрыты пробелы 0.3 у вендоров (QoS и voice VLAN у всех,
  IPsec на всех маршрутизаторах, NAT64 для TCP/UDP).
- **Новое в 0.3**: 12 готовых лабораторных с автопроверкой (VLAN, маршрутизация между VLAN, STP, OSPF, DHCP,
  NAT, ACL, HSRP, IPv6, Wi-Fi и две задачи на поиск неисправностей); режим преподавателя (решение и
  сценарий в файле зашифрованы паролем, «Применить решение», случайная неисправность, HTML-отчёт ученика);
  туннели GRE и IPsec через облако «Интернет»; QoS (классы, DSCP, полисинг); NAT64; OSPFv3 с базой LSA и
  зонами; Syslog, SNMP v2c и NTP; IP-телефоны с voice VLAN, Cisco CME и RTP; `interface range` в Cisco IOS;
  тёмная тема и масштаб интерфейса до 200 %; совместная работа над схемой по локальной сети; установщик
  `setup.exe`.
- **Новое в 0.2**: облако «Интернет» с публичными DNS и сайтами; EtherChannel/LACP; HSRP и VRRP;
  OSPFv3, RIPng и DHCPv6; port security и DHCP snooping; Wi-Fi (точка доступа, ноутбуки, WPA2);
  задания с автопроверкой (`examples/lab-ospf.netemu`); отмена и повтор на схеме; заметки и рамки;
  экспорт схемы в PNG и SVG; иконка приложения.
- **Холст**: устройства перетаскиваются из палитры; соединение кабелем с выбором порта;
  удаление; масштаб (колесо мыши), панорама (средняя кнопка). Индикаторы портов на кабелях:
  зелёный — линк поднят, красный — нет.
- **Устройства**:

  | Тип | Модели |
  |---|---|
  | Маршрутизаторы | Cisco 2911, MikroTik RB4011, Juniper SRX300, Huawei AR2220 |
  | Коммутаторы (L2 и L3) | Cisco 2960-24TT, MikroTik CRS326, Juniper EX2300, Huawei S5720, Extreme X440-G2 |
  | Конечные устройства | ПК и сервер (Windows), PC (Linux), Server (Linux), IP Phone 7960 |
  | Прочее | Хаб |

- **Сеть**: Ethernet, VLAN 802.1Q (access/trunk, native VLAN, router-on-a-stick), STP (PVST у
  Cisco, RSTP/MSTP у остальных: выбор корня, блокировка петель, edge-порты), OSPFv2
  (DR/BDR, несколько зон, `O IA`/`O E2`, ECMP, маршрут по умолчанию, перераспределение), BGP-4
  (eBGP/iBGP, loopback-пиринг, next-hop-self, route reflector, выбор лучшего пути), RIPv2, ACL и
  firewall-фильтры, NAT (статический, пул, PAT, проброс портов), loopback-интерфейсы,
  DHCP (сервер на маршрутизаторах и серверах, relay, APIPA), DNS, веб-сервер и браузер (HTTP поверх TCP),
  IPv6 (глобальные и link-local адреса, NDP, ping, traceroute, статика, RA и SLAAC),
  маршрутизация на коммутаторах (SVI, Vlanif, irb, VLAN-интерфейсы RouterOS, IP на VLAN в EXOS),
  удалённый вход между устройствами по Telnet и SSH (пароли линий, локальные пользователи, ключи RSA),
  туннели GRE и IPsec, QoS (MQC у Cisco, traffic policy у Huawei), NAT64, OSPFv3 (LSA, зоны, ABR),
  Syslog, SNMP v2c (агент на устройствах, `snmpget`/`snmpwalk`/`snmpset` на Linux), NTP,
  IP-телефония (voice VLAN, DHCP option 150, Cisco CME, SCCP, RTP с DSCP EF),
  коммутация с изучением MAC и старением записей (300 с), ARP
  (кэш, очередь пакетов до разрешения, proxy-ARP на маршрутизаторах), IPv4, connected- и
  статическая маршрутизация (longest prefix match, рекурсивный next hop, административная
  дистанция), TTL, ICMP echo, destination unreachable, time exceeded, ping и traceroute.
- **CLI** (сокращения команд, `?`, Tab, история, Ctrl+C, Ctrl+Z, вставка многострочного текста):
  - **Cisco IOS**: `enable`, `conf t`, `interface`, `ip address`, `[no] shutdown`, `description`,
    `ip route`, `hostname`, `do …`, `show ip route|ip interface brief|arp|interfaces|running-config|
    startup-config|version|mac address-table|interfaces status`, `copy run start`, `write`,
    `reload`, `erase startup-config`, `ping`, `traceroute`, `clear arp-cache|mac address-table`.
  - **Huawei VRP**: `system-view`, `sysname`, `interface`, `ip address IP MASK|LEN`, `undo …`,
    `ip route-static … [preference N]`, `display ip routing-table|ip interface brief|interface brief|
    arp|mac-address|current-configuration|saved-configuration|version`, `save`, `ping [-c N]`, `tracert`.
  - **Juniper Junos**: операционный режим и режим конфигурации с candidate-конфигом:
    `set|delete interfaces … unit 0 family inet address A/L`, `… disable`, `… description`,
    `set system host-name`, `set routing-options static route … next-hop …` и `… qualified-next-hop … preference N` (плавающие маршруты),
    `commit [check|and-quit]`, `rollback`, `show`, `run …`;
    `show route|interfaces terse|arp|configuration|ethernet-switching table`, `ping … count N`, `traceroute`.
  - **Extreme ExtremeXOS**: `create vlan`, `configure vlan … add ports … tagged|untagged`,
    `configure vlan … ipaddress`, `enable ipforwarding`, `configure iproute`, OSPF, `stpd s0`, `show vlan|iproute|fdb|iparp`.
  - **MikroTik RouterOS** (v7): навигация по меню (`/ip address`, `..`), `print`, `add`, `remove N`,
    `/ip address|route|arp`, `/interface enable|disable|set … comment=`,
    `/interface bridge host print`, `/system identity`, `/export`, `/ping … count=N`,
    `/tool traceroute`, `/system reset-configuration`.
  - **ПК / сервер на Windows**: `ipconfig [/all|/renew|/release|/displaydns|/flushdns]`,
    `ipconfig IP MASK [GW]`, `netsh interface ipv4|ipv6 …` (адрес, DNS, маршруты, включение порта),
    `route print|add|delete|change [-p]`, `ping [-n] [-t] [-l] [-i] [-w] [-4|-6]`, `tracert [-d] [-h] [-w]`,
    `pathping`, `nslookup [сервер]`, `arp -a|-d|-s`, `getmac [/v]`, `netstat -an|-r|-e`, `curl [-I]`,
    `hostname`, `ver`, `whoami`, `echo`, `date /t`, `time /t`, `cls`.
  - **ПК / сервер на Linux** (оболочка Ubuntu, изменения только через `sudo` или `sudo -i`):
    `ip [-4|-6] [-br] addr|link|route|neigh` (`add`, `del`, `flush`, `set … up|down`, `route get`),
    `ifconfig`, `route -n|add|del`, `arp -n|-a|-d|-s`, `ping [-c] [-s] [-t] [-i] [-W]`, `ping6`,
    `traceroute [-n] [-m]`, `tracepath`, `dig [@сервер] [+short]`, `host`, `nslookup`, `curl [-I]`, `wget`,
    `dhclient [-v] [-r]`, `resolvectl`, `cat /etc/resolv.conf`, `echo nameserver … | sudo tee /etc/resolv.conf`,
    `ss -tuln`, `netstat -tuln|-rn|-i`, `sysctl`, `ethtool`, `hostname`, `hostnamectl`, `uname -a`, `id`.
- **Подпись на схеме** повторяет hostname, заданный в любом CLI (`hostname`, `sysname`, `/system identity`, `host-name` после `commit`) или в форме; имя, введённое вручную в поле «Имя на схеме», имеет приоритет.
- **Формы настройки** (вкладка «Настройка»): IP/маска/шлюз ПК, интерфейсы и статические маршруты
  маршрутизаторов, имена. Изменения из CLI сразу видны в форме, и наоборот.
- **Режим симуляции**: время останавливается; кнопки «Шаг» и «Воспроизвести» с регулировкой
  скорости; анимация конвертов на кабелях (у каждого протокола свой цвет: ARP, STP, ICMP, ICMPv6, NDP,
  RIP, OSPF, BGP, DHCP, DNS, HTTP, TCP); список событий;
  содержимое PDU по уровням (щелчок по конверту или двойной щелчок по строке); фильтр протоколов.
- **Простой PDU**: инструмент-конверт, щелчок по источнику, затем по получателю — отправляется ping,
  результат показывается в строке состояния.
- **Проекты**: сохранение и открытие `*.netemu` (JSON). Конфигурация хранится в нейтральном к
  вендору виде; есть демо-топология (меню «Файл → Открыть демо-топологию» или
  `examples/multivendor-demo.netemu`); большая лаборатория с резервированием — `examples/complex-lab.netemu`; учебные лаборатории
  `vlan-lab`, `ospf-lab`, `bgp-lab`, `nat-lab`, `services-lab` в папке `examples`.

- **Справка** в программе (**Справка → Руководство и команды**, `F1`; в окне устройства `F1`
  открывает раздел его вендора): руководство пользователя и полный список команд каждого вендора.
  Те же страницы лежат в папке [`docs`](docs/README.md) на русском и английском.
- **Язык интерфейса**: русский или английский (**Вид → Язык / Language**, после перезапуска;
  при первом запуске выбирается по языку системы; `python -m netemu --lang en`).

## Установка

Скачайте пакет для своей системы из релиза или артефактов GitHub Actions.

- **Windows 10/11**:
  - Запустите `NetEmu-<версия>-setup.exe`.
  - Нужные библиотеки входят в программу. Если в системе нет Microsoft Visual C++ 2015–2022
    Redistributable, установщик сообщит об этом, скачает компонент с сайта Microsoft и установит его
    (шаг можно пропустить).
  - Переносной `NetEmu-<версия>-windows-x64.zip` работает без установки.
- **Linux (x86_64)**:
  - Распакуйте `NetEmu-<версия>-linux-x86_64.tar.gz` и запустите `./NetEmu/install.sh`. Он установит
    программу для текущего пользователя, добавит её в меню приложений и проверит библиотеки X11/EGL,
    нужные Qt.
  - Недостающие библиотеки будут перечислены и установлены через пакетный менеджер (apt, dnf, zypper или
    pacman) после вашего подтверждения и ввода пароля администратора.
  - Та же проверка выполняется при каждом запуске `netemu.sh`.
  - `NETEMU_ASSUME_YES=1` ставит пакеты без вопросов, `NETEMU_SKIP_DEPS=1` отключает проверку.
- **macOS**:
  - Распакуйте `NetEmu-<версия>-macos.zip` и перенесите `NetEmu.app` в «Программы».
  - Приложение не нотаризовано, поэтому при первом запуске щёлкните по нему правой кнопкой и выберите
    **Открыть**.

## Запуск из исходников

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m netemu                                     # пустой проект
.venv\Scripts\python -m netemu examples\multivendor-demo.netemu    # демо
```

## Тесты

```powershell
.venv\Scripts\python -m pytest
```

332 теста: ядро (коммутация, VLAN, STP, ARP, маршрутизация, OSPF/OSPFv3, BGP, RIP, NAT/NAT64/ACL, QoS, GRE/IPsec,
DHCP/DNS/HTTP, Syslog/SNMP/NTP, VoIP, IPv6, сохранение), CLI всех вендоров, решаемость всех готовых лабораторных,
режим преподавателя, совместная работа и GUI (pytest-qt; по умолчанию без окна, `QT_QPA_PLATFORM=offscreen`).
Готовые лабораторные пересобираются и перепроверяются командой `python tools/make_labs.py`.

## Сборка и установщик

```powershell
.venv\Scripts\python tools\build.py
```

Скрипт перерисовывает иконку и справочники команд, собирает программу PyInstaller-ом и упаковывает её в `release\`:

| ОС | Результат |
|---|---|
| Windows | `NetEmu-<версия>-windows-x64.zip` (переносная папка) и `NetEmu-<версия>-setup.exe`, если установлен [Inno Setup 6](https://jrsoftware.org/isinfo.php) (сценарий `installer\netemu.iss`: ярлыки, удаление, открытие файлов `.netemu`) |
| Linux | `NetEmu-<версия>-linux-x86_64.tar.gz` |
| macOS | `NetEmu-<версия>-macos.zip` с `NetEmu.app` |

Собирать нужно на целевой ОС. GitHub Actions (`.github/workflows/build.yml`) на каждый push и pull request прогоняет все тесты на Windows, Ubuntu и macOS (Python 3.11 и 3.13), а по тегу `v*` или ручному запуску workflow **build** собирает архивы для трёх систем и установщик. Только программа без упаковки: `pyinstaller --noconfirm netemu.spec` → `dist\NetEmu\NetEmu.exe`.

## Устройство кода

```
netemu/core/          ядро без зависимостей от Qt
  engine.py           дискретно-событийный движок (виртуальное время, мс)
  port.py             порты и кабели
  devices/            хаб, коммутатор, ПК/сервер, маршрутизатор, каталог моделей вендоров
  stack/              ARP, IPv4 + ICMP, сессии ping/traceroute
  cli/                парсер дерева команд и диалекты: cisco, huawei, juniper, mikrotik, host
  routing/            OSPF, OSPFv3 (LSDB), RIP/RIPng, BGP, HSRP/VRRP
  services/           DHCP, DHCPv6, DNS, HTTP, Telnet/SSH, Syslog/SNMP/NTP (mgmt.py)
  security/           ACL, NAT, NAT64, QoS
  tunnel.py           GRE и IPsec
  lab.py              задания, проверки, режим преподавателя, неисправности, отчёт
  collab.py           совместная работа: снимки схемы и операции синхронизации
  topology.py, persistence.py, samples.py
netemu/gui/           PySide6: главное окно, холст, терминал, окна устройств, панель симуляции, справка,
                      задание и режим преподавателя, телефон, тема и масштаб, совместная работа (QtNetwork)
netemu/resources/labs готовые лабораторные (собирает и проверяет tools/make_labs.py)
netemu/i18n.py        перевод интерфейса (русский → английский)
docs/ru, docs/en      документация; таблицы команд генерирует tools/gen_docs.py из CLI
tests/                pytest + pytest-qt
```

Новый вендор добавляется так: модель в `core/devices/__init__.py` (имена портов, умолчания) и
модуль диалекта в `core/cli/`, подключённый в `core/cli/__init__.py`. Затем `python tools/gen_docs.py`
обновит списки команд в документации (тест `tests/test_docs.py` следит, чтобы они не устаревали).

## Лицензия

NetEmu — свободное программное обеспечение. Его можно распространять и изменять на условиях
[GNU General Public License](LICENSE) версии 3 или (по вашему выбору) любой более поздней версии,
опубликованной Free Software Foundation. Программа распространяется БЕЗ КАКИХ-ЛИБО ГАРАНТИЙ.

## Ограничения текущей версии

HSRP — только у Cisco (VRRP — у всех); port security нет в RouterOS (как и на настоящих MikroTik), DHCPv6-сервера
нет в EXOS. HSRP и CME — технологии Cisco и есть только у Cisco, как на настоящем оборудовании. MPLS L3VPN и
RSVP-TE настраиваются на Cisco; сигнализация RSVP рассчитывается по хопам, а не передаётся пакетами.
RIPng считает маршруты по соседям, найденным hello-пакетами (OSPFv3 работает с LSA и зонами).
DDoS-атака моделируется агрегированным потоком (раз в секунду моделирования пакет-проба несёт её интенсивность),
производительность маршрутизаторов в пакетах/с не моделируется.
Вредоносы — учебная модель (четыре семейства, одна уязвимость); черви сканируют только адреса, которые есть в топологии.
При совместной работе каждый участник запускает симуляцию у себя: общими являются схема и конфигурации.
Linux- и macOS-сборки проверяются в CI (GitHub Actions), на этих системах вручную приложение не тестировалось.
На каждом интерфейсе один IPv4-адрес. STP считается централизованно по домену коммутаторов (результат
тот же, что у настоящего протокола, но без таймеров max-age); edge-портами автоматически считаются
порты к ПК и маршрутизаторам. Junos в эмуляторе не рассылает RA, поэтому ПК за SRX настраиваются статически.
Ping в Junos и RouterOS по умолчанию отправляет конечное число пакетов (5 и 4), а не
бесконечный поток. Отката на шаг назад в режиме симуляции нет: есть «Сброс».
