from . import Command

class Samsung32Command(Command):
    def __init__(self, *, address: int, command: int, modulation: int) -> None: ...
