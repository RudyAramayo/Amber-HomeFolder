#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
manifest="${repo_root}/sync/amber-critical.tsv"
ssh_target="${AMBER_SSH_TARGET:-amber@amber-master.local}"
restart_service=false

usage() {
  cat <<'USAGE'
Usage: ./scripts/amber-sync.sh COMMAND [--restart]

Commands:
  check         Read-only SHA-256 comparison of every allowlisted file.
  pull-host     Pull host-owned CAN/arm/LCM/startup files into the repository.
  push-gateway  Push gateway/recovery files, run fake tests, and install them.

Options:
  --restart     With push-gateway, restart only rob-amber-gateway.service.

Environment:
  AMBER_SSH_TARGET  SSH destination (default: amber@amber-master.local)

This tool never copies tokens or SSH state, never uses --delete, and never
restarts rc-local, CAN, or either Amber arm core. push-gateway installs the
reviewed least-privilege rc.local for the next boot/recovery but does not run it.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

validate_manifest() {
  [[ -f "${manifest}" ]] || die "missing manifest: ${manifest}"
  if awk -F '\t' '
    /^[[:space:]]*#/ || NF == 0 { next }
    NF != 3 || $2 !~ /^(amber|system)\// || $3 !~ /^\// { bad = 1 }
    $2 ~ /(^|\/)(\.ssh|token|\.bash_history|\.python_history)(\/|$)/ { bad = 1 }
    $3 ~ /(^|\/)(\.ssh|token|\.bash_history|\.python_history)(\/|$)/ { bad = 1 }
    END { exit bad }
  ' "${manifest}"; then
    :
  else
    die "manifest is malformed or contains a forbidden path"
  fi
}

manifest_rows() {
  awk -F '\t' '
    /^[[:space:]]*#/ || NF == 0 { next }
    { print $1 "\t" $2 "\t" $3 }
  ' "${manifest}"
}

local_sha256() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{print $1}'
  else
    sha256sum "${path}" | awk '{print $1}'
  fi
}

check_sync() {
  local temp_dir remote_paths remote_hashes
  local group local_path remote_path local_hash remote_hash
  local differences=0
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/amber-sync-check.XXXXXX")"
  remote_paths="${temp_dir}/remote-paths"
  remote_hashes="${temp_dir}/remote-hashes"

  manifest_rows | awk -F '\t' '{print $3}' > "${remote_paths}"
  ssh "${ssh_target}" '
    while IFS= read -r path; do
      if [ -f "$path" ]; then
        hash=$(sha256sum "$path" | awk "{print \$1}")
        printf "%s\t%s\n" "$hash" "$path"
      else
        printf "MISSING\t%s\n" "$path"
      fi
    done
    printf "@STATE\thostname\t%s\n" "$(hostnamectl --static 2>/dev/null || hostname)"
    printf "@STATE\tgateway-enabled\t%s\n" "$(systemctl is-enabled rob-amber-gateway.service 2>/dev/null || true)"
    printf "@STATE\tgateway-active\t%s\n" "$(systemctl is-active rob-amber-gateway.service 2>/dev/null || true)"
    printf "@STATE\tavahi-enabled\t%s\n" "$(systemctl is-enabled avahi-daemon.service 2>/dev/null || true)"
    printf "@STATE\tavahi-active\t%s\n" "$(systemctl is-active avahi-daemon.service 2>/dev/null || true)"
    printf "@STATE\ttoken-metadata\t%s\n" "$(stat -c "%a:%U:%G" /etc/rob-amber-gateway/token 2>/dev/null || true)"
    printf "@STATE\trecovery-helper-metadata\t%s\n" "$(stat -c "%a:%U:%G" /usr/local/sbin/rob-amber-recover 2>/dev/null || true)"
    printf "@STATE\trecovery-init-metadata\t%s\n" "$(stat -c "%a:%U:%G" /usr/local/libexec/rob-amber-init-can 2>/dev/null || true)"
    printf "@STATE\trecovery-map-metadata\t%s\n" "$(stat -c "%a:%U:%G" /etc/rob-amber-gateway/can-interfaces.json 2>/dev/null || true)"
    printf "@STATE\trecovery-sudoers-metadata\t%s\n" "$(stat -c "%a:%U:%G" /etc/sudoers.d/rob-amber-recovery 2>/dev/null || true)"
    printf "@STATE\trc-local-metadata\t%s\n" "$(stat -c "%a:%U:%G" /etc/rc.local 2>/dev/null || true)"
    if grep -q "ROB_AMBER_SAFE_RC_LOCAL=1" /etc/rc.local 2>/dev/null; then
      printf "@STATE\trc-local-contract\tsafe\n"
    else
      printf "@STATE\trc-local-contract\tunsafe\n"
    fi
  ' < "${remote_paths}" > "${remote_hashes}"

  while IFS=$'\t' read -r group local_path remote_path; do
    [[ -n "${group}" ]] || continue
    if [[ ! -f "${repo_root}/${local_path}" ]]; then
      printf 'LOCAL MISSING  %s\n' "${local_path}"
      differences=$((differences + 1))
      continue
    fi
    local_hash="$(local_sha256 "${repo_root}/${local_path}")"
    remote_hash="$(awk -F '\t' -v path="${remote_path}" '$2 == path {print $1; exit}' "${remote_hashes}")"
    if [[ -z "${remote_hash}" || "${remote_hash}" == "MISSING" ]]; then
      printf 'REMOTE MISSING %s\n' "${remote_path}"
      differences=$((differences + 1))
    elif [[ "${local_hash}" == "${remote_hash}" ]]; then
      printf 'MATCH          %-9s %s\n' "${group}" "${local_path}"
    else
      printf 'DIFF           %-9s %s <-> %s\n' "${group}" "${local_path}" "${remote_path}"
      differences=$((differences + 1))
    fi
  done < <(manifest_rows)

  while IFS=$'\t' read -r group local_path remote_path; do
    [[ "${group}" == "@STATE" ]] || continue
    case "${local_path}" in
      hostname) expected="$(tr -d '\r\n' < "${repo_root}/system/etc/hostname")" ;;
      gateway-enabled|gateway-active|avahi-enabled|avahi-active) expected="${local_path##*-}" ;;
      token-metadata) expected="640:root:amber" ;;
      recovery-helper-metadata|recovery-init-metadata|rc-local-metadata) expected="755:root:root" ;;
      recovery-map-metadata) expected="644:root:root" ;;
      recovery-sudoers-metadata) expected="440:root:root" ;;
      rc-local-contract) expected="safe" ;;
      *) continue ;;
    esac
    if [[ "${remote_path}" == "${expected}" ]]; then
      printf 'STATE MATCH    %-17s %s\n' "${local_path}" "${remote_path}"
    else
      printf 'STATE DIFF     %-17s expected %s, found %s\n' "${local_path}" "${expected}" "${remote_path:-missing}"
      differences=$((differences + 1))
    fi
  done < "${remote_hashes}"

  if (( differences > 0 )); then
    rm -rf -- "${temp_dir:?}"
    printf '\n%d allowlisted path(s) differ.\n' "${differences}" >&2
    return 1
  fi
  rm -rf -- "${temp_dir:?}"
  printf '\nAll allowlisted persistent files match %s.\n' "${ssh_target}"
}

pull_host() {
  local temp_dir file_list group local_path remote_path staged_path
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/amber-sync-pull.XXXXXX")"
  file_list="${temp_dir}/remote-files"

  manifest_rows | awk -F '\t' '$1 == "host" || $1 == "system" {sub(/^\//, "", $3); print $3}' > "${file_list}"
  rsync -rc --files-from="${file_list}" "${ssh_target}:/" "${temp_dir}/stage/"

  while IFS=$'\t' read -r group local_path remote_path; do
    [[ "${group}" == "host" || "${group}" == "system" ]] || continue
    staged_path="${temp_dir}/stage/${remote_path#/}"
    [[ -f "${staged_path}" ]] || die "host did not provide ${remote_path}"
    mkdir -p "$(dirname "${repo_root}/${local_path}")"
    cp "${staged_path}" "${repo_root}/${local_path}"
    printf 'PULLED         %s\n' "${local_path}"
  done < <(manifest_rows)

  chmod 0755 "${repo_root}/system/etc/rc.local"
  rm -rf -- "${temp_dir:?}"
  printf '\nReview the Git diff before committing host-owned configuration.\n'
}

push_gateway() {
  local file_list temp_dir
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/amber-sync-push.XXXXXX")"
  file_list="${temp_dir}/gateway-files"

  manifest_rows | awk -F '\t' '$1 == "gateway" {sub(/^amber\//, "", $2); print $2}' > "${file_list}"
  rsync -rc --files-from="${file_list}" "${repo_root}/amber/" "${ssh_target}:/home/amber/"
  rsync -c "${repo_root}/system/etc/rc.local" \
    "${ssh_target}:/home/amber/rob_gateway/rc.local.reviewed"

  ssh -t "${ssh_target}" '
    set -eu
    cd /home/amber/rob_gateway
    python3 -m py_compile \
      rob_amber_gateway.py test_gateway.py test_rob_amber_gateway.py \
      rob_amber_recovery.py rob_amber_init_can.py test_rob_amber_recovery.py
    python3 -m unittest -q \
      test_rob_amber_gateway.py test_rob_amber_recovery.py
    sudo install -o root -g root -m 0644 rob-amber-gateway.service /etc/systemd/system/rob-amber-gateway.service
    sudo install -d -o root -g amber -m 0750 /etc/rob-amber-gateway
    sudo install -d -o root -g root -m 0755 /usr/local/libexec /usr/local/sbin
    sudo install -o root -g root -m 0644 can-interfaces.json /etc/rob-amber-gateway/can-interfaces.json
    sudo install -o root -g root -m 0755 rob_amber_init_can.py /usr/local/libexec/rob-amber-init-can
    sudo install -o root -g root -m 0755 rob_amber_recovery.py /usr/local/sbin/rob-amber-recover
    if ! sudo test -e /etc/rc.local.pre-rob-amber-recovery; then
      sudo install -o root -g root -m 0755 \
        /etc/rc.local /etc/rc.local.pre-rob-amber-recovery
    fi
    sudo install -o root -g root -m 0755 rc.local.reviewed /etc/rc.local

    sudoers_target=/etc/sudoers.d/rob-amber-recovery
    sudoers_temp=/etc/sudoers.d/.rob-amber-recovery.new
    sudoers_backup=/etc/sudoers.d/.rob-amber-recovery.previous
    had_sudoers=false
    if sudo test -f "${sudoers_target}"; then
      sudo install -o root -g root -m 0440 "${sudoers_target}" "${sudoers_backup}"
      had_sudoers=true
    fi
    cleanup_sudoers() { sudo rm -f "${sudoers_temp}" "${sudoers_backup}"; }
    trap cleanup_sudoers EXIT
    sudo install -o root -g root -m 0440 rob-amber-recovery.sudoers "${sudoers_temp}"
    sudo /usr/sbin/visudo -cf "${sudoers_temp}"
    sudo mv -f "${sudoers_temp}" "${sudoers_target}"
    if ! sudo /usr/sbin/visudo -c; then
      if [ "${had_sudoers}" = true ]; then
        sudo mv -f "${sudoers_backup}" "${sudoers_target}"
      else
        sudo rm -f "${sudoers_target}"
      fi
      exit 1
    fi
    sudo rm -f "${sudoers_backup}"
    trap - EXIT
    sudo systemctl daemon-reload
  '

  if [[ "${restart_service}" == true ]]; then
    ssh -t "${ssh_target}" '
      set -eu
      sudo systemctl restart rob-amber-gateway.service
      systemctl is-active --quiet rob-amber-gateway.service
      systemctl show rob-amber-gateway.service -p MainPID -p NRestarts -p ActiveState -p SubState
    '
  else
    printf '\nGateway/recovery files and the reviewed rc.local are installed; no service was restarted.\n'
  fi
  rm -rf -- "${temp_dir:?}"
}

main() {
  local command="${1:-}"
  shift || true
  while (( $# > 0 )); do
    case "$1" in
      --restart) restart_service=true ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1" ;;
    esac
    shift
  done

  require_command ssh
  require_command awk
  validate_manifest

  case "${command}" in
    check)
      check_sync
      ;;
    pull-host)
      require_command rsync
      pull_host
      ;;
    push-gateway)
      require_command rsync
      push_gateway
      ;;
    -h|--help|help|'')
      usage
      ;;
    *)
      usage >&2
      die "unknown command: ${command}"
      ;;
  esac
}

main "$@"
