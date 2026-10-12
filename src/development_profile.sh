#!/usr/bin/env bash
# Explicit source-build experiments use the common OTA pipeline and keep the
# production helper/executable pins unchanged.
validate_development_profile() {
  local scope="${1:-${OUTPUT_SCOPE:-}}"
  case "${PIXENEOS_DEVELOPMENT_PROFILE:-}" in
    ''|none) return 0 ;;
    care-map-pdx235)
      if [[ "${ROM_FAMILY:-}" == lineageos && "${DEVICE_NAME:-}" == pdx235 &&
        "${scope}" == local-unpublished ]]; then
        return 0
      fi
      echo 'Error: care-map-pdx235 development requires LineageOS/pdx235 local-unpublished scope.' >&2
      ;;
    *) echo 'Error: unknown development profile.' >&2 ;;
  esac
  return 1
}

activate_development_profile() {
  validate_development_profile || return 1
  case "${PIXENEOS_DEVELOPMENT_PROFILE:-}" in ''|none) return 0 ;; esac
  local repository_root manifest revision
  repository_root="$(git rev-parse --show-toplevel)" || return 1
  manifest="${repository_root}/tools/compat/avbroot_setup_care_map_pdx235.json"
  # No manifest is trusted until reviewed metadata is committed. There is no
  # caller revision/manifest override or fallback to the production contract.
  _locked_input_digest "${manifest}" >/dev/null || {
    echo 'Error: checked-in care-map development helper metadata is unavailable.' >&2
    return 1
  }
  revision="$(python3 -I -c '
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("compat", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
manifest = module.load_manifest(Path(sys.argv[2]))
if manifest["repository"] != "https://github.com/0cwa/my-avbroot-setup":
    raise ValueError("unexpected development helper repository")
print(manifest["revision"])
' "${repository_root}/tools/compat/avbroot_setup_compat.py" "${manifest}")" || return 1
  VERSION[AVBROOT_SETUP]="${revision}"
  PIXENEOS_AVBROOT_SETUP_SOURCE='https://github.com/0cwa/my-avbroot-setup'
  PIXENEOS_DEVELOPMENT_COMPAT_MANIFEST="${manifest}"
}
