"""
Menu configuration and formatting for the Tailorsin chatbot.

Provides structured menu options, formatted messages with emoji-enhanced
visual hierarchy, and keyboard layouts optimised for Telegram & WATI.
"""

from __future__ import annotations

# ──────────────────────────────────────────────
#  Emoji helpers
# ──────────────────────────────────────────────
_OPTION_ICONS: dict[str, str] = {
    # Orders
    "order_status": "🔍",
    "order_changes": "✏️",
    "order_cancel": "🚫",
    # Support
    "alteration_pickup_recent": "🔄",
    "handover": "💬",
    # Services
    "new_order": "➕",
    "fabric_estimate": "📐",
    "fabric_delivery": "📦",
    "book_visit": "📅",
    # Info
    "pricing": "💰",
    "browse": "👗",
    "about": "ℹ️",
    "measurement": "📏",
    "delivery": "🚚",
    "service_area": "📍",
    # Account
    "measurements": "📋",
    "address_update": "🏠",
    "register": "📝",
    # Navigation
    "main_menu": "🏠",
}


def _icon(intent: str) -> str:
    return _OPTION_ICONS.get(intent, "•")


# ──────────────────────────────────────────────
#  Common options (shared across segments)
# ──────────────────────────────────────────────

COMMON_OPTIONS: list[dict[str, str]] = [
    {"label": "Book a store visit",           "intent": "book_visit"},
    {"label": "Report an issue / Alteration", "intent": "alteration_pickup_recent"},
    {"label": "Estimate fabric & price",      "intent": "fabric_estimate"},
    {"label": "View price catalog",           "intent": "pricing"},
    {"label": "Update my address",            "intent": "address_update"},
    {"label": "Chat with a human agent",      "intent": "handover"},
]

# ──────────────────────────────────────────────
#  Menu data
#  Keys are unique identifiers, numbers are NOT shown to the customer.
#  Navigation is done purely through inline tap buttons.
# ──────────────────────────────────────────────

SEGMENT_MENU_OPTIONS: dict[str, list[dict[str, str]]] = {

    "active_client": [
        {"label": "Track my current order",      "intent": "order_status"},
        {"label": "Modify my order",             "intent": "order_changes"},
        {"label": "Cancel my order",             "intent": "order_cancel"},
        {"label": "Place a new order / Pickup",  "intent": "new_order"},
        *COMMON_OPTIONS,
    ],

    "client": [
        {"label": "Place an order / Pickup",      "intent": "new_order"},
        {"label": "Drop off fabric at store",     "intent": "fabric_delivery"},
        *COMMON_OPTIONS,
    ],

    "new_user": [
        {"label": "Learn about tailorsin.com",    "intent": "about"},
        {"label": "Register to place an order / book a visit", "intent": "register"},
    ],
}

KNOWN_CLIENT_TYPES: set[str] = {"active_client", "client", "new_user"}


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def normalize_client_type(client_type: str | None) -> str:
    """Map a raw client-type string to one of the known keys."""
    if not client_type:
        return "new_user"

    normalized = client_type.strip().lower().replace(" ", "_")

    if normalized in KNOWN_CLIENT_TYPES:
        return normalized

    if "active" in normalized:
        return "active_client"
    if "client" in normalized:
        return "client"

    return "new_user"


def get_menu_options(client_type: str | None) -> list[dict[str, str]]:
    """Return the menu items list for the given (normalised) client type."""
    normalized = normalize_client_type(client_type)
    return SEGMENT_MENU_OPTIONS.get(normalized, SEGMENT_MENU_OPTIONS["new_user"])


# ──────────────────────────────────────────────
#  Formatted text messages
# ──────────────────────────────────────────────

def format_menu_message(client_type: str | None) -> str:
    """Shortcut – delegates to the full greeting builder."""
    return format_menu_message_with_greeting(client_type)


def format_menu_message_with_greeting(
    client_type: str | None,
    customer_salutation: str | None = None,
    is_repeat: bool = False,
) -> str:
    """Build a polished, emoji-rich greeting message for the given client segment.
    
    Note: The menu options are shown via inline tap buttons. The text only
    contains the greeting and a prompt to tap a button.
    """
    normalized = normalize_client_type(client_type)

    lines: list[str] = []

    # ── Greeting ──────────────────────────────────
    if normalized in {"active_client", "client"}:
        salutation = customer_salutation or "valued customer"

        if is_repeat:
            lines.append("📋 *Here is the main menu again.*")
            lines.append("")

        lines.extend([
            "👋 *Welcome back!*",
            f"Hello {salutation}, great to see you again at **tailorsin.com** ✨",
            "",
            "I'm your AI assistant. How can I help you today?",
            "",
        ])
    else:
        lines.extend([
            "👋 *Welcome to tailorsin.com!*",
            "",
            "We bring professional tailoring to your doorstep. "
            "Here's everything you can do to get started 👇",
            "",
        ])

    lines.extend([
        "—",
        "Select an option below to get started ⬇️",
    ])

    return "\n".join(lines)


# ──────────────────────────────────────────────
#  Keyboard layouts (Telegram inline buttons)
# ──────────────────────────────────────────────

def get_menu_inline_keyboard(client_type: str | None) -> list[list[dict[str, str]]]:
    """
    Return an inline keyboard layout (2 columns) for the given client segment.
    Each button sends its intent as callback_data.
    """
    menu = get_menu_options(client_type)
    keyboard: list[list[dict[str, str]]] = []

    # Group main options in pairs (2 columns)
    items = list(menu)
    for i in range(0, len(items), 2):
        row: list[dict[str, str]] = []
        for j in range(2):
            if i + j < len(items):
                item = items[i + j]
                icon = _icon(item["intent"])
                row.append({
                    "text": f"{icon} {item['label']}",
                    "callback_data": f"menu_{item['intent']}",
                })
        keyboard.append(row)

    return keyboard


def get_menu_reply_keyboard(client_type: str | None) -> list[list[dict[str, str]]]:
    """
    Return a reply keyboard layout (2 columns) for platforms that don't
    support inline buttons (e.g. WATI/WhatsApp).
    """
    menu = get_menu_options(client_type)
    keyboard: list[list[dict[str, str]]] = []

    items = list(menu)
    for i in range(0, len(items), 2):
        row: list[dict[str, str]] = []
        for j in range(2):
            if i + j < len(items):
                item = items[i + j]
                icon = _icon(item["intent"])
                row.append({"text": f"{icon} {item['label']}"})
        keyboard.append(row)

    return keyboard


def get_nav_inline_keyboard() -> list[list[dict[str, str]]]:
    """Return an inline keyboard with just navigation buttons (handover & main menu)."""
    return [
        [{"text": "💬 Chat with a human agent", "callback_data": "menu_handover"}],
        [{"text": "🏠 Go back to main menu", "callback_data": "menu_main_menu"}],
    ]


def get_nav_reply_keyboard() -> list[list[dict[str, str]]]:
    """Return a reply keyboard with just navigation buttons for non-inline platforms."""
    return [
        [{"text": "💬 Chat with a human agent"}],
        [{"text": "🏠 Go back to main menu"}],
    ]
