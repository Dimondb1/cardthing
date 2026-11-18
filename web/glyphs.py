"""The site's own icons (web/templates/web/includes/feed_sprite.svg) drawn by Pillow, for pictures the server makes:
the story picture's game, its facts and its search bar (web/share.py). Pillow reads no SVG and the server has no
browser, so the few shapes the sprite uses (path with M L H V C A Z, circle, rect; a 24 unit box, a stroke 2 units
wide with round ends and joins, a path filled where it says so) are drawn here, eight times over size and scaled
down so the edges are smooth. Drawn from the sprite itself, an icon changed there changes in the pictures too."""

import math
import re
from functools import lru_cache
from pathlib import Path

SPRITE = Path(__file__).resolve().parent / "templates" / "web" / "includes" / "feed_sprite.svg"
BOX = 24          # the sprite's viewBox
STROKE = 2        # its stroke-width
OVER = 8          # drawn this many times over size, then scaled down
CURVE_STEPS = 24  # straight pieces a curve or an arc is drawn with


@lru_cache(maxsize=1)
def symbols():
    """{id: the symbol's inner markup} from the sprite."""
    text = SPRITE.read_text(encoding="utf-8")
    return {name: body for name, body in re.findall(r'<symbol id="([\w-]+)"[^>]*>(.*?)</symbol>', text, re.S)}


def numbers(text):
    return [float(n) for n in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", text)]


def attributes(tag):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def arc_points(start, rx, ry, rotation, large, sweep, end):
    """An SVG arc from ``start`` to ``end`` as points (the SVG spec's endpoint to centre conversion)."""
    (x1, y1), (x2, y2) = start, end
    if rx == 0 or ry == 0 or start == end:
        return [end]
    phi = math.radians(rotation)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    xp, yp = cos * dx + sin * dy, -sin * dx + cos * dy
    rx, ry = abs(rx), abs(ry)
    scale = (xp * xp) / (rx * rx) + (yp * yp) / (ry * ry)
    if scale > 1:
        rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)
    top = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp
    root = math.sqrt(max(0.0, top / (rx * rx * yp * yp + ry * ry * xp * xp)))
    if large == sweep:
        root = -root
    cxp, cyp = root * rx * yp / ry, -root * ry * xp / rx
    cx = cos * cxp - sin * cyp + (x1 + x2) / 2
    cy = sin * cxp + cos * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    begin = angle(1, 0, (xp - cxp) / rx, (yp - cyp) / ry)
    turn = angle((xp - cxp) / rx, (yp - cyp) / ry, (-xp - cxp) / rx, (-yp - cyp) / ry)
    if not sweep and turn > 0:
        turn -= 2 * math.pi
    elif sweep and turn < 0:
        turn += 2 * math.pi
    points = []
    for step in range(1, CURVE_STEPS + 1):
        t = begin + turn * step / CURVE_STEPS
        x, y = rx * math.cos(t), ry * math.sin(t)
        points.append((cos * x - sin * y + cx, sin * x + cos * y + cy))
    return points


def path_parts(d):
    """[(points, closed)] for a path's ``d``: absolute M L H V C A Z, the sprite's whole vocabulary."""
    parts, points, here, closed = [], [], (0.0, 0.0), False
    for command, args in re.findall(r"([MLHVCAZ])([^MLHVCAZ]*)", d):
        values = numbers(args)
        if command == "M":
            if len(points) > 1:
                parts.append((points, closed))
            here = (values[0], values[1])
            points, closed = [here], False
            for i in range(2, len(values) - 1, 2):   # pairs after the first are lines
                here = (values[i], values[i + 1])
                points.append(here)
        elif command == "L":
            for i in range(0, len(values) - 1, 2):
                here = (values[i], values[i + 1])
                points.append(here)
        elif command == "H":
            for x in values:
                here = (x, here[1])
                points.append(here)
        elif command == "V":
            for y in values:
                here = (here[0], y)
                points.append(here)
        elif command == "C":
            for i in range(0, len(values) - 5, 6):
                (x0, y0), (x1, y1), (x2, y2), (x3, y3) = here, *zip(values[i:i + 6:2], values[i + 1:i + 6:2])
                for step in range(1, CURVE_STEPS + 1):
                    t = step / CURVE_STEPS
                    u = 1 - t
                    points.append((u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
                                   u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3))
                here = (x3, y3)
        elif command == "A":
            for i in range(0, len(values) - 6, 7):
                rx, ry, rotation, large, sweep, x, y = values[i:i + 7]
                points += arc_points(here, rx, ry, rotation, bool(large), bool(sweep), (x, y))
                here = (x, y)
        elif command == "Z":
            closed = True
            if points:
                here = points[0]
    if len(points) > 1:
        parts.append((points, closed))
    return parts


@lru_cache(maxsize=64)
def mask(name, size):
    """The icon ``name`` (a symbol id, such as "rr-search") as a ``size`` pixel square "L" image: 255 where it is
    drawn. A name the sprite does not have is a KeyError."""
    from PIL import Image, ImageDraw

    body = symbols()[name]
    big = size * OVER
    unit = big / BOX
    width = max(1, round(STROKE * unit))
    half = width / 2
    image = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(image)

    def at(point):
        return (point[0] * unit, point[1] * unit)

    def cap(point):
        x, y = at(point)
        draw.ellipse((x - half, y - half, x + half, y + half), fill=255)

    for tag, rest in re.findall(r"<(path|circle|rect)\b([^>]*)>", body):
        found = attributes(rest)
        if tag == "circle":
            cx, cy, r = (float(found[key]) * unit for key in ("cx", "cy", "r"))
            draw.ellipse((cx - r - half, cy - r - half, cx + r + half, cy + r + half), outline=255, width=width)
        elif tag == "rect":
            x, y, w, h = (float(found[key]) * unit for key in ("x", "y", "width", "height"))
            radius = float(found.get("rx", 0)) * unit
            draw.rounded_rectangle((x - half, y - half, x + w + half, y + h + half), radius=radius + half,
                                   outline=255, width=width)
        else:
            filled = found.get("fill", "none") not in ("none", "")
            for points, closed in path_parts(found.get("d", "")):
                line = [at(point) for point in points]
                if filled:
                    draw.polygon(line, fill=255)
                if closed:
                    line.append(line[0])
                draw.line(line, fill=255, width=width, joint="curve")
                for point in points:   # round ends and joins, the closing one and short curved pieces included
                    cap(point)
    return image.resize((size, size), Image.LANCZOS)


def paste(card, name, size, at, colour):
    """Draw the icon ``name`` on ``card`` at ``at`` (its top left), ``size`` pixels square, in ``colour``."""
    from PIL import Image

    shape = mask(name, size)
    card.paste(Image.new("RGB", (size, size), colour), at, shape)
