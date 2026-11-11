"""Build Rip for Ask Rip (web/templates/web/includes/rip_bot.html) from the owner's rip_bot pack in
web/static/img/rip/: the head and jaw once, the five eyes and the marks above, each colour a class site.css fills
from the tokens. Run it again after the pack changes:

    python scripts/build_rip.py
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://www.w3.org/2000/svg}"
ROOT = Path(__file__).resolve().parent.parent
src = sys.argv[1] if len(sys.argv) > 1 else ROOT / "web/static/img/rip"
out = sys.argv[2] if len(sys.argv) > 2 else ROOT / "web/templates/web/includes/rip_bot.html"
MOODS = ["normal", "thinking", "happy", "puzzled", "sleepy"]
COLOURS = {"#FF5A1F": "brand", "#FFFFFF": "paper"}


def load(mood):
    return ET.parse(f"{src}/avatars/avatar_{mood}.svg").getroot()


def node(el, depth=0):
    """One element as markup, its colours as classes (site.css gives them the tokens)."""
    tag = el.tag.replace(NS, "")
    attrs = dict(el.attrib)
    classes = []
    fill, stroke = attrs.pop("fill", None), attrs.pop("stroke", None)
    if stroke:
        classes.append(f"rip__line-{COLOURS[stroke.upper()]}")
    elif fill and fill != "none":
        classes.append(f"rip__fill-{COLOURS[fill.upper()]}")
    for key in ("id", "transform"):
        attrs.pop(key, None)
    parts = [tag] + ([f'class="{" ".join(classes)}"'] if classes else []) + [f'{k}="{v}"' for k, v in attrs.items()]
    inner = "".join(node(child) for child in el)
    return f"<{' '.join(parts)}>{inner}</{tag}>" if inner else f"<{' '.join(parts)}/>"


normal = load("normal")
clip = normal.find(f"{NS}defs/{NS}clipPath/{NS}path").get("d")
pose = normal.find(f"{NS}g")
head = pose.find(f".//{NS}g[@id='head']/{NS}path")
jaw = pose.find(f".//{NS}g[@id='jaw']/{NS}path")
eyes, marks = [], []
for mood in MOODS:
    root = load(mood)
    eye = root.find(f".//{NS}g[@id='eye']")
    eyes.append(f'<g class="rip__eye rip__eye--{mood}">' + "".join(node(c) for c in eye) + "</g>")
    extra = [c for c in root if c.tag not in (f"{NS}defs", f"{NS}g", f"{NS}title")]
    if extra:
        marks.append(f'<g class="rip__marks rip__marks--{mood}">' + "".join(node(c) for c in extra) + "</g>")

comment = ("{% comment %}Rip, the Ask Rip character: the site's own raptor mark from the owner's rip_bot pack "
           "(web/static/img/rip/), the head, the lower jaw and the five eyes in one drawing so the face changes "
           "without loading a picture. The box's data-mood picks the eye, the tilt, the jaw and the marks above "
           "(site.css, from the pack's own angles); the jaw turns on the pack's hinge, 472 418. Built by "
           "scripts/build_rip.py from the pack's avatar SVGs. Decoration only: the bubble's words say it all."
           "{% endcomment %}")
svg = (f'<svg class="rip" viewBox="{normal.get("viewBox")}" aria-hidden="true" focusable="false">'
       f'<defs><clipPath id="rip-neck"><path d="{clip}"/></clipPath></defs>'
       '<g class="rip__body"><g class="rip__pose">'
       '<g clip-path="url(#rip-neck)">'
       f'<path class="rip__fill-brand" fill-rule="evenodd" d="{head.get("d")}"/>'
       f'<g class="rip__jaw"><path class="rip__fill-brand" d="{jaw.get("d")}"/></g>'
       "</g>" + "".join(eyes) + "</g></g>" + "".join(marks) + "</svg>\n")
open(out, "w").write(comment + svg)
print(len(svg), "bytes;", len(eyes), "eyes;", len(marks), "mark groups")
