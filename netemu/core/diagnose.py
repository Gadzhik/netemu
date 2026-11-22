"""Hints for failed lab checks: walk the path a packet would take and name the likely cause.

Each hint is a sentence in the interface language (``netemu.i18n.tr``).  The diagnosis looks at
configuration and state only (addresses, gateways, interface status, routing tables, ARP,
access lists); it never changes anything."""
from __future__ import annotations

from ipaddress import IPv4Address, ip_address

from ..i18n import tr


def _device(topology, name):
    name = name.lower()
    return next((d for d in topology.devices.values() if d.name.lower() == name or d.hostname.lower() == name), None)


def _owner(topology, ip):
    for d in topology.devices.values():
        for p in d.ports.values():
            if p.ip is not None and p.ip.ip == ip:
                return d, p
    return None, None


def _host_hints(dev, dst, role) -> list[str]:
    out = []
    nic = getattr(dev, "nic", None)
    if nic is None:
        return out
    if not nic.is_up:
        peer = nic.peer if nic.link is not None else None
        if peer is not None and not peer.admin_up:
            out.append(tr("{r}: интерфейс {p}, к которому подключён {dev}, выключен (shutdown).",
                          r=peer.device.name, p=peer.name, dev=dev.name))
        else:
            out.append(tr("{dev}: сетевой интерфейс не подключён или выключен.", dev=dev.name))
        return out
    if nic.ip is None:
        out.append(tr("{dev}: нет IP-адреса — задайте его вручную или включите DHCP.", dev=dev.name)
                   if role == "src" else tr("{dev} (получатель): нет IP-адреса.", dev=dev.name))
        return out
    if dst is not None and dst not in nic.ip.network:
        gw = getattr(dev, "gateway", None)
        if gw is None:
            out.append(tr("{dev}: не задан шлюз по умолчанию, а {dst} в другой сети.", dev=dev.name, dst=dst))
        elif gw not in nic.ip.network:
            out.append(tr("{dev}: шлюз {gw} не входит в его подсеть {net}.", dev=dev.name, gw=gw, net=nic.ip.network))
    return out


def ping_hints(topology, src_name: str, target: str) -> list[str]:
    src = _device(topology, src_name)
    if src is None:
        return [tr("Устройство {name} не найдено.", name=src_name)]
    try:
        dst = ip_address(target)
    except ValueError:
        return []
    if not isinstance(dst, IPv4Address):
        return []
    hints = _host_hints(src, dst, "src") if getattr(src, "category", "") in ("pc", "server", "iot") else []
    if hints:
        return hints
    owner, oport = _owner(topology, dst)
    if owner is None:
        hints.append(tr("Адрес {dst} не настроен ни на одном устройстве.", dst=dst))
    elif owner.category in ("pc", "server", "iot"):
        back = getattr(src, "nic", None)
        src_ip = back.ip.ip if back is not None and back.ip is not None else None
        hints += _host_hints(owner, src_ip, "dst")
    # walk the routers on the way
    seen = set()
    hop = src
    hop_ip = getattr(src, "gateway", None) if getattr(src, "category", "") in ("pc", "server", "iot") else None
    if hop_ip is not None:
        nic = src.nic
        if src.arp.lookup(hop_ip, src.engine.now) is None and nic.ip is not None and hop_ip in nic.ip.network:
            r, rp = _owner(topology, hop_ip)
            if r is None:
                hints.append(tr("Шлюз {gw} не настроен ни на одном маршрутизаторе.", gw=hop_ip))
                return hints
            if rp is not None and not rp.is_up:
                hints.append(tr("{r}: интерфейс {p} со шлюзом {gw} выключен (shutdown) или не подключён.",
                                r=r.name, p=rp.name, gw=hop_ip))
                return hints
            hints.append(tr("{dev} не получил ARP-ответ от шлюза {gw}: проверьте кабели, VLAN и транк до {r}.",
                            dev=src.name, gw=hop_ip, r=r.name))
        hop, _ = _owner(topology, hop_ip)
    for _ in range(12):
        if hop is None or hop.category not in ("router", "switch") or hop.id in seen:
            break
        seen.add(hop.id)
        for p in hop.ports.values():
            for attr in ("acl_in", "acl_out"):
                if getattr(p, attr, None):
                    hints.append(tr("{r}: на интерфейсе {p} действует список доступа {acl} — проверьте его правила.",
                                    r=hop.name, p=p.name, acl=getattr(p, attr)))
        if hop.owns_ip(dst):
            break
        r = hop.route_lookup(dst) if hasattr(hop, "route_lookup") else None
        if r is None:
            hints.append(tr("{r}: нет маршрута к {dst} — добавьте статический маршрут или настройте маршрутизацию.",
                            r=hop.name, dst=dst))
            break
        port, nh = r
        if port.ip is not None and dst in port.ip.network:
            break
        hop, _ = _owner(topology, nh)
    if not hints:
        hints.append(tr("Путь выглядит настроенным: проверьте обратный маршрут от {dst} и ACL/межсетевые экраны.", dst=dst))
    return hints


def hints_for(topology, check) -> list[str]:
    k, a = check.kind, check.args
    if k == "ping":
        return ping_hints(topology, a[0], a[1])
    if k == "noping":
        return [tr("{dst} всё ещё доступен: нужен ACL или правило межсетевого экрана на пути.", dst=a[1])]
    if k == "vlan":
        return [tr("Создайте VLAN {v} на {dev} (команда vlan {v}).", v=a[1], dev=a[0])]
    if k == "access":
        return [tr("Переведите порт {p} в режим access во VLAN {v}.", p=a[1], v=a[2])]
    if k == "dhcp":
        return [tr("{dev} не получил адрес: включите на нём DHCP, проверьте пул (сеть, шлюз), исключения и "
                   "ip helper-address, если сервер в другой сети.", dev=a[0])]
    if k in ("ospf", "ospf6"):
        return [tr("Соседства нет: интерфейсы должны быть включены, в одной подсети и в одной зоне OSPF.")]
    if k == "bgp":
        return [tr("Сессия BGP не установлена: проверьте адрес соседа, remote-as и связность до него.")]
    if k == "route":
        return [tr("Маршрута нет: объявите сеть в протоколе маршрутизации или добавьте статический маршрут.")]
    if k == "hostname":
        return [tr("Задайте имя командой hostname {name}.", name=a[1])]
    if k == "nat":
        return [tr("Трансляций нет: отметьте inside/outside интерфейсы и правило NAT, затем отправьте трафик наружу.")]
    if k == "stproot":
        return [tr("Уменьшите приоритет STP на {dev} (spanning-tree vlan 1 priority 4096).", dev=a[0])]
    if k == "fhrp":
        return [tr("Проверьте группу и виртуальный адрес HSRP/VRRP, приоритет и preempt.")]
    if k == "wifi":
        return [tr("Проверьте SSID, пароль WPA2 и расстояние до точки доступа.")]
    if k == "channel":
        return [tr("Порты должны быть в одном channel-group с совместимыми режимами (active/passive) с обеих сторон.")]
    if k in ("clean", "immune", "outbreak", "c2blocked"):
        return malware_hints(topology, k, a)
    if k in ("available", "ddos"):
        return ddos_hints(topology, a[0] if k == "ddos" else None)
    if k == "phone":
        return [tr("Телефон не зарегистрирован: voice VLAN на порту, DHCP с опцией 150 и telephony-service на CME.")]
    return []


def ddos_hints(topology, device_name=None) -> list[str]:
    """Where a DDoS hurts: overloaded cables, exhausted server resources, the attack kinds."""
    from .security.ddos import TITLES, human_bps, monitor, server_pressure
    m = monitor(topology)
    out = []
    rows = [r for r in m.rows() if r[4] > 1.0]
    if rows:
        link, port, bps, pps, util, kinds = rows[0]
        other = link.other(port)
        out.append(tr("Кабель {a} → {b} перегружен ({pct} %, {bps}): фильтр после этого кабеля его не освободит — "
                      "нужна защита у провайдера (очистка трафика, FlowSpec, RTBH) или фильтр до кабеля.",
                      a=port.device.name, b=other.device.name, pct=round(util * 100), bps=human_bps(bps)))
    for d in topology.devices.values():
        if d.category != "server":
            continue
        p = server_pressure(d)
        if p["syn"][0] > p["syn"][1]:
            out.append(tr("{dev}: очередь SYN переполнена — включите SYN cookies или TCP intercept перед сервером.",
                          dev=d.name))
        if p["http"][0] > p["http"][1]:
            out.append(tr("{dev}: {rps} запросов/с при производительности {cap} — лимит на IP, JS-проверка или WAF.",
                          dev=d.name, rps=round(p["http"][0]), cap=p["http"][1]))
        if p["conn"][0] >= p["conn"][1]:
            out.append(tr("{dev}: все слоты соединений заняты (медленная атака) — лимит соединений с одного IP.",
                          dev=d.name))
    kinds = sorted({k for r in m.rows() for k in r[5]})
    if kinds and not out:
        out.append(tr("Идут атаки: {kinds}. Откройте «Инструменты → Нагрузка и DDoS».",
                      kinds=", ".join(TITLES.get(k, k) for k in kinds)))
    if device_name and not out:
        out.append(tr("Атакующий трафик всё ещё доходит до {dev}: найдите на пути место, где его можно отбросить.",
                      dev=device_name))
    return out


def malware_hints(topology, k, a) -> list[str]:
    from .security import malware as mw
    dev = topology.find(a[0])
    if k == "c2blocked":
        return [tr("{dev} всё ещё связывается с командным сервером {c2}: запретите исходящий трафик к нему (ACL, "
                   "межсетевой экран) или подмените имя на своём DNS (sinkhole).", dev=a[0], c2=mw.C2_NAME)]
    if k == "outbreak":
        return [tr("Червь из {dev} добирается до защищаемых узлов: разделите сеть (ACL/межсетевой экран между "
                   "сегментами, запрет TCP 445), включите IPS или закройте уязвимость на самих узлах.", dev=a[0])]
    if dev is None:
        return []
    if k == "immune":
        fam = mw.FAMILIES.get(a[1])
        if fam is None:
            return []
        if fam.vuln == "default-password":
            return [tr("{dev}: смените заводской пароль или отключите Telnet.", dev=dev.name)]
        return [tr("{dev} уязвим: установите обновление {kb} ({vuln}), отключите службу (TCP {port}), закройте порт "
                   "межсетевым экраном хоста или обновите базы антивируса.", dev=dev.name,
                   kb=mw.PATCHES.get(fam.vuln, ("—",))[0], vuln=fam.vuln, port=fam.port)]
    st = mw.state(dev)
    if st == "encrypted":
        return [tr("{dev}: файлы зашифрованы — восстановите их из резервной копии (wbadmin start recovery) после "
                   "лечения.", dev=dev.name)]
    inf = getattr(dev, "infection", None)
    if inf:
        fam = mw.FAMILIES[inf["family"]]
        if fam.memory_only:
            return [tr("{dev}: вредонос живёт в памяти — перезагрузите устройство, но сначала смените пароль, "
                       "иначе оно заразится снова.", dev=dev.name)]
        s = mw.sec(dev)
        if s["av_sigs"] < fam.sig:
            return [tr("{dev}: антивирус не знает {fam} — обновите базы (MpCmdRun -SignatureUpdate) и проверьте "
                       "компьютер (MpCmdRun -Scan -ScanType 1).", dev=dev.name, fam=mw.name(fam.key))]
        return [tr("{dev}: запустите проверку антивирусом и закройте уязвимость, иначе соседи заразят его снова.",
                   dev=dev.name)]
    return []
