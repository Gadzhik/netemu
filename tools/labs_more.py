"""Second set of ready-made labs (13-19).  Imported by make_labs.py; each returns the same tuple:
(topology, file name, title, task, checks, solution script, notes[, faults])."""
from __future__ import annotations

from netemu.core.topology import Topology


def _host(dev, ip, gw=None):
    from netemu.core.addresses import prefix_to_mask
    a, _, plen = ip.partition("/")
    dev.set_ip(a, prefix_to_mask(int(plen or 24)))
    if gw:
        dev.set_gateway(gw)


def _cli(dev, text):
    s = dev.create_cli()
    for line in text.strip().splitlines():
        s.execute(line, lambda x: None)


def bgp():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911", "R1", 250, 150), t.add_device("cisco-2911", "R2", 550, 150)
    p1, p2 = t.add_device("pc", "PC1", 250, 350), t.add_device("pc", "PC2", 550, 350)
    t.connect(r1.get_port("gi0/1"), r2.get_port("gi0/1"))
    t.connect(p1.nic, r1.get_port("gi0/0"))
    t.connect(p2.nic, r2.get_port("gi0/0"))
    _host(p1, "10.1.0.10/24", "10.1.0.1")
    _host(p2, "10.2.0.10/24", "10.2.0.1")
    _cli(r1, "enable\nconfigure terminal\nhostname R1\ninterface gi0/0\nip address 10.1.0.1 255.255.255.0\nno shutdown\n"
             "interface gi0/1\nip address 172.16.12.1 255.255.255.252\nno shutdown\nend")
    _cli(r2, "enable\nconfigure terminal\nhostname R2\ninterface gi0/0\nip address 10.2.0.1 255.255.255.0\nno shutdown\n"
             "interface gi0/1\nip address 172.16.12.2 255.255.255.252\nno shutdown\nend")
    text = """Две компании соединены каналом 172.16.12.0/30: R1 в AS 65001 (сеть 10.1.0.0/24), R2 в AS 65002 (сеть 10.2.0.0/24).

1. Настройте eBGP-соседство между R1 и R2.
2. Анонсируйте каждую локальную сеть командой `network … mask …`.
3. Проверьте `show ip bgp summary`, маршрут с кодом B и связь PC1 с PC2."""
    checks = """bgp R1 1 | У R1 установлена BGP-сессия
bgp R2 1 | У R2 установлена BGP-сессия
route R1 10.2.0.0/24 | R1 получил сеть 10.2.0.0/24 по BGP
ping PC1 10.2.0.10 | PC1 видит PC2"""
    script = {"R1": "enable\nconfigure terminal\nrouter bgp 65001\nneighbor 172.16.12.2 remote-as 65002\n"
                    "network 10.1.0.0 mask 255.255.255.0\nend",
              "R2": "enable\nconfigure terminal\nrouter bgp 65002\nneighbor 172.16.12.1 remote-as 65001\n"
                    "network 10.2.0.0 mask 255.255.255.0\nend"}
    return t, "13-bgp", "eBGP между двумя AS", text, checks, script, ""


def etherchannel():
    t = Topology()
    s1, s2 = t.add_device("cisco-2960", "SW1", 250, 150), t.add_device("cisco-2960", "SW2", 550, 150)
    p1, p2 = t.add_device("pc", "PC1", 250, 350), t.add_device("pc", "PC2", 550, 350)
    for n in (23, 24):
        t.connect(s1.get_port(f"Fa0/{n}"), s2.get_port(f"Fa0/{n}"))
    t.connect(p1.nic, s1.get_port("Fa0/1"))
    t.connect(p2.nic, s2.get_port("Fa0/1"))
    _host(p1, "192.168.1.10/24")
    _host(p2, "192.168.1.20/24")
    text = """Коммутаторы соединены двумя кабелями (Fa0/23 и Fa0/24), но STP блокирует один из них.

1. Объедините оба канала в EtherChannel Port-channel 1 по протоколу LACP (`channel-group 1 mode active` с обеих сторон).
2. Проверьте `show etherchannel summary`: оба порта должны быть в состоянии P.
3. Убедитесь, что PC1 видит PC2."""
    checks = """channel SW1 Po1 2 | На SW1 в Po1 два активных порта
channel SW2 Po1 2 | На SW2 в Po1 два активных порта
ping PC1 192.168.1.20 | PC1 видит PC2"""
    sc = "enable\nconfigure terminal\ninterface range fa0/23 - 24\nchannel-group 1 mode active\nend"
    return t, "14-etherchannel", "EtherChannel (LACP)", text, checks, {"SW1": sc, "SW2": sc}, ""


def port_security():
    t = Topology()
    sw = t.add_device("cisco-2960", "SW1", 400, 150)
    r = t.add_device("cisco-2911", "R1", 400, 20)
    p1 = t.add_device("pc", "PC1", 250, 350)
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    t.connect(p1.nic, sw.get_port("Fa0/1"))
    _host(p1, "192.168.1.10/24", "192.168.1.1")
    _cli(r, "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\nno shutdown\nend")
    text = """К порту Fa0/1 разрешено подключать только рабочий компьютер PC1.

1. Переведите Fa0/1 в режим access и включите на нём port security.
2. Разрешите один MAC-адрес и запомните его автоматически (`mac-address sticky`), нарушение — shutdown.
3. Проверьте `show port-security interface fa0/1` и связь PC1 со шлюзом."""
    checks = """psec SW1 Fa0/1 | Port security включён на Fa0/1
ping PC1 192.168.1.1 | PC1 работает через защищённый порт"""
    script = {"SW1": "enable\nconfigure terminal\ninterface fa0/1\nswitchport mode access\nswitchport port-security\n"
                     "switchport port-security maximum 1\nswitchport port-security mac-address sticky\n"
                     "switchport port-security violation shutdown\nend"}
    return t, "15-port-security", "Port security", text, checks, script, ""


def vpn():
    t = Topology()
    inet = t.add_device("internet", "Internet", 400, 60)
    r1, r2 = t.add_device("cisco-2911", "HQ", 200, 200), t.add_device("cisco-2911", "BRANCH", 600, 200)
    a, b = t.add_device("pc", "PC-HQ", 200, 380), t.add_device("pc", "PC-BR", 600, 380)
    t.connect(r1.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(r2.get_port("gi0/1"), inet.get_port("ISP1"))
    t.connect(a.nic, r1.get_port("gi0/0"))
    t.connect(b.nic, r2.get_port("gi0/0"))
    _host(a, "192.168.1.10/24", "192.168.1.1")
    _host(b, "192.168.2.10/24", "192.168.2.1")
    for r, i, me, gw in ((r1, 1, "203.0.113.2", "203.0.113.1"), (r2, 2, "203.0.113.6", "203.0.113.5")):
        _cli(r, f"enable\nconfigure terminal\nhostname {r.name}\ninterface gi0/0\nip address 192.168.{i}.1 255.255.255.0\n"
                f"no shutdown\ninterface gi0/1\nip address {me} 255.255.255.252\nno shutdown\nexit\n"
                f"ip route 0.0.0.0 0.0.0.0 {gw}\nend")
    text = """Главный офис (HQ, 203.0.113.2) и филиал (BRANCH, 203.0.113.6) выходят в интернет. Частные сети 192.168.1.0/24 и 192.168.2.0/24 пока не видят друг друга.

1. Создайте между маршрутизаторами GRE-туннель Tunnel0 (10.255.0.0/30).
2. Защитите его IPsec: ISAKMP-политика с ключом `Vpn-2026`, transform-set и `tunnel protection ipsec profile`.
3. Направьте сеть другого офиса в туннель и проверьте связь между ПК."""
    checks = """ping PC-HQ 192.168.2.10 | Офисы связаны через VPN
ping HQ 10.255.0.2 | Туннель поднят"""

    def side(i, peer):
        return ("enable\nconfigure terminal\ncrypto isakmp policy 10\nencryption aes 256\nauthentication pre-share\n"
                f"group 14\nexit\ncrypto isakmp key Vpn-2026 address {peer}\n"
                "crypto ipsec transform-set TS esp-aes 256 esp-sha-hmac\nexit\ncrypto ipsec profile VPN\n"
                f"set transform-set TS\nexit\ninterface tunnel 0\nip address 10.255.0.{i} 255.255.255.252\n"
                f"tunnel source gi0/1\ntunnel destination {peer}\ntunnel protection ipsec profile VPN\nexit\n"
                f"ip route 192.168.{3 - i}.0 255.255.255.0 Tunnel0\nend")
    return t, "16-vpn", "VPN между офисами (GRE + IPsec)", text, checks, \
        {"HQ": side(1, "203.0.113.6"), "BRANCH": side(2, "203.0.113.2")}, ""


def voip():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 40)
    sw = t.add_device("cisco-2960", "SW1", 400, 180)
    p1, p2 = t.add_device("ip-phone", "Phone1", 250, 330), t.add_device("ip-phone", "Phone2", 550, 330)
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    t.connect(p1.nic, sw.get_port("Fa0/1"))
    t.connect(p2.nic, sw.get_port("Fa0/2"))
    _cli(sw, "enable\nconfigure terminal\nvlan 10\nvlan 20\nexit\ninterface range fa0/1 - 2\nswitchport mode access\n"
             "switchport access vlan 10\nexit\ninterface fa0/24\nswitchport mode trunk\nend")
    _cli(r, "enable\nconfigure terminal\ninterface gi0/0\nno shutdown\ninterface gi0/0.20\nencapsulation dot1Q 20\n"
            "ip address 192.168.20.1 255.255.255.0\nend")
    text = """Подключите два IP-телефона к станции Cisco CME на R1. Данные идут во VLAN 10, голос — во VLAN 20 (192.168.20.0/24).

1. На SW1 включите голосовой VLAN 20 на портах телефонов (`switchport voice vlan 20`).
2. На R1 создайте DHCP-пул VOICE с опцией 150 (адрес CME 192.168.20.1).
3. Настройте `telephony-service` (ip source-address, max-ephones, max-dn, auto assign) и номера 1001, 1002.
4. Позвоните с Phone1 на 1002 (вкладка «Телефон»)."""
    checks = """phone Phone1 | Phone1 зарегистрирован на CME
phone Phone2 | Phone2 зарегистрирован на CME"""
    script = {"SW1": "enable\nconfigure terminal\ninterface range fa0/1 - 2\nswitchport voice vlan 20\nend",
              "R1": "enable\nconfigure terminal\nip dhcp excluded-address 192.168.20.1\nip dhcp pool VOICE\n"
                    "network 192.168.20.0 255.255.255.0\ndefault-router 192.168.20.1\noption 150 ip 192.168.20.1\nexit\n"
                    "telephony-service\nmax-ephones 5\nmax-dn 5\nip source-address 192.168.20.1 port 2000\n"
                    "auto assign 1 to 5\nexit\nephone-dn 1\nnumber 1001\nexit\nephone-dn 2\nnumber 1002\nend"}
    return t, "17-voip", "IP-телефония (voice VLAN, CME)", text, checks, script, ""


def ipv6_ospf():
    t = Topology()
    rs = [t.add_device("cisco-2911", f"R{i}", 150 + 250 * (i - 1), 150) for i in (1, 2, 3)]
    p1, p3 = t.add_device("pc", "PC1", 150, 350), t.add_device("pc", "PC3", 650, 350)
    t.connect(rs[0].get_port("gi0/1"), rs[1].get_port("gi0/1"))
    t.connect(rs[1].get_port("gi0/2"), rs[2].get_port("gi0/1"))
    t.connect(p1.nic, rs[0].get_port("gi0/0"))
    t.connect(p3.nic, rs[2].get_port("gi0/0"))
    p1.set_ipv6("2001:db8:1::10/64")
    p1.set_gateway6("2001:db8:1::1")
    p3.set_ipv6("2001:db8:3::10/64")
    p3.set_gateway6("2001:db8:3::1")
    addrs = {0: {"gi0/0": "2001:db8:1::1/64", "gi0/1": "2001:db8:12::1/64"},
             1: {"gi0/1": "2001:db8:12::2/64", "gi0/2": "2001:db8:23::1/64"},
             2: {"gi0/0": "2001:db8:3::1/64", "gi0/1": "2001:db8:23::2/64"}}
    for i, r in enumerate(rs):
        lines = ["enable", "configure terminal", f"hostname R{i + 1}", "ipv6 unicast-routing"]
        for ifn, a in addrs[i].items():
            lines += [f"interface {ifn}", f"ipv6 address {a}", "no shutdown"]
        _cli(r, "\n".join(lines + ["end"]))
    text = """Адреса IPv6 уже назначены. Сети ПК 2001:db8:1::/64 и 2001:db8:3::/64 пока не связаны.

1. На каждом маршрутизаторе запустите OSPFv3 (`ipv6 router ospf 1`, router-id 1.1.1.1 / 2.2.2.2 / 3.3.3.3).
2. Включите его на всех интерфейсах в зоне 0 (`ipv6 ospf 1 area 0`).
3. Проверьте `show ipv6 ospf neighbor`, `show ipv6 route` и `ping 2001:db8:3::10` с PC1."""
    checks = """ospf6 R2 2 | У R2 два соседа OSPFv3
route R1 2001:db8:3::/64 | R1 знает сеть PC3
ping PC1 2001:db8:3::10 | PC1 видит PC3 по IPv6"""
    script = {}
    for i in range(3):
        lines = ["enable", "configure terminal", "ipv6 router ospf 1", f"router-id {i + 1}.{i + 1}.{i + 1}.{i + 1}", "exit"]
        for ifn in addrs[i]:
            lines += [f"interface {ifn}", "ipv6 ospf 1 area 0"]
        script[f"R{i + 1}"] = "\n".join(lines + ["end"])
    return t, "18-ipv6-ospfv3", "IPv6 и OSPFv3", text, checks, script, ""


def exam():
    t = Topology()
    inet = t.add_device("internet", "Internet", 650, 40)
    r = t.add_device("cisco-2911", "R1", 400, 40)
    sw = t.add_device("cisco-2960", "SW1", 400, 200)
    p1, p2 = t.add_device("pc", "PC-SALES", 250, 360), t.add_device("pc", "PC-HR", 550, 360)
    t.connect(r.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    t.connect(p1.nic, sw.get_port("Fa0/1"))
    t.connect(p2.nic, sw.get_port("Fa0/2"))
    for pc in (p1, p2):
        pc.dhcp_enabled = True
    text = """**Экзамен: офис с нуля.** Оборудование соединено, но ничего не настроено.

Требования:
- VLAN 10 `SALES` (PC-SALES, Fa0/1) и VLAN 20 `HR` (PC-HR, Fa0/2), транк к R1 на Fa0/24;
- маршрутизация между VLAN на R1: 192.168.10.1/24 и 192.168.20.1/24;
- DHCP для обеих сетей (шлюз .1, DNS 8.8.8.8), ПК получают адреса автоматически;
- выход в интернет: адрес провайдера 203.0.113.2/30, шлюз 203.0.113.1, PAT для обеих сетей;
- имя маршрутизатора `OFFICE-GW`.

Проверьте ping 8.8.8.8 и браузер `http://www.example.com` на обоих ПК."""
    checks = """hostname OFFICE-GW OFFICE-GW | Маршрутизатор называется OFFICE-GW
vlan SW1 10 | На SW1 есть VLAN 10
vlan SW1 20 | На SW1 есть VLAN 20
dhcp PC-SALES | PC-SALES получил адрес по DHCP
dhcp PC-HR | PC-HR получил адрес по DHCP
ping PC-SALES 192.168.20.1 | Маршрутизация между VLAN работает
ping PC-SALES 8.8.8.8 | Отдел продаж выходит в интернет
ping PC-HR 8.8.8.8 | Отдел кадров выходит в интернет
http PC-HR http://www.example.com | Веб-сайты открываются
nat OFFICE-GW | На маршрутизаторе работает NAT"""
    script = {"SW1": "enable\nconfigure terminal\nvlan 10\nname SALES\nvlan 20\nname HR\nexit\ninterface fa0/1\n"
                     "switchport mode access\nswitchport access vlan 10\ninterface fa0/2\nswitchport mode access\n"
                     "switchport access vlan 20\ninterface fa0/24\nswitchport mode trunk\nend",
              "R1": "enable\nconfigure terminal\nhostname OFFICE-GW\ninterface gi0/0\nno shutdown\n"
                    "interface gi0/0.10\nencapsulation dot1Q 10\nip address 192.168.10.1 255.255.255.0\nip nat inside\n"
                    "interface gi0/0.20\nencapsulation dot1Q 20\nip address 192.168.20.1 255.255.255.0\nip nat inside\n"
                    "interface gi0/1\nip address 203.0.113.2 255.255.255.252\nip nat outside\nno shutdown\nexit\n"
                    "ip route 0.0.0.0 0.0.0.0 203.0.113.1\nip dhcp excluded-address 192.168.10.1\n"
                    "ip dhcp excluded-address 192.168.20.1\nip dhcp pool SALES\nnetwork 192.168.10.0 255.255.255.0\n"
                    "default-router 192.168.10.1\ndns-server 8.8.8.8\nexit\nip dhcp pool HR\n"
                    "network 192.168.20.0 255.255.255.0\ndefault-router 192.168.20.1\ndns-server 8.8.8.8\nexit\n"
                    "access-list 1 permit 192.168.0.0 0.0.255.255\n"
                    "ip nat inside source list 1 interface gi0/1 overload\nend",
              "PC-SALES": "@dhcp", "PC-HR": "@dhcp"}
    return t, "19-exam-office", "Экзамен: офис с нуля", text, checks, script, ""


LABS = [bgp, etherchannel, port_security, vpn, voip, ipv6_ospf, exam]

EN = {
    "13-bgp": {"title": "eBGP between two autonomous systems", "text": """Two companies are linked by 172.16.12.0/30: R1 in AS 65001 (10.1.0.0/24), R2 in AS 65002 (10.2.0.0/24).

1. Configure the eBGP neighbourship between R1 and R2.
2. Advertise each local network with `network … mask …`.
3. Check `show ip bgp summary`, the B route and that PC1 reaches PC2.""",
               "checks": ["R1 has an established BGP session", "R2 has an established BGP session",
                          "R1 learned 10.2.0.0/24 through BGP", "PC1 reaches PC2"]},
    "14-etherchannel": {"title": "EtherChannel (LACP)", "text": """The switches are linked by two cables (Fa0/23 and Fa0/24), but STP blocks one of them.

1. Bundle both links into Port-channel 1 with LACP (`channel-group 1 mode active` on both sides).
2. Check `show etherchannel summary`: both ports must be in state P.
3. Make sure PC1 reaches PC2.""",
                        "checks": ["Po1 on SW1 has two active ports", "Po1 on SW2 has two active ports", "PC1 reaches PC2"]},
    "15-port-security": {"title": "Port security", "text": """Only the office PC1 may be connected to port Fa0/1.

1. Make Fa0/1 an access port and enable port security on it.
2. Allow one MAC address, learn it automatically (`mac-address sticky`), violation mode shutdown.
3. Check `show port-security interface fa0/1` and that PC1 reaches its gateway.""",
                         "checks": ["Port security is on for Fa0/1", "PC1 works through the secured port"]},
    "16-vpn": {"title": "Site-to-site VPN (GRE + IPsec)", "text": """The head office (HQ, 203.0.113.2) and the branch (BRANCH, 203.0.113.6) are on the Internet. Their private networks 192.168.1.0/24 and 192.168.2.0/24 cannot reach each other yet.

1. Build a GRE tunnel Tunnel0 between the routers (10.255.0.0/30).
2. Protect it with IPsec: an ISAKMP policy with the key `Vpn-2026`, a transform set and `tunnel protection ipsec profile`.
3. Route the other office's network into the tunnel and test PC to PC.""",
               "checks": ["The offices are connected through the VPN", "The tunnel is up"]},
    "17-voip": {"title": "IP telephony (voice VLAN, CME)", "text": """Connect two IP phones to Cisco CME on R1. Data uses VLAN 10, voice VLAN 20 (192.168.20.0/24).

1. On SW1 enable voice VLAN 20 on the phone ports (`switchport voice vlan 20`).
2. On R1 create DHCP pool VOICE with option 150 (CME address 192.168.20.1).
3. Configure `telephony-service` (ip source-address, max-ephones, max-dn, auto assign) and numbers 1001, 1002.
4. Call 1002 from Phone1 (Phone tab).""",
                "checks": ["Phone1 is registered with CME", "Phone2 is registered with CME"]},
    "18-ipv6-ospfv3": {"title": "IPv6 and OSPFv3", "text": """The IPv6 addresses are assigned. The PC networks 2001:db8:1::/64 and 2001:db8:3::/64 are not connected yet.

1. Start OSPFv3 on every router (`ipv6 router ospf 1`, router-id 1.1.1.1 / 2.2.2.2 / 3.3.3.3).
2. Enable it on all interfaces in area 0 (`ipv6 ospf 1 area 0`).
3. Check `show ipv6 ospf neighbor`, `show ipv6 route` and `ping 2001:db8:3::10` from PC1.""",
                       "checks": ["R2 has two OSPFv3 neighbours", "R1 knows PC3's network", "PC1 reaches PC3 over IPv6"]},
    "19-exam-office": {"title": "Exam: an office from scratch", "text": """**Exam: an office from scratch.** The equipment is cabled but nothing is configured.

Requirements:
- VLAN 10 `SALES` (PC-SALES, Fa0/1) and VLAN 20 `HR` (PC-HR, Fa0/2), trunk to R1 on Fa0/24;
- inter-VLAN routing on R1: 192.168.10.1/24 and 192.168.20.1/24;
- DHCP for both networks (gateway .1, DNS 8.8.8.8); the PCs get their addresses automatically;
- Internet access: ISP address 203.0.113.2/30, gateway 203.0.113.1, PAT for both networks;
- the router is named `OFFICE-GW`.

Test ping 8.8.8.8 and the browser `http://www.example.com` on both PCs.""",
                       "checks": ["The router is named OFFICE-GW", "SW1 has VLAN 10", "SW1 has VLAN 20",
                                  "PC-SALES got an address by DHCP", "PC-HR got an address by DHCP",
                                  "Inter-VLAN routing works", "Sales reaches the Internet", "HR reaches the Internet",
                                  "Web sites open", "NAT is working on R1"]},
}
