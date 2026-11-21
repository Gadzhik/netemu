"""English versions of the ready-made labs (title, task text, check descriptions in order)."""

EN = {
    "01-vlan": {
        "title": "VLAN: separating departments",
        "text": """Sales (PC1, PC2) and HR (PC3, PC4) share one switch and can still see each other.

1. On **SW1** create VLAN 10 named `SALES` and VLAN 20 named `HR`.
2. Make Fa0/1–Fa0/2 access ports in VLAN 10 and Fa0/3–Fa0/4 access ports in VLAN 20.
3. Check that PC1 reaches PC2 while PC3 from the other department no longer answers.

The PC addresses are already set: 192.168.1.11–14/24.""",
        "checks": ["SW1 has VLAN 10", "SW1 has VLAN 20", "Fa0/1 is in VLAN 10", "Fa0/2 is in VLAN 10",
                   "Fa0/3 is in VLAN 20", "Fa0/4 is in VLAN 20", "PC1 reaches PC2", "PC3 reaches PC4",
                   "PC1 cannot reach PC3 (other VLAN)"],
    },
    "02-inter-vlan": {
        "title": "Inter-VLAN routing",
        "text": """PC1 is in VLAN 10 (192.168.10.0/24), PC2 in VLAN 20 (192.168.20.0/24). Route between the VLANs with a router-on-a-stick.

1. On **SW1** create VLANs 10 and 20, put Fa0/1 in VLAN 10 and Fa0/2 in VLAN 20, and make Fa0/24 a trunk.
2. On **R1** enable Gi0/0 and create subinterfaces Gi0/0.10 and Gi0/0.20 with dot1Q encapsulation and the .1 address in each network.
3. Ping PC2 from PC1.""",
        "checks": ["SW1 has VLAN 10", "SW1 has VLAN 20", "VLAN 10 gateway on R1", "VLAN 20 gateway on R1",
                   "PC1 reaches its gateway", "PC1 reaches PC2 through R1"],
    },
    "03-stp": {
        "title": "STP: choosing the root bridge",
        "text": """Three switches form a ring. **SW2** is currently the STP root bridge, although **SW1** is the core of the network.

1. Look at `show spanning-tree` on the switches: which bridge is the root, which port is blocked?
2. Make SW1 the root bridge for VLAN 1 (priority 4096 or `spanning-tree vlan 1 root primary`).
3. Check that PC1 and PC2 can still reach each other.""",
        "checks": ["SW1 is the root bridge", "PC1 reaches PC2"],
    },
    "04-ospf": {
        "title": "Single-area OSPF",
        "text": """The interface addresses are configured. The networks of PC1 (192.168.1.0/24) and PC3 (192.168.3.0/24) do not know about each other yet.

1. Enable OSPF (process 1, area 0) on R1, R2 and R3 for all their networks.
2. Check the adjacencies with `show ip ospf neighbor`: R2 must have two neighbours.
3. Make sure R1 has an O route to 192.168.3.0/24 and PC1 reaches PC3.""",
        "checks": ["R2 has two OSPF neighbours", "R1 knows PC3's network", "R3 knows PC1's network",
                   "PC1 reaches PC3"],
    },
    "05-dhcp": {
        "title": "DHCP server on a router",
        "text": """The computers must get their addresses automatically.

1. On **R1** exclude 192.168.1.1–192.168.1.10 from the pool.
2. Create pool `LAN`: network 192.168.1.0/24, gateway 192.168.1.1, DNS 8.8.8.8.
3. Turn on DHCP on PC1 and PC2 (Config tab or `ipconfig /renew`).""",
        "checks": ["PC1 got an address by DHCP", "PC2 got an address by DHCP", "PC1 reaches the gateway"],
    },
    "06-nat": {
        "title": "NAT/PAT and Internet access",
        "text": """The ISP gave the office 203.0.113.2/30 with the ISP gateway at 203.0.113.1. Let 192.168.1.0/24 out to the Internet.

1. Configure Gi0/1 on **R1** with the ISP address and add a default route.
2. Configure PAT (NAT overload): Gi0/0 inside, Gi0/1 outside, an access list for 192.168.1.0/24.
3. Check `ping 8.8.8.8` from PC1 and `show ip nat translations` on R1.""",
        "checks": ["R1 reaches the Internet", "PC1 reaches the Internet", "R1 has NAT translations"],
    },
    "07-acl": {
        "title": "Access lists (ACL)",
        "text": """The accounting server (192.168.2.10) must be reachable from PC2 only.

1. On **R1** create an access list that denies PC1 (192.168.1.10) access to the server and permits everything else.
2. Apply it on the right interface in the right direction.
3. Check: PC2 reaches the server, PC1 does not, and PC1 still reaches its gateway 192.168.1.1.""",
        "checks": ["PC1 cannot reach the server", "PC2 reaches the server", "PC1 reaches the gateway"],
    },
    "08-hsrp": {
        "title": "Gateway redundancy (HSRP)",
        "text": """PC1 uses the virtual gateway 192.168.1.254, which nobody owns yet.

1. Configure HSRP group 1 with virtual address 192.168.1.254 on Gi0/0 of R1 and R2.
2. R1 must be active (priority 110, preempt) and R2 standby.
3. Check `show standby brief` and that PC1 reaches the server. Try shutting Gi0/0 on R1.""",
        "checks": ["R1 is the active HSRP router", "R2 is the standby HSRP router", "The virtual gateway answers",
                   "PC1 reaches the server"],
    },
    "09-ipv6": {
        "title": "IPv6: addressing and routing",
        "text": """The computers already have IPv6 addresses: PC1 2001:db8:1::10/64, PC2 2001:db8:2::10/64.

1. Enable IPv6 routing on **R1** (`ipv6 unicast-routing`).
2. Give Gi0/0 the address 2001:db8:1::1/64 and Gi0/1 2001:db8:2::1/64, and enable the interfaces.
3. Check `ping 2001:db8:2::10` and `tracert -6` from PC1.""",
        "checks": ["Gi0/0 address", "Gi0/1 address", "PC1 reaches PC2 over IPv6"],
    },
    "10-wifi": {
        "title": "Wireless network",
        "text": """Access point AP1 is connected to R1, which already runs DHCP for 192.168.50.0/24.

1. On **AP1** set up the network `OFFICE` with WPA2 and the key `Office2026` (device window → Config).
2. Connect the laptops to OFFICE (Wi-Fi settings or `netsh wlan connect`) and enable DHCP.
3. Check that the laptops reach the gateway 192.168.50.1.""",
        "checks": ["Laptop1 joined OFFICE", "Laptop2 joined OFFICE", "Laptop1 got an address",
                   "Laptop2 reaches the gateway"],
    },
    "11-trouble-intervlan": {
        "title": "Troubleshooting: VLAN",
        "text": """This inter-VLAN network used to work, but after "a few small changes" PC1 cannot reach PC2.

There are **two** faults. Find them with `show` commands, `ipconfig` and `ping`, then fix them.

Hints: compare the VLANs on the switch with the subinterfaces; check the PC settings.""",
        "checks": ["PC2 reaches its gateway", "PC1 reaches PC2"],
    },
    "12-trouble-ospf": {
        "title": "Troubleshooting: OSPF",
        "text": """This three-router OSPF network used to work, but now PC1 reaches nothing and R3 has lost its neighbour.

There are **two** faults. Use `show ip interface brief`, `show ip ospf neighbor` and `show ip protocols`.""",
        "checks": ["R2 has two neighbours again", "PC1 reaches its gateway", "PC1 reaches PC3"],
    },
}
