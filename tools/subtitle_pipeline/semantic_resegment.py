#!/usr/bin/env python3
"""Sentence-first bilingual subtitle repair. Dry-run by default; no duration cuts.

Automatic boundaries are conservative *candidates*, not a claim of language
understanding. Reviewed TSVs override them, with character-level lossless checks.
All output is staged and validated before --apply changes any source file.
"""
from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
from datetime import datetime
import hashlib
import difflib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


@dataclass(frozen=True)
class Block:
    en: str
    zh: str


@dataclass(frozen=True)
class Cue:
    start: int
    end: int
    block: Block


def compact(text: str) -> str:
    return re.sub(r'\s+', '', text.replace('|||', ''))


def read_blocks(path: Path) -> list[Block]:
    blocks = []
    for i, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise ValueError(f'{path}:{i}: expected two nonempty TSV columns')
        # ||| is old segmentation metadata, never speech or a mandatory cut.
        blocks.append(Block(parts[0].replace('|||', ' ').strip(),
                            parts[1].replace('|||', '').strip()))
    if not blocks:
        raise ValueError(f'{path}: empty blocks')
    return blocks


def timestamp(text: str) -> int:
    h, m, s, ms = map(int, re.split('[:,]', text))
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def fmt(t: int) -> str:
    if t < 0:
        raise ValueError('negative timestamp')
    s, ms = divmod(t, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def read_srt(path: Path) -> list[Cue]:
    cues = []
    for i, part in enumerate(re.split(r'\n\s*\n', path.read_text(encoding='utf-8-sig').strip()), 1):
        lines = part.splitlines()
        if len(lines) != 4 or lines[0] != str(i):
            raise ValueError(f'{path}: cue {i}: expected sequential index and ZH/EN lines')
        m = re.fullmatch(r'(\d{2,}:\d{2}:\d{2},\d{3}) --> (\d{2,}:\d{2}:\d{2},\d{3})', lines[1])
        if not m:
            raise ValueError(f'{path}: invalid timing at {i}')
        cues.append(Cue(timestamp(m[1]), timestamp(m[2]), Block(lines[3], lines[2])))
    validate_timing(cues)
    return cues


def validate_timing(cues: list[Cue]) -> None:
    if not cues:
        raise ValueError('empty subtitles')
    for i, c in enumerate(cues):
        if c.start < 0 or c.end <= c.start:
            raise ValueError(f'nonpositive cue {i + 1}')
        if i and c.start < cues[i - 1].end:
            raise ValueError(f'overlap or inversion at cue {i + 1}')


def text_identity(before: list[Block], after: list[Block]) -> dict[str, bool]:
    return {lang: compact(''.join(getattr(b, lang) for b in before)) ==
                  compact(''.join(getattr(b, lang) for b in after))
            for lang in ('en', 'zh')}


def assert_identity(before: list[Block], after: list[Block]) -> None:
    identity = text_identity(before, after)
    if not all(identity.values()):
        raise ValueError(f'text changed, omitted or reordered: {identity}')


CLOSERS = '\"”’」』)）]】}'
ABBREVIATIONS = {'mr', 'mrs', 'ms', 'dr', 'prof', 'vs', 'e.g', 'i.e', 'cf', 'st'}
PAIRS = {'(': ')', '（': '）', '[': ']', '【': '】', '{': '}', '“': '”', '「': '」', '『': '』'}


def balanced(text: str) -> bool:
    stack = []
    for c in text:
        if c in PAIRS:
            stack.append(PAIRS[c])
        elif c in PAIRS.values():
            if not stack or stack.pop() != c:
                return False
    return not stack


def sentence_stops(text: str, lang: str) -> list[int]:
    """Real full stops only: not semicolons, pauses, formula dots or word limits."""
    pattern = r'[.!?]+[' + re.escape(CLOSERS) + r']*(?=\s|$)' if lang == 'en' else r'[。！？]+[' + re.escape(CLOSERS) + r']*'
    out = []
    for m in re.finditer(pattern, text):
        if lang == 'en':
            before = text[:m.start()]
            token = re.search(r'([\w.]+)$', before)
            if token and token[1].lower() in ABBREVIATIONS:
                continue
            if m[0].startswith('..'):
                continue
        # A standalone numeric list marker is not a sentence. A formula
        # ending in a digit (e.g. 'its norm is 1.') certainly can be.
        if lang == 'en' and re.fullmatch(r'\s*\d+[.]', text[:m.end()]):
            continue
        out.append(m.end())
    return out


def filler_or_setup(en: str) -> bool:
    """Do not strand discourse fillers, a theorem introduction or a premise."""
    stops = sentence_stops(en, 'en')
    if len(stops) > 1:
        en = en[stops[-2]:]
    s = en.strip().lower().rstrip('.?!' + CLOSERS).strip()
    if re.fullmatch(r'(?:(?:okay|ok|so|well|all right|right|yes|no|good|now|yeah|um|uh)[,; ]*)+', s):
        return True
    s = re.sub(r'^(?:(?:so|now|well|okay|ok|and|all right)[, ]+)+', '', s)
    if re.match(r'^(?:let|suppose|assume|consider|take|imagine|define)\b', s):
        # Keep setup with its result, unless it already states that result.
        return not re.search(r'\b(?:then|therefore|hence|we (?:get|have|see|conclude)|it follows)\b', s)
    if re.match(r'^(?:if|when|once|unless|because|although|since)\b', s):
        return not re.search(r'[,;—]|\bthen\b', s)
    return bool(re.search(r'\b(?:the following|as follows|the claim is|the theorem is|the proof is)$', s))


def automatic_blocks(rows: list[Block]) -> tuple[list[Block], list[dict]]:
    """Coalesce broken sentences; use internal bilingual stops only if unambiguous.

    A source row is a coarse bilingual anchor, not a compulsory cue. Internal
    stops are accepted only when both languages have the same number of stops.
    Ambiguous rows remain together until a shared closure. No max words/seconds.
    Human-reviewed cuts are preferred and can override every candidate.
    """
    result, notes = [], []
    en_pending, zh_pending = '', ''
    for number, row in enumerate(rows, 1):
        es, zs = sentence_stops(row.en, 'en'), sentence_stops(row.zh, 'zh')
        ends = []
        if es and len(es) == len(zs):
            # Keep already coherent multi-sentence rows intact. Only detach
            # an unfinished tail; never create a cue for each short sentence.
            ends = [(es[-1], zs[-1])]
        elif es and zs and es[-1] == len(row.en) and zs[-1] == len(row.zh):
            ends = [(len(row.en), len(row.zh))]
            notes.append({'row': number, 'reason': 'unequal bilingual sentence counts; kept whole'})
        elif es or zs:
            notes.append({'row': number, 'reason': 'ambiguous bilingual closure; joined with context'})
        pe = pz = 0
        for ee, zz in ends:
            en_piece, zh_piece = row.en[pe:ee].strip(), row.zh[pz:zz].strip()
            en_pending = (en_pending + ' ' + en_piece).strip()
            zh_pending += zh_piece
            pe, pz = ee, zz
            if filler_or_setup(en_pending):
                continue
            result.append(Block(en_pending, zh_pending))
            en_pending = zh_pending = ''
        en_pending = (en_pending + ' ' + row.en[pe:].strip()).strip()
        zh_pending += row.zh[pz:].strip()
    if en_pending or zh_pending:
        # Never discard a recorded fragment at EOF or claim it is complete.
        if not en_pending or not zh_pending:
            if not result:
                raise ValueError('one language has no content')
            last = result.pop()
            en_pending = (last.en + ' ' + en_pending).strip()
            zh_pending = last.zh + zh_pending
        result.append(Block(en_pending, zh_pending))
        notes.append({'row': len(rows), 'reason': 'trailing source text retained; manual review required'})
    assert_identity(rows, result)
    return result, notes


def apply_boundary_review(blocks: list[Block], review: dict) -> list[Block]:
    text = compact(''.join(b.en for b in blocks))
    if hashlib.sha256(text.encode()).hexdigest() != review['en_sha256']:
        raise ValueError('context review does not match source text')
    joins = {item['en_offset'] for item in review['joins']}
    result = []
    en = zh = ''
    offset = 0
    for b in blocks:
        en = (en + ' ' + b.en).strip()
        zh += b.zh
        offset += len(compact(b.en))
        if offset not in joins:
            result.append(Block(en, zh))
            en = zh = ''
    if en or zh:
        raise ValueError('invalid review: cannot join after the final cue')
    assert_identity(blocks, result)
    return result



def visible_len(text: str) -> int:
    """Count visible characters: whitespace excluded, punctuation/math included."""
    return len(compact(text))


# A clause/sentence cut may be made after these punctuation marks when they are
# outside brackets and outside $...$ math.  Conversation pauses and word counts
# are deliberately absent: they are not syntax.
_ZH_STRONG = set('。！？')
_ZH_MEDIUM = set('；;：:')
_ZH_WEAK = set('，,、—–')
_EN_STRONG = set('.!?')
_EN_MEDIUM = set(';:')
_EN_WEAK = set(',—–')


def _cut_strength(text: str, pos: int, lang: str) -> int:
    """Return 0/2/5/15 for strong/medium/weak/word-space boundaries."""
    j = pos - 1
    while j >= 0 and text[j] in CLOSERS:
        j -= 1
    if j < 0:
        return 15
    ch = text[j]
    if lang == 'zh':
        if ch in _ZH_STRONG:
            return 0
        if ch in _ZH_MEDIUM:
            return 2
        if ch in _ZH_WEAK:
            return 5
    else:
        if ch in _EN_STRONG:
            return 0
        if ch in _EN_MEDIUM:
            return 2
        if ch in _EN_WEAK:
            return 5
    return 15


def _at_top_level(text: str, pos: int) -> bool:
    """Check brackets and $...$ before a proposed cut."""
    stack = []
    in_math = False
    for i, ch in enumerate(text[:pos]):
        if ch == '$' and (i == 0 or text[i - 1] != '\\'):
            in_math = not in_math
        if in_math:
            continue
        if ch in PAIRS:
            stack.append(PAIRS[ch])
        elif ch in PAIRS.values():
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                return False
    return not stack and not in_math


def _top_level_cuts(text: str, lang: str) -> list[tuple[int, int]]:
    """Punctuation boundaries outside formulas/brackets, with priorities."""
    marks = (_ZH_STRONG | _ZH_MEDIUM | _ZH_WEAK) if lang == 'zh' else (_EN_STRONG | _EN_MEDIUM | _EN_WEAK)
    found = []
    for i, ch in enumerate(text):
        if ch not in marks:
            continue
        if not _at_top_level(text, i + 1):
            continue
        if lang == 'en' and ch == '.':
            # Decimal points and known abbreviations are not boundaries.
            if i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
                continue
            before = text[:i]
            token = re.search(r'([A-Za-z.]+)$', before)
            if token and token.group(1).lower() in ABBREVIATIONS:
                continue
            if re.fullmatch(r'\s*\d+\.', text[:i + 1]):
                continue
        pos = i + 1
        while pos < len(text) and text[pos] in CLOSERS:
            pos += 1
        if _at_top_level(text, pos):
            found.append((pos, _cut_strength(text, pos, lang)))
    # Keep the earliest position when several punctuation marks are adjacent.
    result = {}
    for pos, strength in found:
        result[pos] = min(result.get(pos, 99), strength)
    return sorted(result.items())


def _fallback_chunks(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Split an overlong punctuation-free clause without touching $...$."""
    if visible_len(text) <= max_chars:
        return [(0, len(text))]
    # Prefer whitespace just before the limit; this is only a last resort.
    candidates = [pos for pos, kind in _top_level_cuts(text, 'zh')
                  if 0 < pos < len(text) and visible_len(text[:pos]) <= max_chars]
    for m in re.finditer(r'\s+', text):
        pos = m.end()
        if 0 < pos < len(text) and _at_top_level(text, pos) and visible_len(text[:pos]) <= max_chars:
            candidates.append(pos)
    candidates = sorted(set(candidates))
    if not candidates:
        # Find the largest safe prefix, then hard-cut as a recorded fallback.
        safe = [i + 1 for i in range(1, len(text))
                if _at_top_level(text, i + 1) and visible_len(text[:i + 1]) <= max_chars]
        candidates = [max(safe)] if safe else [min(len(text), max(1, max_chars))]
    cut = candidates[-1]
    return [(0, cut)] + [(cut + a, cut + b) for a, b in _fallback_chunks(text[cut:], max_chars)]


def _pack_zh(text: str, max_chars: int) -> tuple[list[tuple[int, int]], int]:
    """Pack complete punctuation clauses up to max_chars; return raw spans.

    A small dynamic program chooses the partition with fewest cues and then
    balances clause totals, so a 92-character first cue does not leave a
    4-character tail.  It never cuts inside a punctuation atom.
    """
    pts = [0] + [p for p, _ in _top_level_cuts(text, 'zh') if p < len(text)] + [len(text)]
    atoms = []
    fallback = 0
    for a, b in zip(pts, pts[1:]):
        if not visible_len(text[a:b]):
            continue
        if visible_len(text[a:b]) <= max_chars:
            atoms.append((a, b))
            continue
        pieces = _fallback_chunks(text[a:b], max_chars)
        fallback += max(0, len(pieces) - 1)
        atoms.extend((a + x, a + y) for x, y in pieces)
    if not atoms:
        return [(0, len(text))], 0

    weights = [visible_len(text[a:b]) for a, b in atoms]
    n = len(atoms)
    # (cue count, squared underfill) is lexicographic: balance without creating
    # unnecessary subtitle transitions.
    best = [(10**9, 10**18)] * (n + 1)
    previous = [-1] * (n + 1)
    best[0] = (0, 0)
    for i in range(1, n + 1):
        total = 0
        for j in range(i - 1, -1, -1):
            total += weights[j]
            if total > max_chars:
                break
            candidate = (best[j][0] + 1, best[j][1] + (max_chars - total) ** 2)
            if candidate < best[i]:
                best[i] = candidate
                previous[i] = j
    if previous[n] < 0:
        raise ValueError('no punctuation-respecting partition fits the Chinese cap')
    spans = []
    i = n
    while i:
        j = previous[i]
        spans.append((atoms[j][0], atoms[i - 1][1]))
        i = j
    spans.reverse()
    return spans, fallback

def _compact_prefix(text: str, pos: int) -> int:
    return visible_len(text[:pos])


def _raw_pos_for_compact(text: str, wanted: int) -> int:
    if wanted <= 0:
        return 0
    count = 0
    for i, ch in enumerate(text):
        if not ch.isspace():
            count += 1
        if count >= wanted:
            return i + 1
    return len(text)


def _english_candidates(text: str) -> list[tuple[int, int]]:
    found = dict(_top_level_cuts(text, 'en'))
    # Whitespace is a safe fallback when the translation has no punctuation
    # at the matching point.  It is deliberately weaker than punctuation.
    for m in re.finditer(r'\s+', text):
        pos = m.end()
        if pos < len(text) and _at_top_level(text, pos):
            found.setdefault(pos, 15)
    return sorted(found.items())


def _ends_with_dangling_word(text: str) -> bool:
    ends = re.findall(r"[A-Za-z']+$", text)
    if not ends:
        return False
    return ends[-1].lower() in {
        'a', 'an', 'the', 'of', 'to', 'and', 'or', 'but', 'because', 'if',
        'when', 'once', 'that', 'which', 'who', 'is', 'are', 'was', 'were',
        'can', 'could', 'will', 'would', 'should', 'with', 'in', 'on', 'for',
        'as', 'by', 'from', 'than', 'then', 'so', 'now', 'all', 'right',
    }


def _choose_en_cuts(en: str, zh: str, zh_spans: list[tuple[int, int]]) -> tuple[list[int], int]:
    """Map Chinese chunk boundaries to monotone English boundaries."""
    if len(zh_spans) <= 1:
        return [], 0
    boundaries = []
    total_zh = max(1, visible_len(zh))
    candidates = _english_candidates(en)
    last = 0
    fallback = 0
    needed = len(zh_spans) - 1
    for k, (_, zh_end) in enumerate(zh_spans[:-1], 1):
        want = _raw_pos_for_compact(en, round(len(compact(en)) * _compact_prefix(zh, zh_end) / total_zh))
        remaining = needed - k
        usable = [(p, s) for p, s in candidates if p > last and p < len(en) - remaining]
        if usable:
            def score(item):
                p, strength = item
                # Prefer a natural boundary near the proportional point.  The
                # penalty is small enough that a nearby comma can beat a far
                # sentence stop, but strong punctuation wins ties.
                penalty = strength * 2
                if strength == 15 and _ends_with_dangling_word(en[:p].rstrip()):
                    penalty += 80
                return abs(p - want) + penalty
            p = min(usable, key=score)[0]
        else:
            safe = [i + 1 for i in range(last + 1, len(en))
                    if _at_top_level(en, i + 1)]
            if not safe:
                p = min(len(en) - remaining, max(last + 1, want))
                fallback += 1
            else:
                p = min(safe, key=lambda x: abs(x - want))
                fallback += 1
        if not (last < p < len(en)):
            raise ValueError('cannot place monotone English max-character boundary')
        boundaries.append(p)
        last = p
    boundaries = _refine_en_cuts(en, boundaries)
    return boundaries, fallback



def _refine_en_cuts(en: str, boundaries: list[int]) -> list[int]:
    """Keep translated cues from starting with punctuation or ending on a stopword."""
    refined = []
    prev = 0
    for k, original in enumerate(boundaries):
        nxt = boundaries[k + 1] if k + 1 < len(boundaries) else len(en)
        p = original
        right = en[p:nxt]
        stripped = right.lstrip()
        if stripped[:1] in ',;:—–':
            base = p + len(right) - len(stripped)
            lead = re.match(r'^[,;:—–]\s*', en[base:nxt])
            if lead:
                q = base + lead.end()
                # For ", c_n, with ...", put the comma and c_n back with the
                # previous cue; only the following clause starts the new cue.
                follow = re.match(r'^[^,;:—–]{1,40}[,;:—–]\s*', en[q:nxt])
                if follow:
                    q += follow.end()
                p = q
        if p < nxt:
            for _ in range(4):
                if not _ends_with_dangling_word(en[:p].rstrip()):
                    break
                more = re.search(r'\s+', en[p:nxt])
                if not more:
                    break
                p += more.end()
            if p >= nxt:
                p = original
        if not (prev < p < nxt):
            p = original
        if not (prev < p < nxt):
            raise ValueError('refined English boundary is not monotone')
        refined.append(p)
        prev = p
    return refined


def split_long_blocks(blocks: list[Block], max_zh_chars: int) -> tuple[list[Block], dict]:
    """Batch cap Chinese cues at max_zh_chars while preserving bilingual text."""
    if max_zh_chars <= 0:
        return blocks, {'enabled': False, 'max_zh_chars': 0, 'cuts': 0, 'fallbacks': 0}
    out = []
    cuts = fallback = 0
    notes = []
    for i, block in enumerate(blocks, 1):
        if visible_len(block.zh) <= max_zh_chars:
            out.append(block)
            continue
        spans, atom_fallbacks = _pack_zh(block.zh, max_zh_chars)
        if len(spans) <= 1:
            out.append(block)
            notes.append({'block': i, 'reason': 'punctuation packing did not split'})
            continue
        en_cuts, en_fallbacks = _choose_en_cuts(block.en, block.zh, spans)
        if len(en_cuts) != len(spans) - 1:
            raise ValueError(f'block {i}: English/Chinese split count mismatch')
        start = 0
        for j, ((_, end), en_end) in enumerate(zip(spans, en_cuts + [len(block.en)])):
            en_start = start
            zh_start = 0 if j == 0 else spans[j - 1][1]
            out.append(Block(block.en[en_start:en_end].strip(),
                             block.zh[zh_start:end].strip()))
            start = en_end
        # The final span always reaches the end; add it explicitly when the
        # loop above stopped at the last English boundary.
        if len(out) > 0 and out[-1].zh == '':
            out.pop()
        cuts += len(spans) - 1
        fallback += atom_fallbacks + en_fallbacks
        if atom_fallbacks or en_fallbacks:
            notes.append({'block': i, 'fallbacks': atom_fallbacks + en_fallbacks})
    assert_identity(blocks, out)
    for b in out:
        if visible_len(b.zh) > max_zh_chars:
            raise ValueError(f'Chinese cue remains over {max_zh_chars}: {visible_len(b.zh)}')
    return out, {'enabled': True, 'max_zh_chars': max_zh_chars, 'cuts': cuts,
                 'fallbacks': fallback, 'notes': notes}


def norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', text.lower().translate(str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789')))


class RawBoundaryAligner:
    """Map moved cuts to original ASR text windows, not whole-block durations.

    Raw SRT provides phrase windows, not genuine word timestamps. Character
    interpolation inside the matching ASR window is reported as an estimate.
    Overlapping rolling display windows are clipped at the next phrase onset.
    """
    def __init__(self, raw: Path, blocks: list[Block], cache_dir: Path):
        self.text = compact(''.join(b.en for b in blocks))
        self.prefix = [0]
        for ch in self.text:
            self.prefix.append(self.prefix[-1] + len(norm(ch)))
        cache_key = hashlib.sha256((sha(raw) + self.text + 'raw-window-v1').encode()).hexdigest()
        cache = cache_dir / f'{cache_key}.json'
        if cache.exists():
            data = json.loads(cache.read_text())
        else:
            clean = norm(self.text)
            raw_parts = []
            windows = []
            for part in re.split(r'\n\s*\n', raw.read_text(encoding='utf-8-sig').strip()):
                lines = part.splitlines()
                start, end = lines[1].split(' --> ')
                raw_parts.append(norm(' '.join(lines[2:])))
                windows.append([timestamp(start), timestamp(end)])
            raw_text = ''.join(raw_parts)
            for i in range(len(windows)-1):
                windows[i][1] = min(windows[i][1], max(windows[i][0], windows[i+1][0]))
            cursor = 0
            spans = []
            for part, (start, end) in zip(raw_parts, windows):
                if part:
                    spans.append([cursor, cursor+len(part), start, end])
                cursor += len(part)
            matches = [[m.a, m.b, m.size] for m in difflib.SequenceMatcher(None, clean, raw_text, autojunk=False).get_matching_blocks() if m.size]
            data = {'matches': matches, 'spans': spans, 'normalized_length': len(clean),
                    'coverage': sum(m[2] for m in matches) / max(1, len(clean))}
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data)+'\n')
        self.data = data
        self.match_ends = [m[0]+m[2] for m in data['matches']]
        self.raw_ends = [w[1] for w in data['spans']]

    def time(self, offset: int) -> tuple[int | None, int]:
        pos = self.prefix[offset]
        i = bisect.bisect_right(self.match_ends, pos)
        if i == len(self.match_ends):
            return None, 0
        source, target, size = self.data['matches'][i]
        advance = max(0, source - pos)
        raw_pos = target + max(0, pos-source)
        k = bisect.bisect_right(self.raw_ends, raw_pos)
        a, b, start, end = self.data['spans'][k]
        return start + round((raw_pos-a) / (b-a) * (end-start)), advance


class ExistingTimeline:
    """Keep baseline anchors instead of globally realigning already corrected ASR.

    Exact original starts/ends remain exact except artificial tiny blank gaps.
    New intra-cue boundaries use RawBoundaryAligner when available, with a
    reported local estimate fallback. These are NOT audio-forced word alignments.
    Merging spans internal pauses, preserving blackboard thinking time.
    """
    def __init__(self, cues: list[Cue], aligner: RawBoundaryAligner | None = None):
        self.cues = cues
        self.aligner = aligner
        self.alignment_notes = []
        self.ends = []
        self.intervals = []
        offset = 0
        for c in cues:
            n = len(compact(c.block.en))
            if not n:
                raise ValueError('empty English cue')
            self.intervals.append((offset, offset + n))
            offset += n
            self.ends.append(offset)
        self.total = offset

    def boundary(self, offset: int) -> tuple[int, int, bool]:
        """Return (previous end, next start, interpolated)."""
        if offset == 0:
            s = self.cues[0].start
            return s, s, False
        if offset == self.total:
            e = self.cues[-1].end
            return e, e, False
        if not 0 < offset < self.total:
            raise ValueError('boundary outside source text')
        k = bisect.bisect_left(self.ends, offset)
        cue = self.cues[k]
        a, b = self.intervals[k]
        if offset == b:
            nxt = self.cues[k + 1].start
            # Remove artificial 50ms blanks and similar sub-perceptual holes.
            # Real pauses remain; within merged units they are naturally held.
            end = nxt if 0 <= nxt - cue.end <= 100 else cue.end
            return end, nxt, False
        t = cue.start + round((offset - a) / (b - a) * (cue.end - cue.start))
        if self.aligner:
            raw_t, advance = self.aligner.time(offset)
            if raw_t is not None and cue.start < raw_t < cue.end:
                t = raw_t
                if advance:
                    self.alignment_notes.append({'offset': offset, 'next_matched_char_distance': advance})
            else:
                self.alignment_notes.append({'offset': offset, 'fallback': 'ASR match outside original anchor; local estimate', 'raw_time': raw_t})
        return t, t, True

    def build(self, blocks: list[Block]) -> tuple[list[Cue], list[int]]:
        offsets = [0]
        for b in blocks:
            offsets.append(offsets[-1] + len(compact(b.en)))
        if offsets[-1] != self.total:
            raise ValueError('timeline/text coverage mismatch')
        bounds = [self.boundary(p) for p in offsets]
        out = [Cue(bounds[i][1], bounds[i + 1][0], b) for i, b in enumerate(blocks)]
        validate_timing(out)  # fail instead of silently moving speech to fix collisions
        return out, [i for i, b in enumerate(bounds) if b[2]]


def blocks_text(blocks: list[Block]) -> str:
    return ''.join(f'{b.en}\t{b.zh}\n' for b in blocks)


def srt_text(cues: list[Cue]) -> str:
    return '\n'.join(f'{i}\n{fmt(c.start)} --> {fmt(c.end)}\n{c.block.zh}\n{c.block.en}\n'
                     for i, c in enumerate(cues, 1))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_copy(source: Path, target: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.subtitle-', delete=False) as f:
        temporary = Path(f.name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def stage(root: Path, stage_dir: Path, selected: set[str], baseline: Path | None = None,
          max_zh_chars: int = 0) -> dict:
    stage_dir.mkdir(parents=True, exist_ok=False)
    after = stage_dir / 'after'
    after.mkdir()
    report = {'layout_version': 'normalized-v1',
              'policy': 'complete bilingual meaning first; optional visible-Chinese-character cap',
              'max_zh_chars': max_zh_chars,
              'timing': 'baseline endpoints preserved; moved cuts match original ASR phrase windows (estimated, not forced-aligned)' ,
              'created': datetime.now().isoformat(timespec='seconds'),
              'lectures': [], 'files': []}
    context_path = root / 'archive' / 'reviews' / 'boundary-review.json'
    context_reviews = json.loads(context_path.read_text())['lectures'] if context_path.exists() else {}
    source_root = baseline or root
    report['baseline'] = str(source_root)
    paths = sorted((source_root / 'subtitles' / 'blocks').glob('abstract-algebra-*.blocks.tsv'))
    for path in paths:
        match = re.fullmatch(r'abstract-algebra-(\d{2})\.blocks\.tsv', path.name)
        if not match:
            continue
        number = match.group(1)
        if selected and number not in selected:
            continue
        srt = source_root / 'videos' / f'abstract-algebra-{number}.zh-en.srt'
        source = read_blocks(path)
        old = read_srt(srt)
        assert_identity(source, [c.block for c in old])
        reviewed = root / 'archive' / 'reviews' / f'abstract-algebra-{number}.reviewed.blocks.tsv'
        if reviewed.exists():
            blocks = read_blocks(reviewed)
            assert_identity(source, blocks)
            notes = []
            method = 'reviewed'
        else:
            blocks, notes = automatic_blocks(source)
            method = 'conservative-automatic'
            if number in context_reviews:
                blocks = apply_boundary_review(blocks, context_reviews[number])
                method = 'conservative-automatic + context-reviewed joins'
        split_notes = None
        if max_zh_chars:
            blocks, split_notes = split_long_blocks(blocks, max_zh_chars)
            method += f' + max-zh={max_zh_chars}'
        # No unpunctuated whole-lecture fallback: require a real reviewed plan.
        if len(blocks) <= 1 and len(source) > 1:
            raise ValueError(f'{number}: no reliable boundaries; provide a semantic review, not a giant cue')
        raw = root / 'subtitles' / 'raw' / f'abstract-algebra-{number}.raw.en.srt'
        aligner = RawBoundaryAligner(raw, source, root / 'archive' / 'qa' / 'cache')
        timeline = ExistingTimeline(old, aligner)
        cues, estimated = timeline.build(blocks)
        staged_tsv, staged_srt = after / path.name, after / srt.name
        staged_tsv.write_text(blocks_text(blocks), encoding='utf-8')
        staged_srt.write_text(srt_text(cues), encoding='utf-8')
        assert_identity(source, read_blocks(staged_tsv))
        assert_identity(source, [c.block for c in read_srt(staged_srt)])
        durations = [c.end - c.start for c in cues]
        item = {'lecture': number, 'method': method, 'before': len(old), 'after': len(cues),
                'identity': text_identity(source, blocks), 'overlaps': 0, 'inversions': 0,
                'min_seconds': min(durations) / 1000, 'max_seconds': max(durations) / 1000,
                'interpolated_boundary_count': len(estimated),
                'raw_alignment_coverage': aligner.data['coverage'],
                'alignment_notes': timeline.alignment_notes,
                'interpolated_boundary_after_cues': estimated,
                'sub_100ms_gaps': sum(0 < cues[i].start - cues[i-1].end <= 100 for i in range(1, len(cues))),
                'notes': notes,
                'split_notes': split_notes,
                'over_max_zh_after': sum(visible_len(b.zh) > max_zh_chars for b in blocks) if max_zh_chars else 0,
                'longest_units': [{'cue': i+1, 'seconds': (c.end-c.start)/1000, 'en': c.block.en, 'zh': c.block.zh}
                                  for i,c in sorted(enumerate(cues), key=lambda x:x[1].end-x[1].start, reverse=True)[:5]]}
        report['lectures'].append(item)
        targets = [(root / 'subtitles' / 'blocks' / path.name, staged_tsv),
                   (root / 'videos' / srt.name, staged_srt)]
        for target, source_file in targets:
            report['files'].append({'target': str(target.relative_to(root)),
                                    'staged': str(source_file.relative_to(stage_dir)),
                                    'before_sha256': sha(target), 'after_sha256': sha(source_file)})
        cap = f'; max-zh={max(sorted(visible_len(b.zh) for b in blocks))}' if max_zh_chars else ''
        print(f'{number}: {len(old)} -> {len(cues)}; {method}; EN/ZH lossless; no overlaps{cap}; '
              f'longest {max(durations)/1000:.1f}s', flush=True)
    if not report['lectures']:
        raise ValueError('no selected lectures')
    (stage_dir / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def apply(root: Path, stage_dir: Path) -> dict:
    report = json.loads((stage_dir / 'manifest.json').read_text())
    if report.get('applied'):
        raise ValueError('already applied; do not reuse a mutable baseline')
    # Whole-batch validation before the first backup or write (optimistic lease).
    for f in report['files']:
        if sha(root / f['target']) != f['before_sha256']:
            raise ValueError(f"source changed since staging: {f['target']}")
        if sha(stage_dir / f['staged']) != f['after_sha256']:
            raise ValueError(f"staged output changed: {f['staged']}")
    before = stage_dir / 'before'
    before.mkdir(exist_ok=False)
    for f in report['files']:
        b = before / f['target']
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / f['target'], b)
        if sha(b) != f['before_sha256']:
            raise ValueError('backup verification failed')
    # Interrupted installation can be restored from verified before/ files.
    for f in report['files']:
        atomic_copy(stage_dir / f['staged'], root / f['target'])
        if sha(root / f['target']) != f['after_sha256']:
            raise ValueError('installed hash verification failed')
    report['applied'] = datetime.now().isoformat(timespec='seconds')
    (stage_dir / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--stage-dir', type=Path)
    p.add_argument('--baseline', type=Path, help='immutable project mirror (with subtitles/ and videos/) for repeatable rebuilding')
    p.add_argument('--lectures', nargs='*', default=[])
    p.add_argument('--max-zh-chars', type=int, default=0, help='optional visible Chinese-character cap; 0 disables')
    p.add_argument('--apply-staged', type=Path, help='apply a previously inspected staging directory')
    args = p.parse_args()
    root = args.root.resolve()
    if args.apply_staged:
        report = apply(root, args.apply_staged.resolve())
        print(f"Applied {len(report['files'])} files from a verified backup.")
    else:
        stage_dir = args.stage_dir or root / 'archive' / 'qa' / datetime.now().strftime('stage-%Y%m%d-%H%M%S')
        stage(root, stage_dir.resolve(), {f'{int(n):02}' for n in args.lectures},
                  args.baseline.resolve() if args.baseline else None,
                  max_zh_chars=args.max_zh_chars)
        print(f'Staged only (originals untouched): {stage_dir}')


if __name__ == '__main__':
    main()
