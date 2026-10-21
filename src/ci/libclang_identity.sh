#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Resolve the exact shared library clang-sys will use, then pin Cargo to it.

declare -ga LIBCLANG_CANDIDATE_PATHS=()

function _libclang_add_candidate() {
  local candidate="${1}" resolved absolute_candidate existing

  [[ -f "${candidate}" ]] || return 0
  case "${candidate##*/}" in
    libclang.so|libclang-[0-9]*.so) ;;
    *) return 0 ;;
  esac
  resolved="$(readlink -f -- "${candidate}")" || return 1
  [[ -f "${resolved}" ]] || return 0
  for existing in "${LIBCLANG_CANDIDATE_PATHS[@]}"; do
    [[ "$(readlink -f -- "${existing}")" == "${resolved}" ]] && return 0
  done
  absolute_candidate="$(cd -- "$(dirname -- "${candidate}")" && pwd -P)/${candidate##*/}" || return 1
  LIBCLANG_CANDIDATE_PATHS+=("${absolute_candidate}")
}

function _libclang_collect_directory() {
  local directory="${1}" candidate
  local -a candidates
  [[ -d "${directory}" ]] || return 0
  candidates=("${directory}"/libclang.so "${directory}"/libclang-*.so)
  for candidate in "${candidates[@]}"; do
    _libclang_add_candidate "${candidate}" || return 1
  done
}

function _libclang_select_single_candidate() {
  if [[ "${#LIBCLANG_CANDIDATE_PATHS[@]}" -ne 1 ]]; then
    echo "Error: expected one unambiguous libclang shared object; found ${#LIBCLANG_CANDIDATE_PATHS[@]} candidates." >&2
    return 1
  fi
  printf '%s\n' "${LIBCLANG_CANDIDATE_PATHS[0]}"
}

function resolve_libclang_shared_object() {
  local configured_path="${LIBCLANG_PATH-}" llvm_config='' prefix package package_list
  local candidate_file
  local -a package_names
  LIBCLANG_CANDIDATE_PATHS=()

  if [[ -n "${configured_path}" ]]; then
    if [[ -f "${configured_path}" ]]; then
      _libclang_add_candidate "${configured_path}" || return 1
    elif [[ -d "${configured_path}" ]]; then
      _libclang_collect_directory "${configured_path}" || return 1
    else
      echo "Error: LIBCLANG_PATH does not identify a libclang file or directory." >&2
      return 1
    fi
    _libclang_select_single_candidate
    return $?
  fi

  if [[ -n "${LLVM_CONFIG_PATH-}" ]]; then
    llvm_config="${LLVM_CONFIG_PATH}"
  elif command -v llvm-config >/dev/null 2>&1; then
    llvm_config="$(command -v llvm-config)"
  fi
  if [[ -n "${llvm_config}" ]]; then
    prefix="$("${llvm_config}" --prefix 2>/dev/null | sed -n '1p')" || prefix=''
    if [[ -n "${prefix}" ]]; then
      for candidate_file in bin lib lib64; do
        _libclang_collect_directory "${prefix}/${candidate_file}" || return 1
      done
    fi
    if [[ "${#LIBCLANG_CANDIDATE_PATHS[@]}" -gt 0 ]]; then
      _libclang_select_single_candidate
      return $?
    fi
  fi

  if command -v dpkg-query >/dev/null 2>&1; then
    package_list="$(dpkg-query -W -f='${binary:Package}\n' 'libclang*' 2>/dev/null)" || package_list=''
    while IFS= read -r package; do
      [[ -n "${package}" ]] || continue
      while IFS= read -r candidate_file; do
        _libclang_add_candidate "${candidate_file}" || return 1
      done < <(dpkg-query -L "${package}" 2>/dev/null)
    done <<<"${package_list}"
  fi

  if [[ "${#LIBCLANG_CANDIDATE_PATHS[@]}" -eq 0 ]] && command -v rpm >/dev/null 2>&1; then
    package_names=(libclang-devel clang-devel clang-libs libclang rocm-clang-devel)
    for package in "${package_names[@]}"; do
      package_list="$(rpm -ql "${package}" 2>/dev/null)" || continue
      while IFS= read -r candidate_file; do
        _libclang_add_candidate "${candidate_file}" || return 1
      done <<<"${package_list}"
    done
  fi

  if [[ "${#LIBCLANG_CANDIDATE_PATHS[@]}" -gt 0 ]]; then
    _libclang_select_single_candidate
    return $?
  fi

  echo "Error: could not resolve a libclang shared object from LIBCLANG_PATH, llvm-config, or installed package files." >&2
  return 1
}

function resolve_libclang_identity() {
  local selected_path library_path content_sha256 owner_query owner package version brew_identity
  selected_path="$(resolve_libclang_shared_object)" || return 1
  library_path="$(readlink -f -- "${selected_path}")" || return 1
  if [[ "${library_path}" == *$'\n'* || "${library_path}" == *$'\r'* ]]; then
    echo "Error: resolved libclang path contains a line break." >&2
    return 1
  fi
  content_sha256="$(sha256sum <"${library_path}" | awk '{print $1}')" || return 1
  owner='unowned'

  if command -v dpkg-query >/dev/null 2>&1; then
    owner_query="$(dpkg-query -S -- "${library_path}" 2>/dev/null)" || owner_query=''
    if [[ -n "${owner_query}" ]]; then
      if [[ "${owner_query}" == *$'\n'* ]]; then
        echo "Error: multiple dpkg packages claim the selected libclang file." >&2
        return 1
      fi
      package="${owner_query%%: *}"
      version="$(dpkg-query -W -f='${binary:Package}=${Version}' "${package}" 2>/dev/null)" || version=''
      [[ -n "${version}" ]] || return 1
      owner="dpkg:${version}"
    fi
  fi

  if [[ "${owner}" == 'unowned' ]] && command -v rpm >/dev/null 2>&1; then
    owner_query="$(rpm -qf --qf '%{NAME}=%{VERSION}-%{RELEASE}.%{ARCH}' "${library_path}" 2>/dev/null)" || owner_query=''
    if [[ -n "${owner_query}" ]]; then
      if [[ "${owner_query}" == *$'\n'* ]]; then
        echo "Error: multiple RPM packages claim the selected libclang file." >&2
        return 1
      fi
      owner="rpm:${owner_query}"
    fi
  fi

  if [[ "${owner}" == 'unowned' && "${library_path}" == */Cellar/*/*/* ]] &&
    command -v brew >/dev/null 2>&1; then
    local cellared_path="${library_path#*/Cellar/}"
    package="${cellared_path%%/*}"
    version="${cellared_path#*/}"
    version="${version%%/*}"
    brew_identity="$(HOMEBREW_NO_AUTO_UPDATE=1 brew list --versions "${package}" 2>/dev/null)" || brew_identity=''
    if [[ "${brew_identity}" == "${package} ${version}" ]]; then
      owner="brew:${package}=${version}"
    fi
  fi

  printf '%s\n' \
    "libclang_selected_path=${selected_path}" \
    "libclang_path=${library_path}" \
    "libclang_sha256=${content_sha256}" \
    "libclang_owner=${owner}"
}
