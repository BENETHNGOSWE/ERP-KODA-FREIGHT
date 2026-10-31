"""SMS text encoding + segmentation helpers.

The legacy Laravel system counted one row in `messages` per recipient regardless of the
length of the text, which under-billed long messages. Kodafreight computes the real number
of SMS parts following the GSM 03.38 / UCS-2 specification.
"""

GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENDED = "^{}\\[~]|€"

GSM7_SINGLE = 160
GSM7_MULTI = 153
UCS2_SINGLE = 70
UCS2_MULTI = 67


def is_gsm7(text: str) -> bool:
    """True when every character can be encoded with the GSM 7-bit alphabet."""
    for ch in text or "":
        if ch not in GSM7_BASIC and ch not in GSM7_EXTENDED:
            return False
    return True


def gsm7_length(text: str) -> int:
    """Septet count: characters from the extension table occupy two septets."""
    total = 0
    for ch in text or "":
        total += 2 if ch in GSM7_EXTENDED else 1
    return total


def analyse(text: str) -> dict:
    """Return encoding, billable length and number of SMS parts for `text`."""
    text = text or ""
    if is_gsm7(text):
        encoding = "GSM7"
        length = gsm7_length(text)
        single, multi = GSM7_SINGLE, GSM7_MULTI
    else:
        encoding = "UCS2"
        # Characters outside the BMP need a surrogate pair (2 UCS-2 units).
        length = sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)
        single, multi = UCS2_SINGLE, UCS2_MULTI

    if length == 0:
        parts = 0
    elif length <= single:
        parts = 1
    else:
        parts = -(-length // multi)  # ceil

    return {
        "encoding": encoding,
        "length": length,
        "parts": parts,
        "characters": len(text),
        "per_part": single if parts <= 1 else multi,
    }


def count_parts(text: str) -> int:
    return analyse(text)["parts"]
