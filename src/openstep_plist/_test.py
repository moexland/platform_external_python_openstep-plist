# Pure-Python version of _test.pyx, exposing the parser internals to the tests
# when the Cython extension modules are not available.

from .parser import (
    ParseInfo,
    line_number_strings as _line_number_strings,
    advance_to_non_space as _advance_to_non_space,
    get_slashed_char as _get_slashed_char,
    parse_unquoted_plist_string as _parse_unquoted_plist_string,
    parse_plist_string as _parse_plist_string,
)
from .util import (
    tounicode,
    is_valid_unquoted_string_char as _is_valid_unquoted_string_char,
)


class ParseContext:
    def __init__(self, string, offset=0, dict_type=dict, use_numbers=False):
        self.s = tounicode(string)
        self.dict_type = dict_type
        self.pi = ParseInfo(
            s=self.s,
            begin=0,
            curr=offset,
            end=len(self.s),
            dict_type=dict_type,
            use_numbers=bool(use_numbers),
        )


def is_valid_unquoted_string_char(c):
    # 'c' is a code point or a single character string, like Py_UCS4
    return _is_valid_unquoted_string_char(chr(c) if isinstance(c, int) else c)


def line_number_strings(s, offset=0):
    ctx = ParseContext(s, offset)
    return _line_number_strings(ctx.pi)


def advance_to_non_space(s, offset=0):
    ctx = ParseContext(s, offset)
    eof = not _advance_to_non_space(ctx.pi)
    return None if eof else s[ctx.pi.curr - ctx.pi.begin]


def get_slashed_char(s, offset=0):
    ctx = ParseContext(s, offset)
    return chr(_get_slashed_char(ctx.pi))


def parse_unquoted_plist_string(s):
    ctx = ParseContext(s)
    return _parse_unquoted_plist_string(ctx.pi)


def parse_plist_string(s, required=True):
    ctx = ParseContext(s)
    return _parse_plist_string(ctx.pi, required=required)
