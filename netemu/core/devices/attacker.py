"""A LAN attacker host for layer-2 attack training (security/l2attack.py).

It is an ordinary PC (IP, MAC, one NIC) with a list of L2 attacks.  Running attacks are driven once per
simulated second by the topology's L2Monitor; frames delivered to the attacker by MITM or CAM-overflow are
counted (and relayed) in ``receive``."""
from __future__ import annotations

from ..security import l2attack
from .host import Host


class L2Attacker(Host):
    category = "attack"
    model = "LAN attacker"
    default_hostname = "Attacker"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        self.l2attacks: list = []

    # -- control --------------------------------------------------------------------------------
    def start(self, index: int | None = None) -> None:
        for i, a in enumerate(self.l2attacks):
            if index is None or i == index:
                a.running = True
        topo = getattr(self.engine, "topology", None)
        if topo is not None:
            l2attack.monitor(topo).ensure_running()
        self.engine.emit("device_changed", self)

    def stop(self, index: int | None = None) -> None:
        for i, a in enumerate(self.l2attacks):
            if index is None or i == index:
                a.running = False
        self.engine.emit("device_changed", self)

    # -- intercept MITM / flooded traffic -------------------------------------------------------
    def receive(self, frame, port) -> None:
        if l2attack.attacker_rx(self, frame, port):
            return
        super().receive(frame, port)

    # -- persistence ----------------------------------------------------------------------------
    def to_config(self) -> dict:
        cfg = super().to_config()
        l2attack.to_config(self, cfg)
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        l2attack.apply_config(self, cfg)
