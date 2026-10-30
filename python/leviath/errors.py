"""Exception types for the Leviath client."""


class LeviathError(Exception):
    """Base for all Leviath client errors."""


class LeviathTimeoutError(LeviathError):
    """A run did not reach a terminal status within the timeout.

    The run is still going — the timeout only stopped waiting.
    Call cancel() to stop it, or run_and_wait() again with the same run_id.
    """

    def __init__(self, run_id: str, timeout_secs: float, last_status: str):
        self.run_id = run_id
        self.timeout_secs = timeout_secs
        self.last_status = last_status
        super().__init__(
            f"Run {run_id} did not finish within {timeout_secs}s "
            f"(last status: {last_status}). The run continues; "
            f"call cancel() or run_and_wait() again."
        )


class LeviathSpawnError(LeviathError):
    """The `lev run` command failed to spawn a run."""

    def __init__(self, message: str):
        super().__init__(message)


class LeviathDaemonError(LeviathError):
    """The daemon is unreachable."""

    def __init__(self, message: str):
        super().__init__(message)