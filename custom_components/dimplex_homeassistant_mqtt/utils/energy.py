"""Combine independently reported energy registers using their cached values."""


class EnergyCounter:
    def __init__(self, registers):
        self.registers = registers
        self.parts = {}
        self.total = None

    def update(self, values):
        """Update received parts; unseen parts contribute zero to the total."""
        for register in self.registers:
            if register not in values:
                continue
            try:
                value = int(values[register])
            except (TypeError, ValueError, OverflowError):
                continue
            if 0 <= value < 10_000:
                self.parts[register] = value

        if self.parts:
            low, middle, high = (self.parts.get(key, 0) for key in self.registers)
            self.total = high * 100_000_000 + middle * 10_000 + low
        return self.total
