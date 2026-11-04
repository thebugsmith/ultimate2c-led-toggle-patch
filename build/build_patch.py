#!/usr/bin/env python3
"""Build the experimental L4+R4 ring toggle for the exact supplied v1.09.

The assembler output is included alongside the sources and read from disk.
"""
import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

STOCK_SHA256 = '87349e603bb92d6ae9ff6defb6f32b10de2f2d3252fe4e442fa7993f982f2a5a'
HERE = Path(__file__).resolve().parent
WRAPPER = 0x1c
BASE = 0x6000
HOOK = 0xd4f0
CAVE = 0x18274
STATE = 0x843cf0
CAVE_HEX = '7065120b1b58120c2300120d00ab11c0a30211c16e4800ae10c0ae4801b6ae4003ae0bc12e4801a466002e4000a66e40ae40038001a66e4000a6ae402e4800ae00c000a0f5975898706dc0464c34840000000006f03c8400'
HOOK_HEX = '0a90c09e'

def file_offset(address):
    return address - BASE + WRAPPER

def build(stock, cave, hook):
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
        raise ValueError('Only the exact supplied stock v1.09 is supported; input hash differs')
    if cave != bytes.fromhex(CAVE_HEX) or hook != bytes.fromhex(HOOK_HEX):
        raise ValueError('Assembly bytes differ from the reviewed build')
    data = bytearray(stock)
    assert len(data) == 0x1241c
    assert struct.unpack_from('<4I', data) == (109, BASE, 0x12400, 0x301b)
    assert struct.unpack_from('<I', data, file_offset(0x617c))[0] == STATE
    assert data[file_offset(HOOK):file_offset(HOOK)+4] == bytes.fromhex('ff973c9f')
    assert not any(data[file_offset(CAVE):])
    assert file_offset(CAVE)+len(cave) <= len(data)-4
    length = struct.unpack_from('<I', data, WRAPPER+0x18)[0]
    assert length == 0x12274
    assert zlib.crc32(data[WRAPPER:WRAPPER+length]) == 0xffffffff
    assert zlib.crc32(data[WRAPPER:]) == 0xffffffff
    # Extend the existing cold-start zeroing boundary by one aligned word.
    struct.pack_into('<I', data, file_offset(0x617c), STATE+4)
    data[file_offset(HOOK):file_offset(HOOK)+4] = hook
    data[file_offset(CAVE):file_offset(CAVE)+len(cave)] = cave
    # Preserve the original length-defined Telink CRC and whole-payload residue.
    inner_offset = WRAPPER+length-4
    struct.pack_into('<I', data, inner_offset, ~zlib.crc32(data[WRAPPER:inner_offset]) & 0xffffffff)
    struct.pack_into('<I', data, len(data)-4, ~zlib.crc32(data[WRAPPER:-4]) & 0xffffffff)
    assert zlib.crc32(data[WRAPPER:WRAPPER+length]) == 0xffffffff
    assert zlib.crc32(data[WRAPPER:]) == 0xffffffff
    assert data[:WRAPPER] == stock[:WRAPPER]
    allowed = [(file_offset(0x617c),4),(file_offset(HOOK),4),
               (file_offset(CAVE),len(cave)),(inner_offset,4),(len(data)-4,4)]
    for i,(a,b) in enumerate(zip(stock,data)):
        if a != b and not any(start <= i < start+size for start,size in allowed):
            raise AssertionError(f'Unexpected changed byte at {i:#x}')
    metadata = dict(experimental=True, hardware_tested=False,
        input_sha256=STOCK_SHA256, output_sha256=hashlib.sha256(data).hexdigest(),
        intended_shortcut='L4 + R4 (physical button assignment needs hardware confirmation)',
        raw_mask='0x06000000', cave_address=hex(CAVE), cave_length=len(cave),
        hook_address=hex(HOOK), state_address=hex(STATE), state_length=4,
        hold_samples=3, internal_crc=hex(struct.unpack_from('<I',data,inner_offset)[0]),
        suffix_crc=hex(struct.unpack_from('<I',data,len(data)-4)[0]),
        whole_payload_crc32=hex(zlib.crc32(data[WRAPPER:])),
        changes=[dict(file_offset=hex(start),length=size,
                      before=stock[start:start+size].hex(),after=data[start:start+size].hex())
                 for start,size in allowed])
    return bytes(data), metadata

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    manifest = args.output.with_suffix('.json')
    if args.output.exists() or manifest.exists():
        raise SystemExit('Refusing to overwrite an existing output or manifest')
    data, metadata = build(args.stock.read_bytes(), (HERE/'led_toggle.bin').read_bytes(), (HERE/'hook.bin').read_bytes())
    args.output.write_bytes(data)
    manifest.write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata,indent=2))

if __name__ == '__main__':
    main()
