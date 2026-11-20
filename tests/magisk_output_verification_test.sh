#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 PixeneOS contributors

set -euo pipefail

source src/util_functions.sh

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT
WORKDIR="${TEST_ROOT}/work"
mkdir -p "${WORKDIR}/modules"
printf 'selected APK fixture' >"${WORKDIR}/modules/magisk.apk"

PARTITIONS=$'boot\ninit_boot\nvendor_boot'
MAGISK_INFO='PREINITDEVICE=sda10'
EXTRACT_LOG="${TEST_ROOT}/extract.log"
PAYLOAD_LOG="${TEST_ROOT}/payload.log"
: >"${EXTRACT_LOG}"
: >"${PAYLOAD_LOG}"
EXTRACT_IDENTICAL=false
ROOTLESS_HAS_MAGISK=false
BOOT_UNPACK_RESULT=success
CPIO_UNPACK_RESULT=success
PAYLOAD_RESULT=success
VERIFY_CALLS=()
PAIR_VERIFY_CALLS=()

fail() {
  echo "$*" >&2
  exit 1
}

run_executable_tool() {
  local tool="${1}"
  shift
  [[ "${tool}" == avbroot ]] || fail "unexpected tool: ${tool}"

  case "${1} ${2}" in
    'ota list')
      printf '%s\n' "${PARTITIONS}"
      ;;
    'ota extract')
      local directory='' partition='' input=''
      shift 2
      while (($#)); do
        case "${1}" in
          --directory)
            directory="${2}"
            shift 2
            ;;
          --partition)
            partition="${2}"
            shift 2
            ;;
          --input)
            input="${2}"
            shift 2
            ;;
          *)
            fail "unexpected ota extract argument: ${1}"
            ;;
        esac
      done
      [[ -n "${directory}" && -n "${partition}" && -n "${input}" ]] ||
        fail "extract did not receive input/directory/partition"
      mkdir -p -- "${directory}"
      if [[ "${EXTRACT_IDENTICAL}" == true ]]; then
        printf 'same-boot-image' >"${directory}/${partition}.img"
      elif [[ "$(basename -- "${input}")" == *rootless* ]]; then
        printf 'rootless-boot-image' >"${directory}/${partition}.img"
      else
        printf 'magisk-boot-image' >"${directory}/${partition}.img"
      fi
      printf '%s\n' "${partition}" >>"${EXTRACT_LOG}"
      ;;
    'boot magisk-info')
      local image=''
      shift 2
      while (($#)); do
        case "${1}" in
          --image)
            image="${2}"
            shift 2
            ;;
          *)
            fail "unexpected magisk-info argument: ${1}"
            ;;
        esac
      done
      [[ -n "${image}" ]] || fail "magisk-info did not receive an image"
      if grep -Fq 'rootless-boot-image' "${image}" &&
        [[ "${ROOTLESS_HAS_MAGISK}" != true ]]; then
        return 1
      fi
      [[ -n "${MAGISK_INFO}" ]] || return 1
      printf '%s\n' "${MAGISK_INFO}"
      ;;
    'boot unpack')
      local input='' header='' prefix=''
      shift 2
      while (($#)); do
        case "${1}" in
          --input) input="${2}"; shift 2 ;;
          --output-header) header="${2}"; shift 2 ;;
          --output-ramdisk-prefix) prefix="${2}"; shift 2 ;;
          --no-output-kernel|--no-output-second|--no-output-recovery-dtbo|\
            --no-output-dtb|--no-output-vts-signature|--no-output-bootconfig|--quiet)
            shift ;;
          *) fail "unexpected boot unpack argument: ${1}" ;;
        esac
      done
      [[ -f "${input}" && "${header}" == "${input%/*}/boot.toml" &&
        "${prefix}" == "${input%/*}/ramdisk." ]] ||
        fail "boot unpack did not use the extracted target and temporary directory"
      [[ "${BOOT_UNPACK_RESULT}" == success ]] || return 1
      printf 'header fixture' >"${header}"
      printf 'ramdisk fixture' >"${prefix}0"
      ;;
    'cpio unpack')
      local input='' info='' tree=''
      shift 2
      while (($#)); do
        case "${1}" in
          --input) input="${2}"; shift 2 ;;
          --output-info) info="${2}"; shift 2 ;;
          --output-tree) tree="${2}"; shift 2 ;;
          --quiet) shift ;;
          *) fail "unexpected cpio unpack argument: ${1}" ;;
        esac
      done
      [[ -f "${input}" && "${input##*/}" == ramdisk.0 &&
        "${info}" == "${input%/*}/cpio.toml" &&
        "${tree}" == "${input%/*}/tree" ]] ||
        fail "cpio unpack did not consume the freshly extracted ramdisk"
      [[ "${CPIO_UNPACK_RESULT}" == success ]] || return 1
      mkdir -p "${tree}"
      printf 'cpio fixture' >"${info}"
      printf 'fresh extraction marker' >"${tree}/.extracted"
      ;;
    *)
      fail "unexpected avbroot call: $*"
      ;;
  esac
}

run_magisk_artifact() {
  [[ "${1:-}" == verify-ramdisk ]] ||
    fail "unexpected Magisk artifact action: ${1:-}"
  shift
  local apk='' tree='' info=''
  while (($#)); do
    case "${1}" in
      --apk) apk="${2}"; shift 2 ;;
      --ramdisk-dir) tree="${2}"; shift 2 ;;
      --cpio-info) info="${2}"; shift 2 ;;
      *) fail "unexpected Magisk artifact argument: ${1}" ;;
    esac
  done
  [[ "${apk}" == "${WORKDIR}/modules/magisk.apk" && -f "${apk}" ]] ||
    fail "finished-image verification did not use the selected Magisk APK"
  [[ "${tree}" == "${WORKDIR}"/magisk-verify.*/tree &&
    -f "${tree}/.extracted" ]] ||
    fail "finished-image verification did not receive the freshly unpacked tree"
  [[ "${info}" == "${tree%/*}/cpio.toml" && -f "${info}" ]] ||
    fail "finished-image verification did not receive the extracted CPIO metadata"
  printf '%s|%s\n' "${apk}" "${tree}" >>"${PAYLOAD_LOG}"
  [[ "${PAYLOAD_RESULT}" == success ]]
}

assert_verification_scratch_removed() {
  if compgen -G "${WORKDIR}/magisk-verify.*" >/dev/null; then
    fail "Magisk verification left its temporary extraction directory behind"
  fi
}

ota="${TEST_ROOT}/rooted.zip"
printf 'ota' >"${ota}"

verify_magisk_ota "${ota}" sda10 >/dev/null ||
  fail "valid Magisk OTA boot-patch verification failed"
[[ "$(tail -n1 "${EXTRACT_LOG}")" == init_boot ]] ||
  fail "init_boot was not preferred over boot"
[[ "$(wc -l <"${PAYLOAD_LOG}")" -eq 1 ]] ||
  fail "valid Magisk OTA was accepted without comparing its payload to the APK"
assert_verification_scratch_removed

PARTITIONS='boot'
verify_magisk_ota "${ota}" sda10 >/dev/null ||
  fail "boot fallback verification failed"
[[ "$(tail -n1 "${EXTRACT_LOG}")" == boot ]] ||
  fail "boot fallback was not selected"

PARTITIONS=$'boot\ninit_boot'
MAGISK_INFO='PREINITDEVICE=wrong'
: >"${PAYLOAD_LOG}"
if verify_magisk_ota "${ota}" sda10 >/dev/null 2>&1; then
  fail "wrong Magisk preinit device was accepted"
fi
[[ ! -s "${PAYLOAD_LOG}" ]] || fail "payload check ran after incorrect preinit metadata"
assert_verification_scratch_removed

MAGISK_INFO=''
if verify_magisk_ota "${ota}" sda10 >/dev/null 2>&1; then
  fail "non-Magisk boot image was accepted"
fi

PARTITIONS='vendor_boot'
MAGISK_INFO='PREINITDEVICE=sda10'
if verify_magisk_ota "${ota}" sda10 >/dev/null 2>&1; then
  fail "OTA without boot/init_boot was accepted"
fi

PARTITIONS=$'boot\ninit_boot'
MAGISK[PREINIT]=sda10
RESOLVED_ROOT_MODE=magisk
OUTPUTS[PATCHED_OTA]="${TEST_ROOT}/rejected-magisk.zip"
for failure in payload boot-unpack cpio-unpack; do
  BOOT_UNPACK_RESULT=success
  CPIO_UNPACK_RESULT=success
  PAYLOAD_RESULT=success
  case "${failure}" in
    payload) PAYLOAD_RESULT=failure ;;
    boot-unpack) BOOT_UNPACK_RESULT=failure ;;
    cpio-unpack) CPIO_UNPACK_RESULT=failure ;;
  esac
  printf 'rejected OTA' >"${OUTPUTS[PATCHED_OTA]}"
  printf 'rejected signature' >"${OUTPUTS[PATCHED_OTA]}.csig"
  : >"${PAYLOAD_LOG}"
  if verify_requested_root_outputs >/dev/null 2>&1; then
    fail "${failure} failure was accepted despite matching PREINITDEVICE"
  fi
  [[ ! -e "${OUTPUTS[PATCHED_OTA]}" && ! -e "${OUTPUTS[PATCHED_OTA]}.csig" ]] ||
    fail "${failure} failure retained an unverified Magisk OTA or signature"
  if [[ "${failure}" == payload ]]; then
    [[ -s "${PAYLOAD_LOG}" ]] || fail "payload mismatch did not reach APK comparison"
  else
    [[ ! -s "${PAYLOAD_LOG}" ]] || fail "payload comparison ran after ${failure} failure"
  fi
  assert_verification_scratch_removed
done
BOOT_UNPACK_RESULT=success
CPIO_UNPACK_RESULT=success
PAYLOAD_RESULT=success

rootless_ota="${TEST_ROOT}/pair-rootless.zip"
magisk_ota="${TEST_ROOT}/pair-magisk.zip"
printf 'rootless ota' >"${rootless_ota}"
printf 'magisk ota' >"${magisk_ota}"

verify_paired_root_outputs "${rootless_ota}" "${magisk_ota}" >/dev/null ||
  fail "distinct rootless/Magisk pair was rejected"

EXTRACT_IDENTICAL=true
if verify_paired_root_outputs "${rootless_ota}" "${magisk_ota}" >/dev/null 2>&1; then
  fail "pair with identical boot targets was accepted"
fi
EXTRACT_IDENTICAL=false

ROOTLESS_HAS_MAGISK=true
if verify_paired_root_outputs "${rootless_ota}" "${magisk_ota}" >/dev/null 2>&1; then
  fail "rootless output containing Magisk evidence was accepted"
fi
ROOTLESS_HAS_MAGISK=false

verify_magisk_ota() {
  VERIFY_CALLS+=("${1}")
  [[ "${VERIFY_RESULT:-success}" == success ]]
}

verify_paired_root_outputs() {
  PAIR_VERIFY_CALLS+=("${1}|${2}")
  [[ "${PAIR_VERIFY_RESULT:-success}" == success ]]
}

MAGISK[PREINIT]=sda10
OUTPUTS[PATCHED_OTA]="${TEST_ROOT}/single-magisk.zip"
OUTPUTS[PATCHED_OTA_ROOTLESS]="${TEST_ROOT}/paired-rootless.zip"
OUTPUTS[PATCHED_OTA_MAGISK]="${TEST_ROOT}/paired-magisk.zip"

RESOLVED_ROOT_MODE=rootless
verify_requested_root_outputs
[[ "${#VERIFY_CALLS[@]}" -eq 0 && "${#PAIR_VERIFY_CALLS[@]}" -eq 0 ]] ||
  fail "rootless output unexpectedly ran Magisk verification"

RESOLVED_ROOT_MODE=magisk
VERIFY_CALLS=()
PAIR_VERIFY_CALLS=()
verify_requested_root_outputs
[[ "${VERIFY_CALLS[*]}" == "${OUTPUTS[PATCHED_OTA]}" ]] ||
  fail "single Magisk mode verified the wrong OTA"
[[ "${#PAIR_VERIFY_CALLS[@]}" -eq 0 ]] ||
  fail "single Magisk mode unexpectedly ran paired verification"

RESOLVED_ROOT_MODE=both
VERIFY_CALLS=()
PAIR_VERIFY_CALLS=()
verify_requested_root_outputs
[[ "${VERIFY_CALLS[*]}" == "${OUTPUTS[PATCHED_OTA_MAGISK]}" ]] ||
  fail "paired mode verified the wrong Magisk OTA"
[[ "${PAIR_VERIFY_CALLS[*]}" == "${OUTPUTS[PATCHED_OTA_ROOTLESS]}|${OUTPUTS[PATCHED_OTA_MAGISK]}" ]] ||
  fail "paired mode compared the wrong OTA outputs"

printf 'bad ota' >"${OUTPUTS[PATCHED_OTA_MAGISK]}"
printf 'bad csig' >"${OUTPUTS[PATCHED_OTA_MAGISK]}.csig"
VERIFY_RESULT=failure
if verify_requested_root_outputs >/dev/null 2>&1; then
  fail "failed Magisk verification did not fail the build"
fi
[[ ! -e "${OUTPUTS[PATCHED_OTA_MAGISK]}" &&
  ! -e "${OUTPUTS[PATCHED_OTA_MAGISK]}.csig" ]] ||
  fail "failed Magisk artifacts were not removed"

VERIFY_RESULT=success
PAIR_VERIFY_RESULT=failure
printf 'bad pair ota' >"${OUTPUTS[PATCHED_OTA_MAGISK]}"
printf 'bad pair csig' >"${OUTPUTS[PATCHED_OTA_MAGISK]}.csig"
if verify_requested_root_outputs >/dev/null 2>&1; then
  fail "failed paired-output verification did not fail the build"
fi
[[ ! -e "${OUTPUTS[PATCHED_OTA_MAGISK]}" &&
  ! -e "${OUTPUTS[PATCHED_OTA_MAGISK]}.csig" ]] ||
  fail "failed paired Magisk artifacts were not removed"

grep -Fq 'verify_requested_root_outputs || return 1' src/util_functions.sh ||
  fail "patch pipeline does not enforce Magisk output verification"
grep -Fq 'not proof of a working runtime' src/util_functions.sh ||
  fail "patch pipeline still overstates static Magisk verification as runtime root"

echo "Magisk output verification tests passed"
