#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fictional demo corpus reproducing every mail shape seen in the real
WorkBuddy mailbox experiment. All names/companies/domains are invented
(example.com-style); safe to commit and distribute.

build_extended(dest) writes the multipart .eml files that plain RFC822
fixtures cannot express: empty text/plain + full HTML bodies, inline cid
logos repeated across mails (signature-logo case), point-by-point quoted
replies, signature/disclaimer blocks, and real attachment payloads.
"""
from __future__ import annotations

from pathlib import Path

from mailbuilder import build_mail, minimal_pdf

LOGO_CID = "cr-logo@casarossi.it"
ALEX = ("Alex", "alex@sunrisetrade.example")


def logo_html(body_text: str) -> str:
    return (f"<p>{body_text}</p>"
            f'<p><img src="cid:{LOGO_CID}" alt="logo"></p>'
            "<p style='font-size:9px;color:#888'>Casa Rossi Mobili S.r.l. - Milano | www.casarossi.example</p>")


def build_extended(dest: Path, assets: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    logo = (assets / "logo-red.png").read_bytes()
    product = (assets / "product-blue.png").read_bytes()
    names = []

    def emit(name: str, data: bytes) -> None:
        (dest / name).write_bytes(data)
        names.append(name)

    # 1) marco: inline signature logo + real PDF attachment (mail 1 of 3)
    emit("ext-01-marco-logo.eml", build_mail(
        mid="<marco-ext-1@casarossi.it>",
        frm=("Marco Rossi", "marco.rossi@casarossi.it"), to=[ALEX],
        date="Mon, 21 Sep 2026 09:00:00 +0200",
        subject="Our price list for reference",
        text="Hi Alex, our current price list is attached for your reference.\n\nMarco",
        html=logo_html("Hi Alex, our current price list is attached for your reference."),
        cid_images={LOGO_CID: ("logo.png", logo)},
        attachments=[("price-list-2026.pdf", minimal_pdf("Price list 2026 - fictional"), "application/pdf")]))

    # 2) marco: html-only body with interleaved point-by-point replies + quote + signature
    reply_chain_text = "\n".join([
        "Dear Alex,", "",
        "> 1. Can you do USD 230/pc if we order 300 pcs?",
        "Yes — for 300 pcs we can accept USD 230/pc.",
        "> 2. Lead time of 45 days is too long for us.",
        "Lead time can be 40 days after deposit.",
        "> 3. Please include the catalog PDF.",
        "The catalog is attached to my previous mail; sending again here.", "",
        ">",
        "> -----Original Message-----",
        "> From: Alex [mailto:alex@sunrisetrade.example]",
        "> Sent: Wednesday, September 23, 2026 10:30 AM",
        "> To: Marco Rossi",
        "> Subject: Re: Inquiry: walnut dining tables",
        ">",
        "> Dear Marco, our offer is USD 245.00/pc FOB Qingdao, MOQ 100,",
        "> lead time 45 days, T/T 30/70, validity 30 days.",
        "",
        "Best regards,",
        "Marco Rossi",
        "Casa Rossi Mobili S.r.l.",
        "Via dei Mobili 1, Milano",
    ])
    emit("ext-02-marco-reply-chain.eml", build_mail(
        mid="<marco-ext-2@casarossi.it>",
        frm=("Marco Rossi", "marco.rossi@casarossi.it"), to=[ALEX],
        date="Sat, 26 Sep 2026 16:20:00 +0200",
        subject="Re: Inquiry: walnut dining tables (approx. 200 pcs)",
        text=reply_chain_text))

    # 3) marco: another logo mail (2/3) with catalog attached
    emit("ext-03-marco-catalog.eml", build_mail(
        mid="<marco-ext-3@casarossi.it>",
        frm=("Marco Rossi", "marco.rossi@casarossi.it"), to=[ALEX],
        date="Mon, 28 Sep 2026 11:05:00 +0200",
        subject="Catalog, as promised",
        text="Sending the catalog again as promised in my last mail.",
        html=logo_html("Sending the catalog again as promised in my last mail."),
        cid_images={LOGO_CID: ("logo.png", logo)},
        attachments=[("catalog-2026.pdf", minimal_pdf("Product catalog 2026 - fictional"), "application/pdf")]))

    # 4) sarah: EMPTY text/plain + full HTML (the 58-sent-mail finding, inbox side)
    emit("ext-04-sarah-html.eml", build_mail(
        mid="<sarah-ext-1@northpeakoutfitters.com>",
        frm=("Sarah Thompson", "sarah.thompson@northpeakoutfitters.com"), to=[ALEX],
        date="Mon, 28 Sep 2026 22:10:00 -0500",
        subject="Re-order specs attached",
        text="",
        html="<p>Hi Alex,</p><p>Spec sheet for the <b>NP-104</b> re-order is attached."
             "<br>Quantities as discussed: <b>500 pcs</b>.</p>"
             "<p>Best,<br>Sarah</p>",
        attachments=[("NP104-spec-v2.pdf", minimal_pdf("NP-104 spec sheet v2 - fictional"), "application/pdf")]))

    # 5) alex SENT mail with empty text/plain + full HTML (the experiment finding)
    emit("ext-05-sent-html.eml", build_mail(
        mid="<alex-ext-sent-1@sunrisetrade.example>",
        frm=ALEX, to=[("Sarah Thompson", "sarah.thompson@northpeakoutfitters.com")],
        date="Tue, 29 Sep 2026 09:40:00 +0800",
        subject="Re: Re-order specs attached",
        text="",
        html="<p>Dear Sarah,</p><p>Thanks — we received the spec sheet. Quotation for "
             "<b>500 pcs NP-104</b> follows separately.</p><p>Best regards,<br>Alex</p>"))

    # 6) marco: third logo mail (3/3) -> same inline hash in 3 mails = suspected logo
    emit("ext-06-marco-logo-3rd.eml", build_mail(
        mid="<marco-ext-4@casarossi.it>",
        frm=("Marco Rossi", "marco.rossi@casarossi.it"), to=[ALEX],
        date="Wed, 30 Sep 2026 10:00:00 +0200",
        subject="Showroom photo",
        text="A photo from our Milano showroom for your reference.",
        html=logo_html("A photo from our Milano showroom for your reference."),
        cid_images={LOGO_CID: ("logo.png", logo)},
        attachments=[("showroom.png", product, "image/png")]))

    # 7) hans: complaint with real photo attachment (genuine image, not a logo)
    emit("ext-07-hans-photo.eml", build_mail(
        mid="<hans-ext-1@bayerwerk-gmbh.de>",
        frm=("Hans Müller", "h.mueller@bayerwerk-gmbh.de"), to=[ALEX],
        date="Tue, 6 Oct 2026 12:00:00 +0200",
        subject="BW-2409: defect photo",
        text="Photo of the leaking seal corner, as promised.",
        attachments=[("defect-photo.png", product, "image/png")]))

    return names


if __name__ == "__main__":
    import sys
    dest = Path(sys.argv[1])
    assets = Path(__file__).resolve().parent / "fixtures" / "assets"
    for n in build_extended(dest, assets):
        print(n)
