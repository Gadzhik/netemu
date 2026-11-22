"""A simple SIEM (Security Information and Event Management): one pane of glass over the network's
security signals.

It collects events from the three security monitors (DDoS, malware, L2 attacks) and from device syslog
buffers, correlates them into **alerts** (a port scan, a flood, a worm outbreak, ARP spoofing, a brute
force, ...) and ranks them by severity.  The GUI shows the merged event timeline and the alerts; a lab can
hide an attack and ask the student to find it here and apply the right defence (the check reuses the
attack-specific checks such as ``mitm`` or ``ddos``)."""
from __future__ import annotations

from dataclasses import dataclass

from ..i18n import tr

# severity: 1 = critical, 2 = high, 3 = medium, 4 = low / info
SEV_NAMES = {1: "критический", 2: "высокий", 3: "средний", 4: "низкий"}


def sev_name(sev: int) -> str:
    return tr(SEV_NAMES.get(sev, "низкий"))


@dataclass(frozen=True)
class Alert:
    category: str                 # ddos | scan | worm | c2 | arp-spoof | mac-flood | stp | brute | ids | syslog
    severity: int                 # 1..4
    source: str                   # device / attacker / "network"
    text: str
    advice: str = ""


@dataclass
class Event:
    ms: float
    source: str
    text: str
    kind: str                     # ddos | malware | l2 | syslog


# ---------------------------------------------------------------------------------- collection
def events(topology) -> list[Event]:
    """Merged, time-ordered security event timeline from every source."""
    out: list[Event] = []
    for mon, kind in ((getattr(topology, "ddos", None), "ddos"),
                      (getattr(topology, "malware", None), "malware"),
                      (getattr(topology, "l2mon", None), "l2")):
        for ev in getattr(mon, "events", []) or []:
            out.append(Event(ev[0], ev[1], ev[2], kind))
    for d in topology.devices.values():
        mg = getattr(d, "mgmt", None)
        buf = getattr(mg, "buffer", None) if mg is not None else None
        if buf:
            for line in list(buf)[-40:]:
                stamp, _, text = line.partition(": ")
                out.append(Event(0.0, d.name, text or stamp, "syslog"))
    return sorted(out, key=lambda e: e.ms)


# ---------------------------------------------------------------------------------- detection rules
def alerts(topology) -> list[Alert]:
    """Correlate the monitors' state into ranked alerts (most severe first)."""
    out: list[Alert] = []
    ddos = getattr(topology, "ddos", None)
    mw = getattr(topology, "malware", None)
    l2 = getattr(topology, "l2mon", None)

    # -- DDoS -----------------------------------------------------------------------------------
    if ddos is not None:
        from .security.ddos import human_bps, server_pressure
        rows = [r for r in ddos.rows() if r[4] > 0.7]
        if rows:
            link, port, bps, pps, util, kinds = rows[0]
            kindtxt = ", ".join(kinds)
            out.append(Alert("ddos", 1 if util > 1 else 2, port.device.name,
                             tr("DDoS: поток {bps} на канал {a}→{b} ({pct} %), тип: {k}", bps=human_bps(bps),
                                a=port.device.name, b=link.other(port).device.name, pct=round(util * 100), k=kindtxt),
                             tr("Фильтруйте у провайдера (scrubbing/FlowSpec/RTBH) или ограничьте на границе.")))
        for d in topology.devices.values():
            if d.category != "server":
                continue
            p = server_pressure(d)
            if p["syn"][0] > p["syn"][1] or p["http"][0] > p["http"][1] or p["conn"][0] >= p["conn"][1] > 0:
                out.append(Alert("ddos", 2, d.name, tr("Перегрузка сервера {dev} под атакой (SYN/HTTP/соединения).",
                                                       dev=d.name),
                                 tr("SYN cookies, TCP intercept, лимиты и JS-проверка на сервере.")))

    # -- malware --------------------------------------------------------------------------------
    if mw is not None:
        from .security.malware import state as mw_state
        infected = [d for d in topology.devices.values() if getattr(d, "infection", None)]
        if infected:
            out.append(Alert("worm", 1, "network",
                             tr("Заражено узлов: {n} ({names}).", n=len(infected),
                                names=", ".join(d.name for d in infected[:6])),
                             tr("Изолируйте, вылечите и закройте уязвимость; проверьте сегментацию и IPS.")))
        enc = [d for d in topology.devices.values() if mw_state(d) == "encrypted"]
        if enc:
            out.append(Alert("worm", 1, "network", tr("Шифровальщик: зашифрованы {n} узлов.", n=len(enc)),
                             tr("Восстановите из резервной копии после лечения; проверьте правило 3-2-1.")))
        st = mw.stats
        if st.get("scans", 0) > 20:
            out.append(Alert("scan", 3, "network", tr("Сканирование сети: {n} попыток эксплойтов.", n=st["scans"]),
                             tr("Признак червя: разделите сеть и включите IPS.")))
        if st.get("beacons", 0) > 0 or st.get("exfil_kb", 0) > 0:
            out.append(Alert("c2", 2, "network",
                             tr("Связь с командным сервером: утекло {kb} КБ.", kb=st.get("exfil_kb", 0)),
                             tr("Перекройте C2 (ACL/DNS sinkhole) и вылечите узлы.")))
        if st.get("ips", 0) > 0:
            out.append(Alert("ids", 4, "network", tr("IPS отбросил вредоносные пакеты: {n}.", n=st["ips"]),
                             tr("IPS работает; проверьте, не осталось ли заражённых узлов.")))

    # -- L2 (gated on currently running attacks so the alert clears when the attack stops) --------
    if l2 is not None:
        st = l2.stats
        running = {a.kind for d in l2.attackers() for a in getattr(d, "l2attacks", []) if a.running}
        if "arp-spoof" in running:
            sev = 2 if st.get("intercepted", 0) > 0 else 3
            out.append(Alert("arp-spoof", sev, "network",
                             tr("Подмена ARP: отравлено {p}, перехвачено кадров {i}.", p=st.get("poisoned", 0),
                                i=st.get("intercepted", 0)),
                             tr("Включите Dynamic ARP Inspection на коммутаторе.")))
        if "mac-flood" in running:
            out.append(Alert("mac-flood", 2, "network", tr("Переполнение MAC-таблицы коммутатора."),
                             tr("Включите port security на портах доступа.")))
        if ("stp-root" in running or st.get("bpdu_blocked", 0) == 0) and _stp_hijacked(topology):
            out.append(Alert("stp", 2, "network", tr("Захват корня STP чужим устройством."),
                             tr("Включите BPDU Guard / Root Guard на портах к устройствам.")))
        if st.get("dai_drops", 0) > 0:
            out.append(Alert("ids", 4, "network", tr("DAI отбросил поддельные ARP: {n}.", n=st["dai_drops"]),
                             tr("Защита от подмены ARP работает.")))

    sev_rank = {1: 0, 2: 1, 3: 2, 4: 3}
    return sorted(out, key=lambda al: sev_rank.get(al.severity, 9))


def _stp_hijacked(topology) -> bool:
    for d in topology.devices.values():
        stp = getattr(d, "stp", None)
        if stp is not None and getattr(stp, "enabled", False) and stp.root_id is not None \
                and stp.root_id != stp.bridge_id and stp.root_port is not None:
            peer = d.get_port(stp.root_port).peer if d.get_port(stp.root_port) else None
            if peer is not None and getattr(peer.device, "category", "") not in ("switch",):
                return True
    return False


def alert_counts(topology) -> dict:
    c = {1: 0, 2: 0, 3: 0, 4: 0}
    for al in alerts(topology):
        c[al.severity] = c.get(al.severity, 0) + 1
    return c


# ---------------------------------------------------------------------------------- attack-and-defend control
def set_all_attacks(topology, on: bool) -> int:
    """Start or stop every live attack in the topology (botnets and LAN attackers).  Returns how many."""
    n = 0
    for d in topology.devices.values():
        if d.category == "attack" and hasattr(d, "start"):
            (d.start if on else d.stop)()
            n += 1
    return n


def has_attackers(topology) -> bool:
    return any(d.category == "attack" for d in topology.devices.values())
