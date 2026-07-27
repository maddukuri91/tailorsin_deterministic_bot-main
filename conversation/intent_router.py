import re

from conversation.menu import get_menu_options


VISIT_HISTORY_INPUTS = {
    "visit history",
    "appointment history",
    "visit status",
    "appointment status",
    "my appointments",
}

# Regex to strip common emoji characters so button taps with emojis still match.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # misc
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Remove emoji characters from *text* so plain-label matching still works.

    Whitespace is also normalised (emoji removal can leave double spaces, e.g.
    "🔍 Track my order" -> " Track my order") so that button-tap text matches
    single-space menu candidates.
    """
    cleaned = _EMOJI_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def get_intent(client_type, option):

    menu = get_menu_options(client_type)

    normalized_option = option.strip()
    if normalized_option.casefold() in VISIT_HISTORY_INPUTS:
        return "visit_history"

    # Support menu_ prefixed callback data (e.g. "menu_order_status")
    if normalized_option.startswith("menu_"):
        intent_key = normalized_option[len("menu_"):]
        # Verify it's a known intent by checking if any menu item has it
        for item in menu:
            if item["intent"] == intent_key:
                return intent_key
        return None

    # Support numeric menu selection (1-based index), optionally prefixed like "1. text"
    plain_option = _strip_emoji(normalized_option)
    if "." in plain_option:
        parts = plain_option.split(".", 1)
        if parts[0].strip().isdigit():
            plain_option = parts[1].strip()

    if plain_option.isdigit():
        index = int(plain_option)
        if index == 0:
            return "main_menu"
        if 1 <= index <= len(menu):
            return menu[index - 1]["intent"]

    # Also try matching after stripping emojis (for button taps).
    # plain_option already has numeric prefix stripped if present.
    # Strip whitespace to handle cases where emoji removal left leading/trailing spaces
    # (e.g. "🔍 Track my order" -> emoji stripped -> " Track my order").
    match_option = plain_option.strip()

    for item in menu:
        label = item["label"]
        candidates = {
            label.casefold(),
            item["intent"].casefold(),
        }
        if normalized_option.casefold() in candidates or match_option.casefold() in candidates:
            return item["intent"]
        # If the button text matches the label (possibly with emoji)
        if match_option.casefold() == label.casefold():
            return item["intent"]
        # Also try matching strip-emoji version against label
        if match_option.casefold() == _strip_emoji(label).casefold():
            return item["intent"]
        # Try matching inner text without emoji AND without any prefix numbering
        if "." in match_option:
            inner = match_option.split(".", 1)[1].strip()
            if inner.casefold() in candidates:
                return item["intent"]

    # Allow bare intent names as input
    for item in menu:
        if normalized_option.casefold() == item["intent"].casefold():
            return item["intent"]

    # Check for common navigation text inputs (exact matches only)
    if normalized_option.casefold() in {"main menu", "main_menu", "menu", "home", "back", "go back"}:
        return "main_menu"

    # Don't match "handover" as common text - only exact match or button tap
    if normalized_option.casefold() == "handover":
        return "handover"

    return None
