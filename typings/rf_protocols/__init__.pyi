"""Type stub for the external `rf_protocols` package.

Not a real installable dependency in this dev environment - signal_transport.py
already has a runtime fallback for when it's missing. Stubbed only so editors/
type checkers can resolve `RadioFrequencyCommand`. Not used at runtime.
"""

class RadioFrequencyCommand:
    frequency: int
    modulation: str
    repeat_count: int

    def __init__(
        self,
        *,
        frequency: int,
        timings: list[int],
        modulation: str = ...,
        repeat_count: int = ...,
    ) -> None: ...
    def get_raw_timings(self) -> list[int]: ...
