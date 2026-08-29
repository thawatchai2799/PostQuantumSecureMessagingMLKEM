"""
alphabet54.py — Somsuk's Table 1 alphabet (54 characters).

Single source of truth for the S1 (6-bit static) alphabet, shared by
corpus generators (C3 uniform) and, later, the S1 encoder in the harness.

Composition (total 54):
  - Uppercase English letters A-Z ........ 26
  - Digits 0-9 ........................... 10
  - Symbols (18): space . ? ! , - / : = ( ) @ # $ % & + *

Codes follow the paper's ordering (A=000000 ... 9=101001). We only need
the character SET and a stable ordering here; exact bit codes are the
harness's concern.
"""

LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]          # 26
SYMBOLS = [" ", ".", "?", "!", ",", "-", "/", ":", "=", "(", ")",
           "@", "#", "$", "%", "&", "+", "*"]                       # 18
DIGITS = [str(d) for d in range(10)]                                # 10

# Paper's Table 1 interleaves letters, then symbols, then digits.
ALPHABET54 = LETTERS + SYMBOLS + DIGITS

assert len(ALPHABET54) == 54, f"alphabet size {len(ALPHABET54)} != 54"
assert len(set(ALPHABET54)) == 54, "duplicate character in alphabet"

ALPHABET_SET = set(ALPHABET54)


def coverage(text: str) -> float:
    """Fraction of characters in `text` that are inside the 54-char set."""
    if not text:
        return 0.0
    return sum(1 for ch in text if ch in ALPHABET_SET) / len(text)


if __name__ == "__main__":
    print("Alphabet (54):", "".join(ALPHABET54))
