#!/usr/bin/env python3
"""Persistent Cerebro-to-Amber gateway.

Commands arrive over an authenticated, ordered TCP session. The gateway owns
the local UDP sockets used by the vendor Amber cores and subscribes to LCM arm
status. It never changes actuator configuration or drive limits.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import hmac
import json
import logging
import os
import secrets
import signal
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
            self.authenticated = True
            self.last_heartbeat = time.monotonic()
            await self.send({"type": "ready", "protocol": PROTOCOL,
                             "heartbeat_timeout_s": HEARTBEAT_TIMEOUT_S,
                             "telemetry_hz": round(1 / TELEMETRY_PERIOD_S)})
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
        if message_type != "trajectory":
            raise ValueError("unsupported message type")
        if time.monotonic() - self.last_heartbeat > HEARTBEAT_TIMEOUT_S:
            raise ValueError("heartbeat expired")
        command_id = int(message.get("command_id", 0))
        if command_id <= self.last_command_id:
            raise ValueError("command_id is stale or out of order")
        arm_name = message.get("arm")
        if arm_name not in self.server.arms:
            raise ValueError("unknown arm")
        positions = message.get("positions_rad")
        duration = float(message.get("duration_s", 0))
        if (not isinstance(positions, list) or len(positions) != JOINT_COUNT or
                not all(isinstance(value, (int, float)) for value in positions)):
            raise ValueError("positions_rad must contain seven numbers")
        positions = [float(value) for value in positions]
        if not all(-3.10 <= value <= 3.10 for value in positions):
            raise ValueError("joint request exceeds gateway absolute bound")
        if not 0.65 <= duration <= 10.0:
            raise ValueError("duration_s is outside 0.65...10.0")
        self.last_command_id = command_id
        started = time.monotonic_ns()
        response = await self.server.transport.move_joints(
            self.server.arms[arm_name], command_id, positions, duration
        )
        await self.send({
            "type": "trajectory_ack", "command_id": command_id,
            "accepted": response == 1, "amber_response": response,
            "gateway_latency_ms": (time.monotonic_ns() - started) / 1_000_000,
        })

    async def _telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(TELEMETRY_PERIOD_S)
            if time.monotonic() - self.last_heartbeat > HEARTBEAT_TIMEOUT_S:
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

    async def client_connected(self, reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter) -> None:
        await ClientSession(self, reader, writer).run()


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
