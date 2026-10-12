# Pure-Python version of writer.pyx, used when the Cython extension modules
# are not available. It mirrors the Cython module function by function.

from collections import OrderedDict
from operator import index

from .util import (
    tounicode,
    isdigit,
    isprint,
    high_surrogate_from_unicode_scalar,
    low_surrogate_from_unicode_scalar,
)


# this table includes A-Z, a-z, 0-9, '.', '_' and '$'
VALID_UNQUOTED_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._$"
)


def string_needs_quotes(a):
    if not isinstance(a, str):
        raise TypeError(
            f"Argument 'a' has incorrect type (expected str, got {type(a).__name__})"
        )
    # empty string is always quoted
    if not a:
        return True

    is_number = True
    seen_period = False

    for ch in a:
        # if non-ASCII or contains any invalid unquoted characters,
        # we must write it with quotes
        if ch not in VALID_UNQUOTED_CHARS:
            return True
        elif is_number:
            # check if the string could be confused with an integer or float;
            # if so we write it with quotes to disambiguate its type
            if isdigit(ord(ch)):
                continue
            elif ch == ".":
                if not seen_period:
                    seen_period = True
                else:
                    # if it contains two '.', it can't be a number
                    is_number = False
            else:
                # if any characters not in ".0123456789", it's not a number
                is_number = False

    return is_number


def escape_unicode(ch):
    return "\\U%04X" % ch


_ESCAPES = {
    "\a": "\\a",
    "\b": "\\b",
    "\v": "\\v",
    "\f": "\\f",
    "\\": "\\\\",
    '"': '\\"',
    "\r": "\\r",
}


class Writer:
    def __init__(
        self,
        unicode_escape=True,
        float_precision=6,
        indent=None,
        single_line_tuples=False,
        escape_newlines=True,
        sort_keys=True,
        single_line_empty_objects=True,
        binary_spaces=True,
    ):
        self.dest = []
        self.unicode_escape = bool(unicode_escape)
        self.float_precision = index(float_precision)
        self.escape_newlines = bool(escape_newlines)
        self.sort_keys = bool(sort_keys)
        self.single_line_empty_objects = bool(single_line_empty_objects)
        self.binary_spaces = bool(binary_spaces)

        if indent is not None:
            if isinstance(indent, str):
                self.indent = tounicode(indent)
            else:
                self.indent = " " * indent
        else:
            self.indent = None
        self.single_line_tuples = bool(single_line_tuples)
        self.current_indent_level = 0

    def getvalue(self):
        return self._getvalue()

    def dump(self, file):
        s = self._getvalue()
        # figure out whether file object expects bytes or unicodes
        try:
            file.write(b"")
        except TypeError:
            file.write("")  # this better not fail...
            # file already accepts unicodes; use it directly
            file.write(s)
        else:
            # file expects bytes; always encode as UTF-8
            file.write(s.encode("utf-8"))

    def write(self, obj):
        return self.write_object(obj)

    def _getvalue(self):
        return "".join(self.dest)

    def write_object(self, obj):
        if obj is None:
            return self.write_string("(nil)")
        if isinstance(obj, str):
            return self.write_string(obj)
        elif isinstance(obj, bool):
            self.dest.append("1" if obj else "0")
            return 1
        elif isinstance(obj, float):
            return self.write_short_float_repr(obj)
        elif isinstance(obj, int):
            return self.write_unquoted_string(str(obj))
        elif isinstance(obj, list):
            return self.write_array_from_list(obj)
        elif isinstance(obj, tuple):
            return self.write_array_from_tuple(obj)
        elif isinstance(obj, OrderedDict):
            return self.write_ordered_dict(obj)
        elif isinstance(obj, dict):
            return self.write_dict(obj)
        elif isinstance(obj, bytes):
            return self.write_data(obj)
        else:
            raise TypeError(
                f"Object of type {type(obj).__name__} is not PLIST serializable"
            )

    def write_quoted_string(self, string):
        escape_newlines = self.escape_newlines
        unicode_escape = self.unicode_escape
        dest = ['"']

        for c in string:
            ch = ord(c)
            if ch == 0x09 or ch == 0x20 or (ch == 0x0A and not escape_newlines):
                dest.append(c)
            elif ch == 0x0A and escape_newlines:
                dest.append("\\n")
            elif c in _ESCAPES:
                dest.append(_ESCAPES[c])
            else:
                if ch < 128:
                    if isprint(ch):
                        dest.append(c)
                    else:
                        dest.append("\\%03o" % ch)
                elif unicode_escape:
                    if ch > 0xFFFF:
                        dest.append(
                            escape_unicode(high_surrogate_from_unicode_scalar(ch))
                        )
                        dest.append(
                            escape_unicode(low_surrogate_from_unicode_scalar(ch))
                        )
                    else:
                        dest.append(escape_unicode(ch))
                else:
                    dest.append(c)

        dest.append('"')

        s = "".join(dest)
        self.dest.append(s)
        return len(s)

    def write_unquoted_string(self, string):
        self.dest.append(string)
        return len(string)

    def write_string(self, string):
        if string_needs_quotes(string):
            return self.write_quoted_string(string)
        else:
            return self.write_unquoted_string(string)

    def write_short_float_repr(self, py_float):
        string = f"{py_float:.{self.float_precision}f}"
        length = len(string)

        # read digits backwards, skipping all the '0's until either a
        # non-'0' or '.' is found
        while length > 0:
            ch = string[length - 1]
            if ch == ".":
                length -= 1  # skip the trailing dot
                break
            elif ch != "0":
                break
            length -= 1

        return self.write_unquoted_string(string[:length])

    def write_data(self, data):
        length = len(data)
        binary_spaces = self.binary_spaces

        # the number includes the opening '<' and closing '>', and the
        # interleaving spaces between each group of 4 bytes; each byte
        # is encoded with two hexadecimal digit
        extra_length = (
            2 + 2 * length + ((length - 1) // 4 if binary_spaces and length > 4 else 0)
        )

        digits = data.hex().upper()
        if binary_spaces:
            # print a space after each 32-bit int, except the last one
            digits = " ".join(digits[i : i + 8] for i in range(0, len(digits), 8))
        self.dest.append("<" + digits + ">")

        return extra_length

    def write_array_from_list(self, seq):
        length = len(seq)
        newline_indent = ""

        indent = self.indent

        if length == 0:
            if self.single_line_empty_objects or indent is None:
                self.dest.append("()")
                return 2
            else:
                newline_indent = "(\n" + self.current_indent_level * indent + ")"
                return self.write_unquoted_string(newline_indent)

        self.dest.append("(")
        count = 1

        if indent is not None:
            self.current_indent_level += 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        last = length - 1
        for i in range(length):
            count += self.write_object(seq[i])
            if i != last:
                if indent is None:
                    self.dest.append(", ")
                    count += 2
                else:
                    self.dest.append(",")
                    count += 1 + self.write_unquoted_string(newline_indent)

        if indent is not None:
            self.current_indent_level -= 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        self.dest.append(")")
        count += 1

        return count

    def write_array_from_tuple(self, seq):
        length = len(seq)
        newline_indent = ""

        indent = self.indent

        if length == 0:
            if self.single_line_empty_objects or indent is None:
                self.dest.append("()")
                return 2
            else:
                newline_indent = "(\n" + self.current_indent_level * indent + ")"
                return self.write_unquoted_string(newline_indent)

        self.dest.append("(")
        count = 1

        if indent is not None and not self.single_line_tuples:
            self.current_indent_level += 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        last = length - 1
        for i in range(length):
            count += self.write_object(seq[i])
            if i != last:
                if indent is None:
                    self.dest.append(", ")
                    count += 2
                else:
                    self.dest.append(",")
                    count += 1 + self.write_unquoted_string(newline_indent)

        if indent is not None and not self.single_line_tuples:
            self.current_indent_level -= 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        self.dest.append(")")
        count += 1

        return count

    def write_dict(self, d):
        newline_indent = ""

        indent = self.indent

        if not d:
            if self.single_line_empty_objects or indent is None:
                self.dest.append("{}")
                return 2
            else:
                newline_indent = "{\n" + self.current_indent_level * indent + "}"
                return self.write_unquoted_string(newline_indent)

        self.dest.append("{")
        count = 1

        if indent is not None:
            self.current_indent_level += 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        last = len(d) - 1
        items = d.items()
        if self.sort_keys:
            items = sorted(items)
        for i, (key, value) in enumerate(items):
            if not isinstance(key, str):
                key = str(key)
            count += self.write_string(key)

            self.dest.append(" = ")
            count += 3

            count += self.write_object(value)

            if i != last:
                if indent is None:
                    self.dest.append("; ")
                    count += 2
                else:
                    self.dest.append(";")
                    count += 1 + self.write_unquoted_string(newline_indent)
            else:
                self.dest.append(";")
                count += 1

        if indent is not None:
            self.current_indent_level -= 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        self.dest.append("}")
        count += 1

        return count

    def write_ordered_dict(self, d):
        # This is the same as the write_dict method but doesn't sort the items.
        newline_indent = ""

        indent = self.indent

        if not d:
            if self.single_line_empty_objects or indent is None:
                self.dest.append("{}")
                return 2
            else:
                newline_indent = "{\n" + self.current_indent_level * indent + "}"
                return self.write_unquoted_string(newline_indent)

        self.dest.append("{")
        count = 1

        if indent is not None:
            self.current_indent_level += 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        last = len(d) - 1
        # we don't sort OrderedDict
        for i, (key, value) in enumerate(d.items()):
            if not isinstance(key, str):
                key = str(key)
            count += self.write_string(key)

            self.dest.append(" = ")
            count += 3

            count += self.write_object(value)

            if i != last:
                if indent is None:
                    self.dest.append("; ")
                    count += 2
                else:
                    self.dest.append(";")
                    count += 1 + self.write_unquoted_string(newline_indent)
            else:
                self.dest.append(";")
                count += 1

        if indent is not None:
            self.current_indent_level -= 1
            newline_indent = "\n" + self.current_indent_level * indent
            count += self.write_unquoted_string(newline_indent)

        self.dest.append("}")
        count += 1

        return count


def dumps(
    obj,
    unicode_escape=True,
    float_precision=6,
    indent=None,
    single_line_tuples=False,
    escape_newlines=True,
    sort_keys=True,
    single_line_empty_objects=True,
    binary_spaces=True,
):
    w = Writer(
        unicode_escape=unicode_escape,
        float_precision=float_precision,
        indent=indent,
        single_line_tuples=single_line_tuples,
        escape_newlines=escape_newlines,
        sort_keys=sort_keys,
        single_line_empty_objects=single_line_empty_objects,
        binary_spaces=binary_spaces,
    )
    w.write(obj)
    return w.getvalue()


def dump(
    obj,
    fp,
    unicode_escape=True,
    float_precision=6,
    indent=None,
    single_line_tuples=False,
    escape_newlines=True,
    sort_keys=True,
    single_line_empty_objects=True,
    binary_spaces=True,
):
    w = Writer(
        unicode_escape=unicode_escape,
        float_precision=float_precision,
        indent=indent,
        single_line_tuples=single_line_tuples,
        escape_newlines=escape_newlines,
        sort_keys=sort_keys,
        single_line_empty_objects=single_line_empty_objects,
        binary_spaces=binary_spaces,
    )
    w.write(obj)
    w.dump(fp)
