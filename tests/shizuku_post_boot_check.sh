#!/usr/bin/env bash
set -u
set -o pipefail

readonly MANAGER_PACKAGE='moe.shizuku.privileged.api'
readonly MANAGER_PROVIDER="content://${MANAGER_PACKAGE}.shizuku"
readonly MANAGER_BOOT_RECEIVER="${MANAGER_PACKAGE}/moe.shizuku.manager.receiver.BootCompleteReceiver"
readonly SERVER_PROCESS='shizuku_server'

usage() {
  printf 'Usage: %s rootless|magisk\n' "$0" >&2
}

if [[ "$#" -ne 1 ]]; then
  usage
  exit 2
fi

case "$1" in
  rootless)
    expected_uid='2000'
    expected_label='u:r:shell:s0'
    process_fields='LABEL,UID,PID,NAME'
    ;;
  magisk)
    expected_uid='0'
    expected_label=''
    process_fields='UID,PID,NAME'
    ;;
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

if [[ "$1" == rootless ]]; then
  service_output="$(adb_shell getprop init.svc.shizuku 2>&1)"
  service_status=$?
  service_value="${service_output//$'\r'/}"
  if [[ "$service_status" -ne 0 ]]; then
    printf 'UNKNOWN: could not read the Shizuku init service state.\n'
    unresolved=$((unresolved + 1))
  elif [[ "$service_value" == running ]]; then
    printf 'PASS: init.svc.shizuku is running.\n'
  elif [[ "$service_value" == stopped || "$service_value" == restarting ]]; then
    printf 'FAIL: init.svc.shizuku is %s.\n' "$service_value"
    failures=$((failures + 1))
  else
    printf 'UNKNOWN: init.svc.shizuku is unavailable or has an unrecognized value.\n'
    unresolved=$((unresolved + 1))
  fi

  enforcing_output="$(adb_shell getenforce 2>&1)"
  enforcing_status=$?
  enforcing_value="${enforcing_output//$'\r'/}"
  if [[ "$enforcing_status" -ne 0 ]]; then
    printf 'UNKNOWN: could not read SELinux enforcement state.\n'
    unresolved=$((unresolved + 1))
  elif [[ "$enforcing_value" == Enforcing ]]; then
    printf 'PASS: SELinux is Enforcing.\n'
  elif [[ "$enforcing_value" == Permissive || "$enforcing_value" == Disabled ]]; then
    printf 'FAIL: SELinux is %s; enforcing-policy startup has not been demonstrated.\n' "$enforcing_value"
    failures=$((failures + 1))
  else
    printf 'UNKNOWN: SELinux enforcement response is unrecognized.\n'
    unresolved=$((unresolved + 1))
  fi

  # A previous explicit ENABLED choice in the manager takes precedence over
  # the image's default component override. Query effective intent resolution
  # for the owner without MATCH_DISABLED_COMPONENTS; never change user state.
  receiver_output="$(adb_shell cmd package query-receivers --components --user 0 \
    -a android.intent.action.LOCKED_BOOT_COMPLETED -p "$MANAGER_PACKAGE" 2>&1)"
  receiver_status=$?
  receiver_value="${receiver_output//$'\r'/}"
  if [[ "$receiver_status" -ne 0 ]]; then
    printf 'UNKNOWN: could not query the manager boot receiver effective state.\n'
    unresolved=$((unresolved + 1))
  elif [[ "$receiver_value" == 'No receivers found' ]]; then
    printf 'PASS: the manager boot receiver is disabled for user 0.\n'
  elif [[ "$receiver_value" == "$MANAGER_BOOT_RECEIVER" ]]; then
    printf 'FAIL: the manager boot receiver is enabled for user 0; turn off Start on boot in Shizuku to prevent its ADB startup path.\n'
    failures=$((failures + 1))
  else
    printf 'UNKNOWN: manager boot receiver query returned an unrecognized response.\n'
    unresolved=$((unresolved + 1))
  fi
fi

process_output="$(adb_shell ps -A -o "$process_fields" 2>&1)"
process_status=$?
process_output="${process_output//$'\r'/}"
if [[ "$process_status" -ne 0 ]]; then
  printf 'UNKNOWN: this Android build does not expose the expected ps output format.\n'
  unresolved=$((unresolved + 1))
else
  process_format_valid="$(printf '%s\n' "$process_output" | awk -v label="$expected_label" '
    BEGIN { offset = (label != "") }
    NR == 1 {
      if (NF != 3 + offset || (offset && $1 != "LABEL") ||
          $(1 + offset) != "UID" || $(2 + offset) != "PID" ||
          $(3 + offset) != "NAME") invalid = 1
      next
    }
    NF != 3 + offset || $(1 + offset) !~ /^[0-9]+$/ ||
      $(2 + offset) !~ /^[0-9]+$/ { invalid = 1 }
    END { if (NR == 0 || invalid) print "invalid"; else print "valid" }
  ')"
  if [[ "$process_format_valid" != 'valid' ]]; then
    printf 'UNKNOWN: ps output did not have the expected %s header and numeric UID/PID columns.\n' "$process_fields"
    unresolved=$((unresolved + 1))
  else
    process_rows="$(printf '%s\n' "$process_output" | awk -v name="$SERVER_PROCESS" -v label="$expected_label" '
      BEGIN { offset = (label != "") }
      NR > 1 && $(3 + offset) == name {
        if (offset) print $2, $3, $1; else print $1, $2
      }
    ')"
    if [[ -z "$process_rows" ]]; then
      printf 'FAIL: process %s is not running.\n' "$SERVER_PROCESS"
      failures=$((failures + 1))
    else
      process_count="$(printf '%s\n' "$process_rows" | awk 'NF >= 2 { count++ } END { print count + 0 }')"
      actual_rows="$(printf '%s\n' "$process_rows" | tr '\n' ';' | sed 's/;$//')"
      wrong_uid_rows="$(printf '%s\n' "$process_rows" | awk -v uid="$expected_uid" '$1 != uid { print }')"
      if [[ "$process_count" -ne 1 ]]; then
        printf 'FAIL: expected exactly one %s process, found %s (UID PID [LABEL] rows: %s).\n' \
          "$SERVER_PROCESS" "$process_count" "$actual_rows"
        failures=$((failures + 1))
      elif [[ -n "$wrong_uid_rows" ]]; then
        printf 'FAIL: %s is running with an unexpected UID for %s mode (expected UID %s; observed %s).\n' \
          "$SERVER_PROCESS" "$1" "$expected_uid" "$actual_rows"
        failures=$((failures + 1))
      else
        read -r actual_uid process_pid actual_label <<<"$process_rows"
        printf 'PASS: exactly one %s process is running with UID %s (PID %s), as expected for %s mode.\n' \
          "$SERVER_PROCESS" "$actual_uid" "$process_pid" "$1"
        if [[ -n "$expected_label" ]]; then
          if [[ "$actual_label" == "$expected_label" ]]; then
            printf 'PASS: %s has SELinux domain %s.\n' "$SERVER_PROCESS" "$expected_label"
          elif [[ "$actual_label" == '?' || "$actual_label" == '-' ]]; then
            printf 'UNKNOWN: ps could not read the Shizuku SELinux domain.\n'
            unresolved=$((unresolved + 1))
          else
            printf 'FAIL: %s has SELinux domain %s; expected %s.\n' \
              "$SERVER_PROCESS" "$actual_label" "$expected_label"
            failures=$((failures + 1))
          fi
        fi
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
  --extra modos_read_only_probe:s:1 2>&1)"
binder_status=$?
binder_value="${binder_output//$'\r'/}"
# The platform command can print parser/provider exceptions and still exit 0.
# Accept only its complete, unpacked success response with the provider's
# actual Binder key; empty, still-parcelled, or unrelated bundles prove nothing.
binder_success_pattern='^Result: Bundle\[\{moe\.shizuku\.privileged\.api\.intent\.extra\.BINDER=[^[:space:]{}]+\}\]$'
if [[ "$binder_status" -ne 0 ]]; then
  printf 'UNKNOWN: the manager Binder provider could not be queried; Android may not support this read-only content call.\n'
  unresolved=$((unresolved + 1))
elif [[ "$binder_value" == *Error* || "$binder_value" == *ERROR* || "$binder_value" == *Exception* ]]; then
  printf 'UNKNOWN: the manager Binder provider query reported an error.\n'
  unresolved=$((unresolved + 1))
elif [[ "$binder_value" == 'Result: null' || "$binder_value" == 'null' ]]; then
  printf 'FAIL: the manager provider returned no live Shizuku Binder.\n'
  failures=$((failures + 1))
elif [[ "$binder_value" =~ $binder_success_pattern && "$binder_value" != *'BINDER=null}'* ]]; then
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
wireless_value="${wireless_output//$'\r'/}"
if [[ "$wireless_status" -ne 0 ]]; then
  printf 'UNKNOWN: could not read global setting adb_wifi_enabled.\n'
  unresolved=$((unresolved + 1))
elif [[ "$wireless_value" == '0' ]]; then
  printf 'PASS: wireless debugging setting adb_wifi_enabled is 0 (off).\n'
elif [[ "$wireless_value" == '1' ]]; then
  printf 'FAIL: wireless debugging setting adb_wifi_enabled is 1 (on).\n'
  failures=$((failures + 1))
else
  printf 'UNKNOWN: adb_wifi_enabled is unavailable or has an unrecognized value (%s).\n' \
    "${wireless_value:-empty}"
  unresolved=$((unresolved + 1))
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
