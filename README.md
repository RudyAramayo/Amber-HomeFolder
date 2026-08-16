# Amber-HomeFolder

This repository preserves the files needed to rebuild and operate the Amber
Ubuntu controller used by Cerebro. The live controller is reached through
`amber@amber-master.local`; its DHCP address is intentionally not stored.

The repository is not a safe byte-for-byte mirror of `/home/amber`. It began as
a historical home-folder snapshot and includes caches and other runtime files.
All new synchronization is therefore controlled by the explicit allowlist in
[`sync/amber-critical.tsv`](sync/amber-critical.tsv). The sync tool never uses
`--delete` and never reads or copies the gateway token, SSH files, histories,
logs, caches, telemetry, staging directories, or backups.

## Normal workflow

From the repository root:

```sh
./scripts/amber-sync.sh check
./scripts/amber-sync.sh pull-host
./scripts/amber-sync.sh push-gateway
```

- `check` is read-only and compares SHA-256 hashes for every allowlisted file.
- `pull-host` makes Git match the live host-owned CAN, arm-release, LCM, and
  startup configuration. It does not pull gateway source over local work.
- `push-gateway` copies the gateway and guarded recovery sources, runs both
  fake-only test suites on Ubuntu, and installs the gateway unit plus the
  reviewed root-owned recovery boundary. It installs the least-privilege
  `rc.local` but never runs or restarts CAN or either core. It does not restart
  the gateway service unless `--restart` is supplied.

Set `AMBER_SSH_TARGET` to override the default Bonjour target. SSH credentials
remain in the user's SSH agent/configuration or interactive prompt; this
repository does not store them.

Robot launch files and core binaries remain host-owned because changing them
can affect physical hardware. Review a `pull-host` diff before committing it.
The reviewed CAN serial map and `rc.local` are also preserved as system state;
`push-gateway` installs their hardened recovery copies but does not execute
them.

See [`docs/amber-master-persistence.md`](docs/amber-master-persistence.md) for
the recovery boundary, installed dependencies, service ordering, and secret
handling. See [`docs/amber-stack-recovery.md`](docs/amber-stack-recovery.md)
for the privileged-helper contract and physical safety requirements.
