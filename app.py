from datetime import datetime, timedelta
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import hardware
from hardware import CoinPulseListener, hardware_status, internet_status, router_adapter

try:
    import psutil
except ImportError:
    psutil = None

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "piso_wifi.db"

app = Flask(__name__)
app.secret_key = os.environ.get("PISO_WIFI_SECRET", "change-this-local-secret")


def now_text():
    return datetime.now().isoformat(timespec="seconds")


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coin_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                minutes INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                denomination REAL NOT NULL,
                minutes INTEGER NOT NULL,
                bonus_minutes INTEGER NOT NULL DEFAULT 0,
                cap_type TEXT NOT NULL DEFAULT 'time',
                data_limit_mb INTEGER,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS vouchers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                minutes INTEGER NOT NULL,
                download_limit_mbps REAL NOT NULL DEFAULT 2,
                upload_limit_mbps REAL NOT NULL DEFAULT 1,
                router_profile TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'unused',
                mac_address TEXT,
                created_at TEXT NOT NULL,
                used_at TEXT,
                expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac_address TEXT NOT NULL,
                ip_address TEXT,
                minutes_left INTEGER NOT NULL,
                data_down_mb REAL NOT NULL DEFAULT 0,
                data_up_mb REAL NOT NULL DEFAULT 0,
                download_limit_mbps REAL NOT NULL DEFAULT 2,
                upload_limit_mbps REAL NOT NULL DEFAULT 1,
                router_profile TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                started_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                expires_at TEXT,
                source TEXT,
                granted_minutes INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS speed_tiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                download_limit_mbps REAL NOT NULL,
                upload_limit_mbps REAL NOT NULL,
                router_profile TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(download_limit_mbps, upload_limit_mbps),
                UNIQUE(router_profile)
            );
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS voucher_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                succeeded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coin_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                ip_address TEXT,
                plan_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                minutes INTEGER NOT NULL,
                coin_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                accepted_at TEXT,
                last_coin_at TEXT,
                last_activity_at TEXT
            );
            """
        )
        try:
            connection.execute("ALTER TABLE sessions ADD COLUMN expires_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE coin_requests ADD COLUMN coin_count INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE coin_requests ADD COLUMN last_coin_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE coin_requests ADD COLUMN last_activity_at TEXT")
        except sqlite3.OperationalError:
            pass
        connection.execute(
            "UPDATE coin_requests SET last_activity_at = COALESCE(last_activity_at, last_coin_at, created_at) WHERE last_activity_at IS NULL"
        )
        try:
            connection.execute("ALTER TABLE sessions ADD COLUMN source TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE sessions ADD COLUMN granted_minutes INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE vouchers ADD COLUMN download_limit_mbps REAL NOT NULL DEFAULT 2")
        except sqlite3.OperationalError:
            pass
        try:
            connection.execute("ALTER TABLE vouchers ADD COLUMN upload_limit_mbps REAL NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        for table in ("vouchers", "sessions"):
            try:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN router_profile TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('minutes_per_peso', '10')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('ssid', 'PisoPilot WiFi')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('session_mode', 'time')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('mac_voucher_mode', '0')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('coin_pulse_tolerance_ms', '80')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('coin_request_timeout_minutes', '10')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('coin_auto_settle_seconds', '5')"
        )
        coin_config = hardware.HardwareConfig()
        coin_input_defaults = {
            "coin_gpio": str(coin_config.coin_gpio),
            "coin_pull_up": "1" if coin_config.coin_pull_up else "0",
            "coin_active_low": "1" if coin_config.coin_active_low else "0",
            "coin_edge_debounce_ms": str(coin_config.coin_edge_debounce_ms),
            "coin_debounce_ms": str(coin_config.coin_debounce_ms),
            "coin_pulses": coin_config.coin_pulses,
        }
        for key, value in coin_input_defaults.items():
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        tier_count = connection.execute("SELECT COUNT(*) AS total FROM speed_tiers").fetchone()["total"]
        if tier_count == 0:
            profiles = hardware.router_adapter.config.rate_profile_map()
            profiles.setdefault("2/1", hardware.router_adapter.config.router_default_profile)
            for key, profile in profiles.items():
                try:
                    download, upload = (float(value) for value in key.split("/", 1))
                except (TypeError, ValueError):
                    continue
                if download <= 0 or upload <= 0 or not str(profile).strip():
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO speed_tiers (download_limit_mbps, upload_limit_mbps, router_profile) VALUES (?, ?, ?)",
                    (download, upload, str(profile).strip()),
                )
        connection.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("admin")),
        )
        plan_count = connection.execute("SELECT COUNT(*) AS total FROM plans").fetchone()["total"]
        if plan_count == 0:
            connection.executemany(
                "INSERT INTO plans (name, denomination, minutes, bonus_minutes, cap_type) VALUES (?, ?, ?, ?, ?)",
                [("Piso 1", 1, 5, 0, "time"), ("Piso 5", 5, 30, 0, "time"), ("Piso 10 Promo", 10, 180, 30, "time")],
            )


def get_minutes_per_peso():
    with get_db() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'minutes_per_peso'"
        ).fetchone()
    return int(row["value"])


def setting(key, default=""):
    with get_db() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def coin_input_settings():
    config = hardware.HardwareConfig()
    try:
        pulse_map = json.loads(setting("coin_pulses", config.coin_pulses))
        if not isinstance(pulse_map, dict):
            raise ValueError("pulse map must be an object")
    except (TypeError, ValueError, json.JSONDecodeError):
        pulse_map = json.loads(config.coin_pulses)
    return {
        "gpio": int(setting("coin_gpio", str(config.coin_gpio))),
        "pull_up": setting("coin_pull_up", "1" if config.coin_pull_up else "0") == "1",
        "active_low": setting("coin_active_low", "1" if config.coin_active_low else "0") == "1",
        "edge_debounce_ms": int(setting("coin_edge_debounce_ms", str(config.coin_edge_debounce_ms))),
        "burst_quiet_ms": int(setting("coin_debounce_ms", str(config.coin_debounce_ms))),
        "pulse_map": pulse_map,
    }


def configured_coin_input():
    config = hardware.HardwareConfig()
    values = coin_input_settings()
    return replace(
        config,
        coin_gpio=values["gpio"],
        coin_pull_up=values["pull_up"],
        coin_active_low=values["active_low"],
        coin_edge_debounce_ms=values["edge_debounce_ms"],
        coin_debounce_ms=values["burst_quiet_ms"],
        coin_pulses=json.dumps(values["pulse_map"]),
    )


def validate_coin_input_settings(payload):
    if not isinstance(payload, dict):
        return None, "Coin input settings must be an object."
    try:
        gpio = int(payload.get("gpio", hardware.HardwareConfig().coin_gpio))
        edge_ms = int(payload["edge_debounce_ms"])
        burst_ms = int(payload["burst_quiet_ms"])
        pulse_map = payload["pulse_map"]
        if isinstance(pulse_map, str):
            pulse_map = json.loads(pulse_map)
        if not isinstance(pulse_map, dict) or not pulse_map:
            raise ValueError
        normalized = {}
        for pulses, amount in pulse_map.items():
            pulse_count = int(pulses)
            coin_amount = int(amount)
            if pulse_count < 1 or pulse_count > 100 or coin_amount not in COIN_DENOMINATIONS:
                raise ValueError
            normalized[str(pulse_count)] = coin_amount
        if len(normalized) != len(pulse_map):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, "Enter a valid pulse map, edge debounce, and burst quiet-time."
    if gpio < 0 or gpio > 27:
        return None, "GPIO must be a BCM pin from 0 to 27."
    if edge_ms < 0 or edge_ms > 50:
        return None, "Edge debounce must be between 0 and 50 ms."
    if burst_ms < 20 or burst_ms > 1000:
        return None, "Burst quiet-time must be between 20 and 1000 ms."
    return {
        "gpio": gpio,
        "pull_up": bool(payload.get("pull_up")),
        "active_low": bool(payload.get("active_low")),
        "edge_debounce_ms": edge_ms,
        "burst_quiet_ms": burst_ms,
        "pulse_map": normalized,
    }, None


COIN_DENOMINATIONS = (1, 5, 10, 20)


def voucher_speed_tiers():
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM speed_tiers WHERE active = 1 ORDER BY download_limit_mbps, upload_limit_mbps"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "key": f"{row['download_limit_mbps']:g}/{row['upload_limit_mbps']:g}",
            "download": row["download_limit_mbps"],
            "upload": row["upload_limit_mbps"],
            "profile": row["router_profile"],
            "label": f"{row['download_limit_mbps']:g}/{row['upload_limit_mbps']:g} Mbps down/up - {row['router_profile']}",
        }
        for row in rows
    ]


def router_profile_for_speed(download_mbps, upload_mbps):
    with get_db() as connection:
        row = connection.execute(
            "SELECT router_profile FROM speed_tiers WHERE active = 1 AND download_limit_mbps = ? AND upload_limit_mbps = ?",
            (download_mbps, upload_mbps),
        ).fetchone()
    if row:
        return row["router_profile"]
    return hardware.router_adapter.rate_profile(download_mbps, upload_mbps)


def parse_speed_tier(payload):
    if not isinstance(payload, dict):
        return None, "Speed tier data must be an object."
    try:
        download = float(payload.get("download_limit_mbps"))
        upload = float(payload.get("upload_limit_mbps"))
    except (TypeError, ValueError):
        return None, "Enter valid download and upload speeds."
    profile = str(payload.get("router_profile", "")).strip()[:64]
    if not math.isfinite(download) or not math.isfinite(upload) or not (0 < download <= 1000) or not (0 < upload <= 1000):
        return None, "Speeds must be greater than 0 and no more than 1000 Mbps."
    if not profile:
        return None, "Enter an existing RouterOS HotSpot user profile name."
    if profile.lower() in {"default", "default-trial"}:
        return None, "Use a dedicated RouterOS profile name, not a built-in profile."
    return {"download": download, "upload": upload, "profile": profile}, None


def push_speed_tier_to_router(tier):
    try:
        return hardware.router_adapter.sync_hotspot_user_profile(
            tier["profile"], tier["download"], tier["upload"]
        ), None
    except Exception as error:
        write_log("router", f"Could not sync speed tier {tier['profile']} to RouterOS: {error}", "warning")
        return None, str(error)

# #clients view: a device quiet for this long is flagged as idle so abandoned sessions stand out.
SESSION_IDLE_SECONDS = 300
SESSION_FINISHED_STATUSES = ("expired", "kicked", "ended")
SESSION_STATUS_LABELS = {
    "active": "Active",
    "paused": "Paused",
    "expired": "Expired",
    "kicked": "Kicked",
    "ended": "Ended",
}
# Quick "+N" buttons on every #clients row.
EXTEND_CHOICES = (5, 30, 60)


def coin_request_timeout_minutes():
    """Minutes an idle coin request is held open before it is released.

    This is the inactivity countdown the client sees, and it is deliberately a
    *different* setting from ``coin_auto_settle_seconds``: auto-settle is the
    short quiet window after a coin that settles the total automatically, while
    this is the long backstop that stops an abandoned request from holding the
    single coin acceptor forever. Changing auto-settle does not move this.
    """
    try:
        minutes = int(setting("coin_request_timeout_minutes", "10"))
    except (TypeError, ValueError):
        minutes = 10
    return min(max(minutes, 1), 1440)


def coin_request_timeout_seconds():
    return coin_request_timeout_minutes() * 60


def auto_settle_seconds():
    """Seconds of quiet after the last coin before the total settles itself. 0 keeps the operator in charge."""
    try:
        seconds = int(setting("coin_auto_settle_seconds", "5"))
    except (TypeError, ValueError):
        seconds = 5
    return min(max(seconds, 0), 300)


def expire_stale_coin_requests():
    """Release the coin acceptor when a client walks away from an open request.

    Coins that are already in the box are never voided: a stale request that holds coins is settled
    onto that device, and only an empty request is simply expired.
    """
    cutoff = (datetime.now() - timedelta(seconds=coin_request_timeout_seconds())).isoformat(timespec="seconds")
    with get_db() as connection:
        stale = connection.execute(
            "SELECT id, coin_count FROM coin_requests WHERE status = 'pending' AND COALESCE(last_activity_at, last_coin_at, created_at) < ?",
            (cutoff,),
        ).fetchall()
    expired = []
    for row in stale:
        if row["coin_count"] >= 1 and complete_coin_request(row["id"], source="coin-request-timeout"):
            continue
        expired.append(row["id"])
    if expired:
        with get_db() as connection:
            connection.executemany(
                "UPDATE coin_requests SET status = 'expired' WHERE id = ? AND status = 'pending'",
                [(request_id,) for request_id in expired],
            )
    return expired


def pending_coin_request(device_id=None):
    """Return the single open coin request, either globally or for one client device."""
    expire_stale_coin_requests()
    with get_db() as connection:
        if device_id:
            return connection.execute(
                "SELECT * FROM coin_requests WHERE device_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        return connection.execute(
            "SELECT * FROM coin_requests WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        ).fetchone()


def coin_request_payload(row):
    """Serialise a request. Minutes always describe the best payout for the running total."""
    reward = best_coin_allocation(row["amount"])
    activity_text = row["last_activity_at"] or row["last_coin_at"] or row["created_at"]
    activity_at = datetime.fromisoformat(activity_text)
    seconds_remaining = max(
        0,
        coin_request_timeout_seconds() - int((datetime.now() - activity_at).total_seconds()),
    )
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "amount": row["amount"],
        "minutes": reward["minutes"],
        "coin_count": row["coin_count"],
        "created_at": row["created_at"],
        "last_coin_at": row["last_coin_at"],
        "seconds_remaining": seconds_remaining,
        "reward": reward,
    }


def best_plan_rates(rewards=None):
    """The operator's price list: one rate per coin size, straight from the ``#pricing`` rows.

    Plans may share a denomination; the best paying one becomes the rate for that amount,
    which is exactly the row the portal advertises for the coin.
    """
    steps = time_capped_plan_rewards() if rewards is None else rewards
    rates = {}
    for step in steps:
        current = rates.get(step["pesos"])
        if current is None or step["minutes"] > current["minutes"]:
            rates[step["pesos"]] = step
    return rates


def coin_options():
    """Chips, simulator buttons and the pricing panel: what one coin of each denomination pays.

    Every number is read off the ``#pricing`` rows: the plan the operator listed for that coin,
    or the same mix the settlement applies when no row matches the amount (with
    ``minutes_per_peso`` covering whatever no row can). ``hint`` flags a coin whose row pays
    less than the standard rate, or less than repeating a smaller row, so the pricing panel can
    show the operator where the price list prices a coin below what the same money could buy.
    """
    rate = max(0, int(get_minutes_per_peso()))
    rewards = time_capped_plan_rewards()
    table = best_plan_rates(rewards)
    options = []
    for amount in COIN_DENOMINATIONS:
        pesos = max(0, int(round(amount)))
        reward = best_coin_allocation(pesos, rewards)
        plan = table.get(pesos)
        minutes = reward["minutes"]
        rate_minutes = pesos * rate
        menu_minutes = rate_minutes
        menu_label = f"the {rate} min/peso standard rate" if rate else None
        for size, step in table.items():
            if size >= pesos:
                continue
            count = pesos // size
            candidate = count * step["minutes"]
            if candidate > menu_minutes:
                menu_minutes = candidate
                menu_label = f"{count} x {step['name']}"
        hint = None
        if menu_minutes > minutes:
            hint = (
                f"P{pesos} pays {minutes} min here, but {menu_label} would pay "
                f"{menu_minutes} min for the same money."
            )
        options.append(
            {
                "amount": amount,
                "minutes": minutes,
                "plan": plan["name"] if plan else None,
                "source": plan["name"] if plan else reward["summary"],
                "rate_minutes": rate_minutes,
                "menu_minutes": menu_minutes,
                "hint": hint,
            }
        )
    return options


def write_log(category, message, level="info"):
    with get_db() as connection:
        connection.execute(
            "INSERT INTO system_logs (level, category, message, created_at) VALUES (?, ?, ?, ?)",
            (level, category, message, now_text()),
        )


def admin_required(view):
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


def admin_api_required(view):
    """Admin guard for fetch() endpoints so expired consoles get JSON, not a redirect."""

    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return jsonify({"error": "Admin session expired. Sign in again."}), 401
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


def system_metrics():
    if psutil:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        temperature = "n/a"
        try:
            readings = psutil.sensors_temperatures()
            first = next(iter(readings.values()), [])
            if first:
                temperature = f"{first[0].current:.1f} C"
        except (AttributeError, OSError):
            pass
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_free_mb": round(memory.available / 1024 / 1024),
            "storage_free_gb": round(disk.free / 1024 / 1024 / 1024, 1),
            "temperature": temperature,
        }
    return {"cpu_percent": 0, "memory_free_mb": 0, "storage_free_gb": 0, "temperature": "n/a"}


MAC_PATTERN = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)


def is_device_mac(value):
    """True when a key is a real hardware address, i.e. something a router can authorize."""
    return bool(value) and bool(MAC_PATTERN.match(str(value).strip()))


def resolve_hotspot_mac(ip_address):
    """The MAC the hotspot reports for a client's address, or None.

    Real hardware keys sessions on this, because the MAC is what RouterOS
    authorizes. A router hiccup returns None rather than blocking the page, so a
    customer can never lose a coin because the console had a bad moment.
    """
    if not hardware.real_mode() or not ip_address:
        return None
    try:
        return router_adapter.mac_for_ip(ip_address)
    except Exception as error:
        write_log("router", f"Could not resolve a hotspot MAC for {ip_address}: {error}", "warning")
        return None


def portal_device_id():
    """The key a session is stored under: the client's hotspot MAC, or a guest cookie.

    On real hardware every device that reaches the portal through the hotspot is
    named by its MAC, which is exactly what gets authorized on the router later.
    In simulation (and for a device the router has not reported yet) the signed
    session cookie stands in, so the portal still works before the hardware is
    configured.
    """
    if not session.get("portal_device_id"):
        session["portal_device_id"] = resolve_hotspot_mac(request.remote_addr) or f"guest-{secrets.token_hex(3)}"
    return session["portal_device_id"]


def portal_session():
    session_id = session.get("portal_session_id")
    current = None
    with get_db() as connection:
        if session_id:
            current = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not current or current["status"] not in {"active", "paused"}:
            session.pop("portal_session_id", None)
            current = connection.execute(
                "SELECT * FROM sessions WHERE mac_address = ? AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1",
                (portal_device_id(),),
            ).fetchone()
        if current and current["status"] in {"active", "paused"}:
            session["portal_session_id"] = current["id"]
    if not current or current["status"] not in {"active", "paused"}:
        return None
    if current["expires_at"] and datetime.fromisoformat(current["expires_at"]) <= datetime.now():
        with get_db() as connection:
            connection.execute("UPDATE sessions SET status = 'expired', last_seen = ? WHERE id = ?", (now_text(), current["id"]))
        session.pop("portal_session_id", None)
        return None
    return current


def session_remaining_seconds(current):
    if not current:
        return 0
    if current["expires_at"]:
        return max(0, int((datetime.fromisoformat(current["expires_at"]) - datetime.now()).total_seconds()))
    return max(0, current["minutes_left"] * 60)


def human_size_mb(megabytes):
    """Traffic the way an operator reads it: KB under a megabyte, GB once it passes 1024 MB."""
    value = max(0.0, float(megabytes or 0))
    if value < 1:
        return f"{round(value * 1024)} KB"
    if value < 1024:
        return f"{value:.1f} MB"
    return f"{value / 1024:.2f} GB"


def moment_label(value, now=None):
    """'14:02' for something that happened today, '27 Sep 14:02' for older sessions."""
    if not value:
        return "unknown"
    when = datetime.fromisoformat(value)
    now = now or datetime.now()
    return when.strftime("%H:%M") if when.date() == now.date() else when.strftime("%d %b %H:%M")


def ago_label(value, now=None):
    """How long the console has been quiet about a device: 'just now', '3 min ago', '2 h ago'."""
    if not value:
        return "never seen"
    when = datetime.fromisoformat(value)
    now = now or datetime.now()
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def source_kind(source):
    """Which chip a #clients row shows for the money a device spent."""
    text = (source or "").lower()
    if "voucher" in text:
        return "voucher"
    if "coin" in text or "simulat" in text or "gpio" in text:
        return "coin"
    return "admin"


def merged_source(existing, addition, limit=90):
    """Keep one device's credit history readable: newest last, duplicates ignored, length capped."""
    history = [part.strip() for part in (existing or "").split(" + ") if part.strip()]
    addition = (addition or "").strip()
    if addition and addition not in history:
        history.append(addition)
    while len(history) > 1 and len(" + ".join(history)) > limit:
        history.pop(0)
    return " + ".join(history)[-limit:]


def expire_finished_sessions():
    """Flip active rows whose clock ran out to ``expired``, and cut them off the hotspot.

    On real hardware a finished session must also lose its HotSpot user, otherwise
    the router would happily keep the device online on the last limit it was given.
    """
    stamp = now_text()
    with get_db() as connection:
        finishing = connection.execute(
            "SELECT mac_address FROM sessions WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
            (stamp,),
        ).fetchall()
        cursor = connection.execute(
            "UPDATE sessions SET status = 'expired' WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
            (stamp,),
        )
        expired = cursor.rowcount
    if expired:
        for row in finishing:
            router_release(row["mac_address"], "paid time ran out")
        write_log("session", f"{expired} client session(s) ran out of time and are now expired")
    return expired


def session_payload(row, now=None):
    """What the #clients table shows for one device, straight off its session row.

    ``minutes_left`` is the number the client is really counting down: an active row reads its
    ``expires_at``, a paused row shows the minutes it banked, and a finished row shows zero. Granted
    minutes turn that into the used-time bar, ``source`` says what the time was paid with, and the
    idle/attention flags point the operator at the devices that need a look.
    """
    now = now or datetime.now()
    status = row["status"] if row["status"] in SESSION_STATUS_LABELS else "active"
    finished = status in SESSION_FINISHED_STATUSES
    left_seconds = session_remaining_seconds(row) if status == "active" else max(0, int(row["minutes_left"] or 0)) * 60
    left_minutes = 0 if finished else (left_seconds + 59) // 60
    granted = max(0, int(row["granted_minutes"] or 0))
    if granted <= 0:
        # Rows created before the granted column existed: treat what is left as the whole grant.
        granted = max(0, int(row["minutes_left"] or 0))
    granted = max(granted, left_minutes)
    seconds_since_seen = None
    if row["last_seen"]:
        seconds_since_seen = max(0, int((now - datetime.fromisoformat(row["last_seen"])).total_seconds()))
    idle = status == "active" and seconds_since_seen is not None and seconds_since_seen >= SESSION_IDLE_SECONDS
    attention = not finished and (idle or (status == "active" and left_minutes <= 5))
    if finished:
        urgency = "done"
    elif status == "paused":
        urgency = "banked"
    elif left_minutes <= 5:
        urgency = "critical"
    elif left_minutes <= 15:
        urgency = "low"
    else:
        urgency = "ok"
    expiry = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
    download = float(row["data_down_mb"] or 0)
    upload = float(row["data_up_mb"] or 0)
    label = row["source"] or "Not recorded"
    search_bits = (
        row["mac_address"],
        row["ip_address"],
        label,
        SESSION_STATUS_LABELS[status],
        "attention" if attention else "",
        "idle" if idle else "",
    )
    return {
        "id": row["id"],
        "device": row["mac_address"],
        "ip_address": row["ip_address"] or None,
        "status": status,
        "status_label": SESSION_STATUS_LABELS[status],
        "status_class": {"active": "ok", "paused": "warn"}.get(status, "muted"),
        "finished": finished,
        "source": row["source"] or None,
        "source_kind": source_kind(row["source"]),
        "source_label": label,
        "granted_minutes": granted,
        "minutes_left": left_minutes,
        "progress_percent": round(left_minutes / granted * 100) if granted else None,
        "urgency": urgency,
        "idle": idle,
        "attention": attention,
        "started_at": row["started_at"],
        "started_clock": moment_label(row["started_at"], now),
        "last_seen": row["last_seen"],
        "seen_label": ago_label(row["last_seen"], now),
        "expires_at": row["expires_at"],
        "ends_clock": expiry.strftime("%H:%M") if expiry and not finished else None,
        "remaining_seconds": left_seconds,
        "data_down": human_size_mb(download),
        "data_up": human_size_mb(upload),
        "data_total": human_size_mb(download + upload),
        "data_total_mb": round(download + upload, 2),
        "speed": f"{float(row['download_limit_mbps'] or 0):g} / {float(row['upload_limit_mbps'] or 0):g} Mbps",
        # Live devices first (most urgent clock on top), finished devices last, newest of those first.
        "sort_key": [2, -row["id"]] if finished else [1 if status == "paused" else 0, left_minutes],
        "search": " ".join(str(bit) for bit in search_bits if bit).lower(),
    }


def client_shell():
    """Blank payload behind the #clients row template, so JS-built rows match the server-rendered ones."""
    return {
        "id": 0,
        "device": "",
        "ip_address": None,
        "status": "active",
        "status_label": "",
        "status_class": "ok",
        "finished": False,
        "source": None,
        "source_kind": "admin",
        "source_label": "",
        "granted_minutes": 0,
        "minutes_left": 0,
        "progress_percent": None,
        "urgency": "ok",
        "idle": False,
        "attention": False,
        "started_at": "",
        "started_clock": "",
        "last_seen": "",
        "seen_label": "",
        "expires_at": None,
        "ends_clock": None,
        "remaining_seconds": 0,
        "data_down": "",
        "data_up": "",
        "data_total": "",
        "data_total_mb": 0,
        "speed": "",
        "sort_key": [9, 0],
        "search": "",
    }


def client_rows(now=None):
    """Every device for the #clients view: expired rows are swept first, then the list is ordered."""
    expire_finished_sessions()
    now = now or datetime.now()
    with get_db() as connection:
        rows = connection.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
    clients = [session_payload(row, now) for row in rows]
    clients.sort(key=lambda client: client["sort_key"])
    return clients


def client_summary(clients):
    """The four numbers above the #clients table."""
    active = [client for client in clients if client["status"] == "active"]
    paused = [client for client in clients if client["status"] == "paused"]
    finished = [client for client in clients if client["finished"]]
    traffic_mb = round(sum(client["data_total_mb"] for client in clients), 2)
    return {
        "total": len(clients),
        "active": len(active),
        "paused": len(paused),
        "finished": len(finished),
        "idle": sum(1 for client in clients if client["idle"]),
        "attention": sum(1 for client in clients if client["attention"]),
        "minutes_left": sum(client["minutes_left"] for client in active + paused),
        "traffic_mb": traffic_mb,
        "traffic": human_size_mb(traffic_mb),
    }


@app.get("/hotspot/login.html")
def hotspot_login_page():
    """The page the router shows unpaid clients: it hands them straight to the portal.

    Install it on the router once (router/hap-ax-lite-piso.rsc, step 12):
        /tool fetch url="http://<controller>:5000/hotspot/login.html" dst-path=hotspot/login.html
    RouterOS keeps the file as hotspot/login.html and serves it for every blocked
    request, so a customer never meets the RouterOS login form.
    """
    page = render_template("hotspot-login.html", ssid=setting("ssid", "PisoPilot WiFi"), portal_url=url_for("portal", _external=True))
    return page, 200, {"Cache-Control": "no-store"}


@app.get("/hotspot/api.json")
def hotspot_api_json():
    """RFC 8910 captive-portal descriptor, fetched by a phone over option 114.

    Install it on the router the same way as the login page:
        /tool fetch url="http://<controller>:5000/hotspot/api.json" dst-path=hotspot/api.json

    RouterOS advertises the URL of this file in DHCP option 114, so a phone that
    supports the option fetches it and opens portal_uri directly. Phones that
    cannot get past the certificate check fall back to the plain http:// probe,
    which the login page below already handles.
    """
    body = jsonify(
        {
            "version": 1,
            "portal_uri": url_for("portal", _external=True),
            "api_version": 1,
        }
    )
    return body, 200, {"Cache-Control": "no-store"}


@app.get("/portal")
def portal():
    current = portal_session()
    device_id = portal_device_id()
    holder = pending_coin_request()
    pending_coin = holder if holder is not None and holder["device_id"] == device_id else None
    acceptor_busy = holder is not None and pending_coin is None
    pending_reward = best_coin_allocation(pending_coin["amount"]) if pending_coin is not None else None
    with get_db() as connection:
        plans = connection.execute("SELECT * FROM plans WHERE active = 1 ORDER BY denomination").fetchall()
    options = coin_options()
    return render_template(
        "portal.html",
        ssid=setting("ssid", "PisoPilot WiFi"),
        plans=plans,
        current=current,
        remaining_seconds=session_remaining_seconds(current),
        error=request.args.get("error"),
        pending_coin=pending_coin,
        pending_reward=pending_reward,
        acceptor_busy=acceptor_busy,
        internet=internet_status(allow_probe=False),
        minutes_per_peso=get_minutes_per_peso(),
        coin_options=options,
        p1_rate=next((option for option in options if option["amount"] == 1), None),
        auto_settle_seconds=auto_settle_seconds(),
        # The modal reads this for its inactivity countdown. It was never passed,
        # so data-client-timeout rendered empty and the client silently fell back
        # to its own hard-coded default instead of the operator's setting.
        coin_request_timeout_minutes=coin_request_timeout_minutes(),
    )


def portal_actor(mac_address=None):
    return mac_address or portal_device_id()


def voucher_attempt_limited(actor):
    window_start = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    with get_db() as connection:
        attempts = connection.execute(
            "SELECT COUNT(*) AS total FROM voucher_attempts WHERE actor = ? AND succeeded = 0 AND created_at >= ?",
            (actor, window_start),
        ).fetchone()["total"]
    return attempts >= 5


def record_voucher_attempt(actor, succeeded):
    with get_db() as connection:
        connection.execute(
            "INSERT INTO voucher_attempts (actor, succeeded, created_at) VALUES (?, ?, ?)",
            (actor, int(succeeded), now_text()),
        )
        if succeeded:
            connection.execute("DELETE FROM voucher_attempts WHERE actor = ?", (actor,))


def activate_client_session(minutes, source, mac_address=None, label=None, download_limit_mbps=2, upload_limit_mbps=1, router_profile=None):
    """Credit minutes to the portal device, stacking onto its live session when it already has one.

    ``source`` stays the audit word the logs already use; ``label`` is the human line the #clients
    table shows for what the time was paid with.
    """
    started = datetime.now()
    device = portal_actor(mac_address)
    credit = label or source
    current = portal_session()
    if not current and mac_address:
        with get_db() as connection:
            current = connection.execute(
                "SELECT * FROM sessions WHERE mac_address = ? AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1",
                (mac_address,),
            ).fetchone()
    if current:
        old_expiry = datetime.fromisoformat(current["expires_at"]) if current["expires_at"] else started
        expires_at = max(old_expiry, started) + timedelta(minutes=minutes)
        remaining_minutes = max(1, int((expires_at - started).total_seconds() // 60))
        with get_db() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET minutes_left = ?, granted_minutes = granted_minutes + ?, status = 'active',
                    last_seen = ?, expires_at = ?, source = ?,
                    download_limit_mbps = ?, upload_limit_mbps = ?, router_profile = ?
                WHERE id = ?
                """,
                (
                    remaining_minutes,
                    minutes,
                    now_text(),
                    expires_at.isoformat(timespec="seconds"),
                    merged_source(current["source"], credit),
                    download_limit_mbps,
                    upload_limit_mbps,
                    router_profile or "",
                    current["id"],
                ),
            )
        session["portal_session_id"] = current["id"]
        sync_session_to_router(current["id"])
        write_log("portal", f"Stacked {minutes} minutes onto session {current['id']} via {source}")
        return current["id"]
    with get_db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO sessions (mac_address, ip_address, minutes_left, status, started_at, last_seen, expires_at, source, granted_minutes, download_limit_mbps, upload_limit_mbps, router_profile)
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device,
                request.remote_addr,
                minutes,
                started.isoformat(timespec="seconds"),
                now_text(),
                (started + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
                merged_source(None, credit),
                minutes,
                download_limit_mbps,
                upload_limit_mbps,
                router_profile or "",
            ),
        )
        session_id = cursor.lastrowid
    session["portal_session_id"] = session_id
    sync_session_to_router(session_id)
    write_log("portal", f"Started {minutes}-minute client session via {source}")


def activate_device_session(minutes, source, device_id, ip_address=None, label=None):
    """Credit a settled coin total to the device that held the request, stacking onto its session."""
    started = datetime.now()
    credit = label or source
    with get_db() as connection:
        current = connection.execute(
            "SELECT * FROM sessions WHERE mac_address = ? AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        if current:
            old_expiry = datetime.fromisoformat(current["expires_at"]) if current["expires_at"] else started
            expires_at = max(old_expiry, started) + timedelta(minutes=minutes)
            remaining_minutes = max(1, int((expires_at - started).total_seconds() // 60))
            connection.execute(
                """
                UPDATE sessions
                SET minutes_left = ?, granted_minutes = granted_minutes + ?, status = 'active',
                    last_seen = ?, expires_at = ?, source = ?
                WHERE id = ?
                """,
                (
                    remaining_minutes,
                    minutes,
                    now_text(),
                    expires_at.isoformat(timespec="seconds"),
                    merged_source(current["source"], credit),
                    current["id"],
                ),
            )
            session_id = current["id"]
        else:
            cursor = connection.execute(
                """
                INSERT INTO sessions (mac_address, ip_address, minutes_left, status, started_at, last_seen, expires_at, source, granted_minutes)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    ip_address,
                    minutes,
                    started.isoformat(timespec="seconds"),
                    now_text(),
                    (started + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
                    merged_source(None, credit),
                    minutes,
                ),
            )
            session_id = cursor.lastrowid
    sync_session_to_router(session_id)
    write_log("coin", f"Accepted {minutes} minutes for device {device_id} via {source}")
    return session_id


def router_authorize(device, minutes, download_mbps=None, upload_mbps=None, profile=None):
    """Push paid time to the hotspot. Simulation, or a key without a MAC, is a no-op."""
    if not hardware.real_mode():
        return None
    if not is_device_mac(device):
        write_log(
            "router",
            f"No hotspot MAC for {device}: the {minutes} paid minute(s) stay in the console. "
            "Check that the client reaches /portal through the hotspot.",
            "warning",
        )
        return None
    try:
        chosen_profile = profile or router_profile_for_speed(download_mbps, upload_mbps)
        result = router_adapter.authorize(device, minutes, download_mbps=download_mbps, upload_mbps=upload_mbps, profile=chosen_profile)
    except Exception as error:  # a router hiccup must never block a settlement
        write_log("router", f"Could not authorize {device} on the hotspot: {error}", "warning")
        return None
    reauthentication = result.get("reauthentication", "not-needed")
    level = "warning" if reauthentication == "pending" else "info"
    write_log(
        "router",
        f"Hotspot access for {device}: {result.get('status')} at {minutes} minute(s) on "
        f"{result.get('profile', 'the default profile')}; reauthentication: {reauthentication}",
        level,
    )
    return result


def router_release(device, reason):
    """Take a device offline: drop the live session and delete its hotspot user."""
    if not hardware.real_mode() or not is_device_mac(device):
        return None
    try:
        result = router_adapter.revoke(device)
    except Exception as error:
        write_log("router", f"Could not revoke {device} on the hotspot: {error}", "warning")
        return None
    write_log("router", f"Hotspot access for {device} revoked ({reason}): {result.get('status')}")
    return result


def sync_session_to_router(session_id):
    """Mirror one session row onto the router: an active row becomes a hotspot user.

    Every credit path ends here, so paying again simply rewrites the same limit
    with the session's real remaining minutes, and a row that is no longer active
    loses its access instead.
    """
    with get_db() as connection:
        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    device = row["mac_address"]
    if row["status"] != "active":
        return router_release(device, row["status"])
    minutes = max(1, (session_remaining_seconds(row) + 59) // 60)
    return router_authorize(
        device,
        minutes,
        row["download_limit_mbps"],
        row["upload_limit_mbps"],
        profile=row["router_profile"] or None,
    )


@app.post("/portal/redeem")
def portal_redeem():
    code = request.form.get("code", "").strip().upper()
    mac_address = request.form.get("mac_address", "").strip() or None
    actor = portal_actor(mac_address)
    if voucher_attempt_limited(actor):
        return redirect(url_for("portal", error="Too many attempts. Please wait 10 minutes before trying again."))
    with get_db() as connection:
        voucher = connection.execute("SELECT * FROM vouchers WHERE code = ?", (code,)).fetchone()
    expired = voucher and voucher["expires_at"] and datetime.fromisoformat(voucher["expires_at"]) < datetime.now()
    if not voucher or voucher["status"] != "unused" or expired:
        record_voucher_attempt(actor, False)
        return redirect(url_for("portal", error="That voucher is invalid, already used, or expired."))
    with get_db() as connection:
        connection.execute(
            "UPDATE vouchers SET status = 'used', mac_address = ?, used_at = ? WHERE id = ?",
            (mac_address or portal_device_id(), now_text(), voucher["id"]),
        )
    record_voucher_attempt(actor, True)
    activate_client_session(
        voucher["minutes"],
        "voucher",
        mac_address,
        label=f"Voucher {voucher['code']}",
        download_limit_mbps=voucher["download_limit_mbps"],
        upload_limit_mbps=voucher["upload_limit_mbps"],
        router_profile=voucher["router_profile"],
    )
    return redirect(url_for("portal"))


@app.post("/portal/request-coin")
def request_coin():
    device_id = portal_device_id()
    existing = pending_coin_request()
    if existing is not None:
        if existing["device_id"] == device_id:
            if request.is_json:
                return jsonify({**coin_request_payload(existing), "status": "pending"})
            return redirect(url_for("portal"))
        if request.is_json:
            return jsonify({"error": "Another client is currently inserting coins. Please wait."}), 409
        return redirect(url_for("portal", error="Another client is currently inserting coins. Please wait."))
    created_at = now_text()
    with get_db() as connection:
        connection.execute(
            "INSERT INTO coin_requests (device_id, ip_address, plan_id, amount, minutes, coin_count, created_at, last_activity_at) VALUES (?, ?, 0, 0, 0, 0, ?, ?)",
            (device_id, request.remote_addr, created_at, created_at),
        )
        opened = connection.execute(
            "SELECT * FROM coin_requests WHERE device_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    write_log("coin", f"Client opened coin insertion request for {device_id}")
    if request.is_json:
        return jsonify({**coin_request_payload(opened), "status": "pending"}), 201
    return redirect(url_for("portal"))


@app.post("/portal/request-coin/cancel")
def cancel_coin_request():
    device_id = portal_device_id()
    pending = pending_coin_request(device_id)
    if pending is not None and pending["coin_count"] >= 1:
        if request.is_json:
            return jsonify({"error": "Coins have already been inserted. Finish the request to receive your credit."}), 409
        return redirect(url_for("portal"))
    if pending is None:
        if request.is_json:
            return jsonify({"error": "There is no open coin request."}), 404
        return redirect(url_for("portal"))
    cancel_auto_settle(pending["id"])
    with get_db() as connection:
        connection.execute("UPDATE coin_requests SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (pending["id"],))
    write_log("coin", f"Client cancelled coin insertion request for {device_id}")
    if request.is_json:
        return jsonify({"status": "cancelled"})
    return redirect(url_for("portal"))


@app.post("/portal/request-coin/extend")
def extend_coin_request():
    device_id = portal_device_id()
    pending = pending_coin_request(device_id)
    if pending is None:
        return jsonify({"error": "There is no open coin request to extend."}), 404
    activity_at = now_text()
    with get_db() as connection:
        connection.execute(
            "UPDATE coin_requests SET last_activity_at = ? WHERE id = ? AND status = 'pending'",
            (activity_at, pending["id"]),
        )
        refreshed = connection.execute("SELECT * FROM coin_requests WHERE id = ?", (pending["id"],)).fetchone()
    write_log("coin", f"Client extended coin insertion request {pending['id']} for {device_id}")
    return jsonify({**coin_request_payload(refreshed), "status": "pending"})


@app.post("/portal/request-coin/complete")
def complete_client_coin_request():
    device_id = portal_device_id()
    pending = pending_coin_request(device_id)
    if pending is None:
        return jsonify({"error": "There is no open coin request."}), 404
    if pending["coin_count"] < 1:
        return jsonify({"error": "Insert at least one coin before finishing."}), 409
    settled = complete_coin_request(pending["id"], source="client-coin-finish")
    if settled is None:
        return jsonify({"error": "This coin request has already closed."}), 409
    return jsonify({"status": "accepted", "session_active": portal_session() is not None})


def time_capped_plan_rewards():
    """Active time-capped plans as the coin acceptor's price list rows.

    Data-capped plans are skipped: they carry a megabyte limit instead of minutes, so
    they cannot be credited by the coin slot. Denominations that are not a whole peso
    are skipped too, because coins always land in whole pesos.
    """
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT name, denomination, minutes, bonus_minutes FROM plans
            WHERE active = 1 AND cap_type = 'time'
            ORDER BY denomination ASC, minutes DESC
            """
        ).fetchall()
    rewards = []
    for row in rows:
        denomination = float(row["denomination"] or 0)
        pesos = int(denomination)
        minutes = int(row["minutes"] or 0) + int(row["bonus_minutes"] or 0)
        if pesos >= 1 and abs(denomination - pesos) < 0.001 and minutes > 0:
            rewards.append({"name": row["name"], "pesos": pesos, "minutes": minutes})
    return rewards


def best_coin_allocation(amount, rewards=None):
    """Settle one payment at the operator's own price list (the ``#pricing`` rows).

    Coins land one at a time, but the client is settled on the whole amount, and that amount is
    paid at the price list the operator keeps at ``#pricing``: the rows are applied from the
    largest denomination down (when several plans share a denomination, the best paying one
    wins), so a P20 total is paid by the P20 row and a P15 total by the P10 and P5 rows. Pesos
    that no row covers still earn ``minutes_per_peso``. Nothing is invented on top of the list,
    which is what keeps the portal chips, the dashboard simulator, the pricing panel and the
    credited minutes equal to the prices the operator advertises.

    Returns the total minutes, the plan steps that were applied, the remainder that fell back
    to the rate, and a summary both consoles can show.
    """
    total = max(0, int(round(float(amount or 0))))
    rate = max(0, int(get_minutes_per_peso()))
    table = best_plan_rates(rewards)
    remaining = total
    applied = []
    for size in sorted(table, reverse=True):
        step = table[size]
        count, remaining = divmod(remaining, size)
        if count <= 0:
            continue
        applied.append(
            {
                "name": step["name"],
                "denomination": size,
                "minutes": step["minutes"],
                "count": count,
                "total_minutes": step["minutes"] * count,
            }
        )
    parts = [
        f"{step['name']} x{step['count']}" if step["count"] > 1 else step["name"] for step in applied
    ]
    if remaining:
        parts.append(f"P{remaining} at the standard rate")
    plan_minutes = sum(step["total_minutes"] for step in applied)
    return {
        "amount": total,
        "minutes": plan_minutes + remaining * rate,
        "rate": rate,
        "rate_minutes": total * rate,
        "bonus_minutes": max(0, plan_minutes + remaining * rate - total * rate),
        "plans": applied,
        "remainder_pesos": remaining,
        "remainder_minutes": remaining * rate,
        "summary": " + ".join(parts) if parts else "no coins inserted yet",
    }


def coin_credit(amount, rewards=None):
    """Minutes a single coin is worth when it is the whole payment."""
    return best_coin_allocation(amount, rewards)["minutes"]


def accumulate_coin(request_id, amount):
    """Add one coin to an open request and re-read the best payout for the new total.

    The increment and the re-read share one transaction, so coins landing together (a GPIO pulse
    while the operator clicks, or two pulses in flight) both count instead of overwriting each other.
    Returns the updated row, or None when the request is no longer open.
    """
    activity_at = now_text()
    with get_db() as connection:
        connection.execute(
            """
            UPDATE coin_requests
            SET amount = amount + ?, coin_count = coin_count + 1,
                last_coin_at = ?, last_activity_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (amount, activity_at, activity_at, request_id),
        )
        row = connection.execute("SELECT * FROM coin_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None or row["status"] != "pending":
            return None
        connection.execute(
            "UPDATE coin_requests SET minutes = ? WHERE id = ?",
            (best_coin_allocation(row["amount"])["minutes"], request_id),
        )
        row = connection.execute("SELECT * FROM coin_requests WHERE id = ?", (request_id,)).fetchone()
    return row


_auto_settle_lock = threading.Lock()
_auto_settle_timers = {}


def cancel_auto_settle(request_id):
    """Drop the quiet-window settle armed for a request (operator finished, client cancelled, ...)."""
    with _auto_settle_lock:
        timer = _auto_settle_timers.pop(request_id, None)
    if timer is not None:
        timer.cancel()


def arm_auto_settle(request_id):
    """Restart the quiet window: the coin total turns into a real credit without an operator.

    Every coin pushes the window back, so the client is settled once, a few seconds after the last
    coin lands, instead of once per coin.
    """
    seconds = auto_settle_seconds()
    if seconds <= 0:
        return False
    with _auto_settle_lock:
        previous = _auto_settle_timers.pop(request_id, None)
        timer = threading.Timer(seconds, settle_quiet_request, args=(request_id,))
        timer.daemon = True
        _auto_settle_timers[request_id] = timer
    if previous is not None:
        previous.cancel()
    timer.start()
    return True


def settle_quiet_request(request_id):
    """Timer callback: settle the whole coin total once the coins have stopped landing."""
    with _auto_settle_lock:
        _auto_settle_timers.pop(request_id, None)
    complete_coin_request(request_id, source="coin-auto-settle")


def settle_quiet_coin_requests():
    """Startup recovery: a restart drops the live timers, so settle anything already past the quiet window."""
    seconds = auto_settle_seconds()
    if seconds <= 0:
        return []
    cutoff = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with get_db() as connection:
        quiet = connection.execute(
            """
            SELECT id FROM coin_requests
            WHERE status = 'pending' AND coin_count >= 1 AND COALESCE(last_coin_at, created_at) < ?
            """,
            (cutoff,),
        ).fetchall()
    return [
        row["id"]
        for row in quiet
        if complete_coin_request(row["id"], source="coin-auto-settle") is not None
    ]


@app.get("/api/portal/coin-request")
def portal_coin_request_api():
    """Live status the customer portal polls while waiting for the operator."""
    device_id = portal_device_id()
    pending = pending_coin_request(device_id)
    if pending:
        payload = coin_request_payload(pending)
        payload["status"] = "pending"
        return jsonify(payload)
    with get_db() as connection:
        latest = connection.execute(
            "SELECT * FROM coin_requests WHERE device_id = ? ORDER BY id DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    if latest is None:
        return jsonify({"status": "none"})
    payload = coin_request_payload(latest)
    payload["status"] = latest["status"] if latest["status"] in {"accepted", "expired", "cancelled"} else "none"
    if payload["status"] == "accepted":
        payload["session_active"] = portal_session() is not None
    return jsonify(payload)


@app.post("/coin-requests/<int:request_id>/insert")
@admin_required
def insert_simulated_coin(request_id):
    data = request.get_json(silent=True) or request.form
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        amount = None
    if amount not in COIN_DENOMINATIONS:
        return jsonify({"error": "Unsupported coin denomination"}), 400
    expire_stale_coin_requests()
    with get_db() as connection:
        coin_request = connection.execute("SELECT * FROM coin_requests WHERE id = ? AND status = 'pending'", (request_id,)).fetchone()
    if not coin_request:
        return jsonify({"error": "Coin request is no longer pending"}), 409
    # A coin only counts towards the total; what it buys is decided when the request is settled.
    updated = accumulate_coin(request_id, amount)
    if updated is None:
        return jsonify({"error": "Coin request is no longer pending"}), 409
    write_log("coin", f"Admin inserted P{amount:.0f} into request {request_id}")
    payload = coin_request_payload(updated)
    payload["inserted_amount"] = amount
    payload["status"] = updated["status"]
    return jsonify(payload)


def complete_coin_request(request_id, source="admin-simulated-coin"):
    """Settle the whole coin total as one payment at the plan prices it reaches.

    The operator pressing Finish, the quiet-window timer, the walk-away sweep and a cancel that
    already has coins in the box can all land here, so only the first caller to flip the row out
    of ``pending`` credits minutes: everybody else gets None.
    """
    cancel_auto_settle(request_id)
    with get_db() as connection:
        coin_request = connection.execute(
            "SELECT * FROM coin_requests WHERE id = ? AND status = 'pending'", (request_id,)
        ).fetchone()
        if not coin_request or coin_request["coin_count"] < 1:
            return None
        payout = best_coin_allocation(coin_request["amount"])
        cursor = connection.execute(
            "UPDATE coin_requests SET status = 'accepted', accepted_at = ?, minutes = ? WHERE id = ? AND status = 'pending'",
            (now_text(), payout["minutes"], request_id),
        )
        if cursor.rowcount != 1:
            return None
        settled = connection.execute("SELECT * FROM coin_requests WHERE id = ?", (request_id,)).fetchone()
    coin_label = f"Coin P{float(settled['amount']):.0f} · {payout['summary']}"
    activate_device_session(payout["minutes"], source, settled["device_id"], settled["ip_address"], label=coin_label)
    with get_db() as connection:
        connection.execute(
            "INSERT INTO coin_events (amount, minutes, source, created_at) VALUES (?, ?, ?, ?)",
            (settled["amount"], payout["minutes"], source, now_text()),
        )
    write_log(
        "coin",
        f"P{float(settled['amount']):.0f} settled for {settled['device_id']} as {payout['summary']} "
        f"({payout['minutes']} minutes) via {source}",
    )
    return settled


@app.post("/coin-requests/<int:request_id>/complete")
@admin_required
def complete_simulated_coin(request_id):
    coin_request = complete_coin_request(request_id)
    if request.is_json:
        if not coin_request:
            return jsonify({"error": "Insert at least one coin before finishing"}), 409
        payload = coin_request_payload(coin_request)
        payload["status"] = "accepted"
        return jsonify(payload)
    return redirect(url_for("dashboard") + "#clients")


@app.post("/coin-requests/<int:request_id>/accept")
@admin_required
def accept_coin_request(request_id):
    return complete_simulated_coin(request_id)


@app.get("/api/admin/coin-requests")
@admin_api_required
def admin_coin_requests_api():
    """Queue the operator console polls so the simulator opens the moment a client asks."""
    expire_stale_coin_requests()
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM coin_requests WHERE status = 'pending' ORDER BY id ASC"
        ).fetchall()
    requests = [coin_request_payload(row) for row in rows]
    return jsonify(
        {
            "requests": requests,
            "count": len(requests),
            "primary_id": requests[0]["id"] if requests else None,
            "acceptor_busy": bool(requests),
        }
    )


@app.post("/portal/end")
def portal_end():
    current = portal_session()
    if current:
        with get_db() as connection:
            connection.execute("UPDATE sessions SET status = 'ended', last_seen = ? WHERE id = ?", (now_text(), current["id"]))
        write_log("portal", f"Client ended session {current['id']}")
    session.pop("portal_session_id", None)
    return redirect(url_for("portal"))


@app.get("/api/portal/session")
def portal_session_api():
    current = portal_session()
    if not current:
        return jsonify({"active": False})
    return jsonify({"active": True, "status": current["status"], "remaining_seconds": session_remaining_seconds(current), "data_down_mb": current["data_down_mb"], "data_up_mb": current["data_up_mb"]})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as connection:
            admin = connection.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
        if admin and check_password_hash(admin["password_hash"], password):
            session["admin_id"] = admin["id"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        return render_template("login.html", error="Invalid username or password")
    return render_template("login.html", error=None)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@admin_required
def dashboard():
    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")
    expire_stale_coin_requests()
    with get_db() as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS coin_count, COALESCE(SUM(amount), 0) AS revenue,
                   COALESCE(SUM(minutes), 0) AS minutes,
                   COALESCE(SUM(CASE WHEN created_at LIKE ? THEN amount ELSE 0 END), 0) AS today_revenue,
                   COALESCE(SUM(CASE WHEN created_at LIKE ? THEN amount ELSE 0 END), 0) AS month_revenue
            FROM coin_events
            """, (today + "%", month + "%"),
        ).fetchone()
        events = connection.execute(
            "SELECT * FROM coin_events ORDER BY id DESC LIMIT 8"
        ).fetchall()
        coin_requests = connection.execute(
            "SELECT * FROM coin_requests WHERE status = 'pending' ORDER BY id ASC"
        ).fetchall()
        plans = connection.execute("SELECT * FROM plans WHERE active = 1 ORDER BY denomination").fetchall()
        vouchers = connection.execute("SELECT * FROM vouchers ORDER BY id DESC LIMIT 8").fetchall()
        voucher_counts = connection.execute(
            "SELECT status, COUNT(*) AS total FROM vouchers GROUP BY status"
        ).fetchall()
        logs = connection.execute("SELECT * FROM system_logs ORDER BY id DESC LIMIT 8").fetchall()

    # The #clients view and the Active clients metric read the same payload, so they can never disagree.
    clients = client_rows()
    client_totals = client_summary(clients)
    hardware_state = hardware_status()
    uplink = internet_status(allow_probe=False)
    # Named hardware_links, not hardware: a local `hardware` here shadows the
    # `hardware` module for the whole function, so any later `hardware.something`
    # in this view would raise AttributeError on a dict.
    hardware_links = {
        "coin_acceptor": hardware_state["coin_acceptor"],
        "controller": hardware_state["controller"],
        "gateway": hardware_state["gateway"],
        "internet": uplink["state"],
    }
    metrics = system_metrics()

    return render_template(
        "dashboard.html",
        totals=totals,
        events=events,
        minutes_per_peso=get_minutes_per_peso(),
        clients=clients,
        client_summary=client_totals,
        client_shell=client_shell(),
        extend_choices=EXTEND_CHOICES,
        idle_seconds=SESSION_IDLE_SECONDS,
        coin_requests=[coin_request_payload(row) for row in coin_requests],
        coin_options=coin_options(),
        plans=plans,
        vouchers=vouchers,
        voucher_counts={row["status"]: row["total"] for row in voucher_counts},
        logs=logs,
        active_users=client_totals["active"],
        metrics=metrics,
        hardware=hardware_links,
        hardware_mode=hardware_state["mode"],
        coin_acceptor_detail=hardware_state.get("coin_acceptor_detail", ""),
        internet=uplink,
        ssid=setting("ssid", "PisoPilot WiFi"),
        session_mode=setting("session_mode", "time"),
        mac_voucher_mode=setting("mac_voucher_mode", "0"),
        pulse_tolerance=setting("coin_pulse_tolerance_ms", "80"),
        auto_settle_seconds=auto_settle_seconds(),
        coin_request_timeout_minutes=coin_request_timeout_minutes(),
    )


@app.post("/settings/rate")
@admin_required
def update_rate():
    minutes = request.form.get("minutes_per_peso", type=int)
    if minutes is not None and 1 <= minutes <= 1440:
        with get_db() as connection:
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = 'minutes_per_peso'",
                (str(minutes),),
            )
            write_log("pricing", f"Updated default rate to {minutes} minutes per peso")
    return redirect(url_for("dashboard"))


@app.post("/simulate-coin")
@admin_required
def simulate_coin():
    amount = request.form.get("amount", type=float)
    if amount is None or amount <= 0:
        return redirect(url_for("dashboard"))

    reward = best_coin_allocation(amount)
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO coin_events (amount, minutes, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (amount, reward["minutes"], "simulator", now_text()),
        )
    write_log(
        "coin",
        f"Accepted simulated coin worth P{amount:.2f}, issued {reward['minutes']} minutes "
        f"({reward['summary']})",
    )
    return redirect(url_for("dashboard"))


@app.post("/plans")
@admin_required
def create_plan():
    values = plan_form_values()
    if valid_plan(values):
        with get_db() as connection:
            connection.execute(
                "INSERT INTO plans (name, denomination, minutes, bonus_minutes, cap_type, data_limit_mb) VALUES (?, ?, ?, ?, ?, ?)",
                tuple(values.values()),
            )
        write_log("pricing", f"Created plan {values['name']}")
    return redirect(url_for("dashboard") + "#pricing")


def plan_form_values():
    return {
        "name": request.form.get("name", "Custom plan").strip()[:80],
        "denomination": request.form.get("denomination", type=float),
        "minutes": request.form.get("minutes", type=int),
        "bonus_minutes": request.form.get("bonus_minutes", type=int) or 0,
        "cap_type": request.form.get("cap_type", "time"),
        "data_limit_mb": request.form.get("data_limit_mb", type=int),
    }


def valid_plan(values):
    return (
        values["name"]
        and values["denomination"]
        and values["denomination"] > 0
        and values["minutes"]
        and values["minutes"] > 0
        and values["bonus_minutes"] >= 0
        and values["cap_type"] in {"time", "data"}
        and (values["cap_type"] == "time" or (values["data_limit_mb"] and values["data_limit_mb"] > 0))
    )


@app.post("/plans/<int:plan_id>/update")
@admin_required
def update_plan(plan_id):
    values = plan_form_values()
    if valid_plan(values):
        with get_db() as connection:
            connection.execute(
                """
                UPDATE plans
                SET name = ?, denomination = ?, minutes = ?, bonus_minutes = ?, cap_type = ?, data_limit_mb = ?
                WHERE id = ? AND active = 1
                """,
                (*values.values(), plan_id),
            )
        write_log("pricing", f"Updated plan {values['name']} ({plan_id})")
    return redirect(url_for("dashboard") + "#pricing")


@app.post("/plans/<int:plan_id>/delete")
@admin_required
def delete_plan(plan_id):
    plan_name = None
    with get_db() as connection:
        plan = connection.execute("SELECT name FROM plans WHERE id = ? AND active = 1", (plan_id,)).fetchone()
        if plan:
            connection.execute("UPDATE plans SET active = 0 WHERE id = ?", (plan_id,))
            plan_name = plan["name"]
    if plan_name:
        write_log("pricing", f"Archived plan {plan_name} ({plan_id})", "warning")
    return redirect(url_for("dashboard") + "#pricing")


@app.post("/vouchers")
@admin_required
def create_vouchers():
    count = min(max(request.form.get("count", type=int) or 0, 1), 500)
    minutes = request.form.get("minutes", type=int) or 60
    expires_days = request.form.get("expires_days", type=int) or 30
    tiers = {tier["key"]: tier for tier in voucher_speed_tiers()}
    tier = tiers.get(request.form.get("speed_tier", "2/1"))
    if tier is None:
        abort(400)
    expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat(timespec="seconds")
    with get_db() as connection:
        for _ in range(count):
            code = secrets.token_hex(4).upper()
            connection.execute(
                "INSERT INTO vouchers (code, minutes, download_limit_mbps, upload_limit_mbps, router_profile, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (code, minutes, tier["download"], tier["upload"], tier["profile"], now_text(), expires_at),
            )
    write_log("voucher", f"Generated {count} {tier['profile']} vouchers worth {minutes} minutes")
    return redirect(url_for("dashboard") + "#vouchers")


@app.post("/sessions/<int:session_id>/<action>")
@admin_required
def session_action(session_id, action):
    """The buttons on a #clients row: extend, pause, resume, kick, or clear a finished device."""
    with get_db() as connection:
        current = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if current is None:
        return redirect(url_for("dashboard") + "#clients")
    device = current["mac_address"]
    if action == "extend":
        minutes = min(max(request.form.get("minutes", type=int) or 30, 1), 1440)
        started = datetime.now()
        expiry = datetime.fromisoformat(current["expires_at"]) if current["expires_at"] else None
        if current["status"] == "paused" or expiry is None:
            # A paused session banks the extra minutes instead of running them down right away.
            minutes_left = max(0, int(current["minutes_left"] or 0)) + minutes
            expiry_text = current["expires_at"]
        else:
            # An active session keeps one clock: the portal countdown reads the same expiry this table does.
            expiry = max(expiry, started) + timedelta(minutes=minutes)
            minutes_left = max(1, int((expiry - started).total_seconds() // 60))
            expiry_text = expiry.isoformat(timespec="seconds")
        with get_db() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET minutes_left = ?, granted_minutes = granted_minutes + ?, expires_at = ?,
                    status = CASE WHEN status IN ('expired', 'ended', 'kicked') THEN 'active' ELSE status END,
                    source = ?
                WHERE id = ?
                """,
                (
                    minutes_left,
                    minutes,
                    expiry_text,
                    merged_source(current["source"], f"Admin +{minutes} min"),
                    session_id,
                ),
            )
        write_log("session", f"Extended session {session_id} ({device}) by {minutes} minutes to {minutes_left} minutes left")
        sync_session_to_router(session_id)
    elif action == "pause":
        # Freeze the clock: bank what is left and drop the expiry, so nothing runs down while paused.
        banked = max(1, (session_remaining_seconds(current) + 59) // 60)
        with get_db() as connection:
            connection.execute(
                "UPDATE sessions SET status = 'paused', minutes_left = ?, expires_at = NULL WHERE id = ?",
                (banked, session_id),
            )
        write_log("session", f"Paused session {session_id} ({device}) with {banked} minutes banked")
        router_release(device, "session paused")
    elif action == "resume":
        minutes = max(1, int(current["minutes_left"] or 0))
        expires_at = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with get_db() as connection:
            connection.execute(
                "UPDATE sessions SET status = 'active', expires_at = ?, last_seen = ? WHERE id = ?",
                (expires_at, now_text(), session_id),
            )
        write_log("session", f"Resumed session {session_id} ({device}) with {minutes} minutes back on the clock")
        sync_session_to_router(session_id)
    elif action == "kick":
        with get_db() as connection:
            connection.execute(
                "UPDATE sessions SET status = 'kicked', last_seen = ? WHERE id = ?",
                (now_text(), session_id),
            )
        # Real mode also drops the device off the hotspot; without a router this stays a console record.
        released = router_release(device, "kicked by operator") or {}
        write_log("session", f"Kicked session {session_id} ({device}); hotspot: {released.get('status', 'no hotspot record')}", "warning")
    elif action == "clear":
        if current["status"] in SESSION_FINISHED_STATUSES:
            with get_db() as connection:
                connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            write_log("session", f"Cleared finished session {session_id} ({device}) from the console")
    return redirect(url_for("dashboard") + "#clients")


@app.post("/sessions/clear-finished")
@admin_required
def clear_finished_sessions():
    """Housekeeping for #clients: drop every device that is done, the audit log keeps the record."""
    with get_db() as connection:
        removed = connection.execute(
            "DELETE FROM sessions WHERE status IN ('expired', 'kicked', 'ended')"
        ).rowcount
    if removed:
        write_log("session", f"Cleared {removed} finished client session(s) from the console")
    return redirect(url_for("dashboard") + "#clients")


@app.get("/api/admin/sessions")
@admin_api_required
def admin_sessions_api():
    """The #clients table polls this so time left, traffic and status stay current without a reload."""
    clients = client_rows()
    return jsonify(
        {
            "sessions": clients,
            "summary": client_summary(clients),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
    )


@app.post("/settings/system")
@admin_required
def update_system_settings():
    auto_settle = request.form.get("auto_settle", type=int)
    # The inactivity backstop the client counts down. Kept separate from
    # auto_settle on purpose: one settles the total, the other releases an
    # abandoned request. Absent from the form (older cached page) means "keep
    # whatever is stored" rather than silently resetting it to the default.
    request_timeout = request.form.get("request_timeout", type=int)
    if request_timeout is not None:
        request_timeout = min(max(request_timeout, 1), 1440)
    updates = {
        "ssid": request.form.get("ssid", "PisoPilot WiFi").strip()[:64],
        "session_mode": request.form.get("session_mode", "time"),
        "mac_voucher_mode": "1" if request.form.get("mac_voucher_mode") else "0",
        "coin_pulse_tolerance_ms": str(min(max(request.form.get("pulse_tolerance", type=int) or 80, 10), 500)),
        # 0 keeps the operator pressing Finish; any other value is the quiet window that self-settles a coin total.
        "coin_auto_settle_seconds": str(min(max(5 if auto_settle is None else auto_settle, 0), 300)),
    }
    if request_timeout is not None:
        updates["coin_request_timeout_minutes"] = str(request_timeout)
    with get_db() as connection:
        for key, value in updates.items():
            connection.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    write_log("settings", "Updated network, session, and coin acceptor settings")
    return redirect(url_for("dashboard") + "#settings")


@app.post("/settings/admin")
@admin_required
def update_admin():
    username = request.form.get("username", "admin").strip()
    password = request.form.get("password", "")
    if username and len(password) >= 6:
        with get_db() as connection:
            connection.execute("UPDATE admins SET username = ?, password_hash = ? WHERE id = ?", (username, generate_password_hash(password), session["admin_id"]))
        write_log("security", f"Updated admin account {username}")
        session.clear()
    return redirect(url_for("login"))


def run_power_action(command, description):
    """Run a privileged command, and report honestly whether it worked.

    The old handler logged "Requested restart-service" and did nothing at all, so
    an operator pressing Restart was told nothing had happened and had no way to
    tell the difference from a failure. Now the result - including the real
    error - is written to the log the dashboard shows.
    """
    if shutil.which(command[0]) is None:
        message = f"{description}: '{command[0]}' is not installed, so nothing was done"
        write_log("system", message, "warning")
        return message
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except Exception as error:
        message = f"{description} failed: {error}"
        write_log("system", message, "warning")
        return message
    if completed.returncode == 0:
        message = f"{description}: sent ({' '.join(command)})"
        write_log("system", message, "info")
    else:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        message = f"{description} failed (exit {completed.returncode}): {detail or 'no output'}"
        write_log("system", message, "warning")
    return message


# Real commands, resolved at run time. A development machine has no systemctl,
# so the handler says so plainly instead of pretending.
POWER_ACTIONS = {
    "restart-service": (["sudo", "systemctl", "restart", "piso-wifi"], "Service restart requested"),
    "restart-network": (["sudo", "systemctl", "restart", "NetworkManager"], "Network restart requested"),
    "reboot": (["sudo", "systemctl", "reboot"], "Reboot requested"),
    "shutdown": (["sudo", "systemctl", "poweroff"], "Shutdown requested"),
}


@app.post("/system/<action>")
@admin_required
def system_action(action):
    if action not in POWER_ACTIONS:
        write_log("system", f"Ignored unknown system action '{action}'", "warning")
        return redirect(url_for("dashboard") + "#settings")
    command, description = POWER_ACTIONS[action]
    # A power action must not run inside the request that triggers it, or the
    # response never reaches the browser and the operator sees a hung page.
    if action in ("reboot", "shutdown"):
        message = f"{description}: the system will go down now"
        write_log("system", message, "warning")
        threading.Timer(2, lambda: run_power_action(command, description)).start()
    else:
        run_power_action(command, description)
    return redirect(url_for("dashboard") + "#settings")


@app.get("/api/status")
def status():
    with get_db() as connection:
        totals = connection.execute(
            "SELECT COUNT(*) AS coin_count, COALESCE(SUM(amount), 0) AS revenue FROM coin_events"
        ).fetchone()
    return jsonify(
        {
            "status": "online",
            "rate": {"minutes_per_peso": get_minutes_per_peso()},
            "totals": dict(totals),
            "active_users": connection.execute("SELECT COUNT(*) AS total FROM sessions WHERE status = 'active'").fetchone()["total"],
            "resources": system_metrics(),
            "hardware": hardware_status(),
            "internet": internet_status(),
        }
    )


@app.get("/api/hardware/status")
@admin_required
def hardware_status_api():
    return jsonify(hardware_status())


@app.get("/api/admin/coin-input-settings")
@admin_api_required
def coin_input_settings_api():
    return jsonify({**coin_input_settings(), "listener_status": hardware.coin_listener_state()})


@app.post("/api/admin/coin-input-settings")
@admin_api_required
def update_coin_input_settings_api():
    values, error = validate_coin_input_settings(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400
    listener = hardware.coin_listener
    if listener is not None and listener.has_pending_pulses:
        return jsonify({"error": "A coin pulse burst is in progress. Wait for it to finish, then save again."}), 409

    updates = {
        "coin_gpio": str(values["gpio"]),
        "coin_pull_up": "1" if values["pull_up"] else "0",
        "coin_active_low": "1" if values["active_low"] else "0",
        "coin_edge_debounce_ms": str(values["edge_debounce_ms"]),
        "coin_debounce_ms": str(values["burst_quiet_ms"]),
        "coin_pulses": json.dumps(values["pulse_map"], separators=(",", ":")),
    }
    with get_db() as connection:
        for key, value in updates.items():
            connection.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    listener_state = {"status": "simulated", "detail": "Settings saved; live GPIO applies in real mode."}
    if hardware.real_mode():
        if listener is not None:
            listener.stop()
        hardware.coin_listener = CoinPulseListener(record_hardware_coin, configured_coin_input())
        listener_state = hardware.coin_listener.start()
        hardware.set_coin_listener_detail(
            "" if listener_state["status"] == "online" else listener_state.get("detail", "")
        )
    write_log(
        "hardware",
        f"Admin updated coin input settings; GPIO{values['gpio']} listener {listener_state['status']}",
        "warning" if listener_state["status"] == "offline" else "info",
    )
    return jsonify({"settings": coin_input_settings(), "listener": listener_state})


@app.get("/api/admin/voucher-speed-tiers")
@admin_api_required
def voucher_speed_tiers_api():
    return jsonify({"tiers": voucher_speed_tiers()})


@app.get("/api/admin/speed-tiers")
@admin_api_required
def speed_tiers_api():
    return jsonify({"tiers": voucher_speed_tiers()})


@app.post("/api/admin/speed-tiers")
@admin_api_required
def create_speed_tier_api():
    tier, error = parse_speed_tier(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    with get_db() as connection:
        existing = connection.execute(
            "SELECT * FROM speed_tiers WHERE (download_limit_mbps = ? AND upload_limit_mbps = ?) OR router_profile = ?",
            (tier["download"], tier["upload"], tier["profile"]),
        ).fetchall()
    match = next(
        (
            row for row in existing
            if row["download_limit_mbps"] == tier["download"]
            and row["upload_limit_mbps"] == tier["upload"]
            and row["router_profile"] == tier["profile"]
        ),
        None,
    )
    if existing and not (match and not match["active"]):
        return jsonify({"error": "A speed or RouterOS profile is already assigned to another tier."}), 409
    router_sync, sync_error = push_speed_tier_to_router(tier)
    if sync_error:
        return jsonify({"error": f"RouterOS profile sync failed; tier was not saved: {sync_error}"}), 502
    with get_db() as connection:
        if match:
            connection.execute("UPDATE speed_tiers SET active = 1 WHERE id = ?", (match["id"],))
            tier_id = match["id"]
        else:
            cursor = connection.execute(
                "INSERT INTO speed_tiers (download_limit_mbps, upload_limit_mbps, router_profile) VALUES (?, ?, ?)",
                (tier["download"], tier["upload"], tier["profile"]),
            )
            tier_id = cursor.lastrowid
    write_log("settings", f"Added speed tier {tier['download']:g}/{tier['upload']:g} Mbps ({tier['profile']})")
    return jsonify({"id": tier_id, "router_sync": router_sync}), 201


@app.put("/api/admin/speed-tiers/<int:tier_id>")
@admin_api_required
def update_speed_tier_api(tier_id):
    tier, error = parse_speed_tier(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    with get_db() as connection:
        current = connection.execute("SELECT id FROM speed_tiers WHERE id = ? AND active = 1", (tier_id,)).fetchone()
        if current is None:
            return jsonify({"error": "Speed tier not found."}), 404
        conflict = connection.execute(
            "SELECT id FROM speed_tiers WHERE id != ? AND (download_limit_mbps = ? AND upload_limit_mbps = ? OR router_profile = ?)",
            (tier_id, tier["download"], tier["upload"], tier["profile"]),
        ).fetchone()
        if conflict:
            return jsonify({"error": "A speed or RouterOS profile is already assigned to another tier."}), 409
    router_sync, sync_error = push_speed_tier_to_router(tier)
    if sync_error:
        return jsonify({"error": f"RouterOS profile sync failed; tier was not saved: {sync_error}"}), 502
    with get_db() as connection:
        connection.execute(
            "UPDATE speed_tiers SET download_limit_mbps = ?, upload_limit_mbps = ?, router_profile = ? WHERE id = ?",
            (tier["download"], tier["upload"], tier["profile"], tier_id),
        )
    write_log("settings", f"Updated speed tier {tier['download']:g}/{tier['upload']:g} Mbps ({tier['profile']})")
    return jsonify({"ok": True, "router_sync": router_sync})


@app.delete("/api/admin/speed-tiers/<int:tier_id>")
@admin_api_required
def archive_speed_tier_api(tier_id):
    with get_db() as connection:
        current = connection.execute("SELECT * FROM speed_tiers WHERE id = ? AND active = 1", (tier_id,)).fetchone()
        if current is None:
            return jsonify({"error": "Speed tier not found."}), 404
        remaining = connection.execute("SELECT COUNT(*) AS total FROM speed_tiers WHERE active = 1").fetchone()["total"]
        if remaining <= 1:
            return jsonify({"error": "At least one active speed tier is required."}), 409
        connection.execute("UPDATE speed_tiers SET active = 0 WHERE id = ?", (tier_id,))
    write_log("settings", f"Archived speed tier {current['router_profile']}", "warning")
    return jsonify({"ok": True})


@app.get("/api/admin/voucher-inventory-tiers")
@admin_api_required
def voucher_inventory_tiers_api():
    tiers = voucher_speed_tiers()
    with get_db() as connection:
        vouchers = connection.execute(
            "SELECT code, download_limit_mbps, upload_limit_mbps, router_profile FROM vouchers ORDER BY id DESC LIMIT 8"
        ).fetchall()
    inventory = []
    for voucher in vouchers:
        tier = next(
            (
                item for item in tiers
                if item["download"] == voucher["download_limit_mbps"]
                and item["upload"] == voucher["upload_limit_mbps"]
            ),
            None,
        )
        inventory.append(
            {
                "code": voucher["code"],
                "label": (
                    f"{voucher['download_limit_mbps']:g}/{voucher['upload_limit_mbps']:g} Mbps down/up - "
                    f"{voucher['router_profile'] or (tier['profile'] if tier else 'unmapped')}"
                ),
            }
        )
    return jsonify({"vouchers": inventory})


@app.post("/hardware/coin-acceptor/<action>")
@admin_required
def hardware_coin_acceptor_action(action):
    if action not in {"start", "stop"}:
        abort(404)
    if not hardware.real_mode():
        write_log("hardware", f"Ignored coin acceptor {action}: hardware mode is simulation", "warning")
        return redirect(url_for("dashboard") + "#hardware")

    if hardware.coin_listener is None:
        # Build from the saved settings, not the bare defaults, so a hand start
        # uses the same GPIO the dashboard shows.
        hardware.coin_listener = start_coin_listener()
    if action == "stop":
        hardware.coin_listener.stop()
        result = {"status": "offline", "detail": "Coin acceptor stopped by admin"}
    elif hardware.coin_listener.listening:
        result = {"status": "online", "detail": "Coin GPIO listener was already running"}
    else:
        result = hardware.coin_listener.start()

    # Keep the dashboard's reason in step with what the admin just did, so a
    # deliberate stop is not reported as an unexplained fault.
    hardware.set_coin_listener_detail("" if result["status"] == "online" else result.get("detail", ""))

    detail = result.get("detail")
    message = f"Admin {action} coin acceptor: {result['status']}"
    if detail:
        message += f" ({detail})"
    write_log("hardware", message, "warning" if result["status"] == "offline" else "info")
    return redirect(url_for("dashboard") + "#hardware")


@app.get("/api/internet-status")
def internet_status_api():
    """Public uplink state polled by both the operator console and the customer portal."""
    return jsonify(internet_status())


def record_hardware_coin(amount, pulses):
    """GPIO coin: add the denomination to the client's open request.

    The acceptor cannot tell who paid, so a coin is credited to whoever holds the open request (the
    client pressed Insert coin first). With nobody waiting the coin is still booked, but it grants no
    time and the audit trail says so with a warning, so the operator can hand out the time by hand.
    """
    waiting = pending_coin_request()
    if waiting is None:
        with get_db() as connection:
            connection.execute(
                "INSERT INTO coin_events (amount, minutes, source, created_at) VALUES (?, ?, ?, ?)",
                (amount, 0, f"gpio:{pulses}-pulse", now_text()),
            )
        write_log(
            "coin",
            f"P{amount:.0f} landed from {pulses} pulses with no client waiting: no time was granted. "
            "Clients must press Insert coin before paying.",
            "warning",
        )
        return None
    updated = accumulate_coin(waiting["id"], amount)
    if updated is None:
        write_log(
            "coin",
            f"P{amount:.0f} from {pulses} pulses landed while request {waiting['id']} was already settling",
            "warning",
        )
        return None
    payout = best_coin_allocation(updated["amount"])
    write_log(
        "coin",
        f"P{amount:.0f} coin from {pulses} pulses joined request {updated['id']} ({updated['device_id']}): "
        f"total P{float(updated['amount']):.0f} now buys {payout['summary']} ({payout['minutes']} minutes); waiting for client finish",
    )
    return updated


def router_sweep_loop():
    """Real mode only: keep the hotspot in step with the console clock.

    Nothing else expires a session while nobody is clicking, so this loop is what
    stops a device whose paid time ran out from staying online for free.
    """
    interval = max(10, int(hardware.router_adapter.config.router_sweep_seconds))
    while True:
        time.sleep(interval)
        try:
            expire_finished_sessions()
        except Exception as error:  # the sweep must outlive a router hiccup
            write_log("router", f"Hotspot sweep failed: {error}", "warning")


def sync_active_speed_tiers_to_router():
    if not hardware.real_mode():
        return
    for tier in voucher_speed_tiers():
        try:
            result = hardware.router_adapter.sync_hotspot_user_profile(
                tier["profile"], tier["download"], tier["upload"]
            )
            write_log(
                "router",
                f"Startup speed-tier sync {result['status']}: {tier['profile']} rate-limit={result.get('rate_limit', 'unchanged')}",
            )
        except Exception as error:
            write_log("router", f"Startup speed-tier sync failed for {tier['profile']}: {error}", "warning")


def start_coin_listener():
    """Build a fresh, unstarted coin listener from the current saved settings."""
    return CoinPulseListener(record_hardware_coin, configured_coin_input())


def start_hardware():
    # A restart drops the quiet-window timers, so settle any request whose coins
    # are already quiet. This runs on the boot thread and must not wait for GPIO.
    settle_quiet_coin_requests()

    def note_attempt(attempt, result):
        status = result.get("status")
        detail = str(result.get("detail") or "").strip()
        if status == "online":
            write_log("hardware", f"Coin listener online on attempt {attempt}")
            return
        message = f"Coin listener attempt {attempt}: {status}"
        if detail:
            message += f" ({detail})"
        # Warn on the first few and the last, so a dead backend does not bury
        # the audit log in ten near-identical lines.
        write_log("hardware", message, "warning" if attempt in {1, 3, 10} else "info")

    def run_listener():
        # Retries run on their own thread so a pin that is briefly unavailable
        # at boot heals by itself instead of leaving the acceptor offline for
        # the life of the process. The portal is already serving meanwhile.
        hardware.supervise_coin_listener(start_coin_listener, note_attempt)

    threading.Thread(target=run_listener, name="piso-coin-listener", daemon=True).start()
    if hardware.real_mode():
        threading.Thread(
            target=sync_active_speed_tiers_to_router,
            name="piso-speed-tier-sync",
            daemon=True,
        ).start()
        threading.Thread(target=router_sweep_loop, name="piso-hotspot-sweep", daemon=True).start()
        write_log("router", f"Hotspot sweep running every {hardware.router_adapter.config.router_sweep_seconds}s")


initialize_database()

if __name__ == "__main__":
    debug = os.environ.get("PISO_DEBUG", "1") == "1"
    app.debug = debug
    # The Werkzeug reloader executes this module twice. Only the serving process may claim the GPIO pin,
    # otherwise two listeners would count every coin twice.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            start_hardware()
        except Exception as error:
            # The portal has to come up even when a peripheral does not: it is the page that
            # takes the money. Keep serving and surface the fault in the console log instead.
            write_log("hardware", f"Hardware did not start, serving the portal without it: {error}", "error")
    app.run(host="0.0.0.0", port=5000, debug=debug)
