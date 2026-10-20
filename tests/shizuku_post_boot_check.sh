#!/usr/bin/env bash
set -u
set -o pipefail

readonly MANAGER_PACKAGE='moe.shizuku.privileged.api'
readonly MANAGER_PROVIDER="content://${MANAGER_PACKAGE}.shizuku"
readonly SERVER_PROCESS='shizuku_server'

usage() {
  printf 'Usage: %s rootless|magisk\n' "$0" >&2
}

if [[ "$#" -ne 1 ]]; then
  usage
  exit 2
fi

case "$1" in
  rootless) expected_uid='2000' ;;
  magisk) expected_uid='0' ;;
  *)
    usage
    exit 2
    ;;
esac

if ! command -v adb >/dev/null 2>&1; then
  printf 'UNKNOWN: adb is not installed; no checks were run.\n'
  exit 2
fi

adb_shell() {
  adb -d shell "$@"
}

if ! adb -d get-state >/dev/null 2>&1; then
  printf 'UNKNOWN: no single online USB ADB device is available. This script does not select network ADB.\n'
  exit 2
fi
printf 'INFO: using the selected USB ADB device only (mode: %s).\n' "$1"

failures=0
unresolved=0

manager_path_output="$(adb_shell pm path "$MANAGER_PACKAGE" 2>&1)"
manager_path_status=$?
if [[ "$manager_path_status" -ne 0 ]]; then
  printf 'UNKNOWN: Package Manager query for %s failed; installation state could not be determined.\n' "$MANAGER_PACKAGE"
  unresolved=$((unresolved + 1))
elif [[ "$manager_path_output" == *"package:"* ]]; then
  printf 'PASS: manager package %s is installed.\n' "$MANAGER_PACKAGE"
elif [[ "$manager_path_output" == *'Error'* || "$manager_path_output" == *'Exception'* || "$manager_path_output" == *'Unknown command'* ]]; then
  printf 'UNKNOWN: Package Manager returned an error while querying %s; installation state could not be determined.\n' "$MANAGER_PACKAGE"
  unresolved=$((unresolved + 1))
elif [[ -z "${manager_path_output//[[:space:]]/}" ]]; then
  printf 'FAIL: a successful Package Manager query found no installed path for %s.\n' "$MANAGER_PACKAGE"
  failures=$((failures + 1))
else
  printf 'UNKNOWN: Package Manager returned an unrecognized response for %s; installation state could not be determined.\n' "$MANAGER_PACKAGE"
  unresolved=$((unresolved + 1))
fi

process_output="$(adb_shell ps -A -o UID,PID,NAME 2>&1)"
process_status=$?
if [[ "$process_status" -ne 0 ]]; then
  printf 'UNKNOWN: this Android build does not expose the expected ps output format.\n'
  unresolved=$((unresolved + 1))
else
  process_format_valid="$(printf '%s\n' "$process_output" | awk '
    NR == 1 {
      if (NF != 3 || $1 != "UID" || $2 != "PID" || $3 != "NAME") invalid = 1
      next
    }
    NF != 3 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ { invalid = 1 }
    END { if (NR == 0 || invalid) print "invalid"; else print "valid" }
  ')"
  if [[ "$process_format_valid" != 'valid' ]]; then
    printf 'UNKNOWN: ps output did not have the expected UID PID NAME header and numeric UID/PID columns.\n'
    unresolved=$((unresolved + 1))
  else
    process_rows="$(printf '%s\n' "$process_output" | awk -v name="$SERVER_PROCESS" 'NR > 1 && $3 == name { print $1, $2 }')"
    if [[ -z "$process_rows" ]]; then
      printf 'FAIL: process %s is not running.\n' "$SERVER_PROCESS"
      failures=$((failures + 1))
    else
      process_count="$(printf '%s\n' "$process_rows" | awk 'NF == 2 { count++ } END { print count + 0 }')"
      actual_rows="$(printf '%s\n' "$process_rows" | tr '\n' ';' | sed 's/;$//')"
      wrong_uid_rows="$(printf '%s\n' "$process_rows" | awk -v uid="$expected_uid" '$1 != uid { print }')"
      if [[ "$process_count" -ne 1 ]]; then
        printf 'FAIL: expected exactly one %s process, found %s (UID PID rows: %s).\n' \
          "$SERVER_PROCESS" "$process_count" "$actual_rows"
        failures=$((failures + 1))
      elif [[ -n "$wrong_uid_rows" ]]; then
        printf 'FAIL: %s is running with an unexpected UID for %s mode (expected UID %s; observed %s).\n' \
          "$SERVER_PROCESS" "$1" "$expected_uid" "$actual_rows"
        failures=$((failures + 1))
      else
        read -r actual_uid process_pid <<<"$process_rows"
        printf 'PASS: exactly one %s process is running with UID %s (PID %s), as expected for %s mode.\n' \
          "$SERVER_PROCESS" "$actual_uid" "$process_pid" "$1"
      fi
    fi
  fi
fi

# Shizuku v13.6.0 sends its Binder through the manager's exported
# <package>.shizuku ContentProvider; it does not register a named Binder service.
# getBinder only returns a Bundle when the provider already holds a live Binder.
binder_output="$(adb_shell content call \
  --uri "$MANAGER_PROVIDER" \
  --method getBinder \
  --extra s modos_read_only_probe 1 2>&1)"
binder_status=$?
if [[ "$binder_status" -ne 0 ]]; then
  printf 'UNKNOWN: the manager Binder provider could not be queried; Android may not support this read-only content call.\n'
  unresolved=$((unresolved + 1))
elif [[ "$binder_output" == *'Result: null'* || "$binder_output" == 'null'* ]]; then
  printf 'FAIL: the manager provider returned no live Shizuku Binder.\n'
  failures=$((failures + 1))
elif [[ "$binder_output" == *'Result: Bundle'* || "$binder_output" == *'Bundle['* ]]; then
  printf 'PASS: the manager provider returned a Binder bundle.\n'
else
  printf 'UNKNOWN: the manager provider response format is unrecognized; response was not interpreted as Binder availability.\n'
  unresolved=$((unresolved + 1))
fi

# AOSP exposes the Wireless debugging toggle as the global adb_wifi_enabled
# setting. This script reads it only. Missing/unknown values are reported as
# unavailable because ROMs can differ in whether the setting is exposed.
wireless_output="$(adb_shell settings get global adb_wifi_enabled 2>&1)"
wireless_status=$?
wireless_value="$(printf '%s' "$wireless_output" | tr -d '\r' | tail -n 1)"
if [[ "$wireless_status" -ne 0 ]]; then
  printf 'UNKNOWN: could not read global setting adb_wifi_enabled.\n'
elif [[ "$wireless_value" == '0' ]]; then
  printf 'PASS: wireless debugging setting adb_wifi_enabled is 0 (off).\n'
elif [[ "$wireless_value" == '1' ]]; then
  printf 'FAIL: wireless debugging setting adb_wifi_enabled is 1 (on).\n'
  failures=$((failures + 1))
else
  printf 'UNKNOWN: adb_wifi_enabled is unavailable or has an unrecognized value (%s).\n' \
    "${wireless_value:-empty}"
fi

printf 'MANUAL: open Shizuku and confirm its status; request permission from a client app and confirm that app still needs its normal per-app approval. This script does not request or change client permissions.\n'

if [[ "$failures" -gt 0 ]]; then
  printf 'RESULT: FAIL (%s required check(s) failed; %s check(s) inconclusive).\n' "$failures" "$unresolved"
  exit 1
fi
if [[ "$unresolved" -gt 0 ]]; then
  printf 'RESULT: INCONCLUSIVE (%s check(s) could not be verified).\n' "$unresolved"
  exit 2
fi
printf 'RESULT: PASS for the automated checks above; the manual approval check remains required.\n'
