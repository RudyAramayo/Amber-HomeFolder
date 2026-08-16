# Amber stack recovery helper

`rob-amber-recover` is the fixed Ubuntu maintenance operation used by
Cerebro's **Restart CAN/Core Stack…** control. It is not an arm-control protocol.
It never imports LCM, opens UDP/CAN sockets, or sends a mode, activation,
trajectory, position, or motion command.

## Physical precondition

Recovery deliberately interrupts both vendor cores and both USB-CAN links.
Powered arms can lose holding torque or feedback. Before running it, physically
support both arms at a safe pose, clear the workspace, and keep the physical
E-stop ready. Do not run recovery while any manual, Gemini, or controller
command is pending.

Cerebro revokes its temporary Gemini/controller authority and disconnects the
authenticated gateway session before invoking the helper. Authority remains
revoked after recovery. A successful result may reconnect telemetry, but it
does not activate an arm or select position mode.

## Privilege boundary

The SSH account receives passwordless permission for one exact command with no
arguments:

```sudoers
Cmnd_Alias ROB_AMBER_RECOVERY = /usr/local/sbin/rob-amber-recover ""
amber ALL=(root) NOPASSWD: ROB_AMBER_RECOVERY
```

The helper rejects arguments and non-root execution, uses absolute executable
paths, ignores environment configuration, and serializes runs with a protected
nonblocking lock at `/run/rob-amber-recovery.lock`.

The original `rc.local` executed files from the user-writable Amber home folder
as root. That would turn any passwordless restart grant into a privilege-
escalation path. The reviewed startup contract now:

- installs the CAN initializer root-owned at
  `/usr/local/libexec/rob-amber-init-can`;
- installs the reviewed adapter map root-owned at
  `/etc/rob-amber-gateway/can-interfaces.json`;
- runs the home-folder vendor core binaries as user `amber`, not root; and
- marks `/etc/rc.local` with `ROB_AMBER_SAFE_RC_LOCAL=1`.

Recovery refuses to start `rc-local.service` if those files are not root-owned,
are group/other-writable, or the reviewed startup marker and least-privilege
launch are missing. It also pins the root-owned gateway unit to its reviewed
hash and verifies systemd's effective unit has no drop-ins or command hooks,
runs as user/group `amber`, and retains the fixed loopback-only `ExecStart`.

## Ordered recovery and verification

One invocation performs these steps with bounded waits and no automatic retry:

1. Acquire the single-instance lock.
2. Before mutating anything, verify the protected startup files, exact absolute
   dependencies, `amber` account, reviewed serial map, and presence of both USB
   adapters. A preflight rejection leaves a healthy running stack untouched.
3. Stop `rob-amber-gateway.service`.
4. Stop `rc-local.service`.
5. Require no gateway, `slcand`, `amber_core_L`, or `amber_core_R` process and no
   listener on TCP 7443 or UDP 26001/26002.
6. Reverify the root-owned inputs and require exactly one matching USB device
   for each reviewed adapter serial.
7. Start `rc-local.service` exactly once.
8. Require exactly two root-owned `slcand` processes mapping the reviewed serials to
   `can10` and `can11`, one left core, one right core, service-cgroup ownership,
   both cores running as user `amber`, `UP` plus `LOWER_UP` on both CAN links,
   and the matching core PID on each UDP port.
9. Sample both CAN links twice. RX and TX packet counts must advance and error
   and dropped-packet counters must remain zero. This is intentionally fatal:
   the recovery exists to detect an idle adapter. If the chosen E-stop state
   suppresses bus traffic, verification fails and rollback leaves the restarted
   stack stopped rather than claiming success.
10. Reset the gateway failure counter and start the gateway exactly once.
11. Require `active/running`, one nonzero `MainPID`, `NRestarts=0`, user
    `amber`, and exactly one `127.0.0.1:7443` listener owned by that PID; then
    repeat the verification after a settle interval to catch a restart loop.

Once the ordered stop begins, any failed check leaves the gateway stopped. If
recovery had started the CAN/core stack, rollback stops that stack as well
rather than leaving a partial pair running. Start/stop jobs are nonblocking;
the helper owns a 50-second operation deadline plus a bounded rollback window,
leaving headroom under Cerebro's 75-second client timeout. A dropped SSH output
stream does not interrupt the host halfway through the sequence. The GUI treats
a client timeout as an ambiguous failure, keeps debug authority revoked, and
does not retry automatically.

## Output contract

Standard output is bounded JSON Lines using `rob-amber-recovery/1`:

```json
{"protocol":"rob-amber-recovery/1","operation":"restart_can_core_gateway","operation_id":"7eeb2c2e-8ad6-4b88-a17e-c40c4aaf37db","type":"progress","stage":"stopping_gateway","message":"Stopping the gateway before the CAN/core stack."}
{"protocol":"rob-amber-recovery/1","operation":"restart_can_core_gateway","operation_id":"7eeb2c2e-8ad6-4b88-a17e-c40c4aaf37db","type":"result","success":true,"detail":"CAN adapters, both Amber cores, UDP listeners, and the loopback-only gateway passed recovery verification."}
```

No password, gateway token, environment value, command output, or telemetry is
included. Exit status zero and a matching final `success:true` result are both
required for Cerebro to report success.

## Install and validate without running recovery

The reviewed deployment path is:

```sh
./scripts/amber-sync.sh push-gateway
./scripts/amber-sync.sh check
```

`push-gateway` runs only fake Python tests, installs the root-owned artifacts,
validates the staged sudoers file with `visudo`, and installs the sudoers rule
last. Before its first `rc.local` migration, it preserves the prior file once
as root-owned `/etc/rc.local.pre-rob-amber-recovery`; later deployments never
overwrite that backup. It installs but does not start or restart
`rc-local.service`, either core, or either CAN adapter. `--restart` remains
limited to the gateway service.

On Ubuntu, the non-mutating metadata checks are:

```sh
stat -c '%a:%U:%G %n' \
  /usr/local/sbin/rob-amber-recover \
  /usr/local/libexec/rob-amber-init-can \
  /etc/rob-amber-gateway/can-interfaces.json \
  /etc/sudoers.d/rob-amber-recovery \
  /etc/rc.local
sudo visudo -cf /etc/sudoers.d/rob-amber-recovery
sudo -n -l /usr/local/sbin/rob-amber-recover
```

Do not invoke `/usr/local/sbin/rob-amber-recover` merely as an installation
test: it performs the real ordered restart.

To restore the pre-migration `rc.local` without starting it, first remove the
passwordless recovery grant so the legacy root-executes-home-files path cannot
be triggered, then copy the fixed backup path:

```sh
sudo mv /etc/sudoers.d/rob-amber-recovery \
  /root/rob-amber-recovery.sudoers.disabled
sudo install -o root -g root -m 0755 \
  /etc/rc.local.pre-rob-amber-recovery /etc/rc.local
sudo visudo -c
```

Those commands do not restart a service. The GUI recovery action remains
disabled at the sudo boundary until the reviewed `rc.local` and sudoers rule
are reinstalled.

Run the offline unit tests from the copied gateway directory:

```sh
python3 -m unittest -v test_rob_amber_recovery.py
```

The tests inject fake services, processes, listeners, USB mappings, and CAN
counters. They never access robot hardware or the network.
