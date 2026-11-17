"""Turning a number of bytes into something a person would say out loud."""

KB = 1024
MB = 1024 * 1024
GB = 1024 * 1024 * 1024


def human_size(size: int) -> str:
    """A size with one unit and no more precision than is any use.

    One decimal place, dropped when it would be a nought: a round quarter of a
    gigabyte reads better as 250 MB than as 250.0 MB.
    """
    if size < KB:
        return f"{size} bytes"
    if size < MB:
        return _trim(size / KB, "KB")
    if size < GB:
        return _trim(size / MB, "MB")
    return _trim(size / GB, "GB")


def _trim(value: float, unit: str) -> str:
    rounded = round(value, 1)
    if rounded == int(rounded):
        return f"{int(rounded)} {unit}"
    return f"{rounded} {unit}"
