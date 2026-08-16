#!/usr/bin/env python3
"""Protocol tests that never open an Amber UDP socket or load LCM."""

from __future__ import annotations

import asyncio
import ctypes
import json
import time
import unittest
from unittest import mock

import rob_amber_gateway as gateway


class FakeTransport:
    def __init__(self) -> None:
        self.modes = {
            "left": [gateway.MODE_INACTIVE] * gateway.JOINT_COUNT,
            "right": [gateway.MODE_INACTIVE] * gateway.JOINT_COUNT,
        }
        self.calls: list[tuple] = []
        self.apply_mode = True

    async def set_mode(self, arm: gateway.ArmConfig, command_id: int,
                       mode: int) -> int:
        self.calls.append(("set_mode", arm.name, command_id, mode))
        if self.apply_mode:
            self.modes[arm.name] = [mode] * gateway.JOINT_COUNT
        return 1

    async def get_modes(self, arm: gateway.ArmConfig,
                        command_id: int) -> list[int]:
        self.calls.append(("get_modes", arm.name, command_id))
        return list(self.modes[arm.name])

    async def move_joints(self, arm: gateway.ArmConfig, command_id: int,
                          positions: list[float], duration: float) -> int:
        self.calls.append(("move_joints", arm.name, command_id,
                           list(positions), duration))
        return 1


class FakeStatus:
    def __init__(self, advancing: bool = True) -> None:
        self.advancing = advancing
        self.states = {
            "left": gateway.ArmState(
                positions=[0.1, -0.2, 0.3, -0.4, 0.5, -0.6, 0.7],
                velocities=[0.0] * gateway.JOINT_COUNT,
                currents=[0.2] * gateway.JOINT_COUNT,
                statuses=[1.0] * gateway.JOINT_COUNT,
                monotonic_ns=time.monotonic_ns(), sequence=1,
            ),
            "right": gateway.ArmState(
                positions=[-0.1, 0.2, -0.3, 0.4, -0.5, 0.6, -0.7],
                velocities=[0.0] * gateway.JOINT_COUNT,
                currents=[0.2] * gateway.JOINT_COUNT,
                statuses=[1.0] * gateway.JOINT_COUNT,
                monotonic_ns=time.monotonic_ns(), sequence=1,
            ),
        }

    def snapshot(self) -> dict[str, gateway.ArmState]:
        if self.advancing:
            for state in self.states.values():
                state.sequence += 1
                state.monotonic_ns = time.monotonic_ns()
        return {
            name: gateway.ArmState(
                positions=list(state.positions),
                velocities=list(state.velocities),
                currents=list(state.currents),
                statuses=list(state.statuses),
                monotonic_ns=state.monotonic_ns,
                sequence=state.sequence,
            )
            for name, state in self.states.items()
        }


class GatewayProtocolTests(unittest.IsolatedAsyncioTestCase):
    TOKEN = "unit-test-token-that-is-longer-than-32-characters"

    async def asyncSetUp(self) -> None:
        self.transport = FakeTransport()
        self.status = FakeStatus()
        self.arms = {
            "left": gateway.ArmConfig("left", 26001, "Left_ArmStatus"),
            "right": gateway.ArmConfig("right", 26002, "Right_ArmStatus"),
        }
        self.gateway = gateway.GatewayServer(
            self.TOKEN, self.transport, self.status, self.arms
        )
        self.server = await asyncio.start_server(
            self.gateway.client_connected, "127.0.0.1", 0
        )
        self.port = self.server.sockets[0].getsockname()[1]
        self.writers: list[asyncio.StreamWriter] = []

    async def asyncTearDown(self) -> None:
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()

    async def connect(self, token: str | None = None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(writer)
        challenge = await self.read_message(reader)
        self.assertEqual(challenge["type"], "challenge")
        await self.write_message(writer, {
            "type": "hello", "protocol": gateway.PROTOCOL,
            "token": token if token is not None else self.TOKEN,
        })
        return reader, writer

    async def command(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter, request: dict) -> dict:
        await self.write_message(writer, request)
        return await self.read_until(reader, f"{request['type']}_ack")

    @staticmethod
    async def write_message(writer: asyncio.StreamWriter,
                            message: dict) -> None:
        writer.write(json.dumps(message).encode() + b"\n")
        await writer.drain()

    async def read_message(self, reader: asyncio.StreamReader) -> dict:
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        self.assertTrue(line)
        return json.loads(line)

    async def read_until(self, reader: asyncio.StreamReader,
                         message_type: str) -> dict:
        for _ in range(50):
            message = await self.read_message(reader)
            if message.get("type") == message_type:
                return message
        self.fail(f"did not receive {message_type}")

    async def authenticate(self):
        reader, writer = await self.connect()
        ready = await self.read_message(reader)
        self.assertEqual(ready["type"], "ready")
        return reader, writer

    async def test_rejects_unauthenticated_client_without_transport_io(self):
        reader, _ = await self.connect("wrong-token")
        error = await self.read_message(reader)
        self.assertEqual(error["type"], "error")
        self.assertIn("authentication", error["error"])
        self.assertEqual(self.transport.calls, [])

    async def test_allows_only_one_authenticated_controller_session(self):
        first_reader, first_writer = await self.authenticate()
        self.assertFalse(first_reader.at_eof())

        second_reader, _ = await self.connect()
        rejection = await self.read_message(second_reader)
        self.assertEqual(rejection["type"], "error")
        self.assertIn("another authenticated controller", rejection["error"])

        first_writer.close()
        await first_writer.wait_closed()
        for _ in range(20):
            if self.gateway._controller_session is None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(self.gateway._controller_session)

        third_reader, _ = await self.connect()
        ready = await self.read_message(third_reader)
        self.assertEqual(ready["type"], "ready")
        self.assertTrue(ready["exclusive_controller_session"])

    async def test_position_mode_verifies_active_captures_and_holds(self):
        reader, writer = await self.authenticate()
        response = await self.command(reader, writer, {
            "type": "position_mode", "command_id": 1, "arm": "left",
        })

        self.assertTrue(response["accepted"])
        self.assertEqual(response["modes"], [gateway.MODE_POSITION] * 7)
        self.assertEqual(response["active_modes"], [gateway.MODE_ACTIVE] * 7)
        self.assertEqual(
            response["captured_positions_rad"],
            self.status.states["left"].positions,
        )
        call_names = [call[0] for call in self.transport.calls]
        self.assertEqual(call_names, [
            "set_mode", "get_modes", "set_mode", "get_modes", "move_joints",
        ])
        self.assertEqual(self.transport.calls[0][3], gateway.MODE_ACTIVE)
        self.assertEqual(self.transport.calls[2][3], gateway.MODE_POSITION)
        self.assertEqual(
            self.transport.calls[-1][3], self.status.states["left"].positions
        )
        self.assertEqual(self.transport.calls[-1][4], gateway.HOLD_DURATION_S)

    async def test_mode_query_hold_trajectory_and_deactivate(self):
        self.transport.modes["right"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()

        queried = await self.command(reader, writer, {
            "type": "mode_query", "command_id": 1, "arm": "right",
        })
        held = await self.command(reader, writer, {
            "type": "hold_current", "command_id": 2, "arm": "right",
        })
        moved = await self.command(reader, writer, {
            "type": "trajectory", "command_id": 3, "arm": "right",
            "positions_rad": [-0.1, 0.2, -0.3, 0.4, -0.5, 0.6, -0.6],
            "duration_s": 2.0,
        })
        stopped = await self.command(reader, writer, {
            "type": "deactivate", "command_id": 4, "arm": "right",
        })

        self.assertTrue(queried["accepted"])
        self.assertTrue(held["accepted"])
        self.assertTrue(moved["accepted"])
        self.assertTrue(stopped["accepted"])
        self.assertEqual(stopped["modes"], [gateway.MODE_INACTIVE] * 7)
        moves = [call for call in self.transport.calls
                 if call[0] == "move_joints"]
        self.assertEqual(len(moves), 2)

    async def test_trajectory_requires_position_mode(self):
        reader, writer = await self.authenticate()
        response = await self.command(reader, writer, {
            "type": "trajectory", "command_id": 1, "arm": "left",
            "positions_rad": [0.0] * 7, "duration_s": 1.0,
        })
        self.assertFalse(response["accepted"])
        self.assertIn("not in position mode", response["error"])
        self.assertFalse(any(call[0] == "move_joints"
                             for call in self.transport.calls))

    async def test_trajectory_enforces_each_joint_limit(self):
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        upper_bounds = [upper for _, upper in gateway.JOINT_LIMITS_RAD]
        accepted = await self.command(reader, writer, {
            "type": "trajectory", "command_id": 1, "arm": "left",
            "positions_rad": upper_bounds, "duration_s": 1.0,
        })
        self.assertTrue(accepted["accepted"])

        for index, upper in enumerate(upper_bounds):
            outside = [0.0] * gateway.JOINT_COUNT
            outside[index] = upper + 0.0001
            rejected = await self.command(reader, writer, {
                "type": "trajectory", "command_id": index + 2,
                "arm": "left", "positions_rad": outside,
                "duration_s": 1.0,
            })
            self.assertFalse(rejected["accepted"])
            self.assertIn(f"joint {index + 1} request", rejected["error"])

        moves = [call for call in self.transport.calls
                 if call[0] == "move_joints"]
        self.assertEqual(len(moves), 1)

    async def test_activate_rejects_stale_telemetry_before_mode_command(self):
        self.status.advancing = False
        self.status.states["left"].monotonic_ns = 0
        reader, writer = await self.authenticate()
        with mock.patch.object(gateway, "FRESH_TELEMETRY_TIMEOUT_S", 0.06):
            response = await self.command(reader, writer, {
                "type": "activate", "command_id": 1, "arm": "left",
            })
        self.assertFalse(response["accepted"])
        self.assertIn("fresh telemetry", response["error"])
        self.assertFalse(any(call[0] == "set_mode"
                             for call in self.transport.calls))

    async def test_deactivate_remains_available_after_heartbeat_expiry(self):
        with mock.patch.object(gateway, "HEARTBEAT_TIMEOUT_S", 0.02):
            reader, writer = await self.authenticate()
            expired = await self.read_until(reader, "heartbeat_expired")
            self.assertEqual(expired["type"], "heartbeat_expired")
            response = await self.command(reader, writer, {
                "type": "deactivate", "command_id": 1, "arm": "left",
            })
        self.assertTrue(response["accepted"])
        self.assertEqual(response["modes"], [gateway.MODE_INACTIVE] * 7)

    async def test_mode_transition_timeout_is_reported(self):
        self.transport.apply_mode = False
        reader, writer = await self.authenticate()
        with mock.patch.object(gateway, "MODE_TRANSITION_TIMEOUT_S", 0.06):
            response = await self.command(reader, writer, {
                "type": "activate", "command_id": 1, "arm": "left",
            })
        self.assertFalse(response["accepted"])
        self.assertIn("timed out verifying active mode", response["error"])

    def test_vendor_mode_packet_layouts(self):
        self.assertEqual(ctypes.sizeof(gateway.ModeCommand), 10)
        self.assertEqual(ctypes.sizeof(gateway.ModeQuery), 12)
        self.assertEqual(ctypes.sizeof(gateway.ModeQueryResponse), 22)

        transport = gateway.AmberUDPTransport("unused")
        response = gateway.ModeQueryResponse()
        response.modes[:] = [gateway.MODE_POSITION] * gateway.JOINT_COUNT
        arm = gateway.ArmConfig("left", 26001, "Left_ArmStatus")
        with mock.patch.object(
            transport, "_exchange", return_value=response
        ) as exchange:
            modes = transport._get_modes_blocking(arm, 0x12345678)
        payload = exchange.call_args.args[1]
        self.assertEqual(payload.cmd_no, 110)
        self.assertEqual(payload.length, 12)
        self.assertEqual(payload.counter, 0x12345678)
        self.assertEqual(payload.joint_id, 8)
        self.assertEqual(modes, [gateway.MODE_POSITION] * gateway.JOINT_COUNT)


if __name__ == "__main__":
    unittest.main()
