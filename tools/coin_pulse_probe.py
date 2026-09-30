"""Discover what the Allan 1239A coin slot really sends to one GPIO pin.

Run this on the Raspberry Pi *before* switching the controller to
``PISO_HARDWARE_MODE=real`` so ``PISO_COIN_PULSES`` can be filled in from real
measurements instead of a guess:

    sudo systemctl stop piso-wifi        # only one process may own the pin
    python3 tools/coin_pulse_probe.py --gpio 17 --pull-up --active-low

Then drop one coin of each denomination down the slot. Every burst is printed as
a pulse count together with the gap that closed it, so the map is read straight
off the screen:

    P1  -> 1 pulse
    P5  -> 5 pulses
    P10 -> 10 pulses
    P20 -> 20 pulses

Copy the result into ``.env`` (the JSON keys are pulse counts, the values are
peso amounts):

    PISO_COIN_PULSES={"1":1,"5":5,"10":10,"20":20}

The script is read-only: it never credits time and never touches the database.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    from gpiozero import DigitalInputDevice
except ImportError:  # gpiozero is only needed on the Pi itself.
    DigitalInputDevice = None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Count coin-slot pulses on one GPIO pin.")
    parser.add_argument("--gpio", type=int, default=17, help="BCM pin the acceptor pulse line uses (default: 17)")
    parser.add_argument(
        "--pull-up",
        dest="pull_up",
        action="store_true",
        default=True,
        help="enable the internal pull-up resistor (default: on)",
    )
    parser.add_argument(
        "--no-pull-up",
        dest="pull_up",
        action="store_false",
        help="use an external pull-up instead of the internal one",
    )
    parser.add_argument(
        "--active-low",
        dest="active_low",
        action="store_true",
        default=True,
        help="count the falling edge, i.e. the switch closing to ground (default: on)",
    )
    parser.add_argument(
        "--active-high",
        dest="active_low",
        action="store_false",
        help="count the rising edge instead (for a driven 3.3 V pulse line)",
    )
    parser.add_argument(
        "--gap-ms",
        type=int,
        default=None,
        help="quiet time that closes a burst; defaults to the pulse tolerance in the app settings",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="stop after this many seconds (0 = run until Ctrl+C)",
    )
    return parser.parse_args(argv)


def describe(pulses: int, gap_ms: int) -> str:
    """Print the burst and the peso values the seed price list would pay for it."""
    known = {1: "P1", 5: "P5", 10: "P10", 20: "P20"}
    guess = known.get(pulses, "unmapped")
    return f"{time.strftime('%H:%M:%S')}  {pulses} pulse(s)  after {gap_ms} ms quiet  ->  {guess}"


def main(argv=None) -> int:
    args = parse_args(argv)
    if DigitalInputDevice is None:
        print("gpiozero is not installed. Run this on the Raspberry Pi after `pip install -r requirements.txt`.")
        return 1

    gap_ms = args.gap_ms if args.gap_ms is not None else 100
    print(f"Listening on GPIO{args.gpio} (pull_up={args.pull_up}, active_low={args.active_low}, gap={gap_ms} ms)")
    print("Drop one coin of each denomination. Press Ctrl+C when the map is complete.\n")

    device = DigitalInputDevice(args.gpio, pull_up=args.pull_up)
    counters = {"pulses": 0, "last": time.monotonic()}
    release = {"last_log": time.monotonic()}
    started = time.monotonic()
    close_gap = gap_ms / 1000

    def pulse():
        now = time.monotonic()
        quiet_ms = int((now - counters["last"]) * 1000)
        if counters["pulses"] and quiet_ms > gap_ms * 4:
            # A new coin after a long silence is a new burst; report the old one first.
            print(describe(counters["pulses"], int((now - release["last_log"]) * 1000)))
        counters["pulses"] += 1
        counters["last"] = now

    if args.active_low:
        device.when_deactivated = pulse
    else:
        device.when_activated = pulse

    try:
        while True:
            time.sleep(0.02)
            now = time.monotonic()
            if counters["pulses"] and now - counters["last"] > close_gap:
                if now - release["last_log"] > close_gap:
                    print(describe(counters["pulses"], int((counters["last"] - release["last_log"]) * 1000)))
                    release["last_log"] = now
                counters["pulses"] = 0
            if args.timeout and now - started > args.timeout:
                break
    except KeyboardInterrupt:
        if counters["pulses"]:
            print(describe(counters["pulses"], gap_ms))
        print("\nStopped.")
    finally:
        device.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
