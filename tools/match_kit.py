#!/usr/bin/env python3
"""One-function matching loop against the retail bytes, for humans and agents.

Built on verify_decomp.py, so every verdict comes from compiled bytes, never from
whoever wrote the C. Two compilers (--compiler): `contract`, the decomp's own pinned
cc1 (realconv-lp7-fsv3) + la29 ee-as from <upstream>/.work/toolchains, the default
when both are present; `stock`, the unpatched ee-gcc 2.96 + ee-as of EE_ROOTFS.

  match_kit.py init    NAME [--forms N] [--sandbox DIR] [--compiler C] [--author A]
                       [--no-progress-stop K]   private copy + budget + card; exit 3 = UNVERIFIABLE
  match_kit.py disasm  NAME [--range START:END] [--brief]   original code, targets and data named
  match_kit.py card    NAME [--json]                        <= 2 KB evidence card (also SANDBOX/card.md)
  match_kit.py check   SANDBOX "one changed hypothesis" [--full] [--compiler C]
                       compile, judge NAME, charge one form, save forms/NN.c + NN.diff
  match_kit.py cheat   SANDBOX            anti-cheat lint of NAME's C (also run by check)
  match_kit.py verdict SANDBOX [--full]   final machine verdict (JSON) for a gate; charges nothing
  match_kit.py best    SANDBOX            restore the best form's source into the sandbox
  match_kit.py patch   SANDBOX [--against FILE]   unified diff of the function block against
                       the copy init saved (SANDBOX/base/), or spliced into FILE, a newer copy
                       of the same file; refuses (exit 1) without --against once the published
                       file moved since init. The TU-local header's diff follows when it changed.
  match_kit.py self-test                  network-free checks of this tool

check exit codes: 0 judged (MATCH or not) | 2 form ceiling or NO PROGRESS (K charged
forms without a lower diff_words or a higher tu_matches; K = budget.no_progress_stop)
| 3 UNVERIFIABLE stop (two tooling verdicts from the same verifier) | 4 REGRESSION.

Verdicts: MATCH | HAZARD-NOP | REORDER | MIXED | IMMEDIATE (known stock-vs-patched
toolchain classes) | UNEXPLAINED | SIZE_DIFF (the object's function is longer or
shorter than the original; both sizes shown) | UNDECLARED (the C names something the
symbol table never had: a form is charged, the names are listed) | NOT_COMPILED |
UNVERIFIABLE. MATCH with a blocking cheat finding is reported as CHEAT, never as MATCH.

Rules learned from the first agent test (docs/agent-matching.md):
  * the tool decides exactness; a worker's "verified manually" is not evidence;
  * UNVERIFIABLE is a tooling gap: it charges no form, and two in one sandbox stop
    the loop (exit 3) so nobody burns a budget on a verdict the tool cannot give;
    an invented name is not a tooling gap: it is UNDECLARED and costs a form;
  * byte identity is necessary, not sufficient: asm, offset casts, raw addresses
    and placeholder names are cheats even when the bytes match;
  * CHEAT and REGRESSION forms never count as progress and are never `best`.

Environment: DECOMP_UPSTREAM (the decomp checkout; default: the repository root when
this kit lives in <repo>/kit/compat, else ../xsg-i-decomp next to it), DECOMP_GAME_DIR
(folder holding the original), DECOMP_BINARY (its file name, default SLUS_204.69; also
the `binary` column of symbols.csv to use), DECOMP_UNIT (the upstream unit, default
main), EE_ROOTFS (stock toolchain rootfs), DECOMP_TOOLCHAINS (contract tools; default
<upstream>/.work/toolchains; the layout inside comes from the upstream's
config/toolchain-identity.json or DECOMP_CONTRACT_CC1/DECOMP_CONTRACT_AS),
DECOMP_SANDBOXES (default ~/.cache/decomp-match; colima only mounts paths under $HOME). rabbitizer (pip, the
kit venv) is required by `init`: without it the probe cannot read the original code.
"""
import argparse
import csv
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get('DECOMP_UPSTREAM') or (KIT.parents[1] if KIT.parent.name == 'kit'
                                                     else KIT.parent / 'xsg-i-decomp')).expanduser()
GAME = Path(os.environ.get('DECOMP_GAME_DIR', '.')).expanduser()
SYMBOLS = KIT / 'symbols' / 'symbols.csv'
ROOTFS = Path(os.environ.get('EE_ROOTFS', Path.home() / 'ee-rootfs')).expanduser()
SANDBOXES = Path(os.environ.get('DECOMP_SANDBOXES', Path.home() / '.cache' / 'decomp-match')).expanduser()
UNIT = os.environ.get('DECOMP_UNIT', 'main')                       # the upstream unit the sandbox judges
BINARY = os.environ.get('DECOMP_BINARY', 'SLUS_204.69')             # the original ELF's file name in DECOMP_GAME_DIR
UNVERIFIABLE_STOP = 2
NO_PROGRESS_DEFAULT = 4
CARD_LIMIT = 2048
CONTRACT_CC1 = 'ee-gcc2.96-realconv-lp7-fsv3-v1/bin/cc1'
CONTRACT_AS = 'ee-legacy-binutils-la29-vsqrt-nolabel-laimm-v1/bin/ee-as'
ARG_GPRS = {4: 'a0', 5: 'a1', 6: 'a2', 7: 'a3', 8: 't0', 9: 't1', 10: 't2', 11: 't3'}   # EABI
ARG_FPRS = {n: f'f{n}' for n in range(12, 20)}
WIDTHS = dict(lb=1, lbu=1, sb=1, lh=2, lhu=2, sh=2, lw=4, lwu=4, sw=4, lwc1=4, swc1=4, lwl=4, lwr=4,
              swl=4, swr=4, ld=8, sd=8, ldl=8, ldr=8, sdl=8, sdr=8, lq=16, sq=16, ldc1=8, sdc1=8)

try:
    import rabbitizer
except ImportError:                                  # disassembly is a convenience, not a gate
    rabbitizer = None


def verifier_version():
    """Content hash of the verifier; an UNVERIFIABLE verdict is only as current as the verifier that gave it."""
    return hashlib.sha256((KIT / 'tools' / 'verify_decomp.py').read_bytes()).hexdigest()[:12]


def verifier():
    spec = importlib.util.spec_from_file_location('verify_decomp', KIT / 'tools' / 'verify_decomp.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binary():
    path = GAME / BINARY
    if not path.is_file():
        sys.exit(f'no {BINARY} in DECOMP_GAME_DIR={GAME} (set DECOMP_BINARY for another original)')
    return path


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_short(path):
    """`git describe --always --abbrev=7` of PATH's repository, `-dirty` appended when
    `git status --porcelain -- .` run in PATH reports changes there: HEAD alone would
    claim a commit the files on disk do not have."""
    done = subprocess.run(['git', '-C', str(path), 'describe', '--always', '--abbrev=7'], capture_output=True, text=True)
    if done.returncode != 0 or not done.stdout.strip():
        return None
    head = done.stdout.strip()
    status = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '--', '.'], capture_output=True, text=True)
    return head + ('-dirty' if status.returncode == 0 and status.stdout.strip() and not head.endswith('-dirty') else '')


def dis(word, va):
    if rabbitizer is None:
        return f'.word {word:#010x}'
    return rabbitizer.Instruction(word, vram=va, category=rabbitizer.InstrCategory.R5900).disassemble()


# ---------------------------------------------------------------- compilers

def toolchains_dir():
    env = os.environ.get('DECOMP_TOOLCHAINS')
    if env:
        return Path(env).expanduser()
    local = UPSTREAM / '.work' / 'toolchains'
    return local if local.is_dir() else None


def contract_available(toolchains=None):
    """True when verify_decomp can resolve a contract toolchain under TOOLCHAINS: the
    layout named by DECOMP_CONTRACT_CC1/DECOMP_CONTRACT_AS, else the upstream's
    config/toolchain-identity.json, else the kit defaults (CONTRACT_CC1/CONTRACT_AS)."""
    base = toolchains or toolchains_dir()
    return bool(base) and verifier().resolve_contract(base, UPSTREAM) is not None


def default_compiler():
    return 'contract' if contract_available() else 'stock'


def compiler_of(budget, override=None):
    """The compiler a sandbox is judged with: an explicit --compiler, else the one
    recorded at init, else stock (every pre-contract sandbox was judged stock)."""
    return override or budget.get('compiler') or 'stock'


def no_progress_default():
    try:
        value = json.loads((UPSTREAM / 'config' / 'agent-routing.json').read_text())['stock_lane']['no_progress_stop']
        return int(value)
    except (OSError, ValueError, KeyError, TypeError):
        return NO_PROGRESS_DEFAULT


# ---------------------------------------------------------------- locate a function

def defines(text, name):
    """True when the C file holds NAME's definition: its INCLUDE_ASM stub or a C body.
    A call site or a prototype is not a definition (the first pilot's locate() took
    those and put functions in the wrong TU)."""
    stub = re.compile(r'^\s*INCLUDE_ASM\([^,]+,\s*' + re.escape(name) + r'\s*\)\s*;', re.M)
    return bool(stub.search(text)) or function_text(text, name) is not None


def locate(name, upstream=UPSTREAM, warn=None):
    """(tu id, source path) of the in-scope C-mode TU that defines NAME (INCLUDE_ASM stub
    or C body), over the TUs of config/tu-build.json + the per-TU ledgers (the compile
    manifest only as a stale fallback, with a warning)."""
    warn = warn or (lambda m: print(m, file=sys.stderr))
    for entry in verifier().discover_tus(upstream, UNIT, warn=warn):
        path = upstream / entry['path']
        if entry['mode'] != 'c' or not entry['in_scope'] or not path.is_file():
            continue
        if defines(path.read_text(errors='replace'), name):
            return entry['id'], entry['path']
    sys.exit(f'{name}: no in-scope C-mode {UNIT} TU of {upstream} defines it (INCLUDE_ASM stub or C body)')


# ---------------------------------------------------------------- disassembly

def load_symbols():
    by_name, by_va, starts = {}, {}, []
    for r in csv.DictReader(open(SYMBOLS)):
        if r['binary'] != BINARY or not r['name'] or r['name'].startswith(('gcc2_compiled', '__gnu_compiled')):
            continue
        va, size = int(r['va'], 16), int(r['size'] or 0)
        by_name.setdefault(r['name'], (va, size))
        by_va.setdefault(va, r['name'])
        if size:
            starts.append((va, size, r['name']))
    return by_name, by_va, sorted(starts)


def name_at(address, by_va, starts):
    if address in by_va:
        return by_va[address]
    for va, size, name in starts:
        if va <= address < va + size:
            return f'{name}+{address - va:#x}'
    return None


def image_segments(image):
    phoff, = struct.unpack_from('<I', image, 0x1C)
    ent, num = struct.unpack_from('<HH', image, 0x2A)
    segments = []
    for k in range(num):
        typ, off, va, _pa, filesz, memsz = struct.unpack_from('<6I', image, phoff + k * ent)
        if typ == 1:
            segments.append((va, off, filesz, memsz))
    return segments


def offset_va(segments, off):
    """The VA a file offset is loaded at, else None."""
    for sva, soff, filesz, _memsz in segments:
        if soff <= off < soff + filesz:
            return sva + off - soff
    return None


def in_loaded(segments, va):
    """True when VA lies inside a loaded segment, including its .bss tail (memsz > filesz):
    a %hi/%lo pair pointing there is a data address the C must name, not a constant."""
    return any(sva <= va < sva + memsz for sva, _off, _filesz, memsz in segments)


def image_bytes(image, segments, va, n):
    for sva, soff, size, _memsz in segments:
        if sva <= va < sva + size:
            return image[soff + va - sva: soff + va - sva + min(n, sva + size - va)]
    return None


def drop_written(ins, hi):
    """Forget the %hi half of every GPR the instruction writes: the register no longer
    holds it, so a later offset from that register is a field access (or a new pair),
    never a %lo of the old lui. The lui itself is the one write that sets a half."""
    try:
        if ins.modifiesRt():
            hi.pop(ins.rt.value, None)
        if ins.modifiesRd():
            hi.pop(ins.rd.value, None)
    except (AttributeError, ValueError, RuntimeError):
        pass


def pair_step(ins, hi, gp):
    """One instruction against the tracked %hi halves `hi` ({gpr: value << 16}), which it
    updates: returns ('jump', target), ('pair', address) for a %hi/%lo pair, ('gp',
    address) for a $gp-relative access, or (None, None). A lui sets a half; an
    addiu/ori into its own register consumes it (the register now holds the full
    address: later offsets from it are field accesses); any other write to a tracked
    register drops it (a stale half once made `lw $v1, X($v1); lw $v0, Y($v1)` look like
    a second pair at hi+Y and refused a function the verifier could judge)."""
    if ins.isJumpWithAddress():
        drop_written(ins, hi)
        return 'jump', ins.getInstrIndexAsVram()
    if ins.uniqueId == rabbitizer.InstrId.cpu_lui:
        hi[ins.rt.value] = ins.getProcessedImmediate() << 16
        return None, None
    kind = address = None
    if ins.hasOperandAlias(rabbitizer.OperandType.cpu_rs) and ins.canBeLo():
        if ins.rs.value in hi:
            kind, address = 'pair', (hi[ins.rs.value] + ins.getProcessedImmediate()) & 0xFFFFFFFF
        elif ins.rs.value == 28 and gp:
            kind, address = 'gp', (gp + ins.getProcessedImmediate()) & 0xFFFFFFFF
    drop_written(ins, hi)
    return kind, address


def disassemble(name):
    """Rows (va, word, text, address, note) of NAME plus its (va, size). `address` is the
    call target or the %hi/%lo / $gp data address the instruction refers to, if any."""
    vd = verifier()
    by_name, by_va, starts = load_symbols()
    if name not in by_name:
        sys.exit(f'{name}: not in {SYMBOLS}')
    va, size = by_name[name]
    gp = by_name.get('_gp', (None,))[0]
    image = binary().read_bytes()
    segments = image_segments(image)
    data = image[va - vd.TEXT_VA + vd.TEXT_OFF: va - vd.TEXT_VA + vd.TEXT_OFF + size]
    hi, rows = {}, []
    for i in range(0, size, 4):
        word, = struct.unpack_from('<I', data, i)
        note, address = '', None
        if rabbitizer is not None:
            ins = rabbitizer.Instruction(word, vram=va + i, category=rabbitizer.InstrCategory.R5900)
            kind, address = pair_step(ins, hi, gp)
            if kind == 'jump':
                note = name_at(address, by_va, starts) or ''
            elif kind == 'pair':
                if not in_loaded(segments, address):
                    address = None            # a lui/ori constant, not an address (.bss counts as loaded)
                else:
                    note = name_at(address, by_va, starts) or f'{address:#x}'
            elif kind == 'gp':
                note = (name_at(address, by_va, starts) or f'{address:#x}') + ' (gp-relative)'
        rows.append((va + i, word, dis(word, va + i), address, note))
    return va, size, rows


def parse_range(text, va, size):
    """START:END as hex/decimal; a value below the function's size is a byte offset,
    anything else an absolute address. Half-open, rounded to instructions."""
    def one(value, default):
        value = value.strip()
        if not value:
            return default
        n = int(value, 0)
        return va + n if n < size else n
    start, _, end = text.partition(':')
    return one(start, va), one(end, va + size)


def cmd_disasm(args):
    va, size, rows = disassemble(args.name)
    lo, hi = parse_range(args.range, va, size) if args.range else (va, va + size)
    print(f'# {args.name} @ {va:#x}, {size} bytes ({size // 4} instructions)'
          + (f', showing {lo:#x}..{hi:#x}' if args.range else ''))
    for row_va, word, text, _address, note in rows:
        if not (lo <= row_va < hi):
            continue
        tail = ('; ' + note) if note else ''
        if args.brief:
            print(f'{text:<40} {tail}'.rstrip())
        else:
            print(f'{row_va:08x}: {word:08x}  {text:<40} {tail}'.rstrip())


# ---------------------------------------------------------------- evidence card

def tu_ledger(tu):
    try:
        return json.loads((UPSTREAM / 'config' / 'tu' / (tu + '.json')).read_text())
    except (OSError, ValueError):
        return {}


def prototype_of(text, name):
    m = re.search(r'^[^\n;{}#]*\b' + re.escape(name) + r'\s*\([^;{]*\)\s*(?=\{)', text, re.M)
    return re.sub(r'\s+', ' ', m.group(0)).strip() if m else None


def size_class(size, cop2):
    if cop2:
        return 'cop2'
    for limit, label in ((48, 'trivial'), (160, 'easy'), (400, 'medium'), (1024, 'hard')):
        if size <= limit:
            return label
    return 'huge'


def grep_declarations(names, roots):
    """{name: first declaration-looking line found under roots} (extern, prototype,
    typedef, struct tag close)."""
    if not names:
        return {}
    wanted = {n for n in names if re.match(r'^[A-Za-z_]\w*$', n)}
    found = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*.[ch]')):
            try:
                text = path.read_text(errors='replace')
            except OSError:
                continue
            for name in list(wanted - set(found)):
                if name not in text:
                    continue
                m = re.search(r'^[ \t]*(extern\b[^;]*\b' + re.escape(name) + r'\b[^;]*;|typedef\b[^;]*\b'
                              + re.escape(name) + r'\s*;|\}\s*' + re.escape(name) + r'\s*;|[A-Za-z_][\w \t*]*\b'
                              + re.escape(name) + r'\s*\([^;{]*\)\s*;)', text, re.M)
                if m:
                    found[name] = re.sub(r'\s+', ' ', m.group(0)).strip()
            if wanted <= set(found):
                return found
    return found


def enrich_hints(name, va):
    tool = UPSTREAM / 'tools' / 'kosmos_enrich.py'
    if not tool.is_file():
        return {}
    done = subprocess.run([sys.executable, '-B', str(tool), 'fn', '--unit', UNIT, '--va', f'{va:#x}', '--name', name],
                          capture_output=True, text=True, cwd=str(UPSTREAM))
    try:
        return json.loads(done.stdout)
    except ValueError:
        return {}


def card_data(name, tu=None, path=None, source=None):
    """Everything the card says, as data (the JSON form of `card`)."""
    if tu is None:
        tu, path = locate(name)
    va, size, rows = disassemble(name)
    by_name, by_va, starts = load_symbols()
    image = binary().read_bytes()
    segments = image_segments(image)
    text = (source or (UPSTREAM / path)).read_text(errors='replace')
    ledger = tu_ledger(tu)
    entry = next((f for f in ledger.get('functions', []) if f['name'] == name), {})
    words = [w for _va, w, _t, _a, _n in rows]
    cop2 = any((w >> 26) == 0x12 for w in words)
    callees, globals_, strings, floats, members, args_read = [], [], [], [], {}, []
    written = set()
    hi = {}
    for row_va, word, txt, address, note in rows:
        base = (note.split(' (gp')[0].split('+')[0]) if note else None
        if rabbitizer is not None:
            ins = rabbitizer.Instruction(word, vram=row_va, category=rabbitizer.InstrCategory.R5900)
            op = ins.getOpcodeName()
            if ins.isJumpWithAddress() and base and base not in callees:
                callees.append(base)
            elif address is not None and not ins.isJumpWithAddress() and base and base not in globals_ and not base.startswith('0x'):
                globals_.append(base)
            regs = []
            try:
                if ins.readsRs():
                    regs.append(ins.rs.value)
                if ins.readsRt():
                    regs.append(ins.rt.value)
                if ins.readsRd():
                    regs.append(ins.rd.value)
            except (AttributeError, ValueError, RuntimeError):
                pass
            fregs = []
            try:
                if ins.isFloat():
                    if ins.readsFs():
                        fregs.append(ins.fs.value)
                    if ins.readsFt():
                        fregs.append(ins.ft.value)
            except (AttributeError, ValueError, RuntimeError):
                pass
            for r in regs:
                if r in ARG_GPRS and ('g', r) not in written and ARG_GPRS[r] not in args_read:
                    args_read.append(ARG_GPRS[r])
            for r in fregs:
                if r in ARG_FPRS and ('f', r) not in written and ARG_FPRS[r] not in args_read:
                    args_read.append(ARG_FPRS[r])
            try:
                if ins.modifiesRt():
                    written.add(('g', ins.rt.value))
                if ins.modifiesRd():
                    written.add(('g', ins.rd.value))
                if ins.isFloat():
                    for attr, flag in (('fd', ins.modifiesFd()), ('fs', ins.modifiesFs()), ('ft', ins.modifiesFt())):
                        if flag:
                            written.add(('f', getattr(ins, attr).value))
            except (AttributeError, ValueError, RuntimeError):
                pass
            if (ins.doesLoad() or ins.doesStore()) and op in WIDTHS and ins.rs.value not in (28, 29) and address is None:
                members.setdefault(ARG_GPRS.get(ins.rs.value, f'${ins.rs.value}'), []).append(
                    f'{ins.getProcessedImmediate():+#x}/{WIDTHS[op]}')
            if ins.uniqueId == rabbitizer.InstrId.cpu_lui:
                hi[ins.rt.value] = ins.getProcessedImmediate() << 16
            elif op == 'mtc1' and ins.rt.value in hi and hi[ins.rt.value] != 0:
                floats.append(f"{struct.unpack('<f', struct.pack('<I', hi[ins.rt.value] & 0xFFFFFFFF))[0]:g}")
            elif op in ('lwc1',) and address is not None:
                raw = image_bytes(image, segments, address, 4)
                if raw and len(raw) == 4:
                    floats.append(f"{struct.unpack('<f', raw)[0]:g} ({note})")
            if ins.uniqueId != rabbitizer.InstrId.cpu_lui:
                drop_written(ins, hi)         # a float constant needs the lui still in the register
        if address is not None and not (rows and any(a == address for a in ())):
            raw = image_bytes(image, segments, address, 48)
            if raw:
                s = raw.split(b'\0', 1)[0]
                if len(s) >= 4 and all(32 <= c < 127 for c in s):
                    item = s.decode()[:40]
                    if item not in strings:
                        strings.append(item)
    jal = struct.pack('<I', 0x0C000000 | ((va >> 2) & 0x03FFFFFF))
    callers, pos = [], 0
    while True:
        pos = image.find(jal, pos)
        if pos < 0:
            break
        site = offset_va(segments, pos) if pos % 4 == 0 else None
        if site is not None:
            caller = name_at(site, by_va, starts)
            caller = caller.split('+')[0] if caller else f'{site:#x}'
            if caller not in callers:
                callers.append(caller)
        pos += 4
    matched = [f['name'] for f in ledger.get('functions', []) if f.get('state') == 'c' and f.get('category') == 'exact_c']
    def common(a, b):
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        return n
    siblings = []
    for sib in sorted(matched, key=lambda s: (-common(s, name), s)):
        proto = prototype_of(text, sib)
        if proto:
            siblings.append(proto)
        if len(siblings) == 3:
            break
    decls = grep_declarations(callees + globals_, [UPSTREAM / 'include', UPSTREAM / 'src'])
    hints = enrich_hints(name, va)
    return dict(name=name, va=f'{va:#x}', size=size, tu=tu, path=path, state=entry.get('state'),
                category=entry.get('category'), size_class=size_class(size, cop2), cop2=cop2,
                callers=callers, callees=callees, globals=globals_, args_read=args_read, members=members,
                strings=strings, floats=floats, siblings=siblings, declarations=decls,
                java=[l for l in hints.get('lines', []) if l], xs3_twin=hints.get('xs3_twin'))


def card_text(data, limit=CARD_LIMIT):
    """The card as <= `limit` bytes of Markdown: lists are cut with '+N more' and
    trailing sections dropped before the budget is exceeded."""
    def cut(items, n, sep=', '):
        items = list(items)
        return sep.join(items[:n]) + (f' +{len(items) - n} more' if len(items) > n else '')
    sections = [
        [f"# {data['name']}",
         f"{data['va']} {data['size']} B  {data['tu']} {data['path']}  state={data['state'] or '?'}"
         f"{('/' + data['category']) if data.get('category') else ''}  class={data['size_class']}"
         + ('  COP2' if data['cop2'] else '')],
        [f"args read: {', '.join(data['args_read']) or '-'}"],
        [f"callees: {cut(data['callees'], 8) or '-'}"],
        [f"globals: {cut(data['globals'], 8) or '-'}"],
        [f"callers: {cut(data['callers'], 6) or '-'}"],
        [f"members: " + (cut((f"{base} {cut(offs, 8, ' ')}" for base, offs in data['members'].items()), 4, '; ') or '-')],
        [f"strings: {cut((repr(s) for s in data['strings']), 4) or '-'}"] if data['strings'] else [],
        [f"floats: {cut(data['floats'], 6)}"] if data['floats'] else [],
        [f"java: {l[:160]}" for l in data['java'][:2]],
        ([f"xs3 twin: {data['xs3_twin']}"] if data.get('xs3_twin') else []),
        ['siblings (matched, same TU):'] + [f"  {p[:150]}" for p in data['siblings']],
        ['declarations (upstream include/, src/):'] + [f"  {v[:120]}" for v in data['declarations'].values()],
    ]
    def trailer(n):
        return f'(+{n} lines cut)'
    out, size, dropped = [], 0, 0
    for section in sections:
        for line in section:
            n = len(line.encode()) + 1
            if size + n + len(trailer(dropped + 1)) + 1 > limit:
                dropped += 1
                continue
            out.append(line)
            size += n
    while dropped and out and size + len(trailer(dropped).encode()) + 1 > limit:
        size -= len(out.pop().encode()) + 1       # the real trailer, not a guessed reserve
        dropped += 1
    if dropped:
        out.append(trailer(dropped))
    return '\n'.join(out) + '\n'


def cmd_card(args):
    data = card_data(args.name)
    if args.json:
        print(json.dumps(data, indent=1))
    else:
        sys.stdout.write(card_text(data))
    return 0


# ---------------------------------------------------------------- sandbox

def probe(name):
    """UNVERIFIABLE before any work: every address the original function refers to
    must resolve to a symbol the verifier can also resolve by name (a jump table in
    .rodata is proven at check time instead). Returns the list of reasons."""
    if rabbitizer is None:
        return ['rabbitizer is not installed for this interpreter: the probe cannot read the original code '
                '(run the kit through its venv, or pip install rabbitizer==1.16.2)']
    vd = verifier()
    syms = vd.Symbols(SYMBOLS)
    by_name, by_va, starts = load_symbols()
    va, size, rows = disassemble(name)
    indirect = any(t.split()[0] == 'jr' and '$ra' not in t for _v, _w, t, _a, _n in rows)
    reasons, seen = [], set()
    for row_va, word, txt, address, note in rows:
        if address is None or address in seen or va <= address < va + size:
            continue
        seen.add(address)
        named = name_at(address, by_va, starts)
        if named is None:
            if indirect:
                continue                      # a switch table: anchored on the original at check time
            reasons.append(f'{address:#x} (at {row_va:#x}): no symbol covers it')
            continue
        base = named.split('+')[0]
        plain = re.sub(r'\.\d+$', '', base)
        try:
            value = syms.value(plain)
        except KeyError as e:
            reasons.append(f'{base}: {e} -- the C can only name it as {plain}')
            continue
        if value != by_name[base][0]:
            reasons.append(f'{base}: {plain} resolves to {value:#x}, not {by_name[base][0]:#x}')
    return sorted(set(reasons))


def cmd_init(args):
    tu, path = locate(args.name)
    sandbox = Path(args.sandbox).expanduser() if args.sandbox else SANDBOXES / args.name
    if sandbox.exists():
        sys.exit(f'{sandbox} exists: pick another --sandbox or remove it yourself')
    compiler = args.compiler or default_compiler()
    if compiler == 'contract' and not contract_available():
        print(json.dumps(dict(refused='contract toolchain not found', toolchains=str(toolchains_dir()),
                              note='no layout resolved: DECOMP_CONTRACT_CC1/DECOMP_CONTRACT_AS, the upstream '
                                   'config/toolchain-identity.json, or the kit defaults',
                              defaults=[CONTRACT_CC1, CONTRACT_AS])))
        return 3
    reasons = probe(args.name)
    if reasons:
        print(json.dumps(dict(refused='UNVERIFIABLE', function=args.name, tu=tu, reasons=reasons,
                              note='the verifier cannot resolve these references by name; a symbol-table or '
                                   'verifier fix lifts this, not more C'), indent=1))
        return 3
    (sandbox / 'upstream').mkdir(parents=True)
    for part in ('src', 'include', 'config'):
        shutil.copytree(UPSTREAM / part, sandbox / 'upstream' / part, symlinks=True)
    for rel in (path, header_of(path)):        # untouched copies: the base every patch diffs against
        if (sandbox / 'upstream' / rel).is_file():
            (sandbox / 'base' / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sandbox / 'upstream' / rel, sandbox / 'base' / rel)
    budget = dict(function=args.name, tu=tu, path=path, forms_max=args.forms, forms=[], attempts=[],
                  baseline_matches=None, upstream=str(UPSTREAM), created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                  compiler=compiler, toolchains=str(toolchains_dir()) if compiler == 'contract' else None,
                  author=args.author, kit_commit=git_short(KIT), upstream_commit=git_short(UPSTREAM),
                  base_sha256=sha256_file(sandbox / 'upstream' / path), verifier=verifier_version(),
                  no_progress_stop=args.no_progress_stop or no_progress_default())
    (sandbox / 'budget.json').write_text(json.dumps(budget, indent=1))
    result = judge(sandbox, budget, compiler)
    if result['tu_matches'] is None:
        print(json.dumps(dict(refused='NOT_COMPILED', function=args.name, tu=tu, compiler=compiler,
                              detail=result['detail'][:3], sandbox=str(sandbox)), indent=1))
        return 3
    budget['baseline_matches'] = result['tu_matches']
    card = None
    try:
        card = card_text(card_data(args.name, tu, path, sandbox / 'upstream' / path))
        (sandbox / 'card.md').write_text(card)
    except Exception as e:                       # the card is evidence, never a gate
        budget['card_error'] = f'{type(e).__name__}: {e}'[:200]
    (sandbox / 'budget.json').write_text(json.dumps(budget, indent=1))
    print(json.dumps(dict(sandbox=str(sandbox), tu=tu, path=path, forms_max=args.forms, compiler=compiler,
                          baseline_matches=result['tu_matches'], no_progress_stop=budget['no_progress_stop'],
                          author=args.author, kit_commit=budget['kit_commit'], upstream_commit=budget['upstream_commit'],
                          base_sha256=budget['base_sha256'], card=str(sandbox / 'card.md') if card else None,
                          edit=str(sandbox / 'upstream' / path))))
    return 0


# ---------------------------------------------------------------- judging

def judge(sandbox, budget, compiler=None):
    """Compile the TU with the chosen toolchain; return the verdict for the budget's function."""
    vd = verifier()
    compiler = compiler_of(budget, compiler)
    image_path = binary()
    work, report = sandbox / 'work', sandbox / 'last.json'
    report.unlink(missing_ok=True)                # the verifier wipes work/obj per run: whatever exists after is this run's
    argv = [sys.executable, str(KIT / 'tools' / 'verify_decomp.py'), '--upstream', str(sandbox / 'upstream'),
            '--tu', budget['tu'], '--symbols', str(SYMBOLS), '--binary', str(image_path),
            '--rootfs', str(ROOTFS), '--work', str(work), '--out', str(report), '-v', '--compiler', compiler]
    if compiler == 'contract':
        argv += ['--toolchains', str(budget.get('toolchains') or toolchains_dir())]
    run = subprocess.run(argv, capture_output=True, text=True)
    out = dict(verdict='NOT_COMPILED', detail=[], tu_matches=None, rows=[], diff_words=None,
               ours_sha256=None, compiler=compiler, size_ours=None, size_orig=None, undeclared=[])
    if run.returncode == 2:
        out['verdict'] = 'UNVERIFIABLE'
        out['detail'] = ['verifier tooling failure: ' + (run.stderr or run.stdout).strip()[-600:]]
        return out
    run_dir, per_tu, warnings = None, {}, []
    if report.is_file():
        data = json.loads(report.read_text())
        per_tu = data.get('per_tu', {}).get(budget['tu']) or {}
        out['tu_matches'] = per_tu.get('match')
        run_dir = Path(data['work']) if data.get('work') else None
        warnings = [w.get('msg', '') for w in data.get('warnings') or [] if w.get('tu') == budget['tu']]
    if run_dir is None:                            # the verifier stopped before its report: newest run tree
        runs = sorted(work.glob('run-*'), key=lambda d: d.stat().st_mtime)
        run_dir = runs[-1] if runs else work
    tag = budget['tu'].replace('/', '__')
    obj = run_dir / 'obj' / f'{tag}.o'
    if not obj.is_file() or per_tu.get('build') == 'fail':
        errors = [line for line in (run.stdout + run.stderr).splitlines() if 'error' in line.lower() or 'FAIL' in line]
        for log in sorted((run_dir / 'asm').glob('*err')):
            errors.append(log.read_text()[-1500:])
        if per_tu.get('build') == 'fail' and warnings and compiler == 'contract':
            errors.insert(0, 'ASWARN (assembler warning, a build failure under contract): ' + warnings[0])
        out['detail'] = ['\n'.join(errors).strip()[-2000:] or 'build produced no object',
                         'verifier said: ' + (run.stderr or run.stdout).strip()[-600:]]
        return out
    if warnings:
        out['detail'].append('ASWARN (assembler warning, informational under stock): ' + warnings[0])
    image = image_path.read_bytes()
    o = vd.Object(obj, vd.Symbols(SYMBOLS), image=image)
    target = next((f for f in o.funcs if f['name'] == budget['function']), None)
    if target is None:
        out['detail'] = [f"{budget['function']} was not emitted (still INCLUDE_ASM, renamed, or an unused static)"]
        return out
    preprocessed = run_dir / 'pp' / f'{tag}.i'
    candidate = preprocessed.read_text(errors='replace') if preprocessed.is_file() else \
        (sandbox / 'upstream' / budget['path']).read_text(errors='replace')
    undeclared = undeclared_names(o.undeclared_in(target), candidate)
    if undeclared:                                 # the C's defect, not the verifier's: a form is charged
        out['verdict'], out['undeclared'] = 'UNDECLARED', undeclared
        out['detail'] = [f"undeclared name(s) in the candidate, absent from the symbol table: {', '.join(undeclared)}"
                         ' -- use the name the disassembly shows or an existing declaration']
        return out
    off = target['va'] - vd.TEXT_VA + vd.TEXT_OFF
    orig = image[off:off + target['size']]
    obj_size = target.get('obj_size') or target['size']
    if obj_size != target['size'] and compiler == 'contract':
        kind, detail = vd.judge_sizes('match', '', obj_size, target['size'], compiler)
    else:
        if not o.is_clean(target):
            out['verdict'] = 'UNVERIFIABLE'
            lo, hi = o.span(target)
            out['detail'] = sorted({why for off_, why in o.unresolved if lo <= off_ < hi})
            return out
        kind, detail = vd.judge_sizes(*vd.classify(o.bytes_for(target), orig), obj_size, target['size'], compiler)
    ours = o.object_bytes(target)
    out['verdict'] = {'match': 'MATCH', 'size-diff': 'SIZE_DIFF'}.get(kind, kind.upper())
    out['ours_sha256'] = hashlib.sha256(ours).hexdigest()
    out['size_ours'], out['size_orig'] = len(ours), len(orig)
    if detail:
        out['detail'].append(detail)
    rows, differing = [], 0
    for i in range(max(len(ours), len(orig)) // 4):
        a = struct.unpack_from('<I', ours, i * 4)[0] if i * 4 < len(ours) else None
        b = struct.unpack_from('<I', orig, i * 4)[0] if i * 4 < len(orig) else None
        rows.append((target['va'] + i * 4, a, b))
        differing += a != b
    out['rows'], out['diff_words'] = rows, differing
    return out


def undeclared_names(names, candidate_text):
    """Of the unknown names the object refers to, those the candidate text itself spells
    (a typo, an invented global): a name only the compiler introduced (a libgcc helper
    the table lacks) stays a verifier gap."""
    return sorted(n for n in names if re.search(r'\b' + re.escape(n) + r'\b', candidate_text))


def render_rows(rows, full=False, context=1):
    """The ours | original table: every row with --full, else only differing rows with
    one row of context and '... N identical lines' markers."""
    if not rows:
        return []
    keep = [a != b for _va, a, b in rows]
    if not full:
        shown = [False] * len(rows)
        for i, differs in enumerate(keep):
            if differs:
                for j in range(max(0, i - context), min(len(rows), i + context + 1)):
                    shown[j] = True
    else:
        shown = [True] * len(rows)
    out, skipped = [f"{'ours':<44}| original"], 0
    for (va, a, b), show in zip(rows, shown):
        if not show:
            skipped += 1
            continue
        if skipped:
            out.append(f'... {skipped} identical lines')
            skipped = 0
        out.append(f"{'* ' if a != b else '  '}{(dis(a, va) if a is not None else '-'):<42}| "
                   f"{dis(b, va) if b is not None else '-'}")
    if skipped:
        out.append(f'... {skipped} identical lines')
    return out


def progress_state(forms, compiler=None):
    """best_form (lowest diff_words), and how many charged forms in a row brought
    neither a lower diff_words nor a higher tu_matches. Pre-contract records (no
    diff_words key) are not comparable and are skipped; so are CHEAT and REGRESSION
    forms (a cheat's diff_words=0 would poison every later honest form) and, when
    `compiler` is given, forms judged with the other compiler."""
    best_form = best_diff = best_tu = None
    without = 0
    for form in forms:
        if 'diff_words' not in form:
            continue
        if form.get('verdict') == 'CHEAT' or form.get('regression'):
            continue
        if compiler and form.get('compiler', 'stock') != compiler:
            continue
        dw, tu = form.get('diff_words'), form.get('tu_matches')
        lower = dw is not None and (best_diff is None or dw < best_diff)
        higher = tu is not None and (best_tu is None or tu > best_tu)
        if lower:
            best_diff, best_form = dw, form['n']
        if higher:
            best_tu = tu
        without = 0 if (lower or higher) else without + 1
    return dict(best_form=best_form, best_diff_words=best_diff, best_tu_matches=best_tu, forms_without_progress=without)


def regression_of(result, budget, verdict):
    expected = budget.get('baseline_matches')
    if expected is None or result['tu_matches'] is None:
        return False
    return result['tu_matches'] < expected + (1 if verdict in ('MATCH', 'CHEAT') else 0)


def unverifiable_count(budget, verifier_id):
    """UNVERIFIABLE attempts this sandbox recorded under the current verifier."""
    return sum(1 for attempt in budget.get('attempts', [])
               if attempt.get('verdict') == 'UNVERIFIABLE' and attempt.get('verifier') == verifier_id)


def tooling_blocker(count, verifier_id):
    return (f'TOOLING BLOCKER: {count} UNVERIFIABLE results from this verifier ({verifier_id}) in this '
            'sandbox. Stop and report the reasons; this is a verifier or symbol-table gap, not a C problem. '
            'A later verifier fix lifts the stop; the attempt history is never edited.')


# ---------------------------------------------------------------- anti-cheat

PLACEHOLDER = re.compile(r'\b(arg\d+|unk_?\w*|var_?\w*|field_0x\w+|temp\d*|tmp\d*|sp[0-9A-Fa-f]{2,}|D_[0-9A-Fa-f]{8})\b')
RULES = [
    ('inline-asm', re.compile(r'\b(__asm__|asm)\s*(volatile\s*)?\(|\.word\b'), 'inline assembly or .word in C'),
    ('offset-cast', re.compile(r'\(\s*[\w\s]+\*+\s*\)\s*\(\s*\(?\s*[\w\.\->]+\s*\)?\s*[+-]\s*(0x[0-9A-Fa-f]+|\d+|[A-Z][A-Z0-9_]{2,})\s*\)'),
     'pointer built from base + byte offset: model the struct field instead'),
    ('raw-address', re.compile(r'\(\s*[\w\s]+\*+\s*\)\s*0x[0-9A-Fa-f]{5,}'), 'raw absolute address cast to a pointer'),
    ('view-cast', re.compile(r'\(\s*\(\s*[\w\s]+\*+\s*\)\s*&?\s*[A-Za-z_]\w*\s*\)\s*(->|\[)'),
     'a global re-typed through a pointer cast (view struct / byte view): declare its real type'),
    ('byte-view', re.compile(r'\(\s*(unsigned\s+|signed\s+)?char\s*\*\s*\)\s*&\s*[A-Za-z_]\w*'),
     'address of an object read as bytes: use its fields'),
    ('padding-member', re.compile(r'\b\w*(pad|before|after|unk|filler|gap|reserved)\w*\s*\[\s*0x[0-9A-Fa-f]{3,}\s*\]', re.I),
     'large invented padding member that positions a field by offset'),
    ('volatile-local', re.compile(r'^\s*(register\s+)?volatile\b[^;(]*\b[A-Za-z_]\w*\s*(=[^;]*)?;', re.M),
     'volatile local used to force a register copy (hardware registers are accessed through volatile pointers, not locals)'),
    ('goto', re.compile(r'\bgoto\b'), 'goto without a compiled receipt (docs/style.md)'),
    ('do-while-0', re.compile(r'do\s*\{[^{}]*\}\s*while\s*\(\s*0\s*\)'), 'do { } while (0) scheduling barrier'),
    ('register-asm', re.compile(r'\bregister\b[^;]*\basm\s*\('), 'register pinned with asm("reg")'),
]


def function_text(text, name):
    match = re.search(r'^[^\n;{}#]*\b' + re.escape(name) + r'\s*\([^;{]*\)\s*\{', text, re.M)
    if not match:
        return None
    depth, index = 0, match.end() - 1
    while index < len(text):
        depth += {'{': 1, '}': -1}.get(text[index], 0)
        if depth == 0:
            return text[match.start():index + 1]
        index += 1
    return None


def upstream_of(budget):
    """The published tree a sandbox was copied from; the current one if that path moved."""
    recorded = Path(budget.get('upstream', ''))
    return recorded if (recorded / budget['path']).is_file() else UPSTREAM


def header_of(path):
    """The TU-local header next to a source file (src/<unit>/<stem>.h)."""
    return str(Path(path).with_suffix('.h'))


def base_of(sandbox, budget, rel):
    """The untouched copy of REL the sandbox started from: SANDBOX/base/<rel>, saved by
    init; for a sandbox older than that, the published tree (which may have moved)."""
    saved = Path(sandbox) / 'base' / rel
    return saved if saved.is_file() else upstream_of(budget) / rel


def added_lines(sandbox, budget):
    before = base_of(sandbox, budget, budget['path']).read_text(errors='replace').splitlines()
    after = (sandbox / 'upstream' / budget['path']).read_text(errors='replace').splitlines()
    return [line[1:] for line in difflib.unified_diff(before, after, lineterm='', n=0)
            if line.startswith('+') and not line.startswith('+++')]


DECLARATION = re.compile(r'^\s*(extern\b|typedef\b|#\s*(include|define)\b|struct\s+\w+\s*;|$|/?\*|//)')


def scope_changes(sandbox, budget):
    """Changed lines outside FUNCTION's body other than new declarations: edits to other
    (possibly already accepted) functions. Not a cheat by itself; the reviewer and the
    integrator must see it."""
    before = base_of(sandbox, budget, budget['path']).read_text(errors='replace').splitlines()
    after_text = (sandbox / 'upstream' / budget['path']).read_text(errors='replace')
    after = after_text.splitlines()
    span = function_text(after_text, budget['function'])
    start = after_text.find(span) if span else -1
    lo = after_text.count('\n', 0, start) if start >= 0 else -1
    hi = lo + span.count('\n') if span else -1
    out = []
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        for n in range(b0, b1):
            if not (lo <= n <= hi) and not DECLARATION.match(after[n]):
                out.append(dict(line=n + 1, text=after[n].strip()[:100]))
        if tag == 'delete' or (tag == 'replace' and not (lo <= b0 <= hi)):
            for n in range(a0, a1):
                if 'INCLUDE_ASM' not in before[n] and not DECLARATION.match(before[n]):
                    out.append(dict(line=None, text='removed: ' + before[n].strip()[:92]))
    return out


def cheat_findings(sandbox, budget):
    text = (sandbox / 'upstream' / budget['path']).read_text(errors='replace')
    body = function_text(text, budget['function'])
    findings = []
    if body is None:
        return [dict(rule='missing', severity='blocking', text=f"{budget['function']} has no C definition")]
    scope = body + '\n' + '\n'.join(added_lines(sandbox, budget))
    for rule, pattern, why in RULES:
        for m in pattern.finditer(scope):
            findings.append(dict(rule=rule, severity='blocking', why=why, text=m.group(0)[:80]))
    for m in set(PLACEHOLDER.findall(body)):
        findings.append(dict(rule='placeholder-name', severity='blocking', why='name from evidence, not a placeholder', text=m))
    for line in added_lines(sandbox, budget):
        m = re.match(r'\s*#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)\b', line)
        if m and re.search(r'[+-]\s*' + re.escape(m.group(1)) + r'\b', scope):
            findings.append(dict(rule='offset-macro', severity='blocking',
                                 why='numeric macro used as a byte offset: model the struct field', text=line.strip()[:80]))
    unique = {}
    for finding in findings:
        unique.setdefault((finding['rule'], finding['text']), finding)
    return list(unique.values())


def cmd_cheat(args):
    sandbox = Path(args.sandbox).expanduser()
    findings = cheat_findings(sandbox, json.loads((sandbox / 'budget.json').read_text()))
    print(json.dumps(findings, indent=1) if findings else 'clean')
    return 1 if any(f['severity'] == 'blocking' for f in findings) else 0


# ---------------------------------------------------------------- one form

def load_budget(sandbox):
    return json.loads((sandbox / 'budget.json').read_text())


def cmd_check(args):
    sandbox = Path(args.sandbox).expanduser()
    budget_path = sandbox / 'budget.json'
    budget = load_budget(sandbox)
    compiler = compiler_of(budget, args.compiler)
    hypothesis = ' '.join(args.hypothesis).strip()
    if not hypothesis:
        sys.exit('state the one changed hypothesis this form tests')
    current = verifier_version()
    unverifiable = unverifiable_count(budget, current)
    if unverifiable >= UNVERIFIABLE_STOP:
        print(tooling_blocker(unverifiable, current))
        return 3
    if len(budget['forms']) >= budget['forms_max']:
        print(f"FORM CEILING ({budget['forms_max']}) reached: stop, report your best form and next hypothesis")
        return 2
    stop_after = int(budget.get('no_progress_stop') or no_progress_default())
    progress = progress_state(budget['forms'], compiler)
    if progress['forms_without_progress'] >= stop_after:
        print(f"NO PROGRESS ({progress['forms_without_progress']} forms): stop and report the best form"
              f" (form {progress['best_form']}, diff_words={progress['best_diff_words']})")
        return 2
    source = sandbox / 'upstream' / budget['path']
    result = judge(sandbox, budget, compiler)
    findings = cheat_findings(sandbox, budget) if result['verdict'] not in ('NOT_COMPILED',) else []
    verdict = 'CHEAT' if result['verdict'] == 'MATCH' and findings else result['verdict']
    regress = regression_of(result, budget, verdict)
    record = dict(n=len(budget['attempts']) + 1, hypothesis=hypothesis, verdict=verdict, verifier=current,
                  compiler=compiler, tu_matches=result['tu_matches'], diff_words=result['diff_words'],
                  ours_sha256=result['ours_sha256'], source_sha256=sha256_file(source), regression=regress,
                  cheats=[f['rule'] for f in findings], undeclared=result.get('undeclared') or [],
                  time=time.strftime('%H:%M:%S'))
    budget['attempts'].append(record)
    rows_text = '\n'.join(render_rows(result['rows'], full=True))
    if verdict != 'UNVERIFIABLE':                  # a tooling gap is not the worker's form
        budget['forms'].append(record)
        forms = sandbox / 'forms'
        forms.mkdir(exist_ok=True)
        shutil.copyfile(source, forms / f"{record['n']:02d}.c")
        (forms / f"{record['n']:02d}.diff").write_text(
            f"# form {record['n']} {verdict} compiler={compiler} diff_words={result['diff_words']}\n"
            f"# {hypothesis}\n" + '\n'.join(result['detail'][:1]) + '\n' + rows_text + '\n')
    budget_path.write_text(json.dumps(budget, indent=1))
    charged = '' if verdict == 'UNVERIFIABLE' else f"FORM {len(budget['forms'])}/{budget['forms_max']}: "
    print(f"{charged}{verdict}   TU {budget['tu']} matches {result['tu_matches']} (baseline {budget['baseline_matches']})"
          f"   compiler={compiler}" + ('   REGRESSION: another function stopped matching' if regress else ''))
    if verdict != 'UNVERIFIABLE':
        line = f"diff_words={result['diff_words'] if result['diff_words'] is not None else '-'}"
        if result['size_ours'] is not None and result['size_ours'] != result['size_orig'] and verdict != 'SIZE_DIFF':
            line += f"   size {result['size_ours']} B vs {result['size_orig']} B original"
        same = [f['n'] for f in budget['forms'][:-1]
                if f.get('ours_sha256') and f.get('ours_sha256') == result['ours_sha256'] and f.get('compiler', 'stock') == compiler]
        if same:
            line += f"   diff identical to form {same[0]}"
        print(line)
    for finding in findings:
        print(f"CHEAT {finding['rule']}: {finding['text']}  -- {finding.get('why', '')}")
    print('\n'.join(result['detail']))
    if result['rows'] and verdict != 'MATCH':
        print('\n'.join(render_rows(result['rows'], full=args.full)))
    if verdict == 'UNVERIFIABLE':                  # the stop counts this attempt, not the next call
        unverifiable = unverifiable_count(budget, current)
        if unverifiable >= UNVERIFIABLE_STOP:
            print(tooling_blocker(unverifiable, current))
            return 3
    if verdict == 'MATCH' and not regress:
        return 0
    if regress:
        return 4
    progress = progress_state(budget['forms'], compiler)
    if progress['forms_without_progress'] >= stop_after:
        print(f"NO PROGRESS ({progress['forms_without_progress']} forms): stop and report the best form"
              f" (form {progress['best_form']}, diff_words={progress['best_diff_words']})")
        return 2
    return 0


def cmd_verdict(args):
    sandbox = Path(args.sandbox).expanduser()
    budget = load_budget(sandbox)
    compiler = compiler_of(budget, args.compiler)
    result = judge(sandbox, budget, compiler)
    findings = cheat_findings(sandbox, budget)
    verdict = 'CHEAT' if result['verdict'] == 'MATCH' and findings else result['verdict']
    progress = progress_state(budget['forms'], compiler)
    detail = result['detail'][:1] + (render_rows(result['rows'], full=args.full) if result['rows'] and verdict != 'MATCH' else [])
    print(json.dumps(dict(function=budget['function'], tu=budget['tu'], verdict=verdict, compiler=compiler,
                          tu_matches=result['tu_matches'], baseline_matches=budget['baseline_matches'],
                          regression=regression_of(result, budget, verdict), diff_words=result['diff_words'],
                          size=dict(ours=result['size_ours'], original=result['size_orig']),
                          best_form=progress['best_form'], forms_without_progress=progress['forms_without_progress'],
                          no_progress_stop=budget.get('no_progress_stop'), author=budget.get('author'),
                          kit_commit=budget.get('kit_commit'), upstream_commit=budget.get('upstream_commit'),
                          base_sha256=budget.get('base_sha256'), scope=scope_changes(sandbox, budget),
                          forms=len(budget['forms']), forms_max=budget['forms_max'], cheats=findings,
                          detail=detail if args.full else detail[:12]), indent=1))
    return 0 if verdict == 'MATCH' else 1


def cmd_best(args):
    sandbox = Path(args.sandbox).expanduser()
    budget = load_budget(sandbox)
    progress = progress_state(budget['forms'], compiler_of(budget))
    best = progress['best_form']
    saved = sandbox / 'forms' / f'{best:02d}.c' if best is not None else None
    if saved is None or not saved.is_file():
        print(json.dumps(dict(restored=False, best_form=best,
                              reason='no saved honest form with a diff_words record (CHEAT and REGRESSION forms never count)')))
        return 1
    shutil.copyfile(saved, sandbox / 'upstream' / budget['path'])
    print(json.dumps(dict(restored=True, best_form=best, diff_words=progress['best_diff_words'],
                          source=str(sandbox / 'upstream' / budget['path']))))
    return 0


def splice(base_text, sandbox_text, newer_text, name):
    """`newer_text` (a later copy of the same file) with NAME's INCLUDE_ASM stub, or its
    C definition, replaced by the block the sandbox put in place of the stub in
    `base_text` (its own base): declarations, comment and body together. Other
    declaration-only insertions of the sandbox (typedefs near the top) go before it."""
    body = function_text(sandbox_text, name)
    if body is None:
        return None
    base_lines, sandbox_lines, newer_lines = base_text.splitlines(), sandbox_text.splitlines(), newer_text.splitlines()
    start = sandbox_text[:sandbox_text.find(body)].count('\n')
    block, extra = None, []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=base_lines, b=sandbox_lines, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        chunk = sandbox_lines[b0:b1]
        if b0 <= start < b1:
            block = chunk
        elif chunk and all(DECLARATION.match(l) for l in chunk) and any(l.strip() for l in chunk):
            extra.extend(l for l in chunk if l not in newer_lines)
    if block is None:
        block = body.splitlines()
    insert = extra + ([''] if extra else []) + block
    stub = re.compile(r'^\s*INCLUDE_ASM\([^,]+,\s*' + re.escape(name) + r'\s*\)\s*;')
    existing = function_text(newer_text, name)
    if existing:
        return newer_text.replace(existing, '\n'.join(insert), 1)
    for i, line in enumerate(newer_lines):
        if stub.match(line):
            return '\n'.join(newer_lines[:i] + insert + newer_lines[i + 1:]) + ('\n' if newer_text.endswith('\n') else '')
    return None


def splice_hunks(base_text, edited_text, newer_text):
    """`newer_text` with every change base -> edited applied to it (the header's member
    split, carried onto a newer copy of the header). Each hunk is anchored on its base
    lines plus one line of leading context, which must occur exactly once in `newer`;
    None when a hunk cannot be placed (the same span moved: merge by hand)."""
    base_lines, edited_lines, out = base_text.splitlines(), edited_text.splitlines(), newer_text.splitlines()
    hunks = [op for op in difflib.SequenceMatcher(a=base_lines, b=edited_lines, autojunk=False).get_opcodes()
             if op[0] != 'equal']
    for _tag, a0, a1, b0, b1 in reversed(hunks):
        lead = 1 if a0 > 0 else 0
        anchor = base_lines[a0 - lead:a1]
        hits = [i for i in range(len(out) - len(anchor) + 1) if out[i:i + len(anchor)] == anchor]
        if len(hits) != 1:
            return None
        at = hits[0] + lead
        out[at:at + (a1 - a0)] = edited_lines[b0:b1]
    return '\n'.join(out) + ('\n' if newer_text.endswith('\n') else '')


def cmd_patch(args):
    sandbox = Path(args.sandbox).expanduser()
    budget = load_budget(sandbox)
    name, path, header = budget['function'], budget['path'], header_of(budget['path'])
    base_text = base_of(sandbox, budget, path).read_text(errors='replace')
    after_text = (sandbox / 'upstream' / path).read_text(errors='replace')
    # the TU-local header (src/<unit>/<stem>.h) travels with the source when a form modelled a
    # named member there (an unmodeled_ span split in place); anything else is out of scope
    h_base, h_ours = base_of(sandbox, budget, header), sandbox / 'upstream' / header
    h_before = h_after = None
    if h_base.is_file() and h_ours.is_file():
        h_before, h_after = h_base.read_text(errors='replace'), h_ours.read_text(errors='replace')
        if h_before == h_after:
            h_before = h_after = None
    if args.against:
        newer = Path(args.against).expanduser()
        newer_text = newer.read_text(errors='replace')
        spliced = splice(base_text, after_text, newer_text, name)
        if spliced is None:
            sys.exit(f"{name}: no INCLUDE_ASM stub or definition to replace in {args.against}, "
                     'or no C definition in the sandbox')
        before, after = newer_text, spliced
        newer_header = newer.with_suffix('.h')
        if h_after is not None and newer_header.is_file():
            newer_h = newer_header.read_text(errors='replace')
            merged = splice_hunks(h_before, h_after, newer_h)
            if merged is None:
                sys.exit(f'{header}: the sandbox\'s header change does not apply onto {newer_header} '
                         '(the same span moved); merge it by hand')
            h_before, h_after = newer_h, merged
    else:
        published = upstream_of(budget) / path
        if budget.get('base_sha256') and published.is_file() and sha256_file(published) != budget['base_sha256']:
            sys.exit('published file moved since init; use --against')
        before, after = base_text, after_text
    sys.stdout.writelines(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f'a/{path}', f'b/{path}'))
    if h_after is not None and h_before != h_after:
        sys.stdout.writelines(difflib.unified_diff(h_before.splitlines(True), h_after.splitlines(True),
                                                   f'a/{header}', f'b/{header}'))
    return 0


# ---------------------------------------------------------------- self-test

def self_test():
    checks = {}
    forms = [dict(n=1, diff_words=10, tu_matches=5), dict(n=2, diff_words=10, tu_matches=5),
             dict(n=3, diff_words=4, tu_matches=5), dict(n=4, diff_words=6, tu_matches=5),
             dict(n=5, diff_words=None, tu_matches=None), dict(n=6, hypothesis='old record')]
    p = progress_state(forms)
    checks['progress'] = p['best_form'] == 3 and p['forms_without_progress'] == 2 and p['best_diff_words'] == 4
    checks['progress-tu'] = progress_state([dict(n=1, diff_words=3, tu_matches=5), dict(n=2, diff_words=3, tu_matches=6)])['forms_without_progress'] == 0
    poisoned = [dict(n=1, diff_words=0, tu_matches=6, verdict='CHEAT'), dict(n=2, diff_words=5, tu_matches=5, verdict='UNEXPLAINED'),
                dict(n=3, diff_words=2, tu_matches=4, verdict='REORDER', regression=True), dict(n=4, diff_words=4, tu_matches=5, verdict='REORDER')]
    p = progress_state(poisoned)
    checks['progress-cheat'] = p['best_form'] == 4 and p['best_diff_words'] == 4 and p['forms_without_progress'] == 0
    p = progress_state([dict(n=1, diff_words=1, tu_matches=5, verdict='REORDER', compiler='stock'),
                        dict(n=2, diff_words=3, tu_matches=5, verdict='REORDER', compiler='contract')], 'contract')
    checks['progress-compiler'] = p['best_form'] == 2 and p['forms_without_progress'] == 0
    attempts = dict(attempts=[dict(verdict='UNVERIFIABLE', verifier='v1'), dict(verdict='UNVERIFIABLE', verifier='v0'),
                              dict(verdict='UNVERIFIABLE', verifier='v1'), dict(verdict='MATCH', verifier='v1')])
    checks['unverifiable-count'] = unverifiable_count(attempts, 'v1') == 2 >= UNVERIFIABLE_STOP and 'TOOLING BLOCKER: 2' in tooling_blocker(2, 'v1')
    rows = [(0x100 + 4 * i, i, i) for i in range(12)]
    rows[6] = (0x118, 6, 99)
    compact = render_rows(rows)
    checks['render-compact'] = (compact[1] == '... 5 identical lines' and compact[-1] == '... 4 identical lines'
                                and sum(1 for l in compact if l.startswith('* ')) == 1 and len(compact) == 6)
    checks['render-full'] = len(render_rows(rows, full=True)) == 13
    checks['diff-words'] = sum(1 for _va, a, b in rows if a != b) == 1
    base = '/*\n * x\n */\nint x;\n\nINCLUDE_ASM("asm/x", fn);\n\nint other(void)\n{\n    return 1;\n}\n'
    edited = base.replace('INCLUDE_ASM("asm/x", fn);', 'extern int y;\n\n/*\n * fn adds y\n */\nint fn(int a)\n{\n    return a + y;\n}')
    edited = edited.replace('int x;', 'int x;\ntypedef int Count;')
    newer = base.replace('int x;', 'int x;\nint z;')
    out = splice(base, edited, newer, 'fn')
    checks['splice'] = out is not None and 'INCLUDE_ASM' not in out and 'int z;' in out and 'typedef int Count;' in out \
        and 'extern int y;\n\n/*\n * fn adds y\n */\nint fn(int a)' in out and function_text(out, 'fn') == function_text(edited, 'fn')
    checks['splice-missing'] = splice(base, base, base, 'fn') is None
    checks['function-text'] = function_text(edited, 'other') == 'int other(void)\n{\n    return 1;\n}'
    data = dict(name='f', va='0x1', size=8, tu='main/tu0', path='src/main/x.c', state='asm', category=None,
                size_class='trivial', cop2=False, callers=[f'c{i}' for i in range(20)], callees=['g'], globals=[],
                args_read=['a0'], members={'a0': ['+0x0/4']}, strings=['hello world'], floats=['0.5'], java=['x' * 300],
                xs3_twin=None, siblings=['void s(void)'] * 3, declarations={f'd{i}': 'extern int d%d;' % i for i in range(200)})
    text = card_text(data)
    checks['card-limit'] = len(text.encode()) <= CARD_LIMIT and '+14 more' in text and 'lines cut' in text
    worst = 0
    for pad in range(0, 60):                   # pad 47 once rendered 2049 bytes: the trailer was guessed at 16
        sweep = dict(data, callers=[], callees=[], globals=[], members={}, strings=[], floats=[], siblings=[], java=['x' * pad])
        worst = max(worst, len(card_text(sweep).encode()))
    checks['card-limit-exact'] = worst <= CARD_LIMIT
    checks['range'] = parse_range('0x10:0x20', 0x1000, 0x40) == (0x1010, 0x1020) and parse_range('0x1008:', 0x1000, 0x40) == (0x1008, 0x1040)
    checks['compiler-default'] = compiler_of(dict(), None) == 'stock' and compiler_of(dict(compiler='contract'), 'stock') == 'stock'
    checks['undeclared-names'] = undeclared_names(['gFoo', '__ashldi3'], 'extern int gFoo;\nint f(void) { return gFoo; }') == ['gFoo']
    header_base = 'typedef union {\n    struct {\n        unsigned char unmodeled_000[0x240];\n        const char *texture;\n    } fields;\n} LayoutStore;\n'
    header_edit = header_base.replace('        unsigned char unmodeled_000[0x240];\n',
                                      '        unsigned char unmodeled_000[0x238];\n        int pixelAlpha;\n        unsigned char unmodeled_23c[0x004];\n')
    header_newer = header_base.replace('} LayoutStore;\n', '} LayoutStore;\n\nextern LayoutStore s_inLayout;\n')
    merged = splice_hunks(header_base, header_edit, header_newer)
    checks['splice-header'] = merged is not None and 'int pixelAlpha;' in merged and 'extern LayoutStore s_inLayout;' in merged \
        and 'unmodeled_000[0x240]' not in merged and splice_hunks(header_base, header_edit, header_base) == header_edit
    checks['splice-header-conflict'] = splice_hunks(header_base, header_edit, header_base.replace('[0x240]', '[0x230]')) is None
    call_site = 'int fn(int a);\n\nint user(void)\n{\n    return fn(1);\n}\n'
    checks['defines'] = not defines(call_site, 'fn') and defines(base, 'fn') and defines(edited, 'fn') and not defines(edited, 'zz')
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        up = Path(tmp)
        (up / 'config' / 'tu' / UNIT).mkdir(parents=True)
        (up / 'src' / UNIT).mkdir(parents=True)
        (up / 'config' / 'tu-build.json').write_text(json.dumps(dict(tus=[
            dict(id=f'{UNIT}/tu001', unit=UNIT, path=f'src/{UNIT}/a.c', in_scope=True),
            dict(id=f'{UNIT}/tu002', unit=UNIT, path=f'src/{UNIT}/b.c', in_scope=True)])))
        for tu in ('tu001', 'tu002'):
            (up / 'config' / 'tu' / UNIT / f'{tu}.json').write_text(json.dumps(dict(mode='c', in_scope=True, contract=dict(flags=['-G8']))))
        (up / 'src' / UNIT / 'a.c').write_text(call_site)                # the caller comes first, as in the manifest order
        (up / 'src' / UNIT / 'b.c').write_text(base)
        warned = []
        checks['locate-definition'] = locate('fn', up, warn=warned.append) == (f'{UNIT}/tu002', f'src/{UNIT}/b.c') and not warned
        sandbox = up / 'sandbox'
        (sandbox / 'base' / 'src' / UNIT).mkdir(parents=True)
        (sandbox / 'base' / 'src' / UNIT / 'b.c').write_text(base)
        budget = dict(path=f'src/{UNIT}/b.c', upstream=str(up))
        checks['base-of'] = base_of(sandbox, budget, f'src/{UNIT}/b.c') == sandbox / 'base' / 'src' / UNIT / 'b.c' \
            and base_of(sandbox, budget, f'src/{UNIT}/a.c') == up / 'src' / UNIT / 'a.c' and header_of('src/main/x.c') == 'src/main/x.h'
    if rabbitizer is not None:
        def step(word, hi, va=0x230000):
            return pair_step(rabbitizer.Instruction(word, vram=va, category=rabbitizer.InstrCategory.R5900), hi, 0x4DFB70)
        hi = {}
        stale = [step(0x3C03004B, hi), step(0x8C630010, hi), step(0x8C620020, hi)]      # lui v1; lw v1,0x10(v1); lw v0,0x20(v1)
        checks['hi-dropped-on-write'] = stale == [(None, None), ('pair', 0x4B0010), (None, None)] and hi == {}
        hi = {}
        kept = [step(0x3C03004B, hi), step(0x246293E0, hi), step(0x8C640010, hi)]       # lui v1; addiu v0,v1,-0x6c20; lw a0,0x10(v1)
        checks['hi-kept-for-other-dest'] = kept == [(None, None), ('pair', 0x4A93E0), ('pair', 0x4B0010)] and hi == {3: 0x4B0000}
        hi = {}
        consumed = [step(0x3C03004B, hi), step(0x246393E0, hi), step(0x7C620000, hi)]   # lui v1; addiu v1,v1,-0x6c20; sq v0,0(v1)
        checks['hi-consumed'] = consumed == [(None, None), ('pair', 0x4A93E0), (None, None)] and hi == {}
        checks['gp-step'] = step(0x8F82FF00, {})[0] == 'gp'                              # lw v0,-0x100(gp)
    else:
        checks['hi-dropped-on-write'] = checks['hi-kept-for-other-dest'] = checks['hi-consumed'] = checks['gp-step'] = 'skipped: no rabbitizer'
    checks['offset-va'] = (offset_va([(0x200000, 0x1000, 0x138570, 0x138570)], 0x198a0) == 0x2188a0
                           and offset_va([(0x200000, 0x1000, 0x10, 0x10)], 0x2000) is None)
    ok = all(checks.values())
    print(json.dumps(dict(self_test='ok' if ok else 'fail', checks=checks)))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init', help='private copy of the decomp + budget + evidence card')
    p.add_argument('name'); p.add_argument('--forms', type=int, default=30); p.add_argument('--sandbox')
    p.add_argument('--compiler', choices=('contract', 'stock'), help='default: contract when the toolchains exist')
    p.add_argument('--author', help='the agent or person writing the C (recorded, never trusted)')
    p.add_argument('--no-progress-stop', type=int, help='K charged forms without progress stop the loop '
                   '(default: config/agent-routing.json stock_lane.no_progress_stop, else 4)')
    p = sub.add_parser('disasm', help='original code with call targets and data named')
    p.add_argument('name'); p.add_argument('--range', help='START:END, offsets or addresses, half-open')
    p.add_argument('--brief', action='store_true', help='drop the address and raw-word columns')
    p = sub.add_parser('card', help='<= 2 KB evidence card'); p.add_argument('name'); p.add_argument('--json', action='store_true')
    p = sub.add_parser('check', help='compile, judge, charge one form'); p.add_argument('sandbox')
    p.add_argument('hypothesis', nargs='+'); p.add_argument('--full', action='store_true', help='print every diff row')
    p.add_argument('--compiler', choices=('contract', 'stock'), help='override the sandbox compiler for this form')
    p = sub.add_parser('verdict', help='final machine verdict (JSON)'); p.add_argument('sandbox')
    p.add_argument('--full', action='store_true'); p.add_argument('--compiler', choices=('contract', 'stock'))
    sub.add_parser('cheat', help='anti-cheat lint').add_argument('sandbox')
    sub.add_parser('best', help='restore the best form').add_argument('sandbox')
    p = sub.add_parser('patch', help='unified diff of the function block (and the TU-local header) against '
                                     'the copy init saved; exit 1 when the published file moved since init')
    p.add_argument('sandbox')
    p.add_argument('--against', help='a newer copy of the same file to splice the function block into '
                                     '(its sibling .h receives the header change)')
    sub.add_parser('self-test', help='network-free checks')
    args = parser.parse_args(argv)
    if args.command == 'self-test':
        return self_test()
    return dict(init=cmd_init, disasm=cmd_disasm, card=cmd_card, check=cmd_check, cheat=cmd_cheat,
                verdict=cmd_verdict, best=cmd_best, patch=cmd_patch)[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
