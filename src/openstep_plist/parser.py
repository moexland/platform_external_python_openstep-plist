# Pure-Python version of parser.pyx, used when the Cython extension modules
# are not available. It mirrors the Cython module function by function.
#
# The Cython code works on a NUL-terminated buffer of UCS4 code points and in
# a few places reads the terminator at the end of the input; here positions
# are indices into the string and reading at the end yields "\0" instead.
# Runs of whitespace, unquoted string characters and quoted string content
# are scanned with regular expressions bounded by the end of the input.

import re

from .util import (
    tounicode,
    isxdigit,
    isdigit,
    is_valid_unquoted_string_char,
    is_high_surrogate,
    is_low_surrogate,
    unicode_scalar_from_surrogates,
)


class ParseError(Exception):
    pass


class ParseInfo:
    __slots__ = ("s", "begin", "curr", "end", "dict_type", "use_numbers")

    def __init__(self, s, begin, curr, end, dict_type, use_numbers):
        self.s = s
        self.begin = begin
        self.curr = curr
        self.end = end
        self.dict_type = dict_type
        self.use_numbers = use_numbers


UNQUOTED_STRING = 0
UNQUOTED_INTEGER = 1
UNQUOTED_FLOAT = 2

_WHITESPACE = re.compile("[\t\n\x0b\x0c\r   ]*")
_WHITESPACE_OR_SLASH = frozenset("\t\n\x0b\x0c\r   /")
_LINE_COMMENT = re.compile("[^\n\r  ]*")
_UNQUOTED_CHARS = re.compile(r"[a-zA-Z0-9_$/:.\-]*")
_QUOTED_CHARS = {
    '"': re.compile(r'[^"\\]*'),
    "'": re.compile(r"[^'\\]*"),
}
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]*)?")
_DATA_WHITESPACE = frozenset(" \n\t\r  ")


def _char_at(pi, i):
    # the Cython buffer is NUL-terminated
    return pi.s[i] if i < pi.end else "\0"


def line_number_strings(pi):
    # warning: doesn't have a good idea of Unicode line separators
    s = pi.s
    return (
        1
        + s.count("\n", pi.begin, pi.curr)
        + s.count("\r", pi.begin, pi.curr)
        - s.count("\r\n", pi.begin, pi.curr)
    )


def advance_to_non_space(pi):
    """Returns true if the advance found something that's not whitespace
    before the end of the buffer, false otherwise.
    """
    s = pi.s
    end = pi.end
    curr = pi.curr
    if curr < end and s[curr] not in _WHITESPACE_OR_SLASH:
        return True
    while curr < end:
        # tab, newline, vt, form feed, carriage return, space, line and
        # paragraph separators
        curr = _WHITESPACE.match(s, curr, end).end()
        if curr >= end:
            break
        if s[curr] != "/":
            pi.curr = curr
            return True
        if curr + 1 >= end:
            # whoops; back up and return
            pi.curr = curr
            return True
        ch = s[curr + 1]
        if ch == "/":
            # go to end of // comment line
            curr = _LINE_COMMENT.match(s, curr + 2, end).end()
        elif ch == "*":
            # handle C-style comments /* ... */
            close = s.find("*/", curr + 2, end)
            curr = end if close < 0 else close + 2
        else:
            pi.curr = curr
            return True
    pi.curr = curr
    return False


# Table mapping from NextStep Encoding to Unicode characters, used
# for decoding octal escaped character codes within quoted plist strings.
# Since the first 128 characters (0x0 - 0x7f) are identical to ASCII
# and Unicode, the table only maps NextStep range from 0x80 - 0xFF.
# Source: ftp://ftp.unicode.org/Public/MAPPINGS/VENDORS/NEXT/NEXTSTEP.TXT
# fmt: off
NEXT_STEP_DECODING_TABLE = (
    0xA0, 0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC7, 0xC8, 0xC9,
    0xCA, 0xCB, 0xCC, 0xCD, 0xCE, 0xCF, 0xD0, 0xD1, 0xD2, 0xD3,
    0xD4, 0xD5, 0xD6, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xB5,
    0xD7, 0xF7, 0xA9, 0xA1, 0xA2, 0xA3, 0x2044, 0xA5, 0x192, 0xA7,
    0xA4, 0x2019, 0x201C, 0xAB, 0x2039, 0x203A, 0xFB01, 0xFB02, 0xAE, 0x2013,
    0x2020, 0x2021, 0xB7, 0xA6, 0xB6, 0x2022, 0x201A, 0x201E, 0x201D, 0xBB,
    0x2026, 0x2030, 0xAC, 0xBF, 0xB9, 0x2CB, 0xB4, 0x2C6, 0x2DC, 0xAF,
    0x2D8, 0x2D9, 0xA8, 0xB2, 0x2DA, 0xB8, 0xB3, 0x2DD, 0x2DB, 0x2C7,
    0x2014, 0xB1, 0xBC, 0xBD, 0xBE, 0xE0, 0xE1, 0xE2, 0xE3, 0xE4,
    0xE5, 0xE7, 0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xC6, 0xED, 0xAA,
    0xEE, 0xEF, 0xF0, 0xF1, 0x141, 0xD8, 0x152, 0xBA, 0xF2, 0xF3,
    0xF4, 0xF5, 0xF6, 0xE6, 0xF9, 0xFA, 0xFB, 0x131, 0xFC, 0xFD,
    0x142, 0xF8, 0x153, 0xDF, 0xFE, 0xFF, 0xFFFD, 0xFFFD,
)
# fmt: on


def get_slashed_char(pi):
    """Returns the code point of the escape sequence following a backslash."""
    ch = ord(_char_at(pi, pi.curr))

    pi.curr += 1
    if ch >= 0x30 and ch <= 0x37:  # '0' to '7'
        num = ch - 0x30
        # three digits maximum to avoid reading \000 followed by 5 as \5 !
        ch = ord(_char_at(pi, pi.curr))
        if ch >= 0x30 and ch <= 0x37:
            pi.curr += 1
            num = ((num << 3) + ch - 0x30) & 0xFF
            if pi.curr < pi.end:
                ch = ord(pi.s[pi.curr])
                if ch >= 0x30 and ch <= 0x37:
                    pi.curr += 1
                    num = ((num << 3) + ch - 0x30) & 0xFF
            if num < 128:  # ascii
                codepoint = num
            else:
                codepoint = NEXT_STEP_DECODING_TABLE[num - 128]
            return codepoint
    elif ch == 0x55:  # 'U'
        unum = 0
        num_digits = 4
        while pi.curr < pi.end and num_digits > 0:
            ch = ord(pi.s[pi.curr])
            if ch < 128 and isxdigit(ch):
                pi.curr += 1
                unum = (unum << 4) + int(chr(ch), 16)
            num_digits -= 1
        return unum
    elif ch == 0x61:  # 'a'
        return 0x07
    elif ch == 0x62:  # 'b'
        return 0x08
    elif ch == 0x66:  # 'f'
        return 0x0C
    elif ch == 0x6E:  # 'n'
        return 0x0A
    elif ch == 0x72:  # 'r'
        return 0x0D
    elif ch == 0x74:  # 't'
        return 0x09
    elif ch == 0x76:  # 'v'
        return 0x0B
    elif ch == 0x22:  # '"'
        return 0x22
    elif ch == 0x0A:  # '\n'
        return 0x0A

    return ch


def parse_quoted_plist_string(pi, quote):
    s = pi.s
    end = pi.end
    quoted_chars = _QUOTED_CHARS[quote]
    string = []
    mark = pi.curr
    while pi.curr < end:
        pi.curr = quoted_chars.match(s, pi.curr, end).end()
        if pi.curr >= end or s[pi.curr] == quote:
            break
        # backslash
        string.append(s[mark : pi.curr])
        pi.curr += 1
        ch = get_slashed_char(pi)
        # Parse two successive \UXXXX escape sequences as one surrogate pair
        # representing a "supplementary" Unicode scalar value.
        if is_high_surrogate(ch) and pi.curr < end and s[pi.curr] == "\\":
            tmp = pi.curr
            pi.curr += 1
            ch2 = get_slashed_char(pi)
            if is_low_surrogate(ch2):
                ch = unicode_scalar_from_surrogates(high=ch, low=ch2)
            else:
                # XXX maybe we should raise here instead of letting this
                # lone high surrogate (not followed by a low) pass through?
                pi.curr = tmp
        string.append(chr(ch))
        mark = pi.curr
    if end <= pi.curr:
        raise ParseError(
            "Unterminated quoted string starting on line %d"
            % line_number_strings(pi)
        )
    if mark != pi.curr:
        string.append(s[mark : pi.curr])
    # Advance past the quote character before returning
    pi.curr += 1

    return "".join(string)


def string_to_number(s, required=True):
    """Convert string s to either int or float.
    Raises ValueError if the string is not a number.
    """
    if not isinstance(s, str):
        raise TypeError(
            f"Argument 's' has incorrect type (expected str, got {type(s).__name__})"
        )

    if s:
        kind = get_unquoted_string_type(s)
        if kind == UNQUOTED_FLOAT:
            return float(s)
        elif kind == UNQUOTED_INTEGER:
            return int(s)

    if required:
        raise ValueError(f"Could not convert string to float or int: {s!r}")
    else:
        return s


def get_unquoted_string_type(s):
    """Check if string starts with a digit, or '-' followed by a digit, and
    if it contains a decimal point '.'.
    Return 0 if string cannot contain a number, 1 if it contains an
    integer, and 2 if it contains a float.
    """
    # NOTE: floats in scientific notation (e.g. 1e-5) or starting with a
    # "." (e.g. .05) are not handled here, but are treated as strings.
    if _NUMBER.fullmatch(s) is None:
        return UNQUOTED_STRING
    return UNQUOTED_FLOAT if "." in s else UNQUOTED_INTEGER


def parse_unquoted_plist_string(pi, ensure_string=False):
    mark = pi.curr
    pi.curr = _UNQUOTED_CHARS.match(pi.s, mark, pi.end).end()
    if pi.curr != mark:
        s = pi.s[mark : pi.curr]

        if not ensure_string and pi.use_numbers:
            kind = get_unquoted_string_type(s)
            if kind == UNQUOTED_FLOAT:
                return float(s)
            elif kind == UNQUOTED_INTEGER:
                return int(s)

        return s

    raise ParseError("Unexpected EOF")


def parse_plist_string(pi, required=True):
    if not advance_to_non_space(pi):
        if required:
            raise ParseError("Unexpected EOF while parsing string")
    ch = _char_at(pi, pi.curr)
    if ch == "'" or ch == '"':
        pi.curr += 1
        return parse_quoted_plist_string(pi, ch)
    elif is_valid_unquoted_string_char(ch):
        return parse_unquoted_plist_string(pi, ensure_string=True)
    else:
        if required:
            raise ParseError(
                "Invalid string character at line %d: %r"
                % (line_number_strings(pi), ch)
            )
    return None


def parse_plist_array(pi):
    result = []
    tmp = parse_plist_object(pi, required=False)
    while tmp is not None:
        result.append(tmp)
        found_char = advance_to_non_space(pi)
        if not found_char:
            raise ParseError(
                "Missing ',' for array at line %d" % line_number_strings(pi)
            )
        if pi.s[pi.curr] != ",":
            tmp = None
        else:
            pi.curr += 1
            tmp = parse_plist_object(pi, required=False)
    found_char = advance_to_non_space(pi)
    if not found_char or pi.s[pi.curr] != ")":
        raise ParseError(
            "Expected terminating ')' for array at line %d" % line_number_strings(pi)
        )
    pi.curr += 1
    return result


def parse_plist_dict_content(pi):
    result = pi.dict_type()
    key = parse_plist_string(pi, required=False)

    while key is not None:
        found_char = advance_to_non_space(pi)
        if not found_char:
            raise ParseError("Missing ';' on line %d" % line_number_strings(pi))

        ch = pi.s[pi.curr]
        if ch == ";":
            # This is a 'strings resource' file using the shortcut format,
            # although this check here really applies to all plists
            value = key
        elif ch == "=":
            pi.curr += 1
            value = parse_plist_object(pi, required=True)
        else:
            raise ParseError(
                "Unexpected character after key at line %d: %r"
                % (line_number_strings(pi), ch)
            )
        result[key] = value
        key = None
        value = None
        found_char = advance_to_non_space(pi)
        if found_char and pi.s[pi.curr] == ";":
            pi.curr += 1
            key = parse_plist_string(pi, required=False)
        else:
            raise ParseError("Missing ';' on line %d" % line_number_strings(pi))

    return result


def parse_plist_dict(pi):
    result = parse_plist_dict_content(pi)
    if not advance_to_non_space(pi) or pi.s[pi.curr] != "}":
        raise ParseError(
            "Expected terminating '}' for dictionary at line %d"
            % line_number_strings(pi)
        )
    pi.curr += 1
    return result


def from_hex_digit(ch):
    # 'ch' is an unsigned char (0-255)
    if isdigit(ch):
        return ch - 0x30
    if ch >= 0x61 and ch <= 0x66:  # 'a' to 'f'
        return ch - 0x61 + 10
    elif ch >= 0x41 and ch <= 0x46:  # 'A' to 'F'
        return ch - 0x41 + 10
    return 0xFF  # Just choose a large number for the error code


def get_data_bytes(pi, result):
    s = pi.s
    while pi.curr < pi.end:
        ch1 = s[pi.curr]
        if ch1 == ">":
            return 0
        # the Cython code casts the code point to unsigned char
        first = from_hex_digit(ord(ch1) & 0xFF)
        if first != 0xFF:
            # if the first char is a hex, then try to read a second hex
            pi.curr += 1
            if pi.curr >= pi.end:
                raise ParseError(
                    "Malformed data byte group at line %d: uneven length"
                    % line_number_strings(pi)
                )
            ch2 = s[pi.curr]
            if ch2 == ">":
                raise ParseError(
                    "Malformed data byte group at line %d: uneven length"
                    % line_number_strings(pi)
                )
            second = from_hex_digit(ord(ch2) & 0xFF)
            if second == 0xFF:
                raise ParseError(
                    "Malformed data byte group at line %d: invalid hex digit: %r"
                    % (line_number_strings(pi), ch2)
                )
            result.append((first << 4) + second)
            pi.curr += 1
        elif ch1 in _DATA_WHITESPACE:
            pi.curr += 1
        else:
            raise ParseError(
                "Malformed data byte group at line %d: invalid hex digit: %r"
                % (line_number_strings(pi), ch1)
            )
    return 0


def parse_plist_data(pi):
    data = bytearray()
    get_data_bytes(pi, data)
    if _char_at(pi, pi.curr) == ">":
        pi.curr += 1  # move past '>'
        return bytes(data)
    else:
        raise ParseError(
            "Expected terminating '>' for data at line %d" % line_number_strings(pi)
        )


def parse_plist_object(pi, required=True):
    if not advance_to_non_space(pi):
        if required:
            raise ParseError("Unexpected EOF while parsing plist")
    ch = _char_at(pi, pi.curr)
    pi.curr += 1
    if ch == "{":
        return parse_plist_dict(pi)
    elif ch == "(":
        return parse_plist_array(pi)
    elif ch == "<":
        return parse_plist_data(pi)
    elif ch == "'" or ch == '"':
        return parse_quoted_plist_string(pi, ch)
    elif is_valid_unquoted_string_char(ch):
        pi.curr -= 1
        return parse_unquoted_plist_string(pi)
    else:
        pi.curr -= 1  # must back off the character we just read
        if required:
            raise ParseError(
                "Unexpected character at line %d: %r" % (line_number_strings(pi), ch)
            )
    return None


def loads(string, dict_type=dict, use_numbers=False):
    s = tounicode(string)
    result = None

    pi = ParseInfo(
        s=s,
        begin=0,
        curr=0,
        end=len(s),
        dict_type=dict_type,
        use_numbers=bool(use_numbers),
    )

    begin = pi.curr
    if not advance_to_non_space(pi):
        # a file consisting of only whitespace or empty is defined as an
        # empty dictionary
        result = {}
    else:
        result = parse_plist_object(pi, required=True)
        if result:
            if advance_to_non_space(pi):
                if not isinstance(result, str):
                    raise ParseError(
                        "Junk after plist at line %d" % line_number_strings(pi)
                    )
                else:
                    # keep parsing for a 'strings resource' file: it looks like
                    # a dictionary without the opening/closing curly braces
                    pi.curr = begin
                    result = parse_plist_dict_content(pi)

    return result


def load(fp, dict_type=dict, use_numbers=False):
    return loads(fp.read(), dict_type=dict_type, use_numbers=use_numbers)
