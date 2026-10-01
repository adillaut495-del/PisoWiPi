"""Prove the Raspberry Pi can really drive the MikroTik hotspot before going live.

Run it on the Pi from the project folder, with the same ``.env`` the service uses:

    python router/verify_router.py            # full check (creates and removes a test user)
    python router/verify_router.py --no-write # read-only: skip the create/delete test

It talks to the router through ``hardware.py``, so a green run means the exact
calls ``app.py`` makes at runtime work: REST health, the hotspot tables, and the
create/extend/revoke cycle used when a customer pays.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hardware  # noqa: E402  (import after the path fix so the repo root is importable)

TEST_MAC = "02:00:00:00:00:01"  # locally administered, never a real customer device


class Report:
    def __init__(self):
        self.failed = 0

    def check(self, label, ok, detail=""):
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}{(' - ' + detail) if detail else ''}")
        if not ok:
            self.failed += 1

    def note(self, text):
        print(f"        {text}")


def timed(call, *args, **kwargs):
    started = time.monotonic()
    try:
        return call(*args, **kwargs), round((time.monotonic() - started) * 1000, 1), None
    except Exception as error:  # requests errors, JSON errors, RouterOS 4xx/5xx
        return None, round((time.monotonic() - started) * 1000, 1), error


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Verify the MikroTik REST path used by PisoPilot.")
    parser.add_argument("--no-write", action="store_true", help="skip the create/delete test user")
    parser.add_argument("--mac", default=TEST_MAC, help=f"MAC used for the write test (default: {TEST_MAC})")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    base = hardware.HardwareConfig()
    adapter = hardware.MikroTikRouterAdapter(dataclasses.replace(base, mode="real"))
    report = Report()

    print("PisoPilot router check")
    print(f"  URL        : {base.router_url}")
    print(f"  API user   : {base.router_user}")
    print(f"  Verify SSL : {base.router_verify_ssl}")
    if base.mode != "real":
        print("  App mode   : simulation (the app will not drive the router until this is 'real')")
    print()

    print("1. Reachability and credentials")
    resource, ms, error = timed(adapter._request, "GET", "system/resource")
    if isinstance(resource, list):
        resource = resource[0] if resource else {}
    report.check("GET /rest/system/resource", error is None, f"{ms} ms" if error is None else str(error))
    if error is None and isinstance(resource, dict):
        report.note(f"board {resource.get('board-name', '?')} / RouterOS {resource.get('version', '?')}")
        report.note(f"uptime {resource.get('uptime', '?')}, CPU load {resource.get('cpu-load', '?')}%")
        version = str(resource.get("version", ""))
        report.check("RouterOS 7.9 or newer (REST over https + www-ssl)", version.startswith("7."), version or "unknown")
    print()

    print("2. Hotspot layout")
    server, ms, error = timed(adapter._request, "GET", "ip/hotspot")
    servers = server if isinstance(server, list) else ([server] if server else [])
    report.check("GET /rest/ip/hotspot", error is None, f"{len(servers)} server(s)" if error is None else str(error))
    for entry in servers:
        report.note(f"server '{entry.get('name')}' on {entry.get('interface')} profile {entry.get('profile')}")

    profiles, ms, error = timed(adapter._request, "GET", "ip/hotspot/profile")
    profiles = profiles if isinstance(profiles, list) else ([profiles] if profiles else [])
    report.check("GET /rest/ip/hotspot/profile", error is None, f"{ms} ms" if error is None else str(error))
    mac_login = any("mac" in str(entry.get("login-by", "")) for entry in profiles)
    report.check("a hotspot profile offers MAC login", mac_login, "login-by must include mac")
    for entry in profiles:
        report.note(f"profile '{entry.get('name')}': login-by={entry.get('login-by')} dns-name={entry.get('dns-name')}")

    user_profiles, ms, error = timed(adapter._request, "GET", "ip/hotspot/user/profile")
    user_profiles = user_profiles if isinstance(user_profiles, list) else ([user_profiles] if user_profiles else [])
    report.check("GET /rest/ip/hotspot/user/profile", error is None, f"{ms} ms" if error is None else str(error))
    names = [str(entry.get("name")) for entry in user_profiles]
    report.check(f"app profile '{base.router_default_profile}' exists", base.router_default_profile in names, ", ".join(names))
    for entry in user_profiles:
        report.note(f"user profile '{entry.get('name')}': rate-limit={entry.get('rate-limit') or '-'} shared-users={entry.get('shared-users')}")

    garden, _, error = timed(adapter._request, "GET", "ip/hotspot/walled-garden")
    garden = garden if isinstance(garden, list) else ([garden] if garden else [])
    garden_ip, _, _ = timed(adapter._request, "GET", "ip/hotspot/walled-garden/ip")
    garden_ip = garden_ip if isinstance(garden_ip, list) else ([garden_ip] if garden_ip else [])
    report.check("walled garden lets unpaid clients reach the portal", bool(garden or garden_ip), f"{len(garden)} host rule(s), {len(garden_ip)} ip rule(s)")

    hosts, ms, error = timed(adapter._request, "GET", "ip/hotspot/host")
    hosts = hosts if isinstance(hosts, list) else ([hosts] if hosts else [])
    report.check("GET /rest/ip/hotspot/host (client IP -> MAC)", error is None, f"{len(hosts)} host(s), {ms} ms" if error is None else str(error))
    if hosts:
        sample = next((entry for entry in hosts if entry.get("address")), None)
        if sample:
            report.note(f"mac_for_ip({sample.get('address')}) -> {adapter.mac_for_ip(sample.get('address'))}")
    print()

    print("3. Captive portal redirect")
    ports, _, error = timed(adapter._request, "GET", "interface/bridge/port")
    ports = ports if isinstance(ports, list) else ([ports] if ports else [])
    for entry in servers:
        interface = str(entry.get("interface"))
        wired = [
            str(port.get("interface"))
            for port in ports
            if str(port.get("bridge")) == interface and str(port.get("interface", "")).startswith("ether")
        ]
        report.check(
            "HotSpot interface '" + interface + "' carries no wired port",
            not wired,
            "behind the portal: " + ", ".join(wired) if wired else "wireless only - the wired LAN keeps its internet",
        )
    if error is not None:
        report.note("could not read /rest/interface/bridge/port: " + str(error))

    files, _, error = timed(adapter._request, "GET", "file")
    files = files if isinstance(files, list) else ([files] if files else [])
    hotspot_files = sorted(str(item.get("name", "")) for item in files if "hotspot" in str(item.get("name", "")))
    if hotspot_files:
        report.note("what the router has under hotspot/: " + ", ".join(hotspot_files))
        if hotspot_files == ["hotspot"]:
            report.note("only the bare folder came back, so /rest/file may not list inside it.")
            report.note('Settle it on the router: /file print where name~"hotspot"')
    else:
        report.note("NOTHING under hotspot/ is visible to /rest/file - the login page cannot be served.")
        report.note('Confirm on the router: /file print where name~"hotspot"')
    page = next((item for item in files if str(item.get("name", "")).endswith("hotspot/login.html")), None)
    page_size = int(page.get("size") or 0) if page else 0
    report.check(
        "hotspot/login.html is installed (what a blocked phone is served)",
        page_size > 500,
        str(page_size) + " bytes" if page else "MISSING - the customer gets the RouterOS login form",
    )
    if page_size <= 500:
        report.note("is app.py really serving it? Test on the Pi first:")
        report.note("  curl -sS -o /dev/null -w '%{http_code}' http://192.168.88.2:5000/hotspot/login.html")
        report.note("then install it from the router terminal:")
        report.note('  /tool fetch url="http://192.168.88.2:5000/hotspot/login.html" dst-path=hotspot/login.html')
        report.note('  /file print where name~"hotspot"')
        report.note("and read the import log for the warning:")
        report.note('  /log print where message~"PisoPilot: hotspot/login.html"')
    # Only the profiles the HotSpot servers actually use: the stock "default"
    # profile is not in service, so its empty dns-name is not a fault.
    live_profiles = [
        row for row in profiles if str(row.get("name")) in {str(server.get("profile")) for server in servers}
    ] or profiles

    # api.json is what a phone fetches to pop the portal open by itself. RouterOS
    # only publishes it when the profile can also serve the login page over HTTPS
    # (RFC 7710 wants ssl-certificate), so on firmware without that property
    # api.json is unreachable by design - failing the check for it would be a
    # permanent red mark against a setting nobody can change.
    api = next((item for item in files if str(item.get("name", "")).endswith("hotspot/api.json")), None)
    if any("ssl-certificate" in row for row in live_profiles):
        report.check("hotspot/api.json is installed (RFC 7710 self-opening portal)", api is not None)
    elif api is None:
        report.note("hotspot/api.json is absent, which is expected here: with no ssl-certificate on")
        report.note("the profile RouterOS cannot send the RFC 7710 DHCP option, so there is nothing")
        report.note("for api.json to advertise. Customers open a plain http:// address instead.")
    else:
        report.note("hotspot/api.json exists but the profile has no ssl-certificate, so a phone")
        report.note("still will not auto-open the portal. A plain http:// address works.")

    addresses, _, _ = timed(adapter._request, "GET", "ip/address")
    addresses = addresses if isinstance(addresses, list) else ([addresses] if addresses else [])
    router_ips = {str(item.get("address", "")).split("/")[0] for item in addresses}
    static, _, _ = timed(adapter._request, "GET", "ip/dns/static")
    static = static if isinstance(static, list) else ([static] if static else [])
    for entry in live_profiles:
        profile_name = str(entry.get("name"))
        dns_name = str(entry.get("dns-name", ""))
        if not dns_name:
            report.check("profile '" + profile_name + "' sets dns-name", False, "empty - the login URL falls back to the HotSpot address")
            continue
        record = next((row for row in static if str(row.get("name")) == dns_name), None)
        answer = str(record.get("address", "")) if record else ""
        detail = answer or "no static DNS record"
        report.check("dns-name '" + dns_name + "' answers with the router", answer in router_ips, "-> " + detail)
        if answer and answer not in router_ips:
            report.note("this is what stops the redirect: RouterOS builds the login page URL from")
            report.note("dns-name, so it has to point at the router, never at the Pi")
        report.note(
            "profile '{}': hotspot-address={} https-redirect={} ssl-certificate={}".format(
                profile_name,
                entry.get("hotspot-address"),
                entry.get("https-redirect", "not exposed"),
                entry.get("ssl-certificate", "not exposed"),
            )
        )
        if "ssl-certificate" not in entry:
            report.note("this firmware does not expose 'ssl-certificate' on the HotSpot profile,")
            report.note("so the RFC 7710 DHCP option is unavailable and a phone will not open")
            report.note("the portal by itself. Type a plain http:// address instead.")
            report.note("Confirm on the router with: /ip/hotspot/profile print detail")
    print()


    print("4. What app.py does when a customer pays")
    if args.no_write:
        report.note("skipped (--no-write)")
    else:
        _, ms, error = timed(adapter.authorize, args.mac, 2)
        report.check(f"create hotspot user {args.mac} for 2 minutes", error is None, f"{ms} ms" if error is None else str(error))
        if error is None:
            found, _, error = timed(adapter.hotspot_user, args.mac)
            report.check("read the user back", bool(found) and error is None, f"limit-uptime={found.get('limit-uptime')} profile={found.get('profile')}" if found else str(error))
            _, ms, error = timed(adapter.authorize, args.mac, 5)
            report.check("extend the user to 5 minutes", error is None, f"{ms} ms" if error is None else str(error))
            _, ms, error = timed(adapter.revoke, args.mac)
            report.check("revoke the user again", error is None, f"{ms} ms" if error is None else str(error))
            leftover, _, _ = timed(adapter.hotspot_user, args.mac)
            report.check("no test user left behind", leftover is None)
    print()

    print("5. Per-session QoS mapping")
    for download, upload in ((2, 1), (5, 2), (9, 9)):
        report.note(f"{download} Mbps down / {upload} Mbps up -> {adapter.rate_profile(download, upload)}")
    print()

    if report.failed:
        print(f"{report.failed} check(s) failed - fix them before setting PISO_HARDWARE_MODE=real.")
        return 1
    print("All checks passed. Safe to set PISO_HARDWARE_MODE=real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
