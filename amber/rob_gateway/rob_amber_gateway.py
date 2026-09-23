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
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL = "rob-amber-gateway/1"
SUPPORTED_COMMANDS = frozenset({
    "mode_query", "activate", "position_mode", "hold_current",
    "deactivate", "trajectory", "leased_trajectory",
    "renew_lease", "priority_hold",
    "gripper_state", "gripper_calibrate", "gripper_control",
})
JOINT_COUNT = 7
MAX_LINE_BYTES = 16_384
MAX_PENDING_COMMANDS_PER_ARM = 32
HEARTBEAT_TIMEOUT_S = 2.5
TELEMETRY_PERIOD_S = 0.05
MAX_TELEMETRY_AGE_S = 0.25
FRESH_TELEMETRY_TIMEOUT_S = 1.0
MODE_TRANSITION_TIMEOUT_S = 2.0
MODE_POLL_PERIOD_S = 0.05
HOLD_DURATION_S = 0.65
MIN_LEASE_MS = 700
MAX_LEASE_MS = 1500
GRIPPER_NUMBER = 8
MIN_GRIPPER_FORCE = 1
MAX_GRIPPER_FORCE = 300
GRIPPER_ACTION_RELEASE = 0
GRIPPER_ACTION_HOLD = 1
GRIPPER_CALIBRATION_REQUIRED = "required"
GRIPPER_CALIBRATION_ACCEPTED_UNVERIFIED = "command_accepted_unverified"
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


class GripperCalibrationCommand(ctypes.LittleEndianStructure):
    """Vendor UDP command 7 with the gripper's actuator selector."""

    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("joint_id", ctypes.c_uint32),
    ]


class GripperCommand(ctypes.LittleEndianStructure):
    """Vendor UDP command 9 (release/hold plus opaque force intensity)."""

    _pack_ = 1
    _fields_ = [
        ("cmd_no", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("counter", ctypes.c_uint32),
        ("action", ctypes.c_uint16),
        ("intensity", ctypes.c_uint16),
        ("version", ctypes.c_bool),
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


@dataclass(frozen=True)
class ArmLease:
    generation: int
    deadline: float
    task: asyncio.Task[None]


@dataclass(frozen=True)
class ArmHold:
    generation: int
    task: asyncio.Task[dict[str, Any]]


@dataclass(frozen=True)
class QueuedCommand:
    operation: str
    command_id: int
    arm_name: str
    message: dict[str, Any]
    started_ns: int
    motion_authority_generation: int


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

    async def calibrate_gripper(self, arm: ArmConfig,
                                command_id: int) -> int:
        async with self._locks[arm.name]:
            return await asyncio.to_thread(
                self._calibrate_gripper_blocking, arm, command_id
            )

    def _calibrate_gripper_blocking(self, arm: ArmConfig,
                                    command_id: int) -> int:
        payload = GripperCalibrationCommand()
        payload.cmd_no = 7
        payload.length = ctypes.sizeof(GripperCalibrationCommand)
        payload.counter = command_id & 0xFFFFFFFF
        payload.joint_id = GRIPPER_NUMBER
        response = self._exchange(arm, payload, CommandResponse)
        return int(response.respond)

    async def control_gripper(self, arm: ArmConfig, command_id: int,
                              action: int, force: int) -> int:
        async with self._locks[arm.name]:
            return await asyncio.to_thread(
                self._control_gripper_blocking,
                arm, command_id, action, force,
            )

    def _control_gripper_blocking(self, arm: ArmConfig, command_id: int,
                                  action: int, force: int) -> int:
        payload = GripperCommand()
        payload.cmd_no = 9
        payload.length = ctypes.sizeof(GripperCommand)
        payload.counter = command_id & 0xFFFFFFFF
        payload.action = action
        payload.intensity = force
        payload.version = False
        response = self._exchange(arm, payload, CommandResponse)
        return int(response.respond)

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
        self.heartbeat_expired = False
        self.motion_authority_generation = 0
        self.last_command_id = 0
        self.telemetry_task: asyncio.Task[None] | None = None
        self.controller_generation: int | None = None
        self.command_queues: dict[
            str, asyncio.Queue[QueuedCommand | None]
        ] = {
            arm_name: asyncio.Queue(MAX_PENDING_COMMANDS_PER_ARM)
            for arm_name in server.arms
        }
        self.command_tasks: dict[str, asyncio.Task[None]] = {}
        self.closing = False
        self._send_lock = asyncio.Lock()

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
            controller_generation = self.server.claim_controller(self)
            if controller_generation is None:
                raise PermissionError(
                    "another authenticated controller session is active"
                )
            self.authenticated = True
            self.controller_generation = controller_generation
            self.last_heartbeat = time.monotonic()
            await self.send({"type": "ready", "protocol": PROTOCOL,
                             "heartbeat_timeout_s": HEARTBEAT_TIMEOUT_S,
                             "telemetry_hz": round(1 / TELEMETRY_PERIOD_S),
                             "supported_commands": sorted(SUPPORTED_COMMANDS),
                             "exclusive_controller_session": True})
            self._start_command_workers()
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
        except ConnectionError as error:
            LOGGER.info("client connection ended: %s", error)
        except Exception:
            LOGGER.exception("client session failed")
        finally:
            self.closing = True
            try:
                if self.telemetry_task:
                    self.telemetry_task.cancel()
                    with contextlib.suppress(
                            asyncio.CancelledError, Exception):
                        await self.telemetry_task
                if self.authenticated:
                    await self.server.release_controller(
                        self, self._stop_command_workers()
                    )
                else:
                    await self._stop_command_workers()
            finally:
                self.writer.close()
                with contextlib.suppress(Exception):
                    await self.writer.wait_closed()
                LOGGER.info("client disconnected: %s", peer)

    async def _handle(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "heartbeat":
            received = time.monotonic()
            if received - self.last_heartbeat > HEARTBEAT_TIMEOUT_S:
                await self._expire_heartbeat_authority()
            self.last_heartbeat = received
            self.heartbeat_expired = False
            await self.send({"type": "heartbeat_ack", "monotonic_ns": time.monotonic_ns()})
            return
        if message_type not in SUPPORTED_COMMANDS:
            await self.send({"type": "command_error", "accepted": False,
                             "command_type": message_type,
                             "error": "unsupported message type"})
            return
        started = time.monotonic_ns()
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
            if (message_type not in {
                    "mode_query", "deactivate", "priority_hold",
                    "gripper_state",
                    } and
                    time.monotonic() - self.last_heartbeat > HEARTBEAT_TIMEOUT_S):
                raise ValueError("heartbeat expired")
        except (TypeError, ValueError) as error:
            result = {
                "type": f"{message_type}_ack", "accepted": False,
                "command_id": message.get("command_id"), "error": str(error),
                "gateway_latency_ms":
                    (time.monotonic_ns() - started) / 1_000_000,
            }
            if isinstance(message.get("arm"), str):
                result["arm"] = message["arm"]
            await self.send(result)
            return

        # Reserve before performing I/O. A timed-out or ambiguously acknowledged
        # hardware request must never be replayed with the same command ID.
        self.last_command_id = command_id
        command = QueuedCommand(
            message_type, command_id, arm_name, message, started,
            self.motion_authority_generation,
        )
        superseded: list[QueuedCommand] = []
        if message_type == "priority_hold":
            superseded = self._purge_queued_commands(arm_name)
        try:
            self.command_queues[arm_name].put_nowait(command)
        except asyncio.QueueFull:
            await self.send({
                "type": f"{message_type}_ack",
                "command_id": command_id,
                "arm": arm_name,
                "accepted": False,
                "error": f"{arm_name} arm command queue is full",
                "gateway_latency_ms":
                    (time.monotonic_ns() - started) / 1_000_000,
            })
            return
        if superseded:
            await self._send_superseded_responses(
                superseded, command_id
            )

    def _purge_queued_commands(self, arm_name: str) -> list[QueuedCommand]:
        queue = self.command_queues[arm_name]
        superseded: list[QueuedCommand] = []
        while True:
            try:
                command = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            queue.task_done()
            if command is not None:
                superseded.append(command)
        return superseded

    async def _send_superseded_responses(
            self, commands: list[QueuedCommand],
            priority_command_id: int) -> None:
        for command in commands:
            if self.closing:
                return
            try:
                await self.send({
                    "type": f"{command.operation}_ack",
                    "command_id": command.command_id,
                    "arm": command.arm_name,
                    "accepted": False,
                    "error": (
                        "superseded by priority_hold command "
                        f"{priority_command_id}"
                    ),
                    "gateway_latency_ms": (
                        time.monotonic_ns() - command.started_ns
                    ) / 1_000_000,
                })
            except (ConnectionError, OSError):
                return

    def _start_command_workers(self) -> None:
        for arm_name in self.server.arms:
            self.command_tasks[arm_name] = asyncio.create_task(
                self._arm_worker(arm_name),
                name=f"amber-client-{arm_name}-commands",
            )

    async def _stop_command_workers(self) -> None:
        if not self.command_tasks:
            return
        for queue in self.command_queues.values():
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    queue.task_done()
            queue.put_nowait(None)
        workers = asyncio.gather(
            *self.command_tasks.values(), return_exceptions=True
        )
        cancelled = False
        try:
            results = await asyncio.shield(workers)
        except asyncio.CancelledError:
            # Do not release a per-arm lock by cancelling an asyncio.to_thread
            # wrapper while its blocking Amber exchange can still complete.
            results = await workers
            cancelled = True
        for arm_name, result in zip(self.command_tasks, results):
            if (isinstance(result, BaseException) and
                    not isinstance(result, asyncio.CancelledError)):
                LOGGER.error("%s arm command worker failed: %s",
                             arm_name, result)
        self.command_tasks.clear()
        if cancelled:
            raise asyncio.CancelledError()

    async def _arm_worker(self, arm_name: str) -> None:
        queue = self.command_queues[arm_name]
        while True:
            command = await queue.get()
            try:
                if command is None:
                    return
                await self._execute_command(command)
            finally:
                queue.task_done()

    async def _execute_command(self, command: QueuedCommand) -> None:
        message_type = command.operation
        command_id = command.command_id
        arm_name = command.arm_name
        try:
            if (message_type not in {
                    "mode_query", "deactivate", "priority_hold",
                    "gripper_state",
                    } and
                    time.monotonic() - self.last_heartbeat >
                    HEARTBEAT_TIMEOUT_S):
                raise ValueError("heartbeat expired")
            controller_generation = self.controller_generation
            if controller_generation is None:
                raise RuntimeError("controller session is no longer current")
            result = await self.server.execute(
                message_type, command_id, arm_name, command.message,
                self, controller_generation,
                command.motion_authority_generation,
            )
            result.update({
                "type": f"{message_type}_ack", "command_id": command_id,
                "arm": arm_name, "accepted": True,
                "gateway_latency_ms":
                    (time.monotonic_ns() - command.started_ns) / 1_000_000,
            })
        except (TypeError, ValueError, RuntimeError, OSError,
                asyncio.TimeoutError) as error:
            error_detail = str(error).strip() or type(error).__name__
            LOGGER.warning("%s command %d rejected: %s",
                           message_type, command_id, error_detail)
            result = {
                "type": f"{message_type}_ack", "command_id": command_id,
                "arm": arm_name, "accepted": False, "error": error_detail,
                "gateway_latency_ms":
                    (time.monotonic_ns() - command.started_ns) / 1_000_000,
            }
        if not self.closing:
            try:
                await self.send(result)
            except (ConnectionError, OSError):
                LOGGER.info("client connection ended while sending command %d",
                            command_id)

    async def _telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(TELEMETRY_PERIOD_S)
            if (time.monotonic() - self.last_heartbeat >
                    HEARTBEAT_TIMEOUT_S):
                await self._expire_heartbeat_authority()
                continue
            now = time.monotonic_ns()
            states = self.server.status.snapshot()
            self.server.clear_stale_gripper_calibrations(states)
            for arm_name, state in states.items():
                await self.send({
                    "type": "telemetry", "arm": arm_name,
                    "sequence": state.sequence, "gateway_monotonic_ns": now,
                    "sample_age_ms": None if not state.monotonic_ns else
                        (now - state.monotonic_ns) / 1_000_000,
                    "positions_rad": state.positions,
                    "velocities_rad_s": state.velocities,
                    "currents": state.currents, "statuses": state.statuses,
                })

    async def _expire_heartbeat_authority(self) -> None:
        if self.heartbeat_expired:
            return
        self.heartbeat_expired = True
        self.motion_authority_generation += 1
        self.server.clear_gripper_calibrations("heartbeat_expired")
        await self.server.hold_active_leases("heartbeat_expired")
        if self.heartbeat_expired:
            await self.send({"type": "heartbeat_expired"})

    async def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        async with self._send_lock:
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
        self._controller_generation = 0
        self._controller_releasing = False
        self._arm_generations = {name: 0 for name in arms}
        self._leases: dict[str, ArmLease] = {}
        self._active_holds: dict[str, ArmHold] = {}
        self._lease_tasks: set[asyncio.Task[None]] = set()
        self._hold_tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self._gripper_calibration = {
            name: GRIPPER_CALIBRATION_REQUIRED for name in arms
        }
        self._shutting_down = False

    async def client_connected(self, reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter) -> None:
        await ClientSession(self, reader, writer).run()

    def claim_controller(self, session: ClientSession) -> int | None:
        if (self._shutting_down or self._controller_releasing or
                self._controller_session is not None):
            return None
        # Calibration acceptance is deliberately scoped to one live,
        # authenticated controller session. It is not hardware feedback.
        self.clear_gripper_calibrations("controller_claimed")
        self._controller_generation += 1
        self._controller_session = session
        return self._controller_generation

    async def release_controller(
            self, session: ClientSession,
            command_drain: Awaitable[None] | None = None) -> None:
        owned = self._controller_session is session
        initial_hold: asyncio.Task[list[dict[str, Any]]] | None = None
        if owned:
            # Invalidate authority without awaiting so an in-flight command
            # cannot install a lease after the disconnect has been observed.
            self._controller_session = None
            self._controller_releasing = True
            session.controller_generation = None
            self.clear_gripper_calibrations("client_disconnect")
            initial_hold = asyncio.create_task(
                self.hold_active_leases("client_disconnect"),
                name="amber-disconnect-initial-holds",
            )
        try:
            if command_drain is not None:
                await command_drain
        finally:
            if owned:
                try:
                    if initial_hold is not None:
                        await initial_hold
                    # An Amber request may have crossed the I/O boundary before
                    # invalidation. Its per-arm worker schedules a late hold;
                    # this second sweep also catches any lease it completed.
                    await self.hold_active_leases("client_disconnect")
                finally:
                    self._controller_releasing = False

    def controller_is_current(self, session: ClientSession,
                              generation: int) -> bool:
        return (self._controller_session is session and
                self._controller_generation == generation)

    def _require_controller(self, session: ClientSession,
                            generation: int) -> None:
        if not self.controller_is_current(session, generation):
            raise RuntimeError("controller session is no longer current")

    def _require_motion_authority(
            self, session: ClientSession, generation: int,
            motion_authority_generation: int | None = None) -> None:
        self._require_controller(session, generation)
        if (motion_authority_generation is not None and
                session.motion_authority_generation !=
                motion_authority_generation):
            raise RuntimeError("heartbeat authority changed")
        if (session.heartbeat_expired or
                time.monotonic() - session.last_heartbeat >
                HEARTBEAT_TIMEOUT_S):
            raise RuntimeError("heartbeat expired")

    def clear_gripper_calibrations(self, reason: str) -> None:
        for arm_name in self._gripper_calibration:
            self.clear_gripper_calibration(arm_name, reason)

    def clear_gripper_calibration(self, arm_name: str, reason: str) -> None:
        if (self._gripper_calibration[arm_name] !=
                GRIPPER_CALIBRATION_REQUIRED):
            LOGGER.info(
                "clearing %s gripper's unverified calibration state: %s",
                arm_name, reason,
            )
        self._gripper_calibration[arm_name] = GRIPPER_CALIBRATION_REQUIRED

    def clear_stale_gripper_calibrations(
            self, states: dict[str, ArmState]) -> None:
        for arm_name in self.arms:
            state = states.get(arm_name)
            if state is None or not self._state_is_valid_and_fresh(state):
                self.clear_gripper_calibration(
                    arm_name, "arm_telemetry_stale_or_unavailable"
                )

    async def execute(self, operation: str, command_id: int, arm_name: str,
                      message: dict[str, Any], session: ClientSession,
                      controller_generation: int,
                      motion_authority_generation: int) -> dict[str, Any]:
        if self._shutting_down:
            raise RuntimeError("gateway is shutting down")
        self._require_controller(session, controller_generation)
        heartbeat_optional = operation in {
            "mode_query", "deactivate", "priority_hold", "gripper_state",
        }
        if not heartbeat_optional:
            self._require_motion_authority(
                session, controller_generation,
                motion_authority_generation,
            )
        arm = self.arms[arm_name]
        if operation == "renew_lease":
            lease_ms = self._validated_lease_ms(message)
            deadline = self._renew_lease(arm_name, lease_ms)
            return {
                "lease_ms": lease_ms,
                "lease_deadline_monotonic_ns": int(
                    deadline * 1_000_000_000
                ),
            }
        if operation == "priority_hold":
            return await self._priority_hold(arm_name)
        async with self._operation_locks[arm_name]:
            if self._shutting_down:
                raise RuntimeError("gateway is shutting down")
            self._require_controller(session, controller_generation)
            if not heartbeat_optional:
                self._require_motion_authority(
                    session, controller_generation,
                    motion_authority_generation,
                )
            if operation == "mode_query":
                modes = await self._query_modes(arm)
                return self._mode_result(modes)
            if operation == "gripper_state":
                self._clear_gripper_if_telemetry_stale(arm_name)
                return self._gripper_result(arm_name)
            if operation == "gripper_calibrate":
                return await self._calibrate_gripper(
                    arm, arm_name, session, controller_generation,
                    motion_authority_generation,
                )
            if operation == "gripper_control":
                return await self._control_gripper(
                    arm, arm_name, message, session,
                    controller_generation, motion_authority_generation,
                )
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
                return await self._trajectory(
                    arm, arm_name, command_id, message, session,
                    controller_generation,
                )
            if operation == "leased_trajectory":
                lease_ms = self._validated_lease_ms(message)
                result = await self._trajectory(
                    arm, arm_name, command_id, message, session,
                    controller_generation,
                )
                deadline = self._replace_lease(arm_name, lease_ms)
                return {
                    **result,
                    "lease_ms": lease_ms,
                    "lease_deadline_monotonic_ns": int(
                        deadline * 1_000_000_000
                    ),
                }
        raise ValueError("unsupported operation")

    @staticmethod
    def _validated_lease_ms(message: dict[str, Any]) -> int:
        lease_ms = message.get("lease_ms")
        if (isinstance(lease_ms, bool) or
                not isinstance(lease_ms, int)):
            raise ValueError("lease_ms must be an integer")
        if not MIN_LEASE_MS <= lease_ms <= MAX_LEASE_MS:
            raise ValueError(
                f"lease_ms is outside {MIN_LEASE_MS}...{MAX_LEASE_MS}"
            )
        return lease_ms

    def _replace_lease(self, arm_name: str, lease_ms: int) -> float:
        """Install a lease after Amber has accepted its trajectory."""
        previous = self._leases.pop(arm_name, None)
        if previous and previous.task is not asyncio.current_task():
            previous.task.cancel()

        generation = self._arm_generations[arm_name] + 1
        self._arm_generations[arm_name] = generation
        return self._install_lease_watchdog(
            arm_name, generation, lease_ms
        )

    def _renew_lease(self, arm_name: str, lease_ms: int) -> float:
        lease = self._leases.get(arm_name)
        if lease is None or time.monotonic() >= lease.deadline:
            raise RuntimeError(f"no active lease for {arm_name} arm")
        if lease.task is not asyncio.current_task():
            lease.task.cancel()
        return self._install_lease_watchdog(
            arm_name, lease.generation, lease_ms
        )

    def _install_lease_watchdog(self, arm_name: str, generation: int,
                                lease_ms: int) -> float:
        deadline = time.monotonic() + lease_ms / 1000
        task = asyncio.create_task(
            self._lease_watchdog(arm_name, generation, deadline),
            name=f"amber-{arm_name}-lease-{generation}",
        )
        self._lease_tasks.add(task)
        task.add_done_callback(self._lease_tasks.discard)
        self._leases[arm_name] = ArmLease(generation, deadline, task)
        return deadline

    async def _lease_watchdog(self, arm_name: str, generation: int,
                              deadline: float) -> None:
        try:
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))
            lease = self._leases.get(arm_name)
            if not lease or lease.generation != generation:
                return
            self._leases.pop(arm_name, None)
            hold = self._schedule_hold(
                arm_name, "lease_expired", generation
            )
            await asyncio.shield(hold)
        except asyncio.CancelledError:
            raise

    async def _priority_hold(self, arm_name: str) -> dict[str, Any]:
        hold = self._priority_hold_task(arm_name, "priority_hold")
        return await asyncio.shield(hold)

    def _priority_hold_task(
            self, arm_name: str,
            reason: str) -> asyncio.Task[dict[str, Any]]:
        lease = self._leases.pop(arm_name, None)
        if lease:
            generation = lease.generation
            if lease.task is not asyncio.current_task():
                lease.task.cancel()
        else:
            active = self._active_holds.get(arm_name)
            if (active and not active.task.done() and
                    active.generation == self._arm_generations[arm_name]):
                return active.task
            generation = self._arm_generations[arm_name] + 1
            self._arm_generations[arm_name] = generation

        return self._schedule_hold(arm_name, reason, generation)

    def _schedule_authority_loss_hold(self, arm_name: str) -> None:
        self._priority_hold_task(arm_name, "motion_authority_lost")

    async def hold_active_leases(self, reason: str) -> list[dict[str, Any]]:
        """Cancel active leases and independently hold their measured poses."""
        tasks: list[asyncio.Task[dict[str, Any]]] = []
        current_task = asyncio.current_task()
        for arm_name in self.arms:
            lease = self._leases.pop(arm_name, None)
            if lease:
                if lease.task is not current_task:
                    lease.task.cancel()
                tasks.append(self._schedule_hold(
                    arm_name, reason, lease.generation
                ))
                continue
            active = self._active_holds.get(arm_name)
            if active and not active.task.done():
                tasks.append(active.task)
        if not tasks:
            return []
        return list(await asyncio.gather(
            *(asyncio.shield(task) for task in tasks)
        ))

    def _schedule_hold(self, arm_name: str, reason: str,
                       generation: int) -> asyncio.Task[dict[str, Any]]:
        active = self._active_holds.get(arm_name)
        if (active and not active.task.done() and
                active.generation == generation):
            return active.task

        task = asyncio.create_task(
            self._perform_backstop_hold(arm_name, reason, generation),
            name=f"amber-{arm_name}-hold-{generation}",
        )
        hold = ArmHold(generation, task)
        self._active_holds[arm_name] = hold
        self._hold_tasks.add(task)

        def completed(done: asyncio.Task[dict[str, Any]]) -> None:
            self._hold_tasks.discard(done)
            current = self._active_holds.get(arm_name)
            if current and current.task is done:
                self._active_holds.pop(arm_name, None)

        task.add_done_callback(completed)
        return task

    async def _perform_backstop_hold(
            self, arm_name: str, reason: str,
            generation: int) -> dict[str, Any]:
        arm = self.arms[arm_name]
        modes: list[int] | None = None
        captured: ArmState | None = None
        async with self._operation_locks[arm_name]:
            if self._arm_generations[arm_name] != generation:
                return self._unconfirmed_hold(
                    reason, "hold was superseded by a newer arm lease"
                )
            try:
                modes = await self._query_modes(arm)
                if not all(mode == MODE_POSITION for mode in modes):
                    result = self._unconfirmed_hold(
                        reason, "arm is not entirely in position mode", modes
                    )
                    LOGGER.warning(
                        "%s arm %s unconfirmed: %s; modes=%s",
                        arm_name, reason, result["error"], modes,
                    )
                    return result
                captured = await self._fresh_state(
                    arm_name, require_new=True
                )
                response = await self.transport.move_joints(
                    arm, self._next_udp_counter(), captured.positions,
                    HOLD_DURATION_S,
                )
                if response != 1:
                    result = self._unconfirmed_hold(
                        reason,
                        f"Amber rejected position hold ({response})",
                        modes,
                    )
                    result["captured_positions_rad"] = captured.positions
                    LOGGER.warning(
                        "%s arm %s unconfirmed: %s",
                        arm_name, reason, result["error"],
                    )
                    return result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                result = self._unconfirmed_hold(reason, str(error), modes)
                if captured is not None:
                    result["captured_positions_rad"] = captured.positions
                LOGGER.warning(
                    "%s arm %s unconfirmed: %s",
                    arm_name, reason, result["error"],
                )
                return result

        LOGGER.info("%s arm %s confirmed", arm_name, reason)
        return {
            "hold_confirmed": True,
            "hold_reason": reason,
            "amber_response": response,
            "captured_positions_rad": captured.positions,
            "hold_duration_s": HOLD_DURATION_S,
            **self._mode_result(modes),
        }

    def _unconfirmed_hold(self, reason: str, error: str,
                          modes: list[int] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "hold_confirmed": False,
            "hold_reason": reason,
            "error": error,
        }
        if modes is not None:
            result.update(self._mode_result(modes))
        return result

    async def shutdown(self) -> None:
        self._shutting_down = True
        acquired: list[asyncio.Lock] = []
        try:
            for lock in self._operation_locks.values():
                await lock.acquire()
                acquired.append(lock)
            # Clear only after every in-flight per-arm operation has finished;
            # otherwise a late calibration response could repopulate state.
            self.clear_gripper_calibrations("gateway_shutdown")
        finally:
            for lock in reversed(acquired):
                lock.release()
        await self.hold_active_leases("gateway_shutdown")
        tasks = [*self._lease_tasks, *self._hold_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _calibrate_gripper(
            self, arm: ArmConfig, arm_name: str,
            session: ClientSession, controller_generation: int,
            motion_authority_generation: int) -> dict[str, Any]:
        # A new request invalidates any earlier session-local acceptance before
        # touching the transport. Timeouts and rejections therefore fail closed.
        self._gripper_calibration[arm_name] = GRIPPER_CALIBRATION_REQUIRED
        self._require_fresh_gripper_telemetry(arm_name)
        lease = self._leases.get(arm_name)
        if lease is not None:
            raise RuntimeError(
                f"{arm_name} arm has an active motion lease"
            )
        hold = self._active_holds.get(arm_name)
        if hold is not None and not hold.task.done():
            raise RuntimeError(
                f"{arm_name} arm has an active backstop hold"
            )
        response = await self.transport.calibrate_gripper(
            arm, self._next_udp_counter()
        )
        if response != 1:
            raise RuntimeError(
                f"Amber rejected gripper calibration ({response})"
            )
        self._require_motion_authority(
            session, controller_generation, motion_authority_generation
        )
        self._require_fresh_gripper_telemetry(arm_name)
        self._gripper_calibration[arm_name] = (
            GRIPPER_CALIBRATION_ACCEPTED_UNVERIFIED
        )
        return {
            "amber_response": response,
            "calibration_command_accepted": True,
            "completion_verified": False,
            **self._gripper_result(arm_name),
        }

    async def _control_gripper(
            self, arm: ArmConfig, arm_name: str, message: dict[str, Any],
            session: ClientSession, controller_generation: int,
            motion_authority_generation: int) -> dict[str, Any]:
        self._require_fresh_gripper_telemetry(arm_name)
        if (self._gripper_calibration[arm_name] !=
                GRIPPER_CALIBRATION_ACCEPTED_UNVERIFIED):
            raise RuntimeError(
                f"{arm_name} gripper requires calibration acceptance "
                "in this live controller session"
            )
        action_name = message.get("action")
        actions = {
            "release": GRIPPER_ACTION_RELEASE,
            "hold": GRIPPER_ACTION_HOLD,
        }
        if action_name not in actions:
            raise ValueError("action must be release or hold")
        force = message.get("force")
        if isinstance(force, bool) or not isinstance(force, int):
            raise ValueError("force must be an integer vendor intensity")
        if not MIN_GRIPPER_FORCE <= force <= MAX_GRIPPER_FORCE:
            raise ValueError(
                f"force is outside {MIN_GRIPPER_FORCE}..."
                f"{MAX_GRIPPER_FORCE} vendor intensity units"
            )
        response = await self.transport.control_gripper(
            arm, self._next_udp_counter(), actions[action_name], force
        )
        if response != 1:
            raise RuntimeError(
                f"Amber rejected gripper control ({response})"
            )
        self._require_motion_authority(
            session, controller_generation, motion_authority_generation
        )
        self._require_fresh_gripper_telemetry(arm_name)
        return {
            "amber_response": response,
            "action": action_name,
            "force": force,
            "completion_verified": False,
            **self._gripper_result(arm_name),
        }

    def _gripper_result(self, arm_name: str) -> dict[str, Any]:
        return {
            "calibration_state": self._gripper_calibration[arm_name],
            "calibration_verified": False,
            "feedback_available": False,
            "command_in_flight": False,
            "force_min": MIN_GRIPPER_FORCE,
            "force_max": MAX_GRIPPER_FORCE,
            "force_unit": "vendor_intensity",
            "supported_actions": ["release", "hold"],
        }

    def _clear_gripper_if_telemetry_stale(self, arm_name: str) -> bool:
        state = self.status.snapshot().get(arm_name)
        if state is not None and self._state_is_valid_and_fresh(state):
            return False
        self.clear_gripper_calibration(
            arm_name, "arm_telemetry_stale_or_unavailable"
        )
        return True

    def _require_fresh_gripper_telemetry(self, arm_name: str) -> None:
        if self._clear_gripper_if_telemetry_stale(arm_name):
            raise RuntimeError(
                f"fresh telemetry is unavailable for {arm_name} arm"
            )

    async def _trajectory(
            self, arm: ArmConfig, arm_name: str, command_id: int,
            message: dict[str, Any], session: ClientSession,
            controller_generation: int) -> dict[str, Any]:
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
        self._require_motion_authority(session, controller_generation)
        try:
            response = await self.transport.move_joints(
                arm, command_id, positions, duration
            )
        except Exception:
            if (not self.controller_is_current(
                    session, controller_generation) or
                    time.monotonic() - session.last_heartbeat >
                    HEARTBEAT_TIMEOUT_S):
                self._schedule_authority_loss_hold(arm_name)
            raise
        if (not self.controller_is_current(session, controller_generation) or
                time.monotonic() - session.last_heartbeat >
                HEARTBEAT_TIMEOUT_S):
            self._schedule_authority_loss_hold(arm_name)
            self._require_motion_authority(session, controller_generation)
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
        await gateway.shutdown()
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
