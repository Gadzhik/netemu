"""Ready-made SIEM lab (35).  Imported by make_labs.py.

The student is given a working office network with a hidden attack running.  They open
Tools -> Monitoring (SIEM), read the alert, identify the attack and apply the right defence; the
check verifies the defence worked."""
from __future__ import annotations

from labs_l2 import lan

from netemu.core.security import l2attack as l2


def find_the_attack():
    # a quiet-looking LAN; an ARP-spoofing man-in-the-middle is running but not obvious on the canvas
    t, gw, sw, pcs, atk = lan(l2.L2Attack("arp-spoof", gateway="192.168.1.1", running=True), static=True)
    binds = "\n".join(f"ip source binding {p.nic.mac.cisco()} vlan 1 192.168.1.{11 + i} interface fa0/{i + 2}"
                      for i, p in enumerate(pcs))
    text = """**Найди атаку по мониторингу.** Пользователи жалуются, что сеть «ведёт себя странно». На схеме ничего подозрительного. Ваша задача — как аналитик центра мониторинга (SOC) — найти атаку и остановить её.

1. Откройте **«Инструменты → Мониторинг (SIEM)»** (`Ctrl+Shift+M`). SIEM собирает события со всех устройств и мониторов безопасности и показывает тревоги по важности. Найдите тревогу и прочитайте рекомендацию внизу.
2. В журнале событий и в «Инструменты → Атаки в сети (L2)» видно подробности: кто атакует и сколько кадров перехвачено. Это **подмена ARP** — чужое устройство выдаёт себя за шлюз и перехватывает трафик (атака «человек посередине»).
3. Остановите атаку: включите на коммутаторе **Dynamic ARP Inspection** для VLAN 1, доверенный порт — к шлюзу (gi0/1). Адреса статические, поэтому заполните таблицу привязок `ip source binding`.
4. Проверьте, что перехват прекратился, а связь сохранилась. В SIEM тревога исчезнет.

Чем SIEM помогает по сравнению с просмотром каждого устройства по отдельности? Какие ещё тревоги он показывает (DDoS, черви, командные серверы)?"""
    checks = """mitm ATK 0 | Перехват прекращён, атака остановлена
ping PC1 192.168.1.1 | Связь со шлюзом сохранилась"""
    script = {"SW": "enable\nconfigure terminal\n" + binds + "\ninterface gi0/1\n"
                    "ip arp inspection trust\nexit\nip arp inspection vlan 1\nend"}
    return t, "35-siem-find-attack", "Мониторинг: найди атаку по SIEM", text, checks, script, \
        "SIEM (центр мониторинга) собирает события со всех устройств в одном месте и превращает их в тревоги по " \
        "важности, поэтому атаку видно сразу, не просматривая каждое устройство вручную."


LABS = [find_the_attack]

EN = {
    "35-siem-find-attack": {"title": "Monitoring: find the attack with the SIEM", "text": """**Find the attack with monitoring.** Users complain that the network "behaves strangely". Nothing looks suspicious on the canvas. Your job, as a SOC analyst, is to find the attack and stop it.

1. Open **Tools → Monitoring (SIEM)** (`Ctrl+Shift+M`). The SIEM collects events from every device and security monitor and shows alerts by severity. Find the alert and read the advice at the bottom.
2. The event log and Tools → LAN attacks show the detail: who is attacking and how many frames are intercepted. This is **ARP spoofing** — a foreign device impersonates the gateway and intercepts traffic (a man-in-the-middle attack).
3. Stop it: turn on **Dynamic ARP Inspection** for VLAN 1 on the switch, with the port to the gateway (gi0/1) trusted. The addresses are static, so fill the binding table with `ip source binding`.
4. Check that interception stopped and connectivity is kept. The alert disappears in the SIEM.

How does a SIEM help compared with checking each device separately? What other alerts does it show (DDoS, worms, C2 servers)?""",
                            "checks": ["Interception stopped, the attack is halted", "Connectivity to the gateway is kept"]},
}
