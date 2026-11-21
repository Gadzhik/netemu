"""MPLS Traffic Engineering with RSVP-TE (Cisco IOS style), simplified.

* ``mpls traffic-eng tunnels`` (global and on core interfaces) plus ``ip rsvp bandwidth <kbit/s>`` make a
  link a TE link.  The TE topology is what OSPF-TE would flood: every TE link between two routers that
  both run TE, with its reservable and already reserved bandwidth.
* ``interface TunnelN`` / ``tunnel mode mpls traffic-eng`` / ``tunnel destination`` /
  ``tunnel mpls traffic-eng bandwidth`` / ``path-option N explicit name X | dynamic`` describe an LSP.
  Path options are tried in order: CSPF (shortest path by IGP cost over links with enough free
  bandwidth) for ``dynamic``; ``ip explicit-path`` hops (``next-address``) for ``explicit`` — a hop that
  is not directly connected is reached by CSPF (loose).
* Signalling (RSVP Path/Resv) is computed hop by hop when the LSP comes up: every router on the path
  allocates a label from its MPLS label space and reserves the bandwidth on the link towards the tail.
  The head end re-checks its LSPs every 2 s and re-signals over another path option when a link of
  the path fails or loses TE.  The messages themselves are not sent as packets.
* Forwarding: a packet routed into the tunnel gets the first hop's label; transit routers swap; the
  tail end pops and routes the packet (explicit labels, no penultimate-hop popping).
* ``tunnel mpls traffic-eng autoroute announce``: IGP routes whose path from the head end goes through
  the tail end use the tunnel instead.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from ipaddress import IPv4Address

TICK_MS = 2000.0


@dataclass
class Lsp:
    tunnel: str
    option: int                                 # path-option number in use
    kind: str                                   # explicit | dynamic
    hops: list                                  # [(router, out port, next hop IP, label at next router)]
    bandwidth: int
    weight: int = 0
    instance: int = 1
    up_since: float = 0.0

    @property
    def first(self):
        return self.hops[0] if self.hops else None


@dataclass
class TeState:
    router: object
    reserved: dict = field(default_factory=dict)        # out port name -> kbit/s reserved
    lfib: dict = field(default_factory=dict)            # label -> (out port | None, next hop | None, out label | None)
    lsps: dict = field(default_factory=dict)            # tunnel name -> Lsp (head end)
    owned: dict = field(default_factory=dict)           # (head name, tunnel) -> [labels allocated here]
    timer: object = None
    instances: int = 0

    # -- configuration (router.extra["te"]) ------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return self.router.extra.setdefault("te", {})

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def link_enabled(self, port) -> bool:
        return self.enabled and port.name in self.cfg.get("links", {}) and port.is_up and port.ip is not None

    def capacity(self, port) -> int:
        return int(self.cfg.get("rsvp", {}).get(port.name, 0) or 0)

    def available(self, port) -> int:
        return self.capacity(port) - self.reserved.get(port.name, 0)


def te_of(router) -> TeState:
    st = router.__dict__.get("_te")
    if st is None:
        st = router.__dict__["_te"] = TeState(router)
    return st


def te_links(router):
    """[(out port, peer router, peer port)] of usable TE links."""
    from ..devices.router import Router
    st = te_of(router)
    out = []
    for p in router.ports.values():
        if not st.link_enabled(p):
            continue
        peer = p.peer
        if peer is None or not isinstance(peer.device, Router):
            continue
        if not te_of(peer.device).link_enabled(peer):
            continue
        out.append((p, peer.device, peer))
    return out


def _cost(port) -> int:
    ospf = getattr(port.device, "ospf", None)
    try:
        return int(ospf.cost(port)) if ospf is not None else 1
    except Exception:                              # noqa: BLE001 - any OSPF without a cost helper
        return 1


def cspf(src, dst_router, bw: int):
    """Shortest path src -> dst_router over TE links with >= bw free. [(router, port, peer port)] or None."""
    dist, prev = {id(src): 0}, {}
    routers = {id(src): src}
    heap, tie = [(0, 0, id(src))], 1
    while heap:
        d, _, rid = heapq.heappop(heap)
        if d > dist.get(rid, 1 << 30):
            continue
        if rid == id(dst_router):
            break
        r = routers[rid]
        for p, nr, np_ in te_links(r):
            if te_of(r).available(p) < bw:
                continue
            nd = d + _cost(p)
            if nd < dist.get(id(nr), 1 << 30):
                dist[id(nr)] = nd
                prev[id(nr)] = (r, p, np_)
                routers[id(nr)] = nr
                heapq.heappush(heap, (nd, tie, id(nr)))
                tie += 1
    if id(dst_router) not in prev and dst_router is not src:
        return None
    path, cur = [], dst_router
    while cur is not src:
        r, p, np_ = prev[id(cur)]
        path.append((r, p, np_))
        cur = r
    return list(reversed(path))


def owner_of(start, ip: IPv4Address):
    """The router (reachable over any links from *start*) that owns *ip*."""
    from ..devices.router import Router
    seen, todo = {id(start)}, [start]
    while todo:
        r = todo.pop()
        if r.owns_ip(ip):
            return r
        for p in r.ports.values():
            peer = p.peer
            if peer is not None and isinstance(peer.device, Router) and id(peer.device) not in seen:
                seen.add(id(peer.device))
                todo.append(peer.device)
    return None


def explicit_path(head, addrs, bw: int):
    """Hops are strict ("10.0.13.2": a directly connected neighbour's address, or the current router's own)
    unless written "loose:10.0.34.2" (reached by CSPF).  A path may not visit a router twice."""
    path, cur, seen = [], head, {id(head)}
    for item in addrs:
        loose = str(item).startswith("loose:")
        a = IPv4Address(str(item).split(":", 1)[1] if loose else item)
        if cur.owns_ip(a):
            continue
        direct = next(((p, nr, np_) for p, nr, np_ in te_links(cur)
                       if np_.ip is not None and np_.ip.ip == a or (np_.device.owns_ip(a) and not loose)), None)
        if direct is not None and te_of(cur).available(direct[0]) >= bw and id(direct[1]) not in seen:
            path.append((cur, direct[0], direct[2]))
            cur = direct[1]
            seen.add(id(cur))
            continue
        if not loose:
            return None                              # strict hop not adjacent (or no bandwidth): path invalid
        target = owner_of(cur, a)
        seg = cspf(cur, target, bw) if target is not None else None
        if not seg or any(id(np_.device) in seen for _, _, np_ in seg):
            return None
        path += seg
        seen |= {id(np_.device) for _, _, np_ in seg}
        cur = target
    return path, cur


def signal(head, tunnel) -> Lsp | None:
    """Try the tunnel's path options in order; install labels and reservations on success."""
    te = tunnel.te
    dst = tunnel.destination
    if dst is None:
        return None
    tail = owner_of(head, dst)
    if tail is None or tail is head:
        return None
    bw = int(te.get("bandwidth", 0) or 0)
    paths = head.extra.get("explicit_paths", {})
    for num, kind, name in sorted(te.get("options", []), key=lambda o: int(o[0])):
        if kind == "dynamic":
            path = cspf(head, tail, bw)
        else:
            res = explicit_path(head, paths.get(name, []), bw)
            path = None
            if res is not None:
                path, end = res
                if end is not tail:
                    rest = cspf(end, tail, bw)
                    path = None if rest is None else path + rest
        if not path:
            continue
        return _install(head, tunnel, int(num), kind, path, bw)
    return None


def _install(head, tunnel, num, kind, path, bw) -> Lsp:
    st_head = te_of(head)
    st_head.instances += 1
    key = (head.name, tunnel.name)
    # labels from the tail back to the head (Resv)
    labels = []
    downstream = None                                       # (out port, next hop, out label) at the next router
    for r, p, np_ in reversed(path):
        nr = np_.device
        st = te_of(nr)
        lab = nr.mpls._next_label
        nr.mpls._next_label += 1
        st.lfib[lab] = downstream if downstream is not None else (None, None, None)       # tail: pop
        st.owned.setdefault(key, []).append(lab)
        labels.append(lab)
        downstream = (p, np_.ip.ip, lab)
        te_of(r).reserved[p.name] = te_of(r).reserved.get(p.name, 0) + bw
        r._arp_prime(p, np_.ip.ip)
    labels.reverse()
    hops = [(r, p, np_.ip.ip, labels[i]) for i, (r, p, np_) in enumerate(path)]
    weight = sum(_cost(p) for _, p, _ in path)
    lsp = Lsp(tunnel.name, num, kind, hops, bw, weight, st_head.instances, head.engine.now)
    st_head.lsps[tunnel.name] = lsp
    head.console(f"%MPLS_TE-5-TUN: Tun{tunnel.name.lower().replace('tunnel', '')}: "
                 f"installed LSP for path option {num}, {len(hops)} hops")
    return lsp


def teardown(head, tunnel_name: str) -> None:
    st = te_of(head)
    lsp = st.lsps.pop(tunnel_name, None)
    if lsp is None:
        return
    key = (head.name, tunnel_name)
    for r, p, nh, lab in lsp.hops:
        rs = te_of(r)
        rs.reserved[p.name] = max(0, rs.reserved.get(p.name, 0) - lsp.bandwidth)
        nr = p.peer.device if p.peer is not None else None
        for x in ([nr] if nr is not None else []):
            xs = te_of(x)
            for l in xs.owned.pop(key, []):
                xs.lfib.pop(l, None)


def lsp_valid(head, lsp) -> bool:
    for r, p, nh, lab in lsp.hops:
        if not te_of(r).link_enabled(p) or p.peer is None or not te_of(p.peer.device).link_enabled(p.peer):
            return False
    return True


def te_tunnels(router):
    return [p for p in router.ports.values() if getattr(p, "is_tunnel", False) and getattr(p, "tmode", "gre") == "te"]


def tick(router) -> None:
    st = te_of(router)
    st.timer = None
    for t in te_tunnels(router):
        lsp = st.lsps.get(t.name)
        if lsp is not None and (not t.admin_up or not st.enabled or not lsp_valid(router, lsp)):
            teardown(router, t.name)
            router.console(f"%MPLS_TE-5-LSP: Tunnel {t.name}: LSP torn down (path no longer valid)")
            lsp = None
        if lsp is None and t.admin_up and st.enabled:
            if signal(router, t) is not None:
                router.routes_changed()
    for name in [n for n in st.lsps if n not in {t.name for t in te_tunnels(router)}]:
        teardown(router, name)
    ensure_timer(router)


def ensure_timer(router) -> None:
    st = te_of(router)
    if st.timer is None and te_tunnels(router):
        st.timer = router.engine.schedule(TICK_MS if st.lsps else 300.0, tick, router)


def kick(router) -> None:
    st = te_of(router)
    if st.timer is not None:
        st.timer.cancelled = True
        st.timer = None
    st.timer = router.engine.schedule(50.0, tick, router)


# -- data plane ----------------------------------------------------------------------------------
def tunnel_send(tunnel, pkt) -> bool:
    from .mpls import MplsPacket
    head = tunnel.device
    lsp = te_of(head).lsps.get(tunnel.name)
    if lsp is None:
        return False
    r, p, nh, lab = lsp.first
    return head.mpls._send_labelled(p, nh, MplsPacket((lab,), pkt.ttl, pkt))


def te_switch(router, mp, ttl, port) -> bool:
    """Transit / tail handling of a TE label. True when the label was a TE label."""
    from dataclasses import replace
    from .mpls import MplsPacket
    entry = te_of(router).lfib.get(mp.labels[0])
    if entry is None:
        return False
    out_port, nh, out_label = entry
    if out_port is None:                                         # tail end: pop and route
        if len(mp.labels) > 1:
            router.mpls.receive_stack(replace(mp, labels=mp.labels[1:], ttl=ttl), port)
        else:
            router.send_ip(replace(mp.inner, ttl=ttl))
        return True
    router.mpls.switched += 1
    router.mpls._send_labelled(out_port, nh, MplsPacket((out_label,) + mp.labels[1:], ttl, mp.inner))
    return True


# -- autoroute announce ---------------------------------------------------------------------------
_walking = False


def path_routers(router, dst) -> list:
    """Routers a packet from *router* to *dst* passes (following each router's own routing table)."""
    global _walking
    from ..devices.router import Router
    out, cur = [], router
    _walking = True
    try:
        for _ in range(16):
            if cur.owns_ip(dst):
                out.append(cur)
                break
            res = cur.route_lookup(dst)
            if res is None or res[0].peer is None:
                break
            nxt = res[0].peer.device
            if not isinstance(nxt, Router):
                break
            out.append(nxt)
            cur = nxt
    finally:
        _walking = False
    return out


def autoroute(router, routes: list) -> list:
    """Move IGP routes behind an autoroute tunnel's tail end onto the tunnel."""
    from ..stack.ipv4 import Route
    if _walking:
        return routes
    st = te_of(router)
    tuns = [t for t in te_tunnels(router) if t.te.get("autoroute") and t.name in st.lsps and t.is_up]
    if not tuns:
        return routes
    out = []
    for r in routes:
        moved = False
        if r.code[:1] in ("O", "i", "D", "R") and r.port is not None and not getattr(r.port, "is_tunnel", False):
            on_path = path_routers(router, r.network.network_address if r.network.prefixlen < 32
                                   else r.network.network_address)
            for t in tuns:
                tail = st.lsps[t.name].hops[-1][1].peer.device
                if tail in on_path:
                    out.append(Route(r.network, r.code, t, None, r.distance, r.metric))
                    moved = True
                    break
        if not moved:
            out.append(r)
    return out
