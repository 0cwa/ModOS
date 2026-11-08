#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise publication diagnostics with real log shapes and harmless children."""
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'src/build_diagnostics.py'
spec = importlib.util.spec_from_file_location('build_diagnostics', SCRIPT)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)

SOURCE = '.tmp/lineage source.zip'
OUTPUTS = ['pdx235 rootless.zip', 'pdx235 magisk.zip']
SOURCE_WARNINGS = [
    ' 21.495s  WARN vbmeta is signed with public AOSP test key',
    ' 21.495s  WARN vbmeta has insecure flags: 0x3',
    ' 21.496s  WARN vbmeta_system is signed with public AOSP test key',
]


def start(path):
    return f'\x1b[1m[INFO] Verifying OTA: {path}\x1b[0m'


def complete(path, warnings=()):
    return [start(path), *warnings, ' 28.828s  INFO OK!']


def successful_log(source_warnings=()):
    return complete(SOURCE, source_warnings) + sum((complete(p) for p in OUTPUTS), [])


def replay(lines, child_status=0, *, msd_selected=False):
    parser = diagnostics.BuildDiagnostics(SOURCE, OUTPUTS, msd_selected=msd_selected)
    for line in lines:
        parser.observe_line(line)
    return parser.finish(child_status)


class LogContractTest(unittest.TestCase):
    def test_clean_source_and_both_outputs_have_complete_log_coverage(self):
        report = replay(successful_log())
        self.assertEqual(report['schema'], 'modos-build-diagnostics-v1')
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['coverage_complete'])
        self.assertEqual(report['child_exit_code'], 0)
        self.assertEqual(report['runtime_validation'], 'not_tested')
        self.assertEqual([(r['started'], r['completed']) for r in report['verification_log_coverage']],
                         [(1, 1), (1, 1), (1, 1)])

    def test_source_avb_warnings_remain_visible_and_distinct_from_outputs(self):
        report = replay(successful_log(SOURCE_WARNINGS))
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertEqual(report['warning_counts'], {'source': 3, 'output': 0, 'unclassified': 0})
        self.assertEqual([o['code'] for o in report['observations']], ['source_avb_warning'] * 3)
        summary = diagnostics.render_summary(report)
        self.assertIn('PASSED WITH WARNINGS', summary)
        self.assertIn('| Downloaded source OTA | 3 |', summary)
        self.assertIn('| Final output OTAs | 0 |', summary)
        self.assertIn('NOT TESTED', summary)
        self.assertIn('does not authenticate an OTA', summary)
        self.assertIn('not cryptographic evidence', summary)

    def test_each_output_rejects_test_keys_and_insecure_flags_even_if_child_succeeds(self):
        for output in OUTPUTS:
            for warning in SOURCE_WARNINGS:
                with self.subTest(output=output, warning=warning):
                    lines = complete(SOURCE)
                    for path in OUTPUTS:
                        lines += complete(path, [warning] if path == output else [])
                    report = replay(lines)
                    self.assertEqual(report['status'], 'failed')
                    self.assertTrue(report['coverage_complete'])
                    self.assertEqual(report['warning_counts']['output'], 1)
                    self.assertEqual(report['observations'][0]['code'], 'unsafe_avb_warning')
                    self.assertEqual(report['observations'][0]['ota'], output)

    def test_avb_warning_before_or_after_source_verification_is_unclassified_and_fatal(self):
        for lines in [SOURCE_WARNINGS[:1] + successful_log(),
                      complete(SOURCE) + SOURCE_WARNINGS[:1] + sum((complete(p) for p in OUTPUTS), [])]:
            with self.subTest(lines=lines):
                report = replay(lines)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['warning_counts'], {'source': 0, 'output': 0, 'unclassified': 1})
                self.assertEqual(report['observations'][0]['phase'], 'unclassified')

    def test_msd_fallback_with_github_timestamp_and_ansi_blocks_publication(self):
        lines = complete(SOURCE, SOURCE_WARNINGS)
        lines += ['2026-11-07T06:04:23.8277580Z \x1b[33m' + diagnostics.MSD_FALLBACK + '\x1b[0m']
        lines += sum((complete(p) for p in OUTPUTS), [])
        report = replay(lines)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['msd_fallback_count'], 1)
        self.assertIn('msd_debug_fallback', [o['code'] for o in report['observations']])
        summary = diagnostics.render_summary(report)
        self.assertIn('Publication blocked', summary)
        self.assertIn('actual USB gadget HAL domain', summary)
        self.assertIn('NOT TESTED', summary)

    def test_github_timestamps_and_byte_chunks_do_not_change_classification(self):
        lines = successful_log(SOURCE_WARNINGS)
        payload = ''.join('2026-11-07T06:02:20.2041875Z ' + line + '\r\n' for line in lines).encode()
        parser = diagnostics.BuildDiagnostics(SOURCE, OUTPUTS)
        # Exercise splits inside ANSI sequences, timestamps, paths, and CRLF.
        for byte in payload:
            parser.feed(bytes([byte]))
        report = parser.finish(0)
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertEqual(report['warning_counts']['source'], 3)
        self.assertTrue(report['coverage_complete'])

    def test_absolute_and_lexically_equivalent_paths_match_without_file_access(self):
        lines = complete(os.path.abspath(SOURCE), SOURCE_WARNINGS)
        for path in OUTPUTS:
            lines += complete('./unused/../' + path)
        report = replay(lines)
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertTrue(report['coverage_complete'])

    def test_source_and_duplicate_output_paths_are_rejected(self):
        for outputs in [[], [SOURCE], [OUTPUTS[0], './' + OUTPUTS[0]]]:
            with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                diagnostics.BuildDiagnostics(SOURCE, outputs)

    def test_missing_or_unfinished_output_markers_fail_closed(self):
        for lines in [complete(SOURCE), successful_log()[:-1], [],
                      complete(SOURCE) + complete(OUTPUTS[0]) + [start(OUTPUTS[0])]]:
            with self.subTest(lines=lines):
                report = replay(lines)
                self.assertEqual(report['status'], 'failed')
                self.assertFalse(report['coverage_complete'])
                self.assertIn('incomplete_verification_log', [o['code'] for o in report['observations']])

    def test_new_phase_does_not_hide_unfinished_previous_verification(self):
        report = replay([start(SOURCE)] + sum((complete(p) for p in OUTPUTS), []))
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['status'], 'failed')
        self.assertIn('interrupted_verification', [o['code'] for o in report['observations']])

    def test_unknown_or_malformed_marker_never_inherits_the_source_exception(self):
        for marker in [start(SOURCE + '.other'), '[INFO] Verifying OTA:',
                       '[INFO] Verifying OTA: ', '[INFO] Verifying OTA:changed-format.zip']:
            with self.subTest(marker=marker):
                report = replay([start(SOURCE), marker, SOURCE_WARNINGS[0]] + successful_log())
                self.assertEqual(report['status'], 'failed')
                self.assertFalse(report['coverage_complete'])
                self.assertEqual(report['warning_counts']['source'], 0)
                self.assertEqual(report['warning_counts']['unclassified'], 1)
                self.assertIn('unsafe_avb_warning', [o['code'] for o in report['observations']])

    def test_echoed_or_unrelated_ok_text_does_not_close_a_verification(self):
        lines = [start(SOURCE), 'echo " 0.000s INFO OK!"', 'something INFO OK!',
                 SOURCE_WARNINGS[0], ' 28.828s  INFO OK!']
        lines += sum((complete(p) for p in OUTPUTS), [])
        report = replay(lines)
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertEqual(report['warning_counts']['source'], 1)

    def test_generic_helper_warning_is_reported_without_claiming_avb_failure(self):
        report = replay(complete(SOURCE) + ['\x1b[1m[WARNING] Unrelated helper warning\x1b[0m']
                        + sum((complete(p) for p in OUTPUTS), []))
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertEqual(report['observations'][0]['code'], 'helper_warning')
        self.assertEqual(report['warning_counts']['unclassified'], 1)

    def test_lowercase_rust_warning_headers_are_visible(self):
        for warning in ['warning: unused variable: `item`',
                        'warning[dead_code]: function `old` is never used',
                        '\x1b[33mwarning\x1b[0m[E0001]: diagnostic detail']:
            with self.subTest(warning=warning):
                report = replay([warning] + successful_log())
                self.assertEqual(report['status'], 'passed_with_warnings')
                self.assertEqual(report['warning_counts']['unclassified'], 1)
                self.assertEqual(report['observations'][0]['code'], 'helper_warning')

    def test_selected_msd_requires_injection_even_with_all_ota_markers(self):
        report = replay(successful_log(), msd_selected=True)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['coverage_complete'])
        self.assertFalse(report['msd_policy_coverage']['complete'])
        self.assertIn('msd_policy_coverage_missing', [o['code'] for o in report['observations']])

    def test_msd_empty_policy_or_direct_cil_paths_are_blocked_even_when_not_selected(self):
        for selected in (False, True):
            for fallback in ([], ['[INFO] ' + diagnostics.DIRECT_CIL_FALLBACK]):
                with self.subTest(selected=selected, fallback=fallback):
                    lines = complete(SOURCE)
                    lines += ['[INFO] Injecting MSD: .tmp/modules/msd.zip', *fallback,
                              '[INFO] Injecting OEMUnlockOnBoot: .tmp/modules/oemunlockonboot.zip']
                    lines += sum((complete(p) for p in OUTPUTS), [])
                    report = replay(lines, msd_selected=selected)
                    self.assertEqual(report['status'], 'failed')
                    self.assertFalse(report['coverage_complete'])
                    self.assertEqual(report['msd_policy_coverage']['covered_injections'], 0)
                    self.assertEqual(report['msd_policy_coverage']['direct_cil_fallbacks'], len(fallback))
                    self.assertIn('msd_policy_coverage_missing', [o['code'] for o in report['observations']])

    def test_graphene_msd_log_shape_has_compiled_policy_coverage(self):
        lines = complete(SOURCE)
        lines += ['\x1b[1m[INFO] Injecting MSD: .tmp/modules/msd.zip\x1b[0m',
                  '[INFO] Adding MSD SELinux rules: /helper/vendor_boot/cpio_tree/sepolicy',
                  '[INFO] Adding MSD SELinux rules: /helper/vendor/fs_tree/etc/selinux/precompiled_sepolicy',
                  '[INFO] Injecting OEMUnlockOnBoot: .tmp/modules/oemunlockonboot.zip']
        lines += sum((complete(p) for p in OUTPUTS), [])
        for selected in (False, True):
            with self.subTest(selected=selected):
                report = replay(lines, msd_selected=selected)
                self.assertEqual(report['status'], 'passed')
                self.assertTrue(report['coverage_complete'])
                self.assertEqual(report['msd_policy_coverage'], {
                    'selected': selected, 'injections': 1, 'compiled_policy_patches': 2,
                    'covered_injections': 1, 'direct_cil_fallbacks': 0, 'complete': True,
                })

    def test_compiled_policy_marker_does_not_waive_direct_cil_or_another_empty_injection(self):
        for tail in [['[INFO] ' + diagnostics.DIRECT_CIL_FALLBACK],
                     ['[INFO] Injecting MSD: another.zip']]:
            with self.subTest(tail=tail):
                lines = ['[INFO] Injecting MSD: msd.zip',
                         '[INFO] Adding MSD SELinux rules: /vendor/precompiled_sepolicy', *tail]
                report = replay(lines + successful_log(), msd_selected=True)
                self.assertEqual(report['status'], 'failed')
                self.assertIn('msd_policy_coverage_missing', [o['code'] for o in report['observations']])

    def test_other_module_cil_fallback_is_not_attributed_to_msd(self):
        lines = ['[INFO] Injecting Custota: custota.zip',
                 '[INFO] ' + diagnostics.DIRECT_CIL_FALLBACK,
                 '[INFO] Injecting MSD: msd.zip',
                 '[INFO] Adding MSD SELinux rules: /vendor/precompiled_sepolicy',
                 '[INFO] Injecting AnotherModule: other.zip',
                 '[INFO] ' + diagnostics.DIRECT_CIL_FALLBACK]
        report = replay(lines + successful_log(), msd_selected=True)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['msd_policy_coverage']['direct_cil_fallbacks'], 0)

    def test_msd_policy_marker_without_injection_is_not_sufficient(self):
        report = replay(['[INFO] Adding MSD SELinux rules: /vendor/precompiled_sepolicy']
                        + successful_log(), msd_selected=True)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['msd_policy_coverage']['complete'])

    def test_bounded_records_do_not_hide_late_security_errors(self):
        lines = complete(SOURCE, [' 1.000s WARN warning ' + 'x' * 1500] * (diagnostics.MAX_OBSERVATIONS + 5))
        lines += [diagnostics.MSD_FALLBACK] + sum((complete(p) for p in OUTPUTS), [])
        report = replay(lines)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(report['observations']), diagnostics.MAX_OBSERVATIONS)
        self.assertEqual(report['observations_omitted'], 6)
        self.assertEqual(report['observation_counts']['error'], 1)
        self.assertEqual(report['msd_fallback_count'], 1)
        for observation in report['observations']:
            self.assertTrue(observation['message_truncated'])
            self.assertLessEqual(len(observation['message']), diagnostics.MAX_MESSAGE_CHARACTERS)
        self.assertIn('Publication blocked', diagnostics.render_summary(report))

    def test_oversized_line_fails_coverage_without_unbounded_buffer_or_losing_later_phases(self):
        parser = diagnostics.BuildDiagnostics(SOURCE, OUTPUTS)
        parser.feed(b'x' * (diagnostics.MAX_LINE_BYTES + 1))
        self.assertLessEqual(len(parser._buffer), diagnostics.MAX_LINE_BYTES)
        parser.feed(b'\n' + '\n'.join(successful_log()).encode())
        report = parser.finish(0)
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual([r['completed'] for r in report['verification_log_coverage']], [1, 1, 1])
        self.assertIn('oversized_log_line', [o['code'] for o in report['observations']])

    def test_nonzero_helper_exit_is_fatal_even_after_complete_verification_logs(self):
        report = replay(successful_log(), 37)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['child_exit_code'], 37)
        self.assertTrue(report['coverage_complete'])

    def test_summary_escapes_log_markup(self):
        report = replay(successful_log([' 1.000s WARN <img src=x> | `quoted`']))
        summary = diagnostics.render_summary(report)
        self.assertNotIn('<img', summary)
        self.assertIn('&lt;img', summary)
        self.assertIn('&#124;', summary)


class SelinuxDiagnosticsTest(unittest.TestCase):
    # Header and fallback spellings are from the pinned MSD v2.5 sepatch.rs.
    # Detail text is synthetic: these tests do not exercise a real policy.
    def test_policy_load_and_save_warning_blocks_are_not_reported_as_clean(self):
        for operation in ('loading', 'saving'):
            with self.subTest(operation=operation):
                lines = [f'Warnings when {operation} sepolicy:',
                         '- first policy warning', '- second policy warning']
                report = replay(lines + successful_log())
                self.assertEqual(report['status'], 'passed_with_warnings')
                self.assertTrue(report['coverage_complete'])
                self.assertEqual(report['warning_counts']['unclassified'], 3)
                self.assertEqual([o['code'] for o in report['observations']], ['sepolicy_warning'] * 3)
                self.assertEqual([o['line'] for o in report['observations']], [1, 2, 3])
                self.assertIn(f'While {operation} sepolicy: first policy warning',
                              report['observations'][1]['message'])
                self.assertIn('PASSED WITH WARNINGS', diagnostics.render_summary(report))
                self.assertEqual(report['runtime_validation'], 'not_tested')

    def test_warning_header_alone_is_still_an_observed_warning(self):
        for operation in ('loading', 'saving'):
            with self.subTest(operation=operation):
                report = replay(successful_log() + [f'Warnings when {operation} sepolicy:'])
                self.assertEqual(report['status'], 'passed_with_warnings')
                self.assertEqual(report['warning_counts']['unclassified'], 1)

    def test_old_android_type_fallbacks_are_visible_but_not_claimed_as_policy_failures(self):
        for name in ('hal_usb_gadget_default', 'storage_config_prop',
                     'usb_control_prop', 'untrusted_app_userfaultfd'):
            with self.subTest(name=name):
                message = f'Type not found: {name}; assuming old version of Android'
                report = replay([message] + successful_log())
                self.assertEqual(report['status'], 'passed_with_warnings')
                self.assertEqual(report['warning_counts']['unclassified'], 1)
                self.assertEqual(report['observations'][0]['code'], 'sepolicy_compatibility_fallback')
                self.assertEqual(report['observations'][0]['message'], message)
                self.assertEqual(report['msd_fallback_count'], 0)

    def test_only_contiguous_details_belong_to_a_policy_warning_block(self):
        for boundary in ('', 'ordinary output', '[INFO] Injecting OtherModule: other.zip'):
            with self.subTest(boundary=boundary):
                lines = ['Warnings when loading sepolicy:', '- policy detail', boundary,
                         '- unrelated-private-detail'] + successful_log()
                report = replay(lines)
                self.assertEqual(report['warning_counts']['unclassified'], 2)
                self.assertNotIn('unrelated-private-detail', json.dumps(report))
        report = replay(['- ordinary-private-bullet', 'echo "Warnings when loading sepolicy:"',
                         '- another-private-bullet'] + successful_log())
        self.assertEqual(report['status'], 'passed')
        self.assertNotIn('private-bullet', json.dumps(report))

    def test_verification_markers_end_policy_warning_scope(self):
        lines = complete(SOURCE) + ['Warnings when loading sepolicy:', '- policy detail']
        lines += [start(OUTPUTS[0]), '- unrelated output detail', ' 1.000s INFO OK!']
        lines += complete(OUTPUTS[1])
        report = replay(lines)
        self.assertTrue(report['coverage_complete'])
        self.assertEqual(report['warning_counts'], {'source': 0, 'output': 0, 'unclassified': 2})
        self.assertNotIn('unrelated output detail', json.dumps(report))

    def test_consecutive_warning_blocks_keep_their_operation_and_chunk_boundaries(self):
        lines = successful_log() + ['Warnings when loading sepolicy:', '- load detail',
                                    'Warnings when saving sepolicy:', '- save detail']
        payload = '\r\n'.join('2026-11-08T08:00:00.0000000Z \x1b[33m' + line + '\x1b[0m'
                              for line in lines).encode()
        parser = diagnostics.BuildDiagnostics(SOURCE, OUTPUTS)
        for byte in payload:
            parser.feed(bytes([byte]))
        report = parser.finish(0)  # Final warning has no newline.
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertEqual(report['warning_counts']['unclassified'], 4)
        self.assertIn('While loading sepolicy: load detail', report['observations'][1]['message'])
        self.assertIn('While saving sepolicy: save detail', report['observations'][3]['message'])

    def test_policy_warning_scope_never_waives_security_errors(self):
        for prefix in ('', '- '):
            with self.subTest(prefix=prefix):
                lines = ['Warnings when loading sepolicy:', prefix + diagnostics.MSD_FALLBACK]
                report = replay(lines + successful_log())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['msd_fallback_count'], 1)
                self.assertEqual(report['observation_counts'], {'warning': 1, 'error': 1})
        for path in OUTPUTS:
            lines = complete(SOURCE)
            for output in OUTPUTS:
                warnings = ['Warnings when saving sepolicy:',
                            '- vbmeta has insecure flags: 0x3'] if output == path else []
                lines += complete(output, warnings)
            report = replay(lines)
            self.assertEqual(report['status'], 'failed')
            self.assertIn('unsafe_avb_warning', [o['code'] for o in report['observations']])

    def test_many_policy_details_are_bounded_without_hiding_late_fallback(self):
        lines = ['Warnings when saving sepolicy:']
        lines += ['- detail ' + 'x' * 1500] * (diagnostics.MAX_OBSERVATIONS + 3)
        lines += [diagnostics.MSD_FALLBACK] + successful_log()
        report = replay(lines)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(report['observations']), diagnostics.MAX_OBSERVATIONS)
        self.assertEqual(report['observation_counts']['warning'], diagnostics.MAX_OBSERVATIONS + 4)
        self.assertEqual(report['observations_omitted'], 5)
        self.assertEqual(report['msd_fallback_count'], 1)
        self.assertTrue(report['observations'][1]['message_truncated'])

    def test_oversized_line_cannot_extend_policy_warning_scope(self):
        lines = ['Warnings when saving sepolicy:', 'x' * (diagnostics.MAX_LINE_BYTES + 1),
                 '- unrelated-private-detail'] + successful_log()
        report = replay(lines)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['warning_counts']['unclassified'], 1)
        self.assertNotIn('unrelated-private-detail', json.dumps(report))

    def test_policy_warning_details_keep_existing_markup_and_control_sanitization(self):
        report = replay(['Warnings when loading sepolicy:', '- <img src=x> | `name`\x00']
                        + successful_log())
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertNotIn('\x00', report['observations'][1]['message'])
        summary = diagnostics.render_summary(report)
        self.assertNotIn('<img', summary)
        self.assertIn('&lt;img', summary)
        self.assertIn('&#124;', summary)
        self.assertIn('&#96;', summary)


class CommandWrapperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.report = self.directory / 'reports' / 'diagnostics.json'
        self.summary = self.directory / 'reports' / 'diagnostics.md'

    def run_wrapper(self, code, *, extra=(), child_args=()):
        command = [sys.executable, str(SCRIPT), '--source-ota', SOURCE]
        for path in OUTPUTS:
            command += ['--output-ota', path]
        command += ['--report', str(self.report), '--summary', str(self.summary),
                    *extra, '--', sys.executable, '-c', code, *child_args]
        return subprocess.run(command, cwd=self.directory, capture_output=True, timeout=10)

    def test_combined_stdout_stderr_preserves_original_bytes_and_hides_command_arguments(self):
        payloads = [line.encode() + b'\n' for line in successful_log()]
        payloads.append(b'\xffunrelated bytes without a final newline')
        code = 'import os\n' + '\n'.join(
            f'os.write({1 if i % 2 == 0 else 2}, {payload!r})' for i, payload in enumerate(payloads))
        result = self.run_wrapper(code, child_args=['command-only-secret'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b''.join(payloads))
        report_text = self.report.read_text()
        self.assertNotIn('command-only-secret', report_text)
        self.assertNotIn('unrelated bytes', report_text)
        self.assertEqual(json.loads(report_text)['status'], 'passed')
        self.assertIn('NOT TESTED', self.summary.read_text())

    def test_selinux_warning_streams_are_preserved_and_retained_in_reports(self):
        lines = successful_log() + ['Warnings when loading sepolicy:', '- load detail',
                                    'Warnings when saving sepolicy:', '- save detail',
                                    'Type not found: usb_control_prop; assuming old version of Android']
        payloads = [line.encode() + b'\n' for line in lines]
        code = 'import os\n' + '\n'.join(
            f'os.write({1 if i % 2 else 2}, {payload!r})' for i, payload in enumerate(payloads))
        result = self.run_wrapper(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b''.join(payloads))
        report = json.loads(self.report.read_text())
        self.assertEqual(report['status'], 'passed_with_warnings')
        self.assertEqual(report['warning_counts']['unclassified'], 5)
        self.assertIn('While saving sepolicy: save detail', self.summary.read_text())
        self.assertIn('sepolicy_compatibility_fallback', self.summary.read_text())
        self.assertIn('NOT TESTED', self.summary.read_text())

    def test_debug_fallback_changes_a_zero_child_exit_into_failure_after_it_finishes(self):
        lines = successful_log(SOURCE_WARNINGS) + [diagnostics.MSD_FALLBACK]
        code = f'import os\nos.write(1, {chr(10).join(lines).encode()!r})\nopen("finished", "w").write("yes")'
        result = self.run_wrapper(code)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.directory / 'finished').read_text(), 'yes')
        report = json.loads(self.report.read_text())
        self.assertEqual(report['child_exit_code'], 0)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('actual USB gadget HAL domain', result.stderr.decode())

    def test_original_nonzero_exit_code_is_preserved_despite_diagnostic_errors(self):
        code = f'import sys\nprint({diagnostics.MSD_FALLBACK!r})\nsys.exit(37)'
        result = self.run_wrapper(code)
        self.assertEqual(result.returncode, 37)
        self.assertEqual(json.loads(self.report.read_text())['child_exit_code'], 37)

    @unittest.skipUnless(os.name == 'posix', 'POSIX signal exit status')
    def test_child_signal_uses_standard_shell_exit_status(self):
        result = self.run_wrapper('import os, signal\nos.kill(os.getpid(), signal.SIGTERM)')
        self.assertEqual(result.returncode, 128 + signal.SIGTERM)
        self.assertEqual(json.loads(self.report.read_text())['child_exit_code'], -signal.SIGTERM)

    def test_launch_failure_is_reported_without_exposing_command(self):
        parser = diagnostics.BuildDiagnostics(SOURCE, OUTPUTS)
        code = diagnostics.run_helper([str(self.directory / 'missing-secret-executable')], parser, io.BytesIO())
        report = parser.finish(code)
        self.assertIsNone(code)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('helper_start_failed', [o['code'] for o in report['observations']])
        self.assertNotIn('missing-secret-executable', json.dumps(report))

    def test_python_child_is_unbuffered(self):
        result = self.run_wrapper('import os\nprint(os.environ["PYTHONUNBUFFERED"])')
        self.assertEqual(result.stdout, b'1\n')
        self.assertEqual(result.returncode, 1)  # Missing verification markers.

    def test_msd_selected_cli_requires_compiled_policy_coverage(self):
        for injection in ([], ['[INFO] Injecting MSD: .tmp/modules/msd.zip',
                               '[INFO] Adding MSD SELinux rules: /vendor/precompiled_sepolicy']):
            with self.subTest(injection=injection):
                payload = '\n'.join(injection + successful_log())
                result = self.run_wrapper(f'print({payload!r})', extra=['--msd-selected'])
                self.assertEqual(result.returncode, 0 if injection else 1)
                report = json.loads(self.report.read_text())
                self.assertTrue(report['msd_policy_coverage']['selected'])
                self.assertEqual(report['msd_policy_coverage']['complete'], bool(injection))

    def test_report_path_cannot_overwrite_an_ota_or_the_summary(self):
        for option in [['--report', SOURCE], ['--report', str(self.summary)]]:
            with self.subTest(option=option):
                result = self.run_wrapper('open("should-not-run", "w").write("yes")', extra=option)
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.directory / 'should-not-run').exists())

    def test_report_write_failure_does_not_turn_child_failure_into_success(self):
        self.report.mkdir(parents=True)  # Cannot replace a directory with a report file.
        result = self.run_wrapper('import sys\nsys.exit(37)')
        self.assertEqual(result.returncode, 37)
        self.assertIn('could not write build diagnostics', result.stderr.decode())

    def test_broken_log_sink_is_drained_and_reported_as_failure(self):
        class BrokenSink:
            def write(self, data):
                raise BrokenPipeError

        parser = diagnostics.BuildDiagnostics(SOURCE, OUTPUTS)
        data = '\n'.join(successful_log()).encode() + b'\n'
        # More than a pipe buffer follows the markers, so waiting without
        # continuing to drain would hang this child.
        code = f'import os\nos.write(1, {data!r})\nos.write(1, b"ordinary log line\\n" * 12000)'
        status = diagnostics.run_helper([sys.executable, '-c', code], parser, BrokenSink())
        report = parser.finish(status)
        self.assertEqual(status, 0)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('log_stream_failed', [o['code'] for o in report['observations']])


if __name__ == '__main__':
    unittest.main()
