#!/usr/bin/env python3
"""Exercise ModOS catalog-legacy classes through the pinned helper APIs."""

from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY = Path(__file__).resolve().parents[1]
HELPER_ROOT = Path(os.environ['MODOS_HELPER_ROOT']).resolve(strict=True)
sys.path.insert(0, str(HELPER_ROOT))

from lib.modules.catalog import load_catalog
from lib.modules.extensions import load_adapter_extension
from lib.modules.locks import ArtifactLockFile, cache_path, write_lock
from lib.modules.registry import INTERNAL_ADAPTERS, LOCKED_ADAPTERS
from lib.modules.report import build_patch_report
from lib.modules.verified import construct_locked_adapters, open_verified_selection


def _extension_fixture(root: Path) -> Path:
    extension_root = root / 'extension'
    manifest_root = extension_root / 'manifests'
    adapter_root = extension_root / 'adapters'
    manifest_root.mkdir(parents=True)
    adapter_root.mkdir()
    entries = []
    for module_id in ('shizuku', 'exfatprogs-fsck'):
        source_name = 'exfatprogs_fsck.py' if module_id.startswith('exfat') else 'shizuku.py'
        source = REPOSITORY / 'src' / source_name
        staged_source = adapter_root / source_name
        shutil.copyfile(source, staged_source)
        manifest_source = REPOSITORY / 'tools/modules/catalog' / f'{module_id}.toml'
        shutil.copyfile(manifest_source, manifest_root / f'{module_id}.toml')
        entries.append({
            'id': module_id,
            'kind': 'catalog-legacy',
            'source': f'adapters/{source_name}',
            'sha256': hashlib.sha256(staged_source.read_bytes()).hexdigest(),
            'target': '_module_class',
            'verification_schemes': ['sha256'] + (['apk-signature'] if module_id == 'shizuku' else []),
            'trusted_signers': [
                '268b5590e868fb08bae7e0ac413564cd1ff88f5ccff74af9dbd0dc918e30db30'
            ] if module_id == 'shizuku' else [],
            'digest_required': True,
        })
    extension_path = extension_root / 'adapter-extension.toml'
    lines = ['schema_version = 1', 'catalog_directories = ["manifests"]']
    for entry in entries:
        lines.extend(('', '[[adapters]]'))
        for field in ('id', 'kind', 'source', 'sha256', 'target'):
            lines.append(f'{field} = {json.dumps(entry[field])}')
        for field in ('verification_schemes', 'trusted_signers'):
            lines.append(f'{field} = {json.dumps(entry[field])}')
        lines.append('digest_required = true')
    extension_path.write_text('\n'.join(lines) + '\n')
    return extension_path


def _lock_fixture(root: Path, cache: Path) -> Path:
    source_locks = (
        REPOSITORY / 'locks/shizuku-v13.6.0.json',
        REPOSITORY / 'locks/exfatprogs-termux-v1.4.3.json',
    )
    records = []
    for lock_path in source_locks:
        raw = json.loads(lock_path.read_text())
        for module in raw['modules']:
            for index, artifact in enumerate(module['artifacts']):
                payload = f"fixture:{module['id']}:{artifact['id']}:{index}".encode()
                digest = hashlib.sha256(payload).hexdigest()
                artifact['sha256'] = digest
                artifact['size'] = len(payload)
                artifact_path = cache_path(cache, digest)
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_bytes(payload)
            records.append(module)
    lock = ArtifactLockFile.model_validate({
        'schema_version': 1,
        'modules': sorted(records, key=lambda module: module['id']),
    })
    path = root / 'selected-lock.json'
    write_lock(path, lock)
    return path


def _profile_fixture(root: Path, lock_path: Path) -> Path:
    import tomlkit
    from lib.modules.locks import load_canonical_lock

    lock, lock_digest = load_canonical_lock(lock_path)
    path = root / 'selected-profile.toml'
    profile = {
        'schema_version': 1,
        'id': 'modos-catalog-report-fixture',
        'rom_family': 'lineageos',
        'root_mode': 'rootless',
        'abi': 'arm64-v8a',
        'api_level': 36,
        'output_scope': 'local-unpublished',
        'enabled_modules': ['exfatprogs-fsck', 'shizuku'],
        'acknowledgements': [],
        'experimental_acknowledgements': [],
        'capabilities': {
            'root_providers': [],
            'zygisk_providers': [],
            'selective_signature_spoofing': False,
            'product_priv_app': False,
            'custom_init_selinux': False,
        },
    }
    path.write_text(tomlkit.dumps(profile), encoding='UTF-8')
    return path


class CatalogPatchReportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        extension_path = _extension_fixture(self.root)
        self.extension = load_adapter_extension(extension_path)
        self.catalog = load_catalog(
            registrations=(
                INTERNAL_ADAPTERS
                + LOCKED_ADAPTERS
                + self.extension.catalog_registrations
            ),
            additional_directories=self.extension.catalog_directories,
        )
        self.lock_path = _lock_fixture(self.root, self.cache)
        self.profile_path = _profile_fixture(self.root, self.lock_path)

    def test_resolve_construct_inject_and_report_accurate_paths(self):
        with open_verified_selection(
            self.catalog,
            self.lock_path,
            self.profile_path,
            self.cache,
            verify_apks=False,
        ) as selection:
            self.assertEqual(
                selection.resolution.selected_modules,
                ('exfatprogs-fsck', 'shizuku'),
            )
            shizuku_source = sys.modules['_modos_adapter_shizuku']
            exfat_source = sys.modules['_modos_adapter_exfatprogs_fsck']
            shizuku_artifact = selection.contexts[1].artifact('manager-apk')
            exfat_artifact = selection.contexts[0].artifact('exfatprogs-aarch64-deb')
            with ExitStack() as stack:
                stack.enter_context(patch.object(shizuku_source, 'APK_SHA256', shizuku_artifact.sha256))
                stack.enter_context(patch.object(shizuku_source, 'APK_SIZE', shizuku_artifact.size))
                stack.enter_context(patch.object(exfat_source, 'PACKAGE_SHA256', exfat_artifact.sha256))
                stack.enter_context(patch.object(exfat_source, 'PACKAGE_SIZE', exfat_artifact.size))
                shizuku_install_paths = (
                    '/etc/init/shizuku.rc',
                    '/app/Shizuku/Shizuku.apk',
                )
                stack.enter_context(patch.object(
                    shizuku_source,
                    'inject_payload',
                    return_value=shizuku_install_paths,
                ))
                stack.enter_context(patch.object(
                    exfat_source,
                    '_system_image_binary',
                    return_value=b'verified fixture executable',
                ))
                stack.enter_context(patch.object(exfat_source, '_inject_system_binary'))
                stack.enter_context(patch.dict(os.environ, {
                    'PIXENEOS_ROM_FAMILY': 'lineageos',
                    'PIXENEOS_DEVICE_NAME': 'pdx235',
                    'PIXENEOS_OUTPUT_SCOPE': 'local-unpublished',
                    'PIXENEOS_SHIZUKU_ROOT_MODE': 'rootless',
                }))
                modules = construct_locked_adapters(
                    selection, self.extension.locked_factories
                )
                self.assertEqual(
                    tuple(module_id for module_id, _ in modules),
                    ('exfatprogs-fsck', 'shizuku'),
                )
                ext_fs = {'product': object(), 'system': object()}
                results = tuple(
                    (module_id, module.inject({}, ext_fs, []))
                    for module_id, module in modules
                )
            report = build_patch_report(selection, results)
            self.assertEqual(report['injected_paths'], [
                {'module': 'exfatprogs-fsck', 'path': '/system/bin/fsck.exfat'},
                {'module': 'shizuku', 'path': '/product/app/Shizuku/Shizuku.apk'},
                {'module': 'shizuku', 'path': '/product/etc/init/shizuku.rc'},
            ])

    def test_catalog_adapters_keep_legacy_no_context_return(self):
        shizuku_source = sys.modules['_modos_adapter_shizuku']
        exfat_source = sys.modules['_modos_adapter_exfatprogs_fsck']
        shizuku_class = next(item for item in self.extension.legacy_modules if item.NAME == 'shizuku')
        exfat_class = next(item for item in self.extension.legacy_modules if item.NAME == 'exfatprogs-fsck')
        with patch.dict(os.environ, {
            'PIXENEOS_ROM_FAMILY': 'lineageos',
            'PIXENEOS_DEVICE_NAME': 'pdx235',
            'PIXENEOS_OUTPUT_SCOPE': 'local-unpublished',
            'PIXENEOS_SHIZUKU_ROOT_MODE': 'rootless',
            'PIXENEOS_SHIZUKU_APK_PATH': str(self.root / 'unused.apk'),
            'PIXENEOS_EXFATPROGS_PACKAGE_PATH': str(self.root / 'unused.deb'),
        }):
            with patch.object(shizuku_source, 'inject_payload', return_value=(
                '/app/Shizuku/Shizuku.apk',
            )), patch.object(exfat_source, '_system_image_binary', return_value=b'fixture'), \
                    patch.object(exfat_source, '_inject_system_binary'):
                shizuku_module = shizuku_class.from_args(type('Args', (), {
                    'module_shizuku': Path('selected.zip'),
                })())
                exfat_module = exfat_class.from_args(type('Args', (), {
                    'module_exfatprogs_fsck': Path('selected.zip'),
                })())
                self.assertIsNone(shizuku_module.inject({}, {}, []))
                self.assertIsNone(exfat_module.inject({}, {'system': object()}, []))


if __name__ == '__main__':
    unittest.main(verbosity=2)
