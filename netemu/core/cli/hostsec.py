"""Host security commands for malware training (security/malware.py).

Windows: netsh advfirewall firewall, sc (the Server service = SMB file sharing), wusa / wmic qfe (patches),
MpCmdRun (Microsoft Defender), wbadmin (backup), shutdown /r.
Linux: ufw, apt install clamav / apt upgrade, freshclam, clamscan, reboot."""
from __future__ import annotations

from ..security import malware as mw

NOT_MINE = object()

WIN_HELP = """Security (malware training):
netsh      netsh advfirewall firewall add rule name="<n>" dir=in action=block protocol=TCP localport=<port>
           netsh advfirewall firewall delete rule name="<n>" | show rule name=all
sc         sc query lanmanserver | sc stop|start lanmanserver | sc config lanmanserver start= disabled|auto
wusa       wusa KB4012212.msu [/quiet]       (install the MS17-010 update)    wmic qfe list [brief]
MpCmdRun   MpCmdRun -SignatureUpdate | MpCmdRun -Scan -ScanType 1|2           (Microsoft Defender)
wbadmin    wbadmin start backup | wbadmin start recovery | wbadmin get versions
shutdown   shutdown /r                        (restart)"""


def _kv(tokens) -> dict:
    out = {}
    for t in tokens:
        k, eq, v = t.partition("=")
        if eq:
            out[k.lower()] = v.strip('"')
    return out


def windows(session, cmd: str, args: list[str], line: str):
    d = session.device
    if d.category not in ("pc", "server"):
        return NOT_MINE
    cmd = cmd.rsplit("\\", 1)[-1].lower()
    if cmd.endswith(".exe"):
        cmd = cmd[:-4]
    low = [a.lower() for a in args]
    s = mw.sec(d)
    if cmd == "netsh" and low[:1] == ["advfirewall"]:
        return _advfirewall(d, s, low[1:], args[1:])
    if cmd == "sc" and len(low) >= 2 and low[1] in ("lanmanserver", "server"):
        verb = low[0]
        if verb == "query":
            st = "4  RUNNING" if s["smb"] else "1  STOPPED"
            return (f"\nSERVICE_NAME: LanmanServer\n        TYPE               : 30  WIN32\n"
                    f"        STATE              : {st}")
        if verb == "stop":
            if not s["smb"]:
                return "[SC] ControlService FAILED 1062:\n\nThe service has not been started."
            s["smb"] = False
            d.engine.emit("device_changed", d)
            return "\nSERVICE_NAME: LanmanServer\n        STATE              : 3  STOP_PENDING"
        if verb == "start":
            s["smb"] = True
            d.engine.emit("device_changed", d)
            return "\nSERVICE_NAME: LanmanServer\n        STATE              : 2  START_PENDING"
        if verb == "config":
            mode = low[low.index("start=") + 1] if "start=" in low and low.index("start=") + 1 < len(low) else ""
            if mode in ("disabled", "demand"):
                s["smb"] = False
            elif mode == "auto":
                s["smb"] = True
            else:
                return "DESCRIPTION:\n        Modifies a service entry: sc config lanmanserver start= disabled|auto"
            d.engine.emit("device_changed", d)
            return "[SC] ChangeServiceConfig SUCCESS"
        return "DESCRIPTION:\n        sc query|stop|start|config lanmanserver"
    if cmd == "wusa":
        if not args:
            return "Windows Update Standalone Installer\n\nwusa <update>.msu [/quiet] [/norestart]"
        kb = args[0].upper().split(".")[0].split("-")[0]
        kb = next((k for k in mw.KB_TO_VULN if k in args[0].upper()), kb)
        vuln = mw.KB_TO_VULN.get(kb)
        if vuln is None:
            return "The update is not applicable to your computer."
        if vuln in s["patches"]:
            return f"Update for Windows ({kb}) is already installed on this computer."
        mw.patch(d, vuln)
        return None if "/quiet" in low else f"Installation complete: Security Update for Windows ({kb})."
    if cmd == "wmic" and low[:2] == ["qfe", "list"]:
        rows = ["HotFixID   Description      InstalledOn"]
        for v in s["patches"]:
            rows.append(f"{mw.PATCHES.get(v, (v,))[0]:<10} Security Update  11/20/2026")
        return "\n".join(rows) if len(rows) > 1 else "No Instance(s) Available."
    if cmd == "mpcmdrun":
        if not s["av"]:
            return "Microsoft Defender Antivirus is turned off."
        if "-signatureupdate" in low:
            session.write("Signature update started . . .\n")
            return mw.update_sigs(d, lambda ok, msg: session.write(msg + "\n"))
        if "-scan" in low:
            found, rep = mw.av_scan(d)
            return "Scan starting...\nScan finished.\n" + rep
        return ("Usage: MpCmdRun.exe -SignatureUpdate | -Scan -ScanType 1|2\n"
                f"Signature version: {s['av_sigs']} (latest {mw.LATEST_SIGS})")
    if cmd == "wbadmin":
        if low[:2] == ["start", "backup"]:
            return mw.backup(d)
        if low[:2] == ["start", "recovery"]:
            return mw.restore(d)
        if low[:2] == ["get", "versions"]:
            b = s["backup"]
            return "No backups found." if not b else \
                f"Backup time: virtual {int(b['at'] / 1000)} s\nBackup target: Fixed Disk labeled E:\nVersion identifier: 01/01/2026-00:00"
        return "wbadmin start backup | start recovery | get versions"
    if cmd == "shutdown" and ("/r" in low or "-r" in low):
        return mw.reboot(d)
    return NOT_MINE


def _advfirewall(d, s, low, toks):
    if low[:1] != ["firewall"]:
        return "The following command was not found: advfirewall " + " ".join(toks)
    verb = low[1] if len(low) > 1 else ""
    kv = _kv(toks[2:] if verb == "show" else toks[3:])
    if verb == "add" and low[2:3] == ["rule"]:
        if not {"name", "dir", "action"} <= set(kv):
            return "A specified value is not valid.\n\nUsage: add rule name=<string> dir=in|out action=allow|block|bypass ..."
        if kv["action"].lower() == "block" and kv["dir"].lower() == "in" and kv.get("localport", "").isdigit():
            s["fw_rules"].append({"name": kv["name"], "port": kv["localport"], "proto": kv.get("protocol", "TCP").upper()})
            d.engine.emit("device_changed", d)
        return "Ok."
    if verb == "delete" and low[2:3] == ["rule"]:
        n = len(s["fw_rules"])
        s["fw_rules"] = [r for r in s["fw_rules"] if r["name"] != kv.get("name")]
        gone = n - len(s["fw_rules"])
        d.engine.emit("device_changed", d)
        return f"\nDeleted {gone} rule(s).\nOk." if gone else "No rules match the specified criteria."
    if verb == "show" and low[2:3] == ["rule"]:
        if not s["fw_rules"]:
            return "No rules match the specified criteria."
        out = []
        for r in s["fw_rules"]:
            out += ["", f"Rule Name:                            {r['name']}",
                    "----------------------------------------------------------------------",
                    "Enabled:                              Yes", "Direction:                            In",
                    f"Protocol:                             {r.get('proto', 'TCP')}",
                    f"LocalPort:                            {r['port']}", "Action:                               Block"]
        return "\n".join(out + ["Ok."])
    return "The following command was not found: advfirewall " + " ".join(toks)


def linux(session, cmd: str, a: list[str], root: bool):
    d = session.device
    s = mw.sec(d)
    if cmd == "ufw":
        if a[:1] == ["status"]:
            if not s["fw_rules"]:
                return "Status: active"
            rows = ["Status: active", "", "To                         Action      From",
                    "--                         ------      ----"]
            rows += [f"{r['port'] + '/tcp':<27}DENY        Anywhere" for r in s["fw_rules"]]
            return "\n".join(rows)
        if not root:
            return "ERROR: You need to be root to run this script"
        if len(a) >= 2 and a[0] in ("deny", "allow", "reject"):
            port = a[1].split("/")[0]
            if not port.isdigit():
                return f"ERROR: Bad port '{a[1]}'"
            s["fw_rules"] = [r for r in s["fw_rules"] if r["port"] != port]
            if a[0] != "allow":
                s["fw_rules"].append({"name": f"ufw {a[0]} {port}", "port": port, "proto": "TCP"})
            d.engine.emit("device_changed", d)
            return "Rule added"
        return "Usage: ufw status | ufw deny|allow <port>[/tcp]"
    if cmd == "apt":
        if not root:
            return "E: Could not open lock file /var/lib/dpkg/lock-frontend - open (13: Permission denied)"
        if a[:2] == ["install", "clamav"] or a[:3] == ["install", "-y", "clamav"]:
            s["av"] = True
            d.engine.emit("device_changed", d)
            return "Setting up clamav (1.0.7+dfsg-1ubuntu1) ..."
        if a[:1] in (["upgrade"], ["full-upgrade"]):
            for v in mw.PATCHES:
                mw.patch(d, v)
            return "Calculating upgrade... Done\nThe following packages will be upgraded: (security updates)\nDone."
        return "E: Invalid operation " + " ".join(a)
    if cmd in ("freshclam", "clamscan"):
        if not s["av"]:
            return f"{cmd}: command not found (sudo apt install clamav)"
        if cmd == "freshclam":
            if not root:
                return "ERROR: Can't open /var/log/clamav/freshclam.log in append mode (check permissions!)."
            return mw.update_sigs(d, lambda ok, msg: session.write(msg + "\n"))
        found, rep = mw.av_scan(d)
        return "----------- SCAN SUMMARY -----------\n" + rep
    if cmd == "reboot":
        if not root:
            return "Call to Reboot failed: Access denied"
        return mw.reboot(d)
    return NOT_MINE
