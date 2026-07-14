from . import Command

class NECCommand(Command):
    def __init__(self, *, address: int, command: int, modulation: int) -> None: ...
