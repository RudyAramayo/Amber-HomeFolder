#!/usr/bin/python3
"""Fail-closed recovery of ROB's Amber CAN/core/gateway stack.

This program is intentionally a fixed, no-argument maintenance operation.  It
does not import LCM, open UDP/CAN sockets, or issue any Amber mode, trajectory,
or motion command.  Its only mutations are bounded systemd stop/start/reset
operations for the two owned services.

Install this source root-owned as ``/usr/local/sbin/rob-amber-recover`` and
invoke it through the matching, no-argument sudoers rule.
"""

from __future__ import annotations

import fcntl
import glob
import hashlib
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, FrozenSet, Iterable, Protocol, Sequence, TextIO


PROTOCOL = "rob-amber-recovery/1"
OPERATION = "restart_can_core_gateway"
LOCK_PATH = "/run/rob-amber-recovery.lock"
GATEWAY_UNIT = "rob-amber-gateway.service"
CORE_UNIT = "rc-local.service"
GATEWAY_PORT = 7443
LEFT_PORT = 26001
RIGHT_PORT = 26002
WATCHED_PORTS = frozenset((GATEWAY_PORT, LEFT_PORT, RIGHT_PORT))
EXPECTED_SERIAL_BY_INTERFACE = {
    "can10": "209C36AB4B34",
    "can11": "206134725847",
}
MAPPING_PATH = "/etc/rob-amber-gateway/can-interfaces.json"
SAFE_RC_LOCAL_PATH = "/etc/rc.local"
SAFE_CAN_INIT_PATH = "/usr/local/libexec/rob-amber-init-can"
RECOVERY_INSTALL_PATH = "/usr/local/sbin/rob-amber-recover"
GATEWAY_UNIT_PATH = "/etc/systemd/system/rob-amber-gateway.service"
GATEWAY_UNIT_SHA256 = (
    "610a1f8af0eac51b87ea556746aa09147b61c905f27116f899ce99bb234051db"
)
SAFE_RC_LOCAL_MARKER = "ROB_AMBER_SAFE_RC_LOCAL=1"
MAXIMUM_JSON_LINE_BYTES = 2_048
MAXIMUM_DETAIL_CHARACTERS = 512


class RecoveryFailure(RuntimeError):
    """A classified, operator-actionable recovery failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class HostCommandFailure(RecoveryFailure):
    def __init__(self, operation: str) -> None:
        super().__init__(
            "host_command_failed",
            f"The fixed host command failed or timed out: {operation}.",
        )


@dataclass(frozen=True)
class UnitProperties:
    active_state: str
    sub_state: str
    main_pid: int
    restarts: int


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    arguments: tuple[str, ...]
    cgroup: str = ""
    uid: int = -1

    def names(self) -> set[str]:
        return {os.path.basename(argument) for argument in self.arguments}

    def is_named(self, name: str) -> bool:
        if not self.arguments:
            return False
        executable_name = os.path.basename(self.arguments[0])
        if name == "rob_amber_gateway.py":
            return executable_name.startswith("python") \
                and name in self.names()
        return executable_name == name


@dataclass(frozen=True)
class ListenerRecord:
    protocol: str
    address: str
    port: int
    pids: FrozenSet[int]


@dataclass(frozen=True)
class CANStatistics:
    flags: FrozenSet[str]
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int
    rx_dropped: int
    tx_dropped: int


@dataclass(frozen=True)
class RecoveryTimeouts:
    stop_seconds: float = 6.0
    core_start_seconds: float = 25.0
    gateway_start_seconds: float = 8.0
    can_sample_seconds: float = 1.0
    gateway_settle_seconds: float = 1.0
    poll_seconds: float = 0.20
    # Leave headroom beneath Cerebro's 75-second SSH timeout for a final
    # fail-closed rollback and one slow inspection command.
    overall_seconds: float = 50.0
    rollback_seconds: float = 4.0


class HostBackend(Protocol):
    def systemctl(self, action: str, unit: str) -> None: ...
    def unit_properties(self, unit: str) -> UnitProperties: ...
    def processes(self) -> list[ProcessRecord]: ...
    def listeners(self) -> list[ListenerRecord]: ...
    def configured_mapping(self) -> dict[str, str]: ...
    def validate_startup_trust(self) -> None: ...
    def validate_dependencies(self) -> None: ...
    def amber_uid(self) -> int: ...
    def usb_serials(self) -> dict[str, str]: ...
    def can_statistics(self, interface: str) -> CANStatistics: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class JSONLineEmitter:
    """Emits a small, stable machine-readable stream without command output."""

    def __init__(self, stream: TextIO, operation_id: str) -> None:
        self.stream = stream
        self.operation_id = operation_id

    @staticmethod
    def _bounded(value: object) -> object:
        if isinstance(value, str):
            cleaned = " ".join(value.replace("\x00", "").split())
            return cleaned[:MAXIMUM_DETAIL_CHARACTERS]
        if isinstance(value, dict):
            return {str(key)[:64]: JSONLineEmitter._bounded(item)
                    for key, item in list(value.items())[:32]}
        if isinstance(value, (list, tuple)):
            return [JSONLineEmitter._bounded(item) for item in value[:32]]
        return value

    def emit(self, record_type: str, **fields: object) -> None:
        record = {
            "protocol": PROTOCOL,
            "type": record_type,
            "operation": OPERATION,
            "operation_id": self.operation_id,
        }
        record.update({key: self._bounded(value) for key, value in fields.items()})
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > MAXIMUM_JSON_LINE_BYTES:
            record = {
                "protocol": PROTOCOL,
                "type": record_type,
                "operation": OPERATION,
                "operation_id": self.operation_id,
            }
            if record_type == "result":
                record.update({
                    "success": False,
                    "code": "output_limit_exceeded",
                    "detail": "Recovery output exceeded its safety bound.",
                })
            else:
                record.update({
                    "stage": "output_limit",
                    "message": "A recovery progress record exceeded its safety bound.",
                })
            encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        try:
            self.stream.write(encoded + "\n")
            self.stream.flush()
        except (BrokenPipeError, OSError):
            # An SSH disconnect must not abandon the host halfway through a
            # restart.  The operation continues to its fail-closed outcome.
            pass


class RealHostBackend:
    """Read host state and execute only the fixed systemd operations."""

    SYSTEMCTL = "/usr/bin/systemctl"
    IP = "/usr/sbin/ip"
    SS = "/usr/bin/ss"
    ALLOWED_ACTIONS = frozenset(("start", "stop", "reset-failed"))
    ALLOWED_UNITS = frozenset((GATEWAY_UNIT, CORE_UNIT))
    REQUIRED_EXECUTABLES = (
        SYSTEMCTL, IP, SS, "/usr/bin/slcand", "/usr/sbin/runuser",
        "/usr/bin/env", "/usr/bin/nohup", "/bin/sh", "/usr/bin/seq",
        "/usr/bin/sleep", "/home/amber/L-10/amber_core_L",
        "/home/amber/R-11/amber_core_R",
    )

    def _run(self, arguments: Sequence[str], operation: str,
             timeout: float = 40.0) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=timeout,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise HostCommandFailure(operation) from None
        if result.returncode != 0:
            raise HostCommandFailure(operation)
        return result

    def systemctl(self, action: str, unit: str) -> None:
        if action not in self.ALLOWED_ACTIONS or unit not in self.ALLOWED_UNITS:
            raise RecoveryFailure(
                "internal_allowlist_violation",
                "Recovery refused a systemd operation outside its fixed allowlist.",
            )
        # Keep global options before the verb so this remains valid across the
        # systemd versions used by the Amber image and newer development hosts.
        arguments = [self.SYSTEMCTL, "--no-ask-password"]
        if action in {"start", "stop"}:
            # Never let systemctl's job wait compete with the 75-second GUI
            # timeout. The recovery state machine owns every readiness bound.
            arguments.append("--no-block")
        arguments.extend((action, unit))
        self._run(
            arguments,
            f"systemctl {action} {unit}",
            timeout=5.0,
        )

    def unit_properties(self, unit: str) -> UnitProperties:
        if unit not in self.ALLOWED_UNITS:
            raise RecoveryFailure(
                "internal_allowlist_violation", "Unknown systemd unit requested."
            )
        result = self._run(
            (
                self.SYSTEMCTL, "show", unit, "--no-pager",
                "-p", "ActiveState", "-p", "SubState",
                "-p", "MainPID", "-p", "NRestarts",
            ),
            f"inspect {unit}",
            timeout=2.0,
        )
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        try:
            return UnitProperties(
                active_state=values["ActiveState"],
                sub_state=values["SubState"],
                main_pid=int(values["MainPID"]),
                restarts=int(values["NRestarts"]),
            )
        except (KeyError, ValueError):
            raise RecoveryFailure(
                "invalid_systemd_state",
                f"systemd returned incomplete state for {unit}.",
            ) from None

    def processes(self) -> list[ProcessRecord]:
        records: list[ProcessRecord] = []
        for proc_path in glob.glob("/proc/[0-9]*"):
            try:
                pid = int(os.path.basename(proc_path))
                raw = Path(proc_path, "cmdline").read_bytes()
                arguments = tuple(
                    part.decode("utf-8", errors="replace")
                    for part in raw.split(b"\x00") if part
                )
                cgroup = Path(proc_path, "cgroup").read_text(
                    encoding="utf-8", errors="replace"
                )
                uid = os.stat(proc_path).st_uid
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
            if arguments:
                records.append(ProcessRecord(pid, arguments, cgroup, uid))
        return records

    @staticmethod
    def _parse_endpoint(endpoint: str) -> tuple[str, int] | None:
        address, separator, raw_port = endpoint.rpartition(":")
        if not separator or not raw_port.isdigit():
            return None
        return address.strip("[]"), int(raw_port)

    def listeners(self) -> list[ListenerRecord]:
        result = self._run(
            (self.SS, "-H", "-ltnup"), "inspect TCP/UDP listeners", timeout=2.0
        )
        records: list[ListenerRecord] = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            endpoint = self._parse_endpoint(fields[4])
            if endpoint is None:
                continue
            address, port = endpoint
            pids = frozenset(int(value) for value in re.findall(r"pid=(\d+)", line))
            records.append(ListenerRecord(fields[0].lower(), address, port, pids))
        return records

    def configured_mapping(self) -> dict[str, str]:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                MAPPING_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or status.st_uid != 0 \
                    or status.st_mode & 0o022:
                raise OSError("untrusted mapping")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = None
                payload = json.load(stream)
        except (OSError, json.JSONDecodeError):
            raise RecoveryFailure(
                "mapping_unreadable", f"Cannot read {MAPPING_PATH}."
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise RecoveryFailure(
                "mapping_invalid", "The root-owned CAN serial mapping is malformed."
            )
        return dict(payload)

    @staticmethod
    def _require_root_owned_nonwritable(path: str) -> None:
        try:
            status = os.stat(path, follow_symlinks=False)
        except OSError:
            raise RecoveryFailure(
                "unsafe_startup_files", f"Required root-owned startup file is missing: {path}."
            ) from None
        if not stat.S_ISREG(status.st_mode) or status.st_uid != 0 \
                or status.st_mode & 0o022:
            raise RecoveryFailure(
                "unsafe_startup_files",
                f"Startup file is not root-owned and protected from writes: {path}.",
            )
        parent = Path(path).parent
        while str(parent) != "/":
            try:
                parent_status = os.stat(parent, follow_symlinks=False)
            except OSError:
                raise RecoveryFailure(
                    "unsafe_startup_files",
                    f"Startup parent directory is missing: {parent}.",
                ) from None
            if not stat.S_ISDIR(parent_status.st_mode) or parent_status.st_uid != 0 \
                    or parent_status.st_mode & 0o022:
                raise RecoveryFailure(
                    "unsafe_startup_files",
                    f"Startup parent directory is not protected from writes: {parent}.",
                )
            parent = parent.parent

    def validate_startup_trust(self) -> None:
        for path in (
            RECOVERY_INSTALL_PATH, SAFE_RC_LOCAL_PATH, SAFE_CAN_INIT_PATH,
            MAPPING_PATH, GATEWAY_UNIT_PATH,
        ):
            self._require_root_owned_nonwritable(path)
        try:
            rc_local = Path(SAFE_RC_LOCAL_PATH).read_text(encoding="utf-8")
        except OSError:
            raise RecoveryFailure(
                "unsafe_startup_files", "The reviewed rc.local cannot be read."
            ) from None
        if SAFE_RC_LOCAL_MARKER not in rc_local \
                or SAFE_CAN_INIT_PATH not in rc_local \
                or "/usr/sbin/runuser -u amber" not in rc_local:
            raise RecoveryFailure(
                "unsafe_startup_files",
                "rc.local is not the reviewed least-privilege Amber startup program.",
            )

        try:
            unit_bytes = Path(GATEWAY_UNIT_PATH).read_bytes()
        except OSError:
            raise RecoveryFailure(
                "unsafe_startup_files", "The reviewed gateway unit cannot be read."
            ) from None
        if hashlib.sha256(unit_bytes).hexdigest() != GATEWAY_UNIT_SHA256:
            raise RecoveryFailure(
                "unsafe_startup_files",
                "The gateway unit does not match the reviewed least-privilege unit.",
            )
        self._validate_effective_gateway_unit()

    def _validate_effective_gateway_unit(self) -> None:
        properties = (
            "FragmentPath", "DropInPaths", "User", "Group", "ExecStart",
            "ExecStartPre", "ExecStartPost", "ExecCondition", "ExecReload",
            "ExecStop", "ExecStopPost",
        )
        arguments: list[str] = [self.SYSTEMCTL, "show", GATEWAY_UNIT, "--no-pager"]
        for property_name in properties:
            arguments.extend(("-p", property_name))
        result = self._run(
            arguments, "inspect effective gateway unit", timeout=2.0
        )
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value

        expected_argv = (
            "/usr/bin/python3 /home/amber/rob_gateway/rob_amber_gateway.py "
            "--listen-host 127.0.0.1"
        )
        argv_match = re.search(r"argv\[\]=([^;]+)", values.get("ExecStart", ""))
        hooks = (
            "ExecStartPre", "ExecStartPost", "ExecCondition", "ExecReload",
            "ExecStop", "ExecStopPost",
        )
        safe = (
            values.get("FragmentPath") == GATEWAY_UNIT_PATH
            and not values.get("DropInPaths")
            and values.get("User") == "amber"
            and values.get("Group") == "amber"
            and argv_match is not None
            and argv_match.group(1).strip() == expected_argv
            and "path=/usr/bin/python3" in values.get("ExecStart", "")
            and "flags=" not in values.get("ExecStart", "")
            and all(not values.get(name) for name in hooks)
        )
        if not safe:
            raise RecoveryFailure(
                "unsafe_gateway_unit",
                "The effective gateway unit is not the reviewed amber-user service.",
            )

    def validate_dependencies(self) -> None:
        missing: list[str] = []
        for path in self.REQUIRED_EXECUTABLES:
            try:
                status = os.stat(path)
            except OSError:
                missing.append(path)
                continue
            if not stat.S_ISREG(status.st_mode) or not os.access(path, os.X_OK):
                missing.append(path)
        if missing:
            raise RecoveryFailure(
                "dependency_missing",
                "A required reviewed recovery dependency is missing or not executable: "
                + ", ".join(missing[:4]) + ".",
            )

    @staticmethod
    def amber_uid() -> int:
        try:
            return pwd.getpwnam("amber").pw_uid
        except KeyError:
            raise RecoveryFailure(
                "amber_account_missing", "The required amber service account is missing."
            ) from None

    @staticmethod
    def _serial_for_tty(device: str) -> str | None:
        tty = os.path.basename(device)
        path = Path("/sys/class/tty", tty, "device")
        try:
            current = path.resolve(strict=True)
        except OSError:
            return None
        for candidate in (current, *current.parents):
            serial_path = candidate / "serial"
            try:
                serial = serial_path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if serial:
                return serial
        return None

    def usb_serials(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for device in sorted(glob.glob("/dev/ttyACM*")):
            serial = self._serial_for_tty(device)
            if serial:
                result[device] = serial
        return result

    def can_statistics(self, interface: str) -> CANStatistics:
        if interface not in EXPECTED_SERIAL_BY_INTERFACE:
            raise RecoveryFailure(
                "internal_allowlist_violation", "Unknown CAN interface requested."
            )
        result = self._run(
            (self.IP, "-j", "-s", "-d", "link", "show", "dev", interface),
            f"inspect {interface}",
            timeout=2.0,
        )
        try:
            payload = json.loads(result.stdout)
            link = payload[0]
            receive = link["stats64"]["rx"]
            transmit = link["stats64"]["tx"]
            return CANStatistics(
                flags=frozenset(link["flags"]),
                rx_packets=int(receive["packets"]),
                tx_packets=int(transmit["packets"]),
                rx_errors=int(receive["errors"]),
                tx_errors=int(transmit["errors"]),
                rx_dropped=int(receive["dropped"]),
                tx_dropped=int(transmit["dropped"]),
            )
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RecoveryFailure(
                "invalid_can_state", f"Could not decode state for {interface}."
            ) from None

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


class RecoveryController:
    def __init__(self, backend: HostBackend, emitter: JSONLineEmitter,
                 timeouts: RecoveryTimeouts = RecoveryTimeouts()) -> None:
        self.backend = backend
        self.emitter = emitter
        self.timeouts = timeouts
        self.core_started = False
        self.overall_deadline: float | None = None

    @staticmethod
    def _relevant_processes(processes: Iterable[ProcessRecord]) -> list[ProcessRecord]:
        names = {"rob_amber_gateway.py", "slcand", "amber_core_L", "amber_core_R"}
        return [process for process in processes if process.names() & names]

    def _wait_for(self, check: Callable[[], tuple[bool, str]], timeout: float,
                  code: str) -> None:
        deadline = self.backend.monotonic() + timeout
        if self.overall_deadline is not None:
            deadline = min(deadline, self.overall_deadline)
        last_detail = "readiness condition was not met"
        while True:
            ready, detail = check()
            if ready:
                return
            last_detail = detail
            if self.backend.monotonic() >= deadline:
                raise RecoveryFailure(code, last_detail)
            self.backend.sleep(self.timeouts.poll_seconds)

    def _gateway_is_stopped(self) -> tuple[bool, str]:
        unit = self.backend.unit_properties(GATEWAY_UNIT)
        processes = [process for process in self.backend.processes()
                     if process.is_named("rob_amber_gateway.py")]
        listeners = [listener for listener in self.backend.listeners()
                     if listener.port == GATEWAY_PORT]
        stopped = unit.active_state not in {"active", "activating", "reloading"} \
            and unit.main_pid == 0 and not processes and not listeners
        return stopped, "Gateway process or TCP 7443 listener remained after stop."

    def _all_components_are_stopped(self) -> tuple[bool, str]:
        processes = self._relevant_processes(self.backend.processes())
        listeners = [listener for listener in self.backend.listeners()
                     if listener.port in WATCHED_PORTS]
        core = self.backend.unit_properties(CORE_UNIT)
        gateway = self.backend.unit_properties(GATEWAY_UNIT)
        inactive = all(unit.active_state not in {"active", "activating", "reloading"}
                       for unit in (core, gateway))
        stopped = inactive and gateway.main_pid == 0 and not processes and not listeners
        return stopped, (
            "A gateway, slcand, Amber core process, or watched listener remained "
            "after the ordered stop. No restart was attempted."
        )

    def _verify_expected_usb_adapters(self) -> None:
        configured = self.backend.configured_mapping()
        if configured != EXPECTED_SERIAL_BY_INTERFACE:
            raise RecoveryFailure(
                "mapping_mismatch",
                "The installed CAN serial mapping does not match the reviewed can10/can11 mapping.",
            )
        serials = list(self.backend.usb_serials().values())
        for interface, expected_serial in EXPECTED_SERIAL_BY_INTERFACE.items():
            if serials.count(expected_serial) != 1:
                raise RecoveryFailure(
                    "usb_adapter_missing",
                    f"Expected exactly one USB-CAN adapter for {interface}; recovery was not started.",
                )

    @staticmethod
    def _device_and_interface(process: ProcessRecord) -> tuple[str | None, str | None]:
        device = next((argument for argument in process.arguments
                       if argument.startswith("/dev/ttyACM")), None)
        interface = next((argument for argument in process.arguments
                          if argument in EXPECTED_SERIAL_BY_INTERFACE), None)
        return device, interface

    def _core_stack_is_ready(self) -> tuple[bool, str]:
        unit = self.backend.unit_properties(CORE_UNIT)
        if unit.active_state != "active":
            return False, "rc-local.service did not become active."

        processes = self.backend.processes()
        slcand = [process for process in processes if process.is_named("slcand")]
        left = [process for process in processes if process.is_named("amber_core_L")]
        right = [process for process in processes if process.is_named("amber_core_R")]
        if len(slcand) != 2 or len(left) != 1 or len(right) != 1:
            return False, "Expected exactly two slcand and one left/right Amber core process."
        owned = slcand + left + right
        if any("/system.slice/rc-local.service" not in process.cgroup for process in owned):
            return False, "A CAN/core process is not owned by rc-local.service."
        amber_uid = self.backend.amber_uid()
        if any(process.uid != 0 for process in slcand):
            return False, "A slcand process is not owned by root."
        if left[0].uid != amber_uid or right[0].uid != amber_uid:
            return False, "An Amber core is not running as the amber account."

        usb = self.backend.usb_serials()
        seen_interfaces: set[str] = set()
        seen_devices: set[str] = set()
        for process in slcand:
            device, interface = self._device_and_interface(process)
            if device is None or interface is None or "-s8" not in process.arguments:
                return False, "A slcand process has an unexpected command line."
            if device in seen_devices or interface in seen_interfaces:
                return False, "Duplicate slcand device or interface ownership was detected."
            if usb.get(device) != EXPECTED_SERIAL_BY_INTERFACE[interface]:
                return False, f"The USB-CAN serial bound to {interface} is incorrect."
            seen_devices.add(device)
            seen_interfaces.add(interface)
        if seen_interfaces != set(EXPECTED_SERIAL_BY_INTERFACE):
            return False, "slcand did not create exactly can10 and can11."

        for interface in EXPECTED_SERIAL_BY_INTERFACE:
            statistics = self.backend.can_statistics(interface)
            if not {"UP", "LOWER_UP"}.issubset(statistics.flags):
                return False, f"{interface} is not UP and LOWER_UP."

        listeners = self.backend.listeners()
        udp_by_port = {
            port: [listener for listener in listeners
                   if listener.protocol == "udp" and listener.port == port]
            for port in (LEFT_PORT, RIGHT_PORT)
        }
        if len(udp_by_port[LEFT_PORT]) != 1 or len(udp_by_port[RIGHT_PORT]) != 1:
            return False, "Expected exactly one UDP listener on each Amber core port."
        if left[0].pid not in udp_by_port[LEFT_PORT][0].pids:
            return False, "UDP 26001 is not owned by amber_core_L."
        if right[0].pid not in udp_by_port[RIGHT_PORT][0].pids:
            return False, "UDP 26002 is not owned by amber_core_R."
        return True, "Amber core stack is ready."

    def _verify_advancing_clean_can(self) -> dict[str, dict[str, int]]:
        before = {interface: self.backend.can_statistics(interface)
                  for interface in EXPECTED_SERIAL_BY_INTERFACE}
        self.backend.sleep(self.timeouts.can_sample_seconds)
        after = {interface: self.backend.can_statistics(interface)
                 for interface in EXPECTED_SERIAL_BY_INTERFACE}
        summary: dict[str, dict[str, int]] = {}
        for interface in EXPECTED_SERIAL_BY_INTERFACE:
            first = before[interface]
            second = after[interface]
            if not {"UP", "LOWER_UP"}.issubset(second.flags):
                raise RecoveryFailure(
                    "can_link_not_ready", f"{interface} lost UP or LOWER_UP during sampling."
                )
            if any((second.rx_errors, second.tx_errors,
                    second.rx_dropped, second.tx_dropped)):
                raise RecoveryFailure(
                    "can_errors", f"{interface} reports CAN errors or dropped packets."
                )
            rx_delta = second.rx_packets - first.rx_packets
            tx_delta = second.tx_packets - first.tx_packets
            if rx_delta <= 0 or tx_delta <= 0:
                raise RecoveryFailure(
                    "can_not_advancing",
                    f"{interface} did not advance both RX and TX packet counters.",
                )
            summary[interface] = {"rx_delta": rx_delta, "tx_delta": tx_delta}
        return summary

    def _gateway_is_ready(self) -> tuple[bool, str]:
        unit = self.backend.unit_properties(GATEWAY_UNIT)
        gateways = [process for process in self.backend.processes()
                    if process.is_named("rob_amber_gateway.py")]
        listeners = [listener for listener in self.backend.listeners()
                     if listener.protocol == "tcp" and listener.port == GATEWAY_PORT]
        if unit.active_state != "active" or unit.sub_state != "running":
            return False, "The gateway service did not become active/running."
        if unit.main_pid <= 0 or unit.restarts != 0:
            return False, "The gateway MainPID is invalid or NRestarts is not zero."
        if len(gateways) != 1 or gateways[0].pid != unit.main_pid:
            return False, "The gateway MainPID does not own the only gateway process."
        if gateways[0].uid != self.backend.amber_uid():
            return False, "The gateway process is not running as the amber account."
        if "/system.slice/rob-amber-gateway.service" not in gateways[0].cgroup:
            return False, "The gateway process is not owned by its systemd unit."
        if len(listeners) != 1 or listeners[0].address != "127.0.0.1":
            return False, "TCP 7443 is not bound exactly once to IPv4 loopback."
        if listeners[0].pids != frozenset((unit.main_pid,)):
            return False, "The gateway MainPID does not exclusively own TCP 7443."
        return True, "Gateway is ready."

    def _rollback(self) -> tuple[bool, bool]:
        gateway_stopped = False
        core_stopped = not self.core_started
        # A phase timeout must still reserve a bounded window for stop jobs.
        self.overall_deadline = (
            self.backend.monotonic() + 2 * self.timeouts.rollback_seconds
        )
        try:
            self.backend.systemctl("stop", GATEWAY_UNIT)
            self._wait_for(
                self._gateway_is_stopped, self.timeouts.rollback_seconds,
                "rollback_gateway_stop_incomplete",
            )
            gateway_stopped = True
        except Exception:
            gateway_stopped = False
        if self.core_started:
            try:
                self.backend.systemctl("stop", CORE_UNIT)
                self._wait_for(
                    self._all_components_are_stopped,
                    self.timeouts.rollback_seconds,
                    "rollback_stack_stop_incomplete",
                )
                core_stopped = True
            except Exception:
                core_stopped = False
        return gateway_stopped, core_stopped

    def _report_failure(self, failure: RecoveryFailure,
                        started_at: float) -> bool:
        self.emitter.emit(
            "progress", stage="rollback", code=failure.code,
            message=failure.detail,
        )
        gateway_stopped, core_stopped = self._rollback()
        self.emitter.emit(
            "result", success=False, code=failure.code, detail=failure.detail,
            gateway_stopped=gateway_stopped,
            restarted_core_stack_stopped=core_stopped,
            elapsed_ms=round((self.backend.monotonic() - started_at) * 1_000),
        )
        return False

    def _report_preflight_failure(self, failure: RecoveryFailure,
                                  started_at: float) -> bool:
        self.emitter.emit(
            "result", success=False, code=failure.code, detail=failure.detail,
            recovery_started=False,
            elapsed_ms=round((self.backend.monotonic() - started_at) * 1_000),
        )
        return False

    def run(self) -> bool:
        started_at = self.backend.monotonic()
        self.overall_deadline = started_at + self.timeouts.overall_seconds
        self.emitter.emit(
            "progress", stage="starting",
            message="Beginning ordered, fail-closed recovery."
        )
        try:
            # Reject an incomplete or unsafe installation without interrupting
            # an otherwise healthy running controller stack.
            self.backend.validate_startup_trust()
            self.backend.validate_dependencies()
            self._verify_expected_usb_adapters()
        except RecoveryFailure as failure:
            return self._report_preflight_failure(failure, started_at)
        except Exception:
            return self._report_preflight_failure(
                RecoveryFailure(
                    "internal_error",
                    "Recovery preflight encountered an unexpected internal error.",
                ),
                started_at,
            )

        try:
            self.emitter.emit(
                "progress", stage="stopping_gateway",
                message="Stopping the gateway before the CAN/core stack."
            )
            self.backend.systemctl("stop", GATEWAY_UNIT)
            self._wait_for(
                self._gateway_is_stopped, self.timeouts.stop_seconds,
                "gateway_stop_incomplete",
            )

            self.emitter.emit(
                "progress", stage="stopping_can_and_cores",
                message="Stopping rc-local.service and verifying a clean baseline."
            )
            self.backend.systemctl("stop", CORE_UNIT)
            self._wait_for(
                self._all_components_are_stopped, self.timeouts.stop_seconds,
                "stack_stop_incomplete",
            )

            # Recheck hot-pluggable adapters and protected startup inputs after
            # the clean stop, immediately before the one allowed start.
            self.backend.validate_startup_trust()
            self.backend.validate_dependencies()
            self._verify_expected_usb_adapters()
            self.emitter.emit(
                "progress", stage="starting_can_and_cores",
                message="Starting rc-local.service exactly once."
            )
            self.core_started = True
            self.backend.systemctl("start", CORE_UNIT)
            self._wait_for(
                self._core_stack_is_ready, self.timeouts.core_start_seconds,
                "core_start_incomplete",
            )

            self.emitter.emit(
                "progress", stage="sampling_can_activity",
                message="Checking clean, advancing RX and TX counters."
            )
            deltas = self._verify_advancing_clean_can()
            self.emitter.emit(
                "progress", stage="can_verified",
                message="Both CAN interfaces have advancing RX/TX with no errors.",
                can=deltas,
            )

            self.emitter.emit(
                "progress", stage="starting_gateway",
                message="Starting and verifying the loopback-only gateway."
            )
            self.backend.systemctl("reset-failed", GATEWAY_UNIT)
            self.backend.systemctl("start", GATEWAY_UNIT)
            self._wait_for(
                self._gateway_is_ready, self.timeouts.gateway_start_seconds,
                "gateway_start_incomplete",
            )
            self.backend.sleep(self.timeouts.gateway_settle_seconds)
            ready_after_settle, detail_after_settle = self._gateway_is_ready()
            if not ready_after_settle:
                raise RecoveryFailure(
                    "gateway_restart_loop",
                    "Gateway failed its post-start settle check: " + detail_after_settle,
                )

            self.emitter.emit(
                "result", success=True, code="ok",
                elapsed_ms=round((self.backend.monotonic() - started_at) * 1_000),
                detail=(
                    "CAN adapters, both Amber cores, UDP listeners, and the "
                    "loopback-only gateway passed recovery verification."
                ),
            )
            return True
        except RecoveryFailure as failure:
            return self._report_failure(failure, started_at)
        except Exception:
            return self._report_failure(
                RecoveryFailure(
                    "internal_error",
                    "Recovery encountered an unexpected internal error and failed closed.",
                ),
                started_at,
            )


class RecoveryLock:
    def __init__(self, path: str = LOCK_PATH) -> None:
        self.path = path
        self.descriptor: int | None = None

    def acquire(self) -> bool:
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            status = os.fstat(descriptor)
        except OSError:
            raise RecoveryFailure(
                "unsafe_lock_file", "The protected recovery lock could not be opened."
            ) from None
        if not stat.S_ISREG(status.st_mode) or status.st_uid != os.geteuid() \
                or status.st_mode & 0o077:
            os.close(descriptor)
            raise RecoveryFailure(
                "unsafe_lock_file", "The protected recovery lock has unsafe metadata."
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        self.descriptor = descriptor
        return True

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def __enter__(self) -> "RecoveryLock":
        if not self.acquire():
            raise RecoveryFailure(
                "recovery_busy", "Another Amber recovery operation is already running."
            )
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _single_result(stream: TextIO, operation_id: str, code: str,
                   detail: str) -> None:
    JSONLineEmitter(stream, operation_id).emit(
        "result", success=False, code=code, detail=detail
    )


def main(argv: Sequence[str] | None = None, stream: TextIO = sys.stdout) -> int:
    arguments = list(sys.argv if argv is None else argv)
    operation_id = str(uuid.uuid4())
    if len(arguments) != 1:
        _single_result(
            stream, operation_id, "arguments_forbidden",
            "This privileged recovery helper accepts no command-line arguments.",
        )
        return 64
    if os.geteuid() != 0:
        _single_result(
            stream, operation_id, "root_required",
            "Invoke the recovery helper through its exact sudoers rule.",
        )
        return 77

    # Keep a dropped SSH connection from interrupting the host between stop and
    # verification. JSON writes also tolerate a closed transport.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    emitter = JSONLineEmitter(stream, operation_id)
    lock = RecoveryLock()
    try:
        acquired = lock.acquire()
    except RecoveryFailure as failure:
        emitter.emit(
            "result", success=False, code=failure.code, detail=failure.detail
        )
        return 73
    if not acquired:
        emitter.emit(
            "result", success=False, code="recovery_busy",
            detail="Another Amber recovery operation is already running.",
        )
        return 75
    try:
        return 0 if RecoveryController(RealHostBackend(), emitter).run() else 70
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
