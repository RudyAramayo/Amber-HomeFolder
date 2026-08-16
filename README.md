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
- `push-gateway` copies only gateway source, tests, and documentation, runs the
  mocked gateway unit tests on Ubuntu, and installs the tracked systemd unit.
  It does not restart the service unless `--restart` is supplied.

Set `AMBER_SSH_TARGET` to override the default Bonjour target. SSH credentials
remain in the user's SSH agent/configuration or interactive prompt; this
repository does not store them.

Robot launch files, core binaries, CAN mappings, and `/etc/rc.local` are treated
as host-owned because changing them can affect physical hardware. Review a
`pull-host` diff before committing it. The sync tool deliberately has no
automatic command for pushing those files back to the robot.

See [`docs/amber-master-persistence.md`](docs/amber-master-persistence.md) for
the recovery boundary, installed dependencies, service ordering, and secret
handling.

