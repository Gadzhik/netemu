# Security monitoring and SIEM

Individual defences (a firewall, IPS, antivirus, DHCP snooping, DAI) each work on their own spot. To see an attack as a whole and in time, their events are gathered in one place: this is **monitoring** and a **SIEM** (Security Information and Event Management). NetEmu shows what it looks like: Tools → Monitoring (SIEM) collects events from every device and security monitor and turns them into alerts.

## Why it is needed

An attack is rarely visible from a single event. It is assembled from signs on different devices:
- on a switch: port-security violations, forged ARP dropped by DAI, STP root takeover;
- on a router and firewall: ACL and IPS hits, traffic spikes;
- on servers: overload, denials of service (503), failed SSH logins;
- in the network: port scans, contacts with command-and-control servers, data exfiltration.

Checking each device by hand is slow and unreliable. A SOC (security operations centre) collects logs from every source, **correlates** them (links them into one event) and raises **alerts** with a severity and advice. That is how an attack is seen in the first minutes.

## How it works

1. **Log collection.** Devices send events over **syslog** to a central server (`logging host <ip>`). Servers, antivirus, IPS and firewalls write logs too. Large networks use collectors (rsyslog, Graylog, Elastic/Logstash, Splunk).
2. **Normalisation.** Records from different formats are brought to a single shape (time, source, type, severity).
3. **Correlation and rules.** Detection rules link events: "many failed SSH logins from one address" → brute force; "connections to one port across consecutive addresses" → scanning; "periodic contacts with one external address" → C2; "a flow above a threshold" → DDoS.
4. **Alerts and response.** A rule that fires creates an alert (severity, description, advice). An analyst checks it and acts; some actions are automated (SOAR).
5. **Retention.** Logs are kept for investigations and compliance.

Good practice: synchronise time over **NTP** (otherwise events cannot be lined up), protect the logs from tampering, and set sensible thresholds so alerts do not drown in noise.

## The SIEM in NetEmu

Tools → Monitoring (SIEM) (`Ctrl+Shift+M`) is one pane of glass:
- **Alerts** by severity (critical / high / medium / low): category, source, what was detected. Selecting a row shows advice at the bottom.
- **Event log**: a merged timeline from every security monitor (DDoS, infection, L2 attacks) and from the devices' syslog buffers.

The NetEmu SIEM detects:

| Category | Sign | Advice |
|---|---|---|
| DDoS | link or server-resource overload | scrubbing at the provider, limits, SYN cookies |
| Scanning | many exploit packets | segmentation, IPS |
| Worm/malware | infected hosts, encryption | isolate, clean, close the hole |
| C2 server | contacts with C2, data leaking | block the C2 (ACL/DNS sinkhole) |
| ARP spoofing | ARP poisoning, interception | Dynamic ARP Inspection |
| MAC flooding | switching-table overflow | port security |
| STP attack | root takeover | BPDU Guard / Root Guard |
| IPS/IDS | malicious packets dropped | the defence is working |

To make devices send events over syslog, configure `logging host <ip>` on them and run a Syslog server (a Linux server → Services, or `cat /var/log/syslog`). Synchronise time over NTP.

## Ready-made labs

| Lab | What it teaches | Solution |
|---|---|---|
| 35. Find the attack with the SIEM | monitoring and finding a hidden attack | read the SIEM alert, identify the ARP spoofing, turn on DAI |

## Limits of the model

- The NetEmu SIEM is a training model: the detection rules are built in and fire from the state of the security monitors and the syslog buffers, not from a full correlation language.
- There is no long-term storage or reporting; alerts are computed from the current state of the network.
