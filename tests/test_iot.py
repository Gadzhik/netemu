"""IoT: Wi-Fi sensors report to the server, rules drive actuators."""
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology


def test_rules_switch_the_fan():
    t = Topology()
    r = t.add_device("cisco-2911", "R1", 400, 100)
    ap = t.add_device("access-point", "AP1", 400, 250)
    srv = t.add_device("server", "IOT", 600, 100)
    sw = t.add_device("cisco-2960", "SW1", 500, 100)
    temp, fan = t.add_device("iot-temperature", "Temp1", 350, 330), t.add_device("iot-fan", "Fan1", 450, 330)
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/1"))
    t.connect(ap.get_port("Ethernet0"), sw.get_port("Fa0/2"))
    t.connect(srv.nic, sw.get_port("Fa0/3"))
    from cli_helpers import run_all
    run_all(r.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\n"
                            "no shutdown\nexit\nip dhcp excluded-address 192.168.1.1 192.168.1.100\nip dhcp pool IOT\n"
                            "network 192.168.1.0 255.255.255.0\ndefault-router 192.168.1.1\nend")
    srv.set_ip("192.168.1.100", "255.255.255.0")
    srv.iot_service.enabled = True
    srv.iot_service.rules = ["IF Temp1 > 30 THEN Fan1 on", "IF Temp1 < 25 THEN Fan1 off"]
    for d in (temp, fan):
        d.wifi = {"ssid": "NetEmu", "key": "netemu123"}
        d.server = "192.168.1.100"
    from netemu.core.devices.wireless import rescan
    rescan(t)
    t.engine.run(max_time=12000)
    assert temp.nic.ip is not None and set(srv.iot_service.devices) == {"Temp1", "Fan1"}
    assert fan.value == "off"
    temp.set_value(33.5)
    t.engine.run(max_time=t.engine.now + 1000)
    assert fan.value == "on" and srv.iot_service.devices["Fan1"]["value"] == "on"
    temp.set_value(21)
    t.engine.run(max_time=t.engine.now + 1000)
    assert fan.value == "off"
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[srv.id].iot_service.rules[0].startswith("IF Temp1") and t2.devices[temp.id].value == 21
