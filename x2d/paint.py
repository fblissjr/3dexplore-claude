"""Bambu Studio's painted-triangle strings: `paint_color`, `paint_supports`,
`paint_seam`, `paint_fuzzy_skin` on a `<triangle>`.

Each string is one triangle's TriangleSelector tree, serialised as hex. Read
the nibbles from the END of the string, and the bits of each nibble
least-significant first. A node is 2 bits of split count; a leaf (split 0)
then carries 2 bits of state, and state 3 means "read 4 more bits, state =
3 + n". A split node carries 2 bits naming its special side, then split+1
child nodes. For `paint_color`, state 0 is "unpainted" (the part's own
filament) and state k is filament slot k.

Whole-triangle codes follow: "4" = slot 1, "8" = slot 2, "0C" = slot 3,
"1C" = slot 4, "2C" = slot 5. The string is exactly ceil(bits / 4) nibbles, so
a leading zero is data: "0C" is slot 3, "C" is a truncated tree. Stripping it
is the bug this module exists to not have.

Checked against a Bambu Studio 02.08.02.61 project: every one of its 1147
distinct strings parses with nothing but zero padding left over, and
re-encodes to itself.
"""
from collections import Counter

__all__ = ["leaves", "states", "remap", "solid", "dominant"]


def _bits(code):
    return [(int(ch, 16) >> i) & 1 for ch in reversed(code) for i in range(4)]


def _read(bits, i, n):
    if i + n > len(bits):
        raise ValueError("paint string ends inside a node")
    return sum(bits[i + k] << k for k in range(n)), i + n


def _state_bits(state):
    if not 0 <= state <= 18:
        raise ValueError(f"paint state {state} does not fit the encoding")
    return [state & 1, state >> 1 & 1] if state < 3 else [1, 1] + [(state - 3) >> k & 1 for k in range(4)]


def leaves(code):
    """[(state, bit_offset, bit_width)] for every leaf, and the bit count the
    tree uses. Offsets are what make an in-place rewrite possible."""
    bits, out = _bits(code), []

    def node(i):
        split, i = _read(bits, i, 2)
        if split == 0:
            state, j = _read(bits, i, 2)
            if state == 3:
                extra, j = _read(bits, j, 4)
                state += extra
            out.append((state, i, j - i))
            return j
        _, i = _read(bits, i, 2)            # special side: geometry, not colour
        for _ in range(split + 1):
            i = node(i)
        return i

    end = node(0)
    if any(bits[end:]):
        raise ValueError(f"paint string {code!r} has data past its tree")
    return out, end


def states(code):
    """The leaf states of one triangle, in tree order."""
    return [s for s, _, _ in leaves(code)[0]]


def remap(code, mapping):
    """Rewrite leaf states through {old: new}, keeping the tree.

    A state crossing 3 changes width (2 bits <-> 6), so the string can grow or
    shrink; everything after the leaf moves with it."""
    lv, end = leaves(code)
    if not any(s in mapping for s, _, _ in lv):
        return code
    bits, out, pos = _bits(code), [], 0
    for state, at, width in lv:
        out += bits[pos:at] + _state_bits(mapping.get(state, state))
        pos = at + width
    out += bits[pos:end]
    out += [0] * (-len(out) % 4)
    return ''.join('%X' % sum(out[i + k] << k for k in range(4)) for i in range(0, len(out), 4))[::-1]


def solid(state):
    """The code for a whole triangle in one state: solid(3) -> '0C'."""
    bits = [0, 0] + _state_bits(state)
    bits += [0] * (-len(bits) % 4)
    return ''.join('%X' % sum(bits[i + k] << k for k in range(4)) for i in range(0, len(bits), 4))[::-1]


def dominant(code):
    """The state covering the most leaves -- good enough to colour a preview,
    not to decide anything (leaves are not equal areas)."""
    return Counter(states(code)).most_common(1)[0][0]
