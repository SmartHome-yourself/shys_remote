from . import Command

class RC5Command(Command):
    def __init__(self, *, address: int, command: int, modulation: int) -> None: ...
