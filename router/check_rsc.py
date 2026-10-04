"""Static sanity check for the RouterOS import script.

The import stops dead at the first token RouterOS cannot parse, and a live
piso router is a bad place to find that out. This mirrors the rules the
troubleshooting table in README.md documents, plus the new firewall/IPv6
sections, so a typo is caught on the laptop instead of on the router.

    python router/check_rsc.py            # check router/hap-ax-lite-piso.rsc

Exits 0 when the script looks importable, 1 when it does not.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "hap-ax-lite-piso.rsc"

# Every line that is only a RouterOS statement, i.e. a command or a control
# keyword. Used to tell a comment line from a command with a trailing comment.
STATEMENT = re.compile(r"^\s*(/|:|\})")

# Property names this script is allowed to set, per menu.
#
# An unknown property is a PARSE error, not a runtime one: RouterOS rejects the
# token while reading the line and stops the whole import there, so an
# on-error block never gets a chance to run. That is exactly how
# "disable-advertise=yes" stopped the import at line 350 - the property on
# /ipv6/nd/prefix is "advertise=no". This table is the guard against a repeat.
#
# Deliberately scoped to the menus this script touches, and to the properties
# it actually sets - it is not a general RouterOS schema. A property that is
# real but missing here is a false positive, so add it rather than remove the
# check. Dotted names ("configuration.ssid") are sub-object properties and are
# allowed on their parent menu.
KNOWN_PROPERTIES: dict[str, set[str]] = {
    "/interface/bridge": {"name", "comment"},
    "/interface/bridge/port": {"bridge", "interface", "comment"},
    "/interface/wifi": {
        "disabled",
        "configuration.ssid",
        "configuration.country",
        "security.authentication-types",
        "security.passphrase",
        # Deliberately NOT listing any client-isolation property here. The name
        # moved between RouterOS releases (client-isolation on older builds,
        # isolation-type on some 7.13+ ones), and an unknown property is a parse
        # error that stops the import outright. Since hAP ax lite firmware in
        # the field has been seen to reject isolation-type, the radio setting is
        # done in WinBox and the firewall rule is what the script owns.
    },
    "/ip/address": {"address", "interface", "comment"},
    "/ip/pool": {"name", "ranges", "comment"},
    "/ip/dhcp-server": {
        "name",
        "interface",
        "address-pool",
        "lease-time",
        "disabled",
        "comment",
        # NOT "option-set". Verified on 7.24.4:
        #   /ip/dhcp-server set x option-set=y   -> bad parameter option-set
        "dhcp-option-set",
    },
    # RFC 8910 / option 114, with the two property names that are easy to get
    # wrong. Both were confirmed by the router refusing the singular form:
    #   /ip/dhcp-server/option/sets set x option=y  -> action cancelled
    #   value="https://..."                          -> Unknown data type!
    # A bare string is not a data type: the value needs single quotes INSIDE the
    # double quotes, i.e. value="'https://...'", or the option is refused.
    "/ip/dhcp-server/option": {"name", "code", "value", "force", "comment"},
    "/ip/dhcp-server/option/sets": {"name", "options", "comment"},
    "/ip/dhcp-server/network": {"address", "gateway", "dns-server", "netmask", "comment"},
    "/ip/dhcp-server/lease": {"address", "mac-address", "server", "comment"},
    "/ip/dhcp-client": {"interface", "disabled", "comment"},
    "/ip/dns": {"allow-remote-requests", "servers", "cache-size"},
    "/ip/dns/static": {"name", "address", "comment"},
    "/ip/service": {"disabled", "certificate", "port", "address"},
    "/ip/firewall/nat": {"chain", "out-interface", "src-address", "action", "comment"},
    "/ip/firewall/filter": {
        "chain",
        "action",
        "connection-state",
        "in-interface",
        "dst-address",
        "hotspot",
        "comment",
        "log",
        "disabled",
    },
    "/ipv6/address": {"address", "interface", "advertise", "actual-interface", "comment"},
    "/ipv6/nd/prefix": {"prefix", "interface", "advertise", "autoconf", "on-link", "comment"},
    "/ipv6/firewall/filter": {"chain", "action", "in-interface", "comment", "connection-state"},
    "/ip/hotspot": {"name", "interface", "profile", "address-pool", "addresses-per-mac", "login-timeout"},
    "/ip/hotspot/profile": {
        "name",
        "hotspot-address",
        "dns-name",
        "html-directory",
        "login-by",
        "mac-auth-mode",
        "http-cookie-lifetime",
        "split-user-domain",
        "use-radius",
        # Set in the RFC 8910 section so the /api probe is served over https.
        "ssl-certificate",
    },
    "/ip/hotspot/user/profile": {
        "name",
        "rate-limit",
        "shared-users",
        "keepalive-timeout",
        "idle-timeout",
        "session-timeout",
        "add-mac-cookie",
        "mac-cookie-timeout",
        "open-status-page",
    },
    "/ip/hotspot/ip-binding": {"type", "address", "mac-address", "server", "comment"},
    "/ip/hotspot/walled-garden": {"action", "dst-host", "dst-port", "protocol", "comment", "disabled"},
    "/ip/hotspot/walled-garden/ip": {"action", "dst-address", "dst-port", "protocol", "comment", "disabled"},
    "/user": {"name", "group", "password"},
    "/user/group": {"name", "policy"},
    "/certificate": {"name", "common-name", "days-valid", "key-usage", "key-size", "country"},
    "/system/identity": {"name"},
    "/system/device-mode": {"hotspot", "fetch", "mode"},
}

# Property names that are known NOT to exist, with the correct spelling. Cheap,
# explicit insurance against the same typo coming back.
WRONG_PROPERTIES = {
    "disable-advertise": "on /ipv6/nd/prefix the property is 'advertise' (use advertise=no)",
    # All four confirmed against a hAP ax lite on 7.24.4. Keys are matched
    # against the bare property name, so they carry no "=".
    "option-set": "on /ip/dhcp-server the property is 'dhcp-option-set' (bad parameter option-set)",
}

# "/menu/path set|add|find ... key=value" -> the menu and the property names.
MENU_COMMAND = re.compile(r"^\s*(/[a-z0-9/-]+)\s+(?:set|add)\b(.*)$")
KEY_VALUE = re.compile(r"([a-zA-Z][a-zA-Z0-9._-]*)=")

# The start of a RouterOS statement: a command path, or a ":" keyword.
#
# A match only counts when it follows whitespace, a brace or the start of the
# line, so a path inside [...] or (...) - which is a data reference, not a new
# command - is never mistaken for one.
STATEMENT_START = re.compile(
    r"(?:(?<=\s)|(?<=[{}]))("
    r":(?:if|else|foreach|for|while|do|log|put|set|local|global|return|error|delay)\b"
    r"|/[a-zA-Z0-9/_-]+)"
)


# Two tokens glued together with no whitespace between them.
#
# RouterOS ends a token at whitespace, so "] $var" is two tokens but "]$var" is
# one unreadable one. This is the same "expected end of command" failure as two
# statements on a line, and it bit twice on real lines:
#   line 189  /ip/firewall/filter move [:pick $pisoRule 0]$pisoPosition
#   line 269  :local loginPageSize [/file get$loginPageId size]
# Both stopped the import at the column of the glued "$".
#
# "key=$var" is deliberately NOT matched: "=" is a legitimate separator, so it is
# left out of the character class.
GLUED_VARIABLE = re.compile(r"[A-Za-z0-9_)\]}]\$")
GLUED_BRACKET = re.compile(r"[A-Za-z0-9_$)\]]\[")


def check_token_separation(lines: list[str]) -> list[str]:
    """Flag a variable or "[" glued to the token before it.

    The column in the router's error message is the column of the glued "$",
    which is what makes this easy to misread as a bad property name.
    """
    problems: list[str] = []
    for number, raw in enumerate(lines, start=1):
        code = code_before_comment(raw)
        if not code.strip():
            continue
        for pattern, what in ((GLUED_VARIABLE, "a variable"), (GLUED_BRACKET, "an opening '['")):
            match = pattern.search(code)
            if match:
                problems.append(
                    f"line {number}: {what} is glued to the token before it at "
                    f"column {match.start() + 2} - put a space there or the import "
                    f"stops with 'expected end of command': {raw.strip()}"
                )
                break
    return problems

# :log, :put, :global and :foreach take a fixed number of arguments, so a
# concatenation has to be wrapped in "(" ")" to arrive as ONE argument.
#
#   :log warning "..." . $portalIp . "..."      <- parse error
#   :log warning ("..." . $portalIp . "...")    <- correct
#
# Unparenthesised, the parser finishes the statement at the end of the first
# string and then trips over the "." - the reported column is the "$" of the
# first glued variable, which is what makes it look like a bad property name.
SINGLE_ARG_COMMANDS = re.compile(r"^:(?:log|put|global|foreach|set|local)\b")


def check_single_argument_commands(lines: list[str]) -> list[str]:
    """Flag a concatenation passed to a one-argument command without parens."""
    problems: list[str] = []
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not SINGLE_ARG_COMMANDS.match(stripped):
            continue

        # Walk the line tracking quote and bracket state; a " . " at depth 0
        # is a top-level concatenation, which needs parentheses here.
        depth = 0
        in_string = False
        index = 0
        while index < len(stripped):
            char = stripped[index]
            if char == '"':
                in_string = not in_string
            elif not in_string:
                if char in "([":
                    depth += 1
                elif char in ")]":
                    depth -= 1
                elif (
                    char == "."
                    and depth == 0
                    and stripped[index - 1: index] == " "
                    and stripped[index + 1: index + 2] == " "
                ):
                    problems.append(
                        f"line {number}: {stripped.split()[0]} gets a concatenation "
                        f"without parentheses - it takes one argument, so the "
                        f"import stops with 'expected end of command'. Wrap it in "
                        f"'( )': {stripped}"
                    )
                    break
            index += 1
    return problems

def check_one_statement_per_line(lines: list[str]) -> list[str]:
    """Flag a line that carries more than one RouterOS statement.

    RouterOS ends a statement at the newline. A line that glues a closing
    "}" to a following command - or two ":local" calls together - is a parse
    error, and the import stops dead on it with "expected end of command"
    pointing at the second statement. That is exactly how line 148
    ("...login-timeout=1m } /ip/hotspot set [find ...]") stopped the import
    at column 155, the "[" of the trailing command.

    Statements inside a "{}" block may share a line with the block itself, so
    a second statement is only illegal at brace depth 0. "}" followed by
    "else" is also legal, since else continues the preceding :if.
    """
    problems: list[str] = []
    for number, raw in enumerate(lines, start=1):
        code = code_before_comment(raw)
        if not code.strip():
            continue
        first = len(code) - len(code.lstrip())
        depth = 0
        cursor = 0
        for match in STATEMENT_START.finditer(code):
            between = code[cursor:match.start()]
            depth += between.count("{") - between.count("}")
            if depth < 0:
                depth = 0  # "}" closing a block opened on an earlier line
            cursor = match.start()
            if match.start() <= first or depth > 0:
                continue  # first token on the line, or inside a block
            if match.group(1) == ":else":
                continue  # "} else={" - legal
            problems.append(
                f"line {number}: '{match.group(1)}' is a second statement on one "
                f"line - RouterOS ends a command at the newline, so the import "
                f"stops here with 'expected end of command': {raw.strip()}"
            )
            break
    return problems


def check_properties(lines: list[str]) -> list[str]:
    """Flag property names that the target menu does not have.

    A parse error here kills the import at that exact line, so this is the one
    failure mode the offline check exists to prevent.
    """
    problems: list[str] = []
    for number, raw in enumerate(lines, start=1):
        line = code_before_comment(raw).strip()
        if not line or line.startswith("#"):
            continue

        match = MENU_COMMAND.match(line)
        if not match:
            continue
        menu, remainder = match.group(1), match.group(2)
        if menu not in KNOWN_PROPERTIES:
            continue  # menu not covered by this table - stay quiet

        # Only the argument list counts: drop anything inside [ ... ] (a
        # [find where ...] selector is not a property being set) and any
        # quoted string.
        remainder = re.sub(r"\[[^\]]*\]", " ", remainder)
        remainder = re.sub(r'"[^"]*"', " ", remainder)

        for key in KEY_VALUE.findall(remainder):
            for wrong, hint in WRONG_PROPERTIES.items():
                if key == wrong:
                    problems.append(f"line {number}: '{key}' does not exist on {menu} - {hint}")
            if key not in KNOWN_PROPERTIES[menu]:
                problems.append(
                    f"line {number}: '{key}' is not a known property of {menu} "
                    f"- an unknown property is a parse error and stops the import here"
                )
    return problems


def local_setting(text: str, name: str) -> str:
    """The value of ':local name "value"' (empty when the script does not set it)."""
    match = re.search(rf'^:local {re.escape(name)}\s+"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def hotspot_interface(text: str) -> str:
    """The interface the HotSpot server is really bound to, variable resolved."""
    for line in text.splitlines():
        match = re.match(r"\s*/ip/hotspot\s+(?:add|set)\b.*?\binterface=(\S+)", line)
        if not match:
            continue
        value = match.group(1)
        return local_setting(text, value[1:]) if value.startswith("$") else value
    return ""


def check_https_login(text: str) -> list[str]:
    """login-by must include https, or no phone raises the sign-in notification.

    A HotSpot only intercepts the protocols listed in login-by. Modern Android,
    iOS and Windows probe their captive-portal URL over HTTPS, so a profile
    without https never answers that probe: the phone sees a network that
    resolves nothing and reports "no internet", while the block itself is
    working perfectly. This was the last reason the portal never appeared.
    """
    problems: list[str] = []
    bare = "\n".join(code_only(line) for line in text.splitlines())

    # Resolve :local variables so a value held in a variable is still judged.
    # login-by=$pisoLoginBy is correct only if the variable contains https, and
    # the raw text of the assignment says nothing about that. The values are read
    # from `text`, NOT from `bare`: code_only() strips quoted strings, which is
    # exactly where a :local keeps its value, so bare would show ":local x " with
    # nothing after it.
    variables = dict(re.findall(r":local\s+([A-Za-z0-9_]+)\s+\"?([A-Za-z0-9,._$/-]+)\"?", text))

    def expand(value: str) -> str:
        for _ in range(5):  # variables may reference other variables
            replaced = re.sub(
                r"\$([A-Za-z0-9_]+)",
                lambda m: variables.get(m.group(1), m.group(0)),
                value,
            )
            if replaced == value:
                break
            value = replaced
        return value

    # Find every login-by assignment and check for https among its values.
    assignments = re.findall(r"\blogin-by=([A-Za-z0-9,$-]+)", bare)
    if not assignments:
        return problems  # login-by is not set by this script at all
    for value in assignments:
        values = [part.strip().lower() for part in expand(value).split(",") if part.strip()]
        if "https" not in values:
            problems.append(
                f"login-by={value} does not include https - a phone probes the captive "
                "portal over HTTPS, so without it the HotSpot never answers and the phone "
                "reports 'no internet' instead of raising the sign-in notification"
            )
    if not re.search(r"/certificate add\b[^\n]*\$hotspotCertName", bare):
        problems.append("no HotSpot certificate is created, so ssl-certificate cannot point at one")
    if not re.search(r"/ip/hotspot/profile\s+set\b[^\n]*ssl-certificate=", bare):
        problems.append(
            "ssl-certificate is never assigned - login-by=https cannot intercept anything "
            "until the profile has a certificate to serve"
        )
    # The certificate must be created BEFORE the profile that references it, or
    # the assignment fails on a fresh router. Compare the FIRST occurrence of
    # each: later copies of ssl-certificate= live inside :log warning strings,
    # and matching those would compare a real assignment against a message.
    created = bare.find("/certificate add")
    assigned = bare.find("/ip/hotspot/profile set")
    assigned_ssl = bare.find("ssl-certificate=")
    if created != -1 and assigned_ssl != -1 and assigned != -1 and assigned < created:
        problems.append(
            "the profile points at ssl-certificate before the certificate is created - "
            "move the /certificate add above the profile block or it fails on a fresh router"
        )
    return problems


def check_option_114_wiring(text: str) -> list[str]:
    """Option 114 only reaches a phone if all three of its steps are present.

    Defining the option on /ip/dhcp-server/option is the step everyone tries,
    and it is not enough on its own: the option has to be collected into a SET
    and that set attached to the guest DHCP server. A script that does the first
    and stops there looks correct and sends nothing, which is the exact symptom
    that prompted this rule.
    """
    problems: list[str] = []
    if ":local enableRfc8910" not in text:
        return problems  # the section was switched off deliberately
    # The on-error blocks below quote the broken commands back to the operator,
    # e.g. "...dhcp-option-set=" inside a :log warning. Searching the raw text
    # finds those echoes and reports the script as correct when it is not, so
    # every rule below runs against a copy with comments and quoted strings
    # removed. That is what makes the check trustworthy.
    bare = "\n".join(code_only(line) for line in text.splitlines())

    if not re.search(r"/ip/dhcp-server/option\s+add\b[^\n]*\bcode=114\b", bare):
        problems.append("no '/ip/dhcp-server/option add ... code=114' - nothing defines the option")
    if not re.search(r"/ip/dhcp-server/option/sets\s+add\b", bare):
        problems.append("no option SET is created - a defined option is never sent without one")
    if not re.search(r"/ip/dhcp-server\s+set\b[^\n]*dhcp-option-set=", bare):
        problems.append(
            "no DHCP server is given dhcp-option-set - RouterOS only sends option 114 "
            "to clients of a server that carries a set (the property is 'dhcp-option-set', "
            "not 'option-set')"
        )
    # It must be the guest server. Pointing the wired LAN at the option set would
    # make the desk computer pop the customer portal too.
    for number, raw in enumerate(text.splitlines(), start=1):
        code = code_before_comment(raw)
        if "/ip/dhcp-server set" in code and "dhcp-option-set=" in code and "$guestDhcpServer" not in code:
            problems.append(
                f"line {number}: the option set is attached to the wrong DHCP server - "
                f"only the guest side may be told about the portal: {code.strip()}"
            )
    if not re.search(r"dst-path=\"hotspot/api\.json\"", text):
        problems.append(
            "api.json is never fetched - option 114 points at a URL that would "
            "answer 404, so the client has nothing to open"
        )
    # The mistakes the router actually reported, kept as rules now that the
    # property table can no longer catch them (they are all *valid* names that
    # mean the wrong thing, or a valid value with no data type).
    if not re.search(r"/ip/dhcp-server/option/sets\s+set\b[^\n]*\boptions=", bare):
        problems.append(
            "the option set is filled with 'options=', not 'option=' - the router "
            "rejects the singular form with 'action cancelled'"
        )
    # "/certificate sign" is NOT a bug in general - the management cert
    # piso-cert is signed successfully on this router. It only fails for the
    # HotSpot cert, where there is no CA to issue it. Scope the rule to the
    # HotSpot block so it cannot flag the line that demonstrably works.
    if re.search(r"certificate sign \$hotspotCertName", bare):
        problems.append(
            "'/certificate sign $hotspotCertName' fails with 'CA not found' - there is "
            "no CA here, and /certificate add already makes a self-signed certificate"
        )
    for number, raw in enumerate(text.splitlines(), start=1):
        code = code_before_comment(raw)
        if "/ip/dhcp-server/option" in code and re.search(r"\bvalue=(?![\"'$])\S", code):
            problems.append(
                f"line {number}: the option value has no data type - RouterOS answers "
                f"'Unknown data type!' unless the string is quoted inside the quotes, "
                f"i.e. value=\"'https://...'\": {code.strip()}"
            )
            break
    return problems


def check_hotspot_scope(text: str) -> list[str]:
    """The HotSpot must cover the SSID only, never the wired LAN.

    A HotSpot covers an entire interface. Binding it to the same bridge as
    ether2-4 - which the first version of this script did - puts the desk
    computer and the console laptop behind the customer login page, so the
    wired LAN loses its internet the moment the gate goes up. The guest SSID
    gets a bridge of its own instead, and this keeps it that way.
    """
    problems: list[str] = []
    lan_bridge = local_setting(text, "lanBridge")
    guest_bridge = local_setting(text, "guestBridge")
    if not guest_bridge:
        return ["no ':local guestBridge' - the SSID needs a bridge of its own"]

    hotspot = hotspot_interface(text)
    if not hotspot:
        problems.append("no '/ip/hotspot add|set ... interface=' line to check")
    elif hotspot == lan_bridge:
        problems.append(
            "the HotSpot is bound to '" + hotspot + "', the wired LAN bridge - "
            "ether2-4 would sit behind the customer login page as well; bind it "
            "to '" + guest_bridge + "'"
        )

    # The radio has to land on the hotspot bridge, or the SSID stays on the
    # wired side where there is no HotSpot at all.
    if not re.search(r"/interface/bridge/port\s+(?:add|set)\b[^\n]*\$guestBridge", text):
        problems.append(
            "no bridge-port line puts the radio on $guestBridge - the SSID would "
            "stay on the wired side, where there is no HotSpot at all"
        )

    # And no gate rule may still name the wired bridge, or the gate is on the
    # wrong side of the router and the desk computer loses its internet.
    for number, raw in enumerate(text.splitlines(), start=1):
        code = code_before_comment(raw)
        if "in-interface=$lanBridge" in code or (lan_bridge and f"in-interface={lan_bridge}" in code):
            problems.append(
                f"line {number}: a gate rule still points at the wired LAN - only "
                f"the guest side may be gated: {code.strip()}"
            )
            break
    return problems


def code_before_comment(line: str) -> str:
    """Return the part of a line that is code: quoted strings dropped, and the
    trailing comment (if any) cut off.

    RouterOS ends a statement at the newline, so a '#' on a command line is
    fatal - but a '#' inside a password is not, and neither is a brace inside a
    quoted string. Both have to go before a line is judged.
    """
    out: list[str] = []
    in_string = False
    for char in line:
        if char == '"':
            in_string = not in_string
        elif char == "#" and not in_string:
            break
        elif not in_string:
            out.append(char)
    return "".join(out)


def code_only(line: str) -> str:
    """The line with both its comment and any quoted string removed."""
    out: list[str] = []
    in_string = False
    for char in line:
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            out.append(char)
    return "".join(out)


def check(path: Path) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    depth = 0

    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue  # blank, or a pure comment: can never stop the import

        code = code_before_comment(line)

        # A '#' after a statement is the documented cause of
        # "expected end of command". The code is shorter than the string-free
        # line only when a real trailing comment was cut off.
        if len(code) < len(code_only(line)) and STATEMENT.match(code) and code.strip():
            problems.append(f"line {number}: '#' trails a command - move the comment above it: {stripped}")

        # Unbalanced quotes are the other classic parse stop.
        if line.count('"') % 2:
            problems.append(f"line {number}: odd number of double quotes: {stripped}")

        # Braces are counted across the whole file, never per line: RouterOS
        # block syntax opens with "do={" and closes on a later line, so a
        # per-line check would flag every block in the script.
        depth += code.count("{") - code.count("}")
        if depth < 0:
            problems.append(f"line {number}: a closing brace with no open block")
            depth = 0

    if depth:
        problems.append(f"script ends {depth} block(s) short of closing - the import would stop")

    # The three things that make a portal a real gate. Their absence is the
    # bug this whole change set fixes, so assert they are present.
    required = {
        "drop unpaid clients (forward chain)": r'in-interface=\$guestBridge[^\n]*hotspot=!auth\s+action=drop',
        "walled garden accept for the portal": r'in-interface=\$guestBridge[^\n]*action=accept[^\n]*comment="PisoPilot: walled garden to the portal"',
        "walled garden accept for router DNS": r'in-interface=\$guestBridge[^\n]*action=accept[^\n]*comment="PisoPilot: walled garden to the router DNS"',
        "IPv6 forward drop for customers": r'/ipv6/firewall/filter add chain=forward[^\n]*in-interface=\$guestBridge[^\n]*action=drop',
        "device-mode hotspot/fetch enabled": r'/system/device-mode set[^\n]*hotspot=yes[^\n]*fetch=yes',
    }
    found: dict[str, int] = {}
    for label, pattern in required.items():
        match = re.search(pattern, text)
        if not match:
            problems.append(f"missing rule: {label}")
        else:
            found[label] = match.start()

    # The walled-garden accepts have to be ABOVE the drop, or an unpaid client
    # is cut off from the portal it is meant to be shown. Source order is the
    # order the rules get appended in.
    portal = found.get("walled garden accept for the portal")
    dns = found.get("walled garden accept for router DNS")
    drop = found.get("drop unpaid clients (forward chain)")
    if None not in (portal, dns, drop) and not (portal < drop and dns < drop):
        problems.append("walled-garden accepts must come before the unpaid-client drop")

    # Settings the operator is expected to change must be declared exactly once.
    for name in (
        "ssid",
        "portalIp",
        "portalPort",
        "apiPassword",
        "blockIpv6FromGuests",
        "guestBridge",
        "guestGateway",
        "hotspotDnsName",
    ):
        hits = len(re.findall(rf'^:local {name}\b', text, re.MULTILINE))
        if hits != 1:
            problems.append(f"setting '{name}' declared {hits} time(s), expected exactly 1")

    problems.extend(check_hotspot_scope(text))
    problems.extend(check_https_login(text))
    problems.extend(check_option_114_wiring(text))
    problems.extend(check_token_separation(lines))
    problems.extend(check_single_argument_commands(lines))
    problems.extend(check_one_statement_per_line(lines))
    problems.extend(check_properties(lines))

    return problems


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else DEFAULT
    if not path.exists():
        print(f"no such file: {path}")
        return 1

    problems = check(path)
    print(f"RouterOS import check: {path.name}")
    if problems:
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(f"\n{len(problems)} problem(s). The import would stop on these.")
        return 1
    print("  [PASS] no trailing comments, quotes and braces balance")
    print("  [PASS] firewall gate, IPv6 shutoff and device-mode block all present")
    print("\nThe script looks importable. Upload it and run:")
    print(f"  /import file-name={path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
