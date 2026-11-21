"""Opok's standing review points as mechanical whole-file checks (lessons 20 in docs/ov01-lane-lessons.md,
section 8 of docs/xs2-lane-lessons.md). prepush-check.py runs them on each touched TU at HEAD and at the base
and lists only findings the base does not have. Every finding is a lead for a human: a matching codegen device
is allowed when one comment at the top of the function says which plain form was compiled and what changed.

Checks (the review comment of 2026-11-12 on xsg-ii-decomp PRs #10-#13 that each one encodes):
  placeholder_fns   a C definition still named func_XXXXXXXX ("rename the restored functions that you've left
                    as func_*; based on your comments you know what each does")
  ov_placeholders   func_A8B490-style names for an address config/symbols/<unit>.txt already names
                    ("func_A8B490 ... have names in config/symbols/ov01.txt: battleContextStateGet")
  header_protos     a prototype in the .c of a function an include/ header declares ("hand-written prototypes
                    ... declared in tu096.h. Drop them")
  header_types      a struct/typedef defined in the .c that an include/ header defines ("local struct XenoActor
                    ... declared in include/main/tu051.h: include the header")
  tag_shapes        one struct tag defined with different members in two files of the tree ("the same tag is
                    also defined in tu109.c with another shape")
  view_casts        `(T *) &x->member` / `(T *) &x.member[i]`: the same bytes under a second record type
                    ("the same bytes are described by two types ... remove the view structs")
  ptr_fields        a field cast to a pointer at 3+ sites ("declared int but holds a text pointer and is cast
                    to void * at seven call sites")
  double_casts      `*(T *)(unsigned char *)&obj.field` ("the double cast only hides the access")
  bool_arith        `x -= a < b;` comparison used as a number ("write if (a < b) { x--; } or comment")
  gnu_inline        `__inline__` ("plain static inline")
  builtin_mem       `__builtin_memcpy` / `__builtin_memset` ("memcpy instead of __builtin_memcpy")
  before_object     `(table - N)[i]`: a pointer before its object ("declare the table with its size")
  libc_shadow       a local or parameter named like a libc function ("the local free shadows free()")
  roleless_fields   record members named valueNN / first / second / fourth / fifth / limit_a / pairFirst /
                    *_word(s) / fieldNN ("name them hp, ep ..."; "fourth/fifth represent width and height")
  roleless_types    typedef names carrying a TU number (Tu104Record) ("Tu104Record needs a real name")
A finding is suppressed when the 6 lines above it hold a comment with "compiled" or "plain form" (the
function-top note that the plain spelling was tried and what changed).
"""
import re
from pathlib import Path

IDENT = r"[A-Za-z_]\w*"
LIBC = ("free malloc calloc realloc memcpy memset memmove memcmp strlen strcpy strncpy strcmp strncmp strcat "
        "sprintf printf abs labs exit rand srand time sqrt sin cos atoi qsort").split()
ROLELESS_FIELD = re.compile(r"^(?:value\d*[A-Fa-f0-9]*|first|second|third|fourth|fifth|limit_[a-z]|pair(?:First|Second)"
                            r"|field_?[0-9A-Fa-f]+|word_?[0-9A-Fa-f]+|unk\w*|state_words?|tmp\w*|var\w*)$")
NOTE = re.compile(r"compiled|plain form|plain spelling", re.I)

CHECKS = ("placeholder_fns", "ov_placeholders", "header_protos", "header_types", "tag_shapes", "view_casts",
          "ptr_fields", "double_casts", "bool_arith", "gnu_inline", "builtin_mem", "before_object", "libc_shadow",
          "roleless_fields", "roleless_types")


def strip_comments(text):
    return re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def struct_bodies(text):
    """tag/typedef name -> normalized member list, for every struct/union with a body."""
    out = {}
    code = strip_comments(text)
    for m in re.finditer(r"(?:typedef\s+)?(?:struct|union)\s+(" + IDENT + r")?\s*\{([^{}]*)\}\s*(" + IDENT + r")?\s*;", code):
        body = re.sub(r"\s+", " ", m.group(2)).strip()
        for name in {m.group(1), m.group(3)} - {None}:
            out.setdefault(name, body)
    return out


def members(body):
    return [m.group(1) for m in re.finditer(r"(" + IDENT + r")\s*(?:\[[^\]]*\])*\s*(?::\s*\d+)?\s*;", body)]


class TreeIndex:
    """What the checks need from the whole tree: header declarations, header types, struct shapes, symbol names."""

    def __init__(self, tree, unit="main"):
        tree = Path(tree)
        self.header_protos, self.header_types, self.shapes, self.addr_names = {}, {}, {}, {}
        proto = re.compile(r"^\s*(?:extern\s+)?[A-Za-z_][\w \*]*?\b(" + IDENT + r")\s*\([^;{]*\)\s*;", re.M)
        for h in (tree / "include").rglob("*.h"):
            t = h.read_text(errors="replace")
            rel = str(h.relative_to(tree))
            for m in proto.finditer(strip_comments(t)):
                self.header_protos.setdefault(m.group(1), rel)
            for name in struct_bodies(t):
                self.header_types.setdefault(name, rel)
        for c in (tree / "src").rglob("*.c"):
            for name, body in struct_bodies(c.read_text(errors="replace")).items():
                self.shapes.setdefault(name, {})[str(c.relative_to(tree))] = body
        for txt in (tree / "config/symbols").glob("*.txt"):
            for m in re.finditer(r"^(" + IDENT + r") = 0x([0-9A-Fa-f]+);", txt.read_text(errors="replace"), re.M):
                self.addr_names.setdefault(int(m.group(2), 16), m.group(1))


def findings(text, path, index):
    """Findings for one file's text: dict check -> set of strings."""
    out = {k: set() for k in CHECKS}
    code = strip_comments(text)
    lines = text.splitlines()

    def line_of(pos):
        return code.count("\n", 0, pos) + 1

    def noted(ln):
        return any(NOTE.search(l) for l in lines[max(0, ln - 7):ln])

    def add(check, pos, msg):
        ln = line_of(pos)
        if not noted(ln):
            out[check].add(f"line {ln}: {' '.join(msg.split())[:90]}")

    defs = {}
    for m in re.finditer(r"^(?:static\s+)?(?:inline\s+|__inline__\s+)?[A-Za-z_][\w \*]*?\b(" + IDENT + r")\s*\(([^;{)]*)\)\s*\{", code, re.M):
        defs[m.group(1)] = m.start()
        if re.fullmatch(r"func_[0-9A-Fa-f]{8}", m.group(1)):
            out["placeholder_fns"].add(m.group(1))
        for p in re.finditer(r"\b(" + "|".join(LIBC) + r")\s*(?:[,)\[])", m.group(2)):
            add("libc_shadow", m.start(), f"parameter `{p.group(1)}`")
    for m in re.finditer(r"\bfunc_(?:00)?([0-9A-Fa-f]{6})\b", code):
        name = index.addr_names.get(int(m.group(1), 16))
        if name and name != m.group(0) and not name.startswith("func_" + m.group(1)):
            out["ov_placeholders"].add(f"{m.group(0)} -> {name}")
        elif name and name != m.group(0) and name.startswith("func_"):
            out["ov_placeholders"].add(f"{m.group(0)} -> {name} (the symbol file's spelling)")
    for m in re.finditer(r"^\s*(?:extern\s+)?[A-Za-z_][\w \*]*?\b(" + IDENT + r")\s*\([^;{]*\)\s*;", code, re.M):
        name = m.group(1)
        if name in index.header_protos and not name.startswith("__") and not re.match(r"\s*(return|else|case)\b", m.group(0)):
            add("header_protos", m.start(), f"{name} is declared in {index.header_protos[name]}")
    own_types = struct_bodies(text)
    for name in own_types:
        if name in index.header_types:
            out["header_types"].add(f"{name} is defined in {index.header_types[name]}")
        shapes = index.shapes.get(name, {})
        other = {f: b for f, b in shapes.items() if f != path and members(b) != members(own_types[name])}
        if other:
            out["tag_shapes"].add(f"{name} also defined with other members in {', '.join(sorted(other)[:3])}")
        for mem in members(own_types[name]):
            if ROLELESS_FIELD.match(mem):
                out["roleless_fields"].add(f"{name}.{mem}")
        if re.search(r"\bTu\d+|\bTU\d+|_tu\d+", name):
            out["roleless_types"].add(name)
    for m in re.finditer(r"\(\s*(?:struct\s+)?(" + IDENT + r")\s*\*\s*\)\s*&\s*" + IDENT + r"\s*(?:->|\.)", code):
        if m.group(1) in own_types:
            add("view_casts", m.start(), f"({m.group(1)} *) &member: {code[m.start():m.start() + 60].strip()}")
    casts = {}
    for m in re.finditer(r"\(\s*(?:const\s+)?(?:void|char|unsigned char|u8)\s*\*\s*\)\s*(?:" + IDENT + r"(?:\[[^\]]*\])?\s*(?:->|\.))+(" + IDENT + r")\b(?!\s*\()", code):
        casts.setdefault(m.group(1), []).append(m.start())
    for field, where in casts.items():
        if len(where) >= 3:
            out["ptr_fields"].add(f".{field} cast to a pointer at {len(where)} sites (first line {line_of(where[0])})")
    for m in re.finditer(r"\*\s*\(\s*[\w ]+\*\s*\)\s*\(\s*(?:unsigned\s+char|u8|char|s8)\s*\*\s*\)\s*&", code):
        add("double_casts", m.start(), code[m.start():m.start() + 60].strip())
    for m in re.finditer(r"[-+]=\s*[^;{}=]*?[\w\])]\s*(?:<|>|<=|>=|==|!=)\s*[\w(][^;{}]*;", code):
        add("bool_arith", m.start(), code[m.start() - 20:m.end()].strip().split("\n")[-1])
    for m in re.finditer(r"\b__inline__\b", code):
        add("gnu_inline", m.start(), "__inline__")
    for m in re.finditer(r"\b__builtin_mem\w+", code):
        add("builtin_mem", m.start(), m.group(0))
    for m in re.finditer(r"\(\s*" + IDENT + r"\s*-\s*(?:0x[0-9A-Fa-f]+|\d+)\s*\)\s*\[", code):
        add("before_object", m.start(), code[m.start():m.end() + 10].strip())
    for m in re.finditer(r"^\s+(?:(?:unsigned|signed|const|struct)\s+)*\w+\s*\**\s*\b(" + "|".join(LIBC) + r")\s*(?:=|;|\[)", code, re.M):
        add("libc_shadow", m.start(), f"local `{m.group(1)}`")
    return out
