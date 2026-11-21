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
    if k == "phone":
        return [tr("Телефон не зарегистрирован: voice VLAN на порту, DHCP с опцией 150 и telephony-service на CME.")]
    return []
