# ===========================================================================
#  PisoPilot / Piso WiFi  -  MikroTik hAP ax lite (L41G-2axD) on RouterOS 7
#  File: router/hap-ax-lite-piso.rsc      Import: /import hap-ax-lite-piso.rsc
# ---------------------------------------------------------------------------
#  What this script builds
#    * the LAN side the Raspberry Pi controller and the phones share
#      (bridge + 192.168.88.1/24 + DHCP + masquerade on ether1)
#    * the wireless SSID the customers join
#    * a HotSpot server whose login page is replaced by the PisoPilot portal
#    * MAC login: the controller creates one HotSpot user per paying device,
#      named after the device MAC address, so a phone that paid is let online
#      without typing anything
#    * a walled garden that lets unpaid clients reach only the controller
#    * a static DHCP lease + HotSpot bypass for the controller itself
#    * a restricted REST API user for app.py and www-ssl for HTTPS
#
#  Safety
#    * re-runnable: every block is "add if missing, then set"
#    * it never deletes a firewall filter, a NAT rule or a user you added
#    * read the file once before importing it on a machine that is already live
# ===========================================================================

# --------------------------------------------------------------- 1. SETTINGS
# Change these values, then import the file. The SSID must match
# Settings -> SSID inside the PisoPilot console so the portal and the radio agree.
# Keep every comment on its own line: RouterOS ends a command at the newline, so a
# "# ..." that trails a statement makes the import stop with "expected end of command".
:local ssid "A2N Piso WiFi"
# "" = open network (the usual piso wifi setup)
:local wifiPassword ""
# "" = keep the router's regulatory country, or a name like "latvia"
:local wifiCountry ""
# the Raspberry Pi controller
:local portalIp "192.168.88.2"
# app.py listens here
:local portalPort "5000"
# friendly name for the portal (optional)
:local dnsName "portal.piso.local"
# optional: Pi eth0 MAC for a static lease
:local piMac ""
:local lanBridge "bridge"
:local gateway "192.168.88.1"
:local lanNetwork "192.168.88.0/24"
:local hotspotServer "piso-hotspot"
:local hotspotProfile "piso-profile"
# paid tier, 1M upload / 2M download
:local packageProfile "piso-package"
# faster tier, 2M upload / 5M download
:local premiumProfile "piso-premium"
:local apiUser "piso-controller"
# CHANGE THIS, match PISO_ROUTER_PASSWORD
:local apiPassword "9g3zu9g9LQet8HdU1viZ"
:local certName "piso-cert"

:log info ("PisoPilot: setup started for SSID " . $ssid)

# -------------------------------------------------------------- 2. IDENTITY
/system/identity set name="PisoPilot-GW"

# ---------------------------------------------------- 3. LAN / BRIDGE / DHCP
# ether1 is the upstream cable (WAN), ether2-ether4 plus the radio are the
# customer LAN. The hAP ax lite ships with this layout, so most of it is a
# no-op that only makes the script safe to re-run.
:if ([:len [/interface/bridge find where name=$lanBridge]] = 0) do={
    /interface/bridge add name=$lanBridge comment="PisoPilot customer LAN"
}

:local lanPorts {"ether2"; "ether3"; "ether4"}
:foreach port in=$lanPorts do={
    :if ([:len [/interface/find where name=$port]] > 0) do={
        :if ([:len [/interface/bridge/port find where interface=$port]] = 0) do={
            /interface/bridge/port add bridge=$lanBridge interface=$port comment="PisoPilot LAN"
        }
    }
}

# The radio joins the bridge too, so phones and the controller share one subnet.
# "wifi1" is the name on the hAP ax lite (wifi-qcom package); "wlan1" is the older
# wireless package's name, kept so the file is portable.
:local wifiIface "wifi1"
:if ([:len [/interface/find where name="wifi1"]] = 0) do={ :set wifiIface "wlan1" }
:if ([:len [/interface/find where name=$wifiIface]] > 0) do={
    :if ([:len [/interface/bridge/port find where interface=$wifiIface]] = 0) do={
        /interface/bridge/port add bridge=$lanBridge interface=$wifiIface comment="PisoPilot wireless"
    }
}

# Gateway address on the LAN.
:if ([:len [/ip/address find where address=($gateway . "/24")]] = 0) do={
    /ip/address add address=($gateway . "/24") interface=$lanBridge comment="PisoPilot gateway"
}

# DHCP for customers and the controller.
:local addressPool "piso-pool"
:if ([:len [/ip/pool find where name=$addressPool]] = 0) do={
    /ip/pool add name=$addressPool ranges="192.168.88.10-192.168.88.200" comment="PisoPilot clients"
}
:local dhcpServer "piso-dhcp"
# RouterOS allows only one directly-connected DHCP server per interface, and the
# factory config already ships one on the bridge - park it before adding ours.
# It is only disabled (never deleted), so it can be restored if you revert.
:foreach existing in=[/ip/dhcp-server find where interface=$lanBridge] do={
    :if ([/ip/dhcp-server get $existing name] != $dhcpServer) do={
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

# Upstream: DHCP client on ether1 and masquerade out of it.
:if ([:len [/ip/dhcp-client find where interface="ether1"]] = 0) do={
    /ip/dhcp-client add interface=ether1 disabled=no comment="PisoPilot upstream"
}
:if ([:len [/ip/firewall/nat find where chain="srcnat" and out-interface="ether1"]] = 0) do={
    /ip/firewall/nat add chain=srcnat out-interface=ether1 action=masquerade comment="PisoPilot upstream NAT"
}

# Router DNS: allow-remote-requests is what lets HotSpot clients resolve names.
/ip/dns set allow-remote-requests=yes
:if ([:len [/ip/dns get servers]] = 0) do={
    /ip/dns set servers=1.1.1.1,8.8.8.8
}
:if ([:len [/ip/dns/static find where name=$dnsName]] = 0) do={
    /ip/dns/static add name=$dnsName address=$portalIp comment="PisoPilot portal"
}

# ------------------------------------------------------------- 4. WIRELESS
# hAP ax lite runs the wifi-qcom package, where the single radio is "wifi1".
# RouterOS 7.13 moved the SSID and the radio security into the interface's
# configuration/security sub-objects, so they are set as configuration.ssid and
# security.authentication-types / security.passphrase. A bare ssid= parameter is
# rejected at parse time and stops the whole import with "expected end of command".
:local wifiIface "wifi1"
:if ([:len [/interface/find where name="wifi1"]] = 0) do={ :set wifiIface "wlan1" }

:if ([:len [/interface/wifi find where name=$wifiIface]] = 0) do={
    :log warning "PisoPilot: no wifi interface ($wifiIface) - set the SSID and password in WinBox -> WiFi"
} else={
# An empty wifiPassword keeps the usual piso wifi setup: an open network where
# the HotSpot login is the gate, not a Wi-Fi password. Clearing
# authentication-types also drops the factory password printed on the sticker.
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
# Optional: a regulatory country keeps the AP active where the factory setup never
# asked for one. Leave wifiCountry empty to keep whatever the router already has.
    :if ([:len $wifiCountry] > 0) do={
        /interface/wifi set [find where name=$wifiIface] configuration.country=$wifiCountry
    }
}

# --------------------------------------------------- 5. HOTSPOT SERVER PROFILE
# login-by=mac        -> a device whose MAC has a HotSpot user is logged in automatically
# login-by=http-chap  -> fallback: type the MAC as both user name and password on the router page
# cookie / mac-cookie -> a device that paid keeps its session through a disconnect
:if ([:len [/ip/hotspot/profile find where name=$hotspotProfile]] = 0) do={
    /ip/hotspot/profile add name=$hotspotProfile hotspot-address=$gateway dns-name=$dnsName html-directory=hotspot login-by=mac,http-chap,cookie,mac-cookie http-cookie-lifetime=1d split-user-domain=no use-radius=no
}
/ip/hotspot/profile set [find where name=$hotspotProfile] hotspot-address=$gateway dns-name=$dnsName login-by=mac,http-chap,cookie,mac-cookie http-cookie-lifetime=1d split-user-domain=no use-radius=no

# ------------------------------------------------------------ 6. HOTSPOT USERS
# What each paid client gets: one device, a speed cap, and the paid minutes
# applied per user by app.py (limit-uptime). shared-users=1 stops one code
# from serving a whole household.
# NOTE: rate-limit is "upload/download" as the client sees it - RouterOS docs:
#       "to set 1M download and 512k upload for the client, use 512k/1M".
:if ([:len [/ip/hotspot/user/profile find where name=$packageProfile]] = 0) do={
    /ip/hotspot/user/profile add name=$packageProfile rate-limit="1M/2M" shared-users=1 keepalive-timeout=3m idle-timeout=none session-timeout=0s add-mac-cookie=yes mac-cookie-timeout=1d open-status-page=http-login
}
:if ([:len [/ip/hotspot/user/profile find where name=$premiumProfile]] = 0) do={
    /ip/hotspot/user/profile add name=$premiumProfile rate-limit="2M/5M" shared-users=1 keepalive-timeout=3m idle-timeout=none session-timeout=0s add-mac-cookie=yes mac-cookie-timeout=1d open-status-page=http-login
}

# ----------------------------------------------------------- 7. HOTSPOT SERVER
# One server on the LAN bridge; address-pool=none keeps the DHCP addresses the
# router already handed out. addresses-per-mac=2 tolerates a phone that
# re-connects and changes address while it still has paid time.
:if ([:len [/ip/hotspot find where name=$hotspotServer]] = 0) do={
    /ip/hotspot add name=$hotspotServer interface=$lanBridge profile=$hotspotProfile address-pool=none addresses-per-mac=2 login-timeout=1m
}
/ip/hotspot set [find where name=$hotspotServer] interface=$lanBridge profile=$hotspotProfile addresses-per-mac=2

# ------------------------------------------------ 8. WALLED GARDEN + BYPASS
# Unpaid clients may reach the controller (so the portal and the coin slot
# screen load) and nothing else. Everything else is cut off until they pay.
# Re-import safety: the two address rules are matched on their destination and
# on a comment only where that menu accepts one. RouterOS rejects comment= on
# /ip/hotspot/profile (and the other HotSpot object menus), so those entries are
# left unlabelled and are identified by name or address instead.
:if ([:len [/ip/hotspot/walled-garden/ip find where dst-address=($portalIp . "/32") or dst-address=$portalIp]] = 0) do={
    /ip/hotspot/walled-garden/ip add action=accept dst-address=($portalIp . "/32")
}
:if ([:len [/ip/hotspot/walled-garden find where comment="PisoPilot portal name"]] = 0) do={
    /ip/hotspot/walled-garden add action=allow dst-host=$dnsName comment="PisoPilot portal name"
}
:if ([:len [/ip/hotspot/walled-garden find where comment="PisoPilot portal by IP"]] = 0) do={
    /ip/hotspot/walled-garden add action=allow dst-host=$portalIp comment="PisoPilot portal by IP"
}
# The controller itself is not a paying customer: bypass it so the console,
# the REST API and the coin listener are never held behind the login page.
:if ([:len [/ip/hotspot/ip-binding find where address=$portalIp or address=($portalIp . "/32")]] = 0) do={
    /ip/hotspot/ip-binding add type=bypassed address=$portalIp
}

# ------------------------------------------------------ 9. STATIC PI LEASE
# Optional but recommended: the portal URL, the walled garden and the app's
# PISO_ROUTER_* settings all point at one fixed address.
:if ([:len $piMac] > 0) do={
    :if ([:len [/ip/dhcp-server/lease find where address=$portalIp]] = 0) do={
        /ip/dhcp-server/lease add address=$portalIp mac-address=$piMac server=$dhcpServer
    } else={
        /ip/dhcp-server/lease set [find where address=$portalIp] mac-address=$piMac
    }
} else={
    :log warning "PisoPilot: piMac is empty - set a static address on the Pi itself, or fill piMac and re-import"
}

# ------------------------------------------------------- 10. REST API ACCESS
# app.py talks to /rest over HTTPS with this user. read + write cover the
# HotSpot user and active tables; rest-api is the policy that unlocks REST.
:if ([:len [/user/group find where name="piso-api"]] = 0) do={
    /user/group add name="piso-api" policy=read,write,api,rest-api,test
}
:if ([:len [/user find where name=$apiUser]] = 0) do={
    /user add name=$apiUser group="piso-api" password=$apiPassword
} else={
    /user set [find where name=$apiUser] group="piso-api" password=$apiPassword
}

# ------------------------------------------------------------ 11. HTTPS / REST
# A self-signed certificate is enough: .env.example ships PISO_ROUTER_VERIFY_SSL=0.
# Import the CA into the Pi later if you prefer real verification.
# key-cert-sign + crl-sign let the certificate sign itself (RouterOS refuses to
# self-sign a template without them); tls-server lets www-ssl serve it.
:if ([:len [/certificate find where name=$certName]] = 0) do={
    /certificate add name=$certName common-name=$gateway days-valid=3650 key-usage=key-cert-sign,crl-sign,tls-server
    /certificate sign $certName
}
/ip/service set www-ssl certificate=$certName disabled=no
# Plain HTTP WebFig/API stays off; WinBox (8291) and SSH (22) stay available.
/ip/service set www disabled=yes
/ip/service set telnet disabled=yes
/ip/service set ftp disabled=yes

# --------------------------------------------------- 12. PORTAL REDIRECT PAGE
# Pull the redirect page from app.py so an unauthenticated phone lands on the
# PisoPilot portal instead of the RouterOS login form. Needs device-mode
# fetch=yes and a running controller; failure here is not fatal.
:do {
    /tool fetch url=("http://" . $portalIp . ":" . $portalPort . "/hotspot/login.html") dst-path="hotspot/login.html" mode=http
    :log info "PisoPilot: portal redirect page installed"
} on-error={
    :log warning "PisoPilot: could not fetch login.html - run app.py, then repeat this one command"
}

# ------------------------------------------------------------- 13. SUMMARY
:log info "PisoPilot: setup finished"
:put "PisoPilot setup finished. Verify with:"
:put "  /ip/hotspot print               (should show piso-hotspot on the bridge)"
:put "  /ip/hotspot/profile print       (login-by should include mac)"
:put "  /ip/hotspot/user print          (one user per paying MAC)"
:put "  /ip/dhcp-server print           (piso-dhcp active, factory server disabled)"
:put "  /ip/dns/static print            (portal.piso.local -> $portalIp)"
:put "  /user print                     (api user: $apiUser)"
