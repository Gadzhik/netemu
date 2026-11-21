"""Packet capture: frames serialize to valid wire formats and a libpcap file."""
import struct

from netemu.core.pcap import frame_bytes, write_pcap
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def capture_ping():
    t = Topology()
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, b.nic)
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    frames = []
    t.engine.subscribe("frame_delivered", lambda ev: frames.append((ev.time, ev.info["frame"])))
    assert _ping(None, a, "10.0.0.2").received == 4
    return frames


def _ones_sum(data):
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s


def test_arp_and_icmp_bytes():
    frames = capture_ping()
    kinds = [f.protocol for _, f in frames]
    assert "ARP" in kinds and kinds.count("ICMP") == 8
    arp = frame_bytes(next(f for _, f in frames if f.protocol == "ARP"))
    assert arp[12:14] == b"\x08\x06" and arp[20:22] == b"\x00\x01"                # ARP request
    icmp = frame_bytes(next(f for _, f in frames if f.protocol == "ICMP"))
    assert icmp[12:14] == b"\x08\x00" and icmp[14] == 0x45
    ip_hdr = icmp[14:34]
    assert _ones_sum(ip_hdr) == 0xFFFF                                           # IPv4 checksum is valid
    total = struct.unpack("!H", ip_hdr[2:4])[0]
    assert _ones_sum(icmp[34:14 + total]) == 0xFFFF and icmp[34] == 8            # ICMP echo request, checksum ok
    assert ip_hdr[12:16] == bytes([10, 0, 0, 1]) and ip_hdr[16:20] == bytes([10, 0, 0, 2])


def test_pcap_file(tmp_path):
    frames = capture_ping()
    p = tmp_path / "c.pcap"
    n = write_pcap(p, frames)
    data = p.read_bytes()
    magic, vmaj, vmin, _, _, snap, link = struct.unpack("<IHHiIII", data[:24])
    assert magic == 0xA1B2C3D4 and (vmaj, vmin) == (2, 4) and link == 1 and n == len(frames)
    off, count = 24, 0
    while off < len(data):
        _, _, incl, orig = struct.unpack("<IIII", data[off:off + 16])
        assert incl == orig >= 60
        off += 16 + incl
        count += 1
    assert count == n


def test_capture_panel_filter_and_save(qtbot, tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("pytestqt")
    from PySide6.QtWidgets import QMessageBox
    from netemu.gui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    a, b = w.topology.add_device("pc", "A"), w.topology.add_device("pc", "B")
    w.topology.connect(a.nic, b.nic)
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    panel = w.show_capture()
    panel.start_btn.setChecked(True)
    _ping(None, a, "10.0.0.2")
    assert len(panel.packets) >= 10
    panel.filter.setText("icmp")
    assert panel.table.rowCount() == 8
    panel.filter.setText("ip.src==10.0.0.2 and icmp")
    assert panel.table.rowCount() == 4
    panel.show_details(0)
    assert "ICMP" in panel.details.toPlainText() and "0000" in panel.details.toPlainText()
    assert panel.save(str(tmp_path / "x.pcap")) == 4
    w.shutdown()
