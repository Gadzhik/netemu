"""Windows and Linux end-host terminals: addressing, routes, DNS, DHCP, diagnostics."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology

LAN = """enable
configure terminal
ipv6 unicast-routing
interface gi0/0
ip address 192.168.1.1 255.255.255.0
ipv6 address 2001:db8:1::1/64
no shutdown
interface gi0/1
ip address 192.168.2.1 255.255.255.0
no shutdown
exit
ip dhcp excluded-address 192.168.1.1 192.168.1.9
ip dhcp pool LAN1
network 192.168.1.0 255.255.255.0
default-router 192.168.1.1
dns-server 192.168.2.2
end"""


def lab():
    """linux PC + windows PC on 192.168.1.0/24 (DHCP + RA), DNS/web server (Windows) on 192.168.2.0/24."""
    t = Topology()
    r = t.add_device("cisco-2911")
    sw = t.add_device("cisco-2960")
    lin, win, srv = t.add_device("pc-linux"), t.add_device("pc"), t.add_device("server")
    t.connect(r.get_port("gi0/0"), sw.get_port("gi0/1"))
    t.connect(lin.nic, sw.get_port("fa0/1"))
    t.connect(win.nic, sw.get_port("fa0/2"))
    t.connect(srv.nic, r.get_port("gi0/1"))
    run_all(r.create_cli(), LAN)
    srv.set_ip("192.168.2.2", "255.255.255.0")
    srv.set_gateway("192.168.2.1")
    srv.dns_server.enabled = True
    srv.dns_server.records["www.lab.local"] = "192.168.2.2"
    t.engine.run(max_time=2000)
    return t, r, lin, win, srv


def test_linux_model_and_defaults():
    t, r, lin, win, srv = lab()
    assert lin.nic.name == "eth0" and lin.default_ttl == 64 and lin.os == "linux"
    s = lin.create_cli()
    assert s.prompt.startswith("user@") and s.prompt.endswith("$ ")
    # SLAAC is on by default on Linux (accept_ra=1)
    assert any(str(a.ip).startswith("2001:db8:1:") for a in lin.nic.ipv6)
    assert "proto ra" in run(s, "ip -6 route")
    assert "command not found" in run(s, "foo")


def test_linux_needs_root_and_static_config():
    t, r, lin, win, srv = lab()
    s = lin.create_cli()
    assert "Operation not permitted" in run(s, "ip addr add 192.168.1.50/24 dev eth0")
    assert run(s, "sudo ip addr add 192.168.1.50/24 dev eth0") == ""
    assert "Nexthop has invalid gateway" in run(s, "sudo ip route add default via 10.9.9.9")
    assert run(s, "sudo ip route add default via 192.168.1.1") == ""
    assert "File exists" in run(s, "sudo ip route add default via 192.168.1.1")
    out = run(s, "ip route")
    assert "default via 192.168.1.1 dev eth0" in out and "192.168.1.0/24 dev eth0 proto kernel" in out
    assert "via 192.168.1.1 dev eth0" in run(s, "ip route get 192.168.2.2")
    ping = run(s, "ping -c 3 192.168.2.2")
    assert "3 packets transmitted, 3 received, 0% packet loss" in ping and "icmp_seq=1 ttl=127" in ping
    assert "inet 192.168.1.50/24" in run(s, "ip a")
    assert "192.168.1.1" in run(s, "ip neigh") and "192.168.1.1" in run(s, "arp -n")
    # net-tools
    run(s, "sudo -i")
    assert s.prompt.startswith("root@")
    assert run(s, "ifconfig eth0 192.168.1.60 netmask 255.255.255.0") == ""
    assert "inet 192.168.1.60  netmask 255.255.255.0" in run(s, "ifconfig")
    assert run(s, "route add -net 10.0.0.0 netmask 255.0.0.0 gw 192.168.1.1") == ""
    rn = run(s, "route -n")
    assert "10.0.0.0        192.168.1.1     255.0.0.0       UG" in rn
    assert run(s, "exit").strip() == "logout" and not s.root
    tr = run(s, "traceroute -n 192.168.2.2")
    assert " 1  192.168.1.1" in tr and " 2  192.168.2.2" in tr


def test_linux_dhcp_dns_web_and_persistence():
    t, r, lin, win, srv = lab()
    s = lin.create_cli()
    assert "Permission denied" in run(s, "dhclient eth0")
    out = run(s, "sudo dhclient -v eth0")
    assert "DHCPDISCOVER on eth0" in out and "bound to 192.168.1." in out
    assert "nameserver 192.168.2.2" in run(s, "cat /etc/resolv.conf")
    assert "proto dhcp" in run(s, "ip route")
    assert run(s, "dig +short www.lab.local").strip() == "192.168.2.2"
    assert "status: NOERROR" in run(s, "dig www.lab.local") and "NXDOMAIN" in run(s, "dig nope.lab.local")
    assert "www.lab.local has address 192.168.2.2" in run(s, "host www.lab.local")
    assert "can't find nope.lab.local: NXDOMAIN" in run(s, "nslookup nope.lab.local")
    assert "Name or service not known" in run(s, "ping -c 1 nope.lab.local")
    assert "64 bytes from www.lab.local (192.168.2.2)" in run(s, "ping -c 2 www.lab.local")
    assert "<html" in run(s, "curl http://www.lab.local").lower()
    assert "HTTP/1.1 200 OK" in run(s, "curl -I http://www.lab.local")
    assert "saved" in run(s, "wget http://www.lab.local/")
    run(s, "echo nameserver 192.168.1.1 | sudo tee /etc/resolv.conf")
    assert str(lin.dns) == "192.168.1.1"
    assert "Permission denied" in run(s, "echo nameserver 8.8.8.8 > /etc/resolv.conf")
    run(s, "sudo resolvectl dns eth0 192.168.2.2")
    assert "Current DNS Server: 192.168.2.2" in run(s, "resolvectl status")
    assert run(s, "sudo hostnamectl set-hostname web1") is not None or True
    assert lin.hostname == "web1" and s.prompt.startswith("user@web1:")
    # Linux does not fall back to APIPA
    run(s, "sudo dhclient -r eth0")
    t.engine.run(max_time=500)
    assert lin.nic.ip is None
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    lin2 = t2.devices[lin.id]
    assert lin2.os == "linux" and lin2.nic.name == "eth0"


def test_linux_ipv6_and_misc():
    t, r, lin, win, srv = lab()
    s = lin.create_cli()
    assert "2 received" in run(s, "ping -c 2 2001:db8:1::1")
    assert "2 received" in run(s, "ping6 -c 2 2001:db8:1::1")
    assert run(s, "sudo ip -6 addr add 2001:db8:1::77/64 dev eth0") == ""
    assert "inet6 2001:db8:1::77/64 scope global" in run(s, "ip -6 addr")
    assert "accept_ra = 0" in run(s, "sudo sysctl -w net.ipv6.conf.eth0.accept_ra=0")
    assert "Link detected: yes" in run(s, "ethtool eth0")
    assert "GNU/Linux" in run(s, "uname -a")
    assert "uid=1000(user)" in run(s, "id")
    assert "Operation not permitted" in run(s, "ip link set eth0 down")
    run(s, "sudo ip link set eth0 down")
    assert "state DOWN" in run(s, "ip link") and not lin.nic.admin_up
    run(s, "sudo ip link set eth0 up")
    assert lin.nic.admin_up


def test_windows_route_netsh_and_tools():
    t, r, lin, win, srv = lab()
    w = win.create_cli()
    run(w, "netsh interface ipv4 set address \"FastEthernet0\" static 192.168.1.20 255.255.255.0 192.168.1.1")
    assert str(win.nic.ip) == "192.168.1.20/24" and str(win.gateway) == "192.168.1.1"
    run(w, "netsh interface ipv4 set dnsservers \"FastEthernet0\" static 192.168.2.2 primary")
    assert str(win.dns) == "192.168.2.2"
    cfg = run(w, "netsh interface ipv4 show config")
    assert "192.168.1.20" in cfg and "Statically Configured DNS Servers:    192.168.2.2" in cfg
    out = run(w, "ping -n 2 -l 500 -i 64 www.lab.local")
    assert "Pinging www.lab.local [192.168.2.2] with 500 bytes" in out and "Received = 2" in out
    assert "www.lab.local" in run(w, "ipconfig /displaydns")
    assert "Successfully flushed" in run(w, "ipconfig /flushdns") and not win.dns_cache
    assert "Server:  192.168.2.2" in run(w, "nslookup www.lab.local 192.168.2.2")
    assert "does not lie on the same network" in run(w, "route add 10.0.0.0 mask 255.0.0.0 172.16.0.1")
    assert "OK!" in run(w, "route -p add 10.0.0.0 mask 255.0.0.0 192.168.1.1")
    rp = run(w, "route print -4")
    assert "10.0.0.0        255.0.0.0      192.168.1.1" in rp and "Persistent Routes:" in rp
    assert "OK!" in run(w, "route delete 10.0.0.0")
    assert "Element not found" in run(w, "route delete 10.0.0.0")
    assert "Physical Address" in run(w, "getmac") and win.nic.mac.windows() in run(w, "getmac /v")
    assert "UDP    0.0.0.0:53" in run(srv.create_cli(), "netstat -an")
    assert run(w, "arp -s 192.168.1.99 00-11-22-33-44-55") == ""
    assert "static" in run(w, "arp -a")
    assert run(w, "arp -d 192.168.1.99") == ""
    tr = run(w, "tracert -d -h 5 192.168.2.2")
    assert "over a maximum of 5 hops" in tr and "Trace complete" in tr
    assert "Microsoft Windows" in run(w, "ver")
    run(w, "netsh interface ipv6 add address \"FastEthernet0\" 2001:db8:1::20")
    run(w, "netsh interface ipv6 add route ::/0 \"FastEthernet0\" 2001:db8:1::1")
    assert "Received = 4" in run(w, "ping -6 2001:db8:1::1")
    assert "2001:db8:1::20" in run(w, "netsh interface ipv6 show address")
    run(w, "netsh interface set interface \"FastEthernet0\" admin=disabled")
    assert not win.nic.admin_up
    assert "is not recognized" in run(w, "ifconfig")
    run(w, "netsh interface ipv4 set address \"FastEthernet0\" dhcp")
    t.engine.run(max_time=3000)
    assert win.dhcp_enabled
