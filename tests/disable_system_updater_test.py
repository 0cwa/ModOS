#!/usr/bin/env python3
"""Tests for the stock system updater removal module."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from disable_system_updater import (  # noqa: E402
    DisableSystemUpdaterError,
    remove_system_updater,
    target_for_family,
)


def fake_fs(root: Path, paths: list[str]) -> SimpleNamespace:
    entries = []
    for raw in paths:
        path = PurePosixPath(raw)
        tree_path = root / path.relative_to("/")
        tree_path.parent.mkdir(parents=True, exist_ok=True)
        tree_path.write_bytes(b"fixture")
        entries.append(SimpleNamespace(path=path, file_type="RegularFile"))
    return SimpleNamespace(
        tree=root,
        info=SimpleNamespace(entries=entries),
    )


def assert_raises(func, context: str) -> None:
    try:
        func()
    except DisableSystemUpdaterError:
        return
    raise AssertionError(f"{context}: expected DisableSystemUpdaterError")


def test_grapheneos() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        partition = Path(temporary)
        paths = list(target_for_family("grapheneos")[1])
        fs = fake_fs(partition, paths)
        removed = remove_system_updater("grapheneos", {"system": fs})
        assert tuple(paths) == removed
        assert not fs.info.entries
        for raw in paths:
            assert not (partition / PurePosixPath(raw).relative_to("/")).exists()


def test_lineageos() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        partition = Path(temporary)
        paths = list(target_for_family("lineageos")[1])
        fs = fake_fs(partition, paths)
        removed = remove_system_updater("lineageos", {"system_ext": fs})
        assert tuple(paths) == removed
        assert not fs.info.entries


def test_optional_companion_files_can_be_absent() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        partition = Path(temporary)
        apk = target_for_family("grapheneos")[1][0]
        fs = fake_fs(partition, [apk])
        assert remove_system_updater("grapheneos", {"system": fs}) == (apk,)


def test_required_apk_missing_fails() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        fs = fake_fs(Path(temporary), [])
        assert_raises(
            lambda: remove_system_updater("grapheneos", {"system": fs}),
            "missing updater APK",
        )


def test_tree_only_file_fails_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        partition = Path(temporary)
        paths = list(target_for_family("grapheneos")[1])
        fs = fake_fs(partition, [paths[0]])
        companion = partition / PurePosixPath(paths[1]).relative_to("/")
        companion.parent.mkdir(parents=True, exist_ok=True)
        companion.write_bytes(b"untracked")

        assert_raises(
            lambda: remove_system_updater("grapheneos", {"system": fs}),
            "tree-only companion",
        )
        assert (partition / PurePosixPath(paths[0]).relative_to("/")).exists()
        assert len(fs.info.entries) == 1


def test_metadata_only_file_fails_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        partition = Path(temporary)
        paths = list(target_for_family("grapheneos")[1])
        fs = fake_fs(partition, paths[:2])
        companion = partition / PurePosixPath(paths[1]).relative_to("/")
        companion.unlink()

        assert_raises(
            lambda: remove_system_updater("grapheneos", {"system": fs}),
            "metadata-only companion",
        )
        assert (partition / PurePosixPath(paths[0]).relative_to("/")).exists()
        assert len(fs.info.entries) == 2


def test_symlink_target_fails_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        partition = Path(temporary)
        apk = target_for_family("lineageos")[1][0]
        fs = fake_fs(partition, [apk])
        apk_path = partition / PurePosixPath(apk).relative_to("/")
        apk_path.unlink()
        outside = partition / "outside.apk"
        outside.write_bytes(b"outside")
        apk_path.symlink_to(outside)

        assert_raises(
            lambda: remove_system_updater("lineageos", {"system_ext": fs}),
            "symlink updater APK",
        )
        assert outside.read_bytes() == b"outside"
        assert len(fs.info.entries) == 1


def test_unknown_family_fails() -> None:
    assert_raises(lambda: target_for_family("unknown"), "unknown ROM family")


def main() -> None:
    test_grapheneos()
    test_lineageos()
    test_optional_companion_files_can_be_absent()
    test_required_apk_missing_fails()
    test_tree_only_file_fails_before_mutation()
    test_metadata_only_file_fails_before_mutation()
    test_symlink_target_fails_before_mutation()
    test_unknown_family_fails()
    print("system updater removal tests passed")


if __name__ == "__main__":
    main()
