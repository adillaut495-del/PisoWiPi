from __future__ import annotations

import json
import importlib
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

try:
    DigitalInputDevice = importlib.import_module("gpiozero").DigitalInputDevice
except ImportError:
    DigitalInputDevice = None

LOGGER = logging.getLogger(__name__)


def load_env_file(path=None):
    """Read the project ``.env`` into ``os.environ`` so ``cp .env.example .env`` is enough.

    Values already present in the environment win, because systemd's
    ``EnvironmentFile`` and an operator's ``export`` are the authoritative copy.
    Reading the file here (before ``HardwareConfig`` is defined) is what makes the
    documented workflow work without adding a dependency on python-dotenv.
    """
    env_path = Path(path) if path else Path(os.getenv("PISO_ENV_FILE") or Path(__file__).resolve().parent / ".env")
    if not env_path.is_file():
        return {}
    loaded = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
        loaded[key] = os.environ[key]
    return loaded


load_env_file()


@dataclass(frozen=True)
class HardwareConfig:
    mode: str = os.getenv("PISO_HARDWARE_MODE", "simulation")
    router_url: str = os.getenv("PISO_ROUTER_URL", "https://192.168.88.1/rest").rstrip("/")
    router_user: str = os.getenv("PISO_ROUTER_USER", "admin")
    router_password: str = os.getenv("PISO_ROUTER_PASSWORD", "")
    router_verify_ssl: bool = os.getenv("PISO_ROUTER_VERIFY_SSL", "0") == "1"
    coin_gpio: int = int(os.getenv("PISO_COIN_GPIO", "17"))
    coin_pull_up: bool = os.getenv("PISO_COIN_PULL_UP", "1") == "1"
    coin_active_low: bool = os.getenv("PISO_COIN_ACTIVE_LOW", "0") == "1"
    coin_debounce_ms: int = int(os.getenv("PISO_COIN_DEBOUNCE_MS", "80"))
    coin_pulses: str = os.getenv("PISO_COIN_PULSES", '{"1": 1, "5": 5, "10": 10, "20": 20}')
    router_default_profile: str = os.getenv("PISO_ROUTER_PROFILE", "piso-package")
    router_rate_profiles: str = os.getenv("PISO_ROUTER_RATE_PROFILES", '{"2/1": "piso-package", "5/2": "piso-premium"}')
    router_sweep_seconds: int = int(os.getenv("PISO_ROUTER_SWEEP_SECONDS", "30"))

    def pulse_map(self):
        try:
            values = json.loads(self.coin_pulses)
            return {int(pulses): float(amount) for pulses, amount in values.items()}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {1: 1.0, 5: 5.0, 10: 10.0}

    def rate_profile_map(self):
        """Session QoS (download/upload MBps) to hotspot user profile on the router."""
        try:
            values = json.loads(self.router_rate_profiles)
            return {str(key): str(profile) for key, profile in values.items()}
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            return {}


class MikroTikRouterAdapter:
    def __init__(self, config: HardwareConfig | None = None):
        self.config = config or HardwareConfig()
        self.session = requests.Session()
        self.session.auth = (self.config.router_user, self.config.router_password)
        self.session.verify = self.config.router_verify_ssl
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, f"{self.config.router_url}/{path.lstrip('/')}", timeout=4, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def health(self):
        if self.config.mode != "real":
            return {"status": "simulated", "detail": "Router adapter disabled"}
        try:
            resource = self._request("GET", "system/resource")
            resource = resource[0] if isinstance(resource, list) else resource
            return {"status": "online", "version": resource.get("version", "unknown"), "cpu_load": resource.get("cpu-load", "n/a")}
        except (requests.RequestException, ValueError, AttributeError) as error:
            LOGGER.warning("MikroTik health check failed: %s", error)
            return {"status": "offline", "detail": str(error)}

    def active_clients(self):
        if self.config.mode != "real":
            return []
        try:
            return self._request("GET", "ip/hotspot/active") or []
        except requests.RequestException as error:
            LOGGER.warning("MikroTik active-client query failed: %s", error)
            return []

    def hotspot_users(self):
        if self.config.mode != "real":
            return []
        try:
            users = self._request("GET", "ip/hotspot/user") or []
        except (requests.RequestException, ValueError) as error:
            LOGGER.warning("MikroTik hotspot user query failed: %s", error)
            return []
        return users if isinstance(users, list) else [users]

    def sync_hotspot_user_profile(self, name, download_mbps, upload_mbps):
        """Create or update a RouterOS profile with its rate-limit (upload/download)."""
        if self.config.mode != "real":
            return {"status": "simulated", "name": name}
        profiles = self._request("GET", "ip/hotspot/user/profile") or []
        profiles = profiles if isinstance(profiles, list) else [profiles]
        existing = next((profile for profile in profiles if profile.get("name") == name), None)
        rate_limit = f"{round(float(upload_mbps) * 1000):g}k/{round(float(download_mbps) * 1000):g}k"
        if existing:
            self._request(
                "PATCH",
                f"ip/hotspot/user/profile/{existing['.id']}",
                json={"rate-limit": rate_limit},
            )
            status = "updated"
        else:
            self._request(
                "PUT",
                "ip/hotspot/user/profile",
                json={
                    "name": name,
                    "rate-limit": rate_limit,
                    "shared-users": "1",
                    "keepalive-timeout": "3m",
                    "idle-timeout": "none",
                    "session-timeout": "0s",
                    "add-mac-cookie": "yes",
                    "mac-cookie-timeout": "1d",
                    "open-status-page": "http-login",
                },
            )
            status = "created"
        return {"status": status, "name": name, "rate_limit": rate_limit}

    def hotspot_user(self, mac_address):
        """The hotspot user that belongs to one device, matched by name or bound MAC."""
        wanted = (mac_address or "").strip().lower()
        if not wanted:
            return None
        for user in self.hotspot_users():
            if str(user.get("name", "")).lower() == wanted or str(user.get("mac-address", "")).lower() == wanted:
                return user
        return None

    def hotspot_hosts(self):
        """The router's own IP-to-MAC table for the devices behind the hotspot."""
        if self.config.mode != "real":
            return []
        try:
            hosts = self._request("GET", "ip/hotspot/host") or []
        except (requests.RequestException, ValueError) as error:
            LOGGER.warning("MikroTik hotspot host query failed: %s", error)
            return []
        return hosts if isinstance(hosts, list) else [hosts]

    def mac_for_ip(self, ip_address):
        """Which device really sent a portal request: the MAC the hotspot reports for that IP.

        The portal only ever sees a client's address and the hotspot hands those
        out by DHCP, so this is what turns "192.168.88.34 opened /portal" into the
        MAC address the session and the hotspot user are keyed on.
        """
        ip_address = (ip_address or "").strip()
        if not ip_address:
            return None
        for host in self.hotspot_hosts():
            if str(host.get("address", "")).strip() != ip_address:
                continue
            mac = str(host.get("mac-address", "")).strip()
            if mac and mac != "00:00:00:00:00:00":
                return mac
        return None

    def rate_profile(self, download_mbps=None, upload_mbps=None):
        """The hotspot user profile that matches a session's QoS columns."""
        try:
            key = f"{int(float(download_mbps or 0))}/{int(float(upload_mbps or 0))}"
        except (TypeError, ValueError):
            key = ""
        return self.config.rate_profile_map().get(key) or self.config.router_default_profile

    def hotspot_user_payload(self, mac_address, minutes, password=None, profile=None):
        """The fields RouterOS 7 accepts for a paid device.

        The user is named after the MAC address and shares that name as its
        password, which is what RouterOS 7's default ``mac-auth-mode``
        (``mac-as-username-and-password``) expects when a device logs in by MAC.
        There is no ``rate-limit`` here: in RouterOS 7 the speed tier belongs to
        the user *profile*, which this payload selects with ``profile``.
        """
        payload = {
            "name": mac_address,
            "password": password or mac_address,
            "mac-address": mac_address,
            "limit-uptime": f"{max(1, int(minutes))}m",
        }
        if profile:
            payload["profile"] = profile
        return payload

    def authorize(self, mac_address, minutes, download_mbps=None, upload_mbps=None, profile=None, password=None):
        """Let one MAC address online for the next ``minutes``.

        ``minutes`` is the session's *remaining* time, so calling this again after
        another coin simply rewrites the same limit. RouterOS reads
        ``limit-uptime`` at login, so a device that was already online is dropped
        once: it re-authenticates by MAC a moment later with the new limit.
        """
        minutes = max(1, int(minutes))
        if self.config.mode != "real":
            return {"status": "simulated", "name": mac_address, "minutes": minutes}
        chosen = profile or self.rate_profile(download_mbps, upload_mbps)
        payload = self.hotspot_user_payload(mac_address, minutes, password=password, profile=chosen)
        existing = self.hotspot_user(mac_address)
        if not existing:
            self._request("PUT", "ip/hotspot/user", json=payload)
            return {"status": "created", "name": mac_address, "minutes": minutes, "profile": chosen}
        self._request("PATCH", f"ip/hotspot/user/{existing['.id']}", json={k: v for k, v in payload.items() if k != "name"})
        self.disconnect(mac_address)
        return {"status": "updated", "name": mac_address, "minutes": minutes, "profile": chosen}

    def revoke(self, mac_address):
        """End a device's access: drop the live session, then delete its hotspot user."""
        if self.config.mode != "real":
            return {"status": "simulated", "name": mac_address}
        dropped = self.disconnect(mac_address)
        existing = self.hotspot_user(mac_address)
        if not existing:
            return {"status": "no-user", "name": mac_address, "disconnected": dropped.get("status")}
        self._request("DELETE", f"ip/hotspot/user/{existing['.id']}")
        return {"status": "removed", "name": mac_address, "disconnected": dropped.get("status")}

    def create_hotspot_user(self, username, password, minutes, download_mbps=2, upload_mbps=1):
        """Kept for callers that came before: ``authorize`` owns the exact fields."""
        return self.authorize(username, minutes, download_mbps=download_mbps, upload_mbps=upload_mbps, password=password)

    def disconnect(self, mac_address):
        """Drop a connected device off the hotspot without touching its user row."""
        if self.config.mode != "real":
            return {"status": "simulated"}
        for client in self.active_clients():
            if client.get("mac-address", "").lower() == (mac_address or "").lower():
                self._request("DELETE", f"ip/hotspot/active/{client['.id']}")
                return {"status": "disconnected", "name": client.get("user", mac_address)}
        return {"status": "not-connected"}


class CoinPulseListener:
    def __init__(self, on_coin: Callable[[float, int], None], config: HardwareConfig | None = None):
        self.config = config or HardwareConfig()
        self.on_coin = on_coin
        self.device = None
        self._lock = threading.Lock()
        self._pulse_count = 0
        self._last_pulse = 0.0
        self._timer = None

    @property
    def available(self):
        """True when the pin *could* be claimed: real mode, with a GPIO library present."""
        return self.config.mode == "real" and DigitalInputDevice is not None

    @property
    def listening(self):
        """True only once this process really holds the pin, so the console can trust it."""
        return self.device is not None

    def start(self):
        if not self.available:
            return {"status": "simulated", "detail": "GPIO listener disabled"}
        try:
            self.device = DigitalInputDevice(self.config.coin_gpio, pull_up=self.config.coin_pull_up)
        except Exception as error:
            # A dead coin pin must never take the portal down with it. The portal is the page
            # that takes the money, and a process that dies here leaves nobody able to even see
            # the fault. Leave the pin unclaimed, report it, and keep serving - the console
            # shows the acceptor as offline.
            self.device = None
            LOGGER.error("could not claim GPIO%s for the coin acceptor: %s", self.config.coin_gpio, error)
            return {"status": "offline", "gpio": self.config.coin_gpio, "detail": str(error)}
        if self.config.coin_active_low:
            self.device.when_deactivated = self._pulse
        else:
            self.device.when_activated = self._pulse
        return {"status": "online", "gpio": self.config.coin_gpio}

    def _pulse(self):
        with self._lock:
            self._pulse_count += 1
            self._last_pulse = time.monotonic()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.config.coin_debounce_ms / 1000, self._commit)
            self._timer.daemon = True
            self._timer.start()

    def _commit(self):
        with self._lock:
            pulses = self._pulse_count
            self._pulse_count = 0
        amount = self.config.pulse_map().get(pulses)
        if amount is None:
            LOGGER.warning("Ignored unmapped coin pulse count: %s", pulses)
            return
        self.on_coin(amount, pulses)

    def stop(self):
        if self.device:
            self.device.close()
            self.device = None


router_adapter = MikroTikRouterAdapter()
coin_listener = None


def real_mode():
    """True when the controller is allowed to drive the installed hardware."""
    return router_adapter.config.mode == "real"


def coin_listener_state():
    """What the coin acceptor is really doing: 'simulated' outside real mode, 'offline' when
    the pin could not be claimed. Reading this straight off the config used to report 'online'
    for an acceptor that never started, which is the worst possible thing to hide."""
    if not real_mode():
        return "simulated"
    return "online" if coin_listener is not None and coin_listener.listening else "offline"


def hardware_status():
    """The four links on the dashboard.

    "controller" used to be a copy of the coin acceptor's state, which made the
    dashboard report Controller Offline whenever the acceptor was not running -
    two unrelated things sharing one value. The controller is the router adapter,
    so it is judged on its own: reachable in real mode, and deliberately not
    driving anything otherwise. The dashboard then shows Simulation for that,
    which is the truth, rather than a red Offline that looks like a fault.
    """
    router = router_adapter.health()
    state = coin_listener_state()
    if not real_mode():
        controller = "simulated"
    else:
        controller = "online" if router.get("status") == "online" else "offline"
    return {
        "mode": router_adapter.config.mode,
        "coin_acceptor": state,
        "controller": controller,
        "gateway": router.get("status", "offline"),
        "router": router,
    }


_internet_lock = threading.Lock()
_internet_cache = {"value": None, "expires": 0.0}


def internet_targets():
    """TCP probe targets, e.g. PISO_INTERNET_PROBES=1.1.1.1:53,8.8.8.8:53."""
    raw = os.getenv("PISO_INTERNET_PROBES", "1.1.1.1:53,8.8.8.8:53")
    targets = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        host, separator, port = chunk.rpartition(":")
        if separator and port.isdigit():
            targets.append((host or chunk, int(port)))
        else:
            targets.append((chunk, 53))
    return targets or [("1.1.1.1", 53)]


def tcp_latency(host, port, timeout):
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout):
        return round((time.monotonic() - started) * 1000, 1)


def probe_internet(timeout=None):
    """Return (online, latency_ms, method). TCP reachability first, DNS resolution as fallback."""
    timeout = timeout or float(os.getenv("PISO_INTERNET_TIMEOUT_SECONDS", "1.5"))
    for host, port in internet_targets():
        try:
            return True, tcp_latency(host, port, timeout), f"tcp://{host}:{port}"
        except OSError:
            continue
    dns_host = os.getenv("PISO_INTERNET_DNS", "one.one.one.one")
    try:
        started = time.monotonic()
        socket.getaddrinfo(dns_host, None)
        return True, round((time.monotonic() - started) * 1000, 1), f"dns://{dns_host}"
    except OSError:
        return False, None, None


def internet_status(force=False, allow_probe=True):
    """Cached uplink state shared by the admin console and the customer portal.

    Pages render with allow_probe=False so a slow probe never delays a response; the
    consoles then poll this cached value, which refreshes the probe at most every
    PISO_INTERNET_CACHE_SECONDS (default 5).
    """
    now = time.monotonic()
    with _internet_lock:
        cached = _internet_cache["value"]
        fresh = cached is not None and now < _internet_cache["expires"]
    if cached is not None and fresh and not force:
        return dict(cached, cached=True)
    if not allow_probe:
        if cached is not None:
            return dict(cached, cached=True)
        return {"online": False, "state": "checking", "latency_ms": None, "method": None, "checked_at": None, "cached": False}
    online, latency, method = probe_internet()
    value = {
        "online": online,
        "state": "online" if online else "offline",
        "latency_ms": latency,
        "method": method,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "cached": False,
    }
    with _internet_lock:
        _internet_cache["value"] = value
        _internet_cache["expires"] = time.monotonic() + float(os.getenv("PISO_INTERNET_CACHE_SECONDS", "5"))
    return value
