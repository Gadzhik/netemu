"""Ready-made layer-2 attack labs (31-33).  Imported by make_labs.py.

A LAN: a gateway router with DHCP, an access switch, two victim PCs and a LAN attacker, all in
192.168.1.0/24.  The attack is already running when the lab opens."""
from __future__ import annotations

from labs_more import _cli

from netemu.core.security import l2attack as l2
from netemu.core.topology import Topology


def lan(attack, static=False):
    from netemu.core.addresses import prefix_to_mask
    t = Topology()
    gw = t.add_device("cisco-2911", "GW", 420, 80)
    sw = t.add_device("cisco-2960", "SW", 420, 240)
    t.connect(gw.get_port("gi0/0"), sw.get_port("Gi0/1"))
    _cli(gw, """enable
configure terminal
hostname GW
interface gi0/0
ip address 192.168.1.1 255.255.255.0
no shutdown
exit
ip dhcp excluded-address 192.168.1.1 192.168.1.10
ip dhcp pool LAN
network 192.168.1.0 255.255.255.0
default-router 192.168.1.1
end""")
    pcs = []
    for i in range(2):
        pc = t.add_device("pc", f"PC{i + 1}", 240 + i * 150, 420)
        t.connect(pc.nic, sw.get_port(f"Fa0/{i + 2}"))
        if static:
            pc.set_ip(f"192.168.1.{11 + i}", prefix_to_mask(24))
            pc.set_gateway("192.168.1.1")
        else:
            pc.start_dhcp()
        pcs.append(pc)
    atk = t.add_device("lan-attacker", "ATK", 620, 420)
    t.connect(atk.nic, sw.get_port("Fa0/10"))
    if static:
        atk.set_ip("192.168.1.13", prefix_to_mask(24))
        atk.set_gateway("192.168.1.1")
    else:
        atk.start_dhcp()
    t.engine.run(max_time=4000)
    atk.l2attacks = [attack]
    atk.start()
    return t, gw, sw, pcs, atk


def arp_dai():
    t, gw, sw, pcs, atk = lan(l2.L2Attack("arp-spoof", gateway="192.168.1.1", running=True), static=True)
    binds = "\n".join(f"ip source binding {p.nic.mac.cisco()} vlan 1 192.168.1.{11 + i} interface fa0/{i + 2}"
                      for i, p in enumerate(pcs))
    text = """**Подмена ARP (MITM).** В сеть подключили чужой ноутбук (ATK). Он рассылает поддельные ARP-ответы: говорит каждому компьютеру, что шлюз 192.168.1.1 — это он. Теперь весь трафик PC1 и PC2 к шлюзу идёт через атакующего, который может его читать и подменять (атака «человек посередине»).

1. Откройте «Инструменты → Атаки в сети (L2)»: видно, кто атакует и сколько кадров перехвачено. В `arp -a` на PC1 MAC шлюза совпадает с MAC атакующего.
2. Включите на коммутаторе **Dynamic ARP Inspection** для VLAN 1. Коммутатор будет сверять ARP на недоверенных портах с таблицей «какой IP какому MAC принадлежит» и отбрасывать подделки. Порт к шлюзу сделайте доверенным. Так как адреса здесь статические, заполните таблицу вручную командами `ip source binding` (в сети с DHCP её автоматически строит DHCP snooping).
3. Проверьте, что перехват прекратился, а связь компьютеров со шлюзом сохранилась.

Почему DAI нужен именно на коммутаторе, а не на компьютерах? Что такое доверенный порт?"""
    checks = """mitm ATK 0 | Атакующий больше не перехватывает трафик
ping PC1 192.168.1.1 | PC1 по-прежнему видит шлюз"""
    script = {"SW": "enable\nconfigure terminal\n" + binds + "\ninterface gi0/1\n"
                    "ip arp inspection trust\nexit\nip arp inspection vlan 1\nend"}
    return t, "31-l2-arp-spoofing", "L2: подмена ARP и Dynamic ARP Inspection", text, checks, script, \
        "Подмену ARP в локальной сети останавливает Dynamic ARP Inspection, опирающийся на таблицу DHCP snooping " \
        "(или на статические привязки ip source binding). Защищать нужно на коммутаторе, а не на конечных узлах."


def mac_port_security():
    t, gw, sw, pcs, atk = lan(l2.L2Attack("mac-flood", running=True))
    sw.mac_limit = 300
    text = """**Переполнение MAC-таблицы (MAC flooding).** Атакующий заваливает коммутатор кадрами с тысячами случайных MAC-адресов. Таблица коммутации переполняется, и коммутатор начинает работать «на отказ» — рассылает все кадры во все порты (как концентратор). Атакующий видит трафик, предназначенный другим.

1. В «Инструменты → Атаки в сети (L2)» видно переполнение таблицы и перехваченные кадры. На коммутаторе `show mac address-table count` показывает, что таблица забита.
2. Включите **port security** на порту атакующего (fa0/10): ограничьте число MAC-адресов и задайте действие при нарушении — отключить порт. Тогда лавина MAC-адресов отключит порт нарушителя, а таблица не переполнится.
3. Убедитесь, что перехват прекратился.

Почему port security ставят на порты к пользователям, а не на магистральные порты между коммутаторами?"""
    checks = "mitm ATK 0 | Атакующий больше не видит чужой трафик"
    script = {"SW": "enable\nconfigure terminal\ninterface fa0/10\nswitchport mode access\n"
                    "switchport port-security\nswitchport port-security maximum 3\n"
                    "switchport port-security violation shutdown\nend"}
    return t, "32-l2-mac-flooding", "L2: переполнение MAC-таблицы и port security", text, checks, script, \
        "Переполнение MAC-таблицы (CAM overflow) останавливает port security: лимит адресов на порт и отключение " \
        "порта при нарушении. Ставится на порты доступа к пользователям."


def stp_bpduguard():
    t, gw, sw, pcs, atk = lan(l2.L2Attack("stp-root", running=True))
    text = """**Захват корня STP.** Атакующий отправляет «лучшие» BPDU (с приоритетом 0) и становится корнем связующего дерева. Коммутаторы перестраивают топологию так, что трафик идёт через атакующего, — ещё один способ встать «человеком посередине» и подслушивать сеть.

1. В «Инструменты → Атаки в сети (L2)» видно захват корня. `show spanning-tree` на SW показывает, что корень — чужой коммутатор (MAC атакующего).
2. На портах, к которым подключены **конечные устройства**, корневых мостов быть не должно. Включите на порту атакующего **BPDU Guard**: при получении BPDU порт сразу отключается (err-disable). Так защищают все пользовательские порты (`spanning-tree portfast bpduguard default`).
3. Проверьте, что коммутатор SW снова стал корнем.

Чем BPDU Guard отличается от Root Guard? Где применяют каждый?"""
    checks = """stproot SW | Коммутатор SW снова корень связующего дерева
ping PC1 192.168.1.1 | Компьютеры работают в сети"""
    script = {"SW": "enable\nconfigure terminal\ninterface fa0/10\nspanning-tree bpduguard enable\nend"}
    return t, "33-l2-stp-root", "L2: захват корня STP и BPDU Guard", text, checks, script, \
        "Захват корня STP останавливает BPDU Guard (отключает порт при получении BPDU) или Root Guard (запрещает " \
        "порту стать путём к корню). Ставятся на порты к пользователям и к чужим коммутаторам."


LABS = [arp_dai, mac_port_security, stp_bpduguard]

EN = {
    "31-l2-arp-spoofing": {"title": "L2: ARP spoofing and Dynamic ARP Inspection", "text": """**ARP spoofing (MITM).** A foreign laptop (ATK) was plugged into the network. It sends forged ARP replies telling every computer that the gateway 192.168.1.1 is at its own MAC. Now all of PC1's and PC2's traffic to the gateway flows through the attacker, who can read and alter it (a man-in-the-middle attack).

1. Open Tools → LAN attacks: it shows who is attacking and how many frames are intercepted. In `arp -a` on PC1 the gateway's MAC equals the attacker's.
2. On the switch turn on **DHCP snooping** (it records which IP was leased to which MAC) and **Dynamic ARP Inspection**: the switch checks ARP on untrusted ports against that table and drops forgeries. The port to the gateway is trusted.
3. Check that interception stopped while the computers still reach the gateway.

Why must DAI run on the switch rather than on the computers? What is a trusted port?""",
                           "checks": ["The attacker no longer intercepts traffic", "PC1 still reaches the gateway"]},
    "32-l2-mac-flooding": {"title": "L2: MAC flooding and port security", "text": """**MAC flooding (CAM overflow).** The attacker floods the switch with frames from thousands of random MAC addresses. The switching table overflows and the switch fails open, flooding every frame out of all ports (like a hub). The attacker sees traffic meant for others.

1. Tools → LAN attacks shows the table overflow and the intercepted frames. On the switch `show mac address-table count` shows the table is full.
2. Turn on **port security** on the attacker's port (fa0/10): limit the number of MAC addresses and set the violation action to shut the port. The flood of MACs then disables the offender's port and the table does not overflow.
3. Check that interception stopped.

Why is port security put on user access ports rather than on the trunk ports between switches?""",
                           "checks": ["The attacker no longer sees others' traffic"]},
    "33-l2-stp-root": {"title": "L2: STP root takeover and BPDU Guard", "text": """**STP root takeover.** The attacker sends "superior" BPDUs (priority 0) and becomes the spanning-tree root. The switches rebuild the topology so that traffic flows through the attacker: another way to become a man-in-the-middle and eavesdrop on the network.

1. Tools → LAN attacks shows the root takeover. `show spanning-tree` on SW shows the root is a foreign switch (the attacker's MAC).
2. Ports where **end devices** connect must never carry a root bridge. Turn on **BPDU Guard** on the attacker's port: when a BPDU arrives the port is disabled at once (err-disable). All user ports are protected this way (`spanning-tree portfast bpduguard default`).
3. Check that switch SW is the root again.

How does BPDU Guard differ from Root Guard? Where is each used?""",
                       "checks": ["Switch SW is the spanning-tree root again", "The computers work in the network"]},
}
