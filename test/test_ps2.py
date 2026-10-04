# SPDX-License-Identifier: Apache-2.0
"""PS/2 device and host programs against independent PS/2 peers."""

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge

from chip import Chip, regs
from pe.proto import ps2

CLK, DATA = 3, 4


def odd_parity_ok(byte, p):
    return (bin(byte).count("1") + p) % 2 == 1


class Lines:
    def __init__(self, chip):
        self.chip = chip

    def clk(self):
        return (self.chip.uio() >> CLK) & 1

    def data(self):
        return (self.chip.uio() >> DATA) & 1

    def pull(self, pin, low):
        self.chip.drive_uio(pin, 0 if low else None)


class HostModel(Lines):
    """PS/2 host: reads device frames on CLK falling edges; sends commands
    with an inhibit and request-to-send."""

    def __init__(self, chip, clk_sig):
        super().__init__(chip)
        self.clk_sig = clk_sig
        self.frames = []
        self.acks = []
        self.to_send = []

    async def _wait_clk(self, level):
        while self.clk() != level:
            await RisingEdge(self.clk_sig)

    async def _receive(self):
        bits = []
        for i in range(11):
            if i:
                await self._wait_clk(1)
                await self._wait_clk(0)
            bits.append(self.data())
        byte = sum(b << j for j, b in enumerate(bits[1:9]))
        ok = bits[0] == 0 and bits[10] == 1 and odd_parity_ok(byte, bits[9])
        self.frames.append((byte, ok))
        await self._wait_clk(1)

    async def _send(self, byte):
        self.pull(CLK, True)
        await ClockCycles(self.clk_sig, 400)
        self.pull(DATA, True)
        await ClockCycles(self.clk_sig, 20)
        self.pull(CLK, False)
        await ClockCycles(self.clk_sig, 5)  # let the release reach the pin before watching CLK
        bits = [(byte >> i) & 1 for i in range(8)]
        bits += [1 - (sum(bits) & 1), 1]
        for b in bits:
            await self._wait_clk(0)
            self.pull(DATA, not b)
            await self._wait_clk(1)
        self.pull(DATA, False)
        await self._wait_clk(0)
        self.acks.append(self.data() == 0)
        await self._wait_clk(1)

    async def run(self):
        while True:
            await RisingEdge(self.clk_sig)
            if self.to_send and self.clk() and self.data():
                await self._send(self.to_send.pop(0))
            elif not self.clk():
                await self._receive()


class DeviceModel(Lines):
    """PS/2 keyboard: generates the clock; answers host requests."""

    def __init__(self, chip, clk_sig, half=150):
        super().__init__(chip)
        self.clk_sig = clk_sig
        self.half = half
        self.commands = []
        self.to_send = []

    async def _cycles(self, n):
        await ClockCycles(self.clk_sig, n)

    async def _clock_pulse(self):
        self.pull(CLK, True)
        await self._cycles(self.half)
        self.pull(CLK, False)
        await self._cycles(self.half // 2)

    async def _send(self, byte):
        bits = [0] + [(byte >> i) & 1 for i in range(8)]
        bits += [1 - (sum(bits[1:]) & 1), 1]
        for b in bits:
            self.pull(DATA, not b)
            await self._cycles(self.half // 2)
            await self._clock_pulse()
        self.pull(DATA, False)

    async def _receive(self):
        bits = []
        for _ in range(10):
            await self._clock_pulse()
            bits.append(self.data())
            await self._cycles(self.half // 2)
        byte = sum(b << j for j, b in enumerate(bits[:8]))
        ok = odd_parity_ok(byte, bits[8]) and bits[9] == 1
        self.pull(DATA, True)  # acknowledge
        await self._clock_pulse()
        self.pull(DATA, False)
        self.commands.append((byte, ok))
        if ok and byte == 0xFF:
            self.to_send += [0xFA, 0xAA]

    async def run(self):
        low_for = 0
        while True:
            await RisingEdge(self.clk_sig)
            if not self.clk():
                low_for += 1
                continue
            if low_for > 100 and not self.data():  # inhibit then request to send
                low_for = 0
                await self._cycles(self.half)
                await self._receive()
                continue
            low_for = 0
            if self.to_send:
                await self._cycles(self.half)
                await self._send(self.to_send.pop(0))


async def setup(dut, name, clkdiv, jmp_pin):
    chip = Chip(dut)
    await chip.start()
    await chip.set_quad()
    chip.pull_up((1 << CLK) | (1 << DATA))
    prog = await chip.load_program(name)
    await chip.setup_engine(0, prog, clkdiv=clkdiv, shiftctrl=regs.OUT_RIGHT | regs.IN_RIGHT,
                            out_base=DATA, set_base=DATA, in_base=DATA, jmp_pin=jmp_pin,
                            side_base=CLK, statuscfg=1)
    await chip.force_asm(0, "set pins, 1 side 1", side_count=1, side_opt=True)
    await chip.pincfg(CLK, regs.DRIVE_OPEN_DRAIN)
    await chip.pincfg(DATA, regs.DRIVE_OPEN_DRAIN)
    return chip


@cocotb.test()
async def test_ps2_chip_as_keyboard(dut):
    chip = await setup(dut, "ps2_device", clkdiv=9, jmp_pin=DATA)
    host = HostModel(chip, dut.clk)
    cocotb.start_soon(host.run())
    await chip.enable(1)
    scan = [0x1C, 0xF0, 0x1C]  # "A" pressed and released
    await chip.push(0, [ps2.device_frame(b) for b in scan])
    for _ in range(100):
        if len(host.frames) == 3:
            break
        await chip.cycles(500)
    assert host.frames == [(b, True) for b in scan], host.frames
    # The host sends "set LEDs" (0xED); the chip receives and acknowledges it.
    host.to_send.append(0xED)
    (word,) = await chip.pop_wait(0, 1)
    assert ps2.decode(word) == (0xED, True, True), hex(word)
    for _ in range(40):
        if host.acks:
            break
        await chip.cycles(200)
    assert host.acks == [True]
    # The keyboard answers 0xFA.
    await chip.push(0, [ps2.device_frame(0xFA)])
    for _ in range(100):
        if len(host.frames) == 4:
            break
        await chip.cycles(500)
    assert host.frames[3] == (0xFA, True)


@cocotb.test()
async def test_ps2_chip_as_host(dut):
    chip = await setup(dut, "ps2_host", clkdiv=1, jmp_pin=CLK)
    kbd = DeviceModel(chip, dut.clk)
    cocotb.start_soon(kbd.run())
    await chip.enable(1)
    kbd.to_send.append(0xAA)  # self-test passed
    (word,) = await chip.pop_wait(0, 1)
    assert ps2.decode(word) == (0xAA, True, True), hex(word)
    # Reset command: acknowledged on the wire, then 0xFA and 0xAA.
    await chip.push(0, [ps2.host_frame(0xFF)])
    ack, fa, aa = await chip.pop_wait(0, 3)
    assert ps2.acked(ack), hex(ack)
    assert kbd.commands == [(0xFF, True)]
    assert ps2.decode(fa) == (0xFA, True, True)
    assert ps2.decode(aa) == (0xAA, True, True)
