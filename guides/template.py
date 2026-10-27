"""
The guide template marketing downloads from Admin, Guides, Import a guide: a whole example guide, written the
way the importer reads it (guides/importer.py), as a Word file and as plain text. Replacing its words and
keeping its headings and labels is all a new guide needs. The example's facts are from marketing's fact base
(27 Oct 2026) and its sources were checked to open that day.
"""

import io
import zipfile
from xml.sax.saxutils import escape

TEXT = """# A parent's guide to buying trading cards
Web address: parents
One-line answer: What to buy, what it should cost and how to pay safely, with today's prices from every UK shop we track.
Title in Google: Pokémon cards for Christmas: a parent's guide
For: Parents
Linked from: Product pages
Link words: New to this? Read the parent's guide.
Last checked: 27 Oct 2026
Next review: 20 Jan 2027
Owner: Marketing
Checked by: Marketing

## What should I buy for my child?
Short answer: For a first present, an Elite Trainer Box in English suits most children, because it has packs to open plus card sleeves and dice to play with. Check today's price below before you buy.

A booster bundle is a cheaper choice with packs only, so it suits a smaller budget or a child who already has sleeves and dice.

- Buy English unless they asked for Japanese, because English cards can be read and swapped with friends.
- Pick a set that is out now, because a pre-order may not arrive in time for the day.

Prices:
https://ripraptor.com/products/prismatic-evolutions-elite-trainer-box/
What is in the box: Elite Trainer Box
Sources: none, this is our advice

## Is this price fair?
Short answer: Type the price you have been offered, with delivery, into the checker below. It compares it with today's prices at every UK shop we track, so you know before you pay.
Price checker: yes
Sources: none, the checker uses our own prices

## How do I pay safely?
Short answer: Pay by credit card, debit card or PayPal Goods and Services, because they give you a way to claim your money back if the order never arrives.

Buying online from a shop, you normally have 14 days to change your mind, starting the day after delivery. A private seller does not have to give you that, which is why the shop you buy from matters.

This is general guidance, not legal advice.

Good signs:
- A UK address and phone number, so you know who to contact if something goes wrong.
- Card or PayPal at checkout, so you can claim your money back.
Warning signs:
- Bank transfer or gift cards only, because card and PayPal protection is quicker and more certain.
- A price far below every other shop, because a real shop cannot usually sell that cheaply.
Sources:
- Citizens Advice: if you've changed your mind about something you've bought, https://www.citizensadvice.org.uk/consumer/changed-your-mind/, 27 Oct 2026
- Citizens Advice: check if you can get your money back after a scam, https://www.citizensadvice.org.uk/consumer/scams/check-if-you-can-get-your-money-back-after-a-scam/, 27 Oct 2026

## Checklist
Title: Before you buy: a parent's checklist
- It is in English, unless they asked for Japanese
- The price is fair on the Fair Price Checker
- The shop has a UK address and takes card payments
- You are paying by card or PayPal Goods and Services
- You have kept the receipt and the order email
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>"""
PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {NS}>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>
<w:pPr><w:ind w:left="720"/></w:pPr></w:style>
</w:styles>"""
NUMBERING = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering {NS}>
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>
<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""


def _run(text, bold=False):
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(line):
    from .importer import GUIDE_LABELS, SECTION_LABELS

    if line.startswith("## "):
        return f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>{_run(line[3:])}</w:p>'
    if line.startswith("# "):
        return f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>{_run(line[2:])}</w:p>'
    if line.startswith("- "):
        return (f'<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/>'
                f'</w:numPr></w:pPr>{_run(line[2:])}</w:p>')
    head, colon, rest = line.partition(":")
    if colon and head.strip().lower() in (set(GUIDE_LABELS) | set(SECTION_LABELS)):
        return f"<w:p>{_run(head + ':', bold=True)}{_run(rest)}</w:p>"
    return f"<w:p>{_run(line)}</w:p>"


def docx():
    """The template as a Word file (bytes)."""
    body = "".join(_paragraph(line) for line in TEXT.splitlines() if line.strip())
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{body}'
                f'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" '
                f'w:left="1134" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr></w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("_rels/.rels", PACKAGE_RELS)
        package.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        package.writestr("word/document.xml", document)
        package.writestr("word/styles.xml", STYLES)
        package.writestr("word/numbering.xml", NUMBERING)
    return out.getvalue()
