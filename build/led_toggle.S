/* Ultimate 2C Wireless v1.09 ONLY. Experimental, not hardware-tested.
 * Hook: original call at firmware 0xd4f0 to ring driver 0xd36c.
 * r0/r1/r2 are original state/pin/counter arguments.
 * Private state is 4 bytes appended to the zero-initialized BSS.
 * flags[0]=disabled, flags[1]=armed, flags[2]=hold samples.
 * Both buttons must be released before arming; 3 consecutive LED ticks
 * with both pressed toggle once. Other button presses are untouched.
 */
.text
.global led_gate
led_gate:
    tpush {r4, r5, r6, lr}
    tloadr r3, [pc, #(raw_buttons - . - 2)]
    tloadr r3, [r3, #0]
    tloadr r4, [pc, #(pair_mask - . - 2)]
    tand r3, r4
    tloadr r5, [pc, #(private_state - . - 2)]
    tcmp r3, #0
    tjeq released
    tcmp r3, r4
    tjne partial
    tloadrb r6, [r5, #1]
    tcmp r6, #0
    tjeq apply_gate
    tloadrb r6, [r5, #2]
    tadd r6, #1
    tstorerb r6, [r5, #2]
    tcmp r6, #3
    tjne apply_gate
    tloadrb r6, [r5, #0]
    tmov r4, #1
    txor r6, r4
    tstorerb r6, [r5, #0]
    tmov r6, #0
    tstorerb r6, [r5, #1]
    tstorerb r6, [r5, #2]
    tj apply_gate
released:
    tmov r6, #1
    tstorerb r6, [r5, #1]
partial:
    tmov r6, #0
    tstorerb r6, [r5, #2]
apply_gate:
    tloadrb r6, [r5, #0]
    tcmp r6, #0
    tjeq call_driver
    tmov r0, #0
call_driver:
    tjl original_ring_driver
    tpop {r4, r5, r6, pc}
.balign 4
raw_buttons: .word 0x0084344c
pair_mask: .word 0x06000000
private_state: .word 0x00843cf0
