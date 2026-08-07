#!/usr/bin/env bash
set -euo pipefail

VERSION_ROOT="${HUIJIAN_FRONTEND_VERSION_ROOT:-/var/www/huijian-frontends}"
ACTIVE_ROOT="${HUIJIAN_FRONTEND_ACTIVE_ROOT:-/var/www/v2}"
LOCK_FILE="${HUIJIAN_FRONTEND_LOCK_FILE:-/var/lock/realguard-public-release.lock}"

usage() {
  cat <<'EOF'
Usage:
  sudo huijian-frontend-version list
  sudo huijian-frontend-version current
  sudo huijian-frontend-version save <version>
  sudo huijian-frontend-version activate <version>

Versions use letters, numbers, dots, underscores, and hyphens only.
EOF
}

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    printf 'Run this command with sudo.\n' >&2
    exit 77
  fi
}

validate_version() {
  local version="${1:-}"
  [[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]] || {
    printf 'Invalid frontend version: %s\n' "$version" >&2
    exit 64
  }
}

validate_release() {
  local release_dir="$1"
  [[ -d "$release_dir" && ! -L "$release_dir" ]] || {
    printf 'Frontend release does not exist: %s\n' "$release_dir" >&2
    exit 66
  }
  [[ -s "$release_dir/index.html" ]] || {
    printf 'Frontend release is missing index.html: %s\n' "$release_dir" >&2
    exit 65
  }
  [[ -d "$release_dir/assets" ]] || {
    printf 'Frontend release is missing assets/: %s\n' "$release_dir" >&2
    exit 65
  }
  local asset_ref asset_count=0
  while IFS= read -r asset_ref; do
    asset_count=$((asset_count + 1))
    [[ -f "$release_dir$asset_ref" ]] || {
      printf 'Frontend release references a missing asset: %s%s\n' "$release_dir" "$asset_ref" >&2
      exit 65
    }
  done < <(grep -oE '/assets/[A-Za-z0-9._/-]+' "$release_dir/index.html" | sort -u)
  [[ "$asset_count" -gt 0 ]] || {
    printf 'Frontend release index does not reference bundled assets: %s\n' "$release_dir" >&2
    exit 65
  }
}

current_version() {
  if [[ -f "$ACTIVE_ROOT/.HUIJIAN_FRONTEND_VERSION" ]]; then
    tr -d '[:space:]' <"$ACTIVE_ROOT/.HUIJIAN_FRONTEND_VERSION"
    return
  fi
  if [[ -f "$ACTIVE_ROOT/DEPLOYED_COMMIT" ]]; then
    printf 'v2-%s\n' "$(tr -d '[:space:]' <"$ACTIVE_ROOT/DEPLOYED_COMMIT")"
    return
  fi
  printf 'unversioned\n'
}

list_versions() {
  local current
  current="$(current_version)"
  printf '%-42s %-9s %s\n' VERSION ACTIVE COMMIT
  while IFS= read -r release_dir; do
    local version commit marker
    version="$(basename "$release_dir")"
    marker=""
    [[ "$version" == "$current" ]] && marker="yes"
    commit="-"
    [[ -f "$release_dir/DEPLOYED_COMMIT" ]] && commit="$(tr -d '[:space:]' <"$release_dir/DEPLOYED_COMMIT")"
    printf '%-42s %-9s %s\n' "$version" "$marker" "$commit"
  done < <(find "$VERSION_ROOT" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort)
}

save_active() {
  local version="$1"
  local destination="$VERSION_ROOT/$version"
  validate_release "$ACTIVE_ROOT"
  [[ ! -e "$destination" ]] || {
    printf 'Frontend version already exists: %s\n' "$version" >&2
    exit 73
  }
  local staging="$VERSION_ROOT/.${version}.saving.$$"
  trap 'rm -rf -- "$staging"' EXIT
  install -d -m 755 "$VERSION_ROOT"
  cp -a "$ACTIVE_ROOT" "$staging"
  printf '%s\n' "$version" >"$staging/.HUIJIAN_FRONTEND_VERSION"
  validate_release "$staging"
  mv "$staging" "$destination"
  chown -R root:root "$destination"
  trap - EXIT
  printf 'Saved frontend version: %s\n' "$version"
}

activate_version() {
  local version="$1"
  local source_dir="$VERSION_ROOT/$version"
  local next_dir="${ACTIVE_ROOT}.next.$$"
  local previous_dir="${ACTIVE_ROOT}.previous.$$"
  local previous_moved=0
  local active_installed=0

  validate_release "$source_dir"
  trap 'status=$?; set +e; rm -rf -- "$next_dir"; if [[ "$active_installed" == "1" ]]; then rm -rf -- "$ACTIVE_ROOT"; fi; if [[ "$previous_moved" == "1" && -d "$previous_dir" ]]; then rm -rf -- "$ACTIVE_ROOT"; mv "$previous_dir" "$ACTIVE_ROOT"; fi; exit "$status"' ERR INT TERM

  rm -rf -- "$next_dir" "$previous_dir"
  cp -a "$source_dir" "$next_dir"
  printf '%s\n' "$version" >"$next_dir/.HUIJIAN_FRONTEND_VERSION"
  validate_release "$next_dir"
  chown -R root:root "$next_dir"

  if [[ -d "$ACTIVE_ROOT" ]]; then
    mv "$ACTIVE_ROOT" "$previous_dir"
    previous_moved=1
  fi
  mv "$next_dir" "$ACTIVE_ROOT"
  active_installed=1

  nginx -t >/dev/null
  curl -kfsS --connect-timeout 2 --max-time 12 \
    -H 'Host: www.rrreal.cn' https://127.0.0.1/ \
    | grep -q '<div id="root"></div>'

  rm -rf -- "$previous_dir"
  previous_moved=0
  active_installed=0
  trap - ERR INT TERM
  printf 'Activated frontend version: %s\n' "$version"
}

main() {
  local command="${1:-}"
  case "$command" in
    list)
      list_versions
      ;;
    current)
      current_version
      ;;
    save|activate)
      require_root
      local version="${2:-}"
      validate_version "$version"
      install -d -m 755 "$VERSION_ROOT"
      install -m 600 /dev/null "$LOCK_FILE"
      exec 9>"$LOCK_FILE"
      flock -w 900 9 || { printf 'Another public release transaction is running.\n' >&2; exit 75; }
      if [[ "$command" == "save" ]]; then
        save_active "$version"
      else
        activate_version "$version"
      fi
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
}

main "$@"
