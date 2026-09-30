# PisoPilot hardware setup — hAP ax lite + Raspberry Pi 3B + Allan 1239A

This folder finishes the physical side of PisoPilot: the MikroTik hotspot that
sells the internet, the Raspberry Pi that runs the console and the coin logic,
and the Allan coin acceptor that takes the money.

| File | Purpose |
| --- | --- |
| `hap-ax-lite-piso.rsc` | RouterOS 7 script: LAN, SSID, HotSpot, walled garden, MAC login, REST API user, HTTPS |
| `verify_router.py` | Runs on the Pi: proves the REST connection and every HotSpot call `app.py` makes |
| `../tools/coin_pulse_probe.py` | Runs on the Pi: measures what the coin slot really sends per denomination |

## 1. Topology

```
        ISP cable
            |
        [ether1]
   MikroTik hAP ax lite  (192.168.88.1, HotSpot gateway, DHCP, NAT)
        |        \
     [ether2]     \  wifi "A2N Piso WiFi"
        |          \
  [Raspberry Pi 3B]  phones / laptops  --- join the SSID, get the captive portal
   192.168.88.2
   app.py :5000  (console + /portal)
        |
   GPIO17  <-- pulse line (optocoupler) <-- Allan 1239A coin slot  <-- 12 V PSU
```

Who does what:

* **The router sells nothing by itself.** It blocks every unpaid device and shows
  a login page; the page is replaced by the PisoPilot portal on the Pi.
* **The Pi decides who paid.** Coins land on GPIO17, the console settles them,
  and the Pi then creates one HotSpot user on the router named after the paying
  device's MAC address. RouterOS logs that device in automatically
  (`login-by=mac`), so the phone never types a username.
* **The Pi also banks the time.** When the paid minutes run out, a background
  sweep removes the HotSpot user and drops the device, so nobody rides free.

## 2. Wiring the coin slot (do this before powering the router)

The Allan 1239A Promax is a 12 V multi-coin acceptor. Its connector is labelled
**DC12V / COIN / GND / COUNTER**, and it has two switches on the back that must be
set to **FAST** and **NO** for controller use.

| Allan pin | Connects to |
| --- | --- |
| `DC12V` | +12 V of the 12 V PSU (usually the red wire) |
| `GND` | 0 V of the same PSU (black wire) and the Pi's ground |
| `COIN` | pulse output → Pi GPIO17 through the optocoupler (see below) |
| `COUNTER` | machine payout counter → leave unconnected |

### Never wire the pulse line straight into a GPIO pin

The pulse line sits in the 12 V domain, and a Pi GPIO is a 3.3 V input. Isolate
it with an optocoupler (PC817 or 4N35) — the acceptor's output transistor then
switches the opto's LED instead of your GPIO pin:

```
12 V PSU +12V ──[ 1 kΩ ]── PC817 pin 1 (anode)
                           PC817 pin 2 (cathode) ── Allan COIN pin
12 V PSU GND  ─────────────────────────────────── (same ground as the acceptor)

PC817 pin 4 (collector) ── Pi GPIO17 / pin 11   (internal pull-up on)
PC817 pin 3 (emitter)   ── Pi GND / pin 9
```

* With this wiring the acceptor pulls `COIN` to ground on each pulse, the opto
  transistor conducts, and GPIO17 falls to 0 V — so the pin is **active low**
  (`PISO_COIN_ACTIVE_LOW=1`, which is also what `tools/coin_pulse_probe.py`
  assumes by default). A 10 kΩ pull-up to 3.3 V is a good backup if you do not
  want to rely on the Pi's internal one.
* Power the Pi from its own supply and share only ground through the opto path.
  Never feed 12 V into the Pi's 5 V input.
* Sanity check with a multimeter before connecting the Pi: with the acceptor
  idle, `COIN` sits near 12 V, and it pulses toward 0 V when a coin passes.
* Every coil acceptor fires a burst of pulses per coin. The 1239A is
  programmable, and the usual piso setting is one pulse per peso (`P1=1`,
  `P5=5`, `P10=10`, `20` for a P20 program). Measure yours with
  `../tools/coin_pulse_probe.py` instead of trusting that.

### Configuring the acceptor's own pulse values

Hold **+** and **-** together for three seconds, then use **Set** to walk the menu:

1. `E` — how many coin programs to teach (up to 6).
2. `H1..H6` — how many sample coins of that program you will drop in later
   (10 is the documented standard, 20 is better).
3. `P1..P6` — the pulse value of that program. Set `P1=1`, `P2=5`, `P3=10`,
   `P4=20` so the pulses match the peso value.
4. `F1..F6` — sensitivity, standard `8`.
5. Final pass: the display shows `A1`, `A2`, … and you insert ten sample coins
   per program. It returns to `0` when the calibration is stored.

## 3. Prepare the router (WinBox or WebFig)

1. Power the unit, connect a laptop to **ether2** (not ether1).
2. Open WinBox → *Neighbors*, connect by MAC, log in as `admin` with no password.
3. **System → RouterBOARD → Check for updates** and install the latest
   RouterOS 7 firmware (**7.13** or newer — that is the `wifi` menu this script
   configures, and it also gives REST over `www-ssl` the behaviour the console expects).
4. Set a strong admin password (*System → Users*) and disable the `admin`
   account once your own user exists (optional but recommended).
5. Check device-mode, because HotSpot and `fetch` can be gated:

```
/system/device-mode/print
```

If `hotspot=no`, enable it and confirm physically (the router starts a 5 minute
countdown; press the listed button or power-cycle during it):

```
/system/device-mode/update hotspot=yes fetch=yes
```

6. Confirm the upstream works: *IP → DHCP Client* should list `ether1` as
   `bound`, and *IP → Firewall → NAT* should show a masquerade rule on `ether1`.

## 4. Import the hotspot script

1. Open `hap-ax-lite-piso.rsc` and edit the settings block at the top:

| Variable | Set it to |
| --- | --- |
| `ssid` | the SSID your customers join — keep it identical to *Settings → SSID* in the console |
| `wifiPassword` | leave `""` for an open network (the usual piso setup — the script clears the factory Wi-Fi password on the sticker and leaves the HotSpot as the gate), or type 8+ characters to switch the SSID to WPA2 |
| `wifiCountry` | leave `""` to keep the router's regulatory country, or name one the way MikroTik does (for example `"latvia"`) when the factory setup never asked and the AP has no allowed channel set |
| `portalIp` / `portalPort` | where the Pi serves the portal (`192.168.88.2` and `5000` by default) |
| `piMac` | the Pi's `eth0` MAC (`ip link show eth0`), so the router always gives it the same address |
| `apiPassword` | a strong password — put the same value in the Pi's `.env` as `PISO_ROUTER_PASSWORD` |

2. Upload the file: WinBox → **Files** → drag `hap-ax-lite-piso.rsc` in.
3. Run it: open **New Terminal** and type

```
/import file-name=hap-ax-lite-piso.rsc
```

4. Watch the log for `PisoPilot: setup finished`, then check the pieces:

```
/ip/hotspot print              # piso-hotspot on "bridge"
/ip/hotspot/profile print      # login-by=mac,http-chap,cookie,mac-cookie
/ip/hotspot/user/profile print # piso-package (1M/2M), piso-premium (2M/5M)
/ip/hotspot/walled-garden print
/ip/hotspot/walled-garden/ip print
/ip/hotspot/ip-binding print   # the Pi is "bypassed"
/ip/dns/static print           # portal.piso.local -> 192.168.88.2
/user print                    # piso-controller
/ip/service print              # www-ssl enabled, www/telnet/ftp disabled
```

5. Save a known-good baseline so you can always go back:

```
/export file=piso-baseline
```

### What the script deliberately does *not* do

* It never removes your firewall filter rules, NAT rules or existing hotspot
  users — it only adds what is missing and re-applies its own settings.
* It does not change the admin account or the Wi-Fi country/regulatory
  settings; pick the country once in **QuickSet** so the radio is legal.
* It does not touch the Pi's own firewall. Only `5000` needs to be reachable
  from the LAN, and Flask already binds `0.0.0.0`.

## 5. Prepare the Raspberry Pi

```
sudo apt update && sudo apt install -y python3-venv git
cd ~ && git clone <your-repo> piso-wifi && cd piso-wifi
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Give the Pi a fixed address (`sudo raspi-config` → *Network* → static, or the
DHCP lease created by the script) and keep it on **ether2/ether3/ether4** — never
on `ether1`, which belongs to the ISP.

Run it under systemd so it survives reboots and power cuts (`/etc/systemd/system/piso-wifi.service`):

```
[Unit]
Description=PisoPilot console and customer portal
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/piso-wifi
EnvironmentFile=/home/pi/piso-wifi/.env
ExecStart=/home/pi/piso-wifi/.venv/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl daemon-reload
sudo systemctl enable --now piso-wifi
systemctl status piso-wifi
```

`PISO_DEBUG=0` in `.env` matters here: the reloader would otherwise start the
process twice and both copies would claim GPIO17, double-counting every coin.

## 6. Point the Pi at the router, then at the hardware

Keep `PISO_HARDWARE_MODE=simulation` for now. Fill in `.env` on the Pi:

```
PISO_HARDWARE_MODE=simulation        # flip to real only after the checks below pass
PISO_DEBUG=0

PISO_ROUTER_URL=https://192.168.88.1/rest
PISO_ROUTER_USER=piso-controller
PISO_ROUTER_PASSWORD=<same password you put in the .rsc>
PISO_ROUTER_VERIFY_SSL=0             # self-signed certificate
PISO_ROUTER_PROFILE=piso-package     # paid tier every client gets
PISO_ROUTER_RATE_PROFILES={"2/1":"piso-package","5/2":"piso-premium"}

PISO_COIN_GPIO=17
PISO_COIN_PULL_UP=1
PISO_COIN_ACTIVE_LOW=1               # matches the optocoupler wiring above
PISO_COIN_DEBOUNCE_MS=100
PISO_COIN_PULSES={"1":1,"5":5,"10":10,"20":20}
```

`PISO_ROUTER_RATE_PROFILES` maps a session's *download/upload* MBps to a hotspot
user profile, so a plan that promises 5 Mbps down gets `piso-premium` instead of
`piso-package`. Anything not in the map falls back to `PISO_ROUTER_PROFILE`.

Then prove the connection before trusting it:

```
python router/verify_router.py
```

The tool logs in over HTTPS, prints the RouterOS version, lists the hotspot
profiles, creates a throwaway HotSpot user, drops it again and reports how long
each REST call took. Every check must pass before you continue.

Measure the coin slot next (app stopped, so only the probe owns the pin):

```
sudo systemctl stop piso-wifi
python tools/coin_pulse_probe.py --gpio 17 --active-low
```

Drop one coin of each denomination and write down the pulse counts. Update
`PISO_COIN_PULSES` if they differ from the default, then restart the service.

## 7. Go live, in this order

1. `PISO_HARDWARE_MODE=real` in `.env`, then `sudo systemctl restart piso-wifi`.
2. Console → **Hardware links** should now read the router as `online`
   (`/api/hardware/status` shows the RouterOS version and CPU load).
3. Connect a phone to the SSID. The captive-portal popup (or any http:// page)
   should land on the PisoPilot portal, not the RouterOS login form. If you see
   the RouterOS form, the `login.html` fetch in step 12 of the script failed —
   run that one command again from the router terminal.
4. Press **Insert coin** on the phone. The console's coin simulator opens by
   itself; drop coins in the real slot and watch each one land.
5. Wait for the quiet window (or press **Finish / accept coins** in the console).
   The phone should reload into an active session and the router should now show
   the device:

```
/ip/hotspot/user print        # one user named after the phone's MAC
/ip/hotspot/active print      # the phone, logged in with that user
```

6. Let the timer run out (or press **Kick** in the console). The user is removed,
   the phone loses the uplink and gets the portal back.

## 8. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Portal never loads on the phone | `/ip/hotspot/walled-garden/ip print` must show the Pi. From the router: `/tool ping 192.168.88.2 count=2` |
| RouterOS login form instead of the portal | The `login.html` fetch failed. From the router: `/tool fetch url="http://192.168.88.2:5000/hotspot/login.html" dst-path=hotspot/login.html`, and enable `fetch` in device-mode |
| Hardware links show `offline` | Wrong URL/user/password, or `www-ssl` is off. Test from the Pi: `curl -k -u piso-controller:<password> https://192.168.88.1/rest/system/resource` |
| `401` in the console log | `PISO_ROUTER_PASSWORD` does not match the router user |
| `unknown parameter` in the log | RouterOS rejected a field; update the unit, then re-run `verify_router.py` |
| `Script Error: expected end of command (line N column M)` | The import stops at the first token it cannot parse, so fix that one line and import again. It is usually a `#` comment trailing a command (move the comment to its own line, or end the command with `;` first) |
| The import stops on the SSID/WPA2 line | RouterOS **7.13** moved the SSID and the radio security into the interface sub-objects, so the script sets `configuration.ssid`, `security.authentication-types` and `security.passphrase`. On older firmware set the SSID and password by hand in WinBox → *WiFi*, or upgrade to 7.13+ |
| Phone pays but stays blocked | `/ip/hotspot/user print` should list the MAC. If not, the console could not resolve the client MAC — check `/ip/hotspot/host print` and how the phone reaches the portal |
| Phone pays but has no internet | Check `/ip/hotspot/active print` (uptime climbing), then the upstream: `/ip/dhcp-client print` |
| Every coin counts twice | `PISO_DEBUG=0` and only one `app.py` process (`pgrep -af app.py`) |
| Coins ignored or the wrong value | Re-run the pulse probe; check `PISO_COIN_PULSES` and the acceptor's `P1..P4` pulse programming |
| Devices get cut off too soon | Raise `keepalive-timeout` on the user profile. `limit-uptime` counts only connected time, so a phone that stays off does not spend its minutes |

## 9. Everyday operations

* **Who is online:** `/ip/hotspot/active print detail`
* **Change a speed tier:** `/ip/hotspot/user/profile set [find name=piso-package] rate-limit="1M/2M"`.
  RouterOS reads `rate-limit` as *upload/download*, so `1M/2M` is 1 Mbps up and
  2 Mbps down. Users pick up the new value on their next login.
* **Grant time by hand:** the *Extend* button on a client row does it for you.
  Manually: `/ip/hotspot/user set [find name=AA:BB:CC:DD:EE:FF] limit-uptime=1h`.
* **Free a locked device:** `/ip/hotspot/user remove [find name=AA:BB:CC:DD:EE:FF]`.
* **Re-apply this configuration:** import the `.rsc` again — it is safe to re-run
  and never duplicates what already exists.
* **Back up / roll back:** `/export file=piso-baseline` then
  `/import file=piso-baseline.rsc`, or reset the unit with the button.

## 10. How the pieces talk at runtime

1. A phone joins the SSID and is blocked by the hotspot.
2. It opens any page → RouterOS serves `hotspot/login.html`, generated by the Pi
   → the browser lands on `http://192.168.88.2:5000/portal`.
3. The portal sees the phone's IP and asks the router (`/ip/hotspot/host`) which
   MAC owns it, so the session is keyed on the real device MAC.
4. The customer presses **Insert coin**; pulses on GPIO17 accumulate into one
   total that is settled once at the `#pricing` rows.
5. The controller creates a HotSpot user — `name=<MAC>`, `password=<MAC>`,
   `mac-address=<MAC>`, `limit-uptime=<paid minutes>m`, `profile=<tier>` — and
   RouterOS logs that MAC in automatically (`login-by=mac`), with no typing.
6. A sweep thread runs every `PISO_ROUTER_SWEEP_SECONDS` (30 s by default):
   when a session's clock runs out, the user is deleted and the device dropped
   from `/ip/hotspot/active`, so the machine never gives the internet away.

Everything above is exercised by `verify_router.py` (router side) and by the
console's Hardware panel (both sides), so a broken link is visible before a
customer ever loses a coin.
