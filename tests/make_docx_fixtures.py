#!/usr/bin/env python3
"""Build the .docx fixtures that tests/docx_edits.py measures against.

Neither pandoc nor python-docx will emit tracked changes for you - python-docx
has no revision-mark API at all - so the only way to get a file carrying real
<w:ins>/<w:del> runs and word/comments.xml entries from two authors is to write
that XML into the zip by hand. That is what this does.

The generated files are committed and are the test suite's whole basis; this
script exists so they stay reproducible and so a new case is added by editing
XML rather than by hand-patching a zip.

Every case here is deliberate. In particular:

  * a deletion nested inside an insertion (J Vale inserted it, G Jensen cut
    part of it) - it must vanish from both the accepted and the rejected view,
    which a naive "accept drops deletions, reject drops insertions" rule gets
    wrong in one direction;
  * a paragraph-mark insertion at <w:pPr><w:rPr><w:ins/> - a `//w:ins` search
    finds it and reports a phantom textless insertion;
  * a <w:rPrChange> formatting change, which is a revision but not a text one;
  * a move pair (<w:moveFrom>/<w:moveTo>), which Word emits for dragged text
    and which is invisible to a parser that only knows ins and del;
  * "et al." mid-sentence, which a naive sentence splitter breaks on.

Run:  python tests/make_docx_fixtures.py [--out tests/fixtures]
"""

import argparse
import os
import sys
import zipfile

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
W14 = 'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'
MC = ('xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'mc:Ignorable="w14 w15"')

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

CONTENT_TYPES = XML_DECL + """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
{extra}</Types>"""

CT_COMMENTS = """  <Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
  <Override PartName="/word/commentsExtended.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"/>
"""

ROOT_RELS = XML_DECL + """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = XML_DECL + """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{rels}</Relationships>"""

COMMENT_RELS = """  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
  <Relationship Id="rId2" Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended" Target="commentsExtended.xml"/>
"""

SECT_PR = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
           '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
           '</w:sectPr>')


def document(body):
    return (XML_DECL + '<w:document ' + W + ' ' + W14 + ' ' + W15 + ' ' + MC
            + '>\n<w:body>\n' + body + '\n' + SECT_PR + '\n</w:body>\n</w:document>')


def write_docx(path, body, comments=None, comments_ex=None):
    """Assemble a minimal but real .docx package."""
    has_comments = comments is not None
    parts = {
        "[Content_Types].xml": CONTENT_TYPES.format(
            extra=CT_COMMENTS if has_comments else ""),
        "_rels/.rels": ROOT_RELS,
        "word/_rels/document.xml.rels": DOC_RELS.format(
            rels=COMMENT_RELS if has_comments else ""),
        "word/document.xml": document(body),
    }
    if has_comments:
        parts["word/comments.xml"] = comments
        parts["word/commentsExtended.xml"] = comments_ex
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # A fixed timestamp so rebuilding an unchanged fixture gives a byte-stable
    # file - otherwise every run looks like a change to OneDrive and to git.
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 8, 15, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"))
    return path


# ---------------------------------------------------------------------------
# Fixture 1: tracked changes and comments from two authors
# ---------------------------------------------------------------------------

JV = 'w:author="J Vale"'
GJ = 'w:author="G Jensen"'

TRACKED_BODY = """
<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>
  <w:r><w:t xml:space="preserve">Symmetry breaking in bacterial chemoreceptor arrays</w:t></w:r>
</w:p>

<w:p>
  <w:r><w:t xml:space="preserve">Bacterial chemoreceptor arrays form a hexagonal lattice of trimer-of-dimer units (Briegel et al. 2009). </w:t></w:r>
  <w:ins w:id="101" @JV@ w:date="2026-08-14T09:12:00Z">
    <w:r><w:t xml:space="preserve">Cryo-electron tomography has resolved this lattice in situ. </w:t></w:r>
  </w:ins>
  <w:r><w:t xml:space="preserve">The array </w:t></w:r>
  <w:del w:id="102" @JV@ w:date="2026-08-14T09:13:00Z">
    <w:r><w:delText xml:space="preserve">may indicate</w:delText></w:r>
  </w:del>
  <w:ins w:id="103" @JV@ w:date="2026-08-14T09:13:00Z">
    <w:r><w:t xml:space="preserve">indicates</w:t></w:r>
  </w:ins>
  <w:r><w:t xml:space="preserve"> a cooperative signalling mechanism.</w:t></w:r>
</w:p>

<w:p>
  <w:r><w:t xml:space="preserve">Lattice order was quantified across the temperature series. </w:t></w:r>
  <w:commentRangeStart w:id="0"/>
  <w:del w:id="104" @GJ@ w:date="2026-08-15T16:40:00Z">
    <w:r><w:delText xml:space="preserve">We observed a trend toward significance (p = 0.07). </w:delText></w:r>
  </w:del>
  <w:commentRangeEnd w:id="0"/>
  <w:r><w:commentReference w:id="0"/></w:r>
  <w:r><w:t xml:space="preserve">Order parameters declined above 310 K</w:t></w:r>
  <w:ins w:id="105" @GJ@ w:date="2026-08-15T16:41:00Z">
    <w:r><w:t xml:space="preserve"> (Figure 2B)</w:t></w:r>
  </w:ins>
  <w:r><w:t xml:space="preserve">.</w:t></w:r>
</w:p>

<w:p>
  <w:commentRangeStart w:id="1"/>
  <w:r><w:t xml:space="preserve">The signalling mechanism remains </w:t></w:r>
  <w:ins w:id="106" @JV@ w:date="2026-08-14T09:20:00Z">
    <w:del w:id="107" @GJ@ w:date="2026-08-15T16:45:00Z">
      <w:r><w:delText xml:space="preserve">entirely </w:delText></w:r>
    </w:del>
    <w:r><w:t xml:space="preserve">poorly </w:t></w:r>
  </w:ins>
  <w:r><w:t xml:space="preserve">understood.</w:t></w:r>
  <w:commentRangeEnd w:id="1"/>
  <w:r><w:commentReference w:id="1"/></w:r>
</w:p>

<w:p>
  <w:pPr><w:rPr><w:ins w:id="108" @JV@ w:date="2026-08-14T09:22:00Z"/></w:rPr></w:pPr>
  <w:r>
    <w:rPr><w:i/>
      <w:rPrChange w:id="109" @GJ@ w:date="2026-08-15T16:50:00Z"><w:rPr/></w:rPrChange>
    </w:rPr>
    <w:t xml:space="preserve">Arrays were imaged by cryo-electron tomography.</w:t>
  </w:r>
</w:p>

<w:p>
  <w:moveFrom w:id="110" @JV@ w:date="2026-08-14T09:30:00Z">
    <w:r><w:delText xml:space="preserve">Tilt series were collected from -60 to +60 degrees. </w:delText></w:r>
  </w:moveFrom>
  <w:r><w:t xml:space="preserve">Data were processed in IMOD.</w:t></w:r>
</w:p>

<w:p>
  <w:moveTo w:id="111" @JV@ w:date="2026-08-14T09:30:00Z">
    <w:r><w:t xml:space="preserve">Tilt series were collected from -60 to +60 degrees. </w:t></w:r>
  </w:moveTo>
  <w:r><w:t xml:space="preserve">Alignment used gold fiducials.</w:t></w:r>
</w:p>
""".replace("@JV@", JV).replace("@GJ@", GJ)

TRACKED_COMMENTS = XML_DECL + (
    '<w:comments ' + W + ' ' + W14 + ' ' + W15 + ' ' + MC + '>\n'
    '  <w:comment w:id="0" ' + GJ + ' w:initials="GJ" w:date="2026-08-15T16:42:00Z">\n'
    '    <w:p w14:paraId="0A0A0A01"><w:r><w:t xml:space="preserve">A p of 0.07 is not a trend. Cut this or report it as null.</w:t></w:r></w:p>\n'
    '  </w:comment>\n'
    '  <w:comment w:id="1" ' + JV + ' w:initials="JV" w:date="2026-08-14T09:21:00Z">\n'
    '    <w:p w14:paraId="0A0A0A02"><w:r><w:t xml:space="preserve">Does the discussion motivate this? It reads as a non sequitur after the results.</w:t></w:r></w:p>\n'
    '  </w:comment>\n'
    '</w:comments>')

TRACKED_COMMENTS_EX = XML_DECL + (
    '<w15:commentsEx ' + W + ' ' + W14 + ' ' + W15 + ' ' + MC + '>\n'
    '  <w15:commentEx w15:paraId="0A0A0A01" w15:done="0"/>\n'
    '  <w15:commentEx w15:paraId="0A0A0A02" w15:done="1"/>\n'
    '</w15:commentsEx>')


# ---------------------------------------------------------------------------
# Fixture 2: reference-manager field codes
# ---------------------------------------------------------------------------

FIELDS_BODY = """
<w:p>
  <w:r><w:t xml:space="preserve">Chemoreceptor arrays are hexagonal </w:t></w:r>
  <w:r><w:fldChar w:fldCharType="begin"/></w:r>
  <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE &lt;EndNote&gt;&lt;Cite&gt;&lt;Author&gt;Briegel&lt;/Author&gt;&lt;Year&gt;2009&lt;/Year&gt;&lt;RecNum&gt;12&lt;/RecNum&gt;&lt;/Cite&gt;&lt;/EndNote&gt;</w:instrText></w:r>
  <w:r><w:fldChar w:fldCharType="separate"/></w:r>
  <w:r><w:t xml:space="preserve">(1)</w:t></w:r>
  <w:r><w:fldChar w:fldCharType="end"/></w:r>
  <w:r><w:t xml:space="preserve">.</w:t></w:r>
</w:p>

<w:p>
  <w:r><w:t xml:space="preserve">A second array type was described later </w:t></w:r>
  <w:fldSimple w:instr=" ADDIN ZOTERO_ITEM CSL_CITATION {&quot;citationID&quot;:&quot;a1b2&quot;} ">
    <w:r><w:t xml:space="preserve">(2)</w:t></w:r>
  </w:fldSimple>
  <w:r><w:t xml:space="preserve">.</w:t></w:r>
</w:p>

<w:p>
  <w:ins w:id="201" @JV@ w:date="2026-08-16T11:00:00Z">
    <w:r><w:t xml:space="preserve">Both types share the trimer-of-dimer building block.</w:t></w:r>
  </w:ins>
</w:p>
""".replace("@JV@", JV)


# ---------------------------------------------------------------------------
# Fixture 3: a clean document - no revisions, and no comments part at all
# ---------------------------------------------------------------------------

CLEAN_BODY = """
<w:p><w:r><w:t xml:space="preserve">Symmetry breaking in bacterial chemoreceptor arrays</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Arrays were imaged by cryo-electron tomography and processed in IMOD.</w:t></w:r></w:p>
"""


FIXTURES = {
    "tracked_two_authors.docx": (TRACKED_BODY, TRACKED_COMMENTS, TRACKED_COMMENTS_EX),
    "field_codes.docx": (FIELDS_BODY, None, None),
    "clean.docx": (CLEAN_BODY, None, None),
}


def build(out_dir):
    return [write_docx(os.path.join(out_dir, name), body, comments, ex)
            for name, (body, comments, ex) in FIXTURES.items()]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=os.path.join(here, "fixtures"))
    args = p.parse_args()
    for path in build(args.out):
        print("wrote {}  ({} bytes)".format(path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
