from . import Command

class SonyCommand(Command):
    def __init__(
        self, *, address: int, address_bits: int, command: int, modulation: int
    ) -> None: ...
