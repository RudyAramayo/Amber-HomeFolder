# ROB Amber gateway protocol

The gateway listens on an SSH-forwarded TCP connection and exchanges one
compact JSON object per line. It must remain bound to `127.0.0.1`; the bearer
token authenticates the connection but does not encrypt it.

After the server sends `challenge`, the client sends:

```json
{"type":"hello","protocol":"rob-amber-gateway/1","token":"..."}
```

The server replies with `ready`. Only one authenticated controller session is
allowed at a time; a second valid-token connection receives an error and is
closed. The owner receives telemetry and is the only session that can query
modes or issue commands. Ownership is released when its TCP connection closes.

Send a `heartbeat` more frequently than the advertised `heartbeat_timeout_s`.
Commands other than `mode_query` and the fail-safe `deactivate` are rejected
after heartbeat expiry.

Every command has a strictly increasing unsigned 32-bit `command_id` and one
of the arm names `left` or `right`:

```json
{"type":"mode_query","command_id":1,"arm":"left"}
{"type":"activate","command_id":2,"arm":"left"}
{"type":"position_mode","command_id":3,"arm":"left"}
{"type":"hold_current","command_id":4,"arm":"left"}
{"type":"trajectory","command_id":5,"arm":"left","positions_rad":[0,0,0,0,0,0,0],"duration_s":2.0}
{"type":"deactivate","command_id":6,"arm":"left"}
```

Each reply is named after the request, for example `position_mode_ack`. It
contains `accepted`, `command_id`, `arm`, and `gateway_latency_ms`. Successful
mode operations also contain seven `modes` and seven human-readable
`mode_names`. Rejections have `accepted:false` and an `error` string.

`position_mode` is deliberately a composite operation:

1. Send vendor command 10 for active mode (1).
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

Normal `trajectory` requests are accepted only while all seven joints report
position mode and LCM telemetry is fresh. Requests require exactly seven finite
positions, a duration from 0.65 through 10 seconds, and these inclusive limits:

| Joint | Minimum (rad) | Maximum (rad) |
| --- | ---: | ---: |
| 1 | -2.4435 | 2.4435 |
| 2 | -2.3213 | 2.3213 |
| 3–6 | -2.2863 | 2.2863 |
| 7 | -3.05 | 3.05 |

`deactivate` sends mode 0 and verifies all seven mode replies. It remains
available after heartbeat expiry so an authenticated operator can always
request the lower-energy state.

## Tests

Run the non-hardware protocol tests from this directory:

```sh
python3 -m unittest -v test_rob_amber_gateway.py
```

The tests use fake UDP and status implementations. They never load LCM, open an
Amber UDP socket, or send a robot command.
