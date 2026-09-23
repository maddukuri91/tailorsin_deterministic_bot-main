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
    "manage_orders": "📁",
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
#  Menu definitions
# ──────────────────────────────────────────────
# Each menu is an ordered list of {"label", "intent"} options. The "main" menu
# is the entry point for a customer segment; every other menu is a nested
# follow-up that is revealed after a related action is chosen.

MAIN_MENU_ID = "main"

NEW_USER_MENU = [
    {"label": "How the process Works",   "intent": "about"},
    {"label": "Price Catalogue",         "intent": "pricing"},
    {"label": "Place an Order",          "intent": "register"},
]

# Revealed beneath the "How the process Works" overview.
NEW_USER_ABOUT_MENU = [
    {"label": "Price Catalogue",         "intent": "pricing"},
    {"label": "Place an Order",          "intent": "register"},
]

# Revealed beneath the "Price Catalogue" pricing details.
NEW_USER_PRICING_MENU = [
    {"label": "Custom fabric Estimate",          "intent": "fabric_estimate"},
    {"label": "Bulk Order Enquiry",              "intent": "bulk_order_enquiry"},
    {"label": "Place an Order",                  "intent": "register"},
]

CLIENT_MENU = [
    {"label": "New Order",                   "intent": "new_order"},
    {"label": "Book Visit",                  "intent": "book_visit"},
    {"label": "Price Catalogue",             "intent": "pricing"},
    {"label": "Custom fabric Estimate",      "intent": "fabric_estimate"},
    {"label": "Bulk Order Enquiry",          "intent": "bulk_order_enquiry"},
    {"label": "Update Address",              "intent": "address_update"},
    {"label": "Human Support",               "intent": "handover"},
]

# Revealed beneath "New Order" for a customer who has ordered before.
CLIENT_ORDERS_MENU = [
    {"label": "New Order",                  "intent": "new_order"},
    {"label": "Drop Fabric",                "intent": "fabric_delivery"},
    {"label": "Book Visit",                 "intent": "book_visit"},
]

ACTIVE_CLIENT_MENU = [
    {"label": "Manage Orders",              "intent": "manage_orders"},
    {"label": "Book Visit",                 "intent": "book_visit"},
    {"label": "Price Catalogue",            "intent": "pricing"},
    {"label": "Custom fabric Estimate",     "intent": "fabric_estimate"},
    {"label": "Bulk Order Enquiry",         "intent": "bulk_order_enquiry"},
    {"label": "Update Address",             "intent": "address_update"},
    {"label": "Human Support",              "intent": "handover"},
]

# Revealed beneath "Manage Orders" for a customer with active orders.
ACTIVE_CLIENT_ORDERS_MENU = [
    {"label": "Track Order",                "intent": "order_status"},
    {"label": "Modify Order",               "intent": "order_changes"},
    {"label": "Cancel Order",               "intent": "order_cancel"},
    {"label": "Request Alteration",         "intent": "alteration_pickup_recent"},
]

# client segment -> menu id -> ordered options
SEGMENT_MENU_OPTIONS: dict[str, dict[str, list[dict[str, str]]]] = {
    "new_user": {
        MAIN_MENU_ID:       NEW_USER_MENU,
        "new_user_about":   NEW_USER_ABOUT_MENU,
        "new_user_pricing": NEW_USER_PRICING_MENU,
    },
    "client": {
        MAIN_MENU_ID:   CLIENT_MENU,
        "client_orders": CLIENT_ORDERS_MENU,
    },
    "active_client": {
        MAIN_MENU_ID:            ACTIVE_CLIENT_MENU,
        "active_client_orders":  ACTIVE_CLIENT_ORDERS_MENU,
    },
}

KNOWN_CLIENT_TYPES: set[str] = {"active_client", "client", "new_user"}


# Choosing one of these actions reveals the nested menu for it. Keyed by
# (segment, intent) because the same intent can behave differently per segment.
SUBMENU_INTENTS: dict[tuple[str, str], str] = {
    ("new_user", "about"):              "new_user_about",
    ("new_user", "pricing"):            "new_user_pricing",
    ("client", "new_order"):            "client_orders",
    ("active_client", "manage_orders"): "active_client_orders",
}

# Actions whose response *is* the nested menu. They have no content of their
# own, so the nested menu is sent instead of running a conversation flow.
MENU_ONLY_ACTIONS: frozenset[tuple[str, str]] = frozenset({
    ("client", "new_order"),
    ("active_client", "manage_orders"),
})

# Heading shown when a nested menu is opened on its own.
MENU_PROMPTS: dict[str, str] = {
    "client_orders": (
        "📋 *New Order*\n"
        "Start a fresh pickup, drop your fabric at the store, or book a store visit."
    ),
    "active_client_orders": (
        "📋 *Manage Orders*\n"
        "Track, modify, or cancel an order — or report an issue with a delivered order."
    ),
}


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


def get_menu_options(
    client_type: str | None,
    menu_id: str | None = MAIN_MENU_ID,
) -> list[dict[str, str]]:
    """Return the options for one of a segment's menus.

    Unknown or cross-segment ``menu_id`` values fall back to the segment's main
    menu, so a stale session can never leave a customer without a menu.
    """
    normalized = normalize_client_type(client_type)
    menus = SEGMENT_MENU_OPTIONS.get(normalized, SEGMENT_MENU_OPTIONS["new_user"])
    return menus.get(menu_id or MAIN_MENU_ID) or menus[MAIN_MENU_ID]


def get_follow_up_menu(
    client_type: str | None,
    intent: str,
    current_menu_id: str | None = MAIN_MENU_ID,
) -> str | None:
    """Return the nested menu an *intent* reveals, or ``None`` if it has none.

    ``current_menu_id`` is compared against the target so an action that also
    appears inside its own nested menu (for example ``new_order``) runs its flow
    instead of reopening the same menu forever.
    """
    target = SUBMENU_INTENTS.get((normalize_client_type(client_type), intent))
    if not target or target == (current_menu_id or MAIN_MENU_ID):
        return None
    return target


def is_menu_only_action(client_type: str | None, intent: str) -> bool:
    """Return whether *intent* only opens a nested menu, with no response of its own."""
    return (normalize_client_type(client_type), intent) in MENU_ONLY_ACTIONS


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
            f"Hello {salutation}. How can we help today?",
            "",
            "I can help with orders, pickups, visits, pricing, and support.",
            "",
        ])
    else:
        lines.extend([
            "👋 *Welcome to Tailorsin.com!*",
            "",
            "We offer premium bespoke tailoring and embroidery services for Men, Women, Kids, and Bridal wear. We collect your fabric, custom stitch it to your design, and deliver it to your doorstep—starting from just 24 hours after confirmation.",
            "",
        ])

    lines.extend([
        "Please choose an option below.",
    ])

    return "\n".join(lines)


# ──────────────────────────────────────────────
#  Keyboard layouts (Telegram inline buttons)
# ──────────────────────────────────────────────

def get_menu_inline_keyboard(
    client_type: str | None,
    menu_id: str | None = MAIN_MENU_ID,
) -> list[list[dict[str, str]]]:
    """
    Return an inline keyboard layout (2 columns) for the given menu.
    Each button sends its intent as callback_data.
    """
    menu = get_menu_options(client_type, menu_id)
    keyboard: list[list[dict[str, str]]] = []

    # Group options in pairs (2 columns)
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


def get_menu_reply_keyboard(
    client_type: str | None,
    menu_id: str | None = MAIN_MENU_ID,
) -> list[list[dict[str, str]]]:
    """
    Return a reply keyboard layout (2 columns) for platforms that don't
    support inline buttons (e.g. WATI/WhatsApp).
    """
    menu = get_menu_options(client_type, menu_id)
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
    """Return the two standard navigation actions used on every channel."""
    return [
        [{"text": "Main menu", "callback_data": "menu_main_menu"}],
        [{"text": "Human support", "callback_data": "menu_handover"}],
    ]


def get_nav_reply_keyboard() -> list[list[dict[str, str]]]:
    """Return the same two standard navigation actions for reply keyboards."""
    return [
        [{"text": "Main menu"}],
        [{"text": "Human support"}],
    ]
