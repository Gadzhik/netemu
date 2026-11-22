# CLAUDE.md — NetEmu

Briefing for Claude Code. Read this first when opening the repository on a new machine.

## What this is
NetEmu is an educational network simulator in the spirit of Cisco Packet Tracer.
- Stack: Python 3.11+ with PySide6 (Qt Widgets).
- Devices from several vendors are configured through their *native* CLI dialects:
  - routers and switches: Cisco IOS, MikroTik RouterOS v7, Juniper Junos, Huawei VRP, Extreme EXOS;
  - end hosts: Windows cmd and an Ubuntu-style Linux shell.

There is a canvas with cables, a simulation mode with packet animation, labs with automatic checks,
teacher mode, LAN collaboration and a Russian/English UI.

Current version: **0.5.0** (`netemu/__init__.py`, `pyproject.toml`, `installer/netemu.iss`).
- README.md is in English, README.ru.md in Russian.
- User docs are in `docs/en` and `docs/ru`; they are also shown in the app via F1.

## Working with the owner
- **Language.**
  - Reply in **Russian**; the owner writes in Russian.
  - Write commit messages, the README and screenshots in **English**. Screenshots must show the English UI.
- **How they work.** The owner often says "Что еще?" ("what else?"), then hands over the whole list to do
  autonomously ("Начинай … и до конца" — start and go to the end). Work through it fully without asking
  more questions, then give a concise Russian report: what was done, what was verified, what is still open.
- **Rules.**
  - Do not install software without asking. Inno Setup 6 and GitHub CLI were already approved and installed.
  - Commit and push only when asked.
  - Never kill all python processes; stop only what you started.
  - Never commit secrets, real passwords, tokens or personal data. This includes local paths with the
    Windows user name and the owner's real email.
    - Before every commit, scan the staged files (`git grep` for tokens, keys, user paths and emails).
    - Passwords in the docs, examples and labs (`netemu123`, `cisco`, `Cisco123`, `S3cret!`, `mt123`, …)
      are deliberately fictional training values. Keep new examples fictional too.
  - Commits are authored as `Gadzhik <28716956+Gadzhik@users.noreply.github.com>`. Never put a real email
    into commits or anything sent to outside services.
- **GitHub.** Account `Gadzhik`, private repository `netemu`.
  - Git Credential Manager holds the HTTPS login, so `git push` works.
  - For the API (creating repos, running workflows) use the GitHub CLI after `gh auth login`.
  - On Windows the CLI is at `C:\Program Files\GitHub CLI\gh.exe`.

## Setup on a new machine
```bash
python -m venv .venv
.venv/Scripts/pip install -e .[dev] pytest-qt pyflakes      # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m netemu                                # run the app
.venv/Scripts/python -m netemu examples/multivendor-demo.netemu
```

## Everyday commands (all must stay green before finishing a task)
```bash
.venv/Scripts/python -m pyflakes netemu tools         # lint, must print nothing
.venv/Scripts/python -m pytest -q tests                # ~396 tests, ~10-20 s, GUI tests off screen
.venv/Scripts/python tools/gen_docs.py                 # regenerate CLI command tables in docs/*/*.md
.venv/Scripts/python tools/make_labs.py                # rebuild + verify the 35 ready-made labs (…: labs_more/ddos/malware.py, 31-33: labs_l2.py)
.venv/Scripts/python tools/screenshots.py              # re-render docs/images/*.png (English UI, off screen)
.venv/Scripts/python tools/build.py                    # PyInstaller build -> release/ (zip; setup.exe via Inno Setup)
```
- `tests/test_docs.py` fails when:
  - the generated command tables are stale (run `gen_docs.py`);
  - a fenced example in the vendor docs produces a CLI error (every code block in the
    hand-written part of `docs/*/<vendor>.md` is executed);
  - a GUI string has no English translation in `netemu/i18n.py` (the `EN` dict, keyed by the Russian text).
    The UI languages are Russian and English only (the owner does not want others).
- `tests/test_gui.py::test_english_interface_has_no_russian_text` scans the menus in English mode.

## Architecture
- `netemu/core/` is the simulation core and has **no Qt imports** (keep it that way).
  - `engine.py`: discrete-event engine.
    - Virtual time in ms; `schedule`, `run`, `run_until`.
    - Pub/sub events through `emit` and `subscribe`: device_added, device_changed, link_added,
      device_moved, console, frame_delivered, …
  - `devices/`: the device classes.
    - `Router`. `Switch(Router)` is an L3 switch; `AccessPoint(Switch)`.
    - `Host`, `Server`, Linux variants, `IPPhone(Host)` in `phone.py`, `Internet` cloud.
    - The model catalogue (`MODELS`, `create_device`) is in `devices/__init__.py`.
  - `stack/`: ARP, IPv4/ICMP, IPv6/ICMPv6/NDP, ping and traceroute jobs.
  - `routing/`: OSPF, `ospf6.py` (real LSDB, areas), `v6igp.py` (RIPng), RIP, BGP, `fhrp.py` (HSRP/VRRP).
  - `services/`: dhcp, dhcp6, dns, http, `remote.py` (Telnet/SSH), `mgmt.py` (Syslog/SNMP/NTP).
  - `security/`: acl, nat, `nat64.py`, `qos.py`.
  - `tunnel.py`: GRE/IPsec via `TunnelPort`.
  - `lab.py`: labs, checks, teacher-mode encryption, `plant_fault`, HTML report.
  - `collab.py`: snapshot/diff/apply sync used by LAN collaboration.
  - `persistence.py`: the `.netemu` JSON format.
    - Device configuration is a vendor-neutral dict: `Device.to_config()` / `apply_config()`.
    - Every new feature must round-trip through it.
- `netemu/core/cli/`: vendor dialects (`cisco.py`, `huawei.py`, `juniper.py`, `mikrotik.py`, `extreme.py`,
  `windows.py`/`host.py`, `linux.py`).
  - Built on the declarative command-tree parser in `parser.py`: `P.kw` / `P.arg`, `negatable`, handlers
    `lambda s, a, n:`.
  - Trees are post-processed in `build_trees()`. When adding a keyword that already exists in a mode,
    **merge into the existing node** (duplicates cause "% Ambiguous command").
  - Juniper edits a *candidate* config dict that is applied on `commit`.
  - MikroTik is menu-path based (`m[("ip","address")] = {cmd: (help, fn)}`).
  - Shared helpers live at the bottom of `cli/base.py`.
- `netemu/gui/`: PySide6.
  - `main_window.py`, `canvas.py` (scene reacts to engine events), `items.py`, `terminal.py`,
    `device_dialog.py`, `lab_panel.py`, `phone_panel.py`, `services_panel.py`, `theme.py`
    (light/dark + UI scale, colours read from `theme.COLORS` at paint time), `collab.py` (QtNetwork
    transport, PIN), `icons.py` (SVG icons rendered with QtSvg).
- `netemu/resources/labs/*.netemu`: generated by `tools/make_labs.py`.
  - English texts come from `tools/labs_en.py` (`Lab.translations["en"]`).
  - Never hand-edit the .netemu files; change the generator and rebuild.
- CI is `.github/workflows/build.yml`.
  - Job `test`: every push/PR, on Windows, Ubuntu and macOS with Python 3.11 and 3.13.
  - Job `build`: tags `v*` or a manual run; builds zip/tar.gz/.app and the Windows setup.exe.

## Installation and dependencies
- **Windows**:
  - PyInstaller bundles the MSVC runtime DLLs and the UCRT.
  - `installer/netemu.iss` also checks the system-wide VC++ 2015–2022 runtime (registry
    `HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64`). When it is missing, setup tells the
    user, downloads it (`CreateDownloadPage`) and installs it.
  - Test that path with `setup.exe /FORCEVC=1`.
  - `MinVersion=10.0` (Qt 6).
- **Linux**:
  - `installer/linux/netemu.sh` is the launcher. It checks the X11/EGL libraries Qt needs and installs the
    missing ones with apt, dnf, zypper or pacman after asking (`NETEMU_ASSUME_YES` / `NETEMU_SKIP_DEPS`).
  - `installer/linux/install.sh` installs for the current user (menu entry, `.netemu` MIME type).
  - `tools/build.py` copies both into the tarball.
  - CI runs the dependency installer in clean `ubuntu:24.04` and `fedora:41` containers.
- **macOS**: `build.py` ad-hoc signs `NetEmu.app` (`codesign -s -`). It is not notarized, so the first start
  needs right-click → Open.
- Running ISCC from Git Bash mangles `/Q` and `/D…` switches; call it from PowerShell.

## Gotchas learned the hard way
- `engine.run(max_time=X)` is **relative** (X more virtual ms). `run(max_time=engine.now + X)` doubles the
  clock every call; "Apply the solution" froze the GUI for minutes that way. Use `run_until(...)` for absolute.
- `QAction.triggered(bool)` and `QPushButton.clicked(bool)` pass `checked` into slots with optional
  parameters (`class_join(address=False)`). `MainWindow._act` wraps non-checkable actions; for buttons connect
  a lambda.
- Cisco `enable` asks for the enable secret on the console too (as real IOS); `lab.apply_script` answers the
  prompt with the device's own secret. `plant_fault(..., lab=lab)` only keeps a fault that breaks a check.
- The router/switch Config form: rebuild only the table you changed (`_load_routes`), not `load()`, or unsaved
  edits are lost.
- FortiGate keeps its config in `extra["forti"]`; `FortiSession._sync()` pulls form/project changes in before
  `config`/`show`, otherwise the next CLI change overwrites them.
- DDoS floods are **one probe IPv4 packet per second** carrying `pkt.flood` (a `FloodTag`). `Link.transmit`
  calls `DdosMonitor.offer()` *before* capping to `capacity_bps` (else utilisation reads 1.0) and drops
  legitimate frames by overload. Every filter (ACL, NAT, uRPF, QoS police, cloud protection) must handle
  the probe as the whole flow. Traffic the cloud sends itself (reflectors) bypasses `egress_filter`, so
  `Internet.send_ip` applies the provider protection too.
- Worm exploits are real packets (TCP SYN carrying `ExploitPayload`) caught by `malware.on_packet` in
  `Host.deliver_protocol` and `Server.deliver_protocol`; malware log messages go through `tr()` (Russian keys).
- `Port.is_up` is hot (every packet): keep `Link.too_long` cheap (fast path when nothing is placed).
- Exercising the real GUI: drive `MainWindow` with QTest (menus via `QAction.trigger()`, shortcuts sent to
  the activated window's `view`), answer `QInputDialog`/`QFileDialog` by monkeypatching, record
  `sys.excepthook` (exceptions in Qt slots) and `qInstallMessageHandler`. Run it maximized on the real
  platform and look at the screenshots; this found most of the bugs fixed after 0.4.0.
- **Do not write Python through bash heredocs** on this Windows machine: `\n` escapes inside the code
  get mangled (unterminated strings). Write a script file with the Write tool and run it instead.
- Keep code **Python 3.11 compatible**:
  - no same-quote nesting inside f-strings;
  - no backslashes inside f-string expressions.
- Nested `P.kw` trees: count parentheses carefully, mismatches were frequent.
- The doc examples all run on **one** device in sequence, so later sections must not conflict with
  earlier ones (use unique VLAN names and addresses).
- Shortcut F5 is taken by simulation Play; lab check is F6.
- `MainWindow.shutdown()` is idempotent (guarded); the collaboration session is stopped there.
- Off-screen screenshots need fonts: `QT_QPA_FONTDIR=C:/Windows/Fonts` (`tools/screenshots.py` sets it).
- Wide widgets inside docks force the main window layout (the simulation filter row once made the dock
  about 1250 px wide); use grids or wrapping.
- `QSettings` stores the UI language, theme and scale. GUI tests restore light theme / 100 %.
- Keep app-wide Qt changes (`QApplication.setFont`, palette) out of `test_gui.py`: after the many windows
  its tests leave behind they crashed Windows + Python 3.11 in CI (heap corruption). The theme/scale test
  lives in `tests/test_appearance.py`. Close Qt sockets with `classroom.close_socket()` (block signals,
  abort, deleteLater), never a bare `disconnectFromHost()` on an object about to be dropped.

## History (what has been built, in order)
1. **MVP**: canvas, cables, Ethernet/ARP/IPv4/ICMP, static routing, Cisco CLI, save/load, simulation mode,
   PyInstaller build.
2. **Vendors and protocols**:
   - MikroTik, Juniper, Huawei, then Extreme; Windows and Linux host shells;
   - VLAN/trunk, STP, OSPF, BGP, RIP, ACL, NAT, DHCP, DNS, HTTP, IPv6;
   - L3 switching on every vendor; Telnet/SSH between devices; IPv6 traceroute.
3. **0.2.0**:
   - Internet cloud, EtherChannel, HSRP/VRRP, OSPFv3/RIPng/DHCPv6, port security/DHCP snooping, Wi-Fi;
   - labs with checks, undo, notes/frames, PNG/SVG export;
   - icon style "A" (SVG), app icon variant 1 "Topology", installer script.
4. **0.3.0**:
   - real OSPFv3 (LSA/areas, O/OI), NAT64, GRE/IPsec, QoS, Syslog/SNMP/NTP;
   - VoIP (IP phone with PC passthrough port, voice VLAN, CME, RTP), Cisco `interface range`;
   - 12 ready-made labs; teacher mode with encrypted solution, fault injection and reports;
   - dark theme and UI scale, LAN collaboration;
   - English README and screenshots; first git commit; CI test matrix.

5. **0.4.0**:
   - vendor gaps closed: QoS and voice VLAN on every vendor, IPsec on every router, NAT64 for TCP/UDP;
   - EIGRP (`routing/eigrp.py`), IS-IS (`isis.py`), MPLS/LDP (`mpls.py`), VRF-lite (`Port.vrf`, `Router.vrf_ctx`);
   - stateful firewall (`security/stateful.py`: Cisco ZBF, SRX zones), 802.1X + RADIUS, AAA/TACACS+
     (`services/aaa.py`); TFTP/FTP/SMTP/POP3 (`services/files.py`), DHCP option 82;
   - devices: Cisco ASA (`cli/asa.py`) and FortiGate (`cli/fortigate.py`) selected by `DeviceModel.dialect`,
     WLC + lightweight AP (`devices/wlc.py`), IoT (`devices/iot.py`);
   - labs 13-19, classroom mode (`core/classroom.py`, `gui/classroom.py`, TCP 47900), failed-check hints
     (`core/diagnose.py`);
   - packet analyzer (`gui/capture_panel.py`, `core/pcap.py` writes real wire formats), physical view
     (`Device.location`, `Link.media`/`length_m`, copper > 100 m keeps the link down), terminal highlighting,
     next-keyword hint and Ctrl+F command search (`gui/terminal_tools.py`; the terminal tab is now a page
     widget, use `dlg.terminal.parentWidget()` with `tabs.setCurrentWidget`);
   - update checker (`gui/updater.py`); packaged self-test `--self-test <dir>` run by CI on all three OSes
     (screenshots uploaded as artifacts); CI tests Python 3.11-3.15;
   - EIGRP for IPv6 (`v6igp.Eigrp6`) and IS-IS for IPv6 (TLV 236 in `isis.py`), MPLS L3VPN (VPNv4 in
     `bgp.py`, VPN labels / `_send_vpn` in `router.py`), RSVP-TE (`routing/te.py`, `TunnelPort.tmode == "te"`;
     note `Port.mode` is the switchport mode, do not reuse it), secondary IPv4 addresses (`Port.ip2`).
6. **0.5.0, DDoS**: `devices/botnet.py` (category "attack"), `security/ddos.py` (`Attack`, `FloodTag`,
   `DdosMonitor` = `topology.ddos`, server `guard()`), `gui/ddos_panel.py` (attacks, provider protection,
   server guard, Tools → Load and DDoS); uRPF/null routes on every vendor, Cisco TCP intercept, cloud
   scrub/RTBH/FlowSpec (`Internet.protect`); lab checks `available`/`ddos`, directives `@ddos`/`@isp`/`@attack`;
   labs 20-25; docs `docs/*/ddos.md` (help topic "ddos"). DNS switching: `Internet.zones` (DNS hosting,
   TTL), `shield` sends the domain to the reverse proxy 198.51.100.200 (`Internet._shield`); clients cache
   records with a TTL (`Host.dns_expiry`, `services.dns.cached`); botnets can attack a domain name.
7. **0.5.0, Malware**: `security/malware.py` (families smb-worm, ransomworm + kill switch, iot-botnet,
   trojan + C2; `sec(dev)` host state, `dev.infection`, `MalwareMonitor` = `topology.malware`, `outbreak()`
   what-if on a copy), host CLI in `cli/hostsec.py` (netsh advfirewall, sc, wusa, wmic qfe, MpCmdRun, wbadmin;
   ufw, clamav), Cisco `ip ips`, cloud update.example/c2.example, `gui/malware_panel.py` (Security tab,
   Tools → Network infection); checks clean/immune/outbreak/c2blocked, `@sec` directives; labs 26-30;
   docs `docs/*/malware.md` (help topic "malware"). SambaCry (`samba-worm`, 445) and `ssh-worm` (22) target
   Linux (sec flags samba/ssh_weak/fail2ban); IPS also on FortiGate (`config ips sensor`) and Juniper IDP.
8. **0.5.0, L2 attacks**: `security/l2attack.py` (arp-spoof, mac-flood, stp-root; `L2Attacker` in
   `devices/attacker.py`, switch state `l2sec()`, `intercepted_by()`), switch hooks in `devices/l2.py` (DAI,
   BPDU/Root Guard, CAM overflow), Cisco `ip arp inspection`/`ip source binding`/`spanning-tree bpduguard`,
   `gui/l2attack_panel.py`; check `mitm`, directive `@l2`; labs 31-33; docs `docs/*/l2.md` (topic "l2").
9. **0.5.0, SIEM** (item 4): `core/siem.py` aggregates the DDoS/malware/L2 monitors + device syslog buffers
   into `alerts()`/`events()`; `gui/siem_panel.py` (Tools → Monitoring, Ctrl+Shift+M); lab 35; docs
   `docs/*/siem.md` (help topic "siem"). Also: router/switch `pps_limit` (DDoS by packets/s).
10. **0.5.0, attack-and-defend class** (item 5): `TeacherClass.set_attacks()` broadcasts start/stop to every
    student; the student applies it with `siem.set_all_attacks()` and reports its SIEM alert count back;
    `Roster.alerts()`/`Student.status`, RosterPanel status column + attack buttons.

## Known gaps / ideas for next time (also listed in README "Limitations")
- CME and HSRP only on Cisco (Cisco technologies; VRRP everywhere) - by design.
- L3VPN and RSVP-TE only in the Cisco CLI; RSVP signalling is computed, not sent as packets.
- STP is computed centrally (no max-age timers).
- Collaboration shares the project, not the running simulation (each peer simulates locally; last writer
  wins on concurrent edits of one device).
- Linux/macOS have never been run by hand; CI covers them (tests + packaged self-test with screenshots).
  Check the latest Actions run first.
- Lab texts: the Russian is the source, English sits in `tools/labs_en.py`. A new lab needs both;
  `make_labs.py` asserts the check counts match.
