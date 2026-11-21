from netemu.core.addresses import IPv4Address
from netemu.core.stack.icmp import PingListener, PingSession
from netemu.core.topology import Topology


class Collect(PingListener):
    def __init__(self):
        self.events = []

    def on_reply(self, seq, src, rtt, ttl, size):
        self.events.append(("reply", seq, str(src), ttl))

    def on_timeout(self, seq):
        self.events.append(("timeout", seq))

    def on_error(self, seq, kind, src):
        self.events.append((kind, seq, str(src)))

    def on_send_fail(self, seq):
        self.events.append(("fail", seq))


def ping(dev, dst, count=4):
    c = Collect()
    s = PingSession(dev, IPv4Address(dst), c, count=count).start()
    dev.engine.run_until(lambda: s.done)
    return s, c


def test_two_pcs_through_switch():
    t = Topology()
    sw = t.add_device("cisco-2960")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, sw.get_port("fa0/1"))
    t.connect(b.nic, sw.get_port("fa0/2"))
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    s, c = ping(a, "10.0.0.2")
    assert s.stats.received == 4, c.events
    assert len(sw.mac_table) == 2
    assert b.arp.lookup(IPv4Address("10.0.0.1"), t.engine.now) is not None
