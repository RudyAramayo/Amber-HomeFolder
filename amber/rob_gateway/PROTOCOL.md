# ROB Amber gateway protocol

The gateway listens on an SSH-forwarded TCP connection and exchanges one
compact JSON object per line. It must remain bound to `127.0.0.1`; the bearer
token authenticates the connection but does not encrypt it.

After the server sends `challenge`, the client sends:

```json
{"type":"hello","protocol":"rob-amber-gateway/1","token":"..."}
```

The server replies with `ready`, including `supported_commands`, a list of the
command types implemented by this gateway. Clients must require explicit
advertisement of `gripper_state`, `gripper_calibrate`, and `gripper_control`
before sending optional gripper requests. Older gateways omit this list;
their arm telemetry and original arm-mode commands remain usable, with gripper
controls unavailable until the gateway is updated. Unsupported requests return
`command_error` with `command_type` and `error`; this is not an acknowledgement
of hardware execution.

Only one authenticated controller session is
allowed at a time; a second valid-token connection receives an error and is
closed. The owner receives telemetry and is the only session that can query
modes or issue commands. Ownership is released when its TCP connection closes.

Send a `heartbeat` more frequently than the advertised `heartbeat_timeout_s`.
Commands other than `mode_query`, `gripper_state`, the fail-safe `deactivate`,
and `priority_hold` are rejected after heartbeat expiry. Heartbeat expiry also
cancels every active arm lease, clears the session-local gripper calibration
acceptances, and runs the measured-pose hold described below.

Every command has a strictly increasing unsigned 32-bit `command_id` and one
of the arm names `left` or `right`:

```json
{"type":"mode_query","command_id":1,"arm":"left"}
{"type":"activate","command_id":2,"arm":"left"}
{"type":"position_mode","command_id":3,"arm":"left"}
{"type":"hold_current","command_id":4,"arm":"left"}
{"type":"trajectory","command_id":5,"arm":"left","positions_rad":[0,0,0,0,0,0,0],"duration_s":2.0}
{"type":"leased_trajectory","command_id":6,"arm":"left","positions_rad":[0,0,0,0,0,0,0],"duration_s":2.0,"lease_ms":1000}
{"type":"renew_lease","command_id":7,"arm":"left","lease_ms":1000}
{"type":"priority_hold","command_id":8,"arm":"left"}
{"type":"deactivate","command_id":9,"arm":"left"}
```

Each reply is named after the request, for example `position_mode_ack`. It
contains `accepted`, `command_id`, `arm`, and `gateway_latency_ms`. Successful
mode operations also contain seven `modes` and seven human-readable
`mode_names`. Rejections have `accepted:false` and an `error` string.

The increasing `command_id` sequence is session-global, while execution uses a
bounded FIFO queue for each arm. Commands for one arm remain ordered, but left
and right commands may execute concurrently and their acknowledgements may arrive
out of command-ID order; clients must correlate every result by `command_id`.
Each arm accepts at most 32 pending commands. Queue overflow consumes the command
ID and returns `accepted:false` instead of retaining unbounded work. A
`priority_hold` is a same-arm barrier: it rejects and removes every older command
that is still queued, then runs immediately after the one same-arm hardware
exchange already in progress. An opposite-arm exchange cannot delay it.

Disconnect invalidates controller ownership before in-flight workers drain. No
late command may install a lease for the departed session; if an Amber trajectory
crossed the hardware boundary before invalidation, the gateway schedules a fresh
measured-pose hold. A replacement controller session is not admitted until that
drain and the disconnect hold sweeps finish.

`position_mode` is deliberately a composite operation:

1. Require a new, bounded, finite position sample before any mode change, then
   send vendor command 10 for active mode (1).
2. Poll vendor command 110 until all seven joints report mode 1.
3. Capture a new, finite LCM telemetry sample no older than 250 ms.
4. Send vendor command 10 for position mode (2).
5. Poll command 110 until all seven joints report mode 2.
6. Send the captured seven positions as a 0.65-second hold trajectory.

The vendor command-110 request is the 12-byte form used by the checked-in
Amber API (`cmd_no`, `length`, `counter`, and the 32-bit all-joints selector
`joint_id = 8`). A header-only 8-byte request does not match that API.

**Vendor mode-switch warning:** Amber's documentation says a mode transition
momentarily cuts actuator power. Before `activate` or `position_mode`, support
the arm or place it at a safe initial pose, clear the workspace, and keep the
physical E-stop operator ready. Software pose capture does not remove that
physical requirement.

The acknowledgement includes `active_modes`, `captured_positions_rad`, and the
three vendor responses. `hold_current` similarly requires verified position
mode, captures a new telemetry sample, and holds that measured pose.

The deployed core can return vendor response `0` after actually changing mode
(observed on L10 activation on 2026-09-23). For mode changes only, the gateway
reconciles that response with all seven mode-query results **and a new, fresh
CAN-backed sample whose seven joint statuses match the requested mode**.
Disagreement or missing feedback fails the operation; it never resends the
change to obtain a different acknowledgement. The original `0` remains in
`amber_response`. Response `1` retains the existing mode-query verification;
other response values fail immediately. Trajectory and gripper acknowledgement
requirements are unchanged.

This update passed 54 gateway fixtures locally and on the robot and was deployed
to `/home/amber/rob_gateway` on 2026-09-23. Only `rob-amber-gateway.service` was
restarted, and its active status and deployed hashes were verified. Source
SHA-256: `abf962f8487ce4a66d0ba7c20966e0dcb4db7c4c404bf184feac29a0c07a38c1`;
test SHA-256: `0a4d5a2c72dace63462cd535a3afe3be66d4c1637521082652234d90ba7f43e7`.
The replaced files are retained in `.mode-reconcile.woCaLA` on that robot.
Fixture success does not establish completion of a live arm route.

Normal `trajectory` requests are accepted only while all seven joints report
position mode and LCM telemetry is fresh. Requests require exactly seven finite
positions, a duration from 0.65 through 10 seconds, and these inclusive limits:

| Joint | Minimum (rad) | Maximum (rad) |
| --- | ---: | ---: |
| 1 | -2.4435 | 2.4435 |
| 2 | -2.3213 | 2.3213 |
| 3–6 | -2.2863 | 2.2863 |
| 7 | -3.05 | 3.05 |

The same bounds apply to measured feedback used by **every** hold path,
including position-mode entry, priority holds, and disconnect/lease backstops.
The UDP transport also validates every outgoing joint vector before opening a
socket. Invalid feedback blocks activation and movement; `deactivate` and
`mode_query` remain available. These checks do not establish physical clearance
by themselves: the vendor core can continue publishing cached data. The
independent CAN checks below also apply before activation, trajectories and
every measured-pose hold.

## Independent motor feedback

The September 22 wrist probe exposed a partial bus loss: only CAN reply ID
`0x91` remained on physical right / L10 / can10, while the core kept publishing
all seven cached joint values with advancing LCM sequence numbers. Core packet
arrival therefore cannot establish individual motor freshness.

The gateway passively listens on `--left-can-interface can10` and
`--right-can-interface can11`. These names follow the **legacy core identities**:
gateway `left` is physical right, and gateway `right` is physical left. The
interfaces must be distinct. This monitor never transmits a CAN frame or
changes an interface, motor mode or target. It accepts only complete remote
standard replies `0x91` through `0x98`, rejecting local transmit echoes,
truncated, extended, remote-request and error frames.

Telemetry adds:

- `controller_sample_age_ms`: age of the most recent LCM callback;
- `joint_feedback_age_ms`: seven kernel CAN receive ages, with `null` for a
  motor that has not replied since the monitor started or was invalidated;
- `gripper_feedback_age_ms`: the corresponding age for `0x98`;
- `sample_age_ms`: the maximum of the controller age and all seven joint
  feedback ages, or `null` if any required observation is unknown.

Kernel timestamps prevent an old queued reply from acquiring a new receipt
time when the listener catches up. The deployed Linux monitor requires
`SO_TIMESTAMPNS_NEW`; unavailable timestamps fail closed. Interface/socket
failure invalidates its observations, and a detected wall/monotonic clock
alignment change flushes the queue before reacquiring feedback. See the
[Linux timestamp interface](https://docs.kernel.org/networking/timestamping.html).

All seven joint replies and LCM must be at most 250 ms old before a new arm
command is admitted. Gripper calibration/control additionally requires a
current eighth reply; losing it clears calibration acceptance. Positions,
currents and modes remain visible as **cached controller data** when feedback
is missing. A reply establishes device liveness, not verified physical
position, velocity, gripper completion or clearance. A core mode acknowledgement
alone does not prove that an unreachable servo changed mode.

Deactivation and mode queries remain available. No command is replayed by the
monitor when feedback returns. These admission checks do **not** cancel an
already dispatched vendor trajectory or clear a target retained in the core
or motor. Support/power isolation and supervised recovery remain necessary
after communication loss; software cannot hold an unreachable motor.

## Telemetry units and availability

The installed vendor cores publish LCM `jointPosition` in **degrees**, while
UDP position commands and command-1 status replies use **radians**. The gateway
converts LCM positions with `radians()` exactly once before exporting
`positions_rad` or capturing a hold. No side, direction, mounting-angle, or
encoder-zero correction is included in this unit conversion.

Evidence from September 22, 2026: both deployed core binaries match the
checked-in L-10 core SHA-256
`554e7088b94b98f03f152f394c5e5b1d1ecfd16dacd470889e65dc83c36d2100`.
`RosInterface::Publish()` copies `directControl::jointPositionNow` unchanged
into LCM; the command-1 handler divides that same array by `57.2958` before
returning floats. The live raw J2 sample `-119.1484375` therefore represents
about `-2.07953` rad, not `-119.1484375` rad. The all-zero sample after the power
cycle cannot independently confirm a conversion factor.

The same core forwards an undocumented raw CAN velocity field. Its physical
scale and validity are unverified, so this LCM adapter reports
`"velocities_available":false` and `"velocities_rad_s":null` instead of
inventing zero velocities. Updated Cerebro retains position/current/status
diagnostics but leaves velocity cells empty and closes reference/settling gates
that require seven verified velocities. Older clients may discard these
telemetry samples and must be rebuilt before using diagnostics. A future
verified velocity source may supply seven finite rad/s values with availability
true; clients retain compatibility with legacy finite velocity arrays.

## Leased trajectories and the backstop hold

`leased_trajectory` applies exactly the same mode, telemetry, position,
duration, and Amber-response checks as `trajectory`. It additionally requires
an integer `lease_ms` from 700 through 1500, inclusive. A successful reply
echoes `lease_ms` and supplies `lease_deadline_monotonic_ns` in the gateway's
monotonic clock domain.

Each arm has an independent monotonic lease watchdog. Only an Amber-accepted
leased trajectory replaces that arm's motion lease generation; validation
failures, mode failures, stale telemetry, and rejected or ambiguous Amber
requests leave the previous lease in force. Replacing one arm's lease does not
affect the other arm.

An authenticated exclusive controller can extend the current deadline with:

```json
{"type":"renew_lease","command_id":7,"arm":"left","lease_ms":1200}
```

`renew_lease` requires an existing active lease for the selected arm and the
same integer 700-through-1500 `lease_ms` bounds. It atomically replaces only
that lease's monotonic watchdog and deadline: it does not query modes, inspect
telemetry, resend the target trajectory, or perform any Amber UDP I/O. Its
acknowledgement echoes `lease_ms` and returns the new
`lease_deadline_monotonic_ns`. Missing leases and invalid values are rejected
without changing the current lease. Like other motion-authority operations,
renewal is rejected after heartbeat expiry.

Lease expiry, heartbeat expiry, authenticated-client disconnect/release, and
gateway shutdown cancel the affected active leases and independently attempt a
backstop hold for each arm. The gateway:

1. Queries all seven modes without changing them.
2. Proceeds only when every joint is already in position mode.
3. Captures a new, finite LCM pose no older than 250 ms.
4. Sends that measured pose as a 0.65-second trajectory.

The backstop never sends `activate`, `position_mode`, or any other mode-change
command. If modes are not entirely position, telemetry is unavailable, or
Amber does not confirm the hold, the gateway logs an unconfirmed hold and does
not activate the arm. Overlapping expiry, disconnect, heartbeat, and explicit
hold triggers are coalesced or superseded by a newer accepted per-arm lease so
stale watchdog work cannot hold a newer trajectory.

`priority_hold` is available to any authenticated controller session even
after heartbeat expiry and whether or not that arm currently has a lease. It
cancels the selected arm's active lease and runs the same no-activation hold.
Its acknowledgement has `accepted:true` when the authenticated request was
processed and a separate `hold_confirmed` boolean. A confirmed result includes
the seven captured `captured_positions_rad`, the seven `modes` and
`mode_names`, `hold_duration_s`, and the Amber response. An unconfirmed result
has `hold_confirmed:false` and an `error`; known modes are included when the
mode query succeeded.

The legacy `trajectory` operation does not create or renew a safety lease.
Remote bounded-motion controllers should use `leased_trajectory` and renew it
only while they retain their own operator authority and dead-man input.
Disconnect and heartbeat expiry cancel a renewed lease exactly as they cancel
its original watchdog and immediately attempt the same backstop hold.

`deactivate` sends mode 0 and verifies all seven mode replies. It remains
available after heartbeat expiry so an authenticated operator can always
request the lower-energy state.

## Calibrated gripper control

Gripper requests use the same authenticated, exclusive controller session,
strictly increasing `command_id`, per-arm operation lock, and heartbeat rules
as arm commands:

```json
{"type":"gripper_state","command_id":10,"arm":"left"}
{"type":"gripper_calibrate","command_id":11,"arm":"left"}
{"type":"gripper_control","command_id":12,"arm":"left","action":"release","force":5}
{"type":"gripper_control","command_id":13,"arm":"left","action":"hold","force":10}
```

The transport is based on the checked-in vendor packet definitions and core:

- Calibration is packed little-endian UDP command 7: a 12-byte request
  containing the command header and fixed gripper selector 8.
- Control is packed little-endian UDP command 9: a 13-byte request containing
  the command header, action 0 for `release`/open or 1 for `hold`/close, a
  16-bit intensity, and fixed version 0.
- The companion vendor V2 API documents integer intensity 1 through 300. The
  gateway enforces those inclusive raw bounds. `force` and
  `force_unit:"vendor_intensity"` are protocol names only: the repository
  provides no physical unit or conversion to newtons. A user interface may
  impose a smaller operating envelope; the legacy dashboard used 2 through
  20.

The checked-in Amber core returns the generic command response after dispatch
to its gripper LCM path. An `amber_response` of 1 therefore proves only that
the core accepted the command for dispatch. It does **not** prove that the
gripper moved, reached an endpoint, achieved a requested force, or completed
calibration. Successful calibration replies consequently report:

```json
{
  "type":"gripper_calibrate_ack",
  "command_id":11,
  "arm":"left",
  "accepted":true,
  "amber_response":1,
  "calibration_command_accepted":true,
  "calibration_state":"command_accepted_unverified",
  "calibration_verified":false,
  "completion_verified":false,
  "feedback_available":false
}
```

The gateway starts each arm at `calibration_state:"required"`. Control is
allowed only after command 7 returned 1 for that same arm during the same live
controller session. A new calibration attempt first returns that arm to
`required`, so a rejection, timeout, or ambiguous response fails closed.
Calibration acceptance is independently tracked per arm and is cleared on a
new controller claim, heartbeat expiry, disconnect, gateway shutdown, or stale
or unavailable telemetry for that arm. Fresh telemetry returning after an
outage does not restore the previous acceptance. Calibration and control both
require a same-arm sample no older than 250 ms and revalidate the controller
session, heartbeat-authority generation, and telemetry after the vendor request
returns. A late reply after heartbeat expiry or disconnect therefore cannot
restore acceptance. Calibration is also rejected while that arm has an active
motion lease or backstop hold. It is intentionally never reported as
`calibrated` or `verified`.

`gripper_state` performs no Amber I/O and remains queryable after heartbeat
expiry. It reports the calibration state, raw force bounds and unit,
`supported_actions:["release","hold"]`, `command_in_flight:false`, and the
fixed facts `calibration_verified:false` and `feedback_available:false`.
`gripper_control_ack` echoes the accepted action and raw force and likewise
reports `completion_verified:false`.

There is no evidence-backed gripper stop/cancel operation. The seven-joint LCM
arm status has no gripper opening, force, calibration, limit-switch, or object
detection field. UDP status contains an unlabeled eighth position/speed pair,
but its gripper identity and units are not established, so the gateway does
not expose it as gripper feedback. Calibration duration, physical travel,
required arm mode, calibration completion, and force semantics remain unknown.
The gateway never auto-calibrates or automatically retries an ambiguous
calibration/control request.
Clearing session state on heartbeat expiry or disconnect is bookkeeping only;
it cannot stop gripper motion that Amber already accepted.

The gateway has no arm-core boot-generation signal. A power cycle that makes
LCM feedback unavailable or stale is detected and clears that arm's acceptance;
an extremely fast restart that never produces an observable telemetry gap
cannot be distinguished from uninterrupted operation. Recalibrate deliberately
after every known power cycle even if the gateway still reports fresh feedback.

**Calibration safety:** vendor documentation requires recalibration after each
power cycle, and calibration can cause physical gripper motion. Clear hands and
objects from the gripper, begin from a safe configuration, and keep the
physical E-stop available before issuing `gripper_calibrate`.

## Tests

Run the non-hardware protocol tests from this directory:

```sh
python3 -m unittest -v test_rob_amber_gateway.py
```

The tests use fake UDP and status implementations. They never load LCM, open an
Amber UDP socket, or send a robot command.
