 
:local ssid "A2N Piso WiFi"
 
:local wifiPassword ""
 
:local wifiCountry ""
 
:local portalIp "192.168.88.2"
 
:local portalPort "5000"
 
:local dnsName "portal.piso.local"
# The name the HotSpot itself answers with. RouterOS builds BOTH the login page URL
# and (RFC 7710) the https://<name>/api captive-portal JSON from it, so it has to
# resolve to the ROUTER. It is deliberately not $dnsName: /ip/dns/static points that
# one at the Pi, and a login URL that lands on the Pi's closed port 80 is exactly
# why a blocked phone saw "site can't be reached" instead of the portal.
:local hotspotDnsName "hotspot.piso.local"
 
:local piMac "B8:27:EB:07:89:9A"
# ether2-4 (the desk computer and the Pi) stay on "bridge" and keep plain internet.
# A hotspot covers a whole interface, so the older script binding it to $lanBridge
# put every wired machine behind the login page as well.
:local lanBridge "bridge"
:local gateway "192.168.88.1"
:local lanNetwork "192.168.88.0/24"
# The SSID lives on its own bridge and the hotspot is bound to that one only. That
# is what makes the gate cover the phones and nothing else.
:local guestBridge "bridge-guest"
:local guestGateway "192.168.90.1"
:local guestNetwork "192.168.90.0/24"
:local guestPool "piso-guest-pool"
:local guestDhcpServer "piso-guest-dhcp"
:local hotspotServer "piso-hotspot"
:local hotspotProfile "piso-profile"
 
:local blockIpv6FromGuests "yes"
 
:local packageProfile "piso-package"
 
:local premiumProfile "piso-premium"
:local apiUser "piso-controller"
 
:local apiPassword "9g3zu9g9LQet8HdU1viZ"
:local certName "piso-cert"
# RFC 8910 (option 114) needs its own certificate whose name matches the HotSpot
# dns-name, NOT the router's management cert: the phone checks that the certificate
# served at https://hotspot.piso.local/api belongs to hotspot.piso.local.
:local hotspotCertName "piso-hotspot-cert"
:local captiveOptionName "piso-captive-portal"
:local captiveOptionSet "piso-captive-set"

:log info ("PisoPilot: setup started for SSID " . $ssid)
 
/system/identity set name="PisoPilot-GW"
 
:if ([:len [/interface/bridge find where name=$lanBridge]] = 0) do={
/interface/bridge add name=$lanBridge comment="PisoPilot customer LAN"
}
:if ([:len [/interface/bridge find where name=$guestBridge]] = 0) do={
/interface/bridge add name=$guestBridge comment="PisoPilot guest WiFi (hotspot)"
}

:local lanPorts {"ether2"; "ether3"; "ether4"}
:foreach port in=$lanPorts do={
:if ([:len [/interface/find where name=$port]] > 0) do={
:if ([:len [/interface/bridge/port find where interface=$port]] = 0) do={
/interface/bridge/port add bridge=$lanBridge interface=$port comment="PisoPilot LAN"
}
}
}
 
:local wifiIface "wifi1"
:if ([:len [/interface/find where name="wifi1"]] = 0) do={ :set wifiIface "wlan1" }
:if ([:len [/interface/find where name=$wifiIface]] > 0) do={
:if ([:len [/interface/bridge/port find where interface=$wifiIface]] = 0) do={
/interface/bridge/port add bridge=$guestBridge interface=$wifiIface comment="PisoPilot guest wireless"
} else={
# Re-import after the older script: the radio is still a port of the trusted
# bridge. Move it, or the phones stay on the side with no hotspot - and the
# wired ports stay inside the one that has it.
/interface/bridge/port set [find where interface=$wifiIface] bridge=$guestBridge
}
}
 
:if ([:len [/ip/address find where address=($gateway . "/24")]] = 0) do={
/ip/address add address=($gateway . "/24") interface=$lanBridge comment="PisoPilot gateway"
}
:if ([:len [/ip/address find where address=($guestGateway . "/24")]] = 0) do={
/ip/address add address=($guestGateway . "/24") interface=$guestBridge comment="PisoPilot guest gateway"
}
 
:local addressPool "piso-pool"
:if ([:len [/ip/pool find where name=$addressPool]] = 0) do={
/ip/pool add name=$addressPool ranges="192.168.88.10-192.168.88.200" comment="PisoPilot clients"
}
:local dhcpServer "piso-dhcp"
 
:foreach existing in=[/ip/dhcp-server find where interface=$lanBridge] do={
:if ([/ip/dhcp-server get $existing name] !=$dhcpServer) do={
/ip/dhcp-server set $existing disabled=yes comment="PisoPilot: replaced by piso-dhcp"
}
}
:if ([:len [/ip/dhcp-server find where name=$dhcpServer]] = 0) do={
/ip/dhcp-server add name=$dhcpServer interface=$lanBridge address-pool=$addressPool lease-time=2h disabled=no comment="PisoPilot DHCP"
}
/ip/dhcp-server set [find where name=$dhcpServer] disabled=no interface=$lanBridge address-pool=$addressPool
:if ([:len [/ip/dhcp-server/network find where address=$lanNetwork]] = 0) do={
/ip/dhcp-server/network add address=$lanNetwork gateway=$gateway dns-server=$gateway netmask=24 comment="PisoPilot LAN"
} else={
# the factory entry already matches this subnet - keep it, but make sure it
# carries our gateway and DNS before the hotspot starts answering clients
/ip/dhcp-server/network set [find where address=$lanNetwork] gateway=$gateway dns-server=$gateway
}

# The guest side gets its own pool, DHCP server and network, so the wired DHCP is
# never asked to hand out a hotspot range and vice versa.
:if ([:len [/ip/pool find where name=$guestPool]] = 0) do={
/ip/pool add name=$guestPool ranges="192.168.90.10-192.168.90.200" comment="PisoPilot guests"
}
:foreach existing in=[/ip/dhcp-server find where interface=$guestBridge] do={
:if ([/ip/dhcp-server get $existing name] !=$guestDhcpServer) do={
/ip/dhcp-server set $existing disabled=yes comment="PisoPilot: replaced by piso-guest-dhcp"
}
}
:if ([:len [/ip/dhcp-server find where name=$guestDhcpServer]] = 0) do={
/ip/dhcp-server add name=$guestDhcpServer interface=$guestBridge address-pool=$guestPool lease-time=2h disabled=no comment="PisoPilot guest DHCP"
}
/ip/dhcp-server set [find where name=$guestDhcpServer] disabled=no interface=$guestBridge address-pool=$guestPool
:if ([:len [/ip/dhcp-server/network find where address=$guestNetwork]] = 0) do={
/ip/dhcp-server/network add address=$guestNetwork gateway=$guestGateway dns-server=$guestGateway netmask=24 comment="PisoPilot guest LAN"
} else={
/ip/dhcp-server/network set [find where address=$guestNetwork] gateway=$guestGateway dns-server=$guestGateway
}
  
:if ([:len [/ip/dhcp-client find where interface="ether1"]] = 0) do={
/ip/dhcp-client add interface=ether1 disabled=no comment="PisoPilot upstream"
}
:if ([:len [/ip/firewall/nat find where chain="srcnat" and out-interface="ether1"]] = 0) do={
/ip/firewall/nat add chain=srcnat out-interface=ether1 action=masquerade comment="PisoPilot upstream NAT"
}
 
/ip/dns set allow-remote-requests=yes
:if ([:len [/ip/dns get servers]] = 0) do={
/ip/dns set servers=1.1.1.1,8.8.8.8
}
:if ([:len [/ip/dns/static find where name=$dnsName]] = 0) do={
/ip/dns/static add name=$dnsName address=$portalIp comment="PisoPilot portal"
}
# The hotspot's own name has to answer with the router. Pointed at the Pi, the
# login page URL RouterOS builds from it (and the RFC 7710 /api probe) lands on a
# host with nothing listening on port 80 - a browser error, not the portal.
:if ([:len [/ip/dns/static find where name=$hotspotDnsName]] = 0) do={
/ip/dns/static add name=$hotspotDnsName address=$guestGateway comment="PisoPilot hotspot login page"
}
 
:local wifiIface "wifi1"
:if ([:len [/interface/find where name="wifi1"]] = 0) do={ :set wifiIface "wlan1" }

:if ([:len [/interface/wifi find where name=$wifiIface]] = 0) do={
:log warning "PisoPilot: no wifi interface ($wifiIface) - set the SSID and password in WinBox -> WiFi"
} else={
 
:if ([:len $wifiPassword] = 0) do={
    /interface/wifi set [find where name=$wifiIface] disabled=no configuration.ssid=$ssid security.authentication-types=""
    :log info ("PisoPilot: SSID is now " . $ssid . " (open, no Wi-Fi password)")
} else={
    :if ([:len $wifiPassword] < 8) do={
        :log warning "PisoPilot: wifiPassword is shorter than 8 characters - WPA2 will reject it"
    }
    /interface/wifi set [find where name=$wifiIface] disabled=no configuration.ssid=$ssid security.authentication-types=wpa2-psk security.passphrase=$wifiPassword
    :log info ("PisoPilot: SSID is now " . $ssid . " (WPA2 secured)")
}
 
:if ([:len $wifiCountry] > 0) do={
    /interface/wifi set [find where name=$wifiIface] configuration.country=$wifiCountry
}

}



 
:do {
:local dmMode [/system/device-mode get mode]
:if ($dmMode != "ros") do={
/system/device-mode set hotspot=yes fetch=yes
:log info ("PisoPilot: device mode '" . $dmMode . "' -> hotspot and fetch enabled")
} else={
:log info "PisoPilot: device mode is 'ros', hotspot and fetch already allowed"
}
} on-error={
:log warning "PisoPilot: could not read /system/device-mode - if /ip/hotspot is greyed out in WinBox, run: /system/device-mode set hotspot=yes fetch=yes"
}
 
# The HTTPS certificate has to exist BEFORE the profile can point at it, so it
# is created here rather than in the RFC 8910 section further down. /certificate
# add already produces a self-signed certificate on RouterOS 7 - do NOT add
# "/certificate sign", there is no CA on this router and it answers
# "CA not found".
:do {
:if ([:len [/certificate find where name=$hotspotCertName]] = 0) do={
/certificate add name=$hotspotCertName common-name=$hotspotDnsName days-valid=3650 key-usage=key-cert-sign,crl-sign,tls-server key-size=2048
:log info ("PisoPilot: created the HotSpot certificate " . $hotspotCertName)
}
} on-error={
:log warning ("PisoPilot: could not create " . $hotspotCertName . " - HTTPS login and option 114 will not work until a certificate is assigned")
}

# login-by MUST include https.
#
# This is the single reason a phone reports "A2N Piso WiFi has no internet"
# instead of raising the sign-in notification. Modern Android, iOS and Windows
# probe their captive-portal URL over HTTPS, and a HotSpot only intercepts what
# login-by covers. Without https in this list the router never answers that
# probe, so the phone sees a network that resolves nothing and concludes it has
# no internet - while the blocking itself is working perfectly.
#
# The certificate is self-signed, so the customer gets a browser warning the
# first time. That is expected and unavoidable on a local-only name.
:local pisoLoginBy "mac,https,http-chap,cookie,mac-cookie"
:if ([:len [/ip/hotspot/profile find where name=$hotspotProfile]] = 0) do={
/ip/hotspot/profile add name=$hotspotProfile hotspot-address=$guestGateway dns-name=$hotspotDnsName html-directory=hotspot login-by=$pisoLoginBy mac-auth-mode=mac-as-username-and-password http-cookie-lifetime=1d use-radius=no
}
/ip/hotspot/profile set [find where name=$hotspotProfile] hotspot-address=$guestGateway dns-name=$hotspotDnsName login-by=$pisoLoginBy mac-auth-mode=mac-as-username-and-password http-cookie-lifetime=1d use-radius=no
:do {
/ip/hotspot/profile set [find where name=$hotspotProfile] ssl-certificate=$hotspotCertName
:log info ("PisoPilot: HotSpot serves https with " . $hotspotCertName)
} on-error={
:log warning ("PisoPilot: could not assign ssl-certificate - without it login-by=https cannot intercept the probe a phone uses to raise the sign-in notification. Test: /ip/hotspot/profile print detail")
}
 
:if ([:len [/ip/hotspot/user/profile find where name=$packageProfile]] = 0) do={
/ip/hotspot/user/profile add name=$packageProfile rate-limit="1M/2M" shared-users=1 keepalive-timeout=3m idle-timeout=none session-timeout=0s add-mac-cookie=yes mac-cookie-timeout=1d open-status-page=http-login
}
/ip/hotspot/user/profile set [find where name=$packageProfile] rate-limit="1M/2M"
:if ([:len [/ip/hotspot/user/profile find where name=$premiumProfile]] = 0) do={
/ip/hotspot/user/profile add name=$premiumProfile rate-limit="2M/5M" shared-users=1 keepalive-timeout=3m idle-timeout=none session-timeout=0s add-mac-cookie=yes mac-cookie-timeout=1d open-status-page=http-login
}
/ip/hotspot/user/profile set [find where name=$premiumProfile] rate-limit="2M/5M"
 
:if ([:len [/ip/hotspot find where name=$hotspotServer]] = 0) do={
/ip/hotspot add name=$hotspotServer interface=$guestBridge profile=$hotspotProfile address-pool=none addresses-per-mac=2 login-timeout=1m
}
# Do NOT add log=yes here. It looks like it should exist and does not: the
# /ip/hotspot property table in the MikroTik manual lists name, interface,
# address-pool, profile, idle-timeout, keepalive-timeout, login-timeout,
# addresses-per-mac and proxy-status - and no "log". An unknown property is a
# PARSE error, so it would stop the import dead at that column. check_rsc.py
# refused it for exactly this reason.
#
# A blank "/log print where topics~hotspot" is therefore the NORMAL result and
# proves nothing either way. Diagnose a client with /ip/hotspot/host print and
# by browsing from the phone, not from the log.
/ip/hotspot set [find where name=$hotspotServer] interface=$guestBridge profile=$hotspotProfile addresses-per-mac=2
/ip/hotspot enable [find where name=$hotspotServer]
 
# Only the Pi is walled-gardened. Deliberately NOT the hotspot's own dns-name:
# a walled-garden entry exempts that host from interception, so a blocked client
# would be sent to the router's own port 80 - which this script disables below -
# instead of being served the login page. Only outside hosts belong here.
:if ([:len [/ip/hotspot/walled-garden/ip find where dst-address=($portalIp . "/32") or dst-address=$portalIp]] = 0) do={
/ip/hotspot/walled-garden/ip add action=accept dst-address=($portalIp . "/32")
}
:if ([:len [/ip/hotspot/walled-garden find where comment="PisoPilot portal name"]] = 0) do={
/ip/hotspot/walled-garden add action=allow dst-host=$dnsName comment="PisoPilot portal name"
}
:if ([:len [/ip/hotspot/walled-garden find where comment="PisoPilot portal by IP"]] = 0) do={
/ip/hotspot/walled-garden add action=allow dst-host=$portalIp comment="PisoPilot portal by IP"
}
 
:if ([:len [/ip/hotspot/ip-binding find where address=$portalIp or address=($portalIp . "/32")]] = 0) do={
/ip/hotspot/ip-binding add type=bypassed address=$portalIp
}
# The controller must never be a paying customer. A Pi whose own Wi-Fi has also
# joined the SSID shows up on the guest bridge as well, and the address above
# (192.168.88.2) does not cover that face of it - so bypass it by MAC too.
:if ([:len [/ip/hotspot/ip-binding find where mac-address=$piMac]] = 0) do={
/ip/hotspot/ip-binding add type=bypassed mac-address=$piMac comment="PisoPilot controller"
}
 
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: established,related"]] = 0) do={
/ip/firewall/filter add chain=forward action=accept connection-state=established,related comment="PisoPilot: established,related"
}
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: walled garden to the portal"]] = 0) do={
/ip/firewall/filter add chain=forward in-interface=$guestBridge dst-address=$portalIp action=accept comment="PisoPilot: walled garden to the portal"
}
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: walled garden to the router DNS"]] = 0) do={
/ip/firewall/filter add chain=forward in-interface=$guestBridge dst-address=$guestGateway action=accept comment="PisoPilot: walled garden to the router DNS"
}
# Client isolation, backstop layer. The radio's own isolation (WinBox -> WiFi ->
# "Isolate" / "Client Isolation") stops phones reaching each other before IP, but
# the property that spells it moved between RouterOS releases and an unknown
# property is a PARSE error that stops the whole import before on-error can run.
# So the radio setting is left to WinBox and this layer is the one the script
# owns. It is scoped to the guest bridge and the guest subnet only, so it cannot
# touch the portal (192.168.88.2, on the LAN), the router DNS (accepted just
# above), or the wired LAN on "bridge".
# Placed ABOVE "established,related" on purpose: a client that already has a
# session open to another phone must still be cut off, not waved through by the
# established rule.
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: isolate clients from each other"]] = 0) do={
/ip/firewall/filter add chain=forward in-interface=$guestBridge dst-address=$guestNetwork action=drop comment="PisoPilot: isolate clients from each other" log=no
}
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: drop unpaid clients"]] = 0) do={
/ip/firewall/filter add chain=forward in-interface=$guestBridge hotspot=!auth action=drop comment="PisoPilot: drop unpaid clients" log=no
}
# Every gate rule is scoped to the guest bridge, so ether2-4 keep plain internet.
# Re-import safety: the guards above only ADD a missing rule, so a router that
# still carries the older in-interface=bridge copies would leave the wired LAN
# behind the login page. Push the interface back onto the guest bridge each run.
/ip/firewall/filter set [find where comment="PisoPilot: walled garden to the portal"] in-interface=$guestBridge dst-address=$portalIp
/ip/firewall/filter set [find where comment="PisoPilot: walled garden to the router DNS"] in-interface=$guestBridge dst-address=$guestGateway
/ip/firewall/filter set [find where comment="PisoPilot: drop unpaid clients"] in-interface=$guestBridge

# HotSpot rate limits use dynamic simple queues. FastTrack bypasses those
# queues, so accept only authenticated HotSpot established traffic before any
# FastTrack rule; other forwarded traffic keeps using FastTrack as before.
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: preserve HotSpot queues"]] = 0) do={
/ip/firewall/filter add chain=forward action=accept connection-state=established,related hotspot=auth comment="PisoPilot: preserve HotSpot queues"
}
/ip/firewall/filter set [find where comment="PisoPilot: preserve HotSpot queues"] chain=forward action=accept connection-state=established,related hotspot=auth
# Moving a rule that is already at the top, or one the router treats as built in,
# makes RouterOS throw "cannot move builtin" and that aborts the whole import
# half way through, leaving the rest of the setup unapplied. The move is an
# optimisation (it keeps HotSpot traffic out of FastTrack), so a failure here
# must not stop the script.
:do {
    :local queueRule [/ip/firewall/filter find where comment="PisoPilot: preserve HotSpot queues"]
    # Only move when it is not already the first rule: RouterOS rejects a no-op
    # move with "cannot move builtin" and that aborts the whole import.
    :if ([:len $queueRule] > 0) do={
        :local queuePosition [/ip/firewall/filter find where comment="PisoPilot: preserve HotSpot queues"]
        :local firstRule [/ip/firewall/filter find]
        :if ([:len $queuePosition] > 0 && [:len $firstRule] > 0 && [:pick $queuePosition 0] != [:pick $firstRule 0]) do={
            /ip/firewall/filter move [:pick $queuePosition 0] 0
        }
    }
} on-error={
    :log warning ("PisoPilot: could not move 'preserve HotSpot queues' to the top - check the order in /ip/firewall/filter print")
}
 
:local hsDynamicCount [:len [/ip/firewall/filter find where dynamic=yes]]
:log info ("PisoPilot: " . $hsDynamicCount . " dynamic HotSpot rule(s) in the filter table")
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: drop unpaid clients"]] > 0) do={
    # The whole reorder is best-effort. Every move can fail on its own (a rule
    # already in place, or one the router will not let us move), and a single
    # uncaught failure here would abort the import and leave the rest of the
    # setup unapplied. Ordering only matters for correctness of the gate, which
    # the individual rules already enforce, so a failure is logged and skipped.
    :do {
:local pisoOrder {"PisoPilot: isolate clients from each other"; "PisoPilot: established,related"; "PisoPilot: walled garden to the portal"; "PisoPilot: walled garden to the router DNS"; "PisoPilot: drop unpaid clients"}
:local pisoPosition $hsDynamicCount
:foreach pisoComment in=$pisoOrder do={
:local pisoRule [/ip/firewall/filter find where comment=$pisoComment]
:if ([:len $pisoRule] > 0) do={
:set pisoPosition ($pisoPosition + 1)
:do {
/ip/firewall/filter move [:pick $pisoRule 0] $pisoPosition
} on-error={
:log warning ("PisoPilot: could not re-order the firewall rule '" . $pisoComment . "' - check the order in /ip/firewall/filter print")
}
}
}
} on-error={
:log warning "PisoPilot: firewall re-order stopped early - check /ip/firewall/filter print"
}
}
 
:do {
:if ([:len $blockIpv6FromGuests] > 0) do={
# 1. do not hand the customers a global IPv6 address
:foreach guestAddress in=[/ipv6/address find where interface=$guestBridge] do={
:if ([/ipv6/address get $guestAddress advertise] = "yes") do={
/ipv6/address set $guestAddress advertise=no
:log info ("PisoPilot: stopped advertising IPv6 on " . $guestBridge)
}
}
# 2. Deliberately NOT touching /ipv6/nd/prefix.
# An earlier version of this line tried to stop a prefix being
# re-advertised, first with "disable-advertise=yes" and then with
# "advertise=no". Both stopped the import at this exact column with
# "expected end of command", so the property name could not be confirmed
# from the documentation. A step that is only belt-and-braces is not
# worth a parse error that kills the whole import, so it is gone.
# It is also redundant: with advertise=no on the bridge address the
# router sends no router advertisements on that interface at all, so
# there is no prefix left to re-advertise. If you ever do need it, read
# the real property names off your own router first:
#     /ipv6/nd/prefix print detail
# 3. last resort: forward nothing over IPv6 out of the customer LAN
:if ([:len [/ipv6/firewall/filter find where comment="PisoPilot: no IPv6 for customers"]] = 0) do={
/ipv6/firewall/filter add chain=forward in-interface=$guestBridge action=drop comment="PisoPilot: no IPv6 for customers"
}
# Same re-import safety as the IPv4 gate: only the guests lose IPv6, never the
# wired LAN, so an older in-interface=bridge copy has to be pulled back too.
/ipv6/firewall/filter set [find where comment="PisoPilot: no IPv6 for customers"] in-interface=$guestBridge
:log info "PisoPilot: customers are IPv4-only, so the HotSpot can see them"
} else={
:log info "PisoPilot: blockIpv6FromGuests is empty - IPv6 left untouched"
}
} on-error={
:log warning "PisoPilot: IPv6 section skipped - if phones browse without the portal, run: /ipv6/firewall/filter add chain=forward in-interface=bridge-guest action=drop"
}
 
:if ([:len $piMac] > 0) do={
:if ([:len [/ip/dhcp-server/lease find where address=$portalIp]] = 0) do={
/ip/dhcp-server/lease add address=$portalIp mac-address=$piMac server=$dhcpServer
} else={
/ip/dhcp-server/lease set [find where address=$portalIp] mac-address=$piMac
}
} else={
:log warning "PisoPilot: piMac is empty - set a static address on the Pi itself, or fill piMac and re-import"
}
 
:if ([:len [/user/group find where name="piso-api"]] = 0) do={
/user/group add name="piso-api" policy=read,write,api,rest-api,test
}
:if ([:len [/user find where name=$apiUser]] = 0) do={
/user add name=$apiUser group="piso-api" password=$apiPassword
} else={
/user set [find where name=$apiUser] group="piso-api" password=$apiPassword
}
 
:do {
:if ([:len [/certificate find where name=$certName]] = 0) do={
/certificate add name=$certName common-name=$gateway days-valid=3650 key-usage=key-cert-sign,crl-sign,tls-server
/certificate sign $certName
}
/ip/service set www-ssl certificate=$certName disabled=no
} on-error={
:log warning "PisoPilot: HTTPS section failed - check /certificate print, then run: /ip/service set www-ssl certificate=piso-cert disabled=no"
}
 
# ---------------------------------------------------------------------------
# RFC 8910 / DHCP option 114: what makes a phone open the portal by itself.
#
# Every command below was verified against a real hAP ax lite running 7.24.4,
# where the first four attempts were all rejected. What the terminal taught:
#
#   /ip/dhcp-server/option add ... value="https://..."
#       failure: Unknown data type!
#     A bare string is not a data type. RouterOS wants a quoted string INSIDE
#     the double quotes: value="'https://...'"  (single quotes, then double).
#     Without them the option is refused and the whole section aborts.
#
#   /certificate sign <name>
#       failure: CA not found
#     /certificate add already produces a self-signed certificate on RouterOS 7.
#     sign is only for issuing one from a CA, and there is no CA here.
#
#   /ip/dhcp-server/option/sets add ... option=<name>
#       failure: action cancelled
#     The property is PLURAL: options=, not option=.
#
#   /ip/dhcp-server set ... option-set=<name>
#       bad parameter option-set
#     The property is prefixed: dhcp-option-set=, not option-set=.
#
# force=yes sends the option even to a client that never asked for it in its
# parameter request list, which is what a phone needs in order to receive it.
#
# Each step gets its own on-error so one refusal no longer discards the rest -
# that is how a single bad line hid four problems behind one warning.
#
# https-redirect is deliberately NOT set: it would send the phone to the
# RouterOS HTTPS login form instead of our own login.html, and that page is the
# one which hands the customer to the portal.
# ---------------------------------------------------------------------------
:do {
:local enableRfc8910 "yes"
:if ($enableRfc8910 = "yes") do={
# 1. the certificate. Created much earlier, above the profile, because the
# profile has to point at it. Nothing to do here.
/certificate print where name=$hotspotCertName
# 2. the option. The inner single quotes are what make it a string.
:local captiveUrl ("https://" . $hotspotDnsName . "/api")
:local captiveValue ("'" . $captiveUrl . "'")
:if ([:len [/ip/dhcp-server/option find where code=114]] = 0) do={
:do {
/ip/dhcp-server/option add name=$captiveOptionName code=114 value=$captiveValue force=yes
:log info ("PisoPilot: DHCP option 114 advertises " . $captiveUrl)
} on-error={
:log warning ("PisoPilot: /ip/dhcp-server/option add refused code 114 - type this by hand: /ip/dhcp-server/option add name=piso-captive-portal code=114 value=\"'" . $captiveUrl . "'\" force=yes")
}
} else={
:do {
/ip/dhcp-server/option set [find where code=114] name=$captiveOptionName value=$captiveValue force=yes
:log info ("PisoPilot: DHCP option 114 updated to " . $captiveUrl)
} on-error={
:log warning "PisoPilot: could not update the existing option 114"
}
}
# 3. the HotSpot serves it over https.
:do {
/ip/hotspot/profile set [find where name=$hotspotProfile] ssl-certificate=$hotspotCertName
:log info ("PisoPilot: HotSpot profile serves https using " . $hotspotCertName)
} on-error={
:log warning ("PisoPilot: this firmware refused ssl-certificate on the HotSpot profile - the phone will fall back to its own http:// probe. Test it with: /ip/hotspot/profile set " . $hotspotProfile . " ssl-certificate=" . $hotspotCertName)
}
# 4. the set. Note options=, plural.
:if ([:len [/ip/dhcp-server/option/sets find where name=$captiveOptionSet]] = 0) do={
:do {
/ip/dhcp-server/option/sets add name=$captiveOptionSet
} on-error={
:log warning ("PisoPilot: could not create the option set " . $captiveOptionSet)
}
}
:do {
/ip/dhcp-server/option/sets set [find where name=$captiveOptionSet] options=$captiveOptionName
} on-error={
:log warning ("PisoPilot: could not put " . $captiveOptionName . " into " . $captiveOptionSet)
}
# 5. attach it to the GUEST server only. Note the dhcp- prefix.
:do {
/ip/dhcp-server set [find where name=$guestDhcpServer] dhcp-option-set=$captiveOptionSet
:log info ("PisoPilot: " . $guestDhcpServer . " now sends " . $captiveOptionSet . " (option 114)")
} on-error={
:log warning ("PisoPilot: could not attach the option set to " . $guestDhcpServer . " - this is the step that actually makes option 114 leave the router. By hand: /ip/dhcp-server set [find name=" . $guestDhcpServer . "] dhcp-option-set=" . $captiveOptionSet)
}
} else={
:log info "PisoPilot: enableRfc8910 is not 'yes' - option 114 left alone"
}
}
/ip/service set www disabled=yes
/ip/service set telnet disabled=yes
/ip/service set ftp disabled=yes
 
# Declared OUTSIDE the :do so the on-error handler can quote it in its message:
# a :local declared inside the block is not in scope there.
:local scratchLogin "hotspot/piso-login.tmp"
:do {
# hotspot/login.html is a RESERVED name: the HotSpot owns it and RouterOS
# refuses to let /tool fetch overwrite it directly ("could not fetch
# login.html"), even though the very next fetch to hotspot/api.json in the
# same run succeeds. So download to a scratch name the router does allow,
# then rename it into place. The rename is what actually installs it.
/tool fetch url=("http://" . $portalIp . ":" . $portalPort . "/hotspot/login.html") dst-path=$scratchLogin
:local scratchFile [/file find where name=$scratchLogin]
:if ([:len $scratchFile] = 0) do={
:log warning ("PisoPilot: could not download login.html to " . $scratchLogin . " - is app.py running and is " . $portalPort . " reachable from the router?")
} else={
:local scratchSize [/file get [:pick $scratchFile 0] size]
:if ($scratchSize < 500) do={
:log warning ("PisoPilot: the login page only came down as " . $scratchSize . " bytes - it did not really download. Start app.py and re-run.")
} else={
/file remove [find where name="hotspot/login.html"]
# RouterOS has no "move" sub-command under /file: the menu is add, copy, edit,
# enable, get, import, list, load, make-directory, print, remove, rename, set,
# sign, unpack and upload. Typing it answers "bad command name". A file is moved
# by setting its name to the new path, which is what the next line does.
:local scratchId [:pick $scratchFile 0]
/file set $scratchId name="hotspot/login.html"
:local loginPage [/file find where name="hotspot/login.html"]
:if ([:len $loginPage] > 0) do={
:log info ("PisoPilot: portal redirect page installed (" . $scratchSize . " bytes)")
} else={
:log warning "PisoPilot: the login page downloaded but could not be renamed into hotspot/login.html - check /file print where name~hotspot"
}
}
}
} on-error={
:log warning ("PisoPilot: could not fetch login.html - start app.py, then re-run. RouterOS refuses to write hotspot/login.html directly, so the script downloads it to " . $scratchLogin . " and renames it; by hand: /tool fetch url=http://" . $portalIp . ":" . $portalPort . "/hotspot/login.html dst-path=hotspot/piso-login.tmp  then  /file set [find where name=hotspot/piso-login.tmp] name=hotspot/login.html")
}

# api.json answers the option 114 probe. Missing it does not break the phone -
# the option points at a URL that would 404 - but the client then has nothing to
# open, so it falls back to the plain http:// probe.
:do {
/tool fetch url=("http://" . $portalIp . ":" . $portalPort . "/hotspot/api.json") dst-path="hotspot/api.json"
:local apiFile [/file find where name="hotspot/api.json"]
:if ([:len $apiFile] = 0) do={
:log warning ("PisoPilot: hotspot/api.json is missing - start app.py, then re-run: /tool fetch url=http://" . $portalIp . ":" . $portalPort . "/hotspot/api.json dst-path=hotspot/api.json")
} else={
:local apiFileId [:pick $apiFile 0]
:local apiSize [/file get $apiFileId size]
:if ($apiSize > 10) do={
:log info ("PisoPilot: option 114 descriptor installed (" . $apiSize . " bytes)")
} else={
:log warning ("PisoPilot: hotspot/api.json is only " . $apiSize . " bytes - it did not download")
}
}
} on-error={
:log warning "PisoPilot: could not fetch api.json - start app.py, then re-run the /tool fetch for hotspot/api.json"
}
 
:log info "PisoPilot: setup finished"
:put "PisoPilot setup finished. Verify with:"
:put "  /ip/hotspot print               (piso-hotspot on bridge-guest only)"
:put "  /interface/bridge/port print    (wifi on bridge-guest, ether2-4 on bridge)"
:put "  /ip/hotspot/profile print       (login-by includes mac, dns-name is the"
:put "                                   hotspot name, NOT portal.piso.local)"
:put "  /ip/hotspot/user print          (one user per paying MAC)"
:put "  /ip/dhcp-server print           (piso-dhcp on bridge, piso-guest-dhcp on"
:put "                                   bridge-guest, factory servers disabled)"
:put "  /ip/dns/static print            (portal.piso.local -> $portalIp)"
:put "  /user print                     (api user: $apiUser)"
:put ""
:put "The portal is only a gate if these hold - check them if a phone still"
:put "browses the internet without paying:"
:put "  /ip/firewall/filter print       (PisoPilot rules present, ALL scoped to"
:put "                                   bridge-guest, plus the DYNAMIC hs-unauth"
:put "                                   jumps the HotSpot adds itself)"
:put "  /ipv6/address print            (no global IPv6 advertised on bridge-guest)"
:put "  /system/device-mode print      (hotspot=yes, fetch=yes)"
:put "  /file print where name=hotspot/login.html   (must be a real page, not empty)"
:put ""
:put "If the wired LAN lost its internet, the HotSpot is bound to the wrong"
:put "interface - check /ip/hotspot print and /interface/bridge/port print."
:put ""
:put "If a phone is blocked but never sees the portal:"
:put "  1. open a plain http:// page, not https:// - a HotSpot intercepts port 80"
:put "     only, so an https-only browser just gets a connection reset"
:put "  2. /ip/hotspot/walled-garden print   must list the Pi and nothing else."
:put "     A walled-garden entry for the login page name lets the phone walk past"
:put "     the router to a host with no port 80 open"
:put "  3. /ip/hotspot/host print            the phone must be listed, with the"
:put "     192.168.90.x address the guest DHCP handed out"
:put ""
:put "If the import stops with 'expected end of command', the column points at a"
:put "property the menu does not accept. Test the version-sensitive lines FIRST,"
:put "one at a time in the terminal, so a bad guess cannot block the whole import:"
:put "  /interface/wifi print detail where name=wifi1   (RouterOS 7.13+ only)"
:put "  /ipv6/address set [find where interface=bridge-guest] advertise=no"
:put "  /system/device-mode set hotspot=yes fetch=yes"
:put "  /ipv6/firewall/filter add chain=forward in-interface=bridge-guest action=drop"
:put "A line that is accepted in the terminal is also accepted by /import."
:put ""
:put "Option 114 (a phone that opens the portal by itself) needs ALL of these."
:put "Four of them are traps - each was refused by a 7.24.4 router:"
:put "  1. value=\"'https://.../api'\"   single quotes INSIDE the double quotes,"
:put "     a bare string gives 'Unknown data type!'"
:put "  2. no '/certificate sign'          no CA here - /certificate add is enough"
:put "  3. option/sets set ... options=    plural, 'option=' is cancelled"
:put "  4. dhcp-server set ... dhcp-option-set=   NOT 'option-set='"
:put "Verify with:"
:put "  /ip/dhcp-server/option print       (code 114, raw-value shown)"
:put "  /ip/dhcp-server/option/sets print  (piso-captive-set holds options)"
:put "  /ip/dhcp-server print              (piso-guest-dhcp has dhcp-option-set)"
:put "  /log print where message~\"PisoPilot\""
:put ""
:put "Quickest end-to-end check, as an unpaid phone:"
:put "  1. join the SSID and open any http:// page  -> the PisoPilot portal"
:put "  2. no internet until a coin pays            -> /ip/hotspot/active stays empty"
:put "  3. after paying                             -> /ip/hotspot/active shows the MAC"