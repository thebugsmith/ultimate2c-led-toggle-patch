#!/usr/bin/env python3
"""Instruction-level tests of the emitted hook, not a device emulator.

Implements only the TC32 instructions exercised by this patch and the cold
startup BSS-clear loop. The original LED driver is a deliberately clobbering
call stub: tests verify its arguments, not physical GPIO or radio behavior.
"""
import argparse
import random
import struct
import zlib
from pathlib import Path
from build_patch import build, file_offset, HERE, HOOK, CAVE, STATE

PAIR = 0x06000000
RAW = 0x84344c

def signed(value, bits):
    return value-(1<<bits) if value & (1<<(bits-1)) else value

class CPU:
    def __init__(self, image):
        self.image = image
        self.r = [0]*16
        self.mem = {}
        self.zero = False
        self.negative = False
        self.overflow = False
        self.writes = []
        self.driver_calls = []

    def read(self, address, n=4):
        if 0x6000 <= address < 0x18400:
            return int.from_bytes(self.image[file_offset(address):file_offset(address)+n], 'little')
        if not 0x800000 <= address < 0x850000:
            raise AssertionError(f'Unexpected read {address:#x}')
        return sum(self.mem.get(address+i,0)<<(8*i) for i in range(n))

    def write(self, address, value, n=4):
        if not 0x840000 <= address < 0x844000:
            raise AssertionError(f'Unexpected write {address:#x}')
        self.writes.append((address,n))
        for i in range(n):
            self.mem[address+i] = (value>>(8*i))&255

    def compare(self,a,b):
        result=(a-b)&0xffffffff
        self.zero=result==0
        self.negative=bool(result&0x80000000)
        self.overflow=bool((a^b)&(a^result)&0x80000000)

    def run(self, stop, limit=20000):
        for _ in range(limit):
            pc=self.r[15]
            if pc==stop:
                return
            if pc==0xd36c:
                self.driver_calls.append(tuple(self.r[:3]))
                # The original driver is permitted to overwrite r0-r3 and flags.
                self.r[:4]=[0xdead0000+i for i in range(4)]
                self.zero=False
                self.negative=True
                self.r[15]=self.r[14]&~1
                continue
            op=self.read(pc,2)
            self.r[15]=pc+2
            if op&0xf800==0x9000:
                op2=self.read(pc+2,2)
                assert op2&0xf800==0x9800
                offset=(signed(op&0x7ff,11)<<12)+((op2&0x7ff)<<1)
                self.r[14]=(pc+4)|1
                self.r[15]=(pc+4+offset)&0xffffffff
            elif op&0xfe00==0x6400:
                regs=[r for r in range(8) if op&(1<<r)]
                if op&0x100: regs.append(14)
                self.r[13]-=4*len(regs)
                for i,r in enumerate(regs): self.write(self.r[13]+4*i,self.r[r])
            elif op&0xfe00==0x6c00:
                regs=[r for r in range(8) if op&(1<<r)]
                if op&0x100: regs.append(15)
                for i,r in enumerate(regs): self.r[r]=self.read(self.r[13]+4*i)
                self.r[13]+=4*len(regs)
                self.r[15]&=~1
            elif op&0xf800==0x0800:
                self.r[(op>>8)&7]=self.read(((pc+4)&~3)+(op&255)*4)
            elif op&0xf800 in (0x5800,0x5000,0x4800,0x4000):
                kind=op&0xf800
                size=1 if kind in (0x4800,0x4000) else 4
                reg=op&7; base=(op>>3)&7; imm=((op>>6)&31)*size
                address=self.r[base]+imm
                if kind in (0x5800,0x4800): self.r[reg]=self.read(address,size)
                else: self.write(address,self.r[reg],size)
            elif op&0xf800==0xa000:
                self.r[(op>>8)&7]=op&255
                self.zero=(op&255)==0
            elif op&0xf800==0xa800:
                self.compare(self.r[(op>>8)&7],op&255)
            elif op&0xf800==0xb000:
                r=(op>>8)&7
                self.r[r]=(self.r[r]+(op&255))&0xffffffff
                self.zero=self.r[r]==0
            elif op&0xffc0 in (0x0000,0x0040,0x0280):
                rd=op&7; rs=(op>>3)&7; kind=op&0xffc0
                if kind==0x0280: self.compare(self.r[rd],self.r[rs])
                else:
                    self.r[rd] = self.r[rd]&self.r[rs] if kind==0 else self.r[rd]^self.r[rs]
                    self.zero=self.r[rd]==0
            elif op&0xff00 in (0xc000,0xc100,0xca00):
                kind=op&0xff00
                take=(self.zero if kind==0xc000 else not self.zero if kind==0xc100 else self.negative==self.overflow)
                if take: self.r[15]=pc+4+signed(op&255,8)*2
            elif op&0xf800==0x8000:
                self.r[15]=pc+4+signed(op&0x7ff,11)*2
            else:
                raise AssertionError(f'Unsupported instruction {op:04x} at {pc:x}')
        raise AssertionError('Instruction limit exceeded')

    def tick(self, buttons, state=1, pin=0x180, counter=0x843452):
        self.write(RAW,buttons)
        self.writes=[]
        self.driver_calls=[]
        self.r=[0x100000+i for i in range(16)]
        self.r[:3]=[state,pin,counter]
        self.r[13]=0x843f00
        self.r[15]=HOOK
        saved=self.r.copy()
        self.run(HOOK+4)
        assert self.r[4:14]==saved[4:14], 'Callee-saved register or stack corruption'
        assert self.read(RAW)==buttons, 'Input state modified'
        assert len(self.driver_calls)==1
        args=self.driver_calls[0]
        assert args[1:]==(pin,counter), 'Wrong LED/counter arguments'
        for addr,n in self.writes:
            assert STATE<=addr and addr+n<=STATE+4 or saved[13]-16<=addr and addr+n<=saved[13]
        return args[0]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock',type=Path)
    parser.add_argument('patched',type=Path)
    args=parser.parse_args()
    stock=args.stock.read_bytes()
    expected,meta=build(stock,(HERE/'led_toggle.bin').read_bytes(),(HERE/'hook.bin').read_bytes())
    image=args.patched.read_bytes()
    assert image==expected, 'Patched image differs from reproducible build'
    assert len(image)==len(stock)
    assert zlib.crc32(image[28:28+0x12274])==0xffffffff
    assert zlib.crc32(image[28:])==0xffffffff
    for addr in (0xd4fc,0xd50a):
        o=file_offset(addr)
        assert image[o:o+4]==stock[o:o+4], 'Another LED call was changed'
    cpu=CPU(image)
    # Execute the original BSS-clearing loop with the updated literal boundary.
    for a in range(0x842fb0,STATE+8): cpu.mem[a]=0xa5
    cpu.r[15]=0x612a
    cpu.run(0x613a)
    assert all(cpu.mem[a]==0 for a in range(0x842fb0,STATE+4))
    assert cpu.read(STATE+4)==0xa5a5a5a5, 'BSS clear overran reserved word'
    for _ in range(200): assert cpu.tick(PAIR)==1, 'Boot-held pair toggled without release'
    assert cpu.tick(0)==1
    assert cpu.tick(PAIR)==1
    assert cpu.tick(PAIR)==1
    assert cpu.tick(PAIR)==0
    for _ in range(1000): assert cpu.tick(PAIR)==0, 'Held keys repeated'
    for _ in range(30):
        assert cpu.tick(1<<25)==0
        assert cpu.tick(PAIR)==0, 'Partial release rearmed toggle'
    assert cpu.tick(0)==0
    assert cpu.tick(PAIR)==0
    assert cpu.tick(1<<26)==0  # Interrupt the hold; count must reset.
    assert cpu.tick(PAIR)==0
    assert cpu.tick(PAIR)==0
    assert cpu.tick(PAIR)==1
    cpu.tick(0)
    for key in [1<<i for i in range(32)]+[0x6,0xc09]:
        for _ in range(8): assert cpu.tick(key)==1, f'Unrelated key/combo toggled: {key:#x}'
    for original_state in range(4):
        assert cpu.tick(0,original_state)==original_state
    for _ in range(3): cpu.tick(PAIR|0x1)
    for original_state in range(4):
        assert cpu.tick(PAIR,original_state)==0
    # Independent model used over deterministic random single/dual/other keys.
    disabled,armed,count=1,0,0
    rng=random.Random(20261107)
    choices=[0,1<<25,1<<26,PAIR,PAIR|9,0xc09,0x6]
    for _ in range(5000):
        buttons=rng.choice(choices)
        original_state=rng.randrange(4)
        pair=buttons&PAIR
        if not pair:
            armed,count=1,0
        elif pair!=PAIR:
            count=0
        elif armed:
            count+=1
            if count==3:
                disabled^=1
                armed,count=0,0
        actual=cpu.tick(buttons,original_state)
        assert actual==(0 if disabled else original_state)
        assert [cpu.read(STATE+i,1) for i in range(3)]==[disabled,armed,count]
    # Guard rejects firmware other than the precise base image.
    wrong=bytearray(stock); wrong[0]^=1
    try: build(bytes(wrong),(HERE/'led_toggle.bin').read_bytes(),(HERE/'hook.bin').read_bytes())
    except ValueError: pass
    else: raise AssertionError('Incorrect base was accepted')
    print('PASS: exact rebuild, length, diff scope, both CRCs, cold BSS initialization')
    print('PASS: emitted TC32 hook/cave, return targets, stack/register preservation')
    print('PASS: startup-held pair, 3-sample hold, long hold, full-release rearm, interrupted hold')
    print('PASS: single keys, other combos, normal LED states, 5,000 randomized ticks')
    print('NOT TESTED: physical button identification, GPIO behavior, flashing, boot, radio modes or timing')
    print('SHA-256:',meta['output_sha256'])

if __name__=='__main__': main()
