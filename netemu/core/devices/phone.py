"""IP phone (Cisco 7960 style) and the call manager it registers with (Cisco CME).

The phone has two ports: *Switch* (uplink) and *PC*, a pass-through for a computer, so one
cable from the wall serves both.  On link-up it learns the voice VLAN from the switch port
(what CDP / LLDP-MED do on real gear), tags its own frames with it and asks for an address
by DHCP inside that VLAN.  Untagged frames on the uplink belong to the PC behind it.

Registration and call signalling use SCCP (TCP 2000) to the CME router: the address comes
from DHCP option 150, falling back to the default gateway.  Once a call is connected the
two phones send each other RTP (UDP 16384, DSCP EF) directly; ``rtp_rx`` counts what
arrived, so QoS and ACLs on the path are visible.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from ..port import Port
from .host import Host

SCCP_PORT, RTP_PORT = 2000, 16384
RTP_INTERVAL_MS = 500.0
_calls = itertools.count(1)


@dataclass(frozen=True)
class SccpMsg:
    kind: str                     # register | register_ack | register_reject | call | ring | answer |
    #                               connected | hangup | busy | unknown
    mac: str = ""
    number: str = ""              # own DN (ack) / dialled number (call) / caller (ring)
    call_id: int = 0
    peer_ip: str = ""
    label = "SCCP"

    def summary(self) -> str:
        return f"{self.kind} {self.number}".strip()

    def layer_lines(self) -> list[str]:
        out = [f"Message: {self.kind}"]
        if self.mac:
            out.append(f"Device: SEP{self.mac}")
        if self.number:
            out.append(f"Directory number: {self.number}")
        if self.call_id:
            out.append(f"Call ID: {self.call_id}")
        return out


@dataclass(frozen=True)
class RtpPacket:
    call_id: int
    seq: int
    label = "RTP"
    size = 160                    # G.711, 20 ms of voice

    def summary(self) -> str:
        return f"PT=ITU-T G.711 PCMU, SSRC=0x{self.call_id:08X}, Seq={self.seq}"

    def layer_lines(self) -> list[str]:
        return ["Payload type: G.711 PCMU (0)", f"Sequence number: {self.seq}", f"SSRC: 0x{self.call_id:08X}"]


class _UplinkPort(Port):
    """Tags the phone's own frames with the voice VLAN; PC traffic passes untagged."""

    def send(self, frame) -> bool:
        vv = self.device.voice_vlan
        if vv is not None and frame.vlan is None and frame.src == self.mac:
            frame = replace(frame, vlan=vv)
        return super().send(frame)


class IPPhone(Host):
    category = "phone"
    model = "IP Phone 7960"
    default_hostname = "IPPhone"
    default_ttl = 64
    NIC = ("Switch", "Switch")
    os = "phone"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        old = self.nic
        up = _UplinkPort(self, old.name, old.short)
        up.mac = old.mac
        up.ipv6_enabled, up._slaac = False, set()
        self.ports[old.name] = up
        self.nic = up
        self.pc_port = self.add_port("PC", "PC")
        self.voice_vlan: int | None = None
        self.dhcp_enabled = True                      # phones always use DHCP
        self.cme = None                               # call manager address
        self.number: str | None = None                # directory number after registration
        self.state = "unregistered"                   # unregistered | idle | dialing | ringing | ringback | connected
        self.call: dict | None = None                 # {"id", "peer", "peer_ip", "seq"}
        self.rtp_rx = 0
        self.rtp_tx = 0
        self.history: list[str] = []
        self._rtp_timer = None
        self._sport = 51000 + int(up.mac.value) % 1000
        self.bind_tcp(self._sport, self._sccp_in)
        self.bind_udp(RTP_PORT, self._rtp_in)

    # -- L2: built-in 3-port switch ---------------------------------------------------------
    def receive(self, frame, port: Port) -> None:
        if port is self.pc_port:
            if self.nic.is_up:
                self.nic.send(frame)                 # PC -> wall, untagged (access VLAN)
            return
        vv = self.voice_vlan
        mine = frame.vlan == vv if vv is not None else frame.vlan is None
        if frame.vlan is None and self.pc_port.is_up and frame.dst != self.nic.mac:
            self.pc_port.send(frame)
        if mine:
            self.handle_l3_frame(replace(frame, vlan=None) if frame.vlan is not None else frame, port)

    def link_state_changed(self, port) -> None:
        if port is self.pc_port:
            return
        if port.is_up:
            self.engine.schedule(100.0, self._discover)
        else:
            self._unregister("link down")

    def _discover(self) -> None:
        """CDP / LLDP-MED: ask the switch which VLAN carries voice, then DHCP."""
        peer = self.nic.peer
        vv = getattr(peer, "voice_vlan", None) if peer is not None else None
        if vv is None and peer is not None:               # RouterOS: one LLDP-MED network policy per device
            vv = (getattr(peer.device, "extra", {}) or {}).get("lldp_med_vlan")
        if vv != self.voice_vlan:
            self.voice_vlan = vv
            self.nic.ip, self.gateway = None, None
        self.engine.emit("device_changed", self)
        if self.nic.is_up and (self.nic.ip is None or self.dhcp_info.get("apipa")):
            self.start_dhcp()

    def dhcp_bound(self, result, msg) -> None:
        super().dhcp_bound(result, msg)
        tftp = getattr(msg, "tftp", None)
        self.cme = tftp or (str(self.gateway) if self.gateway else None)
        self.engine.schedule(200.0, self.register)

    def dhcp_failed(self) -> None:
        self.state = "unregistered"
        self.engine.emit("device_changed", self)

    # -- SCCP ------------------------------------------------------------------------------
    def _send(self, msg: SccpMsg) -> bool:
        if self.cme is None or self.nic.ip is None:
            return False
        return self.send_tcp(self.cme, self._sport, SCCP_PORT, msg)

    def register(self) -> None:
        if self.state != "unregistered":
            return
        self._send(SccpMsg("register", mac=self.nic.mac.cisco().replace(".", "").upper()))
        self.engine.schedule(5000.0, self._retry_register)

    def _retry_register(self) -> None:
        if self.state == "unregistered" and self.nic.is_up and self.nic.ip is not None:
            self.register()

    def _unregister(self, why: str) -> None:
        self._stop_rtp()
        self.state, self.number, self.call = "unregistered", None, None
        self.engine.emit("device_changed", self)

    def dial(self, number: str) -> str:
        if self.state != "idle":
            return "busy" if self.state != "unregistered" else "not registered"
        self.state = "dialing"
        self.call = {"id": 0, "peer": number, "peer_ip": "", "seq": 0}
        self._send(SccpMsg("call", number=number))
        self.engine.emit("device_changed", self)
        return ""

    def answer(self) -> bool:
        if self.state != "ringing" or self.call is None:
            return False
        self._send(SccpMsg("answer", call_id=self.call["id"]))
        return True

    def hangup(self) -> None:
        if self.call is not None and self.state in ("dialing", "ringing", "ringback", "connected"):
            self._send(SccpMsg("hangup", call_id=self.call["id"]))
        self._end("ended")

    def _end(self, how: str) -> None:
        if self.call is not None:
            self.history.append(f"{self.call['peer']}: {how}")
        self._stop_rtp()
        self.call = None
        if self.state != "unregistered":
            self.state = "idle"
        self.engine.emit("device_changed", self)

    def _sccp_in(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, SccpMsg):
            return
        if m.kind == "register_ack":
            self.state, self.number = "idle", m.number or None
        elif m.kind == "register_reject":
            self.state = "unregistered"
        elif m.kind == "ring" and self.state == "idle":
            self.state = "ringing"
            self.call = {"id": m.call_id, "peer": m.number, "peer_ip": "", "seq": 0}
        elif m.kind == "ring":
            self._send(SccpMsg("busy", call_id=m.call_id))
        elif m.kind == "ringback" and self.call is not None:
            self.state, self.call["id"] = "ringback", m.call_id
        elif m.kind == "connected" and self.call is not None:
            self.state, self.call["peer_ip"] = "connected", m.peer_ip
            self.call["id"] = m.call_id
            self._start_rtp()
        elif m.kind in ("hangup", "busy", "unknown") and self.call is not None:
            self._end({"hangup": "ended", "busy": "busy", "unknown": "unknown number"}[m.kind])
            return
        self.engine.emit("device_changed", self)

    # -- RTP -------------------------------------------------------------------------------
    def _start_rtp(self) -> None:
        self._stop_rtp()
        self._rtp_tick()

    def _rtp_tick(self) -> None:
        c = self.call
        if c is None or self.state != "connected" or not c["peer_ip"]:
            return
        from ..packets import IPv4Packet, UdpDatagram
        from ..addresses import IPv4Address
        c["seq"] += 1
        dst = IPv4Address(c["peer_ip"])
        r = self.route_lookup(dst)
        if r is not None and self.nic.ip is not None:
            pkt = IPv4Packet(self.nic.ip.ip, dst, 64, UdpDatagram(RTP_PORT, RTP_PORT, RtpPacket(c["id"], c["seq"])),
                             17, next(self._ip_ident), dscp=46)
            if self.send_ip(pkt):
                self.rtp_tx += 1
        self._rtp_timer = self.engine.schedule(RTP_INTERVAL_MS, self._rtp_tick)

    def _stop_rtp(self) -> None:
        if self._rtp_timer is not None:
            self._rtp_timer.cancelled = True
            self._rtp_timer = None

    def _rtp_in(self, pkt, port) -> None:
        m = pkt.payload.data
        if isinstance(m, RtpPacket) and self.call is not None and m.call_id == self.call["id"]:
            self.rtp_rx += 1

    # -- persistence -----------------------------------------------------------------------
    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["dhcp"] = True
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.dhcp_enabled = True

    def status_lines(self) -> list[str]:
        st = {"unregistered": "Не зарегистрирован", "idle": "Готов", "dialing": "Набор…", "ringing": "Входящий вызов",
              "ringback": "Вызов…", "connected": "Разговор"}[self.state]
        return [f"DN: {self.number or '—'}", f"State: {st}", f"Voice VLAN: {self.voice_vlan or '—'}",
                f"IP: {self.nic.ip or '—'}", f"CME: {self.cme or '—'}"]


# ------------------------------------------------------------------------------------ CME (router)
class CallManager:
    """Cisco Unified CME: ephones, ephone-dns, auto-assign, call switching."""

    def __init__(self, dev):
        self.dev = dev
        self.enabled = False
        self.max_ephones = 0
        self.max_dn = 0
        self.source: str | None = None
        self.auto_assign: tuple[int, int] | None = None
        self.dns: dict[int, str] = {}                 # ephone-dn tag -> number
        self.ephones: dict[int, dict] = {}            # tag -> {"mac", "button": dn tag or None}
        self.registered: dict[str, dict] = {}         # mac -> {"ip", "sport", "tag"}
        self.calls: dict[int, dict] = {}
        self._bound = False

    # -- persistence
    def to_dict(self) -> dict | None:
        if not (self.enabled or self.dns or self.ephones):
            return None
        return {"enabled": self.enabled, "max_ephones": self.max_ephones, "max_dn": self.max_dn,
                "source": self.source, "auto_assign": list(self.auto_assign) if self.auto_assign else None,
                "dns": {str(k): v for k, v in self.dns.items()},
                "ephones": {str(k): {"mac": v.get("mac"), "button": v.get("button")} for k, v in self.ephones.items()}}

    def apply(self, d: dict | None) -> None:
        d = d or {}
        self.enabled = bool(d.get("enabled"))
        self.max_ephones, self.max_dn = d.get("max_ephones", 0), d.get("max_dn", 0)
        self.source = d.get("source")
        self.auto_assign = tuple(d["auto_assign"]) if d.get("auto_assign") else None
        self.dns = {int(k): v for k, v in d.get("dns", {}).items()}
        self.ephones = {int(k): dict(v) for k, v in d.get("ephones", {}).items()}
        self.registered.clear()
        self.calls.clear()
        self.ensure()

    def ensure(self) -> None:
        if self.enabled and not self._bound:
            self.dev.bind_tcp(SCCP_PORT, self._in)
            self._bound = True
        elif not self.enabled and self._bound:
            self.dev.unbind_tcp(SCCP_PORT)
            self._bound = False

    # -- helpers
    def number_of(self, mac: str) -> str | None:
        tag = next((t for t, e in self.ephones.items() if e.get("mac") == mac), None)
        if tag is None:
            return None
        dn = self.ephones[tag].get("button")
        return self.dns.get(dn) if dn is not None else None

    def _reply(self, ip, sport, msg) -> None:
        self.dev.send_tcp(ip, SCCP_PORT, sport, msg)

    def _phone_by_number(self, number):
        for mac, r in self.registered.items():
            if self.number_of(mac) == number:
                return mac, r
        return None, None

    # -- SCCP
    def _in(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, SccpMsg) or not self.enabled:
            return
        ip, sport = pkt.src, pkt.payload.sport
        if m.kind == "register":
            self._register(m.mac, ip, sport)
            return
        caller = next((mac for mac, r in self.registered.items() if r["ip"] == ip and r["sport"] == sport), None)
        if caller is None:
            return
        if m.kind == "call":
            callee, r = self._phone_by_number(m.number)
            if callee is None:
                self._reply(ip, sport, SccpMsg("unknown", number=m.number))
                return
            cid = next(_calls)
            self.calls[cid] = {"a": caller, "b": callee, "state": "ringing"}
            self._reply(ip, sport, SccpMsg("ringback", number=m.number, call_id=cid))
            self._reply(r["ip"], r["sport"], SccpMsg("ring", number=self.number_of(caller) or "", call_id=cid))
        elif m.kind == "answer":
            c = self.calls.get(m.call_id)
            if c is None:
                return
            c["state"] = "connected"
            a, b = self.registered.get(c["a"]), self.registered.get(c["b"])
            if a and b:
                self._reply(a["ip"], a["sport"], SccpMsg("connected", call_id=m.call_id, peer_ip=str(b["ip"])))
                self._reply(b["ip"], b["sport"], SccpMsg("connected", call_id=m.call_id, peer_ip=str(a["ip"])))
        elif m.kind in ("hangup", "busy"):
            c = self.calls.pop(m.call_id, None)
            if c is None:
                return
            other = c["b"] if caller == c["a"] else c["a"]
            r = self.registered.get(other)
            if r:
                self._reply(r["ip"], r["sport"], SccpMsg(m.kind, call_id=m.call_id))

    def _register(self, mac, ip, sport) -> None:
        if self.source is None:
            self._reply(ip, sport, SccpMsg("register_reject", mac=mac))
            return
        tag = next((t for t, e in self.ephones.items() if e.get("mac") == mac), None)
        if tag is None:
            if len(self.ephones) >= self.max_ephones:
                self._reply(ip, sport, SccpMsg("register_reject", mac=mac))
                return
            tag = next(t for t in itertools.count(1) if t not in self.ephones)
            self.ephones[tag] = {"mac": mac, "button": None}
        e = self.ephones[tag]
        if e.get("button") is None and self.auto_assign:
            used = {x.get("button") for x in self.ephones.values()}
            lo, hi = self.auto_assign
            e["button"] = next((t for t in range(lo, hi + 1) if t in self.dns and t not in used), None)
        self.registered[mac] = {"ip": ip, "sport": sport, "tag": tag}
        self.dev.mgmt.log(6, f"%IPPHONE-6-REGISTER: ephone-{tag} IP:{ip} Socket:1 DeviceType:Phone has registered.")
        self._reply(ip, sport, SccpMsg("register_ack", mac=mac, number=self.number_of(mac) or ""))
