"""Email body cleaning: keep only what the author of this message wrote (P2 D4).

Steps, all pure and line based:

1. Normalize line endings and whitespace.
2. Cut the quoted history at the first reply header: localized "On … wrote:" lines (en, de, fr,
   it, sv, nl; Gmail may wrap them over up to three lines), Outlook "Original Message"
   separators and Outlook "From:/Sent:" header blocks. Remaining ``>``-quoted lines (inline
   replies) go too.
3. Fold forwarded headers: the forwarded text stays, its From/To/Date/Subject lines go, so the
   forwarded content keeps its value without storing extra addresses.
4. Remove "external sender" banners, mobile footers, legal disclaimers (from the first
   disclaimer paragraph to the end), the signature after a ``-- `` delimiter and a trailing
   block of contact lines (a phone line, or two or more lines of phones, URLs, emails or
   ``|``-separated contact details).

Reply and Outlook headers must contain a time or a year (every mail client writes one), which
keeps sentences like "On the call you wrote:" in the body. Outlook forwards look exactly like
Outlook replies in the body alone, so their forwarded part is cut as reply history.

``clean_email`` is idempotent: cleaning a cleaned body returns it unchanged.
"""

import re
from dataclasses import dataclass

_WS = r"[ \t\u00a0]"
_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_TRAILING_SPACE = re.compile(r"[ \t\u00a0]+\n")
_DATEISH = re.compile(r"\d{1,2}[:.]\d{2}|\b\d{4}\b")

# "On Tue, 1 Sep 2026 at 10:00 Jane <jane@acme.com> wrote:" and its translations.
_REPLY_HEADER = re.compile(
    r"^(?:"
    r"on\b.{1,250}\bwrote:"
    r"|am\b.{1,250}\bschrieb\b.{0,250}:"
    r"|le\b.{1,250}\ba écrit ?:"
    r"|il\b.{1,250}\bha scritto ?:"
    r"|(?:den\b)?.{1,250}\bskrev\b.{0,250}:"
    r"|op\b.{1,250}\bschreef\b.{0,250}:"
    r")$",
    re.IGNORECASE,
)
_ORIGINAL_MESSAGE = re.compile(
    r"^-{2,}" + _WS + r"*(?:original message|ursprüngliche nachricht|message d'origine"
    r"|messaggio originale|ursprungligt meddelande|oorspronkelijk bericht)" + _WS + r"*-{2,}$",
    re.IGNORECASE,
)
_OUTLOOK_FROM = re.compile(r"^\*?(?:from|von|de|da|från|van)" + _WS + r"?:\*?" + _WS, re.IGNORECASE)
_OUTLOOK_SENT = re.compile(
    r"^\*?(?:sent|date|gesendet|datum|envoyé|inviato|data|skickat|verzonden)"
    + _WS
    + r"?:\*?"
    + _WS,
    re.IGNORECASE,
)
_FORWARD_MARKER = re.compile(
    r"^(?:-{2,}" + _WS + r"*(?:forwarded message|weitergeleitete nachricht|message transféré"
    r"|messaggio inoltrato|vidarebefordrat meddelande|doorgestuurd bericht)" + _WS + r"*-{2,}"
    r"|(?:begin forwarded message|anfang der weitergeleiteten nachricht"
    r"|début du message réexpédié|inizio messaggio inoltrato|vidarebefordrat brev"
    r"|begin doorgestuurd bericht) ?:)$",
    re.IGNORECASE,
)
_FORWARD_LINE = "---------- Forwarded message ----------"
_HEADER_FIELD = re.compile(
    r"^\*?(?:from|date|subject|to|cc|sent|reply-to"
    r"|von|datum|betreff|an|gesendet|de|objet|à|envoyé|da|data|oggetto|a|inviato"
    r"|från|ämne|till|skickat|van|onderwerp|aan|verzonden)" + _WS + r"?:\*?(?:" + _WS + r"|$)",
    re.IGNORECASE,
)
_SIGNATURE_DELIMITER = re.compile(r"^--" + _WS + r"?$")
_SEPARATOR = re.compile(r"^[_=\-*\u2014\u2013]{3,}$")
_DEVICE = (
    r"(?:(?:my|mein(?:em)?|mon|il mio|min|mijn)" + _WS + r"+)?"
    r"(?:iphone|ipad|android(?: phone)?|samsung\S*|galaxy\S*|smartphone|mobile(?: device)?"
    r"|handy|cellulare|mobil|mobiel|blackberry)"
)
_MOBILE_FOOTER = re.compile(
    r"^(?:sent from " + _DEVICE + r"|sent from (?:outlook|mail|yahoo mail) for \S+"
    r"|get outlook for \S+|von "
    + _DEVICE
    + r" gesendet|gesendet von "
    + _DEVICE
    + r"|envoyé (?:de|depuis) "
    + _DEVICE
    + r"|inviato da "
    + _DEVICE
    + r"|skickat från "
    + _DEVICE
    + r"|verzonden (?:vanaf|met) "
    + _DEVICE
    + r"|please consider the environment before printing.{0,60}"
    r"|bitte denken sie an die umwelt.{0,60})\.?$",
    re.IGNORECASE,
)
_BANNER = re.compile(
    r"^\[?(?:external|caution|warning|achtung|attention|attenzione|varning|let op)\b.{0,40}"
    r"(?:this (?:e-?mail|message) (?:originated|came|is) from outside|external (?:sender|e-?mail))",
    re.IGNORECASE,
)
_DISCLAIMER = re.compile(
    r"^\*?(?:"
    r"confidentiality notice|confidential(?:ity)?:|disclaimer:|important notice:"
    r"|this (?:e-?mail|message|communication)(?: and any (?:files|attachments)[^.]{0,80})?"
    r" (?:is|are|may be|contains?|and its contents are) .{0,40}"
    r"(?:confidential|privileged|intended (?:solely|only))"
    r"|the information (?:contained )?in this (?:e-?mail|message|communication)"
    r"|if you (?:are not|have received this).{0,60}(?:intended recipient|in error)"
    r"|diese (?:e-?mail|nachricht).{0,80}vertraulich|vertraulichkeitshinweis"
    r"|(?:wenn|falls) sie nicht der (?:richtige|beabsichtigte) (?:adressat|empfänger)"
    r"|ce (?:message|courriel|e-?mail).{0,80}(?:confidentiel|strictement)"
    r"|avis de confidentialité|si vous (?:avez reçu ce|n'êtes pas le destinataire)"
    r"|questo (?:messaggio|e-?mail).{0,80}(?:riservat|confidenzial)"
    r"|le informazioni contenute in questo|se (?:avete|hai) ricevuto questo"
    r"|detta (?:e-?post(?:meddelande)?|meddelande).{0,80}(?:konfidentiell|sekretess|avsett)"
    r"|om du (?:har fått detta|inte är (?:rätt|avsedd) mottagare)"
    r"|dit (?:e-?mailbericht|bericht|e-?mail).{0,80}(?:vertrouwelijk|uitsluitend bestemd)"
    r"|indien u (?:dit bericht|niet de)"
    r")",
    re.IGNORECASE,
)
_PHONE_LABEL = re.compile(
    r"^(?:t|m|p|f|o|tel|tél|phone|mobile|mob|cell|fax|office|direct|telefon|tfn|mobil|cellulare)"
    r"\.?" + _WS + r"*[:.]?" + _WS + r"*(?=[+(\d])",
    re.IGNORECASE,
)
_PHONE_NUMBER = re.compile(r"^\+?[\d ()./\-]{7,}$")
_URL_OR_EMAIL = re.compile(r"^(?:https?://\S+|www\.\S+|[^\s@]+@[^\s@]+\.[a-z]{2,})$", re.IGNORECASE)
_CONTACT_PART = re.compile(r"https?://|www\.|@[^\s@]+\.[a-z]{2,}|\+\d|\d[\d ()./\-]{8,}", re.I)


@dataclass(frozen=True)
class CleanedEmail:
    body: str
    # Text removed as the signature or trailing contact block, kept only for role titles (D8).
    signature: str | None


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _is_reply_header(lines: list[str], i: int) -> bool:
    """A reply header starting on line ``i``, possibly wrapped over up to three lines."""
    parts: list[str] = []
    for line in lines[i : i + 3]:
        if not line.strip():
            break
        parts.append(line.strip())
        candidate = " ".join(parts)
        if _DATEISH.search(candidate) and _REPLY_HEADER.match(candidate):
            return True
    return False


def _is_outlook_header(lines: list[str], i: int) -> bool:
    if not _OUTLOOK_FROM.match(lines[i].strip()):
        return False
    following = lines[i + 1 : i + 4]
    return any(_OUTLOOK_SENT.match(s := line.strip()) and _DATEISH.search(s) for line in following)


def _cut_history(lines: list[str]) -> list[str]:
    """Drops quoted history and folds forwarded headers."""
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _FORWARD_MARKER.match(line):
            kept.append(_FORWARD_LINE)
            i += 1
            while i < len(lines) and (
                not lines[i].strip() or _HEADER_FIELD.match(lines[i].strip())
            ):
                i += 1
            kept.append("")
            continue
        if (
            _ORIGINAL_MESSAGE.match(line)
            or _is_reply_header(lines, i)
            or _is_outlook_header(lines, i)
        ):
            break
        if not line.startswith(">"):
            kept.append(lines[i])
        i += 1
    return kept


def _paragraphs(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    return [b for b in blocks if b]


def _is_phone(text: str) -> bool:
    labelled = _PHONE_LABEL.match(text)
    number = text[labelled.end() :] if labelled else text
    if not _PHONE_NUMBER.match(number):
        return False
    digits = sum(c.isdigit() for c in number)
    # Unlabelled numbers need a "+" or 9+ digits, so a trailing date line is not a phone.
    return digits >= 7 if labelled or number.startswith("+") else digits >= 9


def _is_contact_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if _is_phone(text) or _URL_OR_EMAIL.match(text):
        return True
    parts = re.split(r"\s[|·•]\s", text)
    return len(parts) > 1 and any(_CONTACT_PART.search(p) for p in parts)


def _trailing_contacts(body: list[str]) -> int:
    """Number of trailing lines that form a contact block (0 when there is none)."""
    count = 0
    while count < len(body) and _is_contact_line(body[-1 - count]):
        count += 1
    block = body[len(body) - count :]
    if count >= 2 or any(_is_phone(line.strip()) for line in block):
        return count if count < len(body) else 0
    return 0


def _strip_boilerplate(lines: list[str]) -> tuple[list[str], list[str]]:
    """Removes banners, footers, disclaimers and the signature; returns (body, signature)."""
    lines = [line for line in lines if not _MOBILE_FOOTER.match(line.strip())]

    signature: list[str] = []
    for i, line in enumerate(lines):
        if _SIGNATURE_DELIMITER.match(line.strip()):
            signature = lines[i + 1 :]
            lines = lines[:i]
            break

    kept: list[list[str]] = []
    for block in _paragraphs(lines):
        text = " ".join(line.strip() for line in block)
        if _BANNER.match(text):
            continue
        if _DISCLAIMER.match(text):
            break
        kept.append(block)

    body = _drop_trailing_separators([line for block in kept for line in [*block, ""]])
    count = _trailing_contacts(body)
    trailing = body[len(body) - count :] if count else []
    body = _drop_trailing_separators(body[: len(body) - count])
    return body, [*trailing, *signature]


def _drop_trailing_separators(lines: list[str]) -> list[str]:
    while lines and (not lines[-1].strip() or _SEPARATOR.match(lines[-1].strip())):
        lines = lines[:-1]
    return lines


def clean_email(text: str) -> CleanedEmail:
    lines = normalize_whitespace(text).split("\n")
    body, signature = _strip_boilerplate(_cut_history(lines))
    cleaned = normalize_whitespace("\n".join(body))
    removed = normalize_whitespace("\n".join(signature))
    return CleanedEmail(body=cleaned, signature=removed or None)
