# Pure-Python version of util.pyx, used when the Cython extension modules
# are not available. It mirrors the Cython module function by function.


def isxdigit(c):
    return (
        (c >= 0x30 and c <= 0x39)  # 0-9
        or (c >= 0x41 and c <= 0x46)  # A-F
        or (c >= 0x61 and c <= 0x66)  # a-f
    )


def isdigit(c):
    return c >= 0x30 and c <= 0x39


def isprint(c):
    return c >= 0x20 and c <= 0x7E


def tounicode(s, encoding="ascii", errors="strict"):
    if type(s) is str:
        return s
    elif isinstance(s, str):
        return str(s)
    else:
        raise TypeError(f"Could not convert to unicode: {s!r}")


def tostr(s, encoding="ascii", errors="strict"):
    if isinstance(s, bytes):
        return s.decode(encoding, errors=errors)
    elif isinstance(s, str):
        return s
    else:
        raise TypeError(f"Could not convert to str: {s!r}")


VALID_UNQUOTED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$/:.-"
)


def is_valid_unquoted_string_char(x):
    # 'x' is a single character string
    return x in VALID_UNQUOTED_CHARS


def is_high_surrogate(ch):
    return ch >= 0xD800 and ch <= 0xDBFF


def is_low_surrogate(ch):
    return ch >= 0xDC00 and ch <= 0xDFFF


def unicode_scalar_from_surrogates(high, low):
    return (high - 0xD800) * 0x400 + low - 0xDC00 + 0x10000


def high_surrogate_from_unicode_scalar(scalar):
    return ((scalar - 0x10000) // 0x400) + 0xD800


def low_surrogate_from_unicode_scalar(scalar):
    return (scalar - 0x10000) % 0x400 + 0xDC00
