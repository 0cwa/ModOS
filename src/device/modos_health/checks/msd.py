# SPDX-License-Identifier: AGPL-3.0-or-later
"""Observe MSD presence and the gadget HAL label without changing USB state."""
import re

from ..common import item, manual

ID = "msd"

# The signed MSD v2.6 policy patcher targets this domain, and substitutes su
# when it is absent. An observed vendor domain is evidence to review, not
# authorization to substitute it in policy or proof that MSD works.
_POLICY_TARGET = "hal_usb_gadget_impl"
_SERVICE = "getprop init.svc.vendor.usbgadget-hal"
_PROCESSES = "ps -A -o LABEL,ARGS"
_SERVICE_NAMES = frozenset({
    "android.hardware.usb.gadget-service",
    "android.hardware.usb.gadget-service.qti",
    "android.hardware.usb.gadget-service.pixel",
    *(
        f"android.hardware.usb.gadget@{version}-service{suffix}"
        for version in ("1.0", "1.1", "1.2")
        for suffix in ("", "-qti")
    ),
})
_SERVICE_DIRECTORIES = ("/vendor/bin/hw/", "/system/vendor/bin/hw/", "/odm/bin/hw/")
_LABEL = re.compile(r"u:r:([a-zA-Z][a-zA-Z0-9_]{0,127}):s0(?::c[0-9]+(?:[.,]c[0-9]+)*)?")


def _service_name(executable):
    for directory in _SERVICE_DIRECTORIES:
        if executable.startswith(directory):
            executable = executable[len(directory):]
            break
    return executable if executable in _SERVICE_NAMES else None


def _gadget_service(ctx):
    result = ctx.probe(_SERVICE)
    if result.returncode == 0 and not result.truncated:
        state = result.output.strip()
        if state == "running":
            return item(ID, "usb-gadget-service", "PASS",
                        "Init reports vendor.usbgadget-hal running; this does not establish MSD behavior")
        if state in ("stopped", "stopping", "restarting"):
            return item(ID, "usb-gadget-service", "WARNING",
                        f"Init reports vendor.usbgadget-hal {state}; inspect the gadget service before testing MSD")
    return item(ID, "usb-gadget-service", "WARNING",
                "Init state for vendor.usbgadget-hal is unavailable or unrecognized")


def _gadget_domain(ctx):
    result = ctx.probe(_PROCESSES)
    if result.returncode != 0:
        return item(ID, "usb-gadget-domain", "WARNING",
                    "USB gadget HAL process labels are unavailable")

    observations = []
    unlabelled = set()
    # Never retain complete command lines or unrelated process identities in
    # the report. Only fixed service names and validated domain symbols escape
    # this parser. A cut-off final row cannot establish any observation.
    for line in result.output.splitlines(keepends=True):
        if not line.endswith("\n"):
            continue
        fields = line.split(None, 2)
        if len(fields) < 2:
            continue
        name = _service_name(fields[1])
        if name is None:
            continue
        match = _LABEL.fullmatch(fields[0])
        if match is None:
            unlabelled.add(name)
        else:
            observations.append((name, match.group(1)))

    summary = "; ".join(f"{name}={domain}" for name, domain in sorted(set(observations))[:4])
    if any(domain == "su" for _, domain in observations):
        return item(ID, "usb-gadget-domain", "FAIL",
                    f"Observed gadget HAL in the su debug domain ({summary}); this is not an acceptable physical-device policy target")
    if result.truncated or (result.output and not result.output.endswith("\n")):
        return item(ID, "usb-gadget-domain", "WARNING",
                    "Gadget HAL process observation is truncated; cannot establish a unique domain"
                    + (f". Complete matching rows: {summary}" if summary else ""))
    if unlabelled:
        return item(ID, "usb-gadget-domain", "WARNING",
                    "SELinux process label unavailable or malformed for " + ", ".join(sorted(unlabelled)))
    if not observations:
        return item(ID, "usb-gadget-domain", "WARNING",
                    "No recognized USB gadget HAL process observed; absence does not establish compatibility")
    if len(observations) != 1:
        return item(ID, "usb-gadget-domain", "WARNING",
                    f"Multiple gadget HAL processes observed ({len(observations)}): {summary}; the active policy target is ambiguous")
    if observations[0][1] != _POLICY_TARGET:
        return item(ID, "usb-gadget-domain", "WARNING",
                    f"Observed {summary}; differs from MSD v2.6 target {_POLICY_TARGET}. A replacement domain still requires policy and device qualification")
    return item(ID, "usb-gadget-domain", "PASS",
                f"Observed {summary}, matching MSD v2.6's policy target; this is a process-label observation only")


def check(ctx):
    return [ctx.file(ID, "binary", "/system/bin/msd-tool"),
            _gadget_service(ctx), _gadget_domain(ctx),
            manual(ID, "daemon", "Test MSD export/unexport, gadget HAL pause/resume, USB recovery and SELinux AVCs on the device; file, service and label observations do not prove daemon functionality")]
