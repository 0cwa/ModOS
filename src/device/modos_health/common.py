# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared, bounded, read-only ADB probes and structured health results."""
from dataclasses import dataclass
import re
import subprocess

STATUSES = ("PASS", "FAIL", "WARNING", "NOT_TESTED")
_ALLOWED = re.compile(
    r"(?:getprop [a-z0-9._]+|getenforce|bootctl get-current-slot|"
    r"bootctl is-slot-marked-successful [01]|"
    r"pm path [a-z0-9._]+|pm list packages -d|"
    r"test -f /[a-zA-Z0-9._/-]+|"
    r"cat /product/etc/permissions/privapp-permissions-com\.google\.android\.gms\.xml|"
    r"cat /proc/sys/kernel/random/boot_id)"
)

@dataclass(frozen=True)
class Probe:
    returncode: int | None
    output: str = ""

def item(module, name, status, explanation):
    if status not in STATUSES:
        raise ValueError("unsupported health status")
    return {"module": module, "check": name, "status": status, "detail": explanation}

class Context:
    def __init__(self, adb, *, runner=subprocess.run):
        self.adb = list(adb)
        self.runner = runner
        self._cache = {}

    def probe(self, command, *, refresh=False):
        # Never pass a caller-selected command or execute shell scripts.
        if not _ALLOWED.fullmatch(command):
            raise ValueError("read-only command is not allowlisted")
        if refresh or command not in self._cache:
            try:
                result = self.runner(self.adb + ["shell", command],
                                     capture_output=True, text=True, timeout=20,
                                     check=False)
                self._cache[command] = Probe(result.returncode, result.stdout[:32768])
            except (OSError, subprocess.TimeoutExpired):
                self._cache[command] = Probe(None)
        return self._cache[command]

    def property(self, name):
        if not re.fullmatch(r"[a-z0-9._]+", name):
            raise ValueError("invalid Android property name")
        result = self.probe("getprop " + name)
        return result.output.strip() if result.returncode == 0 else ""

    def file(self, module, name, path):
        result = self.probe("test -f " + path)
        if result.returncode is None:
            status = "WARNING"
        elif result.returncode == 0:
            status = "PASS"
        elif result.returncode == 1:
            status = "FAIL"
        else:
            status = "WARNING"
        return item(module, name, status,
                    "Expected image file present" if status == "PASS" else
                    "Expected image file not confirmed (ADB access or file missing)")

    def package(self, module, name, package):
        result = self.probe("pm path " + package)
        if result.returncode is None or result.returncode != 0:
            return item(module, name, "WARNING", "Package manager query unavailable")
        lines = [line for line in result.output.splitlines()
                 if line.startswith("package:/")]
        if not lines:
            return item(module, name, "FAIL", "Expected installed package not found")
        disabled = self.probe("pm list packages -d")
        if disabled.returncode == 0 and (
                "package:" + package in disabled.output.splitlines()):
            return item(module, name, "FAIL", "Installed package is disabled")
        return item(module, name, "PASS", "Android package manager reports the package installed")

def manual(module, name, explanation):
    return item(module, name, "NOT_TESTED", explanation)
