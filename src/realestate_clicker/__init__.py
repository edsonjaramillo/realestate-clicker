"""Auto clicker for advancing course slides.

Works on Wayland/X11 by creating a virtual mouse through /dev/uinput.
Wayland doesn't let apps read or set the cursor position, so the clicker
clicks wherever the cursor currently is: park the mouse on "Next" and go.

Global hotkeys (read straight from keyboard devices, work in any window).
Keys are NOT swallowed, so they're chosen to be harmless in a browser:
    -  / keypad -  -> click more often (interval - STEP)
    =  / keypad +  -> click less often (interval + STEP)
    F8             -> click now and restart the countdown
    F9             -> pause / resume
    F10            -> quit
Ctrl+C in the terminal also quits.
"""

from __future__ import annotations

import argparse
import math
import random
import select
import sys
import time

from evdev import InputDevice, UInput, ecodes, list_devices
from evdev.uinput import UInputError

KEYMAP = {
    ecodes.KEY_MINUS: "faster",
    ecodes.KEY_KPMINUS: "faster",
    ecodes.KEY_EQUAL: "slower",
    ecodes.KEY_KPPLUS: "slower",
    ecodes.KEY_F8: "click",
    ecodes.KEY_F9: "pause",
    ecodes.KEY_F10: "quit",
}
REPEATABLE = {"faster", "slower"}  # allow holding the key down
MIN_INTERVAL = 1.0
TICK = 0.2  # screen refresh rate (s)

# ANSI helpers
CLEAR_LINE = "\r\033[K"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
BOLD, DIM, GREEN, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[36m", "\033[0m",
)


# ---------------------------------------------------------------- devices

def make_mouse() -> UInput:
    caps = {
        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT],
        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
    }
    try:
        return UInput(caps, name="realestate-clicker")
    except (PermissionError, UInputError):
        sys.exit(
            "No permission for /dev/uinput.\n"
            "Run `task setup`, then log out and back in."
        )


def find_keyboards() -> list[InputDevice]:
    keyboards = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_F9 in keys and ecodes.KEY_MINUS in keys:
            keyboards.append(dev)
        else:
            dev.close()
    return keyboards


def click(mouse: UInput) -> None:
    mouse.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
    mouse.syn()
    time.sleep(random.uniform(0.05, 0.12))
    mouse.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
    mouse.syn()


def poll_keys(keyboards: list[InputDevice], timeout: float) -> list[str]:
    """Wait up to `timeout` seconds and return any hotkey actions pressed."""
    if not keyboards:
        time.sleep(timeout)
        return []
    ready, _, _ = select.select(keyboards, [], [], timeout)
    actions = []
    for dev in ready:
        try:
            events = list(dev.read())
        except OSError:
            continue
        for ev in events:
            if ev.type != ecodes.EV_KEY or ev.code not in KEYMAP:
                continue
            action = KEYMAP[ev.code]
            if ev.value == 1 or (ev.value == 2 and action in REPEATABLE):
                actions.append(action)
    return actions


# ---------------------------------------------------------------- display

def fmt_secs(s: float) -> str:
    s = max(0, math.ceil(s - 0.05))
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def print_header(args: argparse.Namespace, hotkeys: bool) -> None:
    print(f"{BOLD}Real Estate Clicker{RESET}  {DIM}— park the mouse on “Next”{RESET}")
    if hotkeys:
        print(
            f"{DIM}  [-] faster  [=] slower  ({args.step:g}s)   "
            f"[F8] click now   [F9] pause   [F10] quit{RESET}"
        )
    else:
        print(f"{YELLOW}  Hotkeys unavailable (no keyboard access) — Ctrl+C to quit{RESET}")
    print()


def render(state: str, remaining: float, interval: float, jitter: float,
           clicks: int, last: str) -> None:
    if state == "starting":
        status = f"{CYAN}● STARTING{RESET}  first click in {BOLD}{fmt_secs(remaining)}{RESET}"
    elif state == "stopped":
        status = f"{DIM}■ STOPPED{RESET} "
    elif state == "paused":
        status = f"{YELLOW}❚❚ PAUSED{RESET}   {DIM}next in {fmt_secs(remaining)}{RESET}"
    else:
        status = f"{GREEN}▶ RUNNING{RESET}  next click in {BOLD}{fmt_secs(remaining)}{RESET}"
    every = f"every {fmt_secs(interval)}" + (f" ±{jitter:g}s" if jitter else "")
    sys.stdout.write(
        f"{CLEAR_LINE}  {status}  {DIM}│{RESET} {every}  {DIM}│{RESET} "
        f"clicks {BOLD}{clicks}{RESET}  {DIM}│ last {last}{RESET}"
    )
    sys.stdout.flush()


# ---------------------------------------------------------------- main loop

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Click 'Next' every N seconds.")
    p.add_argument("-i", "--interval", type=float, default=30,
                   help="seconds between clicks (default: 30)")
    p.add_argument("-j", "--jitter", type=float, default=2,
                   help="random +/- seconds added to each interval (default: 2)")
    p.add_argument("-d", "--delay", type=float, default=5,
                   help="countdown before first click, to park the mouse (default: 5)")
    p.add_argument("-s", "--step", type=float, default=5,
                   help="seconds added/removed by the -/= hotkeys (default: 5)")
    p.add_argument("-n", "--max-clicks", type=int, default=0,
                   help="stop after this many clicks, 0 = unlimited (default: 0)")
    return p.parse_args()


def run(args: argparse.Namespace) -> None:
    mouse = make_mouse()
    keyboards = find_keyboards()
    print_header(args, bool(keyboards))

    interval = max(MIN_INTERVAL, args.interval)
    clicks = 0
    last = "—"
    state = "starting"
    next_at = time.monotonic() + args.delay
    paused_remaining = 0.0

    def schedule() -> float:
        jitter = random.uniform(-args.jitter, args.jitter) if args.jitter else 0
        return time.monotonic() + max(MIN_INTERVAL, interval + jitter)

    def do_click() -> None:
        nonlocal clicks, last
        click(mouse)
        clicks += 1
        last = time.strftime("%H:%M:%S")

    try:
        while True:
            now = time.monotonic()

            if state != "paused" and now >= next_at:
                do_click()
                state = "running"
                next_at = schedule()
                if args.max_clicks and clicks >= args.max_clicks:
                    break

            remaining = paused_remaining if state == "paused" else next_at - time.monotonic()
            render(state, remaining, interval, args.jitter, clicks, last)

            wait = TICK if state == "paused" else min(TICK, max(0.0, next_at - time.monotonic()))
            for action in poll_keys(keyboards, wait):
                if action == "quit":
                    return
                elif action == "click":
                    do_click()
                    if state == "paused":
                        paused_remaining = interval
                    else:
                        state = "running"
                        next_at = schedule()
                elif action == "pause":
                    if state == "paused":
                        state = "running"
                        next_at = time.monotonic() + paused_remaining
                    else:
                        paused_remaining = max(0.0, next_at - time.monotonic())
                        state = "paused"
                elif action in ("faster", "slower"):
                    delta = -args.step if action == "faster" else args.step
                    old = interval
                    interval = max(MIN_INTERVAL, interval + delta)
                    applied = interval - old
                    # shift the current countdown by the same amount
                    if state == "paused":
                        paused_remaining = max(0.0, paused_remaining + applied)
                    elif state == "running":
                        next_at = max(time.monotonic(), next_at + applied)
    finally:
        render("stopped", 0, interval, args.jitter, clicks, last)
        mouse.close()
        for kb in keyboards:
            kb.close()


def main() -> None:
    args = parse_args()
    sys.stdout.write(HIDE_CURSOR)
    try:
        run(args)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(f"{SHOW_CURSOR}\n")
        sys.stdout.flush()
