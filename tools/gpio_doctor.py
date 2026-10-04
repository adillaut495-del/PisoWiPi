"""Explain exactly why the coin acceptor cannot claim its GPIO pin.

Run this on the Raspberry Pi when the console reports the acceptor offline:

    sudo systemctl stop piso-wifi     # only one process may own the pin
    python3 tools/gpio_doctor.py --gpio 17

It reports which pin backend gpiozero actually loaded, whether the kernel
exposes that line, whether anything else already holds it, and whether the pin
can actually be claimed - then prints the specific fix. It never writes to the
database and never credits time.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Diagnose the coin acceptor GPIO pin.")
    parser.add_argument("--gpio", type=int, default=int(os.getenv("PISO_COIN_GPIO", "17")))
    return parser.parse_args(argv)


def show(label, value):
    print(f"  {label:<34} {value}")


def run(*command):
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
        return (completed.stdout or completed.stderr).strip()
    except Exception as error:
        return f"(could not run {' '.join(command)}: {error})"


def report_environment(gpio):
    print(f"\nCoin acceptor GPIO diagnosis for pin {gpio}\n")

    # Which python, and which gpiozero: the service runs from the venv, so the
    # operator must test with that interpreter and not a system one.
    print("Environment")
    show("python", sys.executable)
    show("inside a venv", sys.prefix != getattr(sys, "base_prefix", sys.prefix))
    show("GPIOZERO_PIN_FACTORY", os.getenv("GPIOZERO_PIN_FACTORY") or "(unset)")
    try:
        import importlib.metadata as metadata

        show("gpiozero version", metadata.version("gpiozero"))
    except Exception as error:
        show("gpiozero version", f"unknown ({error})")


def report_backend():
    # The backend gpiozero resolved is the single most useful fact here: every
    # pin error below depends on which library is actually driving the line.
    print("\nPin backend")
    backend = None
    try:
        from gpiozero import Device

        backend = Device.pin_factory
        show("resolved factory", type(backend).__name__ if backend else "NONE")
        if backend is not None:
            show("gpiochip", getattr(backend, "chip", None))
    except Exception as error:
        show("resolved factory", f"failed: {type(error).__name__}: {error}")

    for module_name in ("lgpio", "RPi", "pigpio"):
        try:
            module = __import__(module_name, fromlist=["x"])
            version = getattr(module, "__version__", None)
            if version is None:
                try:
                    import importlib.metadata as metadata

                    version = metadata.version("lgpio" if module_name == "lgpio" else module_name)
                except Exception:
                    version = "version unknown"
            show(f"import {module_name}", f"OK ({version})")
        except ImportError:
            show(f"import {module_name}", "NOT AVAILABLE")
    return backend
def report_kernel(gpio):
    print("\nKernel")
    show("/dev/gpiochip*", run("sh", "-c", "ls /dev/gpiochip* 2>/dev/null || echo 'none present'"))

    gpioinfo = run("gpioinfo")
    unusable = not gpioinfo or "not found" in gpioinfo.lower() or "not recognized" in gpioinfo.lower()
    if unusable:
        show("gpioinfo", "not installed - run: sudo apt install -y python3-gpiozero-tools")
        return
    lines = [line.strip() for line in gpioinfo.splitlines() if f"line {gpio}:" in line]
    if lines:
        show(f"line {gpio}", lines[0])
    else:
        show(f"line {gpio}", "NOT LISTED by gpioinfo")
        print("    (every line gpiozero lists is claimable; if yours is absent it")
        print("     does not exist on this board or is reserved by the kernel)")


def report_holders():
    print("\nWho holds the pin")
    show("fuser /dev/gpiochip0", run("fuser", "/dev/gpiochip0") or "nothing holds it")
    show("piso-wifi running", run("sh", "-c", "pgrep -af 'app.py' || echo 'not running'"))


def suggest_fix(backend, error, gpio):
    print("\nMost likely fix")
    errno = getattr(error, "errno", None)
    if backend is None:
        print("  No pin backend is loaded, so no pin can ever be claimed.")
        print("    sudo apt install -y python3-lgpio")
        print("    rm -rf ~/.venv && python3 -m venv --system-site-packages ~/.venv")
        print(f"    {sys.executable} -m pip install -r requirements.txt")
        print("  Then re-run this script with the venv python and expect SUCCESS.")
    elif errno == 13:
        print("  Permission denied. Add the service user to the gpio group, then reboot:")
        print("    sudo usermod -aG gpio $(whoami)")
    elif "in use" in str(error).lower() or "busy" in str(error).lower():
        print("  Another process holds the pin. Stop it before testing:")
        print("    sudo systemctl stop piso-wifi")
    else:
        print("  The backend loaded but the kernel rejected the line. Check that:")
        print(f"    - GPIO{gpio} is a valid BCM pin on this board (see `gpioinfo` above)")
        print("    - it is not already exported by another service")
        print("    - the relay really is wired to the pin you think it is; a wrong pin")
        print("      number is the usual cause of errno 22 with a working backend")


def main(argv=None) -> int:
    args = parse_args(argv)
    gpio = args.gpio

    report_environment(gpio)
    backend = report_backend()
    report_kernel(gpio)
    report_holders()

    # The decisive test: can this process actually claim the pin?
    print("\nClaim test")
    try:
        from gpiozero import DigitalInputDevice

        device = DigitalInputDevice(gpio, pull_up=True)
        print(f"  SUCCESS - GPIO{gpio} was claimed and released cleanly.")
        device.close()
        print("\nIf the portal still shows offline, the wiring is fine and the problem")
        print("is the configuration the service reads, not the pin.")
        return 0
    except Exception as error:
        errno = getattr(error, "errno", None)
        print(f"  FAILED - {type(error).__name__}: {error} (errno={errno})")
        suggest_fix(backend, error, gpio)
        return 1


if __name__ == "__main__":
    sys.exit(main())