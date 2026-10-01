class JobTimeoutError(TimeoutError):
    pass


class JobProcessError(RuntimeError):
    pass


class JobOwnershipLost(RuntimeError):
    pass
