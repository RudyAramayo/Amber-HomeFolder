# amber-master persistence and recovery

Last reconciled with `amber-master.local`: 2026-08-15 PDT / 2026-08-16 UTC.

## Persistent boundary

The allowlist records the state needed after a reboot or machine replacement:

- the authenticated Cerebro-to-Amber gateway, protocol, tests, and systemd
  unit;
- active left/right controller binaries and `launch.json` files;
- the CAN discovery script and the serial-number-to-interface mapping;
- LCM schemas and generated types used by the gateway;
- the exact `/etc/rc.local` startup program;
- the Bonjour hostname from `/etc/hostname`;
- the OS/Python dependency list and non-secret host inventory.

The live right-arm configuration uses
`./urdf/dual_b1/DualArmR.urdf`. Both that file and `DualArm.urdf` exist on the
host, but `DualArmR.urdf` is the original controller's selected model and is
therefore preserved in `amber/R-11/launch.json`.

The repository also contains the vendor URDF/mesh trees and older utilities as
historical release artifacts. They are not part of the frequent critical-file
sync because hashing the full raw snapshot is expensive. Do not broadly rsync
the repository onto `/home/amber`.

## Live host inventory

- Hostname: `amber-master` (Bonjour: `amber-master.local`)
- OS: Ubuntu 22.04 (`jammy`)
- Runtime Python: 3.10.12
- Python LCM package: `lcm` 1.4.4, installed for user `amber`
- CAN interfaces: `can10` and `can11`
- CAN adapter mapping:
  - `209C36AB4B34` -> `can10`
  - `206134725847` -> `can11`
- Left/right Amber UDP ports: 26001 and 26002
- Gateway: enabled and supervised by systemd, listening only on
  `127.0.0.1:7443`
- Bonjour: `avahi-daemon` enabled and active

The current IP address is DHCP/runtime state. Cerebro and maintenance tooling
should use `amber-master.local` rather than persisting an address.

## Startup path

The installed startup remains:

1. `/etc/rc.local` enables multicast routing.
2. `/home/amber/init/initCan.sh` maps physical adapters to `can10`/`can11`.
3. `amber_core_L` and `amber_core_R` start and publish arm state over LCM.
4. `rob-amber-gateway.service` exposes authenticated telemetry/control on the
   loopback interface for Cerebro's SSH tunnel.

The current gateway unit is not explicitly ordered after `rc-local.service`.
That should eventually be replaced with dedicated CAN and arm-core systemd
units with readiness dependencies. Until that migration is tested, the exact
working `rc.local` is stored at `system/etc/rc.local`.

## Secrets and runtime data

Never commit or synchronize:

- `/etc/rob-amber-gateway/token` or its contents;
- SSH keys, `authorized_keys`, known-host state, or passwords;
- shell/Python histories, editor state, logs, telemetry, caches, virtualenvs,
  `__pycache__`, staging directories, or pre-change backups.

The token is generated independently on the Ubuntu host, owned by
`root:amber`, and mode `0640`. Cerebro stores its matching credential in the
macOS Keychain. The sync check intentionally verifies neither the token value
nor a hash of it.

This repository's original snapshot already tracks some historical runtime and
SSH-related files. `.gitignore` stops new untracked copies but cannot erase Git
history. Treat the existing remote repository as sensitive and migrate the
declarative deployment subtree into a clean repository if public sharing is
ever planned.

## Dependencies

Install the packages listed in `system/packages/apt-packages.txt`, then install
`system/python/requirements.txt` for user `amber`. Do not copy the tracked
historical `.local` tree or compiled Python extensions between machines.

The exact observed versions are recorded in
`system/packages/amber-master-versions.tsv`; the unpinned apt package names are
the recovery contract so Ubuntu can receive security fixes.

## Safe synchronization rules

- `check` and `pull-host` may read only the allowlisted paths.
- `push-gateway` may write only `/home/amber/rob_gateway/*` and the installed
  gateway systemd unit.
- No sync operation uses `--delete`.
- No sync operation restarts CAN, `rc-local`, or either Amber core.
- Gateway unit tests use fake transports and do not send UDP, LCM, CAN, or arm
  commands.
- Deploying robot launch/CAN files remains an explicit, reviewed manual action.
