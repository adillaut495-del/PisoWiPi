# PisoPilot

A local administration console for a Piso WiFi machine. It runs on a Raspberry Pi or mini PC and provides a SQLite-backed control plane for sales, pricing, vouchers, clients, settings, authentication, and audit logs.

## Included administration modules

- Dashboard revenue totals, active clients, Pi resource metrics, and hardware-link states
- Time-capped and data-capped access plans with promo bonuses
- Coin payments settled once from the whole inserted amount, at the coin rates the `#pricing` plan rows set
- Bulk voucher generation with expiry and status tracking
- Connected-session controls for pause/resume, extension, kick, traffic, and QoS fields
- SSID, MAC-as-voucher, coin pulse tolerance, auto-settle window, and session-mode settings
- Admin password management, system-action audit records, and event logs
- Live internet online/offline indicator (operator console header, Hardware panel, and customer portal) with an offline banner
- Web Audio sound effects on both consoles (coin accepted, client waiting, credited, uplink lost)
- Public customer portal at `/portal` with a splash screen, prominent `Insert coin` / `Use a voucher` actions, a rates modal, voucher login, live countdown, and usage summary
- Real hardware adapters for Allan coinslot pulse input and MikroTik RouterOS REST health/HotSpot operations, with acceptor pulses feeding the same whole-total settlement the operator simulator uses

## Live coin insertion flow

The portal exposes one `Insert coin` button per client. Pressing it opens a coin request, and the operator console renders the coin-acceptor simulator for that request. Only one request can be open at a time, so a second client is shown `Coin acceptor in use` until the first request is credited or released.

1. Client presses `Insert coin` on `/portal` and the waiting banner shows the live coin count, peso total, and minutes.
2. The operator console polls `GET /api/admin/coin-requests` every two seconds; the simulator modal opens by itself as soon as a request appears.
3. The operator inserts P1, P5, P10, or P20 (`POST /coin-requests/<id>/insert`); a coin only adds to the running total, and the response carries the price-list mix that total already reaches.
4. The client polls `GET /api/portal/coin-request` every two seconds and watches each coin land, then reloads into the active session when the request is accepted.
5. The operator presses `Finish / accept coins` (`POST /coin-requests/<id>/complete`): the whole amount is settled once against the price list rows, that credit is written to the request and to `coin_events`, and the session is activated or stacked. The client can release the acceptor early with `POST /portal/request-coin/cancel`.

### Physical coin slot

With `PISO_HARDWARE_MODE=real`, `hardware.CoinPulseListener` turns each pulse train into a weighed coin and calls `record_hardware_coin(amount, pulses)`. The acceptor cannot tell who paid, so a coin is credited to whoever holds the open request, exactly like an operator insert:

1. `record_hardware_coin` looks up the open request and calls `accumulate_coin(request_id, amount)`, which adds the coin to the running total and re-reads the best payout in the same transaction, so a pulse and an operator click landing together both count;
2. every coin restarts the quiet window (`coin_auto_settle_seconds`, default 5, `0` = the operator presses Finish) through `arm_auto_settle`, and `settle_quiet_request` settles the whole total with the same `complete_coin_request` the console uses;
3. the client's two-second poll shows each coin landing and then reloads into the activated or stacked session, so an unattended machine sells time with nobody clicking.

Because a payment is settled once, `coin_events` records the settlement rather than each coin: `coin-auto-settle`, `admin-simulated-coin`, `coin-cancel-settled`, or `coin-request-timeout` says what triggered it, while the audit log keeps a per-coin line with its pulse count, the running total, and the mix that total buys. A coin that lands with nobody waiting is still booked for revenue but grants no minutes, and the log says so at `warning` level so the operator can hand the time out by hand.

Money already in the box is never voided: a client who cancels while the request holds coins is settled instead of cancelled, and a client who walks away is settled by `expire_stale_coin_requests` after `coin_request_timeout_minutes` (default 10). Only an empty request expires. A restart drops the in-process timers, so `start_hardware` runs `settle_quiet_coin_requests` at boot and settles anything whose coins are already past the quiet window.

Requests abandoned by a client are marked `expired` after `coin_request_timeout_minutes` (default 10), so an idle client can never keep the acceptor locked.

## Coin payout: the `#pricing` rows are the coin rates

Coins land one at a time, but a client is never paid per coin. `insert_simulated_coin` only accumulates the peso total, and `complete_coin_request` settles that amount in one go through `best_coin_allocation(amount)`, which prices the whole total off the same `plans` table the operator edits at `#pricing`:

1. `best_plan_rates()` collapses the active `cap_type = 'time'` rows into one rate per denomination (the best paying row wins when several share a coin size); data-capped plans are skipped because they carry a megabyte limit instead of minutes;
2. the amount is covered from the largest denomination down, so a P20 total is paid by the P20 row (`Night Owl`), a P15 total by the P10 and P5 rows, and twenty P1 coins by that same P20 row - coins still add up to the bigger plan;
3. pesos that no row covers (a P7 total when only P5 and P10 rows exist) fall back to `minutes_per_peso`.

The rows *are* the price list, so nothing is invented on top of them: whatever the pricing panel shows for a coin is exactly what that coin is credited. That single source is why the P1/P5/P10/P20 chips, the operator simulator buttons, the console's `COIN RATES` strip and the credited minutes all read `coin_options()` / `best_coin_allocation()`. With the seeded `Piso 1` (5 min), `Piso 5` (30 min), `Piso 10 Promo` (210 min) and a `Night Owl` P20 row (270 min) the chips read 5 / 30 / 210 / 270, P15 settles as `Piso 10 Promo + Piso 5` = 240 minutes, and P20 settles as `Night Owl` = 270 minutes (the earlier knapsack paid 420 by repeating the promo past the operator's own P20 row).

A row can price a coin below the standard rate, so `coin_options()` also returns a `hint` per denomination (`P20 pays 270 min here, but 2 x Piso 10 Promo would pay 420 min for the same money.`). The pricing panel prints it on the card, so a price list that leaks minutes shows up where it is edited instead of on the portal.

`coin_request_payload` returns a live `reward` block (`minutes`, `rate`, `rate_minutes`, `bonus_minutes`, `plans[]`, `remainder_pesos`, `remainder_minutes`, `summary`) that the client banner, the rates modal, and the operator simulator all read, so the mix on screen is always the mix that will be credited. `coin_options()` adds `plan`, `source`, `rate_minutes`, `menu_minutes` and `hint` for the chips, the simulator buttons and the pricing panel. `POST /simulate-coin` books its event through `best_coin_allocation` as well, so `minutes issued` matches what the price list pays.

## Customer portal experience

`/portal` paints a splash overlay immediately: the portal mark, the SSID, a scanning bar, and an `Uplink offline - your coins are still counted` line when the cached probe already knows the link is down. `static/portal-experience.js` fades the overlay out 900 ms after the `load` event, a four-second watchdog removes it even if `load` never fires, and `Skip` dismisses it on demand. Without JavaScript a CSS animation slides it away after three seconds, so a client can never be trapped behind the splash.

The page is built around the two ways to pay, so a first-time guest sees them right away instead of hunting through the rate table:

- `Insert coin` (`POST /portal/request-coin`) with the P1/P5/P10/P20 credits as chips and a busy or disabled state while the single coin acceptor is claimed
- `Use a voucher` (`POST /portal/redeem`) with the code field and the `portal-alert` error slot
- One shared Jinja macro renders both cards on the guest landing page and in the `Add more time` section of an active session, and the plans moved from an inline section into a `Rates` modal

The modal is a small accessible controller in `static/portal-experience.js`: `[data-modal-open]` opens it, `[data-modal-close]` and a backdrop click close it, `Escape` closes it, `Tab` is trapped inside the card, focus returns to the button that opened it, and `body.portal-modal-open` locks background scrolling. `[data-portal-scroll]` closes the modal and scrolls to the coin card. Focus rings are always visible, `prefers-reduced-motion` shortens the splash and skips the smooth scroll, and the action grid collapses to one column under 760 px.

With JavaScript off the header `Rates` button is hidden and the modal renders inline as a normal section, so the rates are always reachable. The headline, the chips and the `Accepted coins` line are all built from `coin_options()`, so the modal quotes the same `#pricing` rows the operator edits, and the plan cards come from the `Piso 1` / `Piso 5` / `Piso 10 Promo` / `Night Owl` rows the portal view passes to the template.

## Internet status and sound effects

Both consoles show a live `Internet online` / `Internet offline` chip and a sound toggle, driven by `GET /api/internet-status` (two-second coin polling and a five-second uplink poll). The admin console also gets a Hardware-panel `Internet` row, an offline banner, and a warning tone when the uplink drops; the customer portal gets the chip plus an offline notice so clients know the machine, not their device, is the problem.

The uplink probe is deliberately dependency-free: it opens a TCP socket to `PISO_INTERNET_PROBES` (default `1.1.1.1:53`, then `8.8.8.8:53`) and falls back to resolving `PISO_INTERNET_DNS`. Results are cached for `PISO_INTERNET_CACHE_SECONDS` so page loads and polls never block on the probe, and pages render with the cached value (`Checking internet` until the first probe completes).

`static/sound.js` synthesises its effects with the Web Audio API, so no audio files are shipped: `coin` (a coin landed, pitched by denomination), `request` (a client is waiting), `success` (credited), `warn` (uplink lost), and `error` (rejected). Browsers keep audio suspended until a page receives a gesture, so both pages unlock playback on the first tap or keypress and the toggle button reads `Sound: tap to enable` until then; the on/off choice is remembered in `localStorage` (`piso.sound.enabled`).

GPIO, router, captive-portal, and reboot actions are represented by adapter boundaries. With `PISO_HARDWARE_MODE=real` the controller drives the installed hardware: coin pulses become settled payments, and every paid session is mirrored onto the MikroTik hotspot as one user per device MAC, then revoked when its clock runs out. Reboot and shutdown actions stay log-only.

## Router and coin-slot setup

`router/README.md` is the installation guide for the three pieces of hardware this build targets — a MikroTik hAP ax lite, a Raspberry Pi 3 Model B, and an Allan 1239A Promax coin slot:

- `router/hap-ax-lite-piso.rsc` — a re-runnable RouterOS 7 script that builds the LAN, the SSID, the HotSpot (with MAC login), the walled garden that only lets unpaid clients reach the controller, the Pi's static lease and HotSpot bypass, the restricted REST API user and `www-ssl`
- `router/verify_router.py` — runs on the Pi and proves every REST call the app makes, including the create/extend/revoke cycle a payment triggers
- `tools/coin_pulse_probe.py` — runs on the Pi and measures how many pulses each denomination really sends, so `PISO_COIN_PULSES` is filled in from the acceptor instead of from a guess

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py app.py
```

Open <http://127.0.0.1:5000>.

Customer portal: <http://127.0.0.1:5000/portal>.

The initial local account is `admin` / `admin`. Change it from the Security section after signing in.

The `/api/status` endpoint reports controller health, configured rate, sales totals, active users, and resource metrics. The customer session endpoint is `/api/portal/session`. The simulator is deliberately isolated from GPIO so a hardware adapter can be added without changing the dashboard or database layer.

The current portal is the application layer of the captive-portal flow. In real mode it now also owns the router side of that flow: the hotspot's `login.html` is replaced with `templates/hotspot-login.html` (served at `/hotspot/login.html`) so unpaid clients land on `/portal`, sessions are keyed on the MAC the router reports for the client's address, and paid time becomes a hotspot user that the sweep thread revokes at expiry.

## Hardware activation

Copy `.env.example` to `.env` (hardware.py reads it on startup; real environment variables still win) or export the values in the service environment. Keep `PISO_HARDWARE_MODE=simulation` until the wiring and the router are verified, then set it to `real` on the Raspberry Pi only.

**Start with `router/README.md`** — it walks the whole installation in order: wiring the acceptor through the documented 12 V relay module (or the PC817 fallback), preparing the router, importing `hap-ax-lite-piso.rsc`, running the Pi under systemd, and the checks that have to pass before a customer ever pays.

For the Allan Universal Coinslot 1239A Promax, use relay contacts between the Pi GPIO and ground; only connect the selector's pulse line to the relay-module input if that input is rated for its 12 V open-collector signal. Never connect 12 V to a Pi pin. Measure pulse counts with `tools/coin_pulse_probe.py`, then set `PISO_COIN_PULSES`, `PISO_COIN_GPIO` and `PISO_COIN_ACTIVE_LOW`.

For the MikroTik hAP ax lite, `hap-ax-lite-piso.rsc` enables RouterOS 7 `www-ssl`, creates the restricted API user and builds the HotSpot. The adapter uses `/system/resource`, `/ip/hotspot/active`, `/ip/hotspot/host`, `/ip/hotspot` and `/ip/hotspot/user`; run `python router/verify_router.py` on the Pi to prove all of them before flipping to real mode.

Going real changes three things: the GPIO listener counts the acceptor's coins, sessions are keyed on the MAC the hotspot reports for the client's address, and a background sweep revokes the hotspot user the moment a paid session expires. The adapter still does not execute reboot or shutdown commands, and it does not assume the acceptor's signal voltage — that one detail is confirmed with a multimeter as described in the setup guide.

