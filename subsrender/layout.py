"""Pixel-based subtitle layout primitives.

All geometry is expressed in pixels.  A ``style`` is a mapping with these
keys:

* ``font_size`` -- em size in pixels, greater than zero;
* ``line_height`` -- baseline-to-baseline distance in pixels, at least
  ``font_size``;
* ``outline`` -- stroke width in pixels, non-negative;
* ``align`` -- one of ``"left"``, ``"center"`` or ``"right"``.

Coordinates are computed as floats.  Callers compare geometry with a
tolerance of 0.5 px.  Invalid input raises :class:`SubRenderError` rather
than silently falling back to a default.
"""


class SubRenderError(Exception):
    """Raised when subtitle text or style cannot be rendered."""


HALF_WIDTH_RATIO = 0.5
ALIGNMENTS = ("left", "center", "right")
ANCHORS = (
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
CLOSING_PUNCTUATION = "。，、；：？！）】》」』,.!?;:)]}"

# Horizontal/vertical placement encoded by each anchor name.
_ANCHOR_GRID = {
    "top-left": ("left", "top"),
    "top-center": ("center", "top"),
    "top-right": ("right", "top"),
    "center-left": ("left", "center"),
    "center": ("center", "center"),
    "center-right": ("right", "center"),
    "bottom-left": ("left", "bottom"),
    "bottom-center": ("center", "bottom"),
    "bottom-right": ("right", "bottom"),
}

_NUMERIC_STYLE_KEYS = ("font_size", "line_height", "outline")


def _is_number(value) -> bool:
    """True for real int/float values (booleans are rejected)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_full_width(char: str) -> bool:
    """True when ``char`` belongs to an East-Asian wide/full-width range.

    CJK ideographs, kana, hangul and full-width punctuation occupy one
    whole em square; every other code point is treated as half-width.
    The ranges mirror the ``W``/``F`` categories of the Unicode East
    Asian Width property without importing ``unicodedata``.
    """
    code = ord(char)
    return (
        0x1100 <= code <= 0x115F  # Hangul Jamo
        or 0x2E80 <= code <= 0x303E  # CJK radicals, Kangxi, CJK symbols
        or 0x3041 <= code <= 0x33FF  # Kana, bopomofo, CJK symbols
        or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xA000 <= code <= 0xA4CF  # Yi syllables/radicals
        or 0xAC00 <= code <= 0xD7A3  # Hangul Syllables
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0xFE30 <= code <= 0xFE4F  # CJK Compatibility Forms
        or 0xFF01 <= code <= 0xFF60  # Fullwidth Forms
        or 0xFFE0 <= code <= 0xFFE6  # Fullwidth signs
        or 0x20000 <= code <= 0x2FA1F  # CJK Extensions B and later
    )


def _char_width(char: str, font_size: float) -> float:
    """Pixel width of one glyph: full-width chars take one em."""
    if _is_full_width(char):
        return float(font_size)
    return float(font_size) * HALF_WIDTH_RATIO


def _line_width(line: str, font_size: float) -> float:
    return sum((_char_width(char, font_size) for char in line), 0.0)


def _require_canvas(canvas) -> None:
    if (
        not isinstance(canvas, (tuple, list))
        or len(canvas) != 2
        or not all(_is_number(dim) and dim > 0 for dim in canvas)
    ):
        raise SubRenderError("canvas must be a (positive width, positive height) pair")


def validate_style(style: dict) -> list[str]:
    """Return a list of problems with ``style``; return ``[]`` if valid.

    The checks are:

    * ``font_size`` must be greater than zero;
    * ``line_height`` must be at least ``font_size`` (otherwise lines
      overlap);
    * ``align`` must be one of ``"left"``, ``"center"``, ``"right"``;
    * required keys must be present and numeric.

    Every message names the offending field (for example
    ``"line_height"`` or ``"align"``) so callers can act on it.  This
    function returns messages instead of raising, and it never returns a
    partial layout.  Sizes are in pixels and are compared with a 0.5 px
    tolerance.
    """
    issues: list[str] = []
    if not isinstance(style, dict):
        return ["style: a mapping with font_size, line_height, outline and align is required"]

    for key in _NUMERIC_STYLE_KEYS:
        if key not in style:
            issues.append(f"{key}: required numeric field is missing")
        elif not _is_number(style[key]):
            issues.append(f"{key}: must be a numeric pixel value")

    if "align" not in style:
        issues.append("align: required field is missing")
    elif style["align"] not in ALIGNMENTS:
        issues.append(f"align: unknown alignment {style['align']!r}")

    font_size = style.get("font_size")
    line_height = style.get("line_height")
    outline = style.get("outline")

    if _is_number(font_size) and font_size <= 0:
        issues.append("font_size: must be greater than zero")
    if (
        _is_number(line_height)
        and _is_number(font_size)
        and font_size > 0
        and line_height < font_size
    ):
        issues.append("line_height: must be at least font_size or lines overlap")
    if _is_number(outline) and outline < 0:
        issues.append("outline: must be non-negative")

    return issues


def _require_valid_style(style: dict) -> None:
    issues = validate_style(style)
    if issues:
        raise SubRenderError("invalid style: " + "; ".join(issues))


def measure_text(text: str, style: dict) -> tuple[float, float]:
    """Estimate the pixel size of ``text`` under ``style``.

    Each CJK ideograph is full-width and contributes ``font_size`` pixels.
    Every other character (ASCII letters, digits, spaces, punctuation) is
    half-width and contributes ``font_size * 0.5`` pixels.  Embedded
    newlines split the text into lines.  The returned width is the widest
    line; the returned height is the line count multiplied by
    ``line_height``.  Empty text measures ``(0.0, 0.0)``.

    ``font_size`` and ``line_height`` are pixels.  Invalid style raises
    ``SubRenderError``.  Geometry is compared with a 0.5 px tolerance.
    """
    _require_valid_style(style)
    if text == "":
        return (0.0, 0.0)

    font_size = float(style["font_size"])
    line_height = float(style["line_height"])
    raw_lines = text.split("\n")
    widths = [_line_width(line, font_size) for line in raw_lines]
    return (max(widths), float(len(raw_lines)) * line_height)


def wrap_lines(text: str, style: dict, max_width: float) -> list[str]:
    """Greedily wrap ``text`` into lines no wider than ``max_width`` px.

    Widths use the same full-width/half-width rule as :func:`measure_text`.
    A single glyph wider than ``max_width`` still occupies one line by
    itself, so wrapping always makes progress.  A line never starts with
    closing punctuation from ``。，、；：？！）】》」』,.!?;:)]}``; such a
    character stays on the preceding line even when that makes the line
    wider than ``max_width``.  Embedded newlines force a break.  Empty
    text returns ``[]``.

    ``max_width`` is in pixels and must be positive.  Invalid style or
    ``max_width`` raises ``SubRenderError``.  Geometry is compared with a
    0.5 px tolerance.
    """
    _require_valid_style(style)
    if not _is_number(max_width) or max_width <= 0:
        raise SubRenderError("max_width must be a positive pixel value")
    if text == "":
        return []

    font_size = float(style["font_size"])
    wrapped: list[str] = []

    for segment in text.split("\n"):
        if segment == "":
            # A forced newline may produce an intentionally blank line.
            wrapped.append("")
            continue

        current = ""
        current_width = 0.0
        for char in segment:
            char_w = _char_width(char, font_size)
            if current == "":
                # Even an oversized glyph starts its own line so that
                # wrapping always makes progress.
                current = char
                current_width = char_w
            elif current_width + char_w <= max_width:
                current += char
                current_width += char_w
            elif char in CLOSING_PUNCTUATION:
                # Keep the punctuation on the preceding line rather than
                # opening a new line with an orphan mark.
                current += char
                current_width += char_w
            else:
                wrapped.append(current)
                current = char
                current_width = char_w
        wrapped.append(current)

    return wrapped


def layout_block(lines: list[str], style: dict, canvas: tuple, anchor: str) -> dict:
    """Lay out prepared ``lines`` as one subtitle block.

    ``canvas`` is ``(width, height)`` in pixels and ``anchor`` is one of
    the nine names in :data:`ANCHORS`.  ``origin`` is the block's top-left
    corner, returned as ``(x, y)``.  The block width is the widest line;
    its height is ``len(lines) * line_height``.  Each line box in
    ``line_boxes`` is ``{"x", "y", "w", "h", "text"}`` and advances by
    exactly ``line_height`` pixels.  Horizontal placement of a shorter
    line inside the block follows ``style["align"]``.

    Anchoring uses the whole block, including all lines, measured without
    the outline (apply :func:`outline_box` separately).  Unknown anchors
    and invalid styles raise ``SubRenderError``.  Returns ``{"origin":
    (x, y), "line_boxes": [...]}``.  Geometry is in pixels and is compared
    with a 0.5 px tolerance.
    """
    _require_valid_style(style)
    _require_canvas(canvas)
    if anchor not in _ANCHOR_GRID:
        raise SubRenderError(f"anchor: unknown anchor {anchor!r}")

    canvas_w, canvas_h = float(canvas[0]), float(canvas[1])
    font_size = float(style["font_size"])
    line_height = float(style["line_height"])
    align = style["align"]

    line_widths = [_line_width(line, font_size) for line in lines]
    block_width = max(line_widths, default=0.0)
    block_height = float(len(lines)) * line_height

    horizontal, vertical = _ANCHOR_GRID[anchor]
    if horizontal == "left":
        origin_x = 0.0
    elif horizontal == "right":
        origin_x = canvas_w - block_width
    else:
        origin_x = (canvas_w - block_width) / 2.0

    if vertical == "top":
        origin_y = 0.0
    elif vertical == "bottom":
        origin_y = canvas_h - block_height
    else:
        origin_y = (canvas_h - block_height) / 2.0

    # A fresh list is built for this block alone; callers can never share
    # or mutate another block's line boxes through it.
    line_boxes = []
    for index, line in enumerate(lines):
        line_w = line_widths[index]
        if align == "left":
            line_x = origin_x
        elif align == "right":
            line_x = origin_x + (block_width - line_w)
        else:
            line_x = origin_x + (block_width - line_w) / 2.0
        line_boxes.append(
            {
                "x": float(line_x),
                "y": origin_y + index * line_height,
                "w": float(line_w),
                "h": line_height,
                "text": line,
            }
        )

    return {"origin": (float(origin_x), float(origin_y)), "line_boxes": line_boxes}


def outline_box(box: dict, width: float) -> dict:
    """Expand ``box`` by an outline stroke of ``width`` pixels.

    ``box`` is ``{"x", "y", "w", "h"}``.  The returned mapping contains
    ``{"outer": rect, "inner": rect}``: ``outer`` grows by ``width`` on
    all four sides, while ``inner`` is an unchanged copy of the input.
    The input mapping is never mutated; a zero-width stroke yields equal
    but distinct rects.  Negative widths raise ``SubRenderError``.

    All values are pixels and are compared with a 0.5 px tolerance.
    """
    if not _is_number(width) or width < 0:
        raise SubRenderError("outline width must be a non-negative pixel value")

    inner = {key: box[key] for key in ("x", "y", "w", "h")}
    outer = {
        "x": float(box["x"]) - width,
        "y": float(box["y"]) - width,
        "w": float(box["w"]) + 2.0 * width,
        "h": float(box["h"]) + 2.0 * width,
    }
    return {"outer": outer, "inner": inner}


def safe_area_fit(box: dict, canvas: tuple, margin: float) -> dict:
    """Translate ``box`` into the canvas safe area without resizing it.

    ``canvas`` is ``(width, height)`` and ``margin`` is in pixels.  The
    safe rectangle is ``[margin, width - margin] x [margin, height -
    margin]``.  A box already inside is returned as an equal copy.  On
    each axis, only the smallest translation needed to remove the overflow
    is applied, and it follows the overflow direction: a left overflow
    moves right, a right overflow moves left, and likewise vertically.  A
    box larger than the safe area on an axis has its left (or top) edge
    aligned with ``margin`` on that axis.  The width and height never
    change and the result is never clipped.

    Invalid canvas or margin raises ``SubRenderError``.  Geometry is in
    pixels and is compared with a 0.5 px tolerance.
    """
    _require_canvas(canvas)
    if not _is_number(margin) or margin < 0:
        raise SubRenderError("margin must be a non-negative pixel value")

    canvas_w, canvas_h = float(canvas[0]), float(canvas[1])
    x, y = float(box["x"]), float(box["y"])
    w, h = float(box["w"]), float(box["h"])
    low_x, high_x = float(margin), canvas_w - float(margin)
    low_y, high_y = float(margin), canvas_h - float(margin)

    if w > high_x - low_x:
        # Wider than the safe area: anchor the near edge to the margin
        # instead of pushing the far edge out of the other side.
        x = low_x
    elif x < low_x:
        # Left/top overflow: push inward to the near edge.
        x = low_x
    elif x + w > high_x:
        # Right/bottom overflow: pull back so the far edge fits.
        x = high_x - w

    if h > high_y - low_y:
        y = low_y
    elif y < low_y:
        y = low_y
    elif y + h > high_y:
        y = high_y - h

    return {"x": float(x), "y": float(y), "w": float(w), "h": float(h)}


def apply_shadow(box: dict, offset: tuple, blur: float) -> dict:
    """Compute the shadow rectangle for ``box``.

    ``offset`` is ``(dx, dy)`` in pixels and ``blur`` is a non-negative
    pixel radius.  The unclipped shadow is ``box`` translated by ``offset``
    and then expanded by ``blur`` on every side, so its width is
    ``box["w"] + 2 * blur``.  If ``box`` contains ``"canvas": (width,
    height)``, the rectangle is clipped to ``(0, 0, width, height)``:
    coordinates never go negative, and a fully outside shadow becomes a
    zero-size rectangle at the canvas boundary.  Without a ``"canvas"``
    key the unclipped rectangle is returned.

    The input mapping is never mutated.  Negative ``blur`` raises
    ``SubRenderError``.  Geometry is in pixels and is compared with a 0.5
    px tolerance.
    """
    if not _is_number(blur) or blur < 0:
        raise SubRenderError("blur must be a non-negative pixel radius")

    dx, dy = offset
    shadow_x = float(box["x"]) + float(dx) - float(blur)
    shadow_y = float(box["y"]) + float(dy) - float(blur)
    shadow_w = float(box["w"]) + 2.0 * float(blur)
    shadow_h = float(box["h"]) + 2.0 * float(blur)

    if "canvas" not in box:
        return {"x": shadow_x, "y": shadow_y, "w": shadow_w, "h": shadow_h}

    canvas_w, canvas_h = float(box["canvas"][0]), float(box["canvas"][1])
    # Clamp both edges to [0, canvas size]; an empty intersection then
    # collapses to a zero-size rect sitting on the canvas boundary.
    x0 = min(max(shadow_x, 0.0), canvas_w)
    y0 = min(max(shadow_y, 0.0), canvas_h)
    x1 = min(max(shadow_x + shadow_w, 0.0), canvas_w)
    y1 = min(max(shadow_y + shadow_h, 0.0), canvas_h)
    return {"x": x0, "y": y0, "w": max(0.0, x1 - x0), "h": max(0.0, y1 - y0)}


def render_batch(blocks: list[dict], canvas: tuple, style: dict) -> dict:
    """Lay out every block atomically and preserve input order.

    ``blocks`` is a list of mappings with ``"lines"`` (a list of strings)
    and ``"anchor"``.  ``canvas`` and ``style`` are shared by all blocks.
    Each output item is the result of :func:`layout_block` plus its
    ``"anchor"``.  No line-box buffer is reused between blocks, so items
    cannot leak geometry into one another.

    The operation is atomic: all blocks and the style are validated before
    any layout is produced.  If any block is invalid (for example an
    unknown anchor or missing lines), ``SubRenderError`` is raised and no
    partial result is returned.  On success the result is ``{"items":
    [...], "count": len(items)}`` in exactly the input order.

    Geometry is in pixels and is compared with a 0.5 px tolerance.
    """
    _require_canvas(canvas)
    _require_valid_style(style)
    if not isinstance(blocks, (list, tuple)):
        raise SubRenderError("blocks must be a list of block mappings")

    # Validation pass: nothing is laid out until every block is accepted,
    # so a failure never leaves a partially produced batch behind.
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or "lines" not in block or "anchor" not in block:
            raise SubRenderError(f"block {index}: requires 'lines' and 'anchor' fields")
        if not isinstance(block["lines"], list) or not all(
            isinstance(line, str) for line in block["lines"]
        ):
            raise SubRenderError(f"block {index}: 'lines' must be a list of strings")
        if block["anchor"] not in ANCHORS:
            raise SubRenderError(f"block {index}: unknown anchor {block['anchor']!r}")

    items = []
    for block in blocks:
        # layout_block allocates a brand-new line-box list per call, so
        # blocks can never share or corrupt one another's geometry.
        laid_out = layout_block(block["lines"], style, canvas, block["anchor"])
        laid_out["anchor"] = block["anchor"]
        items.append(laid_out)

    return {"items": items, "count": len(items)}
