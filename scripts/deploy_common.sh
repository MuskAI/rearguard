#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-124.222.3.205}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:-}"
DRY_RUN="${DRY_RUN:-0}"

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
    printf ' %q' COPYFILE_DISABLE=1 tar -C "$base_dir" -czf "$archive_path"
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  env COPYFILE_DISABLE=1 tar -C "$base_dir" -czf "$archive_path" "$@"
}

run_scp() {
  run_cmd scp -i "$DEPLOY_SSH_KEY" -o StrictHostKeyChecking=no "$@"
}

run_remote() {
  local remote
  remote="$(remote_target)"
  run_cmd ssh -i "$DEPLOY_SSH_KEY" -o StrictHostKeyChecking=no "$remote" "$1"
}

run_remote_capture() {
  local remote
  remote="$(remote_target)"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %q %q %q %q %q %q\n' ssh -i "$DEPLOY_SSH_KEY" -o StrictHostKeyChecking=no "$remote" "$1"
    return 0
  fi
  ssh -i "$DEPLOY_SSH_KEY" -o StrictHostKeyChecking=no "$remote" "$1"
}

write_commit_marker() {
  local output_path="$1"
  local commit_sha="$2"
  printf '%s\n' "$commit_sha" > "$output_path"
}
