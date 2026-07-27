# -*- coding: utf-8 -*-
"""Surgical UnityFS patch: same-length UTF-8 replace inside uncompressed blocks.

Keeps multi-block layout (unlike UnityPy.save which often makes unbootable bundles).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

LogFn = Optional[Callable[[str], None]]


@dataclass
class BlockInfo:
    uncompressed_size: int
    compressed_size: int
    flags: int


@dataclass
class DirEntry:
    offset: int
    size: int
    flags: int
    path: str


@dataclass
class UnityFSLayout:
    version: int
    version_player: str
    version_engine: str
    data_flags: int
    uses_block_alignment: bool
    blocks_info_at_end: bool
    block_info_need_pad: bool
    uncompressed_data_hash: bytes
    blocks: List[BlockInfo]
    directory: List[DirEntry]
    blocks_info_pos: int
    blocks_info_comp_size: int
    blocks_info_uncomp_size: int
    data_pos: int


def _decompress(flags: int, compressed: bytes, uncompressed_size: int) -> bytes:
    from UnityPy.enums import ArchiveFlags, CompressionFlags
    from UnityPy.helpers import CompressionHelper

    comp_flag = CompressionFlags(flags & ArchiveFlags.CompressionTypeMask)
    if comp_flag in CompressionHelper.DECOMPRESSION_MAP:
        out = CompressionHelper.DECOMPRESSION_MAP[comp_flag](compressed, uncompressed_size)
        return out
    return compressed


def _compress(flags: int, raw: bytes) -> Tuple[bytes, int]:
    """Return (compressed_bytes, flags_used). May clear compression bits if store raw."""
    from UnityPy.enums import ArchiveFlags, CompressionFlags
    from UnityPy.helpers import CompressionHelper

    mask = ArchiveFlags.CompressionTypeMask
    comp_flag = CompressionFlags(flags & mask)
    if comp_flag in CompressionHelper.COMPRESSION_MAP and comp_flag != CompressionFlags.NONE:
        try:
            comp = CompressionHelper.COMPRESSION_MAP[comp_flag](raw)
            if len(comp) < len(raw):
                return comp, flags
        except Exception:
            pass
    # uncompressed
    return raw, flags & ~int(mask)


def parse_unityfs(data: bytes) -> UnityFSLayout:
    from UnityPy.streams import EndianBinaryReader

    reader = EndianBinaryReader(data, endian=">")
    signature = reader.read_string_to_null()
    if signature != "UnityFS":
        raise ValueError(f"Not UnityFS: {signature!r}")
    version = reader.read_u_int()
    version_player = reader.read_string_to_null()
    version_engine = reader.read_string_to_null()
    _bundle_size = reader.read_long()
    blocks_info_comp = reader.read_u_int()
    blocks_info_uncomp = reader.read_u_int()
    data_flags = reader.read_u_int()

    # Align after header for v7+ (and many 2019.4+)
    uses_align = version >= 7
    # UnityPy also aligns for certain 2019.4.15 with version 6 — try peek:
    if version >= 7:
        reader.align_stream(16)
        uses_align = True

    blocks_info_at_end = bool(data_flags & 0x80)
    block_info_need_pad = bool(data_flags & 0x200)

    if blocks_info_at_end:
        data_pos = reader.Position
        if block_info_need_pad:
            # align data start
            pad = (16 - (data_pos % 16)) % 16
            data_pos += pad
        blocks_info_pos = len(data) - blocks_info_comp
        blocks_info_bytes = data[blocks_info_pos : blocks_info_pos + blocks_info_comp]
    else:
        blocks_info_pos = reader.Position
        blocks_info_bytes = reader.read_bytes(blocks_info_comp)
        data_pos = reader.Position
        if block_info_need_pad:
            pad = (16 - (data_pos % 16)) % 16
            data_pos += pad

    info_raw = _decompress(data_flags, blocks_info_bytes, blocks_info_uncomp)
    if len(info_raw) < blocks_info_uncomp:
        info_raw = info_raw + b"\x00" * (blocks_info_uncomp - len(info_raw))
    info_raw = info_raw[:blocks_info_uncomp]

    br = EndianBinaryReader(info_raw, endian=">")
    uncompressed_data_hash = br.read_bytes(16)
    n_blocks = br.read_int()
    blocks = [
        BlockInfo(br.read_u_int(), br.read_u_int(), br.read_u_short()) for _ in range(n_blocks)
    ]
    n_nodes = br.read_int()
    directory = [
        DirEntry(br.read_long(), br.read_long(), br.read_u_int(), br.read_string_to_null())
        for _ in range(n_nodes)
    ]

    return UnityFSLayout(
        version=version,
        version_player=version_player,
        version_engine=version_engine,
        data_flags=data_flags,
        uses_block_alignment=uses_align,
        blocks_info_at_end=blocks_info_at_end,
        block_info_need_pad=block_info_need_pad,
        uncompressed_data_hash=uncompressed_data_hash,
        blocks=blocks,
        directory=directory,
        blocks_info_pos=blocks_info_pos,
        blocks_info_comp_size=blocks_info_comp,
        blocks_info_uncomp_size=blocks_info_uncomp,
        data_pos=data_pos,
    )


def decompress_stream(data: bytes, layout: UnityFSLayout) -> bytearray:
    out = bytearray()
    pos = layout.data_pos
    for b in layout.blocks:
        chunk = data[pos : pos + b.compressed_size]
        pos += b.compressed_size
        unc = _decompress(b.flags, chunk, b.uncompressed_size)
        if len(unc) < b.uncompressed_size:
            unc = unc + b"\x00" * (b.uncompressed_size - len(unc))
        out += unc[: b.uncompressed_size]
    return out


def rebuild_unityfs(orig: bytes, layout: UnityFSLayout, stream: bytes, log: LogFn = None) -> bytes:
    from UnityPy.streams import EndianBinaryWriter

    expect = sum(b.uncompressed_size for b in layout.blocks)
    if len(stream) != expect:
        raise ValueError(f"uncompressed size mismatch {len(stream)} vs {expect}")

    new_blocks: List[BlockInfo] = []
    compressed_payload = bytearray()
    off = 0
    for b in layout.blocks:
        piece = stream[off : off + b.uncompressed_size]
        off += b.uncompressed_size
        comp, flags = _compress(b.flags, piece)
        new_blocks.append(BlockInfo(b.uncompressed_size, len(comp), flags))
        compressed_payload += comp

    bw = EndianBinaryWriter(endian=">")
    bw.write_bytes(layout.uncompressed_data_hash)
    bw.write_int(len(new_blocks))
    for b in new_blocks:
        bw.write_u_int(b.uncompressed_size)
        bw.write_u_int(b.compressed_size)
        bw.write_u_short(b.flags)
    bw.write_int(len(layout.directory))
    for d in layout.directory:
        bw.write_long(d.offset)
        bw.write_long(d.size)
        bw.write_u_int(d.flags)
        bw.write_string_to_null(d.path)
    info_raw = bw.bytes
    bw.dispose()

    info_comp, _ = _compress(layout.data_flags, info_raw)
    # if uncompressed preferred for tiny headers
    if (layout.data_flags & 0x3F) == 0:
        info_comp = info_raw

    w = EndianBinaryWriter(endian=">")
    w.write_string_to_null("UnityFS")
    w.write_u_int(layout.version)
    w.write_string_to_null(layout.version_player)
    w.write_string_to_null(layout.version_engine)
    size_pos = w.Position
    w.write_long(0)
    w.write_u_int(len(info_comp))
    w.write_u_int(len(info_raw))
    w.write_u_int(layout.data_flags)
    if layout.uses_block_alignment:
        w.align_stream(16)

    if layout.blocks_info_at_end:
        if layout.block_info_need_pad:
            w.align_stream(16)
        w.write(bytes(compressed_payload))
        w.write(info_comp)
    else:
        w.write(info_comp)
        if layout.block_info_need_pad:
            w.align_stream(16)
        w.write(bytes(compressed_payload))

    end = w.Position
    w.Position = size_pos
    w.write_long(end)
    w.Position = end
    out = w.bytes
    w.dispose()
    if log:
        log(f"UnityFS 重建: {len(orig)} → {len(out)} bytes, blocks={len(new_blocks)}")
    return out


def apply_replacements(
    stream: bytearray, pairs: Sequence[Tuple[bytes, bytes]], log: LogFn = None
) -> int:
    n = 0
    ordered = sorted(pairs, key=lambda x: -len(x[0]))
    seen = set()
    for old, new in ordered:
        if old in seen or len(old) != len(new) or not old:
            continue
        c = stream.count(old)
        if c == 0:
            continue
        if c > 20:
            if log:
                log(f"  跳过过多匹配({c}): {old[:40]!r}")
            continue
        stream[:] = bytes(stream).replace(old, new)
        seen.add(old)
        n += c
    return n


def patch_unityfs_file(
    path: Path,
    pairs: Sequence[Tuple[bytes, bytes]],
    bak: Optional[Path] = None,
    log: LogFn = None,
) -> bool:
    base = bak if bak and bak.is_file() else path
    orig = base.read_bytes()
    if not orig.startswith(b"UnityFS"):
        if log:
            log(f"{path.name}: 非 UnityFS，跳过块级补丁")
        return False
    try:
        layout = parse_unityfs(orig)
    except Exception as e:
        if log:
            log(f"{path.name}: 解析 UnityFS 失败: {e}")
        return False
    try:
        stream = decompress_stream(orig, layout)
    except Exception as e:
        if log:
            log(f"{path.name}: 解压块失败: {e}")
        return False
    n = apply_replacements(stream, pairs, log)
    if n == 0:
        if log:
            log(f"{path.name}: 解压流中未命中可替换字串")
        return False
    if log:
        log(f"{path.name}: 解压流内替换 {n} 处")
    try:
        new_data = rebuild_unityfs(orig, layout, bytes(stream), log)
    except Exception as e:
        if log:
            log(f"{path.name}: 重建失败: {e}")
        return False
    path.write_bytes(new_data)
    return True
