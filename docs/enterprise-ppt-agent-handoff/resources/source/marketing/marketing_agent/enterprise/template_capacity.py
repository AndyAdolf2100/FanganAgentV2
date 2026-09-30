"""Deterministic capacity estimates; browser measurements remain the final visual check."""
import math
import unicodedata

BODY_ROLES = {'body', 'itemBody'}
BODY_RESIZABLE_ROLES = BODY_ROLES | {'title', 'subtitle', 'itemTitle'}

def original_size(element):
    return next((r['size'] for p in element.get('paragraphs', []) for r in p['runs']), 24)

def body_style(element, canvas_height, scale=1, minimum=18):
    original = original_size(element)
    # 18 px at 720 canvas pixels; never enlarge a small source font.
    floor = min(original, minimum * canvas_height / 720)
    return {'fontSize': round(max(floor, original * scale), 2), 'lineHeight': 1.25}

def metrics(element, style=None):
    layout = element.get('textLayout', {})
    top, right, bottom, left = layout.get('padding', [3, 6, 3, 6])
    size = (style or {}).get('fontSize', original_size(element))
    paragraphs = element.get('paragraphs', [])
    line_height = (style or {}).get('lineHeight', (paragraphs[0].get('lineHeight') if paragraphs else None) or 1.2)
    # Reserve a little space for platform font metrics and descenders.
    width = max(0, element['width'] - left - right) / (size * 1.15)
    rows = max(0, math.floor((element['height'] - top - bottom) / (size * line_height * 1.05)))
    return width, rows, layout.get('wrap', True)

def advance(char):
    if unicodedata.combining(char): return 0
    if char == '\t': return 2.6
    if unicodedata.east_asian_width(char) in ('W', 'F') or ord(char) > 127: return 1
    return .95 if char in 'MW@%' else .65

def prefix_length(source, element, style=None):
    width, rows, wrap = metrics(element, style)
    if rows < 1 or width <= 0: return 0
    row, used = 1, 0
    for i, char in enumerate(source):
        if char == '\n':
            # A trailing newline carries no ink; keep it with the preceding page.
            if row == rows: return i + 1
            row += 1; used = 0
        else:
            amount = advance(char)
            if amount > width: return i
            if used + amount > width:
                if not wrap or row == rows: return i
                row += 1; used = 0
            used += amount
    return len(source)

def take(source, element, style=None):
    end = prefix_length(source, element, style)
    # Prefer a paragraph/sentence boundary without leaving most of a slot empty.
    if 0 < end < len(source):
        for i in range(end - 1, int(end * .85), -1):
            if source[i] in '\n。！？；': end = i + 1; break
    return source[:end], source[end:]

def fits(source, element, style=None):
    return prefix_length(source, element, style) >= len(source)
