#!/usr/bin/env python3
"""Persistent Cerebro-to-Amber gateway.

Commands arrive over an authenticated, ordered TCP session. The gateway owns
the local UDP sockets used by the vendor Amber cores and subscribes to LCM arm
status. Mode changes use the vendor's bounded mode-control API; the gateway
never changes drive limits or writes controller configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import hmac
import json
import logging
import math
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL = "rob-amber-gateway/1"
JOINT_COUNT = 7
MAX_LINE_BYTES = 16_384
HEARTBEAT_TIMEOUT_S = 2.5
TELEMETRY_PERIOD_S = 0.05
MAX_TELEMETRY_AGE_S = 0.25
FRESH_TELEMETRY_TIMEOUT_S = 1.0
MODE_TRANSITION_TIMEOUT_S = 2.0
MODE_POLL_PERIOD_S = 0.05
HOLD_DURATION_S = 0.65
MODE_INACTIVE = 0
MODE_ACTIVE = 1
MODE_POSITION = 2
JOINT_LIMITS_RAD = (
    (-2.4435, 2.4435),
    (-2.3213, 2.3213),
    (-2.2863, 2.2863),
    (-2.2863, 2.2863),
    (-2.2863, 2.2863),
    (-2.2863, 2.2863),
    (-3.05, 3.05),
)
MODE_NAMES = {
    0: "inactive",
    1: "active",
    2: "position",
    3: "speed",
    4: "current",
}
LOGGER = logging.getLogger("rob-amber-gateway")


class JointCommand(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("positions", ctypes.c_float * 8),
        ("duration", ctypes.c_float),
    ]


class CommandResponse(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("respond", ctypes.c_uint8),
    ]


class ModeCommand(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
    ]


class ModeQuery(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("joint_id", ctypes.c_uint32),
    ]


class ModeQueryResponse(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("modes", ctypes.c_uint16 * JOINT_COUNT),
    ]


@dataclass(frozen=True)
class ArmConfig:
    name: str
    udp_port: int
    status_channel: str


@dataclass
class ArmState:
    positions: list[float] = field(default_factory=lambda: [0.0] * JOINT_COUNT)
    velocities: list[float] = field(default_factory=lambda: [0.0] * JOINT_COUNT)
    currents: list[float] = field(default_factory=lambda: [0.0] * JOINT_COUNT)
    statuses: list[float] = field(default_factory=lambda: [10.0] * JOINT_COUNT)
    monotonic_ns: int = 0
    sequence: int = 0


class AmberUDPTransport:
    def __init__(self, host: str, timeout: float = 0.5) -> None:
        self.host = host
        self.timeout = timeout
        self._locks = {"left": asyncio.Lock(), "right": asyncio.Lock()}

    async def move_joints(self, arm: ArmConfig, command_id: int,
                          positions: list[float], duration: float) -> int:
        async with self._locks[arm.name]:
            return await asyncio.to_thread(
                self._move_joints_blocking, arm, command_id, positions, duration
            )

    def _move_joints_blocking(self, arm: ArmConfig, command_id: int,
                              positions: list[float], duration: float) -> int:
        payload = JointCommand()
        payload.cmd_no = 4
        payload.length = ctypes.sizeof(JointCommand)
        payload.counter = command_id & 0xFFFFFFFF
        for index, value in enumerate(positions):
            payload.positions[index] = value
        payload.positions[7] = 0.0
        payload.duration = duration
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(self.timeout)
            sock.sendto(bytes(payload), (self.host, arm.udp_port))
            data, _ = sock.recvfrom(1024)
            if len(data) < ctypes.sizeof(CommandResponse):
                raise RuntimeError("short Amber response")
            response = CommandResponse.from_buffer_copy(data)
            if response.counter != payload.counter:
                raise RuntimeError("Amber response counter mismatch")
            return int(response.respond)
        finally:
            sock.close()

    async def set_mode(self, arm: ArmConfig, command_id: int, mode: int) -> int:
        async with self._locks[arm.name]:
            return await asyncio.to_thread(
                self._set_mode_blocking, arm, command_id, mode
            )

    def _set_mode_blocking(self, arm: ArmConfig, command_id: int,
                           mode: int) -> int:
        payload = ModeCommand()
        payload.cmd_no = 10
        payload.length = ctypes.sizeof(ModeCommand)
        payload.counter = command_id & 0xFFFFFFFF
        payload.mode = mode
        response = self._exchange(arm, payload, CommandResponse)
        return int(response.respond)

    async def get_modes(self, arm: ArmConfig, command_id: int) -> list[int]:
        async with self._locks[arm.name]:
            return await asyncio.to_thread(
                self._get_modes_blocking, arm, command_id
            )

    def _get_modes_blocking(self, arm: ArmConfig, command_id: int) -> list[int]:
        payload = ModeQuery()
        payload.cmd_no = 110
        payload.length = ctypes.sizeof(ModeQuery)
        payload.counter = command_id & 0xFFFFFFFF
        # The vendor command uses joint selector 8 to request all seven joints.
        payload.joint_id = 8
        response = self._exchange(arm, payload, ModeQueryResponse)
        return [int(value) for value in response.modes]

    def _exchange(self, arm: ArmConfig, payload: ctypes.Structure,
                  response_type: type[ctypes.Structure]) -> ctypes.Structure:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(self.timeout)
            sock.sendto(bytes(payload), (self.host, arm.udp_port))
            data, _ = sock.recvfrom(1024)
            if len(data) < ctypes.sizeof(response_type):
                raise RuntimeError("short Amber response")
            response = response_type.from_buffer_copy(data)
            if response.cmd_no != payload.cmd_no:
                raise RuntimeError("Amber response command mismatch")
            if response.counter != payload.counter:
                raise RuntimeError("Amber response counter mismatch")
            return response
        finally:
            sock.close()


class LCMStatusBridge:
    def __init__(self, module_root: Path, arms: dict[str, ArmConfig]) -> None:
        sys.path.insert(0, str(module_root))
        try:
            import lcm  # type: ignore
            from lcmTypes.armStatus_t import armStatus_t  # type: ignore
        except ImportError as error:
            raise RuntimeError(
                "LCM Python bindings or generated lcmTypes are unavailable"
            ) from error
        self._lcm = lcm.LCM()
        self._arm_status_type = armStatus_t
        self._states = {name: ArmState() for name in arms}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        for name, arm in arms.items():
            self._lcm.subscribe(arm.status_channel, self._handler(name))
        self._thread = threading.Thread(target=self._run, name="amber-lcm-status", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def snapshot(self) -> dict[str, ArmState]:
        with self._lock:
            return {
                name: ArmState(
                    positions=list(state.positions), velocities=list(state.velocities),
                    currents=list(state.currents), statuses=list(state.statuses),
                    monotonic_ns=state.monotonic_ns, sequence=state.sequence,
                )
                for name, state in self._states.items()
            }

    def _handler(self, arm_name: str):
        def handle(_channel: str, data: bytes) -> None:
            message = self._arm_status_type.decode(data)
            with self._lock:
                state = self._states[arm_name]
                state.positions = list(message.jointPosition)
                state.velocities = list(message.jointVelocity)
                state.currents = list(message.jointCurrent)
                state.statuses = list(message.jointStatus)
                state.monotonic_ns = time.monotonic_ns()
                state.sequence += 1
        return handle

    def _run(self) -> None:
        while not self._stop.is_set():
            self._lcm.handle_timeout(100)


class ClientSession:
    def __init__(self, server: "GatewayServer", reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter) -> None:
        self.server = server
        self.reader = reader
        self.writer = writer
        self.authenticated = False
        self.last_heartbeat = time.monotonic()
        self.last_command_id = 0
        self.telemetry_task: asyncio.Task[None] | None = None
        self.command_in_progress = False

    async def run(self) -> None:
        peer = self.writer.get_extra_info("peername")
        LOGGER.info("client connected: %s", peer)
        try:
            await self.send({"type": "challenge", "protocol": PROTOCOL,
                             "nonce": secrets.token_hex(16)})
            line = await asyncio.wait_for(self.reader.readline(), timeout=5)
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("message too large")
            hello = json.loads(line)
            if hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL:
                raise ValueError("invalid hello")
            supplied = str(hello.get("token", ""))
            if not hmac.compare_digest(supplied, self.server.token):
                raise PermissionError("authentication failed")
            if not self.server.claim_controller(self):
                raise PermissionError(
                    "another authenticated controller session is active"
                )
            self.authenticated = True
            self.last_heartbeat = time.monotonic()
            await self.send({"type": "ready", "protocol": PROTOCOL,
                             "heartbeat_timeout_s": HEARTBEAT_TIMEOUT_S,
                             "telemetry_hz": round(1 / TELEMETRY_PERIOD_S),
                             "exclusive_controller_session": True})
            self.telemetry_task = asyncio.create_task(self._telemetry_loop())
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("message too large")
                await self._handle(json.loads(line))
        except (asyncio.TimeoutError, ValueError, PermissionError, json.JSONDecodeError) as error:
            LOGGER.warning("client rejected: %s", error)
            with contextlib.suppress(Exception):
                await self.send({"type": "error", "error": str(error)})
        except Exception:
            LOGGER.exception("client session failed")
        finally:
            if self.telemetry_task:
                self.telemetry_task.cancel()
            if self.authenticated:
                self.server.release_controller(self)
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
            LOGGER.info("client disconnected: %s", peer)

    async def _handle(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "heartbeat":
            self.last_heartbeat = time.monotonic()
            await self.send({"type": "heartbeat_ack", "monotonic_ns": time.monotonic_ns()})
            return
        supported = {
            "mode_query", "activate", "position_mode", "hold_current",
            "deactivate", "trajectory",
        }
        if message_type not in supported:
            await self.send({"type": "command_error", "accepted": False,
                             "error": "unsupported message type"})
            return
        try:
            raw_command_id = message.get("command_id")
            if (isinstance(raw_command_id, bool) or
                    not isinstance(raw_command_id, int) or
                    not 1 <= raw_command_id <= 0xFFFFFFFF):
                raise ValueError("command_id must be an unsigned 32-bit integer")
            command_id = raw_command_id
            if command_id <= self.last_command_id:
                raise ValueError("command_id is stale or out of order")
            arm_name = message.get("arm")
            if arm_name not in self.server.arms:
                raise ValueError("unknown arm")
            if (message_type not in {"mode_query", "deactivate"} and
                    time.monotonic() - self.last_heartbeat > HEARTBEAT_TIMEOUT_S):
                raise ValueError("heartbeat expired")
        except (TypeError, ValueError) as error:
            await self.send({
                "type": f"{message_type}_ack", "accepted": False,
                "command_id": message.get("command_id"), "error": str(error),
            })
            return

        # Reserve before performing I/O. A timed-out or ambiguously acknowledged
        # hardware request must never be replayed with the same command ID.
        self.last_command_id = command_id
        started = time.monotonic_ns()
        self.command_in_progress = True
        try:
            result = await self.server.execute(
                message_type, command_id, arm_name, message
            )
            result.update({
                "type": f"{message_type}_ack", "command_id": command_id,
                "arm": arm_name, "accepted": True,
                "gateway_latency_ms":
                    (time.monotonic_ns() - started) / 1_000_000,
            })
        except (TypeError, ValueError, RuntimeError, OSError,
                asyncio.TimeoutError) as error:
            LOGGER.warning("%s command %d rejected: %s",
                           message_type, command_id, error)
            result = {
                "type": f"{message_type}_ack", "command_id": command_id,
                "arm": arm_name, "accepted": False, "error": str(error),
                "gateway_latency_ms":
                    (time.monotonic_ns() - started) / 1_000_000,
            }
        finally:
            self.command_in_progress = False
        await self.send(result)

    async def _telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(TELEMETRY_PERIOD_S)
            if (not self.command_in_progress and
                    time.monotonic() - self.last_heartbeat >
                    HEARTBEAT_TIMEOUT_S):
                await self.send({"type": "heartbeat_expired"})
                return
            now = time.monotonic_ns()
            for arm_name, state in self.server.status.snapshot().items():
                await self.send({
                    "type": "telemetry", "arm": arm_name,
                    "sequence": state.sequence, "gateway_monotonic_ns": now,
                    "sample_age_ms": None if not state.monotonic_ns else
                        (now - state.monotonic_ns) / 1_000_000,
                    "positions_rad": state.positions,
                    "velocities_rad_s": state.velocities,
                    "currents": state.currents, "statuses": state.statuses,
                })

    async def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        self.writer.write(payload)
        await self.writer.drain()


class GatewayServer:
    def __init__(self, token: str, transport: AmberUDPTransport,
                 status: LCMStatusBridge, arms: dict[str, ArmConfig]) -> None:
        self.token = token
        self.transport = transport
        self.status = status
        self.arms = arms
        self._operation_locks = {
            name: asyncio.Lock() for name in arms
        }
        self._udp_counter = secrets.randbits(31)
        self._controller_session: ClientSession | None = None

    async def client_connected(self, reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter) -> None:
        await ClientSession(self, reader, writer).run()

    def claim_controller(self, session: ClientSession) -> bool:
        if self._controller_session is not None:
            return False
        self._controller_session = session
        return True

    def release_controller(self, session: ClientSession) -> None:
        if self._controller_session is session:
            self._controller_session = None

    async def execute(self, operation: str, command_id: int, arm_name: str,
                      message: dict[str, Any]) -> dict[str, Any]:
        arm = self.arms[arm_name]
        async with self._operation_locks[arm_name]:
            if operation == "mode_query":
                modes = await self._query_modes(arm)
                return self._mode_result(modes)
            if operation == "activate":
                await self._fresh_state(arm_name, require_new=True)
                response, modes = await self._set_and_verify_mode(
                    arm, MODE_ACTIVE
                )
                return {"amber_response": response,
                        **self._mode_result(modes)}
            if operation == "position_mode":
                return await self._enter_position_mode(arm, arm_name)
            if operation == "hold_current":
                return await self._hold_current(arm, arm_name)
            if operation == "deactivate":
                response, modes = await self._set_and_verify_mode(
                    arm, MODE_INACTIVE
                )
                return {"amber_response": response,
                        **self._mode_result(modes)}
            if operation == "trajectory":
                return await self._trajectory(arm, arm_name, command_id, message)
        raise ValueError("unsupported operation")

    async def _trajectory(self, arm: ArmConfig, arm_name: str, command_id: int,
                          message: dict[str, Any]) -> dict[str, Any]:
        positions = message.get("positions_rad")
        duration_value = message.get("duration_s")
        if (not isinstance(positions, list) or len(positions) != JOINT_COUNT or
                not all(isinstance(value, (int, float)) and
                        not isinstance(value, bool) for value in positions)):
            raise ValueError("positions_rad must contain seven numbers")
        positions = [float(value) for value in positions]
        if not all(math.isfinite(value) for value in positions):
            raise ValueError("positions_rad values must be finite")
        for index, (value, limits) in enumerate(
                zip(positions, JOINT_LIMITS_RAD), start=1):
            lower, upper = limits
            if not lower <= value <= upper:
                raise ValueError(
                    f"joint {index} request {value} rad is outside "
                    f"{lower}...{upper} rad"
                )
        if (isinstance(duration_value, bool) or
                not isinstance(duration_value, (int, float))):
            raise ValueError("duration_s must be a number")
        duration = float(duration_value)
        if not math.isfinite(duration) or not 0.65 <= duration <= 10.0:
            raise ValueError("duration_s is outside 0.65...10.0")
        modes = await self._query_modes(arm)
        self._require_modes(modes, MODE_POSITION)
        await self._fresh_state(arm_name, require_new=False)
        response = await self.transport.move_joints(
            arm, command_id, positions, duration
        )
        if response != 1:
            raise RuntimeError(f"Amber rejected trajectory ({response})")
        return {
            "amber_response": response, "positions_rad": positions,
            "duration_s": duration, **self._mode_result(modes),
        }

    async def _enter_position_mode(self, arm: ArmConfig,
                                   arm_name: str) -> dict[str, Any]:
        active_response, active_modes = await self._set_and_verify_mode(
            arm, MODE_ACTIVE
        )
        captured = await self._fresh_state(arm_name, require_new=True)
        position_response, position_modes = await self._set_and_verify_mode(
            arm, MODE_POSITION
        )
        hold_response = await self.transport.move_joints(
            arm, self._next_udp_counter(), captured.positions, HOLD_DURATION_S
        )
        if hold_response != 1:
            raise RuntimeError(
                f"Amber rejected initial position hold ({hold_response})"
            )
        return {
            "amber_response": position_response,
            "active_amber_response": active_response,
            "hold_amber_response": hold_response,
            "captured_positions_rad": captured.positions,
            "hold_duration_s": HOLD_DURATION_S,
            "active_modes": active_modes,
            **self._mode_result(position_modes),
        }

    async def _hold_current(self, arm: ArmConfig,
                            arm_name: str) -> dict[str, Any]:
        modes = await self._query_modes(arm)
        self._require_modes(modes, MODE_POSITION)
        captured = await self._fresh_state(arm_name, require_new=True)
        response = await self.transport.move_joints(
            arm, self._next_udp_counter(), captured.positions, HOLD_DURATION_S
        )
        if response != 1:
            raise RuntimeError(f"Amber rejected position hold ({response})")
        return {
            "amber_response": response,
            "captured_positions_rad": captured.positions,
            "hold_duration_s": HOLD_DURATION_S,
            **self._mode_result(modes),
        }

    async def _set_and_verify_mode(self, arm: ArmConfig,
                                   expected: int) -> tuple[int, list[int]]:
        response = await self.transport.set_mode(
            arm, self._next_udp_counter(), expected
        )
        if response != 1:
            raise RuntimeError(
                f"Amber rejected {MODE_NAMES.get(expected, expected)} mode "
                f"request ({response})"
            )
        deadline = time.monotonic() + MODE_TRANSITION_TIMEOUT_S
        modes: list[int] = []
        while time.monotonic() < deadline:
            modes = await self._query_modes(arm)
            if all(mode == expected for mode in modes):
                return response, modes
            await asyncio.sleep(MODE_POLL_PERIOD_S)
        readable = ",".join(str(mode) for mode in modes) or "unavailable"
        raise RuntimeError(
            f"timed out verifying {MODE_NAMES.get(expected, expected)} mode; "
            f"reported [{readable}]"
        )

    async def _query_modes(self, arm: ArmConfig) -> list[int]:
        modes = await self.transport.get_modes(arm, self._next_udp_counter())
        if (not isinstance(modes, list) or len(modes) != JOINT_COUNT or
                not all(isinstance(mode, int) and not isinstance(mode, bool)
                        for mode in modes)):
            raise RuntimeError("Amber returned an invalid mode array")
        return modes

    @staticmethod
    def _require_modes(modes: list[int], expected: int) -> None:
        if not all(mode == expected for mode in modes):
            readable = ",".join(str(mode) for mode in modes)
            raise RuntimeError(
                f"arm is not in {MODE_NAMES.get(expected, expected)} mode; "
                f"reported [{readable}]"
            )

    async def _fresh_state(self, arm_name: str,
                           require_new: bool) -> ArmState:
        initial = self.status.snapshot().get(arm_name)
        initial_sequence = initial.sequence if initial else 0
        deadline = time.monotonic() + FRESH_TELEMETRY_TIMEOUT_S
        while time.monotonic() < deadline:
            state = self.status.snapshot().get(arm_name)
            if state and self._state_is_valid_and_fresh(state):
                if not require_new or state.sequence > initial_sequence:
                    return state
            await asyncio.sleep(TELEMETRY_PERIOD_S)
        qualifier = "new, " if require_new else ""
        raise RuntimeError(
            f"{qualifier}fresh telemetry is unavailable for {arm_name} arm"
        )

    @staticmethod
    def _state_is_valid_and_fresh(state: ArmState) -> bool:
        if state.monotonic_ns <= 0 or state.sequence <= 0:
            return False
        if time.monotonic_ns() - state.monotonic_ns > int(
                MAX_TELEMETRY_AGE_S * 1_000_000_000):
            return False
        vectors = (state.positions, state.velocities,
                   state.currents, state.statuses)
        return all(
            len(values) == JOINT_COUNT and
            all(isinstance(value, (int, float)) and
                not isinstance(value, bool) and math.isfinite(float(value))
                for value in values)
            for values in vectors
        )

    @staticmethod
    def _mode_result(modes: list[int]) -> dict[str, Any]:
        return {
            "modes": modes,
            "mode_names": [MODE_NAMES.get(mode, f"unknown({mode})")
                           for mode in modes],
        }

    def _next_udp_counter(self) -> int:
        self._udp_counter = (self._udp_counter + 1) & 0xFFFFFFFF
        if self._udp_counter == 0:
            self._udp_counter = 1
        return self._udp_counter


def read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("token file must contain at least 32 characters")
    return token


async def async_main(args: argparse.Namespace) -> None:
    arms = {
        "left": ArmConfig("left", args.left_udp_port, "Left_ArmStatus"),
        "right": ArmConfig("right", args.right_udp_port, "Right_ArmStatus"),
    }
    status = LCMStatusBridge(Path(args.lcm_types), arms)
    status.start()
    gateway = GatewayServer(read_token(Path(args.token_file)),
                            AmberUDPTransport(args.amber_host), status, arms)
    server = await asyncio.start_server(
        gateway.client_connected, args.listen_host, args.listen_port,
        limit=MAX_LINE_BYTES,
    )
    LOGGER.info("listening on %s:%d", args.listen_host, args.listen_port)
    try:
        async with server:
            await server.serve_forever()
    finally:
        status.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=7443)
    parser.add_argument("--amber-host", default="127.0.0.1")
    parser.add_argument("--left-udp-port", type=int, default=26001)
    parser.add_argument("--right-udp-port", type=int, default=26002)
    parser.add_argument("--token-file", default="/etc/rob-amber-gateway/token")
    parser.add_argument("--lcm-types", default="/home/amber/sin_wave")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
