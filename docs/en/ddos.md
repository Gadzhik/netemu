# DDoS attacks and defence

A DDoS (Distributed Denial of Service) attack does not steal data: it makes a service unavailable to its real users. Thousands of infected machines (a botnet) send the victim traffic or requests at the same time until some resource runs out: link bandwidth, router performance, a firewall's connection table, a server's TCP queue, the CPU or memory of a web application.

In NetEmu you can launch attacks of different kinds and power, see exactly where the network breaks, and turn on defences at every level, from the server to the provider. This page explains the theory and shows how it is done in the simulator and on real equipment.

## How an attack is measured

| Unit | Meaning | What it hits |
|---|---|---|
| **bits/s** (Mbit/s, Gbit/s, Tbit/s) | volume of traffic | fills links |
| **packets/s** (pps, kpps, Mpps) | number of packets | loads routers, firewalls, network cards: every packet must be processed, small packets are worse than big ones |
| **requests/s** (rps) | HTTP requests | exhausts the application and the database |
| **connections** | concurrently open TCP sessions | fills connection tables and server slots |

For scale: the largest recorded attacks reached several Tbit/s and hundreds of millions of packets per second, HTTP attacks hundreds of millions of requests per second. A small site on a 1 Gbit/s link falls to a botnet of a few thousand machines.

## Kinds of attacks

### Volumetric (L3/L4)

The goal is to fill the victim's link. The traffic is meaningless, only its volume counts.

- **UDP flood**: UDP packets to random or chosen ports; the server also spends effort on ICMP "port unreachable" replies.
- **ICMP flood (ping flood)**: echo requests.
- **Reflection with amplification**: the most powerful class, see below.

The key point: if the link to you is full, **no filter on your side of the link helps**. The packets are lost before your router. Filtering has to happen upstream: at the provider or in a scrubbing centre.

### Protocol attacks (state exhaustion)

The goal is to exhaust state tables on a server, firewall or load balancer.

- **SYN flood**: the client starts a TCP handshake (SYN), the server answers SYN-ACK and keeps a half-open connection, the ACK never comes. The backlog fills up and real clients cannot connect. It works with little traffic: 20,000 SYN/s is about 10 Mbit/s.
- **ACK/RST floods**, fragmented packets, the historic **Ping of Death**: they load packet processing.

### Application layer (L7)

The goal is to exhaust the application. The requests look real, there is little traffic, network filters cannot tell them apart.

- **HTTP GET/POST flood**: lots of ordinary requests, often to "heavy" pages (search, cart, API).
- **Slowloris and other slow attacks**: a bot opens a connection and sends the headers very slowly, a byte every few seconds; the server keeps the connection open. A few dozen bots take every slot of a web server with negligible traffic.
- Attacks on DNS (random subdomains) and on TLS (expensive handshakes).

### Reflection with amplification

The attacker sends a small request to someone else's public server (a reflector) with **the victim's address as the source**. The server answers the victim, and the answer is many times bigger than the request. The attacker hides behind the reflectors and multiplies its power.

| Protocol | Port | Amplification (approx.) | What is abused |
|---|---|---|---|
| DNS | UDP 53 | 28–54 | open resolvers, ANY and DNSSEC queries |
| NTP | UDP 123 | up to 556 | the monlist command |
| SSDP | UDP 1900 | about 30 | UPnP devices |
| SNMP v2 | UDP 161 | about 6 | GetBulk |
| CLDAP | UDP 389 | 56–70 | domain controllers on the Internet |
| Memcached | UDP 11211 | up to 51,000 | cache servers open to the outside |

Reflection needs two things: **the attacker's network lets spoofed packets out** (no BCP 38) and **open reflectors exist on the Internet**. Remove either one and the attack is gone.

## How NetEmu models it

A real attack is millions of packets per second; simulating each one is impossible. An attack in NetEmu is therefore a **flow**:

- The **Botnet** device (palette → Attacks (DDoS)) stands for thousands of infected machines. Like a PC it has an address, a mask and a gateway, plus a list of attacks.
- Once a simulated second the botnet sends a **probe packet** that carries the whole attack: kind, number of bots, bits/s and packets/s. The probe is a real IP packet. Routing, NAT, ACLs, uRPF, QoS policers, firewalls and the provider's protection all act on it, so every defence acts on the whole flow at once.
- Every cable the probe crosses gets the load. A cable runs at the speed of its slower port: FastEthernet 100 Mbit/s, GigabitEthernet 1 Gbit/s. The flow is cut to the cable's speed, and **legitimate frames on an overloaded cable are lost in proportion**: at 300 % load two thirds are lost. Pings time out, pages do not open, routing protocols can lose neighbours.
- A server has finite resources: a SYN queue (about 25 half-open connections per second), HTTP capacity (300 requests/s) and concurrent connections (256). A SYN flood, an HTTP flood and Slowloris exhaust them unless protection is on.
- Reflectors: an open DNS resolver and an NTP server with monlist answer a spoofed query to the victim, amplified. The Internet cloud contains two such misconfigured servers, 198.51.100.53 (DNS) and 198.51.100.123 (NTP). Your own server becomes a reflector if you enable "recursion for any address" or "answer monlist" on it.

Seeing the attack:
- **Tools → Load and DDoS** (`Ctrl+Shift+D`) lists every cable direction with its load, bits/s, packets/s and attack kind, plus the servers' resources and the number of legitimate frames lost.
- **Cable colours on the canvas**: yellow means attack traffic is present, orange means load above 70 %, red and thick means the cable is overloaded.
- **The packet analyzer** shows the probes: the IPv4 details say "DDoS flow (sample of …)" with the flow's parameters.

### Launching an attack

1. Drag a **Botnet** onto the canvas and connect it. Usually it is connected to the Internet cloud; then its address is the provider's customer address on that port.
2. Set the address, mask and gateway on the Config tab.
3. On the Attacks tab choose the kind, the victim's address, the port, the number of bots and the rate per bot. For UDP and ICMP set the packet size, optionally spoof the sources, and for amplification list the reflectors. "Total" shows the combined power and, for amplification, what reaches the victim.
4. Click Add, then Start. Several attacks of different kinds can run at the same time.

Power is **bots × rate × packet size × 8** bits/s. For example, 30,000 bots × 10 packets/s × 1,250 bytes = 3 Gbit/s.

## Defences

Defence is built in layers: no single measure covers every kind of attack. Below is what helps against what, in NetEmu and on real equipment.

### 1. Headroom and architecture

- Links and equipment with headroom, load spread over sites (anycast), a CDN for static content.
- Services that need not be reachable from the Internet must not be reachable from the Internet: a closed port cannot be attacked.
- Monitoring: NetFlow/sFlow/IPFIX, thresholds on bits/s and packets/s, alerts. Noticing an attack in the first minutes matters.

### 2. Access lists (ACLs)

ACLs help when the attack has a mark to filter on: protocol, port, source addresses. On a Cisco router:

```text
access-list 100 deny udp any host 203.0.113.2
access-list 100 permit ip any any
interface GigabitEthernet0/1
 ip access-group 100 in
```

Limits: an ACL on your router **does not save a full link**, because it filters what has already arrived. Blocking "by source" is useless against spoofed addresses.

### 3. Policing (rate limiting)

A policer lets a traffic class through no faster than a set rate and drops the rest: a way to limit a harmful kind of traffic (ICMP, UDP to a port) without banning it. Cisco (MQC):

```text
access-list 101 permit icmp any any
class-map match-all ICMP
 match access-group 101
policy-map EDGE-IN
 class ICMP
  police 8000000
interface GigabitEthernet0/1
 service-policy input EDGE-IN
```

On Huawei: `traffic classifier`, `traffic behavior` with `car cir …`, `traffic policy`, `traffic-policy … inbound`. In NetEmu the policer cuts the attack flow to the set rate. Legitimate traffic of the same class is limited too: policing is a compromise.

### 4. uRPF and BCP 38 against spoofing

Unicast Reverse Path Forwarding checks a packet's source address:
- **strict**: the route back to the source must go out of the interface the packet came in on. Use it on customer-facing interfaces and single-homed links.
- **loose**: the source just has to be in the routing table (a default route does not count). It drops "martian" and unallocated addresses and suits asymmetric routing.

BCP 38 (RFC 2827) asks providers to drop spoofed sources at their customer edge. If everybody did, reflection and spoofed attacks would be impossible.

| Vendor | Command |
|---|---|
| Cisco IOS | `ip verify unicast source reachable-via rx` (strict), `… reachable-via any` (loose) on the interface |
| Huawei VRP | `urpf strict` / `urpf loose` on the interface |
| Juniper Junos | `set interfaces ge-0/0/0 unit 0 family inet rpf-check` (`rpf-check mode loose`) |
| MikroTik RouterOS | `/ip settings set rp-filter=strict` (`loose`, `no`) |

Do not use strict uRPF towards an upstream provider with a default route, or with asymmetric routing: legitimate packets would be dropped.

### 5. Null routes and RTBH (Remote Triggered Black Hole)

A route "to nowhere" drops all traffic to an address. When one address is attacked and the link is shared, the victim is sacrificed: it becomes unreachable, but the link and every other service keep working. RTBH does the same at the provider (usually triggered by the BGP community 65535:666), so the flow does not even reach your link.

| Vendor | Command |
|---|---|
| Cisco IOS | `ip route 203.0.113.2 255.255.255.255 Null0` |
| Huawei VRP | `ip route-static 203.0.113.2 32 NULL0` |
| Juniper Junos | `set routing-options static route 203.0.113.2/32 discard` |
| MikroTik RouterOS | `/ip route add dst-address=203.0.113.2/32 blackhole` |

In NetEmu, RTBH at the provider is set on the Internet cloud: Provider protection tab, RTBH column of the customer's port.

### 6. Protection at the provider: scrubbing and FlowSpec

Against volumetric attacks that fill your link, the only remedy is filtering **before** the link:
- **Scrubbing centre.** Traffic to your addresses is diverted through the provider's or a specialised service's filters; attack flows are removed and clean traffic is returned to you. Good against L3/L4; L7 needs application protection.
- **BGP FlowSpec** (RFC 5575/8955). You hand the provider a rule such as "drop UDP from source port 53 to 203.0.113.2", and it applies the rule on its routers. Fast and precise against attacks with a clear mark, reflection for example.

In NetEmu: Internet cloud → Provider protection tab. For every customer port turn on scrubbing, list RTBH addresses and FlowSpec rules separated by «;»: `udp src 53`, `udp src 123`, `udp dst 80`, `tcp dst 80`, `icmp`, `udp`.

### 7. Switching DNS to a protection service (and to a backup site)

The most common way to put a site behind cloud DDoS protection is to **change its DNS record**. The A record stops pointing at your server and points at the protection service (a CDN, a cloud WAF, a scrubbing provider). The service is a **reverse proxy**:
- it takes every client connection on its distributed network of huge capacity (anycast), so a volumetric flood dissolves in it;
- it filters out bots (JS challenge, CAPTCHA, reputation, limits) and lets real requests through;
- it fetches the pages from your server (the origin) itself and serves them to clients, often from a cache.

What matters:
- **TTL.** Clients and resolvers cache the record for its TTL. Until it runs out, traffic keeps going to the old address. Keep the TTL of important records low (60–300 s) *in advance*: during an attack it is too late to lower it.
- **It works against attacks by name.** Bots attacking `shop.example` follow DNS to the proxy. An attack aimed straight at the server's IP address goes on; the DNS switch does not touch it.
- **Hide the real (origin) address.** It leaks through old DNS records (DNS history), subdomains not moved to the proxy (`mail.`, `ftp.`, `dev.`), mail headers and server answers. After moving behind the protection, change the server's address and at the edge accept HTTP/HTTPS **only from the protection service's addresses** (ACL).
- **Backup site (DNS failover).** The same way a site is moved to another server or data centre: the A record changes and clients follow when the TTL runs out. Monitoring with automatic DNS failover is a standard part of a response plan.
- **Protect the DNS itself.** If your DNS servers are attacked the whole site disappears: keep the zone at a provider with distributed (anycast) infrastructure and use NS servers of several operators.
- For large networks the alternative is **BGP diversion**: your prefix is announced through the scrubbing centre and all traffic to the network passes it (this also covers attacks on IP addresses).

In NetEmu the Internet cloud hosts customers' domains: Provider protection tab → the DNS hosting table (domain, A address, TTL, a "Through the protection service" box). The resolvers 8.8.8.8 and 1.1.1.1 answer from this table with that TTL, and PCs and servers cache the answer for that long (`ipconfig /displaydns`, `ipconfig /flushdns`, `resolvectl flush-caches`). The "Through the protection service" box changes the answer to 198.51.100.200, the provider's reverse proxy: floods end there, bots are filtered out, pages are fetched from the A address. A botnet can attack by name (Victim = a domain, with a DNS server set on the Config tab): it looks the name up again when the TTL runs out and follows the record. To move a site to a backup server, just change the A address.

### 8. SYN flood defences

- **SYN cookies.** The server keeps no half-open connection: it encodes the connection's parameters in the initial sequence number of the SYN-ACK and creates state only when the client returns the ACK. Linux: `sysctl -w net.ipv4.tcp_syncookies=1` (the same on a Linux server in NetEmu); the "SYN cookies" box on a server's Services tab. MikroTik: `/ip settings set tcp-syncookies=yes`.
- **TCP intercept / SYN proxy.** A router or firewall in front of the server answers the SYNs itself and lets through only connections whose client sent the ACK. Cisco: `access-list 120 permit tcp any host 203.0.113.2 eq 80` and `ip tcp intercept list 120`; on an ASA, `set connection embryonic-conn-max`.
- A bigger backlog and shorter timeouts help only against weak attacks.

### 9. Application-layer defences

- **Per-IP request limits** (on the server, the load balancer, a WAF). They work against a few strong bots and are useless against a large botnet where every bot sends a couple of requests per second; lab 23 shows this in numbers.
- **Browser check (JS challenge), CAPTCHA.** The server's answer requires running JavaScript or solving a puzzle; simple bots do not and are filtered out. In NetEmu, the "Browser check" box on the server.
- **WAF** (web application firewall): signatures, behaviour, address reputation.
- **Caching and CDN**: repeated requests never reach the application.
- **Against slow attacks (Slowloris)**: per-IP connection limits, short header timeouts, a reverse proxy (nginx) in front of the application. In NetEmu, "Connections per IP".

### 10. Not becoming part of an attack

- Close **open resolvers**: DNS recursion only for your own networks. The "DNS: recursion for any address" box in NetEmu is off by default; keep it off.
- Disable **NTP monlist** (`disable monitor`, current ntpd versions), do not expose SNMP, SSDP, Memcached or CLDAP to the Internet.
- Apply **BCP 38** on your own customer interfaces.
- Watch for infected machines in your network (unusual outgoing traffic).

## Responding to an attack

1. **Detect and classify.** Which resource runs out: the link (bits/s), the equipment (packets/s), the server (connections, requests)? What kind of attack, which ports and addresses, are sources spoofed? In NetEmu: Load and DDoS and the packet analyzer.
2. **Decide where filtering is possible.** If the link is full, only at the provider (scrubbing, FlowSpec, RTBH) or by switching DNS to a protection service. Otherwise at your edge (ACL, policing, TCP intercept) and on the server.
3. **Apply defences from the cheapest to the most expensive**: a precise rule on a mark → rate limiting → scrubbing → RTBH (last resort: the service is down but everything else works).
4. **Check the result**: the service is available to users, not just "the attack is gone".
5. **Afterwards**: review, a response plan, provider and scrubbing contacts arranged in advance, drills.

## Summary

| Attack | Resource | Helps | Does not help |
|---|---|---|---|
| UDP/ICMP flood, link full | link bandwidth | scrubbing at the provider, FlowSpec, RTBH | ACLs and policing on your side |
| UDP/ICMP flood, link not full | router, server | ACL, policing at the edge | — |
| SYN flood | the server's TCP queue | SYN cookies, TCP intercept / SYN proxy | ACL by source (spoofed) |
| DNS/NTP amplification | link bandwidth | FlowSpec on the source port, scrubbing, RTBH; globally: close reflectors, BCP 38 | blocking the bots (not visible), turning off your DNS |
| Any attack on the site's name | link, server | switching DNS to a protection service (with a short TTL), hiding the origin address | the DNS switch, if the IP is attacked directly |
| HTTP flood | application capacity | JS challenge/CAPTCHA, WAF, cache/CDN; per-IP limit against few bots | L3/L4 scrubbing (the requests are real) |
| Slowloris | connection slots | per-IP connection limit, header timeouts, reverse proxy | bandwidth limits (almost no traffic) |
| Any spoofed attack | — | uRPF/BCP 38 in the source network | source filters at the victim |

## Ready-made labs

Lab → Ready-made labs. The attacks are already running when a lab opens. In every stand a company site (router EDGE, a switch, the WEB server behind NAT) is connected to the provider's cloud, and a customer (CLIENT) and the botnet are customers of the same provider.

| Lab | What it teaches | Solution |
|---|---|---|
| 20. Volumetric attack and scrubbing | 3 Gbit/s of UDP against a 1 Gbit/s link; why an ACL on your side is useless | scrubbing at the provider |
| 21. SYN flood | exhausting the TCP queue with 10 Mbit/s | SYN cookies on the Linux server or TCP intercept on EDGE |
| 22. Reflection with amplification (DNS) | 50 Mbit/s of queries become 2.4 Gbit/s at the victim | FlowSpec `udp src 53` at the provider |
| 23. HTTP flood and Slowloris | L7 attacks; why a per-IP limit does not stop a large botnet | JS challenge and a connection limit on the server |
| 24. Anti-spoofing (BCP 38) | spoofed sources from a provider's customer network | strict uRPF on the customer interface |
| 25. Switching DNS to the protection service | an attack by name, DNS TTL and cache, a reverse proxy | the domain "Through the protection service" at the provider, `ipconfig /flushdns` |

Every lab has checks (`F6`): `available` means the shop opens for the customer in 9 of 10 tries; `ddos` means attack traffic does not reach the device (below the given Mbit/s). Hints for failed checks tell where to look.

## Your own labs

The lab editor offers these checks:
- `available <device> <URL> [percent]`: at least *percent* of 10 HTTP requests succeed (default 90);
- `ddos <device> <Mbit/s>`: the attack traffic the device receives stays below the limit.

Solution scripts have directives for servers, the cloud and botnets:
- `@ddos syncookies on`, `@ddos httplimit 5`, `@ddos challenge on`, `@ddos connlimit 10`, `@ddos resolver off`, `@ddos monlist off` (server);
- `@isp ISP0 scrub on`, `@isp ISP0 rtbh 203.0.113.2`, `@isp ISP0 flowspec udp src 53`, `@isp dns shop.example 203.0.113.2 ttl 30`, `@isp dns shop.example shield on` (cloud);
- `@attack start`, `@attack stop` (botnet).

## Limits of the model

- An attack is an aggregated flow in one-second steps. Its individual packets are not simulated; only the probes are visible.
- A router or switch can be given a forwarding capacity in packets/s (`pps_limit`). A small-packet flood (SYN, 64-byte UDP) produces many packets at a low volume and overwhelms the device by packets/s even when the link is not full, and then legitimate traffic is lost too. No limit is set by default. The device's load is shown in Load and DDoS.
- ASA and FortiGate filter the flow by their rules, but their connection tables do not overflow.
- L7 protection is simplified: the JS challenge filters out every bot, and limits apply to the rate of one bot.
