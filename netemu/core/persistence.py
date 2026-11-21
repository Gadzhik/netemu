"""Save / load projects as JSON (*.netemu).

Device configuration is stored in a vendor-neutral form (interfaces, IPs,
routes, ...), so the same file format works for every vendor's CLI dialect.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .topology import Topology

FORMAT = "netemu"
VERSION = 1


def topology_to_dict(topo: Topology) -> dict[str, Any]:
    devices = []
    for d in topo.devices.values():
        devices.append({
            "id": d.id, "model": d.model_key, "name": d.name,
            "x": round(d.x, 1), "y": round(d.y, 1), "powered": d.powered, "location": getattr(d, "location", ""),
            "config": d.to_config(), "startup": d.startup_config,
        })
    links = []
    for l in topo.links:
        if getattr(l, "wireless", False):
            continue
        ld = {"a": [l.a.device.id, l.a.name], "b": [l.b.device.id, l.b.name]}
        if l.media != "copper":
            ld["media"] = l.media
        if l.length_m is not None:
            ld["length"] = l.length_m
        links.append(ld)
    data = {"format": FORMAT, "version": VERSION, "devices": devices, "links": links}
    if getattr(topo, "lab", None) is not None:
        data["lab"] = topo.lab.to_dict()
    if getattr(topo, "annotations", None):
        data["annotations"] = [dict(a) for a in topo.annotations]
    return data


def topology_from_dict(data: dict[str, Any], topo: Topology | None = None) -> Topology:
    if data.get("format") != FORMAT:
        raise ValueError("not a NetEmu project file")
    if int(data.get("version", 0)) > VERSION:
        raise ValueError("project was saved by a newer NetEmu version")
    topo = topo or Topology()
    topo.clear()
    for dd in data.get("devices", []):
        dev = topo.add_device(dd["model"], dd["name"], dd.get("x", 0), dd.get("y", 0),
                              device_id=dd["id"])
        dev.powered = dd.get("powered", True)
        dev.startup_config = dd.get("startup")
        dev.location = dd.get("location", "")
    for ld in data.get("links", []):
        a = topo.devices[ld["a"][0]].ports[ld["a"][1]]
        b = topo.devices[ld["b"][0]].ports[ld["b"][1]]
        link = topo.connect(a, b)
        if ld.get("media") or ld.get("length") is not None:
            link.media = ld.get("media", "copper")
            link.length_m = ld.get("length")
    for dd in data.get("devices", []):
        topo.devices[dd["id"]].apply_config(dd.get("config", {}))
    topo.sync_names()
    from .devices.wireless import rescan
    rescan(topo)
    from .lab import Lab
    topo.lab = Lab.from_dict(data.get("lab"))
    topo.annotations = [dict(a) for a in data.get("annotations", [])]
    return topo


def save(topo: Topology, path: str | Path) -> None:
    Path(path).write_text(json.dumps(topology_to_dict(topo), indent=2, ensure_ascii=False),
                          encoding="utf-8")


def load(path: str | Path, topo: Topology | None = None) -> Topology:
    return topology_from_dict(json.loads(Path(path).read_text(encoding="utf-8")), topo)
