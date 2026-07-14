from . import Command

class SharpCommand(Command):
    def __init__(self, *, address: int, command: int, modulation: int) -> None: ...
