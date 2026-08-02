# -*- coding: utf-8 -*-
"""Offline (static) cxdec decryption for KiriKiri XP3 archives.

Faithful Python port of GARbro ``ArcFormats/KiriKiri/KiriKiriCx.cs`` (MIT licensed)
plus a control-block scanner and per-game scheme loader.

cxdec is a deterministic bytecode VM used by KiriKiri2/KirikiriZ games.  To decrypt
an entry you need:

* ``entry hash``      -- the XP3 index ``adlr`` field (per-entry, read by xp3_io)
* ``control block``   -- 4096 bytes embedded in the game's ``*.tpm`` / ``*.exe``,
                         located by scanning for the ASCII marker
                         ``" Encryption control block"`` (dword-aligned).  Each
                         on-disk dword is bit-inverted to form the in-memory table.
* ``Mask / Offset``   -- two uint32 constants baked into the game executable.
* ``PrologOrder[3] / OddBranchOrder[6] / EvenBranchOrder[8]``
                       -- three branch-order tables baked into the game executable.

The three tables + Mask/Offset are per-game constants that must be extracted from
the game binary once (IDA/x64dbg, see
``GalgameCoding/Kirikiri/HowToFindCxEncryptKey/FindCxdecKey_CN.md``), then supplied
to GalAutoTL via a JSON file:

* ``<game_dir>/GalAutoTL_cxdec.json``  (single game), or
* ``%APPDATA%/GalAutoTL/cxdec_schemes.json``  (multiple games, keyed by exe/archive name)

The control block is detected automatically from the game's ``*.tpm``/``*.exe`` when
not supplied explicitly.

Example ``GalAutoTL_cxdec.json``::

    {
      "mask": "0x1FF",
      "offset": "0x1000",
      "prolog_order": [1, 2, 0],
      "odd_branch_order": [0, 4, 2, 3, 5, 1],
      "even_branch_order": [5, 0, 3, 1, 2, 6, 4, 7],
      "tpm": "game.tpm",          // optional; control block scanned from this file
      "control_block": null,      // optional hex string (4096 bytes) overriding tpm scan
      "nana": false,              // optional; rare CxProgramNana variant
      "random_seed": "0x0"        // optional; only used when nana=true
    }
"""
from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Bytecode constants (CxByteCode enum in GARbro)
# ---------------------------------------------------------------------------
NOP = 0
RETN = 1
MOV_EDI_ARG = 2
PUSH_EBX = 3
POP_EBX = 4
PUSH_ECX = 5
POP_ECX = 6
MOV_EAX_EBX = 7
MOV_EBX_EAX = 8
MOV_ECX_EBX = 9
MOV_EAX_CONTROL_BLOCK = 10
MOV_EAX_EDI = 11
MOV_EAX_INDIRECT = 12
ADD_EAX_EBX = 13
SUB_EAX_EBX = 14
IMUL_EAX_EBX = 15
AND_ECX_0F = 16
SHR_EBX_1 = 17
SHL_EAX_1 = 18
SHR_EAX_CL = 19
SHL_EAX_CL = 20
OR_EAX_EBX = 21
NOT_EAX = 22
NEG_EAX = 23
DEC_EAX = 24
INC_EAX = 25

IMMED = 0x100
MOV_EAX_IMMED = IMMED + 1
AND_EBX_IMMED = IMMED + 2
AND_EAX_IMMED = IMMED + 3
XOR_EAX_IMMED = IMMED + 4
ADD_EAX_IMMED = IMMED + 5
SUB_EAX_IMMED = IMMED + 6

U32 = 0xFFFFFFFF


def _u32(x: int) -> int:
    return x & U32


class CxProgramError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Scheme definition
# ---------------------------------------------------------------------------
@dataclass
class CxScheme:
    mask: int = 0
    offset: int = 0
    prolog_order: List[int] = field(default_factory=lambda: [1, 2, 0])
    odd_branch_order: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    even_branch_order: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6, 7])
    control_block: Optional[List[int]] = None  # 0x400 uint32 values (bit-inverted, like GARbro in-memory)
    tpm_name: str = ""
    nana: bool = False
    random_seed: int = 0

    def __post_init__(self) -> None:
        if self.control_block is not None and len(self.control_block) != 0x400:
            raise ValueError("cxdec control block must be 0x400 dwords")


# ---------------------------------------------------------------------------
# Control block discovery (GARbro CxEncryption.Init)
# ---------------------------------------------------------------------------
CTL_BLOCK_SIGNATURE = b" Encryption control block"


def find_control_block(data: bytes) -> Optional[List[int]]:
    """Scan ``data`` for the control-block marker on dword boundaries.

    Mirrors GARbro: search advances 4 bytes at a time up to
    ``(len - 0x1000) & ~3``; on match read 0x400 little-endian dwords and
    bit-invert each (the in-memory representation used by the VM).
    """
    if len(data) < 0x1000:
        return None
    end = (len(data) - 0x1000) & ~3
    sig = CTL_BLOCK_SIGNATURE
    pos = 0
    while pos < end:
        if data.startswith(sig, pos):
            block: List[int] = []
            for i in range(0x400):
                dword = int.from_bytes(data[pos + i * 4 : pos + i * 4 + 4], "little")
                block.append(_u32(~dword))
            return block
        pos += 4
    return None


# ---------------------------------------------------------------------------
# CxProgram -- generated bytecode VM (GARbro CxProgram)
# ---------------------------------------------------------------------------
class CxProgram:
    LENGTH_LIMIT = 0x80

    def __init__(self, seed: int, control_block: Sequence[int]) -> None:
        self.m_seed = _u32(seed)
        self.m_control_block = control_block
        self.m_length = 0
        self.m_code: List[int] = []

    def clear(self) -> None:
        self.m_length = 0
        self.m_code.clear()

    def emit_nop(self, count: int) -> bool:
        if self.m_length + count > self.LENGTH_LIMIT:
            return False
        self.m_length += count
        return True

    def emit(self, code: int, length: int = 1) -> bool:
        if self.m_length + length > self.LENGTH_LIMIT:
            return False
        self.m_length += length
        self.m_code.append(code)
        return True

    def emit_u32(self, x: int) -> bool:
        if self.m_length + 4 > self.LENGTH_LIMIT:
            return False
        self.m_length += 4
        self.m_code.append(_u32(x))
        return True

    def emit_random(self) -> bool:
        return self.emit_u32(self.get_random())

    def get_random(self) -> int:
        seed = self.m_seed
        self.m_seed = _u32(1103515245 * seed + 12345)
        return _u32(self.m_seed ^ ((seed << 16) & U32) ^ (seed >> 16))

    def execute(self, hash_: int) -> int:
        eax = ebx = ecx = edi = 0
        stack: List[int] = []
        immed = 0
        code = self.m_code
        i = 0
        n = len(code)
        while i < n:
            bytecode = code[i]
            i += 1
            if bytecode & IMMED:
                if i >= n:
                    raise CxProgramError("Incomplete IMMED bytecode in CxEncryption program")
                immed = code[i]
                i += 1
            if bytecode == NOP:
                pass
            elif bytecode == IMMED:
                pass
            elif bytecode == MOV_EDI_ARG:
                edi = _u32(hash_)
            elif bytecode == PUSH_EBX:
                stack.append(ebx)
            elif bytecode == POP_EBX:
                ebx = stack.pop()
            elif bytecode == PUSH_ECX:
                stack.append(ecx)
            elif bytecode == POP_ECX:
                ecx = stack.pop()
            elif bytecode == MOV_EBX_EAX:
                ebx = eax
            elif bytecode == MOV_EAX_EDI:
                eax = edi
            elif bytecode == MOV_ECX_EBX:
                ecx = ebx
            elif bytecode == MOV_EAX_EBX:
                eax = ebx
            elif bytecode == AND_ECX_0F:
                ecx &= 0x0F
            elif bytecode == SHR_EBX_1:
                ebx >>= 1
            elif bytecode == SHL_EAX_1:
                eax = _u32(eax << 1)
            elif bytecode == SHR_EAX_CL:
                eax >>= (ecx & 0x1F)
            elif bytecode == SHL_EAX_CL:
                eax = _u32(eax << (ecx & 0x1F))
            elif bytecode == OR_EAX_EBX:
                eax |= ebx
            elif bytecode == NOT_EAX:
                eax = _u32(~eax)
            elif bytecode == NEG_EAX:
                eax = _u32(-eax)
            elif bytecode == DEC_EAX:
                eax = _u32(eax - 1)
            elif bytecode == INC_EAX:
                eax = _u32(eax + 1)
            elif bytecode == ADD_EAX_EBX:
                eax = _u32(eax + ebx)
            elif bytecode == SUB_EAX_EBX:
                eax = _u32(eax - ebx)
            elif bytecode == IMUL_EAX_EBX:
                eax = _u32(eax * ebx)
            elif bytecode == ADD_EAX_IMMED:
                eax = _u32(eax + immed)
            elif bytecode == SUB_EAX_IMMED:
                eax = _u32(eax - immed)
            elif bytecode == AND_EBX_IMMED:
                ebx &= immed
            elif bytecode == AND_EAX_IMMED:
                eax &= immed
            elif bytecode == XOR_EAX_IMMED:
                eax ^= immed
            elif bytecode == MOV_EAX_IMMED:
                eax = immed
            elif bytecode == MOV_EAX_INDIRECT:
                if eax >= len(self.m_control_block):
                    raise CxProgramError("Index out of bounds in CxEncryption program")
                eax = _u32(~self.m_control_block[eax])
            elif bytecode == RETN:
                if stack:
                    raise CxProgramError("Imbalanced stack in CxEncryption program")
                return _u32(eax)
            else:
                raise CxProgramError("Invalid bytecode in CxEncryption program")
        raise CxProgramError("CxEncryption program without RETN bytecode")


class CxProgramNana(CxProgram):
    """Rare variant with a different PRNG (some titles)."""

    def __init__(self, seed: int, random_seed: int, control_block: Sequence[int]) -> None:
        super().__init__(seed, control_block)
        self.m_random_seed = _u32(random_seed)

    def get_random(self) -> int:
        s = self.m_seed ^ _u32(self.m_seed << 17)
        s = _u32(s ^ _u32((s << 18) | (s >> 15)))
        self.m_seed = _u32(~s)
        r = self.m_random_seed ^ _u32(self.m_random_seed << 13)
        r ^= r >> 17
        self.m_random_seed = _u32(r ^ _u32(r << 5))
        return _u32(self.m_seed ^ self.m_random_seed)


# ---------------------------------------------------------------------------
# CxEncryption -- decryptor / code generator (GARbro CxEncryption)
# ---------------------------------------------------------------------------
class CxDecryptor:
    def __init__(self, scheme: CxScheme) -> None:
        self.m_mask = scheme.mask
        self.m_offset = scheme.offset
        self.prolog_order = scheme.prolog_order
        self.odd_branch_order = scheme.odd_branch_order
        self.even_branch_order = scheme.even_branch_order
        self.control_block = scheme.control_block
        self.nana = scheme.nana
        self.random_seed = scheme.random_seed
        self._programs: List[Optional[CxProgram]] = [None] * 0x80

    # -- public entry point ------------------------------------------------
    def decrypt(self, hash_: int, data: bytes, offset: int = 0) -> bytes:
        """Decrypt ``data`` (an entry body, uncompressed) starting at stream
        position ``offset``.  For a whole file ``offset`` is 0."""
        buf = bytearray(data)
        self.decrypt_core(hash_, offset, buf, 0, len(buf))
        return bytes(buf)

    # -- GARbro CxDecryptCore ----------------------------------------------
    def decrypt_core(self, hash_: int, offset: int, buf: bytearray, pos: int, count: int) -> None:
        key = _u32(hash_)
        base_offset = self.get_base_offset(key)
        if offset < base_offset:
            base_length = min(base_offset - offset, count)
            if base_length > 0:
                self.decode(key, offset, buf, pos, base_length)
                offset += base_length
                pos += base_length
                count -= base_length
        if count > 0:
            key = _u32((key >> 16) ^ key)
            self.decode(key, offset, buf, pos, count)

    def get_base_offset(self, hash_: int) -> int:
        return _u32((hash_ & self.m_mask) + self.m_offset)

    # -- GARbro Decode ------------------------------------------------------
    def decode(self, key: int, offset: int, buf: bytearray, pos: int, count: int) -> None:
        ret1, ret2 = self.execute_xcode(key)
        key1 = ret2 >> 16
        key2 = ret2 & 0xFFFF
        key3 = ret1 & 0xFF
        if key1 == key2:
            key2 = _u32(key2 + 1)
        if key3 == 0:
            key3 = 1

        if offset <= key2 < offset + count:
            buf[pos + key2 - offset] ^= (ret1 >> 16) & 0xFF
        if offset <= key1 < offset + count:
            buf[pos + key1 - offset] ^= (ret1 >> 8) & 0xFF

        for i in range(count):
            buf[pos + i] ^= key3

    # -- GARbro ExecuteXCode ------------------------------------------------
    def execute_xcode(self, hash_: int) -> "tuple[int, int]":
        seed = hash_ & 0x7F
        program = self._programs[seed]
        if program is None:
            program = self.generate_program(seed)
            self._programs[seed] = program
        hash_ >>= 7
        ret1 = program.execute(hash_)
        ret2 = program.execute(_u32(~hash_))
        return ret1, ret2

    def generate_program(self, seed: int) -> CxProgram:
        program = self.new_program(seed)
        for stage in (5, 4, 3, 2, 1):
            if self.emit_code(program, stage):
                return program
            program.clear()
        raise CxProgramError("Overly large CxEncryption bytecode")

    def new_program(self, seed: int) -> CxProgram:
        if self.nana:
            return CxProgramNana(seed, self.random_seed, self.control_block or [])
        return CxProgram(seed, self.control_block or [])

    # -- GARbro EmitCode / EmitBody / ... -----------------------------------
    def emit_code(self, program: CxProgram, stage: int) -> bool:
        return (
            program.emit_nop(5)                      # 0x57 0x56 0x53 0x51 0x52
            and program.emit(MOV_EDI_ARG, 4)         # 0x8b 0x7c 0x24 0x18
            and self.emit_body(program, stage)
            and program.emit_nop(5)                  # 0x5a 0x59 0x5b 0x5e 0x5f
            and program.emit(RETN)                   # 0xc3
        )

    def emit_body(self, program: CxProgram, stage: int) -> bool:
        if stage == 1:
            return self.emit_prolog(program)
        if not program.emit(PUSH_EBX):
            return False
        if program.get_random() & 1:
            if not self.emit_body(program, stage - 1):
                return False
        elif not self.emit_body2(program, stage - 1):
            return False
        if not program.emit(MOV_EBX_EAX, 2):
            return False
        if program.get_random() & 1:
            if not self.emit_body(program, stage - 1):
                return False
        elif not self.emit_body2(program, stage - 1):
            return False
        return self.emit_odd_branch(program) and program.emit(POP_EBX)

    def emit_body2(self, program: CxProgram, stage: int) -> bool:
        if stage == 1:
            return self.emit_prolog(program)
        if program.get_random() & 1:
            rc = self.emit_body(program, stage - 1)
        else:
            rc = self.emit_body2(program, stage - 1)
        return rc and self.emit_even_branch(program)

    def emit_prolog(self, program: CxProgram) -> bool:
        rc = True
        case = self.prolog_order[program.get_random() % 3]
        if case == 2:
            # MOV EAX, (Random() & 0x3ff)
            # MOV EAX, EncryptionControlBlock[EAX]
            rc = (
                program.emit_nop(5)                        # 0xbe
                and program.emit(MOV_EAX_IMMED, 2)         # 0x8b 0x86
                and program.emit_u32(program.get_random() & 0x3FF)
                and program.emit(MOV_EAX_INDIRECT, 0)
            )
        elif case == 1:
            rc = program.emit(MOV_EAX_EDI, 2)              # 0x8b 0xc7
        else:  # case 0
            # MOV EAX, Random()
            rc = program.emit(MOV_EAX_IMMED) and program.emit_random()  # 0xb8
        return rc

    def emit_even_branch(self, program: CxProgram) -> bool:
        rc = True
        case = self.even_branch_order[program.get_random() & 7]
        if case == 0:
            rc = program.emit(NOT_EAX, 2)                  # 0xf7 0xd0
        elif case == 1:
            rc = program.emit(DEC_EAX)                     # 0x48
        elif case == 2:
            rc = program.emit(NEG_EAX, 2)                  # 0xf7 0xd8
        elif case == 3:
            rc = program.emit(INC_EAX)                     # 0x40
        elif case == 4:
            rc = (
                program.emit_nop(5)                        # 0xbe
                and program.emit(AND_EAX_IMMED)            # 0x25
                and program.emit_u32(0x3FF)
                and program.emit(MOV_EAX_INDIRECT, 3)      # 0x8b 0x04 0x86
            )
        elif case == 5:
            rc = (
                program.emit(PUSH_EBX)                     # 0x53
                and program.emit(MOV_EBX_EAX, 2)           # 0x89 0xc3
                and program.emit(AND_EBX_IMMED, 2)         # 0x81 0xe3
                and program.emit_u32(0xAAAAAAAA)
                and program.emit(AND_EAX_IMMED)            # 0x25
                and program.emit_u32(0x55555555)
                and program.emit(SHR_EBX_1, 2)             # 0xd1 0xeb
                and program.emit(SHL_EAX_1, 2)             # 0xd1 0xe0
                and program.emit(OR_EAX_EBX, 2)            # 0x09 0xd8
                and program.emit(POP_EBX)                  # 0x5b
            )
        elif case == 6:
            rc = program.emit(XOR_EAX_IMMED) and program.emit_random()  # 0x35
        else:  # case 7
            if program.get_random() & 1:
                rc = program.emit(ADD_EAX_IMMED)           # 0x05
            else:
                rc = program.emit(SUB_EAX_IMMED)           # 0x2d
            rc = rc and program.emit_random()
        return rc

    def emit_odd_branch(self, program: CxProgram) -> bool:
        rc = True
        case = self.odd_branch_order[program.get_random() % 6]
        if case == 0:
            rc = (
                program.emit(PUSH_ECX)                     # 0x51
                and program.emit(MOV_ECX_EBX, 2)           # 0x89 0xd9
                and program.emit(AND_ECX_0F, 3)            # 0x83 0xe1 0x0f
                and program.emit(SHR_EAX_CL, 2)            # 0xd3 0xe8
                and program.emit(POP_ECX)                  # 0x59
            )
        elif case == 1:
            rc = (
                program.emit(PUSH_ECX)                     # 0x51
                and program.emit(MOV_ECX_EBX, 2)           # 0x89 0xd9
                and program.emit(AND_ECX_0F, 3)            # 0x83 0xe1 0x0f
                and program.emit(SHL_EAX_CL, 2)            # 0xd3 0xe0
                and program.emit(POP_ECX)                  # 0x59
            )
        elif case == 2:
            rc = program.emit(ADD_EAX_EBX, 2)              # 0x01 0xd8
        elif case == 3:
            rc = (
                program.emit(NEG_EAX, 2)                   # 0xf7 0xd8
                and program.emit(ADD_EAX_EBX, 2)           # 0x01 0xd8
            )
        elif case == 4:
            rc = program.emit(IMUL_EAX_EBX, 3)             # 0x0f 0xaf 0xc3
        else:  # case 5
            rc = program.emit(SUB_EAX_EBX, 2)              # 0x29 0xd8
        return rc


# ---------------------------------------------------------------------------
# Scheme loading (JSON)
# ---------------------------------------------------------------------------
def _parse_u32(value) -> int:
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    text = str(value).strip()
    return int(text, 0) & 0xFFFFFFFF


def _parse_orders(value, length: int, name: str) -> List[int]:
    if value is None:
        return list(range(length))
    items = [int(x) for x in value]
    if len(items) != length:
        raise ValueError(f"cxdec {name} must have {length} entries")
    return items


def scheme_from_dict(raw: dict) -> CxScheme:
    mask = _parse_u32(raw.get("mask", 0))
    offset = _parse_u32(raw.get("offset", 0))
    prolog = _parse_orders(raw.get("prolog_order"), 3, "prolog_order")
    odd = _parse_orders(raw.get("odd_branch_order"), 6, "odd_branch_order")
    even = _parse_orders(raw.get("even_branch_order"), 8, "even_branch_order")
    cb = None
    cbh = raw.get("control_block")
    if cbh:
        # JSON holds the RAW on-disk 4096 bytes (marker included, as dumped from
        # tpm/exe).  Mirror GARbro Init: bit-invert each little-endian dword.
        cb = [
            _u32(~struct.unpack("<I", bytes.fromhex(cbh)[i : i + 4])[0])
            for i in range(0, 0x1000, 4)
        ]
    return CxScheme(
        mask=mask,
        offset=offset,
        prolog_order=prolog,
        odd_branch_order=odd,
        even_branch_order=even,
        control_block=cb,
        tpm_name=str(raw.get("tpm", "") or ""),
        nana=bool(raw.get("nana", False)),
        random_seed=_parse_u32(raw.get("random_seed", 0)),
    )


def _resolve_control_block(scheme: CxScheme, game_dir: Path, log: Callable[[str], None] = None) -> bool:
    """Fill ``scheme.control_block`` from explicit hex, the named tpm, or a scan
    of game-dir tpm/exe files.  Returns True when a control block is available."""
    if scheme.control_block is not None:
        return True
    root = Path(game_dir)

    def _scan(path: Path) -> Optional[List[int]]:
        try:
            return find_control_block(path.read_bytes())
        except OSError:
            return None

    if scheme.tpm_name:
        cand = root / scheme.tpm_name
        if cand.is_file():
            cb = _scan(cand)
            if cb is not None:
                scheme.control_block = cb
                return True
            if log:
                log(f"cxdec: 在 {scheme.tpm_name} 中未找到控制块标记")
    for pat in ("*.tpm", "*.exe", "*.EXE"):
        for cand in root.glob(pat):
            cb = _scan(cand)
            if cb is not None:
                scheme.control_block = cb
                if log:
                    log(f"cxdec: 已从 {cand.name} 提取控制块")
                return True
    return False


def load_scheme_file(path: Path) -> Optional[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(raw, dict):
        return raw
    return None


def find_cxdec_scheme(
    game_dir: Path,
    log: Callable[[str], None] = None,
    extra_config_path: Optional[Path] = None,
) -> Optional[CxScheme]:
    """Locate a cxdec scheme for the game in ``game_dir``.

    Lookup order:
      1. ``<game_dir>/GalAutoTL_cxdec.json`` (single-scheme file)
      2. ``%APPDATA%/GalAutoTL/cxdec_schemes.json`` (multi-game map)
      3. ``extra_config_path`` if supplied
    Game keys tried: dir name, each ``*.exe`` stem, each ``*.xp3`` stem.
    """
    candidates: List[Path] = []
    local = Path(game_dir) / "GalAutoTL_cxdec.json"
    if local.is_file():
        candidates.append(local)
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    global_cfg = appdata / "GalAutoTL" / "cxdec_schemes.json"
    if global_cfg.is_file():
        candidates.append(global_cfg)
    if extra_config_path is not None and Path(extra_config_path).is_file():
        candidates.append(Path(extra_config_path))

    keys = [Path(game_dir).name.lower()]
    for p in Path(game_dir).glob("*.exe"):
        keys.append(p.stem.lower())
    for p in Path(game_dir).glob("*.xp3"):
        keys.append(p.stem.lower())
    keys = list(dict.fromkeys(k for k in keys if k))

    for cand in candidates:
        raw = load_scheme_file(cand)
        if raw is None:
            continue
        if any(k in raw for k in ("mask", "prolog_order", "odd_branch_order", "even_branch_order", "tpm")):
            try:
                scheme = scheme_from_dict(raw)
            except (ValueError, KeyError, TypeError) as e:
                if log:
                    log(f"cxdec: 方案文件 {cand.name} 解析失败: {e}")
                continue
            if _resolve_control_block(scheme, game_dir, log):
                return scheme
            continue
        # multi-game map: try keys
        for key in keys:
            entry = raw.get(key)
            if not isinstance(entry, dict):
                continue
            try:
                scheme = scheme_from_dict(entry)
            except (ValueError, KeyError, TypeError) as e:
                if log:
                    log(f"cxdec: 方案 {key} 解析失败: {e}")
                continue
            if _resolve_control_block(scheme, game_dir, log):
                return scheme
    return None


# ---------------------------------------------------------------------------
# Archive-level extraction helper
# ---------------------------------------------------------------------------
def extract_xp3_cxdec(
    archive: Path,
    out_dir: Path,
    scheme: CxScheme,
    *,
    only_suffixes: Optional[Sequence[str]] = None,
    log: Callable[[str], None] = None,
) -> int:
    """Extract ``archive`` into ``out_dir``, cxdec-decrypting encrypted entries.

    Encrypted entries are decrypted with the entry's adlr (hash).  ``.ks`` results
    are sanity-checked with the KAG heuristic; when most fail the scheme is likely
    wrong and a clear error is raised.  Returns number of files written.
    """
    from app.core.xp3_crypto import looks_like_kag_after_decode
    from app.core.xp3_io import is_extractable_xp3_path, list_xp3, read_xp3_entry

    archive = Path(archive)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffixes = {s.lower() for s in only_suffixes} if only_suffixes else None
    entries = list_xp3(archive)
    decryptor = CxDecryptor(scheme)
    written = 0
    checked = 0
    good = 0
    for e in entries:
        if not is_extractable_xp3_path(e.path):
            continue
        if suffixes is not None and Path(e.path).suffix.lower() not in suffixes:
            continue
        data = read_xp3_entry(archive, e)
        if e.encrypted:
            data = decryptor.decrypt(e.adler32, data)
            if Path(e.path).suffix.lower() == ".ks":
                checked += 1
                if looks_like_kag_after_decode(data):
                    good += 1
        dest = out_dir / Path(e.path.replace("/", os.sep))
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        except OSError:
            continue
        written += 1

    if checked >= 3 and good * 2 < checked:
        raise RuntimeError(
            f"cxdec 解密校验失败（{good}/{checked} 个 .ks 通过）——请检查 GalAutoTL_cxdec.json "
            "?? mask/offset/branch orders/control block ????"
        )
    if log and checked:
        log(f"  cxdec 离线解密: {written} 文件，.ks 校验 {good}/{checked}")
    return written
