#!/usr/bin/env python3
"""Protocol tests that never open an Amber UDP socket or load LCM."""

from __future__ import annotations

import asyncio
import ctypes
import json
import math
import threading
import time
import unittest
from types import SimpleNamespace
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
        self.gripper_calibration_response = 1
        self.gripper_control_response = 1
        self.move_started = {
            "left": asyncio.Event(),
            "right": asyncio.Event(),
        }
        self.move_release: dict[str, asyncio.Event | None] = {
            "left": None,
            "right": None,
        }

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
        self.move_started[arm.name].set()
        release = self.move_release[arm.name]
        if release is not None:
            await release.wait()
        return 1

    async def calibrate_gripper(self, arm: gateway.ArmConfig,
                                command_id: int) -> int:
        self.calls.append(("calibrate_gripper", arm.name, command_id))
        return self.gripper_calibration_response

    async def control_gripper(self, arm: gateway.ArmConfig, command_id: int,
                              action: int, force: int) -> int:
        self.calls.append(("control_gripper", arm.name, command_id,
                           action, force))
        return self.gripper_control_response


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
                velocities_available=state.velocities_available,
            )
            for name, state in self.states.items()
        }


class BlockingWriter:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.drain_started = asyncio.Event()
        self.release_drain = asyncio.Event()
        self.active_drains = 0
        self.maximum_active_drains = 0

    def write(self, payload: bytes) -> None:
        self.payloads.append(payload)

    async def drain(self) -> None:
        self.active_drains += 1
        self.maximum_active_drains = max(
            self.maximum_active_drains, self.active_drains
        )
        self.drain_started.set()
        try:
            await self.release_drain.wait()
        finally:
            self.active_drains -= 1


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
        await self.gateway.shutdown()
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

    async def test_ready_advertises_optional_commands_without_hardware_io(self):
        reader, writer = await self.connect()
        ready = await self.read_until(reader, "ready")
        self.assertTrue(ready["exclusive_controller_session"])
        self.assertTrue({
            "gripper_state", "gripper_calibrate", "gripper_control",
        }.issubset(ready["supported_commands"]))
        self.assertEqual(self.transport.calls, [])
        state = await self.command(reader, writer, {
            "type": "gripper_state", "command_id": 1, "arm": "left",
        })
        self.assertTrue(state["accepted"])
        self.assertFalse(state["calibration_verified"])
        self.assertEqual(self.transport.calls, [])

    async def test_unsupported_command_reports_type_without_hardware_io(self):
        reader, writer = await self.connect()
        await self.read_until(reader, "ready")
        await self.write_message(writer, {"type": "future_command"})
        error = await self.read_until(reader, "command_error")
        self.assertFalse(error["accepted"])
        self.assertEqual(error["command_type"], "future_command")
        self.assertEqual(error["error"], "unsupported message type")
        self.assertEqual(self.transport.calls, [])

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

    async def read_command_results(
            self, reader: asyncio.StreamReader,
            command_ids: set[int]) -> dict[int, dict]:
        results: dict[int, dict] = {}
        for _ in range(100):
            message = await self.read_message(reader)
            command_id = message.get("command_id")
            if (command_id in command_ids and
                    str(message.get("type", "")).endswith("_ack")):
                results[command_id] = message
                if results.keys() == command_ids:
                    return results
        self.fail(f"did not receive command results for {command_ids}")

    async def authenticate(self):
        reader, writer = await self.connect()
        ready = await self.read_message(reader)
        self.assertEqual(ready["type"], "ready")
        return reader, writer

    async def wait_for_moves(self, expected: int) -> list[tuple]:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            moves = [call for call in self.transport.calls
                     if call[0] == "move_joints"]
            if len(moves) >= expected:
                return moves
            await asyncio.sleep(0.01)
        self.fail(f"did not receive {expected} move_joints calls")

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

    async def test_different_arms_start_while_left_command_is_blocked(self):
        self.transport.modes = {
            "left": [gateway.MODE_POSITION] * gateway.JOINT_COUNT,
            "right": [gateway.MODE_POSITION] * gateway.JOINT_COUNT,
        }
        left_release = asyncio.Event()
        self.transport.move_release["left"] = left_release
        reader, writer = await self.authenticate()

        await self.write_message(writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "left", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        await asyncio.wait_for(
            self.transport.move_started["left"].wait(), timeout=0.5
        )
        await self.write_message(writer, {
            "type": "leased_trajectory", "command_id": 2,
            "arm": "right", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })

        await asyncio.wait_for(
            self.transport.move_started["right"].wait(), timeout=0.5
        )
        self.assertFalse(left_release.is_set())
        left_release.set()
        results = await self.read_command_results(reader, {1, 2})

        self.assertTrue(results[1]["accepted"])
        self.assertTrue(results[2]["accepted"])

    async def test_concurrent_session_writes_are_serialized(self):
        writer = BlockingWriter()
        session = gateway.ClientSession(
            self.gateway, asyncio.StreamReader(), writer
        )

        first = asyncio.create_task(session.send({"type": "first"}))
        await asyncio.wait_for(writer.drain_started.wait(), timeout=0.5)
        second = asyncio.create_task(session.send({"type": "second"}))
        await asyncio.sleep(0)

        self.assertEqual(len(writer.payloads), 1)
        self.assertEqual(writer.maximum_active_drains, 1)
        writer.release_drain.set()
        await asyncio.gather(first, second)
        self.assertEqual(len(writer.payloads), 2)
        self.assertEqual(writer.maximum_active_drains, 1)

    async def test_full_arm_queue_rejects_without_blocking_heartbeats(self):
        self.transport.modes["left"] = (
            [gateway.MODE_POSITION] * gateway.JOINT_COUNT
        )
        left_release = asyncio.Event()
        self.transport.move_release["left"] = left_release
        reader, writer = await self.authenticate()

        await self.write_message(writer, {
            "type": "trajectory", "command_id": 1, "arm": "left",
            "positions_rad": [0.0] * 7, "duration_s": 1.0,
        })
        await asyncio.wait_for(
            self.transport.move_started["left"].wait(), timeout=0.5
        )
        for command_id in range(
                2, gateway.MAX_PENDING_COMMANDS_PER_ARM + 2):
            await self.write_message(writer, {
                "type": "trajectory", "command_id": command_id,
                "arm": "left", "positions_rad": [0.1] * 7,
                "duration_s": 1.0,
            })
        overflow_id = gateway.MAX_PENDING_COMMANDS_PER_ARM + 2
        await self.write_message(writer, {
            "type": "trajectory", "command_id": overflow_id,
            "arm": "left", "positions_rad": [0.2] * 7,
            "duration_s": 1.0,
        })

        overflow = (
            await self.read_command_results(reader, {overflow_id})
        )[overflow_id]
        self.assertFalse(overflow["accepted"])
        self.assertIn("queue is full", overflow["error"])
        session = self.gateway._controller_session
        self.assertIsNotNone(session)
        self.assertEqual(session.last_command_id, overflow_id)
        self.assertEqual(
            session.command_queues["left"].qsize(),
            gateway.MAX_PENDING_COMMANDS_PER_ARM,
        )

        await self.write_message(writer, {"type": "heartbeat"})
        heartbeat = await self.read_until(reader, "heartbeat_ack")
        self.assertEqual(heartbeat["type"], "heartbeat_ack")
        self.assertFalse(left_release.is_set())
        left_release.set()

    async def test_same_arm_commands_remain_ordered(self):
        self.transport.modes["left"] = (
            [gateway.MODE_POSITION] * gateway.JOINT_COUNT
        )
        left_release = asyncio.Event()
        self.transport.move_release["left"] = left_release
        reader, writer = await self.authenticate()

        await self.write_message(writer, {
            "type": "trajectory", "command_id": 1, "arm": "left",
            "positions_rad": [0.0] * 7, "duration_s": 1.0,
        })
        await self.write_message(writer, {
            "type": "trajectory", "command_id": 2, "arm": "left",
            "positions_rad": [0.1] * 7, "duration_s": 1.0,
        })
        await asyncio.wait_for(
            self.transport.move_started["left"].wait(), timeout=0.5
        )
        await asyncio.sleep(0.05)
        left_moves = [
            call for call in self.transport.calls
            if call[0] == "move_joints" and call[1] == "left"
        ]
        self.assertEqual([call[2] for call in left_moves], [1])

        left_release.set()
        results = await self.read_command_results(reader, {1, 2})
        self.assertTrue(results[1]["accepted"])
        self.assertTrue(results[2]["accepted"])
        left_moves = [
            call for call in self.transport.calls
            if call[0] == "move_joints" and call[1] == "left"
        ]
        self.assertEqual([call[2] for call in left_moves], [1, 2])

    async def test_priority_hold_purges_pending_same_arm_commands(self):
        self.transport.modes["left"] = (
            [gateway.MODE_POSITION] * gateway.JOINT_COUNT
        )
        left_release = asyncio.Event()
        self.transport.move_release["left"] = left_release
        measured = [0.31, -0.21, 0.11, 0.01, -0.11, 0.21, -0.31]
        reader, writer = await self.authenticate()

        await self.write_message(writer, {
            "type": "trajectory", "command_id": 1, "arm": "left",
            "positions_rad": [0.0] * 7, "duration_s": 1.0,
        })
        await asyncio.wait_for(
            self.transport.move_started["left"].wait(), timeout=0.5
        )
        for command_id, position in ((2, 0.1), (3, 0.2), (4, 0.3)):
            await self.write_message(writer, {
                "type": "trajectory", "command_id": command_id,
                "arm": "left", "positions_rad": [position] * 7,
                "duration_s": 1.0,
            })
        await self.write_message(writer, {
            "type": "priority_hold", "command_id": 5, "arm": "left",
        })

        superseded = await self.read_command_results(reader, {2, 3, 4})
        for result in superseded.values():
            self.assertFalse(result["accepted"])
            self.assertIn("superseded by priority_hold command 5",
                          result["error"])
        self.assertEqual(len([
            call for call in self.transport.calls
            if call[0] == "move_joints" and call[1] == "left"
        ]), 1)
        session = self.gateway._controller_session
        self.assertIsNotNone(session)
        self.assertEqual(session.command_queues["left"].qsize(), 1)

        self.status.states["left"].positions = measured
        left_release.set()
        results = await self.read_command_results(reader, {1, 5})
        self.assertTrue(results[1]["accepted"])
        self.assertTrue(results[5]["accepted"])
        self.assertTrue(results[5]["hold_confirmed"])
        self.assertEqual(results[5]["captured_positions_rad"], measured)
        left_moves = [
            call for call in self.transport.calls
            if call[0] == "move_joints" and call[1] == "left"
        ]
        self.assertEqual(len(left_moves), 2)
        self.assertEqual(left_moves[-1][3], measured)

    async def test_priority_hold_on_right_is_not_blocked_by_left(self):
        self.transport.modes = {
            "left": [gateway.MODE_POSITION] * gateway.JOINT_COUNT,
            "right": [gateway.MODE_POSITION] * gateway.JOINT_COUNT,
        }
        left_release = asyncio.Event()
        self.transport.move_release["left"] = left_release
        measured = [-0.2, 0.3, -0.4, 0.5, -0.6, 0.7, -0.8]
        self.status.states["right"].positions = measured
        reader, writer = await self.authenticate()

        await self.write_message(writer, {
            "type": "trajectory", "command_id": 1, "arm": "left",
            "positions_rad": [0.0] * 7, "duration_s": 1.0,
        })
        await asyncio.wait_for(
            self.transport.move_started["left"].wait(), timeout=0.5
        )
        await self.write_message(writer, {
            "type": "priority_hold", "command_id": 2, "arm": "right",
        })

        held = await self.read_until(reader, "priority_hold_ack")
        self.assertTrue(held["accepted"])
        self.assertTrue(held["hold_confirmed"])
        self.assertEqual(held["captured_positions_rad"], measured)
        self.assertFalse(left_release.is_set())
        self.assertTrue(any(
            call[0] == "move_joints" and call[1] == "right"
            for call in self.transport.calls
        ))

        left_release.set()
        left = await self.read_until(reader, "trajectory_ack")
        self.assertTrue(left["accepted"])

    async def test_disconnect_prevents_late_lease_and_holds_after_io(self):
        self.transport.modes["left"] = (
            [gateway.MODE_POSITION] * gateway.JOINT_COUNT
        )
        left_release = asyncio.Event()
        self.transport.move_release["left"] = left_release
        measured = [0.3, -0.2, 0.1, 0.0, -0.1, 0.2, -0.3]
        reader, writer = await self.authenticate()

        await self.write_message(writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "left", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        await asyncio.wait_for(
            self.transport.move_started["left"].wait(), timeout=0.5
        )
        self.status.states["left"].positions = measured
        writer.close()
        await writer.wait_closed()
        for _ in range(50):
            if self.gateway._controller_session is None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(self.gateway._controller_session)

        left_release.set()
        moves = await self.wait_for_moves(2)
        for _ in range(50):
            if not self.gateway._controller_releasing:
                break
            await asyncio.sleep(0.01)

        self.assertEqual(moves[-1][1], "left")
        self.assertEqual(moves[-1][3], measured)
        self.assertNotIn("left", self.gateway._leases)
        self.assertFalse(self.gateway._controller_releasing)
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

    async def test_bad_feedback_never_becomes_a_hold_or_mode_change(self):
        # Reproduce the observed degree-valued sample being treated as radians.
        self.status.states["left"].positions[1] = -119.1484375
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        with mock.patch.object(gateway, "FRESH_TELEMETRY_TIMEOUT_S", 0.06):
            for command_id, operation in enumerate(
                    ("position_mode", "hold_current", "priority_hold",
                     "activate", "trajectory", "leased_trajectory"), 1):
                response = await self.command(reader, writer, {
                    "type": operation, "command_id": command_id, "arm": "left",
                    "positions_rad": [0.0] * 7, "duration_s": 1.0,
                    "lease_ms": 1000,
                })
                if operation == "priority_hold":
                    self.assertFalse(response["hold_confirmed"])
                else:
                    self.assertFalse(response["accepted"])
        self.assertFalse(any(call[0] in {"set_mode", "move_joints"}
                             for call in self.transport.calls))
        # Deactivation must not depend on valid position/velocity feedback.
        stopped = await self.command(reader, writer, {
            "type": "deactivate", "command_id": 7, "arm": "left",
        })
        self.assertTrue(stopped["accepted"])

    async def test_unverified_velocity_is_unavailable_not_zero(self):
        state = self.status.states["left"]
        state.velocities = []
        state.velocities_available = False
        reader, writer = await self.authenticate()
        sample = await self.read_until(reader, "telemetry")
        self.assertEqual(sample["arm"], "left")
        self.assertIsNone(sample["velocities_rad_s"])
        self.assertFalse(sample["velocities_available"])
        self.assertEqual(sample["positions_rad"], state.positions)
        # A manual measured-position hold does not certify a velocity or
        # settled state, and still uses only bounded, fresh measured positions.
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        held = await self.command(reader, writer, {
            "type": "hold_current", "command_id": 1, "arm": "left",
        })
        self.assertTrue(held["accepted"])
        self.assertEqual(held["captured_positions_rad"], state.positions)

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

    async def test_leased_trajectory_expires_into_fresh_measured_hold(self):
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        with (mock.patch.object(gateway, "MIN_LEASE_MS", 20),
              mock.patch.object(gateway, "MAX_LEASE_MS", 50)):
            response = await self.command(reader, writer, {
                "type": "leased_trajectory", "command_id": 1,
                "arm": "left", "positions_rad": [0.0] * 7,
                "duration_s": 1.0, "lease_ms": 30,
            })
            self.assertTrue(response["accepted"])
            self.assertEqual(response["lease_ms"], 30)

            measured = [0.2, -0.3, 0.4, -0.5, 0.6, -0.7, 0.8]
            self.status.states["left"].positions = measured
            moves = await self.wait_for_moves(2)

        self.assertEqual(moves[0][3], [0.0] * 7)
        self.assertEqual(moves[1][3], measured)
        self.assertEqual(moves[1][4], gateway.HOLD_DURATION_S)
        self.assertNotIn("left", self.gateway._leases)
        self.assertFalse(any(call[0] == "set_mode"
                             for call in self.transport.calls))

    async def test_renew_lease_extends_expiry_without_amber_io(self):
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        with (mock.patch.object(gateway, "MIN_LEASE_MS", 20),
              mock.patch.object(gateway, "MAX_LEASE_MS", 400)):
            leased = await self.command(reader, writer, {
                "type": "leased_trajectory", "command_id": 1,
                "arm": "left", "positions_rad": [0.0] * 7,
                "duration_s": 1.0, "lease_ms": 200,
            })
            self.assertTrue(leased["accepted"])
            original = self.gateway._leases["left"]
            await asyncio.sleep(0.05)

            calls_before_renewal = list(self.transport.calls)
            renewed = await self.command(reader, writer, {
                "type": "renew_lease", "command_id": 2,
                "arm": "left", "lease_ms": 300,
            })
            current = self.gateway._leases["left"]
            self.assertTrue(renewed["accepted"])
            self.assertEqual(renewed["lease_ms"], 300)
            self.assertGreater(
                renewed["lease_deadline_monotonic_ns"],
                leased["lease_deadline_monotonic_ns"],
            )
            self.assertEqual(current.generation, original.generation)
            self.assertIsNot(current.task, original.task)
            self.assertEqual(self.transport.calls, calls_before_renewal)

            await asyncio.sleep(0.17)
            moves = [call for call in self.transport.calls
                     if call[0] == "move_joints"]
            self.assertEqual(len(moves), 1)
            self.assertIn("left", self.gateway._leases)

            measured = [0.4, -0.3, 0.2, -0.1, 0.0, 0.1, -0.2]
            self.status.states["left"].positions = measured
            moves = await self.wait_for_moves(2)

        self.assertEqual(moves[-1][3], measured)
        self.assertNotIn("left", self.gateway._leases)

    async def test_renew_lease_requires_an_active_lease(self):
        reader, writer = await self.authenticate()
        renewed = await self.command(reader, writer, {
            "type": "renew_lease", "command_id": 1,
            "arm": "right", "lease_ms": gateway.MIN_LEASE_MS,
        })

        self.assertFalse(renewed["accepted"])
        self.assertIn("no active lease", renewed["error"])
        self.assertEqual(self.transport.calls, [])

    async def test_renew_lease_rejects_invalid_bounds_without_replacement(self):
        self.transport.modes["right"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        leased = await self.command(reader, writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "right", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        self.assertTrue(leased["accepted"])
        original = self.gateway._leases["right"]
        calls_before_renewal = list(self.transport.calls)

        for command_id, bad_lease in enumerate(
                (gateway.MIN_LEASE_MS - 1, gateway.MAX_LEASE_MS + 1,
                 700.0, True), start=2):
            rejected = await self.command(reader, writer, {
                "type": "renew_lease", "command_id": command_id,
                "arm": "right", "lease_ms": bad_lease,
            })
            self.assertFalse(rejected["accepted"])
            self.assertIn("lease_ms", rejected["error"])
            self.assertIs(self.gateway._leases["right"], original)
            self.assertEqual(self.transport.calls, calls_before_renewal)

    async def test_priority_hold_cancels_lease_and_returns_capture(self):
        self.transport.modes["right"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        leased = await self.command(reader, writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "right", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        self.assertTrue(leased["accepted"])

        measured = [-0.2, 0.3, -0.4, 0.5, -0.6, 0.7, -0.8]
        self.status.states["right"].positions = measured
        held = await self.command(reader, writer, {
            "type": "priority_hold", "command_id": 2, "arm": "right",
        })

        self.assertTrue(held["accepted"])
        self.assertTrue(held["hold_confirmed"])
        self.assertEqual(held["captured_positions_rad"], measured)
        self.assertEqual(held["modes"], [gateway.MODE_POSITION] * 7)
        self.assertNotIn("right", self.gateway._leases)
        moves = [call for call in self.transport.calls
                 if call[0] == "move_joints"]
        self.assertEqual(len(moves), 2)
        self.assertFalse(any(call[0] == "set_mode"
                             for call in self.transport.calls))

    async def test_priority_hold_never_activates_non_position_arm(self):
        reader, writer = await self.authenticate()
        held = await self.command(reader, writer, {
            "type": "priority_hold", "command_id": 1, "arm": "left",
        })

        self.assertTrue(held["accepted"])
        self.assertFalse(held["hold_confirmed"])
        self.assertEqual(held["modes"], [gateway.MODE_INACTIVE] * 7)
        self.assertFalse(any(call[0] in {"set_mode", "move_joints"}
                             for call in self.transport.calls))

    async def test_disconnect_backstops_active_lease(self):
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        leased = await self.command(reader, writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "left", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        self.assertTrue(leased["accepted"])
        renewed = await self.command(reader, writer, {
            "type": "renew_lease", "command_id": 2,
            "arm": "left", "lease_ms": gateway.MAX_LEASE_MS,
        })
        self.assertTrue(renewed["accepted"])

        measured = [0.3, -0.2, 0.1, 0.0, -0.1, 0.2, -0.3]
        self.status.states["left"].positions = measured
        writer.close()
        await writer.wait_closed()
        moves = await self.wait_for_moves(2)

        self.assertEqual(moves[-1][3], measured)
        self.assertNotIn("left", self.gateway._leases)
        self.assertFalse(any(call[0] == "set_mode"
                             for call in self.transport.calls))

    async def test_heartbeat_expiry_backstops_active_lease(self):
        self.transport.modes["right"] = [gateway.MODE_POSITION] * 7
        with mock.patch.object(gateway, "HEARTBEAT_TIMEOUT_S", 0.12):
            reader, writer = await self.authenticate()
            leased = await self.command(reader, writer, {
                "type": "leased_trajectory", "command_id": 1,
                "arm": "right", "positions_rad": [0.0] * 7,
                "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
            })
            self.assertTrue(leased["accepted"])
            renewed = await self.command(reader, writer, {
                "type": "renew_lease", "command_id": 2,
                "arm": "right", "lease_ms": gateway.MAX_LEASE_MS,
            })
            self.assertTrue(renewed["accepted"])
            measured = [-0.3, 0.2, -0.1, 0.0, 0.1, -0.2, 0.3]
            self.status.states["right"].positions = measured
            expired = await self.read_until(reader, "heartbeat_expired")

        self.assertEqual(expired["type"], "heartbeat_expired")
        moves = [call for call in self.transport.calls
                 if call[0] == "move_joints"]
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[-1][3], measured)
        self.assertNotIn("right", self.gateway._leases)
        self.assertFalse(any(call[0] == "set_mode"
                             for call in self.transport.calls))

    async def test_bad_lease_does_not_replace_accepted_lease(self):
        self.transport.modes["left"] = [gateway.MODE_POSITION] * 7
        reader, writer = await self.authenticate()
        accepted = await self.command(reader, writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "left", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        self.assertTrue(accepted["accepted"])
        original = self.gateway._leases["left"]

        for command_id, bad_lease in enumerate(
                (gateway.MIN_LEASE_MS - 1, gateway.MAX_LEASE_MS + 1,
                 700.0, True), start=2):
            rejected = await self.command(reader, writer, {
                "type": "leased_trajectory", "command_id": command_id,
                "arm": "left", "positions_rad": [0.0] * 7,
                "duration_s": 1.0, "lease_ms": bad_lease,
            })
            self.assertFalse(rejected["accepted"])
            self.assertIn("lease_ms", rejected["error"])
            self.assertIs(self.gateway._leases["left"], original)

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

    async def test_gripper_requires_session_calibration_acceptance(self):
        reader, writer = await self.authenticate()
        state = await self.command(reader, writer, {
            "type": "gripper_state", "command_id": 1, "arm": "left",
        })
        rejected = await self.command(reader, writer, {
            "type": "gripper_control", "command_id": 2, "arm": "left",
            "action": "hold", "force": 5,
        })

        self.assertTrue(state["accepted"])
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )
        self.assertFalse(state["calibration_verified"])
        self.assertFalse(state["feedback_available"])
        self.assertEqual(state["force_min"], gateway.MIN_GRIPPER_FORCE)
        self.assertEqual(state["force_max"], gateway.MAX_GRIPPER_FORCE)
        self.assertFalse(rejected["accepted"])
        self.assertIn("requires calibration acceptance", rejected["error"])
        self.assertFalse(any(call[0] == "control_gripper"
                             for call in self.transport.calls))

    async def test_gripper_calibrate_then_accepts_bounded_actions(self):
        reader, writer = await self.authenticate()
        calibrated = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1, "arm": "left",
        })
        released = await self.command(reader, writer, {
            "type": "gripper_control", "command_id": 2, "arm": "left",
            "action": "release", "force": gateway.MIN_GRIPPER_FORCE,
        })
        held = await self.command(reader, writer, {
            "type": "gripper_control", "command_id": 3, "arm": "left",
            "action": "hold", "force": gateway.MAX_GRIPPER_FORCE,
        })
        right_state = await self.command(reader, writer, {
            "type": "gripper_state", "command_id": 4, "arm": "right",
        })

        self.assertTrue(calibrated["accepted"])
        self.assertTrue(calibrated["calibration_command_accepted"])
        self.assertEqual(calibrated["amber_response"], 1)
        self.assertEqual(
            calibrated["calibration_state"],
            gateway.GRIPPER_CALIBRATION_ACCEPTED_UNVERIFIED,
        )
        self.assertFalse(calibrated["calibration_verified"])
        self.assertFalse(calibrated["completion_verified"])
        self.assertTrue(released["accepted"])
        self.assertEqual(released["action"], "release")
        self.assertEqual(released["force"], gateway.MIN_GRIPPER_FORCE)
        self.assertFalse(released["completion_verified"])
        self.assertTrue(held["accepted"])
        self.assertEqual(held["action"], "hold")
        self.assertEqual(held["force"], gateway.MAX_GRIPPER_FORCE)
        self.assertEqual(
            right_state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )
        calls = [call for call in self.transport.calls
                 if call[0] in {"calibrate_gripper", "control_gripper"}]
        self.assertEqual(calls[0][0:2], ("calibrate_gripper", "left"))
        self.assertEqual(
            calls[1][3:],
            (gateway.GRIPPER_ACTION_RELEASE, gateway.MIN_GRIPPER_FORCE),
        )
        self.assertEqual(
            calls[2][3:],
            (gateway.GRIPPER_ACTION_HOLD, gateway.MAX_GRIPPER_FORCE),
        )

    async def test_gripper_calibration_requires_fresh_arm_telemetry(self):
        self.status.advancing = False
        self.status.states["left"].monotonic_ns = 0
        reader, writer = await self.authenticate()

        calibration = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1,
            "arm": "left",
        })

        self.assertFalse(calibration["accepted"])
        self.assertIn("fresh telemetry is unavailable", calibration["error"])
        self.assertFalse(any(
            call[0] == "calibrate_gripper" for call in self.transport.calls
        ))

    async def test_gripper_calibration_rejects_active_arm_lease(self):
        self.transport.modes["left"] = (
            [gateway.MODE_POSITION] * gateway.JOINT_COUNT
        )
        reader, writer = await self.authenticate()
        trajectory = await self.command(reader, writer, {
            "type": "leased_trajectory", "command_id": 1,
            "arm": "left", "positions_rad": [0.0] * 7,
            "duration_s": 1.0, "lease_ms": gateway.MAX_LEASE_MS,
        })
        self.assertTrue(trajectory["accepted"])

        calibration = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 2,
            "arm": "left",
        })

        self.assertFalse(calibration["accepted"])
        self.assertIn("active motion lease", calibration["error"])
        self.assertFalse(any(
            call[0] == "calibrate_gripper" for call in self.transport.calls
        ))

    async def test_stale_telemetry_clears_gripper_calibration(self):
        reader, writer = await self.authenticate()
        calibrated = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1,
            "arm": "right",
        })
        self.assertTrue(calibrated["accepted"])

        self.status.advancing = False
        self.status.states["right"].monotonic_ns = (
            time.monotonic_ns()
            - int((gateway.MAX_TELEMETRY_AGE_S + 1) * 1_000_000_000)
        )
        for _ in range(50):
            if (self.gateway._gripper_calibration["right"] ==
                    gateway.GRIPPER_CALIBRATION_REQUIRED):
                break
            await asyncio.sleep(0.01)
        self.assertEqual(
            self.gateway._gripper_calibration["right"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )

        # Fresh samples returning after a core outage must not restore the old
        # session-local acceptance.
        self.status.advancing = True
        control = await self.command(reader, writer, {
            "type": "gripper_control", "command_id": 2, "arm": "right",
            "action": "hold", "force": 5,
        })
        self.assertFalse(control["accepted"])
        self.assertIn("requires calibration acceptance", control["error"])
        self.assertFalse(any(
            call[0] == "control_gripper" for call in self.transport.calls
        ))

    async def test_heartbeat_expiry_rejects_late_gripper_calibration(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_calibration(arm, command_id):
            self.transport.calls.append(
                ("calibrate_gripper", arm.name, command_id)
            )
            started.set()
            await release.wait()
            return 1

        with mock.patch.object(gateway, "HEARTBEAT_TIMEOUT_S", 0.12), \
                mock.patch.object(
                    self.transport, "calibrate_gripper",
                    side_effect=blocked_calibration,
                ):
            reader, writer = await self.authenticate()
            await self.write_message(writer, {
                "type": "gripper_calibrate", "command_id": 1,
                "arm": "left",
            })
            await asyncio.wait_for(started.wait(), timeout=0.5)
            expired = await self.read_until(reader, "heartbeat_expired")
            self.assertEqual(expired["type"], "heartbeat_expired")

            # Resuming heartbeat cannot resurrect an operation admitted under
            # the expired authority generation.
            await self.write_message(writer, {"type": "heartbeat"})
            await self.read_until(reader, "heartbeat_ack")
            release.set()
            calibration = await self.read_until(
                reader, "gripper_calibrate_ack"
            )
            state = await self.command(reader, writer, {
                "type": "gripper_state", "command_id": 2,
                "arm": "left",
            })

        self.assertFalse(calibration["accepted"])
        self.assertIn("heartbeat authority changed", calibration["error"])
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )

    async def test_heartbeat_expiry_rejects_late_gripper_control(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_control(arm, command_id, action, force):
            self.transport.calls.append(
                ("control_gripper", arm.name, command_id, action, force)
            )
            started.set()
            await release.wait()
            return 1

        with mock.patch.object(gateway, "HEARTBEAT_TIMEOUT_S", 0.12), \
                mock.patch.object(
                    self.transport, "control_gripper",
                    side_effect=blocked_control,
                ):
            reader, writer = await self.authenticate()
            calibrated = await self.command(reader, writer, {
                "type": "gripper_calibrate", "command_id": 1,
                "arm": "right",
            })
            self.assertTrue(calibrated["accepted"])
            await self.write_message(writer, {"type": "heartbeat"})
            await self.read_until(reader, "heartbeat_ack")
            await self.write_message(writer, {
                "type": "gripper_control", "command_id": 2,
                "arm": "right", "action": "release", "force": 5,
            })
            await asyncio.wait_for(started.wait(), timeout=0.5)
            await self.read_until(reader, "heartbeat_expired")
            await self.write_message(writer, {"type": "heartbeat"})
            await self.read_until(reader, "heartbeat_ack")
            release.set()
            control = await self.read_until(reader, "gripper_control_ack")
            state = await self.command(reader, writer, {
                "type": "gripper_state", "command_id": 3,
                "arm": "right",
            })

        self.assertFalse(control["accepted"])
        self.assertIn("heartbeat authority changed", control["error"])
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )

    async def test_disconnect_rejects_late_gripper_calibration(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_calibration(arm, command_id):
            self.transport.calls.append(
                ("calibrate_gripper", arm.name, command_id)
            )
            started.set()
            await release.wait()
            return 1

        with mock.patch.object(
                self.transport, "calibrate_gripper",
                side_effect=blocked_calibration):
            _, writer = await self.authenticate()
            await self.write_message(writer, {
                "type": "gripper_calibrate", "command_id": 1,
                "arm": "left",
            })
            await asyncio.wait_for(started.wait(), timeout=0.5)
            writer.close()
            await writer.wait_closed()
            release.set()

            for _ in range(100):
                if (self.gateway._controller_session is None and
                        not self.gateway._controller_releasing):
                    break
                await asyncio.sleep(0.01)

        self.assertIsNone(self.gateway._controller_session)
        self.assertFalse(self.gateway._controller_releasing)
        self.assertEqual(
            self.gateway._gripper_calibration["left"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )

    async def test_gripper_rejects_bad_action_force_without_transport_io(self):
        reader, writer = await self.authenticate()
        calibrated = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1, "arm": "right",
        })
        self.assertTrue(calibrated["accepted"])
        calls_after_calibration = list(self.transport.calls)

        bad_values = [
            ("stop", 5),
            ("hold", gateway.MIN_GRIPPER_FORCE - 1),
            ("hold", gateway.MAX_GRIPPER_FORCE + 1),
            ("hold", 5.0),
            ("hold", True),
        ]
        for command_id, (action, force) in enumerate(bad_values, start=2):
            rejected = await self.command(reader, writer, {
                "type": "gripper_control", "command_id": command_id,
                "arm": "right", "action": action, "force": force,
            })
            self.assertFalse(rejected["accepted"])

        self.assertEqual(self.transport.calls, calls_after_calibration)

    async def test_failed_gripper_calibration_stays_required(self):
        reader, writer = await self.authenticate()
        accepted = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1, "arm": "left",
        })
        self.assertTrue(accepted["accepted"])
        self.transport.gripper_calibration_response = 0
        calibration = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 2, "arm": "left",
        })
        state = await self.command(reader, writer, {
            "type": "gripper_state", "command_id": 3, "arm": "left",
        })

        self.assertFalse(calibration["accepted"])
        self.assertIn("rejected gripper calibration", calibration["error"])
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )

    async def test_ambiguous_gripper_calibration_timeout_fails_closed(self):
        reader, writer = await self.authenticate()
        accepted = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1, "arm": "right",
        })
        self.assertTrue(accepted["accepted"])

        with mock.patch.object(
            self.transport,
            "calibrate_gripper",
            side_effect=asyncio.TimeoutError,
        ):
            ambiguous = await self.command(reader, writer, {
                "type": "gripper_calibrate", "command_id": 2,
                "arm": "right",
            })
        state = await self.command(reader, writer, {
            "type": "gripper_state", "command_id": 3, "arm": "right",
        })

        self.assertFalse(ambiguous["accepted"])
        self.assertEqual(ambiguous["error"], "TimeoutError")
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )
        control_calls = [
            call for call in self.transport.calls
            if call[0] == "control_gripper"
        ]
        self.assertEqual(control_calls, [])

    async def test_heartbeat_expiry_clears_gripper_calibration(self):
        with mock.patch.object(gateway, "HEARTBEAT_TIMEOUT_S", 0.12):
            reader, writer = await self.authenticate()
            calibrated = await self.command(reader, writer, {
                "type": "gripper_calibrate", "command_id": 1,
                "arm": "left",
            })
            self.assertTrue(calibrated["accepted"])
            expired = await self.read_until(reader, "heartbeat_expired")
            self.assertEqual(expired["type"], "heartbeat_expired")
            state = await self.command(reader, writer, {
                "type": "gripper_state", "command_id": 2, "arm": "left",
            })
            control = await self.command(reader, writer, {
                "type": "gripper_control", "command_id": 3, "arm": "left",
                "action": "release", "force": 5,
            })

        self.assertTrue(state["accepted"])
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )
        self.assertFalse(control["accepted"])
        self.assertEqual(control["arm"], "left")
        self.assertGreaterEqual(control["gateway_latency_ms"], 0)
        self.assertIn("heartbeat expired", control["error"])

    async def test_disconnect_clears_gripper_calibration_for_next_session(self):
        reader, writer = await self.authenticate()
        calibrated = await self.command(reader, writer, {
            "type": "gripper_calibrate", "command_id": 1, "arm": "left",
        })
        self.assertTrue(calibrated["accepted"])
        writer.close()
        await writer.wait_closed()
        for _ in range(20):
            if self.gateway._controller_session is None:
                break
            await asyncio.sleep(0.01)

        next_reader, next_writer = await self.authenticate()
        state = await self.command(next_reader, next_writer, {
            "type": "gripper_state", "command_id": 1, "arm": "left",
        })
        self.assertEqual(
            state["calibration_state"],
            gateway.GRIPPER_CALIBRATION_REQUIRED,
        )

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

    def test_vendor_gripper_packet_layouts(self):
        self.assertEqual(ctypes.sizeof(gateway.GripperCalibrationCommand), 12)
        self.assertEqual(ctypes.sizeof(gateway.GripperCommand), 13)

        transport = gateway.AmberUDPTransport("unused")
        response = gateway.CommandResponse()
        response.respond = 1
        arm = gateway.ArmConfig("right", 26002, "Right_ArmStatus")
        with mock.patch.object(
            transport, "_exchange", return_value=response
        ) as exchange:
            result = transport._calibrate_gripper_blocking(arm, 0x12345678)
        calibration = exchange.call_args.args[1]
        self.assertEqual(result, 1)
        self.assertEqual(calibration.cmd_no, 7)
        self.assertEqual(calibration.length, 12)
        self.assertEqual(calibration.counter, 0x12345678)
        self.assertEqual(calibration.joint_id, gateway.GRIPPER_NUMBER)

        with mock.patch.object(
            transport, "_exchange", return_value=response
        ) as exchange:
            result = transport._control_gripper_blocking(
                arm, 0x87654321, gateway.GRIPPER_ACTION_HOLD, 20
            )
        command = exchange.call_args.args[1]
        self.assertEqual(result, 1)
        self.assertEqual(command.cmd_no, 9)
        self.assertEqual(command.length, 13)
        self.assertEqual(command.counter, 0x87654321)
        self.assertEqual(command.action, gateway.GRIPPER_ACTION_HOLD)
        self.assertEqual(command.intensity, 20)
        self.assertFalse(command.version)


class TelemetryBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lcm_degrees_are_normalized_once_and_velocity_is_not_invented(self):
        degrees = [0.0, -119.1484375, 0.0, 38.203125, 0.0, -1.609375, 0.0]
        message = SimpleNamespace(
            jointPosition=degrees, jointVelocity=[4.243991583e-314] * 7,
            jointCurrent=[0.2] * 7, jointStatus=[2.0] * 7,
        )
        # Exercise the actual LCM callback and snapshot without loading LCM.
        bridge = gateway.LCMStatusBridge.__new__(gateway.LCMStatusBridge)
        bridge._arm_status_type = SimpleNamespace(decode=lambda data: message)
        bridge._states = {"left": gateway.ArmState()}
        bridge._lock = threading.Lock()
        bridge._handler("left")("Left_ArmStatus", b"fixture")
        state = bridge.snapshot()["left"]
        for raw, normalized in zip(degrees, state.positions):
            self.assertAlmostEqual(normalized, raw * math.pi / 180.0)
            # Installed vendor UDP status uses the rounded factor 57.2958.
            self.assertAlmostEqual(normalized, raw / 57.2958, delta=1e-6)
        self.assertFalse(state.velocities_available)
        self.assertEqual(state.velocities, [])
        self.assertEqual(state.currents, message.jointCurrent)
        self.assertEqual(state.statuses, message.jointStatus)
        self.assertTrue(gateway.GatewayServer._state_is_valid_and_fresh(state))

    async def test_udp_boundary_rejects_bad_targets_before_opening_socket(self):
        transport = gateway.AmberUDPTransport("unused")
        arm = gateway.ArmConfig("left", 26001, "Left_ArmStatus")
        invalid_targets = ([0.0] * 6, [True] * 7, [float("nan")] * 7,
                           [0.0, -119.1484375, 0.0, 0.0, 0.0, 0.0, 0.0])
        with mock.patch.object(gateway.socket, "socket") as open_socket:
            for target in invalid_targets:
                with self.subTest(target=target), self.assertRaises(ValueError):
                    transport._move_joints_blocking(arm, 1, target, 0.65)
            open_socket.assert_not_called()


if __name__ == "__main__":
    unittest.main()
