"""Type stub for the external `infrared_protocols` package.

Not a real installable dependency in this dev environment - stubbed only so
editors/type checkers can resolve `Command` and its subclasses. Not used at
runtime.
"""

class Command:
    modulation: int

    def __init__(self, *, modulation: int) -> None: ...
    def get_raw_timings(self) -> list[int]: ...
