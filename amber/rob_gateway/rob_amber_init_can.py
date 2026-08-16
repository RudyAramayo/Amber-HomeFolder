#!/usr/bin/python3
"""Root-owned, deterministic USB-CAN initialization for rc.local.

This utility configures the two serial line CAN interfaces. It never opens a
CAN socket or sends a CAN, UDP, LCM, mode, trajectory, or motion command.
"""

from __future__ import annotations

import glob
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


MAPPING_PATH = "/etc/rob-amber-gateway/can-interfaces.json"
SLCAND_PATH = "/usr/bin/slcand"
IP_PATH = "/usr/sbin/ip"
EXPECTED_INTERFACES = frozenset(("can10", "can11"))


def fail(message: str) -> NoReturn:
    print(f"[ AMBER CAN ] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def load_mapping() -> dict[str, str]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            MAPPING_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_uid != 0 \
                or status.st_mode & 0o022:
            fail("adapter mapping is not a protected root-owned regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = None
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError):
        fail("root-owned adapter mapping is missing or invalid")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(payload, dict) or set(payload) != EXPECTED_INTERFACES:
        fail("adapter mapping must contain exactly can10 and can11")
    if not all(isinstance(value, str) and value.isalnum() and 4 <= len(value) <= 64
               for value in payload.values()):
        fail("adapter serial values are invalid")
    if len(set(payload.values())) != 2:
        fail("adapter serial values must be distinct")
    return dict(payload)


def serial_for_device(device: str) -> str | None:
    try:
        current = Path("/sys/class/tty", os.path.basename(device), "device").resolve(
            strict=True
        )
    except OSError:
        return None
    for candidate in (current, *current.parents):
        try:
            serial_value = (candidate / "serial").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if serial_value:
            return serial_value
    return None


def run(arguments: list[str], description: str) -> None:
    try:
        result = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        fail(f"{description} failed or timed out")
    if result.returncode != 0:
        fail(f"{description} failed")


def interface_exists(interface: str) -> bool:
    result = subprocess.run(
        [IP_PATH, "link", "show", "dev", interface],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=3,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    return result.returncode == 0


def main() -> int:
    if len(sys.argv) != 1:
        fail("this initializer accepts no arguments")
    if os.geteuid() != 0:
        fail("this initializer must run as root from rc.local")

    mapping = load_mapping()
    serial_to_devices: dict[str, list[str]] = {}
    for device in sorted(glob.glob("/dev/ttyACM*")):
        try:
            status = os.stat(device)
        except OSError:
            continue
        if not stat.S_ISCHR(status.st_mode):
            continue
        serial_value = serial_for_device(device)
        if serial_value:
            serial_to_devices.setdefault(serial_value, []).append(device)

    resolved: dict[str, str] = {}
    for interface, serial_value in mapping.items():
        devices = serial_to_devices.get(serial_value, [])
        if len(devices) != 1:
            fail(f"expected exactly one USB adapter for {interface}")
        if interface_exists(interface):
            fail(f"stale interface {interface} already exists")
        resolved[interface] = devices[0]
    if len(set(resolved.values())) != 2:
        fail("can10 and can11 did not resolve to distinct USB devices")

    for interface in sorted(resolved):
        device = resolved[interface]
        run(
            [SLCAND_PATH, "-o", "-c", "-s8", device, interface],
            f"starting {interface}",
        )
        run([IP_PATH, "link", "set", "dev", interface, "up"],
            f"bringing {interface} up")
        run([IP_PATH, "link", "set", "dev", interface, "txqueuelen", "1000"],
            f"setting {interface} queue length")
        print(f"[ AMBER CAN ] {interface} mapped and up", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
