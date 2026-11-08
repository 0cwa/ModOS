#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ModOS contributors
"""Stream helper output and enforce ModOS's diagnostic publication guard.

This is a log-contract check, not an OTA verifier. The helper's cryptographic
checks and exit status remain authoritative. Keep this consumer-side guard
separate from the pinned helper and signed module archives.

For a historical log, instantiate BuildDiagnostics with that run's exact OTA
paths, pass its lines to observe_line(), then call finish(child_exit_code).
GitHub's timestamp prefixes and ANSI log colors are accepted for replay.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import BinaryIO, Sequence


SCHEMA = 'modos-build-diagnostics-v1'
MAX_OBSERVATIONS = 128
MAX_MESSAGE_CHARACTERS = 1024
MAX_LINE_BYTES = 16384
ANSI_SGR = re.compile(r'\x1b\[[0-9;:]*m')
GITHUB_TIMESTAMP = re.compile(r'^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ')
VERIFY_START = re.compile(r'^\[INFO\] Verifying OTA: (.*)$')
VERIFY_END = re.compile(r'^\s*\d+(?:\.\d+)?s\s+INFO\s+OK!\s*$')
WARNING = re.compile(
    r'^\s*(?:\[(?:WARN|WARNING)\]|(?:\d+(?:\.\d+)?s\s+)?(?:WARN|WARNING):?'
    r'|warning(?:\[[^\]\r\n]{1,80}\])?:)\s+(.+)$'
)
# MSD's Rust policy tool emits these diagnostics without a WARN prefix.
SEPOLICY_WARNING_HEADER = re.compile(r'^Warnings when (loading|saving) sepolicy:$')
SEPOLICY_COMPATIBILITY_FALLBACK = re.compile(
    r'^Type not found: [A-Za-z_][A-Za-z0-9_]*; assuming old version of Android$'
)
AVB_WARNING = re.compile(r'\b(?:is signed with public AOSP test key|has insecure flags:)')
DIRECT_CIL_FALLBACK = 'No precompiled sepolicy found, patching CIL files directly'
MSD_FALLBACK = (
    'Type not found: hal_usb_gadget_impl; '
    'allowing fake HAL running in su context for debugging'
)
MSD_ACTION = (
    'MSD selected its emulator/debug su-domain fallback. Identify the device\'s '
    'actual USB gadget HAL domain and validate a reviewed policy fix before '
    'publishing an MSD-enabled build.'
)


def normalized_path(value: str | Path) -> str:
    """Compare paths lexically, without trusting or requiring files to exist."""
    value = os.fspath(value)
    if not value or len(value) > 4096 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError('OTA/report paths must be nonempty and contain no control characters')
    return os.path.abspath(value)


class BuildDiagnostics:
    def __init__(self, source_ota: str | Path, output_otas: Sequence[str | Path],
                 msd_selected: bool = False):
        if not output_otas:
            raise ValueError('at least one output OTA is required')
        self.coverage = []
        self._expected = {}
        for phase, path in [('source', source_ota), *(('output', p) for p in output_otas)]:
            key = normalized_path(path)
            if key in self._expected:
                raise ValueError('source and output OTA paths must be distinct')
            entry = {'phase': phase, 'ota': os.fspath(path), 'started': 0, 'completed': 0}
            self.coverage.append(entry)
            self._expected[key] = entry
        self.observations = []
        self.observation_counts = {'warning': 0, 'error': 0}
        self.warning_counts = {'source': 0, 'output': 0, 'unclassified': 0}
        self.msd_fallback_count = 0
        self.msd_policy_coverage = {
            'selected': msd_selected, 'injections': 0, 'compiled_policy_patches': 0,
            'covered_injections': 0, 'direct_cil_fallbacks': 0,
        }
        self._msd_patch_events = None
        self._msd_injection_failed = False
        self._msd_coverage_error = False
        self._active = None
        self._sepolicy_warning_operation = None
        self._line = 0
        self._buffer = bytearray()
        self._overflow = False
        self._coverage_error = False
        self._finished = None

    def _record(self, severity: str, code: str, message: str, *, entry=None) -> None:
        self.observation_counts[severity] += 1
        if len(self.observations) >= MAX_OBSERVATIONS:
            return
        message = re.sub(r'[\x00-\x1f\x7f]', ' ', message)
        self.observations.append({
            'severity': severity, 'code': code,
            'phase': entry['phase'] if entry else 'unclassified',
            'ota': entry['ota'] if entry else None,
            'line': self._line,
            'message': message[:MAX_MESSAGE_CHARACTERS],
            'message_truncated': len(message) > MAX_MESSAGE_CHARACTERS,
        })

    def _observe_warning(self, message: str, code: str = 'helper_warning') -> None:
        phase = self._active['phase'] if self._active else 'unclassified'
        self.warning_counts[phase] += 1
        avb = AVB_WARNING.search(message) is not None
        severity = 'error' if avb and phase != 'source' else 'warning'
        if avb:
            code = 'source_avb_warning' if phase == 'source' else 'unsafe_avb_warning'
        self._record(severity, code, message, entry=self._active)

    def _msd_policy_missing(self, message: str) -> None:
        self._msd_coverage_error = True
        self._record('error', 'msd_policy_coverage_missing', message)

    def _close_msd_injection(self) -> None:
        if self._msd_patch_events is not None:
            if not self._msd_patch_events and not self._msd_injection_failed:
                self._msd_policy_missing(
                    'MSD injection had no compiled SELinux policy patch event. '
                    'Empty-policy and direct-CIL injection paths are not qualified for publication.')
            elif self._msd_patch_events and not self._msd_injection_failed:
                self.msd_policy_coverage['covered_injections'] += 1
        self._msd_patch_events = None
        self._msd_injection_failed = False

    def observe_line(self, raw: str | bytes) -> None:
        """Observe one line; neither its text nor an OK marker authenticates an OTA."""
        if self._finished is not None:
            raise RuntimeError('diagnostics are already finished')
        self._line += 1
        if len(raw) > MAX_LINE_BYTES:
            self._sepolicy_warning_operation = None
            self._coverage_error = True
            self._record('error', 'oversized_log_line',
                         'A helper log line exceeded the diagnostic limit; coverage is incomplete.')
            return
        line = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        line = ANSI_SGR.sub('', GITHUB_TIMESTAMP.sub('', line.rstrip('\r\n')))
        policy_header = SEPOLICY_WARNING_HEADER.fullmatch(line)
        if policy_header:
            self._sepolicy_warning_operation = policy_header[1]
        elif not line.startswith('- '):
            # Only consecutive bullet lines belong to the policy-tool warning.
            # In particular, module/verification markers end the block before
            # their own handlers return. Do not retain unrelated log details.
            self._sepolicy_warning_operation = None
        if line.startswith('[INFO] Injecting '):
            # The helper's direct-CIL message is shared by MSD and Custota.
            # Attribute it only inside the current MSD injection scope.
            self._close_msd_injection()
            if line.startswith('[INFO] Injecting MSD:'):
                self.msd_policy_coverage['injections'] += 1
                self._msd_patch_events = 0
            return
        if line.startswith('[INFO] Adding MSD SELinux rules:'):
            self.msd_policy_coverage['compiled_policy_patches'] += 1
            if self._msd_patch_events is None:
                self._msd_policy_missing(
                    'MSD policy patch event appeared outside an MSD injection; '
                    'check the helper logging contract before publication.')
            else:
                self._msd_patch_events += 1
            return
        if self._msd_patch_events is not None and DIRECT_CIL_FALLBACK in line:
            self.msd_policy_coverage['direct_cil_fallbacks'] += 1
            if not self._msd_injection_failed:
                self._msd_policy_missing(
                    'MSD used direct CIL policy injection, which bypasses msd-tool\'s '
                    'compiled-policy HAL checks. This path requires separate device/policy '
                    'qualification before publication.')
            self._msd_injection_failed = True
            return
        marker = VERIFY_START.fullmatch(line)
        if marker is not None or line.startswith('[INFO] Verifying OTA:'):
            if self._active is not None:
                self._coverage_error = True
                self._record('error', 'interrupted_verification',
                             'A new OTA verification started before the previous log phase ended.',
                             entry=self._active)
            try:
                self._active = self._expected.get(normalized_path(marker[1])) if marker else None
            except ValueError:
                self._active = None
            if self._active is None:
                self._coverage_error = True
                self._record('error', 'unexpected_verification',
                             'The helper reported verification of an unrequested OTA path.')
            else:
                self._active['started'] += 1
            return
        if VERIFY_END.fullmatch(line):
            if self._active is not None:
                self._active['completed'] += 1
            # Other avbroot commands run between OTA verifications. Do not
            # attribute their diagnostics to the last completed OTA phase.
            self._active = None
            return
        if MSD_FALLBACK in line:
            self.msd_fallback_count += 1
            self._record('error', 'msd_debug_fallback', MSD_ACTION)
            return
        if policy_header:
            self._observe_warning(line, 'sepolicy_warning')
            return
        if self._sepolicy_warning_operation is not None:
            self._observe_warning(
                f'While {self._sepolicy_warning_operation} sepolicy: {line[2:]}', 'sepolicy_warning')
            return
        if SEPOLICY_COMPATIBILITY_FALLBACK.fullmatch(line):
            self._observe_warning(line, 'sepolicy_compatibility_fallback')
            return
        warning = WARNING.fullmatch(line)
        if warning:
            self._observe_warning(warning[1])

    def feed(self, chunk: bytes) -> None:
        """Incrementally parse a byte stream with a bounded partial-line buffer."""
        if self._finished is not None:
            raise RuntimeError('diagnostics are already finished')
        parts = chunk.split(b'\n')
        for index, part in enumerate(parts):
            if not self._overflow:
                if len(self._buffer) + len(part) > MAX_LINE_BYTES:
                    self._buffer.clear()
                    self._overflow = True
                else:
                    self._buffer.extend(part)
            if index < len(parts) - 1:
                self._flush_line()

    def _flush_line(self) -> None:
        if self._overflow:
            self.observe_line(b' ' * (MAX_LINE_BYTES + 1))
        else:
            self.observe_line(bytes(self._buffer))
        self._buffer.clear()
        self._overflow = False

    def finish(self, child_exit_code: int | None) -> dict:
        if self._finished is not None:
            return self._finished
        if self._buffer or self._overflow:
            self._flush_line()
        self._close_msd_injection()
        if self.msd_policy_coverage['selected'] and not self.msd_policy_coverage['injections']:
            self._msd_policy_missing(
                'MSD was selected but its injection marker was absent; '
                'the build did not establish MSD policy log coverage.')
        self.msd_policy_coverage['complete'] = not self._msd_coverage_error
        complete = not (self._coverage_error or self._msd_coverage_error)
        for entry in self.coverage:
            if not entry['completed'] or entry['completed'] != entry['started']:
                complete = False
                self._record('error', 'incomplete_verification_log',
                             'Expected OTA verification log markers are missing or incomplete; '
                             'check the helper logging contract. This is not a cryptographic verdict.',
                             entry=entry)
        if child_exit_code != 0:
            self._record('error', 'helper_failed',
                         f'Helper exit code: {child_exit_code!r}. A successful helper exit is required.')
        counts = self.observation_counts
        status = 'failed' if counts['error'] else 'passed_with_warnings' if counts['warning'] else 'passed'
        self._finished = {
            'schema': SCHEMA, 'status': status, 'child_exit_code': child_exit_code,
            'coverage_complete': complete, 'verification_log_coverage': self.coverage,
            'warning_counts': self.warning_counts, 'msd_fallback_count': self.msd_fallback_count,
            'msd_policy_coverage': self.msd_policy_coverage,
            'observations': self.observations, 'observation_counts': counts,
            'observations_omitted': sum(counts.values()) - len(self.observations),
            'runtime_validation': 'not_tested',
            'scope': 'Diagnostic log guard only; helper cryptographic verification remains required.',
        }
        return self._finished


def _markdown(value: object) -> str:
    return html.escape(str(value)).replace('|', '&#124;').replace('`', '&#96;')


def render_summary(report: dict) -> str:
    counts = report['warning_counts']
    lines = [
        '## OTA build diagnostics', '',
        f"**Diagnostic guard: {report['status'].replace('_', ' ').upper()}**", '',
        f"Helper exit code: `{report['child_exit_code']}`. "
        'This log report does not authenticate an OTA or prove device functionality.', '',
        '| Verification scope | Warning lines observed |', '| --- | ---: |',
        f"| Downloaded source OTA | {counts['source']} |",
        f"| Final output OTAs | {counts['output']} |",
        f"| Outside a recognized verification phase | {counts['unclassified']} |", '',
        'Counts cover recognized helper, avbroot, and Rust warning headers, '
        'SELinux policy warning headers/details, and older-Android type fallbacks.', '',
        'Source AVB warnings describe the unmodified input and remain visible; '
        'they grant no verification waiver.', '',
        '### Verification log coverage', '',
        '| OTA | Scope | Completed / started phases |', '| --- | --- | ---: |',
    ]
    for entry in report['verification_log_coverage']:
        lines.append(f"| `{_markdown(entry['ota'])}` | {entry['phase']} | "
                     f"{entry['completed']} / {entry['started']} |")
    lines += ['', 'A completed log phase is a diagnostic marker, not cryptographic evidence.', '']
    msd = report['msd_policy_coverage']
    lines += ['### MSD policy log coverage', '',
              f"Selected: {'yes' if msd['selected'] else 'no'}. "
              f"Observed injections: {msd['injections']}; "
              f"injections with compiled-policy coverage: {msd['covered_injections']}; "
              f"compiled-policy patch events: {msd['compiled_policy_patches']}.", '']
    if not msd['complete']:
        lines += ['**Publication blocked:** MSD policy log coverage is missing or uses an '
                  'unqualified direct-CIL path. A selected or observed MSD injection must '
                  'include compiled-policy patching; device/HAL qualification remains required.', '']
    if report['msd_fallback_count']:
        lines += [f'**Publication blocked:** {MSD_ACTION}', '']
    if report['observations']:
        lines += ['### Observations', '']
        for item in report['observations']:
            lines.append(f"- **{item['severity'].upper()} · {item['phase']}** "
                         f"`{item['code']}`: {_markdown(item['message'])}"
                         + (' (message truncated)' if item['message_truncated'] else ''))
        lines.append('')
    if report['observations_omitted']:
        lines += [f"{report['observations_omitted']} further observations omitted from this bounded report; "
                  'all observations still count toward the guard result.', '']
    lines += ['**Runtime MSD / USB gadget behavior: NOT TESTED.** '
              'A passing build does not establish that the policy targets the physical device\'s HAL.', '']
    return '\n'.join(lines)


def run_helper(command: Sequence[str], diagnostics: BuildDiagnostics, output: BinaryIO) -> int | None:
    environment = os.environ.copy()
    environment['PYTHONUNBUFFERED'] = '1'
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment)
    except OSError as error:
        diagnostics._record('error', 'helper_start_failed',
                            f'Could not start the helper ({type(error).__name__}).')
        return None
    stream_failed = False
    with child.stdout:
        while chunk := child.stdout.read1(65536):
            if not stream_failed:
                try:
                    output.write(chunk)
                    output.flush()
                except OSError:
                    # Keep draining the child even if the caller's log sink
                    # disappears. Never publish a run with incomplete streaming.
                    stream_failed = True
                    diagnostics._record('error', 'log_stream_failed',
                                        'Could not stream all helper output to the build log.')
            diagnostics.feed(chunk)
    return child.wait()


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-ota', required=True)
    parser.add_argument('--output-ota', action='append', required=True)
    parser.add_argument('--msd-selected', action='store_true',
                        help='Require MSD injection and compiled-policy patch log coverage')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--summary', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a helper command is required after --')
    try:
        diagnostics = BuildDiagnostics(args.source_ota, args.output_ota, msd_selected=args.msd_selected)
        destinations = [normalized_path(args.report)]
        if args.summary is not None:
            destinations.append(normalized_path(args.summary))
        if len(set(destinations)) != len(destinations) or set(destinations) & diagnostics._expected.keys():
            raise ValueError('diagnostic report paths must differ from each other and all OTAs')
    except ValueError as error:
        parser.error(str(error))
    child_status = run_helper(command, diagnostics, sys.stdout.buffer)
    report = diagnostics.finish(child_status)
    result = (128 - child_status if child_status < 0 else child_status) if child_status else 0
    if report['status'] == 'failed' and result == 0:
        result = 1
    try:
        write_file(args.report, json.dumps(report, indent=2, sort_keys=True) + '\n')
        if args.summary is not None:
            write_file(args.summary, render_summary(report))
    except OSError as error:
        print(f'Error: could not write build diagnostics ({type(error).__name__}).', file=sys.stderr)
        return result or 1
    print(f"Build diagnostic guard: {report['status'].replace('_', ' ')}. Report: {args.report}",
          file=sys.stderr)
    if report['msd_fallback_count']:
        print(f'Error: {MSD_ACTION}', file=sys.stderr)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
