"""Build the ready-made labs shipped in netemu/resources/labs/*.netemu.

Every lab is verified while it is built: before the solution at least one check fails,
after running the teacher's script every check passes.  Troubleshooting labs start from a
working network, then the planted faults are applied; their script is the fix.

    python tools/make_labs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from netemu.core import persistence  # noqa: E402
from netemu.core.lab import Lab, apply_script, evaluate, parse_checks  # noqa: E402
from netemu.core.topology import Topology  # noqa: E402

OUT = ROOT / "netemu" / "resources" / "labs"
LABS = []


def lab(fn):
    LABS.append(fn)
    return fn


def host(dev, ip=None, gw=None):
    if ip:
        a, _, plen = ip.partition("/")
        from netemu.core.addresses import prefix_to_mask
        dev.set_ip(a, prefix_to_mask(int(plen or 24)))
    if gw:
        dev.set_gateway(gw)


def cli(dev, text):
    s = dev.create_cli()
    for line in text.strip().splitlines():
        s.execute(line, lambda x: None)


# ------------------------------------------------------------------------------------ 1. VLAN
@lab
def vlan():
    t = Topology()
    sw = t.add_device("cisco-2960", "SW1", 400, 200)
    pcs = [t.add_device("pc", f"PC{i}", 160 + 160 * (i - 1), 400) for i in range(1, 5)]
    for i, pc in enumerate(pcs, 1):
        t.connect(pc.nic, sw.get_port(f"Fa0/{i}"))
        host(pc, f"192.168.1.{10 + i}/24")
    text = """Отдел продаж (PC1, PC2) и отдел кадров (PC3, PC4) подключены к одному коммутатору и пока видят друг друга.

1. Создайте на **SW1** VLAN 10 с именем `SALES` и VLAN 20 с именем `HR`.
2. Порты Fa0/1–Fa0/2 переведите в режим access во VLAN 10, порты Fa0/3–Fa0/4 во VLAN 20.
3. Убедитесь, что PC1 видит PC2, а PC3 из другого отдела больше не отвечает.

Адреса ПК уже заданы: 192.168.1.11–14/24."""
    checks = """vlan SW1 10 | На SW1 есть VLAN 10
vlan SW1 20 | На SW1 есть VLAN 20
access SW1 Fa0/1 10 | Fa0/1 во VLAN 10
access SW1 Fa0/2 10 | Fa0/2 во VLAN 10
access SW1 Fa0/3 20 | Fa0/3 во VLAN 20
access SW1 Fa0/4 20 | Fa0/4 во VLAN 20
ping PC1 192.168.1.12 | PC1 видит PC2
ping PC3 192.168.1.14 | PC3 видит PC4
noping PC1 192.168.1.13 | PC1 не видит PC3 (другой VLAN)"""
    script = {"SW1": """enable
configure terminal
vlan 10
name SALES
vlan 20
name HR
exit
interface range fa0/1 - 2
switchport mode access
switchport access vlan 10
exit
interface range fa0/3 - 4
switchport mode access
switchport access vlan 20
end"""}
    return t, "01-vlan", "VLAN: разделение отделов", text, checks, script, "Каждому отделу свой VLAN, порты в режиме access."


# ------------------------------------------------------------------------------------ 2. inter-VLAN
@lab
def intervlan():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 80)
    sw = t.add_device("cisco-2960", "SW1", 400, 240)
    p1, p2 = t.add_device("pc", "PC1", 250, 400), t.add_device("pc", "PC2", 550, 400)
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    t.connect(p1.nic, sw.get_port("Fa0/1"))
    t.connect(p2.nic, sw.get_port("Fa0/2"))
    host(p1, "192.168.10.10/24", "192.168.10.1")
    host(p2, "192.168.20.10/24", "192.168.20.1")
    text = """PC1 работает во VLAN 10 (192.168.10.0/24), PC2 во VLAN 20 (192.168.20.0/24). Настройте маршрутизацию между VLAN по схеме «маршрутизатор на палочке» (router-on-a-stick).

1. На **SW1** создайте VLAN 10 и 20, переведите Fa0/1 во VLAN 10, Fa0/2 во VLAN 20, а Fa0/24 сделайте транком.
2. На **R1** включите Gi0/0 и создайте подынтерфейсы Gi0/0.10 и Gi0/0.20 с инкапсуляцией dot1Q и адресами .1 в каждой сети.
3. Проверьте ping с PC1 на PC2."""
    checks = """vlan SW1 10 | На SW1 есть VLAN 10
vlan SW1 20 | На SW1 есть VLAN 20
ip R1 192.168.10.1 | Шлюз VLAN 10 на R1
ip R1 192.168.20.1 | Шлюз VLAN 20 на R1
ping PC1 192.168.10.1 | PC1 видит свой шлюз
ping PC1 192.168.20.10 | PC1 видит PC2 через R1"""
    script = {"SW1": """enable
configure terminal
vlan 10
vlan 20
exit
interface fa0/1
switchport mode access
switchport access vlan 10
interface fa0/2
switchport mode access
switchport access vlan 20
interface fa0/24
switchport mode trunk
end""", "R1": """enable
configure terminal
interface gi0/0
no shutdown
interface gi0/0.10
encapsulation dot1Q 10
ip address 192.168.10.1 255.255.255.0
interface gi0/0.20
encapsulation dot1Q 20
ip address 192.168.20.1 255.255.255.0
end"""}
    return t, "02-inter-vlan", "Маршрутизация между VLAN", text, checks, script, "Транк до маршрутизатора и подынтерфейсы dot1Q."


# ------------------------------------------------------------------------------------ 3. STP
@lab
def stp():
    t = Topology()
    s1 = t.add_device("cisco-2960", "SW1", 400, 100)
    s2 = t.add_device("cisco-2960", "SW2", 220, 300)
    s3 = t.add_device("cisco-2960", "SW3", 580, 300)
    t.connect(s1.get_port("Fa0/1"), s2.get_port("Fa0/1"))
    t.connect(s1.get_port("Fa0/2"), s3.get_port("Fa0/1"))
    t.connect(s2.get_port("Fa0/2"), s3.get_port("Fa0/2"))
    p1, p2 = t.add_device("pc", "PC1", 120, 460), t.add_device("pc", "PC2", 680, 460)
    t.connect(p1.nic, s2.get_port("Fa0/10"))
    t.connect(p2.nic, s3.get_port("Fa0/10"))
    host(p1, "192.168.1.10/24")
    host(p2, "192.168.1.20/24")
    cli(s2, "enable\nconfigure terminal\nspanning-tree vlan 1 priority 28672\nend")
    text = """Три коммутатора соединены кольцом. Корневым мостом STP сейчас выбран **SW2**, хотя ядро сети это **SW1**.

1. Посмотрите `show spanning-tree` на коммутаторах: какой мост корневой, какой порт заблокирован?
2. Сделайте SW1 корневым мостом для VLAN 1 (приоритет 4096 или `spanning-tree vlan 1 root primary`).
3. Убедитесь, что связь между PC1 и PC2 сохранилась."""
    checks = """stproot SW1 | SW1 корневой мост
ping PC1 192.168.1.20 | PC1 видит PC2"""
    script = {"SW1": "enable\nconfigure terminal\nspanning-tree vlan 1 priority 4096\nend"}
    return t, "03-stp", "STP: выбор корневого моста", text, checks, script, "Приоритет SW1 должен быть ниже 28672."


# ------------------------------------------------------------------------------------ 4. OSPF
@lab
def ospf():
    t = Topology()
    rs = [t.add_device("cisco-2911", f"R{i}", 150 + 250 * (i - 1), 150) for i in range(1, 4)]
    p1, p3 = t.add_device("pc", "PC1", 150, 350), t.add_device("pc", "PC3", 650, 350)
    t.connect(rs[0].get_port("gi0/1"), rs[1].get_port("gi0/1"))
    t.connect(rs[1].get_port("gi0/2"), rs[2].get_port("gi0/1"))
    t.connect(p1.nic, rs[0].get_port("gi0/0"))
    t.connect(p3.nic, rs[2].get_port("gi0/0"))
    host(p1, "192.168.1.10/24", "192.168.1.1")
    host(p3, "192.168.3.10/24", "192.168.3.1")
    cli(rs[0], "enable\nconfigure terminal\nhostname R1\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\nno shutdown\n"
               "interface gi0/1\nip address 10.0.12.1 255.255.255.252\nno shutdown\nend")
    cli(rs[1], "enable\nconfigure terminal\nhostname R2\ninterface gi0/1\nip address 10.0.12.2 255.255.255.252\nno shutdown\n"
               "interface gi0/2\nip address 10.0.23.1 255.255.255.252\nno shutdown\nend")
    cli(rs[2], "enable\nconfigure terminal\nhostname R3\ninterface gi0/0\nip address 192.168.3.1 255.255.255.0\nno shutdown\n"
               "interface gi0/1\nip address 10.0.23.2 255.255.255.252\nno shutdown\nend")
    text = """Адреса на интерфейсах уже настроены. Сети PC1 (192.168.1.0/24) и PC3 (192.168.3.0/24) пока не знают друг о друге.

1. Включите OSPF (процесс 1, зона 0) на R1, R2 и R3 для всех их сетей.
2. Проверьте соседство: `show ip ospf neighbor`, у R2 должно быть два соседа.
3. Убедитесь, что маршрут к 192.168.3.0/24 появился на R1 с кодом O, и PC1 видит PC3."""
    checks = """ospf R2 2 | У R2 два соседа OSPF
route R1 192.168.3.0/24 | R1 знает сеть PC3
route R3 192.168.1.0/24 | R3 знает сеть PC1
ping PC1 192.168.3.10 | PC1 видит PC3"""
    net = "router ospf 1\nnetwork 192.168.0.0 0.0.255.255 area 0\nnetwork 10.0.0.0 0.255.255.255 area 0\nend"
    script = {f"R{i}": "enable\nconfigure terminal\n" + net for i in (1, 2, 3)}
    return t, "04-ospf", "OSPF в одной зоне", text, checks, script, "Достаточно двух команд network на каждом маршрутизаторе."


# ------------------------------------------------------------------------------------ 5. DHCP
@lab
def dhcp():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 80)
    sw = t.add_device("cisco-2960", "SW1", 400, 230)
    pcs = [t.add_device("pc", f"PC{i}", 250 + 300 * (i - 1), 400) for i in (1, 2)]
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    for i, pc in enumerate(pcs, 1):
        t.connect(pc.nic, sw.get_port(f"Fa0/{i}"))
    cli(r, "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\nno shutdown\nend")
    text = """Компьютеры должны получать адреса автоматически.

1. На **R1** исключите из раздачи адреса 192.168.1.1–192.168.1.10.
2. Создайте пул `LAN`: сеть 192.168.1.0/24, шлюз 192.168.1.1, DNS 8.8.8.8.
3. На PC1 и PC2 включите DHCP (вкладка «Настройка» или `ipconfig /renew`)."""
    checks = """dhcp PC1 | PC1 получил адрес по DHCP
dhcp PC2 | PC2 получил адрес по DHCP
ping PC1 192.168.1.1 | PC1 видит шлюз"""
    script = {"R1": """enable
configure terminal
ip dhcp excluded-address 192.168.1.1 192.168.1.10
ip dhcp pool LAN
network 192.168.1.0 255.255.255.0
default-router 192.168.1.1
dns-server 8.8.8.8
end""", "PC1": "@dhcp", "PC2": "@dhcp"}
    return t, "05-dhcp", "DHCP-сервер на маршрутизаторе", text, checks, script, ""


# ------------------------------------------------------------------------------------ 6. NAT
@lab
def nat():
    t = Topology()
    inet = t.add_device("internet", "Internet", 650, 120)
    r = t.add_device("cisco-2911", "R1", 400, 120)
    pc = t.add_device("pc", "PC1", 150, 120)
    t.connect(r.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(pc.nic, r.get_port("gi0/0"))
    host(pc, "192.168.1.10/24", "192.168.1.1")
    cli(r, "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\nno shutdown\nend")
    text = """Провайдер выделил офису адрес 203.0.113.2/30, шлюз провайдера 203.0.113.1. Нужно выпустить сеть 192.168.1.0/24 в интернет.

1. Настройте на **R1** интерфейс Gi0/1 (адрес провайдера) и маршрут по умолчанию.
2. Настройте PAT (NAT overload): Gi0/0 inside, Gi0/1 outside, список доступа для 192.168.1.0/24.
3. Проверьте `ping 8.8.8.8` с PC1 и `show ip nat translations` на R1."""
    checks = """ping R1 8.8.8.8 | R1 выходит в интернет
ping PC1 8.8.8.8 | PC1 выходит в интернет
nat R1 | На R1 есть трансляции NAT"""
    script = {"R1": """enable
configure terminal
interface gi0/1
ip address 203.0.113.2 255.255.255.252
no shutdown
ip nat outside
interface gi0/0
ip nat inside
exit
ip route 0.0.0.0 0.0.0.0 203.0.113.1
access-list 1 permit 192.168.1.0 0.0.0.255
ip nat inside source list 1 interface gi0/1 overload
end"""}
    return t, "06-nat", "NAT/PAT и выход в интернет", text, checks, script, ""


# ------------------------------------------------------------------------------------ 7. ACL
@lab
def acl():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 150)
    sw = t.add_device("cisco-2960", "SW1", 200, 300)
    srv = t.add_device("server", "Server", 650, 150)
    p1, p2 = t.add_device("pc", "PC1", 100, 450), t.add_device("pc", "PC2", 300, 450)
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    t.connect(p1.nic, sw.get_port("Fa0/1"))
    t.connect(p2.nic, sw.get_port("Fa0/2"))
    t.connect(srv.nic, r.get_port("gi0/1"))
    host(p1, "192.168.1.10/24", "192.168.1.1")
    host(p2, "192.168.1.20/24", "192.168.1.1")
    host(srv, "192.168.2.10/24", "192.168.2.1")
    cli(r, "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\nno shutdown\n"
           "interface gi0/1\nip address 192.168.2.1 255.255.255.0\nno shutdown\nend")
    text = """Сервер бухгалтерии (192.168.2.10) должен быть доступен только с PC2.

1. Создайте на **R1** список доступа, который запрещает PC1 (192.168.1.10) обращаться к серверу и разрешает остальное.
2. Примените его на подходящем интерфейсе и в нужном направлении.
3. Проверьте: PC2 видит сервер, PC1 нет, а шлюз 192.168.1.1 по-прежнему доступен с PC1."""
    checks = """noping PC1 192.168.2.10 | PC1 не видит сервер
ping PC2 192.168.2.10 | PC2 видит сервер
ping PC1 192.168.1.1 | PC1 видит шлюз"""
    script = {"R1": """enable
configure terminal
ip access-list extended BLOCK-PC1
deny ip host 192.168.1.10 host 192.168.2.10
permit ip any any
exit
interface gi0/0
ip access-group BLOCK-PC1 in
end"""}
    return t, "07-acl", "Списки доступа (ACL)", text, checks, script, "Расширенный ACL ставят ближе к источнику."


# ------------------------------------------------------------------------------------ 8. HSRP
@lab
def hsrp():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911", "R1", 250, 150), t.add_device("cisco-2911", "R2", 550, 150)
    sw1 = t.add_device("cisco-2960", "SW1", 400, 300)
    sw2 = t.add_device("cisco-2960", "SW2", 400, 30)
    pc, srv = t.add_device("pc", "PC1", 400, 450), t.add_device("server", "Server", 650, 30)
    t.connect(r1.get_port("gi0/0"), sw1.get_port("Fa0/1"))
    t.connect(r2.get_port("gi0/0"), sw1.get_port("Fa0/2"))
    t.connect(pc.nic, sw1.get_port("Fa0/10"))
    t.connect(r1.get_port("gi0/1"), sw2.get_port("Fa0/1"))
    t.connect(r2.get_port("gi0/1"), sw2.get_port("Fa0/2"))
    t.connect(srv.nic, sw2.get_port("Fa0/10"))
    host(pc, "192.168.1.10/24", "192.168.1.254")
    host(srv, "10.0.0.10/24", "10.0.0.1")
    cli(r1, "enable\nconfigure terminal\nhostname R1\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\nno shutdown\n"
            "interface gi0/1\nip address 10.0.0.1 255.255.255.0\nno shutdown\nend")
    cli(r2, "enable\nconfigure terminal\nhostname R2\ninterface gi0/0\nip address 192.168.1.2 255.255.255.0\nno shutdown\n"
            "interface gi0/1\nip address 10.0.0.2 255.255.255.0\nno shutdown\nend")
    text = """У PC1 шлюзом указан виртуальный адрес 192.168.1.254, которого пока ни у кого нет.

1. Настройте HSRP группу 1 с виртуальным адресом 192.168.1.254 на Gi0/0 маршрутизаторов R1 и R2.
2. R1 должен быть активным (приоритет 110, preempt), R2 резервным.
3. Проверьте `show standby brief` и доступность сервера с PC1. Попробуйте выключить Gi0/0 на R1."""
    checks = """fhrp R1 active | R1 активный маршрутизатор HSRP
fhrp R2 standby | R2 резервный маршрутизатор HSRP
ping PC1 192.168.1.254 | Виртуальный шлюз отвечает
ping PC1 10.0.0.10 | PC1 видит сервер"""
    script = {"R1": "enable\nconfigure terminal\ninterface gi0/0\nstandby 1 ip 192.168.1.254\nstandby 1 priority 110\n"
                    "standby 1 preempt\nend",
              "R2": "enable\nconfigure terminal\ninterface gi0/0\nstandby 1 ip 192.168.1.254\nend"}
    return t, "08-hsrp", "Резервирование шлюза (HSRP)", text, checks, script, ""


# ------------------------------------------------------------------------------------ 9. IPv6
@lab
def ipv6():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 150)
    p1, p2 = t.add_device("pc", "PC1", 200, 350), t.add_device("pc", "PC2", 600, 350)
    t.connect(p1.nic, r.get_port("gi0/0"))
    t.connect(p2.nic, r.get_port("gi0/1"))
    p1.set_ipv6("2001:db8:1::10/64")
    p1.set_gateway6("2001:db8:1::1")
    p2.set_ipv6("2001:db8:2::10/64")
    p2.set_gateway6("2001:db8:2::1")
    text = """Компьютерам уже назначены IPv6-адреса: PC1 2001:db8:1::10/64, PC2 2001:db8:2::10/64.

1. Включите на **R1** маршрутизацию IPv6 (`ipv6 unicast-routing`).
2. Назначьте Gi0/0 адрес 2001:db8:1::1/64, Gi0/1 адрес 2001:db8:2::1/64 и включите интерфейсы.
3. Проверьте `ping 2001:db8:2::10` и `tracert -6` с PC1."""
    checks = """ip R1 2001:db8:1::1 | Адрес Gi0/0
ip R1 2001:db8:2::1 | Адрес Gi0/1
ping PC1 2001:db8:2::10 | PC1 видит PC2 по IPv6"""
    script = {"R1": """enable
configure terminal
ipv6 unicast-routing
interface gi0/0
ipv6 address 2001:db8:1::1/64
no shutdown
interface gi0/1
ipv6 address 2001:db8:2::1/64
no shutdown
end"""}
    return t, "09-ipv6", "IPv6: адресация и маршрутизация", text, checks, script, ""


# ------------------------------------------------------------------------------------ 10. Wi-Fi
@lab
def wifi():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 80)
    ap = t.add_device("access-point", "AP1", 400, 230)
    t.add_device("laptop", "Laptop1", 300, 360)
    t.add_device("laptop", "Laptop2", 500, 360)
    t.connect(r.get_port("gi0/0"), ap.get_port("Ethernet0"))
    cli(r, "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.50.1 255.255.255.0\nno shutdown\nexit\n"
           "ip dhcp pool WIFI\nnetwork 192.168.50.0 255.255.255.0\ndefault-router 192.168.50.1\nend")
    text = """Точка доступа AP1 подключена к R1, на R1 уже работает DHCP для сети 192.168.50.0/24.

1. Настройте на **AP1** сеть `OFFICE` с WPA2 и ключом `Office2026` (окно устройства → «Настройка»).
2. Подключите ноутбуки к сети OFFICE (Desktop → Wi-Fi или `netsh wlan connect`) и включите DHCP.
3. Проверьте доступ ноутбуков к шлюзу 192.168.50.1."""
    checks = """wifi Laptop1 OFFICE | Laptop1 подключён к OFFICE
wifi Laptop2 OFFICE | Laptop2 подключён к OFFICE
dhcp Laptop1 | Laptop1 получил адрес
ping Laptop2 192.168.50.1 | Laptop2 видит шлюз"""
    script = {"AP1": "@ssid OFFICE key Office2026", "Laptop1": "@wifi OFFICE Office2026\n@dhcp",
              "Laptop2": "@wifi OFFICE Office2026\n@dhcp"}
    return t, "10-wifi", "Беспроводная сеть", text, checks, script, ""


# ------------------------------------------------------------------------------------ troubleshooting
@lab
def trouble_office():
    t, *_rest, script, _ = intervlan()
    apply_script(t, script)
    p2 = next(d for d in t.devices.values() if d.name == "PC2")
    p2.set_gateway("192.168.20.254")
    cli(next(d for d in t.devices.values() if d.name == "R1"),
        "enable\nconfigure terminal\ninterface gi0/0.20\nencapsulation dot1Q 21\nend")
    text = """Сеть с маршрутизацией между VLAN раньше работала, но после «небольших правок» PC1 больше не видит PC2.

В сети **две** неисправности. Найдите их с помощью `show`-команд, `ipconfig` и `ping`, затем исправьте.

Подсказки: сравните VLAN на коммутаторе и на подынтерфейсах; проверьте настройки ПК."""
    checks = """ping PC2 192.168.20.1 | PC2 видит свой шлюз
ping PC1 192.168.20.10 | PC1 видит PC2"""
    fix = {"R1": "enable\nconfigure terminal\ninterface gi0/0.20\nencapsulation dot1Q 20\nend",
           "PC2": "@ip 192.168.20.10/24 gw 192.168.20.1"}
    faults = ["R1: подынтерфейс Gi0/0.20 настроен на VLAN 21 вместо 20",
              "PC2: неверный шлюз 192.168.20.254 (нужен 192.168.20.1)"]
    return t, "11-trouble-intervlan", "Поиск неисправностей: VLAN", text, checks, fix, "", faults


@lab
def trouble_ospf():
    t, *_rest, script, _ = ospf()
    apply_script(t, script)
    r = {d.name: d for d in t.devices.values()}
    cli(r["R3"], "enable\nconfigure terminal\nrouter ospf 1\nno network 10.0.0.0 0.255.255.255 area 0\n"
                 "network 10.0.0.0 0.255.255.255 area 1\nend")
    cli(r["R1"], "enable\nconfigure terminal\ninterface gi0/0\nshutdown\nend")
    text = """OSPF-сеть из трёх маршрутизаторов работала, но теперь PC1 не может достучаться ни до чего, а R3 потерял соседа.

В сети **две** неисправности. Используйте `show ip interface brief`, `show ip ospf neighbor`, `show ip protocols`."""
    checks = """ospf R2 2 | У R2 снова два соседа
ping PC1 192.168.1.1 | PC1 видит свой шлюз
ping PC1 192.168.3.10 | PC1 видит PC3"""
    fix = {"R1": "enable\nconfigure terminal\ninterface gi0/0\nno shutdown\nend",
           "R3": "enable\nconfigure terminal\nrouter ospf 1\nno network 10.0.0.0 0.255.255.255 area 1\n"
                 "network 10.0.0.0 0.255.255.255 area 0\nend"}
    faults = ["R1: интерфейс Gi0/0 (LAN PC1) выключен командой shutdown",
              "R3: транзитная сеть 10.0.23.0/30 объявлена в зоне 1 вместо зоны 0, соседство с R2 не поднимается"]
    return t, "12-trouble-ospf", "Поиск неисправностей: OSPF", text, checks, fix, "", faults


def build(fn):
    r = fn()
    t, fname, title, text, checks, script, notes = r[:7]
    faults = r[7] if len(r) > 7 else []
    t.engine.run(max_time=60000)
    lab_obj = Lab(title, text, parse_checks(checks)[0], notes, script, faults, "troubleshoot" if faults else "lab")
    from labs_en import EN
    from labs_more import EN as EN2
    from labs_ddos import EN as EN3
    from labs_malware import EN as EN4
    from labs_l2 import EN as EN5
    en = EN.get(fname) or EN2.get(fname) or EN3.get(fname) or EN4.get(fname) or EN5[fname]
    assert len(en["checks"]) == len(lab_obj.checks), f"{fname}: English check list does not match"
    lab_obj.translations = {"en": en}
    before = [ok for _, ok, _ in evaluate(t, lab_obj)]
    t.lab = lab_obj
    data = persistence.topology_to_dict(t)
    assert not all(before), f"{fname}: every check already passes before the solution"
    # verify the solution on a fresh copy
    t2 = persistence.topology_from_dict(data)
    t2.engine.run(max_time=5000)
    apply_script(t2, script)
    res = evaluate(t2, t2.lab)
    bad = [(c.text, d) for c, ok, d in res if not ok]
    assert not bad, f"{fname}: solution does not pass: {bad}"
    import json
    (OUT / f"{fname}.netemu").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return fname, len(res)


if __name__ == "__main__":
    from labs_more import LABS as MORE
    from labs_ddos import LABS as DDOS
    from labs_malware import LABS as MALWARE
    from labs_l2 import LABS as L2
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    for fn in LABS + MORE + DDOS + MALWARE + L2:
        if only and not any(o in fn.__name__ for o in only):
            continue
        name, n = build(fn)
        print(f"{name}: {n} checks OK")
