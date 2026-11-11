# Rip for the RipRaptor chat bot

All artwork is the site's own raptor mark, traced to vectors. Orange is #FF5A1F, the eye is white.

## Layered master: `rip_layered.svg`
Three named layers on the logo's own 1251 x 1200 canvas. They open as layers in Figma, Illustrator and Inkscape.

| Layer id | What it is |
|---|---|
| `#head` | Head, neck and the three teeth, with no eye |
| `#jaw` | Lower jaw on its own |
| `#eye` | The eye (two white crescents; the slit pupil is the orange gap between them) |

The same three layers are also in `layers/` as separate SVGs and transparent PNGs on the same canvas, so they stack exactly.

### Animating on the site (inline the SVG)
The jaw hinge is at 472, 418. About -6 degrees closes the mouth and +7 opens it wide.

```css
/* talking or "typing" */
#jaw { transform-box: view-box; transform-origin: 472px 418px; animation: rip-chat .28s ease-in-out infinite alternate; }
@keyframes rip-chat { from { transform: rotate(-6deg); } to { transform: rotate(5deg); } }
/* blink every few seconds */
#eye { transform-box: fill-box; transform-origin: center; animation: rip-blink 4s infinite; }
@keyframes rip-blink { 0%, 95%, 100% { transform: scaleY(1); } 97.5% { transform: scaleY(.1); } }
```

## Five expressions
Each one comes in two crops:

- `expressions/rip_*`: the full logo mark, including the neck.
- `avatars/rip_*`: head only, cut under the jaw like the videos. These are best for small chat avatars.

Each crop comes as SVG, a 1024 px transparent PNG and a 256 px transparent PNG.

| File | Look |
|---|---|
| `normal` | The logo as it is |
| `thinking` | Eye looking up, raised brow, mouth shut, thought dots |
| `happy` | Closed smiling eye, mouth open wider |
| `puzzled` | Round eye, slanted brow, head tilted, question mark |
| `sleepy` | Half-closed eye, mouth shut, head drooping, Zs |

`preview.png` shows all five in both crops.
