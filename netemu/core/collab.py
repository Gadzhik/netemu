"""Collaboration over a LAN: keep several NetEmu instances on the same topology.

Every participant runs its own simulation; what is shared is the *project*: devices,
positions, cables, device configurations, notes and the lab.  Each side periodically takes
a snapshot, diffs it against the last state it agreed with the others and sends the
difference as small operations; received operations are applied locally.  Volatile state
(addresses leased by DHCP, learned port-security MACs) is left out of the comparison, so
two simulations running side by side do not ping-pong changes.  Concurrent edits of the
same device resolve as "last writer wins".

The transport (Qt sockets in the GUI) only moves JSON lines; this module has no I/O."""
from __future__ import annotations

import copy
import json

from .persistence import topology_to_dict


def _norm(cfg: dict) -> dict:
    """Configuration without the parts the simulation changes by itself."""
    c = copy.deepcopy(cfg or {})
    if c.get("dhcp"):
        for key in ("gateway", "dns", "dns_alt"):
            c.pop(key, None)
        for ic in c.get("interfaces", {}).values():
            ic.pop("ip", None)
            ic.pop("ipv6", None)
    for ic in c.get("interfaces", {}).values():
        ps = ic.get("psec")
        if isinstance(ps, dict):
            dyn = set(ps.get("dynamic", []))
            ps["macs"] = [m for m in ps.get("macs", []) if m not in dyn]
            for k in ("dynamic", "violations", "last"):
                ps.pop(k, None)
        ic.pop("err_disabled", None)
    return c


def snapshot(topo) -> dict:
    d = topology_to_dict(topo)
    devs = {dd["id"]: dd for dd in d["devices"]}
    links = sorted(tuple(sorted([tuple(l["a"]), tuple(l["b"])])) for l in d["links"])
    return {"devices": devs, "links": [list(map(list, l)) for l in links],
            "annotations": d.get("annotations", []), "lab": d.get("lab")}


def _key(dd):
    return json.dumps(_norm(dd.get("config")), sort_keys=True)


def diff(old: dict | None, new: dict) -> list[dict]:
    if old is None:
        return [{"op": "full", "state": new}]
    ops: list[dict] = []
    od, nd = old["devices"], new["devices"]
    for i, dd in nd.items():
        if i not in od:
            ops.append({"op": "add", "dev": dd})
            continue
        o = od[i]
        if (o["x"], o["y"]) != (dd["x"], dd["y"]):
            ops.append({"op": "move", "id": i, "x": dd["x"], "y": dd["y"]})
        if o["name"] != dd["name"]:
            ops.append({"op": "rename", "id": i, "name": dd["name"]})
        if o.get("powered", True) != dd.get("powered", True):
            ops.append({"op": "power", "id": i, "on": dd.get("powered", True)})
        if _key(o) != _key(dd) or o.get("startup") != dd.get("startup"):
            ops.append({"op": "config", "id": i, "config": dd["config"], "startup": dd.get("startup")})
    for i in od:
        if i not in nd:
            ops.append({"op": "del", "id": i})
    ol, nl = {json.dumps(x) for x in old["links"]}, {json.dumps(x) for x in new["links"]}
    for x in sorted(ol - nl):
        ops.append({"op": "unlink", "ends": json.loads(x)})
    for x in sorted(nl - ol):
        ops.append({"op": "link", "ends": json.loads(x)})
    if old.get("annotations") != new.get("annotations"):
        ops.append({"op": "notes", "annotations": new.get("annotations", [])})
    if json.dumps(old.get("lab"), sort_keys=True) != json.dumps(new.get("lab"), sort_keys=True):
        ops.append({"op": "lab", "lab": new.get("lab")})
    return ops


def _find_link(topo, ends):
    (da, pa), (db, pb) = ends
    for l in topo.links:
        e = {(l.a.device.id, l.a.name), (l.b.device.id, l.b.name)}
        if e == {(da, pa), (db, pb)}:
            return l
    return None


def apply(topo, ops: list[dict]) -> None:
    """Apply remote operations (order: removals, additions, cables, configurations)."""
    from .persistence import topology_from_dict
    eng = topo.engine
    for op in ops:
        if op["op"] == "full":
            st = op["state"]
            data = {"format": "netemu", "version": 1, "devices": list(st["devices"].values()),
                    "links": [{"a": l[0], "b": l[1]} for l in st["links"]], "annotations": st.get("annotations", [])}
            if st.get("lab"):
                data["lab"] = st["lab"]
            topology_from_dict(data, topo)
            return
    for op in ops:
        k = op["op"]
        if k == "unlink":
            l = _find_link(topo, op["ends"])
            if l is not None:
                topo.disconnect(l)
        elif k == "del" and op["id"] in topo.devices:
            topo.remove_device(topo.devices[op["id"]])
    added = []
    for op in ops:
        if op["op"] == "add" and op["dev"]["id"] not in topo.devices:
            dd = op["dev"]
            dev = topo.add_device(dd["model"], dd["name"], dd.get("x", 0), dd.get("y", 0), device_id=dd["id"])
            dev.powered = dd.get("powered", True)
            dev.startup_config = dd.get("startup")
            added.append((dev, dd.get("config", {})))
    for op in ops:
        if op["op"] == "link":
            (da, pa), (db, pb) = op["ends"]
            a, b = topo.devices.get(da), topo.devices.get(db)
            if a is None or b is None or _find_link(topo, op["ends"]) is not None:
                continue
            pa_, pb_ = a.ports.get(pa), b.ports.get(pb)
            if pa_ is not None and pb_ is not None and pa_.link is None and pb_.link is None:
                topo.connect(pa_, pb_)
    for dev, cfg in added:
        dev.apply_config(cfg)
        eng.emit("device_changed", dev)
    for op in ops:
        k = op["op"]
        dev = topo.devices.get(op.get("id"))
        if k == "move" and dev is not None:
            dev.x, dev.y = op["x"], op["y"]
            eng.emit("device_moved", dev)
        elif k == "rename" and dev is not None:
            topo.rename(dev, op["name"])
        elif k == "power" and dev is not None:
            dev.powered = op["on"]
            for p in dev.ports.values():
                dev.link_state_changed(p)
            eng.emit("device_changed", dev)
        elif k == "config" and dev is not None:
            dev.apply_config(op["config"])
            dev.startup_config = op.get("startup")
            eng.emit("device_changed", dev)
        elif k == "notes":
            topo.annotations = [dict(a) for a in op["annotations"]]
            eng.emit("annotations_changed", topo)
        elif k == "lab":
            from .lab import Lab
            topo.lab = Lab.from_dict(op["lab"])
            eng.emit("lab_changed", topo)
    if any(op["op"] in ("add", "link", "unlink", "del") for op in ops):
        from .devices.wireless import rescan
        rescan(topo)


class SyncPeer:
    """One participant: ``local_ops()`` -> ops to send, ``receive(ops)`` -> apply remote ops."""

    def __init__(self, topo):
        self.topo = topo
        self.last: dict | None = None

    def start_from_local(self) -> list[dict]:
        self.last = snapshot(self.topo)
        return [{"op": "full", "state": self.last}]

    def local_ops(self) -> list[dict]:
        if self.last is None:
            return []
        now = snapshot(self.topo)
        ops = diff(self.last, now)
        self.last = now
        return ops

    def receive(self, ops: list[dict]) -> list[dict]:
        """Apply remote ops; returns our own edits made since the last tick (to send right after)."""
        pending = self.local_ops() if self.last is not None else []
        apply(self.topo, ops)
        self.last = snapshot(self.topo)
        return pending


def encode(ops: list[dict], sender: str = "") -> bytes:
    return (json.dumps({"from": sender, "ops": ops}, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> tuple[str, list[dict]]:
    d = json.loads(line.decode("utf-8"))
    return d.get("from", ""), d.get("ops", [])
