#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test helper: build realistic multipart .eml bytes deterministically.

Used to reproduce the real-mailbox findings from the WorkBuddy experiment:
empty text/plain + full HTML, inline cid images, real attachments, reply
chains with point-by-point quoted answers, signatures and disclaimers.
"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr


def minimal_pdf(text: str = "Sunrise Trade - fictional demo document") -> bytes:
    """A genuinely openable one-page PDF 1.4 (no external deps): correct
    objects, xref table offsets and %%EOF. Sample attachments must open in a
    real viewer, not just carry a %PDF- prefix."""
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    stream = f"BT /F1 14 Tf 36 72 Td ({esc(text)}) Tj ET\n".encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 140] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(header)
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_pos))
    return bytes(out)


def build_mail(
    *,
    mid: str,
    frm: tuple[str, str],
    to: list[tuple[str, str]],
    date: str,
    subject: str,
    text: str | None = None,
    html: str | None = None,
    cid_images: dict[str, tuple[str, bytes]] | None = None,   # cid -> (filename, bytes)
    attachments: list[tuple[str, bytes, str]] | None = None,  # (filename, bytes, mimetype)
) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["From"] = formataddr(frm)
    msg["To"] = ", ".join(formataddr(a) for a in to)
    msg["Date"] = date
    msg["Subject"] = subject

    text = text or ""
    if cid_images:
        msg.set_content(text)
        msg.add_alternative(f"<html><body>{html or text}</body></html>", subtype="html")
        # attach inline images to the html alternative part (cid-referenced)
        html_part = msg.get_payload()[1]
        for cid, (fname, blob) in cid_images.items():
            html_part.add_attachment(blob, maintype="image", subtype="png",
                                     filename=fname, cid=f"<{cid}>")
        for part in html_part.iter_attachments():
            if part.get("Content-ID"):
                part.replace_header(
                    "Content-Disposition", f'inline; filename="{part.get_filename()}"')
    elif html is not None:
        msg.set_content(text)
        msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    else:
        msg.set_content(text)

    for fname, blob, ctype in (attachments or []):
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(blob, maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=fname)
    return msg.as_bytes()
