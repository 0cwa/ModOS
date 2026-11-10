#!/usr/bin/env python3
"""Tests for the one-instruction vold exFAT timeout patch (src/lineage_vold.py).

A synthetic AArch64 ELF carries the same structure as the real vold: a .text
holding exfat::Check and ForkExecvpTimeout, and an XZ-compressed
.gnu_debugdata ELF with their symbols. Set MODOS_TEST_VOLD to a real
/system/bin/vold to additionally check it.
"""

from __future__ import annotations

import lzma
import os
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import lineage_vold as m  # noqa: E402

NOP = 0xD503201F
RET = 0xD65F03C0
EXFAT_CHECK = "_ZN7android4vold5exfat5CheckERKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE"
FORK = "_ZN7android4vold17ForkExecvpTimeoutERKNSt3__16vectorINS1_12basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEENS6_IS8_EEEENS1_6chrono8durationIxNS1_5ratioILl1ELl1EEEEEPc"
VFAT_CHECK = "_ZN7android4vold4vfat5CheckERKNSt3__112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE"


def bl(from_address: int, to_address: int) -> int:
    return 0x94000000 | (((to_address - from_address) // 4) & 0x03FFFFFF)


def elf(sections, phdrs=(), kind=3) -> bytes:
    """sections: [(name, sh_type, addr, link, data)]; phdrs filled by the caller."""
    names = b"\0"
    name_offsets = []
    for name, *_ in sections:
        name_offsets.append(len(names))
        names += name.encode() + b"\0"
    shstr_name = len(names)
    names += b".shstrtab\0"
    body = bytearray(b"\0" * (64 + 56 * len(phdrs)))
    placed = []
    for _, _, _, _, data in sections:
        while len(body) % 16:
            body.append(0)
        placed.append(len(body))
        body += data
    shstr_offset = len(body)
    body += names
    while len(body) % 8:
        body.append(0)
    shoff = len(body)
    headers = [struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    for index, (_, sh_type, addr, link, data) in enumerate(sections):
        headers.append(
            struct.pack("<IIQQQQIIQQ", name_offsets[index], sh_type, 0, addr, placed[index], len(data), link, 0, 8, 24 if sh_type == 2 else 0)
        )
    headers.append(struct.pack("<IIQQQQIIQQ", shstr_name, 3, 0, 0, shstr_offset, len(names), 0, 0, 1, 0))
    body += b"".join(headers)
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF\x02\x01\x01" + b"\0" * 9,
        kind, 183, 1, 0, 64 if phdrs else 0, shoff, 0, 64, 56, len(phdrs), 64, len(headers), len(headers) - 1,
    )
    body[:64] = header
    for index, ph in enumerate(phdrs):
        body[64 + 56 * index:64 + 56 * (index + 1)] = ph
    return bytes(body), placed


def symtab(entries):
    """entries: [(name, address, size)] -> (symtab bytes, strtab bytes)."""
    strtab = b"\0"
    raw = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)
    for name, address, size in entries:
        raw += struct.pack("<IBBHQQ", len(strtab), 0x12, 0, 1, address, size)
        strtab += name.encode() + b"\0"
    return raw, strtab


def build_vold(
    check_seconds: int = 45,
    sites: int = 1,
    fork_target_ok: bool = True,
    debugdata: bool = True,
    exfat_symbols: int = 1,
    branch_between: bool = False,
    interpreter: str = "/system/bin/linker64",
    setup_words: list[int] | None = None,
    fork_symbol: str = FORK,
) -> bytes:
    text_base = 0x1000
    fork_at = text_base
    words = [NOP, RET] + [NOP] * 2  # ForkExecvpTimeout stand-in, 16 bytes
    check_at = text_base + 4 * len(words)
    body = []
    for _ in range(sites):
        index = len(body)
        body.append(m._mov_w1(check_seconds))
        body.extend([0xAA1F03E2] if setup_words is None else setup_words)
        if branch_between:
            body.append(0x14000002)  # b .+8
        body.append(NOP)
        target = fork_at if fork_target_ok else fork_at + 4
        body.append(bl(check_at + 4 * len(body), target))
    body.append(RET)
    words += body
    # vfat::Check keeps the generic 45-second limit and must never be patched.
    vfat_at = text_base + 4 * len(words)
    words += [m._mov_w1(45), bl(vfat_at + 4, fork_at), RET]
    text = struct.pack(f"<{len(words)}I", *words)
    check_size = 4 * len(body)

    entries = [(fork_symbol, fork_at, 16)]
    entries += [(EXFAT_CHECK + ("" if i == 0 else f"{i}"), check_at, check_size) for i in range(exfat_symbols)]
    entries.append((VFAT_CHECK, vfat_at, 12))
    sym, strtab = symtab(entries)
    inner, _ = elf([(".symtab", 2, 0, 2, sym), (".strtab", 3, 0, 0, strtab)], kind=1)
    interp = interpreter.encode() + b"\0"

    sections = [(".text", 1, text_base, 0, text), (".interp", 1, 0, 0, interp)]
    if debugdata:
        sections.append((".gnu_debugdata", 1, 0, 0, lzma.compress(inner)))
    # One executable PT_LOAD maps the file so that .text lands at text_base.
    dummy = [b"\0" * 56, b"\0" * 56]
    _, placed = elf(sections, phdrs=dummy)
    load_vaddr = text_base - placed[0]
    interp_offset = placed[1]
    blob, _ = elf(sections, phdrs=dummy)
    phdrs = [
        struct.pack("<IIQQQQQQ", 1, 5, 0, load_vaddr, load_vaddr, len(blob), len(blob), 0x1000),
        struct.pack("<IIQQQQQQ", 3, 4, interp_offset, interp_offset, interp_offset, len(interp), len(interp), 1),
    ]
    blob, placed = elf(sections, phdrs=phdrs)
    assert placed[0] == text_base - load_vaddr
    return blob


def assert_raises(callback, pattern: str) -> None:
    try:
        callback()
    except m.VoldModuleError as exc:
        assert re.search(pattern, str(exc)), f"{pattern!r} not in {exc}"
    else:
        raise AssertionError(f"expected VoldModuleError matching {pattern!r}")


def test_patch_changes_one_instruction() -> None:
    original = build_vold()
    patched = m.patch_vold(original)
    assert len(patched) == len(original)
    changed = [i for i in range(len(original)) if original[i] != patched[i]]
    assert changed and changed[0] // 4 == changed[-1] // 4, "more than one instruction changed"
    (before,) = struct.unpack_from("<I", original, changed[0] & ~3)
    (after,) = struct.unpack_from("<I", patched, changed[0] & ~3)
    assert before == 0x528005A1  # mov w1, #45
    assert after == 0x52800F01  # mov w1, #120
    assert m.find_call_sites(patched, 45) == []
    assert len(m.find_call_sites(patched, 120)) == 1


def test_vfat_check_is_not_patched() -> None:
    original = build_vold()
    patched = m.patch_vold(original)
    # vfat::Check still contains a 45-second mov to the same function.
    assert struct.pack("<I", m._mov_w1(45)) in patched


def test_fails_closed() -> None:
    assert_raises(lambda: m.patch_vold(build_vold(sites=0)), "found 0")
    assert_raises(lambda: m.patch_vold(build_vold(sites=2)), "found 2")
    assert_raises(lambda: m.patch_vold(build_vold(check_seconds=44)), "found 0")
    assert_raises(lambda: m.patch_vold(build_vold(fork_target_ok=False)), "found 0")
    assert_raises(lambda: m.patch_vold(build_vold(branch_between=True)), "found 0")
    assert_raises(lambda: m.patch_vold(build_vold(debugdata=False)), "gnu_debugdata")
    assert_raises(lambda: m.patch_vold(build_vold(exfat_symbols=2)), "exactly one")
    assert_raises(lambda: m.patch_vold(build_vold(interpreter="/lib/ld.so")), "linker")
    assert_raises(lambda: m.patch_vold(b"not an elf" * 20), "ELF")
    assert_raises(lambda: m.patch_vold(m.patch_vold(build_vold())), "found 0")  # already patched


def test_verify_rejects_extra_changes() -> None:
    original = build_vold()
    patched = bytearray(m.patch_vold(original))
    patched[-1] ^= 1
    assert_raises(lambda: m.verify_patched(original, bytes(patched)), "more than one")
    assert_raises(lambda: m.verify_patched(original, original), "more than one")
    assert_raises(lambda: m.verify_patched(original, original[:-1]), "different size")


def test_timeout_must_reach_only_the_timeout_argument() -> None:
    # These would previously pass while either leaving the call's timeout
    # unchanged or also changing the context pointer / another argument.
    for instruction in (
        m._mov_w1(60),              # mov w1,#60: overwrites the intended timeout
        0xAA0203E1,                 # mov x1,x2: overwrites x1 (same physical register)
        0xAA0103E2,                 # mov x2,x1: copies timeout into context pointer
        0x2A0103E2,                 # mov w2,w1: also reads the timeout
        0x91000020,                 # add x0,x1,#0: timeout reaches another argument
        0xF9400101,                 # ldr x1,[x8]: overwrites timeout
        0xF9400022,                 # ldr x2,[x1]: uses timeout as an address
        0xF90003E1,                 # str x1,[sp]: saves a changed value elsewhere
        0xF9000033,                 # str x19,[x1]: uses timeout as a store address
        0x52C00002,                 # unallocated movz Wd with a 32-bit shift
        0x00000000,                 # unknown instruction
    ):
        assert_raises(lambda: m.patch_vold(build_vold(setup_words=[instruction])), "found 0")


def test_reviewed_independent_argument_setup_is_supported() -> None:
    for instruction in (
        NOP,
        0x2A0303E2,                 # mov w2,w3
        0xAA0303E2,                 # mov x2,x3
        0x12800002,                 # movn w2,#0
        0x52800002,                 # movz w2,#0
        0xF2E00022,                 # movk x2,#1,lsl#48
        0x910003E0,                 # add x0,sp,#0
        0xD1004100,                 # sub x0,x8,#16
        0x10000000,                 # adr x0,.
        0x90000002,                 # adrp x2,.
        0xB9400102,                 # ldr w2,[x8]
        0xF9400102,                 # ldr x2,[x8]
        0xF9000FF3,                 # str x19,[sp,#24]
    ):
        original = build_vold(setup_words=[instruction])
        patched = m.patch_vold(original)
        assert len(m.find_call_sites(patched, 120)) == 1


def test_authenticated_pdx235_20261106_argument_setup() -> None:
    # Exact three intervening words from the authenticated official 20261106
    # pdx235 vold. Keep the call address synthetic and exercise the real setup.
    original = build_vold(setup_words=[0xF9469108, 0xF9000FF3, 0xF9400102])
    assert len(m.find_call_sites(m.patch_vold(original), 120)) == 1


def test_function_signature_change_is_rejected() -> None:
    # The name prefix alone does not establish that seconds are passed in x1.
    assert_raises(
        lambda: m.patch_vold(build_vold(fork_symbol=m.FORK_EXEC_PREFIX + "ii")),
        "unsupported function signature",
    )


def test_invalid_program_headers_are_rejected() -> None:
    binary = bytearray(build_vold())
    struct.pack_into("<Q", binary, 32, len(binary) + 56)
    assert_raises(lambda: m.patch_vold(bytes(binary)), "program headers")


class FakeFs:
    def __init__(self, root: Path, data: bytes, label="u:object_r:vold_exec:s0", mode=0o755, kind="RegularFile"):
        self.tree = root
        target = root / "system/bin/vold"
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        self.info = SimpleNamespace(
            entries=[SimpleNamespace(path=PurePosixPath("/system/bin/vold"), file_type=kind, file_mode=mode)]
        )
        self.contexts = [(re.compile(r"/system/bin/vold"), label)]
        self.opened = []

    def open(self, path: str, open_mode: str, **kwargs):
        assert open_mode == "wb" and kwargs == {}, "must keep the existing image metadata"
        fs = self
        target = self.tree / path.lstrip("/")

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def write(self, data: bytes):
                target.write_bytes(data)
                fs.opened.append(path)

        return Stream()


def test_inject() -> None:
    original = build_vold()
    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(Path(tmp), original)
        digest = m.inject_vold(fs)
        assert fs.opened == ["/system/bin/vold"]
        assert digest == m.sha256((Path(tmp) / "system/bin/vold").read_bytes())
        assert (Path(tmp) / "system/bin/vold").read_bytes() == m.patch_vold(original)
        assert len(fs.info.entries) == 1
    for kwargs, pattern in (
        ({"label": "u:object_r:system_file:s0"}, "vold_exec"),
        ({"mode": 0o644}, "not executable"),
        ({"kind": "Symlink"}, "not regular"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            fs = FakeFs(Path(tmp), original, **kwargs)
            assert_raises(lambda: m.inject_vold(fs), pattern)
            assert not fs.opened, "nothing may be written when a check fails"
    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(Path(tmp), build_vold(sites=0))
        assert_raises(lambda: m.inject_vold(fs), "found 0")
        assert not fs.opened
    with tempfile.TemporaryDirectory() as tmp:
        fs = FakeFs(Path(tmp), original)
        (Path(tmp) / "system/bin/vold").unlink()
        (Path(tmp) / "system/bin/vold").symlink_to("/bin/sh")
        assert_raises(lambda: m.inject_vold(fs), "safe regular file")


def test_real_vold() -> None:
    path = os.environ.get("MODOS_TEST_VOLD")
    if not path:
        print("skipped: set MODOS_TEST_VOLD to also check a real vold")
        return
    original = Path(path).read_bytes()
    patched = m.patch_vold(original)
    assert len(m.find_call_sites(original, 45)) == 1
    assert len(m.find_call_sites(patched, 120)) == 1
    print(f"real vold ok: {m.sha256(original)} -> {m.sha256(patched)}")


def main() -> int:
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
    print("lineage vold exFAT timeout patch tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
