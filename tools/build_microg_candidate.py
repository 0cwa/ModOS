#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build an unsigned microG Mapbox candidate from reviewed, immutable inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / 'locks/microg-mapbox-candidate.json'


class CandidateError(Exception):
    """The candidate cannot be prepared or verified."""


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise CandidateError(f'{args[0]} failed: {result.stderr.strip() or result.stdout.strip()}')
    return result.stdout.strip()


def git(source: Path, *args: str, timestamp: str | None = None) -> str:
    env = os.environ.copy()
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    if timestamp:
        env.update(GIT_AUTHOR_DATE=timestamp, GIT_COMMITTER_DATE=timestamp)
    return run('git', '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=/dev/null',
               '-c', 'commit.gpgSign=false', '-c', 'user.name=ChatGPT',
               '-c', 'user.email=chatgpt@openai.com', '-C', str(source), *args, env=env)


def checked_patch(lock: dict, root: Path = ROOT) -> Path:
    patch = root / lock['patch']
    if digest(patch) != lock['patch_sha256']:
        raise CandidateError('Upstream patch SHA-256 does not match the candidate lock')
    if not patch.read_bytes().startswith(f"From {lock['upstream_fix_commit']} ".encode()):
        raise CandidateError('Patch does not identify the locked upstream fix commit')
    return patch


def prepare_source(lock: dict, patch: Path, destination: Path,
                   source_repository: Path | None = None) -> dict:
    """Clone into a new directory; never edit or clean the caller's source tree."""
    repository = str(source_repository) if source_repository else lock['repository']
    git(destination.parent, 'clone', '--quiet', '--no-checkout', '--no-hardlinks',
        '--depth=1', '--single-branch', '--branch', lock['release'],
        '--', repository, str(destination))
    actual = git(destination, 'rev-parse', f"refs/tags/{lock['release']}^{{commit}}")
    if actual != lock['source_commit']:
        raise CandidateError('Release tag does not match the locked source commit')
    git(destination, 'checkout', '--quiet', '--detach', lock['source_commit'])
    for relative, expected in lock['gradle']['wrapper_files'].items():
        if digest(destination / relative) != expected:
            raise CandidateError(f'Unreviewed Gradle wrapper file: {relative}')

    # Preserve upstream authorship. A fixed committer identity/date makes the
    # backport commit (and microG's generated version name) repeatable.
    git(destination, 'am', '--quiet', '--committer-date-is-author-date', str(patch))
    backport_commit = git(destination, 'rev-parse', 'HEAD')
    backport_tree = git(destination, 'rev-parse', 'HEAD^{tree}')

    wrapper = destination / 'gradle/wrapper/gradle-wrapper.properties'
    properties = wrapper.read_text()
    expected_url = lock['gradle']['distribution_url'].replace(':', '\\:')
    if f'distributionUrl={expected_url}\n' not in properties:
        raise CandidateError('Gradle distribution URL differs from the candidate lock')
    if 'distributionSha256Sum=' in properties:
        raise CandidateError('Review the existing Gradle checksum before changing this recipe')
    wrapper.write_text(properties + '\ndistributionSha256Sum=' +
                       lock['gradle']['distribution_sha256'] + '\n')
    git(destination, 'add', 'gradle/wrapper/gradle-wrapper.properties')
    git(destination, 'commit', '--quiet', '-m',
        'ChatGPT: pin Gradle distribution checksum for ModOS candidate',
        timestamp=lock['recipe_timestamp'])
    return {
        'source_repository': lock['repository'],
        'source_release': lock['release'],
        'source_commit': lock['source_commit'],
        'upstream_fix_commit': lock['upstream_fix_commit'],
        'patch_sha256': lock['patch_sha256'],
        'backport_commit': backport_commit,
        'backport_tree': backport_tree,
        'prepared_commit': git(destination, 'rev-parse', 'HEAD'),
        'prepared_tree': git(destination, 'rev-parse', 'HEAD^{tree}'),
        'gradle_distribution_sha256': lock['gradle']['distribution_sha256'],
        'production_eligible': False,
    }


def check_toolchain(lock: dict) -> tuple[Path, Path, str]:
    java_home = os.environ.get('JAVA_HOME')
    java_binary = str(Path(java_home) / 'bin/java') if java_home else 'java'
    java = subprocess.run([java_binary, '-version'], text=True, capture_output=True)
    version = java.stderr or java.stdout
    match = re.search(r'version "(\d+)', version)
    if java.returncode or not match or int(match[1]) != lock['java_major']:
        raise CandidateError(f"This candidate requires JDK {lock['java_major']}")
    sdk_value = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not sdk_value:
        raise CandidateError('Set ANDROID_HOME to the Android SDK before building')
    sdk = Path(sdk_value).resolve()
    android_jar = sdk / f"platforms/android-{lock['android_platform']}/android.jar"
    build_tools = sdk / 'build-tools' / lock['android_build_tools']
    if not android_jar.is_file() or any(not (build_tools / tool).is_file()
                                      for tool in ('aapt', 'apksigner')):
        raise CandidateError(f"Install platform {lock['android_platform']} and "
                             f"build-tools {lock['android_build_tools']} in ANDROID_HOME")
    return sdk, build_tools, version.splitlines()[0]


def inspect_apk(apk: Path, lock: dict, build_tools: Path) -> dict:
    badging = run(str(build_tools / 'aapt'), 'dump', 'badging', str(apk))
    identity = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'",
                         badging, re.M)
    if not identity or (identity[1], identity[2]) != (lock['apk_package'], str(lock['apk_version_code'])):
        raise CandidateError('Built APK package or versionCode does not match the candidate')
    if re.search(r'^application-debuggable(?:\s|$)', badging, re.M):
        raise CandidateError('Expected a release candidate, but the APK is debuggable')
    # The build has no signing inputs. Refuse a signed output, including one
    # accidentally injected through an upstream release signing configuration.
    verified = subprocess.run([str(build_tools / 'apksigner'), 'verify', str(apk)],
                              capture_output=True, text=True)
    if verified.returncode == 0:
        raise CandidateError('Expected an unsigned candidate, but APK signature verification passed')
    with zipfile.ZipFile(apk) as archive:
        if any(re.fullmatch(r'META-INF/[^/]+\.(RSA|DSA|EC)', name, re.I)
               for name in archive.namelist()):
            raise CandidateError('Unexpected signing certificate in the candidate APK')
        with apk.open('rb') as stream:
            stream.seek(max(0, archive.start_dir - 16))
            if stream.read(16) == b'APK Sig Block 42':
                raise CandidateError('Unexpected APK signing block in the candidate')
        if 'lib/arm64-v8a/libmapbox-gl.so' not in archive.namelist():
            raise CandidateError('Candidate does not include the ARM64 Mapbox native library')
    return {'package': identity[1], 'version_code': int(identity[2]),
            'version_name': identity[3], 'sha256': digest(apk),
            'size': apk.stat().st_size, 'signing': 'unsigned'}


def check_prepared_source(source: Path, receipt: dict) -> None:
    # Git status deliberately omits changes hidden by these index flags.
    # Parse NUL records so unusual filenames cannot disguise an entry.
    for entry in git(source, 'ls-files', '-v', '-z').split('\0'):
        if entry and (entry[0].islower() or entry[0] == 'S'):
            raise CandidateError('Prepared source has assume-unchanged or skip-worktree entries')
    if git(source, 'rev-parse', 'HEAD') != receipt['prepared_commit'] or \
            git(source, 'rev-parse', 'HEAD^{tree}') != receipt['prepared_tree'] or \
            git(source, 'status', '--porcelain', '--untracked-files=all'):
        raise CandidateError('Prepared source changed during the build')


def build(lock: dict, patch: Path, output: Path, *, prepare_only: bool = False,
          source_repository: Path | None = None) -> dict:
    # Never remove an output directory supplied by the caller. Publish a new
    # directory only after preparation/build verification succeeds.
    if output.exists() or output.is_symlink():
        raise CandidateError('Output already exists; choose a new output directory')
    toolchain = None if prepare_only else check_toolchain(lock)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.microg-candidate-', dir=output.parent) as temporary:
        stage = Path(temporary)
        source = stage / 'src'
        receipt = prepare_source(lock, patch, source, source_repository)
        check_prepared_source(source, receipt)
        receipt['state'] = 'source-prepared'
        if not prepare_only:
            sdk, build_tools, java_version = toolchain
            env = os.environ.copy()
            # Version information must describe the pinned release + backport.
            env.pop('GRADLE_MICROG_VERSION_WITHOUT_GIT', None)
            # This review candidate intentionally has no service credentials.
            env.pop('MAPBOX_VECTOR_TILES_KEY', None)
            env.pop('STADIA_API_KEY', None)
            env['ANDROID_HOME'] = str(sdk)
            env['ANDROID_SDK_ROOT'] = str(sdk)
            command = ['./gradlew', '--no-daemon', '--console=plain', '--max-workers=2', lock['task']]
            with (stage / 'gradle.log').open('w') as log:
                result = subprocess.run(command, cwd=source, env=env, stdout=log,
                                        stderr=subprocess.STDOUT)
            if result.returncode:
                # Surface the compiler error without publishing an APK/receipt
                # or leaving a previous success looking current.
                tail = (stage / 'gradle.log').read_text(errors='replace').splitlines()[-80:]
                raise CandidateError('Gradle build failed:\n' + '\n'.join(tail))
            candidates = list((source / lock['apk_directory']).glob('*.apk'))
            if len(candidates) != 1:
                raise CandidateError('Expected exactly one Mapbox release APK')
            check_prepared_source(source, receipt)
            receipt['apk'] = inspect_apk(candidates[0], lock, build_tools)
            shutil.copyfile(candidates[0], stage / 'com.google.android.gms-unsigned.apk')
            receipt.update(state='unsigned-candidate-built', java=java_version,
                           android_platform=lock['android_platform'],
                           android_build_tools=lock['android_build_tools'], gradle_task=lock['task'])
        (stage / 'candidate.json').write_text(json.dumps(receipt, indent=2) + '\n')
        if output.exists() or output.is_symlink():
            raise CandidateError('Output appeared during the build; refusing to replace it')
        stage.rename(output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New output directory; never overwritten')
    parser.add_argument('--source-repository', type=Path,
                        help='Optional local Git clone; exact locked tag/commit is still required')
    parser.add_argument('--prepare-only', action='store_true',
                        help='Verify and prepare sources without an Android SDK or compilation')
    args = parser.parse_args()
    try:
        lock = json.loads(LOCK_PATH.read_text())
        if lock['schema_version'] != 1 or lock['production_eligible'] is not False:
            raise CandidateError('Expected a version-1 nonproduction candidate lock')
        patch = checked_patch(lock)
        receipt = build(lock, patch, args.output.absolute(), prepare_only=args.prepare_only,
                        source_repository=args.source_repository.resolve() if args.source_repository else None)
    except (CandidateError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    print(f"{receipt['state']}: {args.output / 'candidate.json'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
