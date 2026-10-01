 
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
 
:if ([:len [/ip/hotspot/profile find where name=$hotspotProfile]] = 0) do={
/ip/hotspot/profile add name=$hotspotProfile hotspot-address=$guestGateway dns-name=$hotspotDnsName html-directory=hotspot login-by=mac,http-chap,cookie,mac-cookie http-cookie-lifetime=1d use-radius=no
}
/ip/hotspot/profile set [find where name=$hotspotProfile] hotspot-address=$guestGateway dns-name=$hotspotDnsName login-by=mac,http-chap,cookie,mac-cookie http-cookie-lifetime=1d use-radius=no
 
:if ([:len [/ip/hotspot/user/profile find where name=$packageProfile]] = 0) do={
/ip/hotspot/user/profile add name=$packageProfile rate-limit="1M/2M" shared-users=1 keepalive-timeout=3m idle-timeout=none session-timeout=0s add-mac-cookie=yes mac-cookie-timeout=1d open-status-page=http-login
}
:if ([:len [/ip/hotspot/user/profile find where name=$premiumProfile]] = 0) do={
/ip/hotspot/user/profile add name=$premiumProfile rate-limit="2M/5M" shared-users=1 keepalive-timeout=3m idle-timeout=none session-timeout=0s add-mac-cookie=yes mac-cookie-timeout=1d open-status-page=http-login
}
 
:if ([:len [/ip/hotspot find where name=$hotspotServer]] = 0) do={
/ip/hotspot add name=$hotspotServer interface=$guestBridge profile=$hotspotProfile address-pool=none addresses-per-mac=2 login-timeout=1m
}
/ip/hotspot set [find where name=$hotspotServer] interface=$guestBridge profile=$hotspotProfile addresses-per-mac=2
 
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
 
:local hsDynamicCount [:len [/ip/firewall/filter find where dynamic=yes]]
:log info ("PisoPilot: " . $hsDynamicCount . " dynamic HotSpot rule(s) in the filter table")
:if ([:len [/ip/firewall/filter find where comment="PisoPilot: drop unpaid clients"]] > 0) do={
:local pisoOrder {"PisoPilot: established,related"; "PisoPilot: walled garden to the portal"; "PisoPilot: walled garden to the router DNS"; "PisoPilot: drop unpaid clients"}
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
 
/ip/service set www disabled=yes
/ip/service set telnet disabled=yes
/ip/service set ftp disabled=yes
 
:do {
/tool fetch url=("http://" . $portalIp . ":" . $portalPort . "/hotspot/login.html") dst-path="hotspot/login.html"
:local loginPage [/file find where name="hotspot/login.html"]
:if ([:len $loginPage] = 0) do={
:log warning "PisoPilot: hotspot/login.html is missing after the fetch - is app.py running and is $portalPort reachable from the router?"
} else={
:local loginPageId [:pick $loginPage 0]
:local loginPageSize [/file get $loginPageId size]
:local loginPageStatus [/file get $loginPageId status]
:if ($loginPageSize > 500) do={
:log info ("PisoPilot: portal redirect page installed (" . $loginPageSize . " bytes)")
} else={
:log warning ("PisoPilot: hotspot/login.html is only " . $loginPageSize . " bytes, status '" . $loginPageStatus . "' - it did not download. Start app.py, then re-run: /tool fetch url=http://" . $portalIp . ":" . $portalPort . "/hotspot/login.html dst-path=hotspot/login.html")
}
}
} on-error={
:log warning ("PisoPilot: could not fetch login.html - start app.py, then re-run: /tool fetch url=http://" . $portalIp . ":" . $portalPort . "/hotspot/login.html dst-path=hotspot/login.html")
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
:put "  /ip/hotspot/profile set piso-profile https-redirect=yes"
:put "  /ip/hotspot/profile set piso-profile ssl-certificate=piso-cert"
:put "A line that is accepted in the terminal is also accepted by /import."
:put ""
:put "Those last two stay out of the body of this script on purpose: they are"
:put "version-sensitive, and an unknown property name is a parse error that kills"
:put "the whole import. ssl-certificate (with a trusted certificate) is what lets"
:put "RouterOS send the RFC 7710 DHCP option that makes a modern phone pop the"
:put "portal open by itself. Neither is needed for the http:// redirect to work."
:put ""
:put "Quickest end-to-end check, as an unpaid phone:"
:put "  1. join the SSID and open any http:// page  -> the PisoPilot portal"
:put "  2. no internet until a coin pays            -> /ip/hotspot/active stays empty"
:put "  3. after paying                             -> /ip/hotspot/active shows the MAC"