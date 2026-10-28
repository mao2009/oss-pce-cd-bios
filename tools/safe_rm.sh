#!/usr/bin/env bash
# Guarded recursive removal for scratch directories.
#
# Source this file; do not execute it. Use safe_rm_rf in place of rm -rf.
#
# Trust model
#   - The allowed root is fixed below and is never taken from the caller.
#     Callers pass only the target path.
#   - The root must already exist as a real directory owned by the current
#     user (not a symlink). Create it yourself before use.
#   - Refused: empty or unset targets, relative paths, paths with "." or ".."
#     components, the root itself, anything outside the root, symlinked
#     targets or symlinked ancestors, any path containing a .git entry
#     (repository, worktree or submodule), any path on another filesystem or
#     containing a mount point, and any path the checks cannot resolve.
#
# Residual risk (not fully closed here)
#   - Time-of-check to time-of-use: the checks and rm are separate steps. An
#     attacker who can write inside the root can swap a directory between the
#     checks and the removal. Mitigation: keep the root private to one user
#     (mode 0700, owned by that user) and do not share it with other users.
#   - Checks are not exhaustive for hostile content. They detect the cases
#     listed above; they do not prove the tree is safe to delete.

SAFE_RM_ROOT="${HOME:-}/.cache/oss-pce-safe-rm"

safe_rm_rf() {
  local target="${1-}"
  local root parent parent_real real dev_root dev_real owner count_all count_xdev

  if [ -z "$target" ]; then echo "safe_rm_rf: refuse: empty target" >&2; return 2; fi
  if [ -z "${HOME:-}" ]; then echo "safe_rm_rf: refuse: HOME unset" >&2; return 2; fi
  case "$target" in
    /*) ;;
    *) echo "safe_rm_rf: refuse: relative path: $target" >&2; return 2 ;;
  esac
  case "$target" in
    */.|*/..|*/./*|*/../*) echo "safe_rm_rf: refuse: dot component: $target" >&2; return 2 ;;
  esac

  if [ ! -d "$SAFE_RM_ROOT" ] || [ -L "$SAFE_RM_ROOT" ]; then
    echo "safe_rm_rf: refuse: allowed root missing or symlink: $SAFE_RM_ROOT" >&2; return 2
  fi
  root=$(realpath -e -- "$SAFE_RM_ROOT") || { echo "safe_rm_rf: refuse: cannot resolve root" >&2; return 2; }
  owner=$(stat -c %u -- "$root") || return 2
  if [ "$owner" != "$(id -u)" ]; then echo "safe_rm_rf: refuse: root not owned by current user" >&2; return 2; fi
  if git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
    echo "safe_rm_rf: refuse: root is inside a git work tree: $root" >&2; return 2
  fi

  case "$target" in
    "$SAFE_RM_ROOT"/?*) ;;
    *) echo "safe_rm_rf: refuse: outside allowed root: $target" >&2; return 2 ;;
  esac

  parent=$(dirname -- "$target")
  parent_real=$(realpath -e -- "$parent" 2>/dev/null) || { echo "safe_rm_rf: refuse: cannot resolve parent: $parent" >&2; return 2; }
  if [ "$parent_real" != "$(realpath -m -- "$parent")" ]; then
    echo "safe_rm_rf: refuse: symlink in ancestor path: $target" >&2; return 2
  fi
  real="$parent_real/$(basename -- "$target")"
  case "$real" in
    "$root"/?*) ;;
    *) echo "safe_rm_rf: refuse: resolves outside allowed root: $real" >&2; return 2 ;;
  esac

  if [ -L "$real" ]; then echo "safe_rm_rf: refuse: symlink target: $real" >&2; return 2; fi
  if [ ! -e "$real" ]; then echo "safe_rm_rf: nothing to remove: $real" >&2; return 0; fi
  if [ ! -d "$real" ]; then echo "safe_rm_rf: refuse: not a directory: $real" >&2; return 2; fi

  if find "$real" -name .git -print -quit 2>/dev/null | grep -q .; then
    echo "safe_rm_rf: refuse: contains .git (repository, worktree or submodule): $real" >&2; return 2
  fi
  if git -C "$real" rev-parse --git-dir >/dev/null 2>&1; then
    echo "safe_rm_rf: refuse: inside a git work tree: $real" >&2; return 2
  fi

  dev_root=$(stat -c %d -- "$root") || return 2
  dev_real=$(stat -c %d -- "$real") || return 2
  if [ "$dev_root" != "$dev_real" ]; then
    echo "safe_rm_rf: refuse: different filesystem: $real" >&2; return 2
  fi
  count_all=$(find "$real" -mindepth 1 2>/dev/null | wc -l)
  count_xdev=$(find "$real" -mindepth 1 -xdev 2>/dev/null | wc -l)
  if [ "$count_all" != "$count_xdev" ]; then
    echo "safe_rm_rf: refuse: mount point inside target: $real" >&2; return 2
  fi

  echo "safe_rm_rf: removing $real" >&2
  rm -rf -- "$real"
  if [ -e "$real" ]; then echo "safe_rm_rf: removal incomplete: $real" >&2; return 1; fi
  return 0
}
