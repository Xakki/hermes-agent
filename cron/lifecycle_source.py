"""Bounded lexical provenance, not a JavaScript AST or runtime binding resolver."""

from __future__ import annotations

import ast
from typing import NamedTuple

AUTO_EXEC = ("auto-exec", "")
FORCED_SHELL = ("forced-shell", "")


def source(kind: str) -> tuple[str, str]:
    return "source", kind


class _Token(NamedTuple):
    kind: str
    raw: str
    start: int
    end: int



class _LexicalUnit:
    """One iterative pass, including code inside arbitrarily nested template interpolations."""

    def __init__(self, text: str, javascript: bool):
        self.text = text
        self.javascript = javascript
        self.position = 0
        self.tokens: list[_Token] = []
        self.pairs: dict[int, int] = {}
        self.comments: list[tuple[int, int]] = []
        self.stack: list[tuple[str, int]] = []
        self.interpolated: set[int] = set()
        self.valid = True

    def scan(self) -> None:
        while self.position < len(self.text):
            if self.stack and self.stack[-1][0] == "`":
                self._template()
            else:
                self._code()
        self.valid = self.valid and not self.stack

    def _add(self, kind: str, end: int) -> int:
        index = len(self.tokens)
        self.tokens.append(_Token(kind, self.text[self.position:end], self.position, end))
        self.position = end
        return index

    def _comment(self) -> None:
        start = self.position
        if self.text.startswith("/*", start):
            closing = self.text.find("*/", start + 2)
            self.valid = self.valid and closing >= 0
            self.position = len(self.text) if closing < 0 else closing + 2
        else:
            closing = self.text.find("\n", start)
            self.position = len(self.text) if closing < 0 else closing
        self.comments.append((start, self.position))

    def _string(self) -> None:
        quote = self.text[self.position]
        end = self.position + 1
        while end < len(self.text):
            char = self.text[end]
            if char == quote:
                self._add("string", end + 1)
                return
            if char in "\r\n":
                self.valid = False
            end += self._escape(end) if char == "\\" else 1
        self.valid = False
        self._add("string", len(self.text))

    def _escape(self, start: int) -> int:
        escape = self.text[start + 1:start + 2]
        if escape and escape in "\\'\"bfnrtv\n\r":
            return 2
        if escape == "0" and not self.text[start + 2:start + 3].isdigit():
            return 2
        width = {"x": 2, "u": 4}.get(escape, 0)
        digits = self.text[start + 2:start + 2 + width]
        if width and len(digits) == width and all(char in "0123456789abcdefABCDEF" for char in digits):
            return 2 + width
        self.valid = False
        return 2

    def _template(self) -> None:
        start = self.position
        end = self.position
        while end < len(self.text):
            char = self.text[end]
            if char == "`":
                _, index = self.stack.pop()
                token = self.tokens[index]
                if index in self.interpolated:
                    self._fragment(start, end)
                    self.tokens[index] = _Token("dynamic", "", token.start, end + 1)
                else:
                    self.tokens[index] = _Token("string", self.text[token.start:end + 1], token.start, end + 1)
                self.position = end + 1
                return
            if self.text.startswith("${", end):
                index = self.stack[-1][1]
                self.interpolated.add(index)
                self._fragment(start, end)
                self.position = end
                self.stack.append(("${}", self._add("punct", end + 2)))
                return
            if char == "\\":
                self.valid = False  # Escaped template spelling is outside the certificate subset.
            end += 2 if char == "\\" else 1
        self.valid = False
        self.position = len(self.text)

    def _fragment(self, start: int, end: int) -> None:
        # Disjoint spans avoid quadratic copies/work for deeply nested templates.
        if start < end:
            self.tokens.append(_Token("fragment", self.text[start:end], start, end))

    def _delimiter(self, char: str) -> None:
        index = self._add("punct", self.position + 1)
        if char in "([{":
            self.stack.append(({"(": ")", "[": "]", "{": "}"}[char], index))
            return
        if not self.stack:
            self.valid = False
            return
        closer, opening = self.stack.pop()
        if char != closer and not (char == "}" and closer == "${}"):
            self.valid = False
        self.pairs[opening] = index
        self.pairs[index] = opening

    def _word(self) -> None:
        end = self.position + 1
        while end < len(self.text) and self.text[end].isascii() and (self.text[end].isalnum() or self.text[end] in "_$"):
            end += 1
        self._add("id", end)

    def _code(self) -> None:
        char = self.text[self.position]
        if char.isspace():
            self.position += 1
        elif self.position == 0 and self.text.startswith("#!") or self.javascript and self.text.startswith(("//", "/*"), self.position):
            self._comment()
        elif char in "'\"":
            self._string()
        elif char == "`":
            self.stack.append(("`", self._add("template", self.position + 1)))
        elif char.isascii() and (char.isalnum() or char in "_$"):
            self._word()
        elif char in "()[]{}":
            self._delimiter(char)
        else:
            self._operator()

    def _operator(self) -> None:
        # Slash/regexp, escaped identifiers and unsupported code revoke proof, never the raw walk.
        char = self.text[self.position]
        if char not in ";:,.+-*%=!?&|^~<>":
            self.valid = False
        for operator in ("...", "=>", "?.", "++", "--", "+=", "-=", "*=", "??", "&&", "||"):
            if self.text.startswith(operator, self.position):
                self._add("punct", self.position + len(operator))
                return
        self._add("punct", self.position + 1)


def _literal(token: _Token) -> str:
    raw = token.raw
    if token.kind == "fragment":
        return raw
    body = raw[1:-1] if len(raw) > 1 and raw[-1] == raw[0] else raw[1:]
    if raw.startswith("`"):
        return body
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return body
    return value if isinstance(value, str) else body


def _plain_call(unit: _LexicalUnit, index: int) -> bool:
    tokens = unit.tokens
    previous = tokens[index - 1].raw if index else ""
    if previous in {".", "?.", "function", "new", "*", "#"}:
        return False
    if index + 1 >= len(tokens) or tokens[index + 1].raw != "(":
        return False
    closing = unit.pairs.get(index + 1)
    return closing is not None and (closing + 1 == len(tokens) or tokens[closing + 1].raw not in {"{", "=>"})


def _static_property_occurrence(tokens: list[_Token], index: int) -> bool:
    if _literal(tokens[index]) != "require":
        return False
    following = tokens[index + 1].raw if index + 1 < len(tokens) else ""
    return following == ":" or (
        index >= 2 and tokens[index - 1].raw == "[" and following == "]"
        and tokens[index - 2].raw not in {"return", "throw", "case"}
        and (tokens[index - 2].kind == "id" or tokens[index - 2].raw in {")", "]", "?."})
    )


def _ambient_require(unit: _LexicalUnit) -> bool:
    """Whole-unit certificate: every supported require occurrence must be a plain direct call."""
    if not unit.valid or not unit.javascript:
        return False
    for index, token in enumerate(unit.tokens):
        if token.kind == "id" and token.raw == "require" and not _plain_call(unit, index):
            return False
        if token.kind == "string" and _static_property_occurrence(unit.tokens, index):
            return False
    return True


def _source_literal(unit: _LexicalUnit, index: int, ambient: bool) -> bool:
    if not unit.valid or not unit.javascript or unit.tokens[index].kind != "string":
        return False
    tokens = unit.tokens
    if index >= 2 and tokens[index - 1].raw == "(":
        callee = tokens[index - 2].raw
        if callee == "require":
            return ambient and _plain_call(unit, index - 2)
        if callee == "import":
            return _plain_call(unit, index - 2)
    return index > 0 and tokens[index - 1].raw == "import" and (
        index == 1 or tokens[index - 2].raw not in {".", "?."}
    )


def _actions(unit: _LexicalUnit) -> list[tuple[str, tuple[str, str]]]:
    actions = []
    ambient = _ambient_require(unit)
    parentheses = 0
    for index, token in enumerate(unit.tokens):
        if token.kind == "punct" and token.raw == "(":
            parentheses += 1
        elif token.kind == "punct" and token.raw == ")":
            parentheses = max(0, parentheses - 1)
        if token.kind not in {"string", "fragment"}:
            continue
        value = _literal(token)
        certified = _source_literal(unit, index, ambient)
        if parentheses or certified:
            obligation = source("javascript") if certified else AUTO_EXEC
            actions.append((value, obligation))
        if not certified and any(char.isspace() for char in value):
            actions.append((value, FORCED_SHELL))
    if not unit.valid:
        # Unsupported syntax cannot hide an execution edge behind a partial lexical view.
        actions.append((unit.text, AUTO_EXEC))
    return actions


def reference_view(text: str, *, javascript: bool):
    """Return raw-code view and additive obligations; proof failure only removes optimization."""
    unit = _LexicalUnit(text, javascript)
    unit.scan()
    pieces, comments = [], []
    end = 0
    for start, stop in unit.comments:
        # The shebang is not a JS comment body or an executable candidate.
        if text.startswith("#!", start):
            continue
        pieces.extend((text[end:start], "\n" * text[start:stop].count("\n")))
        value = text[start:stop]
        comments.append(value[2:-2] if value.startswith("/*") and value.endswith("*/") else value[2:])
        end = stop
    pieces.append(text[end:])
    return "".join(pieces), _actions(unit), comments
