#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-124.221.92.85}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:-}"
DRY_RUN="${DRY_RUN:-0}"
SSH_CONTROL_PATH="/tmp/huijian-deploy-%C"

repo_root() {
  printf '%s\n' "$REPO_ROOT"
}

latest_commit_for_paths() {
  git -C "$REPO_ROOT" log -1 --format=%h -- "$@"
}

remote_target() {
  printf '%s@%s\n' "$DEPLOY_USER" "$DEPLOY_HOST"
}

require_ssh_key() {
  if [[ -z "$DEPLOY_SSH_KEY" ]]; then
    printf 'DEPLOY_SSH_KEY is required.\n' >&2
    exit 1
  fi
}

log_step() {
  printf '\n[%s] %s\n' "$1" "$2"
}

run_cmd() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

run_local() {
  run_cmd "$@"
}

run_tar_create() {
  local base_dir="$1"
  local archive_path="$2"
  shift 2
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %q' env
    printf ' %q' COPYFILE_DISABLE=1 tar -C "$base_dir" --no-xattrs -czf "$archive_path"
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  env COPYFILE_DISABLE=1 tar -C "$base_dir" --no-xattrs -czf "$archive_path" "$@"
}

run_ssh_transport() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run_cmd "$@"
    return 0
  fi

  local attempt
  for attempt in 1 2 3 4 5; do
    if "$@"; then
      return 0
    fi
    if [[ "$attempt" != "5" ]]; then
      printf 'SSH transport interrupted; retrying (%s/5)...\n' "$attempt" >&2
      sleep "$((attempt * 2))"
    fi
  done
  return 1
}

run_scp() {
  run_ssh_transport scp \
    -i "$DEPLOY_SSH_KEY" \
    -o BatchMode=yes \
    -o ConnectTimeout=15 \
    -o ControlMaster=auto \
    -o ControlPersist=60 \
    -o ControlPath="$SSH_CONTROL_PATH" \
    -o StrictHostKeyChecking=no \
    "$@"
}

run_remote() {
  local remote
  remote="$(remote_target)"
  run_ssh_transport ssh \
    -i "$DEPLOY_SSH_KEY" \
    -o BatchMode=yes \
    -o ConnectTimeout=15 \
    -o ControlMaster=auto \
    -o ControlPersist=60 \
    -o ControlPath="$SSH_CONTROL_PATH" \
    -o StrictHostKeyChecking=no \
    "$remote" "$1"
}

run_remote_capture() {
  local remote
  remote="$(remote_target)"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %q %q %q %q %q %q\n' ssh -i "$DEPLOY_SSH_KEY" -o StrictHostKeyChecking=no "$remote" "$1"
    return 0
  fi
  run_ssh_transport ssh \
    -i "$DEPLOY_SSH_KEY" \
    -o BatchMode=yes \
    -o ConnectTimeout=15 \
    -o ControlMaster=auto \
    -o ControlPersist=60 \
    -o ControlPath="$SSH_CONTROL_PATH" \
    -o StrictHostKeyChecking=no \
    "$remote" "$1"
}

write_commit_marker() {
  local output_path="$1"
  local commit_sha="$2"
  printf '%s\n' "$commit_sha" > "$output_path"
}
