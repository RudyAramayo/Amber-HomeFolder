#!/usr/bin/env python3
"""Fully fake tests for the privileged Amber recovery state machine."""

from __future__ import annotations

import io
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import rob_amber_recovery as recovery


class FakeHost:
    def __init__(self) -> None:
        self.now = 0.0
        self.actions: list[tuple[str, str]] = []
        self.units = {
            recovery.GATEWAY_UNIT: recovery.UnitProperties("active", "running", 101, 0),
            recovery.CORE_UNIT: recovery.UnitProperties("active", "running", 0, 0),
        }
        self.mapping = dict(recovery.EXPECTED_SERIAL_BY_INTERFACE)
        self.serials = {
            "/dev/ttyACM1": recovery.EXPECTED_SERIAL_BY_INTERFACE["can10"],
            "/dev/ttyACM0": recovery.EXPECTED_SERIAL_BY_INTERFACE["can11"],
        }
        self.process_records: list[recovery.ProcessRecord] = []
        self.listener_records: list[recovery.ListenerRecord] = []
        self.can_packets = {"can10": [100, 200], "can11": [300, 400]}
        self.can_flags = {
            "can10": frozenset(("UP", "LOWER_UP")),
            "can11": frozenset(("UP", "LOWER_UP")),
        }
        self.can_errors = {"can10": [0, 0, 0, 0], "can11": [0, 0, 0, 0]}
        self.advance_can = True
        self.leave_process_after_stop: str | None = None
        self.omit_core_after_start: str | None = None
        self.gateway_address = "127.0.0.1"
        self.gateway_restarts_after_start = 0
        self.gateway_fails_on_settle = False
        self.startup_trusted = True
        self.dependencies_ready = True
        self.slcand_uid = 0
        self.core_uid = 1000
        self.fail_actions: set[tuple[str, str]] = set()
        self.unexpected_actions: set[tuple[str, str]] = set()
        self._install_running_stack()

    @staticmethod
    def _process(pid: int, *arguments: str, unit: str,
                 uid: int | None = None) -> recovery.ProcessRecord:
        if uid is None:
            uid = 0 if os.path.basename(arguments[0]) == "slcand" else 1000
        return recovery.ProcessRecord(
            pid=pid,
            arguments=tuple(arguments),
            cgroup=f"0::/system.slice/{unit}\n",
            uid=uid,
        )

    def _install_running_stack(self) -> None:
        self.process_records = [
            self._process(
                101, "/usr/bin/python3", "/home/amber/rob_gateway/rob_amber_gateway.py",
                unit=recovery.GATEWAY_UNIT,
            ),
            self._process(
                201, "/usr/bin/slcand", "-o", "-c", "-s8", "/dev/ttyACM1", "can10",
                unit=recovery.CORE_UNIT, uid=self.slcand_uid,
            ),
            self._process(
                202, "/usr/bin/slcand", "-o", "-c", "-s8", "/dev/ttyACM0", "can11",
                unit=recovery.CORE_UNIT, uid=self.slcand_uid,
            ),
            self._process(301, "./amber_core_L", unit=recovery.CORE_UNIT,
                          uid=self.core_uid),
            self._process(302, "./amber_core_R", unit=recovery.CORE_UNIT,
                          uid=self.core_uid),
        ]
        self.listener_records = [
            recovery.ListenerRecord("tcp", self.gateway_address, 7443, frozenset((101,))),
            recovery.ListenerRecord("udp", "0.0.0.0", 26001, frozenset((301,))),
            recovery.ListenerRecord("udp", "0.0.0.0", 26002, frozenset((302,))),
        ]

    def systemctl(self, action: str, unit: str) -> None:
        self.actions.append((action, unit))
        if (action, unit) in self.unexpected_actions:
            raise RuntimeError("fake unexpected host failure containing secret material")
        if (action, unit) in self.fail_actions:
            raise recovery.HostCommandFailure(f"systemctl {action} {unit}")
        if action == "stop" and unit == recovery.GATEWAY_UNIT:
            self.units[unit] = recovery.UnitProperties("inactive", "dead", 0, 0)
            self.process_records = [record for record in self.process_records
                                    if not record.is_named("rob_amber_gateway.py")]
            self.listener_records = [record for record in self.listener_records
                                     if record.port != 7443]
        elif action == "stop" and unit == recovery.CORE_UNIT:
            self.units[unit] = recovery.UnitProperties("inactive", "dead", 0, 0)
            names = {"slcand", "amber_core_L", "amber_core_R"}
            kept = [record for record in self.process_records
                    if not record.names() & names]
            if self.leave_process_after_stop:
                kept.append(self._process(
                    999, self.leave_process_after_stop, unit=recovery.CORE_UNIT
                ))
            self.process_records = kept
            self.listener_records = [record for record in self.listener_records
                                     if record.port not in (26001, 26002)]
        elif action == "start" and unit == recovery.CORE_UNIT:
            self.units[unit] = recovery.UnitProperties("active", "running", 0, 0)
            core_records = [
                self._process(
                    211, "/usr/bin/slcand", "-o", "-c", "-s8",
                    "/dev/ttyACM1", "can10", unit=recovery.CORE_UNIT,
                    uid=self.slcand_uid,
                ),
                self._process(
                    212, "/usr/bin/slcand", "-o", "-c", "-s8",
                    "/dev/ttyACM0", "can11", unit=recovery.CORE_UNIT,
                    uid=self.slcand_uid,
                ),
                self._process(311, "./amber_core_L", unit=recovery.CORE_UNIT,
                              uid=self.core_uid),
                self._process(312, "./amber_core_R", unit=recovery.CORE_UNIT,
                              uid=self.core_uid),
            ]
            if self.omit_core_after_start:
                core_records = [record for record in core_records
                                if not record.is_named(self.omit_core_after_start)]
            self.process_records.extend(core_records)
            if self.omit_core_after_start != "amber_core_L":
                self.listener_records.append(
                    recovery.ListenerRecord("udp", "0.0.0.0", 26001, frozenset((311,)))
                )
            if self.omit_core_after_start != "amber_core_R":
                self.listener_records.append(
                    recovery.ListenerRecord("udp", "0.0.0.0", 26002, frozenset((312,)))
                )
        elif action == "reset-failed" and unit == recovery.GATEWAY_UNIT:
            current = self.units[unit]
            self.units[unit] = recovery.UnitProperties(
                current.active_state, current.sub_state, current.main_pid, 0
            )
        elif action == "start" and unit == recovery.GATEWAY_UNIT:
            self.units[unit] = recovery.UnitProperties(
                "active", "running", 111, self.gateway_restarts_after_start
            )
            self.process_records.append(self._process(
                111, "/usr/bin/python3", "/home/amber/rob_gateway/rob_amber_gateway.py",
                unit=recovery.GATEWAY_UNIT,
            ))
            self.listener_records.append(
                recovery.ListenerRecord(
                    "tcp", self.gateway_address, 7443, frozenset((111,))
                )
            )
        else:
            raise AssertionError(f"unexpected fake systemctl call: {action} {unit}")

    def unit_properties(self, unit: str) -> recovery.UnitProperties:
        return self.units[unit]

    def processes(self) -> list[recovery.ProcessRecord]:
        return list(self.process_records)

    def listeners(self) -> list[recovery.ListenerRecord]:
        return list(self.listener_records)

    def configured_mapping(self) -> dict[str, str]:
        return dict(self.mapping)

    def validate_startup_trust(self) -> None:
        if not self.startup_trusted:
            raise recovery.RecoveryFailure(
                "unsafe_startup_files", "Startup trust fixture failed."
            )

    def validate_dependencies(self) -> None:
        if not self.dependencies_ready:
            raise recovery.RecoveryFailure(
                "dependency_missing", "Dependency fixture failed."
            )

    def amber_uid(self) -> int:
        return 1000

    def usb_serials(self) -> dict[str, str]:
        return dict(self.serials)

    def can_statistics(self, interface: str) -> recovery.CANStatistics:
        packets = self.can_packets[interface]
        errors = self.can_errors[interface]
        return recovery.CANStatistics(
            self.can_flags[interface], packets[0], packets[1],
            errors[0], errors[1], errors[2], errors[3],
        )

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.advance_can and self.units[recovery.CORE_UNIT].active_state == "active":
            for packets in self.can_packets.values():
                packets[0] += 5
                packets[1] += 7
        if self.gateway_fails_on_settle \
                and self.units[recovery.GATEWAY_UNIT].active_state == "active":
            self.units[recovery.GATEWAY_UNIT] = recovery.UnitProperties(
                "failed", "failed", 0, 1
            )
            self.process_records = [record for record in self.process_records
                                    if not record.is_named("rob_amber_gateway.py")]
            self.listener_records = [record for record in self.listener_records
                                     if record.port != 7443]


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, host: FakeHost) -> tuple[bool, list[dict]]:
        output = io.StringIO()
        emitter = recovery.JSONLineEmitter(output, "00000000-0000-0000-0000-000000000001")
        controller = recovery.RecoveryController(
            host,
            emitter,
            recovery.RecoveryTimeouts(
                stop_seconds=0.10,
                core_start_seconds=0.10,
                gateway_start_seconds=0.10,
                can_sample_seconds=0.05,
                gateway_settle_seconds=0.05,
                poll_seconds=0.02,
            ),
        )
        result = controller.run()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        return result, records

    def test_success_uses_exact_order_and_protocol_schema(self) -> None:
        host = FakeHost()
        success, records = self.run_recovery(host)

        self.assertTrue(success)
        self.assertEqual(host.actions, [
            ("stop", recovery.GATEWAY_UNIT),
            ("stop", recovery.CORE_UNIT),
            ("start", recovery.CORE_UNIT),
            ("reset-failed", recovery.GATEWAY_UNIT),
            ("start", recovery.GATEWAY_UNIT),
        ])
        self.assertEqual(sum(action == ("start", recovery.CORE_UNIT)
                             for action in host.actions), 1)
        self.assertTrue(records[-1]["success"])
        self.assertTrue(all(record["protocol"] == recovery.PROTOCOL
                            for record in records))
        self.assertTrue(all(record["operation"] == recovery.OPERATION
                            for record in records))
        self.assertEqual(
            {record["operation_id"] for record in records},
            {"00000000-0000-0000-0000-000000000001"},
        )
        self.assertEqual(
            sum(record["type"] == "result" for record in records), 1
        )
        self.assertEqual(records[-1]["type"], "result")
        progress = [record for record in records if record["type"] == "progress"]
        self.assertTrue(all("stage" in record and "message" in record
                            for record in progress))

    def test_wrong_root_owned_mapping_fails_before_core_start(self) -> None:
        host = FakeHost()
        host.mapping["can11"] = "wrong"
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertNotIn(("start", recovery.CORE_UNIT), host.actions)
        self.assertEqual(records[-1]["code"], "mapping_mismatch")
        self.assertFalse(records[-1]["recovery_started"])
        self.assertEqual(host.actions, [])

    def test_missing_usb_adapter_fails_before_core_start(self) -> None:
        host = FakeHost()
        del host.serials["/dev/ttyACM0"]
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "usb_adapter_missing")
        self.assertEqual(host.actions, [])

    def test_untrusted_startup_files_are_rejected(self) -> None:
        host = FakeHost()
        host.startup_trusted = False
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "unsafe_startup_files")
        self.assertEqual(host.actions, [])

    def test_missing_dependency_is_rejected_before_stop(self) -> None:
        host = FakeHost()
        host.dependencies_ready = False
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "dependency_missing")
        self.assertEqual(host.actions, [])

    def test_orphan_process_prevents_restart(self) -> None:
        host = FakeHost()
        host.leave_process_after_stop = "slcand"
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "stack_stop_incomplete")
        self.assertNotIn(("start", recovery.CORE_UNIT), host.actions)
        self.assertTrue(records[-1]["gateway_stopped"])

    def test_partial_core_start_rolls_back_core_and_gateway(self) -> None:
        host = FakeHost()
        host.omit_core_after_start = "amber_core_R"
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "core_start_incomplete")
        self.assertEqual(sum(action == ("start", recovery.CORE_UNIT)
                             for action in host.actions), 1)
        self.assertEqual(host.actions[-2:], [
            ("stop", recovery.GATEWAY_UNIT), ("stop", recovery.CORE_UNIT)
        ])
        self.assertTrue(records[-1]["gateway_stopped"])
        self.assertTrue(records[-1]["restarted_core_stack_stopped"])

    def test_process_users_must_match_least_privilege_contract(self) -> None:
        for attribute, value in (("slcand_uid", 1000), ("core_uid", 0)):
            with self.subTest(attribute=attribute):
                host = FakeHost()
                setattr(host, attribute, value)
                success, records = self.run_recovery(host)
                self.assertFalse(success)
                self.assertEqual(records[-1]["code"], "core_start_incomplete")
                self.assertTrue(records[-1]["gateway_stopped"])

    def test_nonadvancing_can_counters_roll_back(self) -> None:
        host = FakeHost()
        host.advance_can = False
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "can_not_advancing")
        self.assertNotIn(("start", recovery.GATEWAY_UNIT), host.actions)
        self.assertEqual(host.actions[-1], ("stop", recovery.CORE_UNIT))

    def test_can_errors_roll_back(self) -> None:
        host = FakeHost()
        host.can_errors["can11"][0] = 1
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "can_errors")
        self.assertNotIn(("start", recovery.GATEWAY_UNIT), host.actions)

    def test_gateway_must_be_loopback_and_have_zero_restarts(self) -> None:
        for address, restarts in (("0.0.0.0", 0), ("127.0.0.1", 1)):
            with self.subTest(address=address, restarts=restarts):
                host = FakeHost()
                host.gateway_address = address
                host.gateway_restarts_after_start = restarts
                success, records = self.run_recovery(host)
                self.assertFalse(success)
                self.assertEqual(records[-1]["code"], "gateway_start_incomplete")
                self.assertEqual(host.actions[-2:], [
                    ("stop", recovery.GATEWAY_UNIT), ("stop", recovery.CORE_UNIT)
                ])
                self.assertTrue(records[-1]["gateway_stopped"])

    def test_gateway_must_survive_settle_window(self) -> None:
        host = FakeHost()
        host.gateway_fails_on_settle = True
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "gateway_restart_loop")
        self.assertTrue(records[-1]["gateway_stopped"])

    def test_systemctl_start_failure_leaves_gateway_stopped(self) -> None:
        host = FakeHost()
        host.fail_actions.add(("start", recovery.CORE_UNIT))
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "host_command_failed")
        self.assertTrue(records[-1]["gateway_stopped"])

    def test_unexpected_exception_rolls_back_with_generic_result(self) -> None:
        host = FakeHost()
        host.unexpected_actions.add(("start", recovery.CORE_UNIT))
        success, records = self.run_recovery(host)

        self.assertFalse(success)
        self.assertEqual(records[-1]["code"], "internal_error")
        self.assertNotIn("secret material", json.dumps(records))
        self.assertEqual(host.actions[-2:], [
            ("stop", recovery.GATEWAY_UNIT), ("stop", recovery.CORE_UNIT)
        ])
        self.assertTrue(records[-1]["gateway_stopped"])

    def test_output_is_bounded_and_contains_no_command_output(self) -> None:
        stream = io.StringIO()
        emitter = recovery.JSONLineEmitter(stream, "fixture")
        emitter.emit("progress", stage="test", message="secret=" + "x" * 10_000)
        encoded = stream.getvalue().encode("utf-8")
        self.assertLessEqual(max(map(len, encoded.splitlines())),
                             recovery.MAXIMUM_JSON_LINE_BYTES)
        parsed = json.loads(stream.getvalue())
        self.assertEqual(parsed["type"], "progress")
        self.assertEqual(parsed["operation"], recovery.OPERATION)
        self.assertEqual(parsed["operation_id"], "fixture")
        self.assertLessEqual(len(parsed["message"]), recovery.MAXIMUM_DETAIL_CHARACTERS)

    def test_lock_is_nonblocking_and_single_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "recovery.lock")
            first = recovery.RecoveryLock(path)
            second = recovery.RecoveryLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.close()
            self.assertTrue(second.acquire())
            second.close()

    def test_main_rejects_arguments_before_privilege_check(self) -> None:
        output = io.StringIO()
        status = recovery.main(["rob-amber-recover", "unexpected"], output)
        result = json.loads(output.getvalue())
        self.assertEqual(status, 64)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "arguments_forbidden")

    def test_main_requires_root(self) -> None:
        output = io.StringIO()
        with mock.patch.object(recovery.os, "geteuid", return_value=501):
            status = recovery.main(["rob-amber-recover"], output)
        result = json.loads(output.getvalue())
        self.assertEqual(status, 77)
        self.assertEqual(result["code"], "root_required")

    def test_real_backend_systemctl_jobs_are_nonblocking(self) -> None:
        completed = recovery.subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            recovery.subprocess, "run", return_value=completed
        ) as run:
            backend = recovery.RealHostBackend()
            backend.systemctl("start", recovery.CORE_UNIT)
            start_arguments = run.call_args.args[0]
            self.assertIn("--no-block", start_arguments)

            backend.systemctl("reset-failed", recovery.GATEWAY_UNIT)
            reset_arguments = run.call_args.args[0]
            self.assertNotIn("--no-block", reset_arguments)

        with self.assertRaises(recovery.RecoveryFailure):
            recovery.RealHostBackend().systemctl("restart", recovery.CORE_UNIT)

    def test_effective_gateway_unit_must_remain_least_privilege(self) -> None:
        safe_properties = "\n".join((
            f"FragmentPath={recovery.GATEWAY_UNIT_PATH}",
            "DropInPaths=",
            "User=amber",
            "Group=amber",
            "ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
            "/home/amber/rob_gateway/rob_amber_gateway.py --listen-host "
            "127.0.0.1 ; ignore_errors=no ; }",
            "ExecStartPre=",
            "ExecStartPost=",
            "ExecCondition=",
            "ExecReload=",
            "ExecStop=",
            "ExecStopPost=",
        ))
        backend = recovery.RealHostBackend()
        with mock.patch.object(
            backend, "_run",
            return_value=recovery.subprocess.CompletedProcess(
                [], 0, safe_properties, ""
            ),
        ):
            backend._validate_effective_gateway_unit()

        unsafe_properties = safe_properties.replace("User=amber", "User=root")
        with mock.patch.object(
            backend, "_run",
            return_value=recovery.subprocess.CompletedProcess(
                [], 0, unsafe_properties, ""
            ),
        ):
            with self.assertRaises(recovery.RecoveryFailure) as context:
                backend._validate_effective_gateway_unit()
        self.assertEqual(context.exception.code, "unsafe_gateway_unit")

    def test_install_templates_preserve_fixed_privilege_contract(self) -> None:
        directory = Path(__file__).resolve().parent
        sudoers = (directory / "rob-amber-recovery.sudoers").read_text()
        service = (directory / "rob-amber-gateway.service").read_text()
        service_bytes = (directory / "rob-amber-gateway.service").read_bytes()
        mapping = json.loads((directory / "can-interfaces.json").read_text())
        # The Mac repository keeps this under system/etc; push-gateway stages
        # the same reviewed bytes beside the tests on Ubuntu.
        rc_local_candidates = (
            directory / "rc.local.reviewed",
            directory.parent.parent / "system/etc/rc.local",
        )
        rc_local_path = next(
            (candidate for candidate in rc_local_candidates if candidate.is_file()),
            None,
        )
        self.assertIsNotNone(rc_local_path, "reviewed rc.local fixture is missing")
        rc_local = rc_local_path.read_text()

        self.assertIn('/usr/local/sbin/rob-amber-recover ""', sudoers)
        self.assertNotIn("ALL=(ALL) ALL", sudoers)
        self.assertIn("After=network-online.target rc-local.service", service)
        self.assertIn("Requisite=rc-local.service", service)
        self.assertEqual(
            hashlib.sha256(service_bytes).hexdigest(),
            recovery.GATEWAY_UNIT_SHA256,
        )
        self.assertEqual(mapping, recovery.EXPECTED_SERIAL_BY_INTERFACE)
        self.assertIn(recovery.SAFE_RC_LOCAL_MARKER, rc_local)
        self.assertIn("/usr/sbin/runuser -u amber", rc_local)
        self.assertTrue((directory / "rob_amber_recovery.py").read_text().startswith(
            "#!/usr/bin/python3\n"
        ))
        self.assertTrue((directory / "rob_amber_init_can.py").read_text().startswith(
            "#!/usr/bin/python3\n"
        ))


if __name__ == "__main__":
    unittest.main()
