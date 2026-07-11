import unicodedata


def normalize_match_separators(value: str) -> str:
    """Normalize text for case-insensitive, dash-insensitive matching."""
    normalized = unicodedata.normalize("NFC", value).casefold()
    separated = "".join(
        " " if character == "-" or character.isspace() or unicodedata.category(character) == "Pd"
        else character
        for character in normalized
    )
    return " ".join(separated.split())
