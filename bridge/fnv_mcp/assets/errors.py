"""The one error the asset makers raise: a source or an option they cannot use (an fnv_* tool answers it as
INVALID_ARGUMENT with the message)."""


class AssetMakeError(ValueError):
    pass
