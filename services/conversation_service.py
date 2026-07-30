from dataclasses import dataclass
from datetime import date, timedelta
import logging
import time
from typing import Any

from conversation.intent_router import get_intent
from config import settings
from services.geocoder import geocode_address
from conversation.menu import (
    format_menu_message_with_greeting,
    get_menu_inline_keyboard,
    get_menu_reply_keyboard,
    get_nav_inline_keyboard,
    get_nav_reply_keyboard,
)
from conversation.session import (
    begin_session_scope,
    end_session_scope,
    get_session,
    reset_session,
    save_active_sessions,
    save_session,
)
from conversation.state_manager import (
    get_client_profile,
    mark_awaiting_contact,
    save_client_profile,
)
from crm.alteration_pickup import schedule_alteration_pickup
from crm.book_appointment import book_store_visit, fetch_available_visit_slots
from crm.client_address import (
    AddressUpsertResult,
    add_client_address,
    delete_client_address,
    fetch_client_addresses,
)
from crm.client_type import lookup_customer_profile
from crm.cancel_order import cancel_current_order
from crm.fabric_delivery import create_fabric_delivery_request
from crm.fabric_alert import raise_fabric_alert
from crm.bulk_order import create_bulk_order_enquiry
from crm.delivered_orders import fetch_delivered_orders
from crm.human_handover import request_human_handover
from crm.order_change_request import modify_order
from crm.order_status import fetch_all_active_orders, fetch_current_order_status
from crm.schedule_pickup import schedule_fresh_pickup
from crm.schedule_another_pickup import schedule_another_pickup
from crm.user_register import register_new_client
from intents.browse import build_browse_response
from intents.measurements import build_measurements_response
from intents.order_status import build_order_status_response
from intents.pricing import build_pricing_response
from intents.visit_history import build_visit_history_response


logger = logging.getLogger(__name__)


def with_footer(text: str) -> str:
    """Keep existing response calls concise without adding a visible footer."""
    return text


TAILORSIN_OVERVIEW = (
"""
tailorsin.com collects your fabric, stitches your garments, and delivers them—typically within 24 hours after design approval and cloth pickup.

*How tailorsin.com Works*

1. *Schedule a Pickup*
• We collect your fabric from your location, or you can drop it off at our store.

2. *Share Your Fabric & Reference Garment*
• Provide your fabric along with a sample/reference garment.
• Within 6 business hours, our team contacts you to confirm the design and share a stitching estimate.

3. *Approve the Estimate & Make Payment*
• Once you approve the estimate and complete the payment, we begin stitching.

4. *Stitching & Delivery*
• Your stitched garments are delivered to your doorstep.

5. *Free Alterations*
• Free fitting alterations are available within 7 days of delivery.

*Measurement Policy*

• We do *not* provide home measurement services.
• Please provide a sample/reference garment or book a store visit.

*Delivery Timeline*

• Most orders are completed within *24 hours* of cloth pickup after estimate approval and payment.

*Alteration Policy*

• Free alterations are available within *7 days* of bill generation for fitting issues.

*Service Areas*

• Pickup & delivery across *Hyderabad*.
• Customers from other cities can also place orders by sending their fabric.

*Garments We Stitch*

• Men's Wear
• Women's Wear
• Kids' Wear

*Price List*

https://drive.google.com/file/d/1s67qOzn2n22lN670ir0Le462FcgGyCGL/view?usp=sharing

"""
)

# WATI/WhatsApp permits at most 1,024 characters in an interactive-message
# body. Keep the overview in two deliberate, readable sections so no service
# information is silently truncated by the provider.
_OVERVIEW_SPLIT_MARKER = "*Delivery Timeline*"
_overview_split_at = TAILORSIN_OVERVIEW.index(_OVERVIEW_SPLIT_MARKER)
TAILORSIN_OVERVIEW_SECTIONS = (
    TAILORSIN_OVERVIEW[:_overview_split_at].strip(),
    TAILORSIN_OVERVIEW[_overview_split_at:].strip(),
)


@dataclass
class IncomingMessage:
    user_id: int
    text: str = ""
    contact_phone: str | None = None
    contact_user_id: int | None = None
    source_user_id: int | None = None
    is_start_command: bool = False
    location_lat: float | None = None
    location_lng: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class OutgoingMessage:
    text: str
    reply_markup: dict[str, Any] | None = None
    include_wati_navigation: bool = True


async def build_intent_response(intent_name: str, client_type: str) -> list[OutgoingMessage] | None:
    if intent_name == "about":
        sections = [
            OutgoingMessage(text=text, include_wati_navigation=False)
            for text in TAILORSIN_OVERVIEW_SECTIONS[:-1]
        ]
        sections.append(
            OutgoingMessage(
                text=TAILORSIN_OVERVIEW_SECTIONS[-1],
                reply_markup=build_menu_reply_markup(client_type),
            )
        )
        return sections

    if intent_name == "browse":
        return [
            OutgoingMessage(
                text=await build_browse_response(),
                reply_markup=build_menu_reply_markup(client_type),
            )
        ]

    if intent_name == "pricing":
        return [
            OutgoingMessage(
                text=await build_pricing_response(client_type),
                reply_markup=build_menu_reply_markup(client_type),
            )
        ]

    return None


RETURNING_CLIENT_TYPES = {"active_client", "client"}
SESSION_TIMEOUT_SECONDS = settings.session_timeout_seconds


def normalize_mobile(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def extract_mobile_from_text(text: str) -> str | None:
    normalized = normalize_mobile(text)
    if 10 <= len(normalized) <= 15:
        return normalized
    return None


async def resolve_mobile(
    text: str,
    contact_phone: str | None,
    fallback_mobile: str | None = None,
) -> str | None:
    if contact_phone:
        mobile_from_contact = normalize_mobile(contact_phone)
        if mobile_from_contact:
            return mobile_from_contact

    mobile_from_text = extract_mobile_from_text(text) if text else None
    if mobile_from_text:
        return mobile_from_text

    return fallback_mobile


def derive_mobile_from_message(message: IncomingMessage, fallback_mobile: str | None = None) -> str | None:
    if fallback_mobile:
        return fallback_mobile

    metadata = message.metadata or {}
    platform = str(metadata.get("platform", "")).strip().lower()
    if platform == "wati":
        candidate = normalize_mobile(str(message.user_id))
        if 10 <= len(candidate) <= 15:
            return candidate

    return None


def is_telegram_message(message: IncomingMessage) -> bool:
    metadata = message.metadata or {}
    return str(metadata.get("platform", "")).strip().lower() == "telegram"


def is_onboarding_trigger(message: IncomingMessage) -> bool:
    if message.is_start_command:
        return True

    normalized_text = (message.text or "").strip().casefold()
    return normalized_text in {"hi", "hello", "menu", "main menu", "start"}


async def resolve_client_type(
    text: str,
    contact_phone: str | None,
    fallback_mobile: str | None = None,
) -> tuple[str | None, str, str | None]:
    mobile = await resolve_mobile(text, contact_phone, fallback_mobile)
    customer_profile = await lookup_customer_profile(mobile or "")
    return mobile, customer_profile.client_type, customer_profile.customer_salutation


def build_contact_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "Share mobile number", "request_contact": True}],
            *get_nav_reply_keyboard(),
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def build_menu_reply_markup(client_type: str) -> dict[str, Any]:
    """Build an inline keyboard markup for the menu (Telegram inline buttons)."""
    return {
        "inline_keyboard": get_menu_inline_keyboard(client_type),
    }


def build_nav_keyboard() -> dict[str, Any]:
    """Build an inline keyboard with just navigation buttons (handover & main menu)."""
    return {
        "inline_keyboard": get_nav_inline_keyboard(),
    }


def build_selection_reply_markup(options: list[str]) -> dict[str, Any]:
    """Build a consistent selection UI for Telegram and WhatsApp.

    Every selectable step shows one option per row followed by the same two
    escape actions. WATI converts this structure to buttons or a list as
    appropriate; Telegram renders it as a reply keyboard.
    """
    return {
        "keyboard": [
            *[[{"text": option}] for option in options],
            *get_nav_reply_keyboard(),
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def build_number_selection_reply_markup(count: int, *, include_add_address: bool = False) -> dict[str, Any]:
    options = [str(index) for index in range(1, count + 1)]
    if include_add_address:
        options.append("Add address")
    return build_selection_reply_markup(options)


def build_location_choice_reply_markup() -> dict[str, Any]:
    """Offer consistent share, manual, and automatic-location choices."""
    return {
        "keyboard": [
            [{"text": "Share current location", "request_location": True}],
            [{"text": "Enter location manually"}],
            [{"text": "Skip location"}],
            *get_nav_reply_keyboard(),
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def build_location_link_fallback_reply_markup() -> dict[str, Any]:
    """Actions available after WATI omits a shared map's coordinates."""
    return build_selection_reply_markup([
        "Enter location manually",
        "Skip location",
    ])


LOCATION_PROMPT = (
    "📍 *Location (optional)*\n"
    "Share your current location to help us find the pickup address.\n\n"
    "On WhatsApp, use Attach → Location. You can also choose an option below."
)


def build_pickup_time_reply_markup() -> dict[str, Any]:
    return build_selection_reply_markup([
        "1. Morning (9 AM - 2 PM)",
        "2. Afternoon (2 PM - 9 PM)",
    ])


def parse_pickup_time_option(raw_text: str) -> int | None:
    normalized_text = (raw_text or "").strip().casefold()
    # WATI interactive buttons allow only 20 title characters, so WhatsApp
    # sends values such as "1. Morning (9 AM - 2". It may also quote the
    # earlier message and append that selected value on the final line.
    candidates = [normalized_text]
    candidates.extend(
        line.strip().casefold()
        for line in (raw_text or "").splitlines()
        if line.strip()
    )
    for candidate in reversed(candidates):
        if candidate.startswith("1.") or candidate == "1":
            return 1
        if candidate.startswith("2.") or candidate == "2":
            return 2

    if normalized_text in {
        "1",
        "1. 9am-2pm",
        "1. morning (9 am - 2 pm)",
        "morning",
    }:
        return 1
    if normalized_text in {
        "2",
        "2. 2pm-9pm",
        "2. afternoon (2 pm - 9 pm)",
        "afternoon",
    }:
        return 2
    return None


def get_pickup_date_choices() -> list[tuple[str, str]]:
    today = date.today()
    return [
        ("1", today.isoformat()),
        ("2", (today + timedelta(days=1)).isoformat()),
        ("3", (today + timedelta(days=2)).isoformat()),
    ]


def build_pickup_date_reply_markup() -> dict[str, Any]:
    choices = get_pickup_date_choices()
    return build_selection_reply_markup([f"{index}. {value}" for index, value in choices])


def parse_pickup_date_option(raw_text: str) -> str | None:
    normalized_text = (raw_text or "").strip()

    choice_lookup = {index: value for index, value in get_pickup_date_choices()}

    if normalized_text in choice_lookup:
        return choice_lookup[normalized_text]

    for index, value in get_pickup_date_choices():
        if normalized_text == f"{index}. {value}":
            return value

    # Keep ISO-date input accepted as a fallback.
    try:
        pickup_date = date.fromisoformat(normalized_text)
    except ValueError:
        return None

    if pickup_date < date.today():
        return None

    return pickup_date.isoformat()


def build_visit_slot_reply_markup(slots: list[str]) -> dict[str, Any]:
    return build_selection_reply_markup([
        f"{index + 1}. {slot}" for index, slot in enumerate(slots)
    ])


def parse_visit_slot_option(raw_text: str, slots: list[str]) -> str | None:
    normalized_text = (raw_text or "").strip()
    if not normalized_text or not slots:
        return None

    if normalized_text.isdigit():
        index = int(normalized_text) - 1
        if 0 <= index < len(slots):
            return slots[index]

    # WATI list titles can be shortened. The leading number remains stable
    # and is sufficient to identify the selected visit slot.
    for line in reversed([line.strip() for line in normalized_text.splitlines() if line.strip()]):
        if "." in line:
            prefix, _ = line.split(".", 1)
            if prefix.strip().isdigit():
                index = int(prefix.strip()) - 1
                if 0 <= index < len(slots):
                    return slots[index]

    for index, slot in enumerate(slots, start=1):
        if normalized_text == f"{index}. {slot}":
            return slot
        if normalized_text.casefold() == slot.casefold():
            return slot

    return None


def clear_all_flows(session: Any) -> None:
    """Clear all in-progress sub-flow flags, returning session to a clean state."""
    clear_address_update_flow(session)
    clear_pickup_flow(session)
    clear_order_change_flow(session)
    clear_order_cancel_flow(session)
    session.awaiting_contact = False
    session.awaiting_registration_name = False
    session.awaiting_registration_email = False
    session.pending_registration_name = None
    session.awaiting_visit_date = False
    session.awaiting_visit_time = False
    session.awaiting_fabric_delivery_notes = False
    session.pending_visit_date = None
    session.pending_visit_slots = []
    session.address_needed_for_pickup = False


def clear_address_update_flow(session: Any) -> None:
    session.awaiting_address_action = False
    session.awaiting_address_add_line = False
    session.awaiting_address_add_city = False
    session.awaiting_address_add_pincode = False
    session.awaiting_address_delete_id = False
    session.pending_address_line = None
    session.pending_address_city = None
    session.pending_address_pincode = None
    session.pending_address_list_ids = []
    session.awaiting_address_location = False
    session.pending_address_lat = None
    session.pending_address_lng = None
    session.awaiting_manual_coordinates = False


def clear_pickup_flow(session: Any) -> None:
    session.awaiting_pickup_date = False
    session.awaiting_pickup_time = False
    session.awaiting_alteration_pickup_notes = False
    session.awaiting_pickup_address = False
    session.pending_pickup_date = None
    session.pending_pickup_time = None
    session.pending_pickup_address_id = None
    session.pending_address_ordered_ids = []
    session.pickup_mode = None
    session.awaiting_alteration_order = False
    session.pending_alteration_order_ids = []
    session.pending_alteration_order_id = None


def clear_order_change_flow(session: Any) -> None:
    session.awaiting_order_change_select = False
    session.awaiting_order_change_details = False
    session.pending_change_order_id = None
    session.pending_change_order_ids = []


def clear_order_cancel_flow(session: Any) -> None:
    session.awaiting_order_cancel_select = False
    session.awaiting_order_cancel_reason = False
    session.pending_cancel_order_id = None
    session.pending_cancel_order_ids = []


async def build_address_list_message(mobile: str) -> str:
    result = await fetch_client_addresses(mobile)
    if not result.success:
        return result.message

    lines: list[str] = []
    if result.customer_name:
        lines.append(f"Saved addresses for {result.customer_name}:")
    else:
        lines.append("Saved addresses:")
    lines.append("")

    if result.addresses:
        for index, address in enumerate(result.addresses, start=1):
            main_tag = " (Main)" if address.is_main else ""
            location_parts = [part for part in [address.address1, address.city, address.pincode] if part]
            lines.append(f"{index}. {', '.join(location_parts)}{main_tag}")
    else:
        lines.append("No saved addresses found.")

    lines.extend(
        [
            "",
            "Choose an action below.",
        ]
    )
    return "\n".join(lines)


def send_main_menu(
    client_type: str,
    customer_salutation: str | None = None,
    is_repeat: bool = False,
) -> OutgoingMessage:
    return OutgoingMessage(
        text=format_menu_message_with_greeting(client_type, customer_salutation, is_repeat),
        reply_markup=build_menu_reply_markup(client_type),
    )


async def build_main_menu_response(
    user_id: int,
    client_type: str,
    customer_salutation: str | None,
) -> OutgoingMessage:
    session = await get_session(user_id)
    is_repeat = client_type in RETURNING_CLIENT_TYPES and session.has_seen_known_customer_menu

    if client_type in RETURNING_CLIENT_TYPES:
        session.has_seen_known_customer_menu = True
        await save_session(session)

    return send_main_menu(client_type, customer_salutation, is_repeat)


async def _handle_incoming_message(message: IncomingMessage) -> list[OutgoingMessage]:
    now = time.time()
    existing_session = await get_session(message.user_id)

    if now - existing_session.last_activity_at > SESSION_TIMEOUT_SECONDS:
        await reset_session(message.user_id)
        existing_session = await get_session(message.user_id)

    existing_session.last_activity_at = now
    # Redis returns a fresh SessionState for every webhook request. Persist the
    # activity update immediately so WhatsApp button taps do not look stale on
    # the following request.
    await save_session(existing_session)
    existing_mobile, existing_client_type, existing_customer_salutation = await get_client_profile(message.user_id)
    auto_mobile = derive_mobile_from_message(message, existing_mobile)

    logger.info(
        "incoming_message user_id=%s text=%r start=%s mobile_known=%s auto_mobile=%s client_type=%s",
        message.user_id,
        (message.text or "")[:80],
        message.is_start_command,
        bool(existing_mobile),
        bool(auto_mobile),
        existing_client_type,
    )

    if is_telegram_message(message) and not existing_mobile and not message.contact_phone:
        if existing_session.awaiting_contact or is_onboarding_trigger(message):
            await mark_awaiting_contact(message.user_id)
            logger.info("onboarding_request_contact user_id=%s", message.user_id)
            return [
                OutgoingMessage(
                    text="Please share your mobile number using the button below.",
                    reply_markup=build_contact_keyboard(),
                )
            ]

    if message.is_start_command:
        has_seen_known_customer_menu = existing_session.has_seen_known_customer_menu
        await reset_session(message.user_id)
        existing_session = await get_session(message.user_id)
        existing_session.has_seen_known_customer_menu = has_seen_known_customer_menu
        await save_session(existing_session)
        mobile, client_type, customer_salutation = await resolve_client_type(
            message.text,
            message.contact_phone,
            auto_mobile,
        )
        await save_client_profile(message.user_id, mobile, client_type, customer_salutation)
        logger.info(
            "start_flow user_id=%s resolved_client_type=%s mobile_present=%s",
            message.user_id,
            client_type,
            bool(mobile),
        )
        return [await build_main_menu_response(message.user_id, client_type, customer_salutation)]

    # --- Early navigation interceptor: allow escape from any sub-flow ---
    if message.text:
        normalized_text = (message.text or "").strip().casefold()
        # Also resolve via get_intent to handle full button tap text like "9. 💬 Chat with a human agent"
        interceptor_intent = get_intent(existing_client_type or "new_user", message.text)

        if normalized_text in {"0", "10", "menu", "main menu"} or interceptor_intent == "main_menu":
            clear_all_flows(existing_session)
            mobile, client_type, customer_salutation = await resolve_client_type(
                message.text, message.contact_phone, auto_mobile or existing_mobile,
            )
            await save_client_profile(message.user_id, mobile, client_type, customer_salutation)
            return [await build_main_menu_response(message.user_id, client_type, customer_salutation)]

        if normalized_text in {"9", "handover"} or interceptor_intent == "handover":
            # If the user is not yet registered (new_user), redirect to registration first
            resolved_client = existing_client_type
            if not resolved_client:
                _, resolved_client, _ = await resolve_client_type(
                    message.text, message.contact_phone, auto_mobile or existing_mobile,
                )

            if resolved_client == "new_user":
                existing_session.awaiting_registration_name = True
                return [
                    OutgoingMessage(
                        text=with_footer("Please complete registration first. Enter your full name to continue."),
                        reply_markup=build_nav_keyboard(),
                    )
                ]

            clear_all_flows(existing_session)
            mobile_for_handover = derive_mobile_from_message(message, existing_mobile)
            if not mobile_for_handover:
                mobile_for_handover = auto_mobile or existing_mobile
            if not mobile_for_handover:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for human handover. Please share contact or send your mobile number.",
                        reply_markup=build_contact_keyboard(),
                    )
                ]
            handover_result = await request_human_handover(mobile_for_handover)
            return [OutgoingMessage(text=handover_result.message)]
    # --- End early navigation interceptor ---

    # Telegram callbacks and WATI interactive replies are explicit menu taps.
    # A customer may start another menu action while an earlier flow is still
    # waiting for input (for example, fabric notes). A menu tap must take
    # priority over that stale state; otherwise a later address choice such as
    # "2" is incorrectly consumed by the earlier flow.
    metadata = message.metadata or {}
    if metadata.get("is_menu_selection") and message.text:
        selection_text = (message.text or "").strip()
        # Numbered choices are used by address, order, date, and slot
        # sub-flows. WATI marks every interactive response as a menu
        # selection, but a value such as "1" is not a fresh main-menu tap.
        # Treating it as one restarts New Order instead of advancing the flow.
        tapped_intent = None if selection_text.isdigit() else get_intent(
            existing_client_type or "new_user", message.text
        )
        if tapped_intent and tapped_intent not in {"main_menu", "handover"}:
            clear_all_flows(existing_session)
            await save_session(existing_session)
            logger.info(
                "menu_tap_cleared_stale_flows user_id=%s intent=%s",
                message.user_id,
                tapped_intent,
            )

    if existing_session.awaiting_registration_name:
        registration_name = (message.text or "").strip()
        if len(registration_name) < 2:
            return [OutgoingMessage(text="Please enter a valid full name for registration.")]

        mobile_for_registration = derive_mobile_from_message(message, existing_mobile)
        if not mobile_for_registration:
            existing_session.awaiting_registration_name = False
            return [
                OutgoingMessage(
                    text="I could not detect your mobile number automatically for this session, so registration could not be completed.",
                )
            ]

        # Store name and proceed to ask for email
        existing_session.pending_registration_name = registration_name
        existing_session.awaiting_registration_name = False
        existing_session.awaiting_registration_email = True

        return [
            OutgoingMessage(
                text=with_footer(
                    "Enter your email address (optional).\n"
                    "Reply *Skip* if you would prefer not to add an email address."
                ),
                reply_markup=build_nav_keyboard(),
            )
        ]

    if existing_session.awaiting_registration_email:
        registration_name = existing_session.pending_registration_name
        mobile_for_registration = derive_mobile_from_message(message, existing_mobile)

        if not mobile_for_registration or not registration_name:
            existing_session.awaiting_registration_email = False
            existing_session.pending_registration_name = None
            return [
                OutgoingMessage(
                    text="Registration details were lost. Please start the registration process again from the menu.",
                )
            ]

        registration_email = (message.text or "").strip()
        if registration_email.casefold() in {"skip", "no", "none", ""}:
            registration_email = None

        existing_session.awaiting_registration_email = False
        existing_session.pending_registration_name = None

        registration_result = await register_new_client(mobile_for_registration, registration_name, email=registration_email)

        logger.info(
            "registration_attempt user_id=%s mobile_present=%s has_email=%s success=%s",
            message.user_id,
            bool(mobile_for_registration),
            bool(registration_email),
            registration_result.success,
        )

        if registration_result.success:
            mobile, client_type, customer_salutation = await resolve_client_type(
                message.text,
                message.contact_phone,
                mobile_for_registration,
            )
            await save_client_profile(message.user_id, mobile, client_type, customer_salutation)
            return [
                OutgoingMessage(
                    text=f"{registration_result.message}\nRegistration complete. Showing your client menu now.",
                ),
                await build_main_menu_response(message.user_id, client_type, customer_salutation),
            ]

        return [
            OutgoingMessage(
                text=registration_result.message,
                reply_markup=build_menu_reply_markup(existing_client_type or "new_user"),
            )
        ]

    if existing_session.awaiting_pickup_date:
        pickup_date = parse_pickup_date_option(message.text)
        if pickup_date is None:
            # Check if user tapped the back button
            if (message.text or "").strip() == "← Back to main menu":
                clear_all_flows(existing_session)
                return [await build_main_menu_response(message.user_id, existing_client_type or "client", existing_customer_salutation)]
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Select a pickup date:\n"
                        "1. Today's date\n"
                        "2. Tomorrow's date\n"
                        "3. Day after tomorrow"
                    ),
                    reply_markup=build_pickup_date_reply_markup(),
                )
            ]

        existing_session.pending_pickup_date = pickup_date
        existing_session.awaiting_pickup_date = False
        existing_session.awaiting_pickup_time = True
        return [
            OutgoingMessage(
                text=with_footer("Select a pickup time slot:\n1. Morning (9 AM – 2 PM)\n2. Afternoon (2 PM – 9 PM)"),
                reply_markup=build_pickup_time_reply_markup(),
            )
        ]

    if existing_session.awaiting_pickup_time:
        pickup_time = parse_pickup_time_option(message.text)
        if pickup_time is None:
            if (message.text or "").strip() == "← Back to main menu":
                clear_all_flows(existing_session)
                return [await build_main_menu_response(message.user_id, existing_client_type or "client", existing_customer_salutation)]
            return [
                OutgoingMessage(
                    text=with_footer("Select a pickup time slot: Morning or Afternoon."),
                    reply_markup=build_pickup_time_reply_markup(),
                )
            ]

        mobile_for_pickup = derive_mobile_from_message(message, existing_mobile)
        pickup_date = existing_session.pending_pickup_date
        pickup_mode = existing_session.pickup_mode or "fresh"

        if not mobile_for_pickup or not pickup_date:
            clear_pickup_flow(existing_session)
            return [
                OutgoingMessage(
                    text=with_footer("We could not schedule your pickup because some details are missing. Please start a new order from the main menu."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        if pickup_mode == "alteration":
            existing_session.awaiting_pickup_time = False
            existing_session.awaiting_alteration_pickup_notes = True
            existing_session.pending_pickup_time = pickup_time
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Add a note about the alteration (optional).\n"
                        "Example: Shorten the shirt sleeves.\n"
                        "Reply *Skip* to continue without a note."
                    ),
                    reply_markup=build_nav_keyboard(),
                )
            ]

        # For fresh pickup, address should already be selected (or we're in address-needed flow)
        existing_session.pending_pickup_time = pickup_time
        existing_session.awaiting_pickup_time = False

        # Check if we have an address_id from earlier in the flow
        address_id = existing_session.pending_pickup_address_id

        # Select the scheduling API based on the pickup mode
        schedule_func = schedule_another_pickup if pickup_mode == "another" else schedule_fresh_pickup

        # If no address_id, try scheduling without it (API will return needs_address if required)
        schedule_result = await schedule_func(mobile_for_pickup, pickup_date, pickup_time, address_id=address_id)
        clear_pickup_flow(existing_session)

        if schedule_result.needs_address:
            # No saved address — redirect to address update flow
            existing_session.address_needed_for_pickup = True
            existing_session.pending_pickup_date = pickup_date
            existing_session.pending_pickup_time = pickup_time
            clear_address_update_flow(existing_session)
            existing_session.awaiting_address_action = True
            mobile_for_address = mobile_for_pickup
            return [
                OutgoingMessage(
                    text=(
                        f"{schedule_result.message}\n\n"
                        "Please add a delivery/pickup address first so we can schedule the pickup."
                    ),
                ),
                OutgoingMessage(
                    text=await build_address_list_message(mobile_for_address),
                ),
            ]

        if schedule_result.success:
            # Fresh pickup creates an active order, so switch menu context immediately.
            await save_client_profile(
                message.user_id,
                mobile_for_pickup,
                "active_client",
                existing_customer_salutation,
            )
            # Retrieve updated profile to ensure we have the latest salutation
            _, _, updated_salutation = await get_client_profile(message.user_id)
            return [
                OutgoingMessage(text=schedule_result.message),
                await build_main_menu_response(message.user_id, "active_client", updated_salutation),
            ]

        if schedule_result.conflict_open_order:
            return [
                OutgoingMessage(
                    text=(
                        f"{schedule_result.message}\n"
                        "You already have an active open order. Please use pickup alteration flow instead."
                    ),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        return [
            OutgoingMessage(
                text=schedule_result.message,
                reply_markup=build_menu_reply_markup(existing_client_type or "client"),
            )
        ]

    if existing_session.awaiting_pickup_address:
        normalized_text = (message.text or "").strip().casefold()

        # The customer explicitly chose to add an address for this pickup.
        # Go directly to address capture; re-showing the generic address list
        # creates duplicate replies and makes them choose "1" a second time.
        if normalized_text in {"add", "add address", "new address"}:
            existing_session.awaiting_pickup_address = False
            existing_session.address_needed_for_pickup = True
            clear_address_update_flow(existing_session)
            existing_session.awaiting_address_add_line = True
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Please enter Address Line 1: door number, building name, street, and area. "
                        "Do not include city or PIN code."
                    ),
                    reply_markup=build_nav_keyboard(),
                )
            ]

        choice_text = normalize_mobile(message.text or "")
        if not choice_text:
            return [
                OutgoingMessage(
                    text=with_footer("Please select a saved address below, or choose Add address."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_address_ordered_ids), include_add_address=True
                    ),
                )
            ]

        choice_index = int(choice_text) - 1
        if not (0 <= choice_index < len(existing_session.pending_address_ordered_ids)):
            return [
                OutgoingMessage(
                    text=with_footer("That address is not available. Please select an option below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_address_ordered_ids), include_add_address=True
                    ),
                )
            ]

        # Address selected — store it and move on to date/time collection
        address_id = existing_session.pending_address_ordered_ids[choice_index]
        existing_session.pending_pickup_address_id = address_id
        existing_session.awaiting_pickup_address = False
        existing_session.awaiting_pickup_date = True

        pickup_intro = "Select a pickup date:"
        if existing_session.pickup_mode == "alteration":
            pickup_intro = "Select a date for the alteration pickup:"

        return [
            OutgoingMessage(
                text=with_footer(
                    f"{pickup_intro}\n"
                    "1. Today's date\n"
                    "2. Tomorrow's date\n"
                    "3. Day after tomorrow"
                ),
                reply_markup=build_pickup_date_reply_markup(),
            )
        ]

    if existing_session.awaiting_alteration_order:
        choice_text = normalize_mobile(message.text or "")
        if not choice_text:
            return [
                OutgoingMessage(
                    text=with_footer("Please select a delivered order below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_alteration_order_ids)
                    ),
                )
            ]

        choice_index = int(choice_text) - 1
        if not (0 <= choice_index < len(existing_session.pending_alteration_order_ids)):
            return [
                OutgoingMessage(
                    text=with_footer("That order is not available. Please select an order below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_alteration_order_ids)
                    ),
                )
            ]

        order_id = existing_session.pending_alteration_order_ids[choice_index]
        existing_session.pending_alteration_order_id = order_id
        existing_session.awaiting_alteration_order = False

        mobile_for_pickup = derive_mobile_from_message(message, existing_mobile)
        if not mobile_for_pickup:
            clear_pickup_flow(existing_session)
            return [
                OutgoingMessage(
                    text=with_footer("I could not identify your mobile number for this request. Please share contact or send your mobile number."),
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        address_result = await fetch_client_addresses(mobile_for_pickup)
        if not address_result.success or not address_result.addresses:
            existing_session.address_needed_for_pickup = True
            clear_address_update_flow(existing_session)
            existing_session.awaiting_address_action = True
            return [
                OutgoingMessage(
                    text=(
                        "No saved addresses found. Please add a delivery/pickup address first "
                        "so we can schedule the alteration pickup.\n\n"
                        "Please reply 1 to add a new address."
                    ),
                ),
                OutgoingMessage(
                    text=with_footer(await build_address_list_message(mobile_for_pickup)),
                ),
            ]

        existing_session.awaiting_pickup_address = True
        existing_session.pending_address_ordered_ids = [a.address_id for a in address_result.addresses]

        address_lines: list[str] = []
        if address_result.customer_name:
            address_lines.append(f"Saved addresses for {address_result.customer_name}:")
        else:
            address_lines.append("Saved addresses:")
        address_lines.append("")

        for index, address in enumerate(address_result.addresses, start=1):
            main_tag = " (Main)" if address.is_main else ""
            location_parts = [part for part in [address.address1, address.city, address.pincode] if part]
            address_lines.append(f"{index}. {', '.join(location_parts)}{main_tag}")

        address_lines.extend([
            "",
            "Select an address below, or choose Add address.",
        ])

        return [
            OutgoingMessage(
                text=with_footer("\n".join(address_lines)),
                reply_markup=build_number_selection_reply_markup(
                    len(address_result.addresses), include_add_address=True
                ),
            )
        ]

    if existing_session.awaiting_alteration_pickup_notes:
        mobile_for_pickup = derive_mobile_from_message(message, existing_mobile)
        pickup_date = existing_session.pending_pickup_date
        pickup_time = existing_session.pending_pickup_time
        order_id = existing_session.pending_alteration_order_id
        address_id = existing_session.pending_pickup_address_id
        notes = (message.text or "").strip()
        if notes.casefold() in {"skip", "no", "none"}:
            notes = ""

        clear_pickup_flow(existing_session)

        if not mobile_for_pickup or not pickup_date or pickup_time is None:
            return [
                OutgoingMessage(
                    text=with_footer("We could not schedule the alteration pickup because some details are missing. Please start again from the main menu."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
                )
            ]

        alteration_result = await schedule_alteration_pickup(
            mobile=mobile_for_pickup,
            pickup_date=pickup_date,
            pickup_time=pickup_time,
            order_id=order_id,
            address_id=address_id,
            notes=notes,
        )

        if alteration_result.success:
            # An alteration pickup order was placed, so transition the customer
            # to the active_client menu (mirrors fresh pickup behaviour).
            await save_client_profile(
                message.user_id,
                mobile_for_pickup,
                "active_client",
                existing_customer_salutation,
            )
            _, _, updated_salutation = await get_client_profile(message.user_id)
            return [
                OutgoingMessage(text=alteration_result.message),
                await build_main_menu_response(message.user_id, "active_client", updated_salutation),
            ]

        return [
            OutgoingMessage(
                text=with_footer(alteration_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
            )
        ]

    if existing_session.awaiting_visit_date:
        visit_date = parse_pickup_date_option(message.text)
        if visit_date is None:
            # Check if user tapped the back button
            if (message.text or "").strip() == "← Back to main menu":
                clear_all_flows(existing_session)
                return [await build_main_menu_response(message.user_id, existing_client_type or "client", existing_customer_salutation)]
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Select a visit date:\n"
                        "1. Today's date\n"
                        "2. Tomorrow's date\n"
                        "3. Day after tomorrow"
                    ),
                    reply_markup=build_pickup_date_reply_markup(),
                )
            ]

        availability_result = await fetch_available_visit_slots(visit_date)
        if not availability_result.success:
            return [
                OutgoingMessage(
                    text=with_footer(availability_result.message),
                    reply_markup=build_pickup_date_reply_markup(),
                )
            ]

        existing_session.pending_visit_date = visit_date
        existing_session.pending_visit_slots = availability_result.slots
        existing_session.awaiting_visit_date = False
        existing_session.awaiting_visit_time = True

        slot_lines = [
            "Select an available visit time:",
            *[f"{index + 1}. {slot}" for index, slot in enumerate(availability_result.slots)],
        ]
        return [
            OutgoingMessage(
                text=with_footer("\n".join(slot_lines)),
                reply_markup=build_visit_slot_reply_markup(availability_result.slots),
            )
        ]

    if existing_session.awaiting_visit_time:
        if (message.text or "").strip() == "← Back to main menu":
            clear_all_flows(existing_session)
            return [await build_main_menu_response(message.user_id, existing_client_type or "client", existing_customer_salutation)]
        
        selected_slot = parse_visit_slot_option(message.text, existing_session.pending_visit_slots)
        if selected_slot is None:
            return [
                OutgoingMessage(
                    text=with_footer("Please select one of the available visit times."),
                    reply_markup=build_visit_slot_reply_markup(existing_session.pending_visit_slots),
                )
            ]

        visit_mobile = derive_mobile_from_message(message, existing_mobile)
        visit_date = existing_session.pending_visit_date

        existing_session.awaiting_visit_time = False
        existing_session.pending_visit_date = None
        existing_session.pending_visit_slots = []

        if not visit_mobile or not visit_date:
            return [
                OutgoingMessage(
                    text=with_footer("We could not schedule your store visit because some details are missing. Please start again from the main menu."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        book_result = await book_store_visit(visit_mobile, visit_date, selected_slot)
        if book_result.success:
            return [
                OutgoingMessage(text=book_result.message),
                await build_main_menu_response(message.user_id, existing_client_type or "client", existing_customer_salutation),
            ]

        return [
            OutgoingMessage(
                text=with_footer(book_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "client"),
            )
        ]

    if existing_session.awaiting_fabric_delivery_notes:
        mobile_for_fabric_delivery = derive_mobile_from_message(message, existing_mobile)
        existing_session.awaiting_fabric_delivery_notes = False

        if not mobile_for_fabric_delivery:
            return [
                OutgoingMessage(
                    text=with_footer("I could not identify your mobile number for fabric delivery request. Please share contact or send your mobile number."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        notes = (message.text or "").strip()
        if notes.casefold() in {"skip", "no", "none"}:
            notes = ""

        fabric_delivery_result = await create_fabric_delivery_request(mobile_for_fabric_delivery, notes=notes)
        handover_result = await request_human_handover(mobile_for_fabric_delivery)
        return [
            OutgoingMessage(
                text=with_footer(f"{fabric_delivery_result.message}\n{handover_result.message}"),
                reply_markup=build_menu_reply_markup(existing_client_type or "client"),
            )
        ]

    if existing_session.awaiting_address_action:
        normalized_text = (message.text or "").strip().casefold()
        if normalized_text in {"1", "add", "add address"}:
            existing_session.awaiting_address_action = False
            existing_session.awaiting_address_add_line = True
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Please enter Address Line 1: door number, building name, street, and area. "
                        "Do not include city or PIN code."
                    ),
                    reply_markup=build_nav_keyboard(),
                )
            ]

        if normalized_text in {"2", "delete", "delete address", "remove", "remove address"}:
            existing_session.awaiting_address_action = False
            existing_session.awaiting_address_delete_id = True
            return [OutgoingMessage(
                text=with_footer("Select the address you would like to delete below."),
                reply_markup=build_number_selection_reply_markup(
                    len(existing_session.pending_address_list_ids)
                ),
            )]

        return [
            OutgoingMessage(
                text=with_footer("Please choose an address action below."),
                reply_markup=build_selection_reply_markup(["Add address", "Delete address"]),
            )
        ]

    if existing_session.awaiting_address_delete_id:
        choice_text = normalize_mobile(message.text or "")
        if not choice_text:
            return [OutgoingMessage(
                text=with_footer("Please select an address below."),
                reply_markup=build_number_selection_reply_markup(
                    len(existing_session.pending_address_list_ids)
                ),
            )]

        choice_index = int(choice_text) - 1
        if not (0 <= choice_index < len(existing_session.pending_address_list_ids)):
            return [
                OutgoingMessage(
                    text=with_footer("That address is not available. Please select an address below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_address_list_ids)
                    ),
                )
            ]

        mobile_for_address = derive_mobile_from_message(message, existing_mobile)
        address_id = existing_session.pending_address_list_ids[choice_index]
        clear_address_update_flow(existing_session)

        if not mobile_for_address:
            return [
                OutgoingMessage(
                    text=with_footer("We could not delete this address because we could not verify your mobile number. Please try again from Update Address."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        delete_result = await delete_client_address(mobile_for_address, address_id)
        return [
            OutgoingMessage(
                text=with_footer(delete_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "client"),
            )
        ]

    if existing_session.awaiting_address_add_line:
        address_line = (message.text or "").strip()
        if len(address_line) < 5:
            return [OutgoingMessage(text=with_footer("Please enter a valid Address Line 1."), reply_markup=build_nav_keyboard())]

        existing_session.pending_address_line = address_line
        existing_session.awaiting_address_add_line = False
        existing_session.awaiting_address_add_city = True
        return [OutgoingMessage(text=with_footer("Please enter your city."), reply_markup=build_nav_keyboard())]

    if existing_session.awaiting_address_add_city:
        city = (message.text or "").strip()
        if len(city) < 2:
            return [OutgoingMessage(text=with_footer("Please enter a valid city name."), reply_markup=build_nav_keyboard())]

        existing_session.pending_address_city = city
        existing_session.awaiting_address_add_city = False
        existing_session.awaiting_address_add_pincode = True
        return [OutgoingMessage(text=with_footer("Please enter your 6-digit PIN code."), reply_markup=build_nav_keyboard())]

    if existing_session.awaiting_address_add_pincode:
        pincode = normalize_mobile(message.text or "")
        if len(pincode) != 6:
            return [OutgoingMessage(text=with_footer("Please enter a valid 6-digit PIN code."), reply_markup=build_nav_keyboard())]

        mobile_for_address = derive_mobile_from_message(message, existing_mobile)
        address_line = existing_session.pending_address_line
        city = existing_session.pending_address_city

        if not mobile_for_address or not address_line or not city:
            clear_address_update_flow(existing_session)
            return [
                OutgoingMessage(
                    text=with_footer("We could not save your address because some details are missing. Please try again from Update Address."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        # Store pincode and ask for location before saving
        existing_session.pending_address_line = address_line
        existing_session.pending_address_city = city
        existing_session.pending_address_pincode = pincode
        existing_session.awaiting_address_add_pincode = False
        existing_session.awaiting_address_location = True

        return [
            OutgoingMessage(
                text=with_footer(
                    LOCATION_PROMPT
                ),
                reply_markup=build_location_choice_reply_markup(),
            )
        ]

    if existing_session.awaiting_address_location:
        # Handle location shared via Telegram's native location picker
        if message.location_lat is not None and message.location_lng is not None:
            existing_session.pending_address_lat = message.location_lat
            existing_session.pending_address_lng = message.location_lng
            lat_lng = (message.location_lat, message.location_lng)
            mobile_for_address = derive_mobile_from_message(message, existing_mobile)
            address_line = existing_session.pending_address_line
            city = existing_session.pending_address_city
            pincode = existing_session.pending_address_pincode or ""
            clear_address_update_flow(existing_session)

            if not mobile_for_address or not address_line or not city:
                return [
                    OutgoingMessage(
                        text=with_footer("We could not save your address because some details are missing. Please try again from Update Address."),
                        reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                    )
                ]

            add_result = await add_client_address(
                mobile_for_address, address_line, city, pincode,
                lat=lat_lng[0], lng=lat_lng[1],
            )
        else:
            normalized_text = (message.text or "").strip().casefold()
            if (message.metadata or {}).get("location_coordinates_unavailable"):
                return [
                    OutgoingMessage(
                        text=with_footer(
                            "Your map was received. To save the exact pin, please:\n\n"
                            "1. Long-press the map you just shared and select Copy link\n"
                            "2. Paste the link in this chat\n\n"
                    
                            "Or choose *Skip location* to detect coordinates from the address you entered."
                        ),
                        reply_markup=build_location_link_fallback_reply_markup(),
                    )
                ]
            # Handle the standard location-choice labels and numeric fallback.
            button_text = normalized_text
            if "." in normalized_text:
                parts = normalized_text.split(".", 1)
                if parts[0].strip().isdigit():
                    button_text = parts[1].strip()
            
            if button_text in {"enter manually", "enter location manually"} or normalized_text in {"2", "2. enter manually"}:
                # User wants to enter coordinates manually
                existing_session.awaiting_address_location = False
                existing_session.awaiting_manual_coordinates = True
                return [
                    OutgoingMessage(
                        text=with_footer(
                            "📍 Please enter the latitude and longitude for your location.\n\n"
                            "Format: latitude,longitude\n"
                            "Example: 17.3850,78.4867\n\n"
                            "You can get coordinates from Google Maps or any map app."
                        ),
                        reply_markup=build_nav_keyboard(),
                    )
                ]
            elif button_text in {"skip", "skip location", "no"} or normalized_text in {"3", "3. skip"}:
                # User skipped location — try auto-geocoding
                mobile_for_address = derive_mobile_from_message(message, existing_mobile)
                address_line = existing_session.pending_address_line
                city = existing_session.pending_address_city
                pincode = existing_session.pending_address_pincode or ""
                lat_lng = None

                if mobile_for_address and address_line and city and pincode:
                    lat_lng = await geocode_address(address_line, city, pincode)

                clear_address_update_flow(existing_session)

                if not mobile_for_address or not address_line or not city:
                    return [
                        OutgoingMessage(
                            text=with_footer("We could not save your address because some details are missing. Please try again from Update Address."),
                            reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                        )
                    ]

                add_result = await add_client_address(
                    mobile_for_address, address_line, city, pincode,
                    lat=lat_lng[0] if lat_lng else None,
                    lng=lat_lng[1] if lat_lng else None,
                )

                if lat_lng:
                    add_result_message_ext = f"\n📍 Location detected automatically."
                else:
                    add_result_message_ext = ""
                add_result = AddressUpsertResult(
                    success=add_result.success,
                    message=add_result.message + add_result_message_ext,
                    address_id=add_result.address_id,
                )
            elif button_text in {"share location", "share current location", "location"}:
                return [
                    OutgoingMessage(
                        text=with_footer(
                            "To share your current location, tap WhatsApp's attachment (+) icon, "
                            "then choose *Location* and send it here."
                        ),
                        reply_markup=build_location_choice_reply_markup(),
                    )
                ]
            else:
                return [
                    OutgoingMessage(
                        text=with_footer("Please share a WhatsApp location, or choose an option below."),
                        reply_markup=build_location_choice_reply_markup(),
                    )
                ]

        # If this address was added as part of the pickup flow, continue the pickup
        if add_result.success and existing_session.pickup_mode is not None:
            recheck = await fetch_client_addresses(mobile_for_address)
            pickup_date = existing_session.pending_pickup_date
            pickup_time = existing_session.pending_pickup_time

            # Date/time already collected → schedule now using the saved address
            if recheck.success and len(recheck.addresses) >= 1 and pickup_date and pickup_time is not None:
                chosen_address_id = recheck.addresses[0].address_id
                retry_schedule_func = schedule_another_pickup if existing_session.pickup_mode == "another" else schedule_fresh_pickup
                retry_result = await retry_schedule_func(
                    mobile_for_address, pickup_date, pickup_time, address_id=chosen_address_id,
                )
                clear_pickup_flow(existing_session)
                if retry_result.success:
                    await save_client_profile(
                        message.user_id,
                        mobile_for_address,
                        "active_client",
                        existing_customer_salutation,
                    )
                    _, _, updated_salutation = await get_client_profile(message.user_id)
                    return [
                        OutgoingMessage(text=add_result.message),
                        OutgoingMessage(text=retry_result.message),
                        await build_main_menu_response(message.user_id, "active_client", updated_salutation),
                    ]
                if retry_result.needs_address:
                    # Still no usable address — ask to add again
                    existing_session.address_needed_for_pickup = True
                    existing_session.awaiting_address_action = True
                    return [
                        OutgoingMessage(text=add_result.message),
                        OutgoingMessage(
                            text=with_footer("We still need a usable pickup address. Please choose Add address below."),
                            reply_markup=build_selection_reply_markup(["Add address"]),
                        ),
                    ]
                return [
                    OutgoingMessage(text=add_result.message),
                    OutgoingMessage(text=retry_result.message),
                ]

            # Date/time not yet collected — continue the flow
            if recheck.success and len(recheck.addresses) == 1:
                existing_session.address_needed_for_pickup = False
                existing_session.pending_pickup_address_id = recheck.addresses[0].address_id
                existing_session.awaiting_pickup_date = True
                pickup_intro = "Address added. Select a pickup date:"
                if existing_session.pickup_mode == "alteration":
                    pickup_intro = "Address added. Select a date for the alteration pickup:"
                return [
                    OutgoingMessage(text=add_result.message),
                    OutgoingMessage(
                        text=with_footer(
                            f"{pickup_intro}\n"
                            "1. Today's date\n"
                            "2. Tomorrow's date\n"
                            "3. Day after tomorrow"
                        ),
                        reply_markup=build_pickup_date_reply_markup(),
                    ),
                ]

            if recheck.success and len(recheck.addresses) > 1:
                existing_session.address_needed_for_pickup = False
                existing_session.awaiting_pickup_address = True
                existing_session.pending_address_ordered_ids = [a.address_id for a in recheck.addresses]

                address_lines = []
                if recheck.customer_name:
                    address_lines.append(f"Saved addresses for {recheck.customer_name}:")
                else:
                    address_lines.append("Saved addresses:")
                address_lines.append("")
                for index, address in enumerate(recheck.addresses, start=1):
                    main_tag = " (Main)" if address.is_main else ""
                    location_parts = [part for part in [address.address1, address.city, address.pincode] if part]
                    address_lines.append(f"{index}. {', '.join(location_parts)}{main_tag}")
                address_lines.extend([
                    "",
                    "Select an address below, or choose Add address.",
                ])
                return [
                    OutgoingMessage(text=add_result.message),
                    OutgoingMessage(
                        text=with_footer("\n".join(address_lines)),
                        reply_markup=build_number_selection_reply_markup(
                            len(recheck.addresses), include_add_address=True
                        ),
                    ),
                ]

            # Still no address saved
            existing_session.address_needed_for_pickup = True
            existing_session.awaiting_address_action = True
            return [
                OutgoingMessage(text=add_result.message),
                OutgoingMessage(
                    text=with_footer("We could not save that address. Please choose Add address to try again."),
                    reply_markup=build_selection_reply_markup(["Add address"]),
                ),
            ]

        return [
            OutgoingMessage(
                text=with_footer(add_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "client"),
            )
        ]

    if existing_session.awaiting_manual_coordinates:
        # Handle manual coordinate entry
        normalized_text = (message.text or "").strip()
        if normalized_text == "← Back to main menu":
            clear_address_update_flow(existing_session)
            return [await build_main_menu_response(message.user_id, existing_client_type or "client", existing_customer_salutation)]

        # Parse coordinates in format: latitude,longitude
        try:
            parts = normalized_text.split(",")
            if len(parts) != 2:
                raise ValueError("Invalid format")

            lat = float(parts[0].strip())
            lng = float(parts[1].strip())

            # Basic validation
            if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
                raise ValueError("Coordinates out of range")
        except (ValueError, AttributeError):
            return [
                OutgoingMessage(
                    text=with_footer("Invalid format. Please enter coordinates as: latitude,longitude\nExample: 17.3850,78.4867"),
                    reply_markup=build_nav_keyboard(),
                )
            ]

        # Valid coordinates received - save the address
        mobile_for_address = derive_mobile_from_message(message, existing_mobile)
        address_line = existing_session.pending_address_line
        city = existing_session.pending_address_city
        pincode = existing_session.pending_address_pincode or ""

        clear_address_update_flow(existing_session)

        if not mobile_for_address or not address_line or not city:
            return [
                OutgoingMessage(
                    text=with_footer("We could not save your address because some details are missing. Please try again from Update Address."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "client"),
                )
            ]

        add_result = await add_client_address(
            mobile_for_address, address_line, city, pincode,
            lat=lat, lng=lng,
        )

        # If this address was added as part of the pickup flow, continue the pickup
        if add_result.success and existing_session.pickup_mode is not None:
            recheck = await fetch_client_addresses(mobile_for_address)
            pickup_date = existing_session.pending_pickup_date
            pickup_time = existing_session.pending_pickup_time

            # Date/time already collected → schedule now using the saved address
            if recheck.success and len(recheck.addresses) >= 1 and pickup_date and pickup_time is not None:
                chosen_address_id = recheck.addresses[0].address_id
                retry_schedule_func = schedule_another_pickup if existing_session.pickup_mode == "another" else schedule_fresh_pickup
                retry_result = await retry_schedule_func(
                    mobile_for_address, pickup_date, pickup_time, address_id=chosen_address_id,
                )
                clear_pickup_flow(existing_session)
                if retry_result.success:
                    await save_client_profile(
                        message.user_id,
                        mobile_for_address,
                        "active_client",
                        existing_customer_salutation,
                    )
                    _, _, updated_salutation = await get_client_profile(message.user_id)
                    return [
                        OutgoingMessage(text=add_result.message),
                        OutgoingMessage(text=retry_result.message),
                        await build_main_menu_response(message.user_id, "active_client", updated_salutation),
                    ]
                if retry_result.needs_address:
                    # Still no usable address — ask to add again
                    existing_session.address_needed_for_pickup = True
                    existing_session.awaiting_address_action = True
                    return [
                        OutgoingMessage(text=add_result.message),
                        OutgoingMessage(
                            text=with_footer("We still need a usable pickup address. Please choose Add address below."),
                            reply_markup=build_selection_reply_markup(["Add address"]),
                        ),
                    ]
                return [
                    OutgoingMessage(text=add_result.message),
                    OutgoingMessage(text=retry_result.message),
                ]

            # Date/time not yet collected — continue the flow
            if recheck.success and len(recheck.addresses) == 1:
                existing_session.address_needed_for_pickup = False
                existing_session.pending_pickup_address_id = recheck.addresses[0].address_id
                existing_session.awaiting_pickup_date = True
                pickup_intro = "Address added. Select a pickup date:"
                if existing_session.pickup_mode == "alteration":
                    pickup_intro = "Address added. Select a date for the alteration pickup:"
                return [
                    OutgoingMessage(text=add_result.message),
                    OutgoingMessage(
                        text=with_footer(
                            f"{pickup_intro}\n"
                            "1. Today's date\n"
                            "2. Tomorrow's date\n"
                            "3. Day after tomorrow"
                        ),
                        reply_markup=build_pickup_date_reply_markup(),
                    ),
                ]

            if recheck.success and len(recheck.addresses) > 1:
                existing_session.address_needed_for_pickup = False
                existing_session.awaiting_pickup_address = True
                existing_session.pending_address_ordered_ids = [a.address_id for a in recheck.addresses]

                address_lines = []
                if recheck.customer_name:
                    address_lines.append(f"Saved addresses for {recheck.customer_name}:")
                else:
                    address_lines.append("Saved addresses:")
                address_lines.append("")
                for index, address in enumerate(recheck.addresses, start=1):
                    main_tag = " (Main)" if address.is_main else ""
                    location_parts = [part for part in [address.address1, address.city, address.pincode] if part]
                    address_lines.append(f"{index}. {', '.join(location_parts)}{main_tag}")
                address_lines.extend([
                    "",
                    "Select an address below, or choose Add address.",
                ])
                return [
                    OutgoingMessage(text=add_result.message),
                    OutgoingMessage(
                        text=with_footer("\n".join(address_lines)),
                        reply_markup=build_number_selection_reply_markup(
                            len(recheck.addresses), include_add_address=True
                        ),
                    ),
                ]

            # Still no address saved
            existing_session.address_needed_for_pickup = True
            existing_session.awaiting_address_action = True
            return [
                OutgoingMessage(text=add_result.message),
                OutgoingMessage(
                    text=with_footer("We could not save that address. Please choose Add address to try again."),
                    reply_markup=build_selection_reply_markup(["Add address"]),
                ),
            ]

        return [
            OutgoingMessage(
                text=with_footer(add_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "client"),
            )
        ]

    if existing_session.awaiting_order_change_select:
        choice_text = normalize_mobile(message.text or "")
        if not choice_text:
            return [
                OutgoingMessage(
                    text=with_footer("Please select an order below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_change_order_ids)
                    ),
                )
            ]

        choice_index = int(choice_text) - 1
        if not (0 <= choice_index < len(existing_session.pending_change_order_ids)):
            return [
                OutgoingMessage(
                    text=with_footer("That order is not available. Please select an order below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_change_order_ids)
                    ),
                )
            ]

        existing_session.pending_change_order_id = existing_session.pending_change_order_ids[choice_index]
        existing_session.awaiting_order_change_select = False
        existing_session.awaiting_order_change_details = True

        return [
            OutgoingMessage(
                text=with_footer(f"Order #{existing_session.pending_change_order_id} selected.\n\nPlease describe the changes you would like to make."),
                reply_markup=build_nav_keyboard(),
            )
        ]

    if existing_session.awaiting_order_change_details:
        details = (message.text or "").strip()
        if len(details) < 5:
            return [OutgoingMessage(text=with_footer("Please provide a little more detail about the changes you need."), reply_markup=build_nav_keyboard())]

        mobile_for_change = derive_mobile_from_message(message, existing_mobile)
        order_id = existing_session.pending_change_order_id
        clear_order_change_flow(existing_session)

        if not mobile_for_change:
            return [
                OutgoingMessage(
                    text=with_footer("I could not identify your mobile number for order modification. Please share contact or send your mobile number."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
                )
            ]

        modify_result = await modify_order(
            mobile=mobile_for_change,
            comment=details,
            order_id=order_id,
            chatby="AI Assistant",
        )
        return [
            OutgoingMessage(
                text=with_footer(modify_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
            )
        ]

    if existing_session.awaiting_order_cancel_select:
        choice_text = normalize_mobile(message.text or "")
        if not choice_text:
            return [
                OutgoingMessage(
                    text=with_footer("Please select an order below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_cancel_order_ids)
                    ),
                )
            ]

        choice_index = int(choice_text) - 1
        if not (0 <= choice_index < len(existing_session.pending_cancel_order_ids)):
            return [
                OutgoingMessage(
                    text=with_footer("That order is not available. Please select an order below."),
                    reply_markup=build_number_selection_reply_markup(
                        len(existing_session.pending_cancel_order_ids)
                    ),
                )
            ]

        existing_session.pending_cancel_order_id = existing_session.pending_cancel_order_ids[choice_index]
        existing_session.awaiting_order_cancel_select = False
        existing_session.awaiting_order_cancel_reason = True
        return [
            OutgoingMessage(
                text=with_footer(f"Please tell us why you would like to cancel Order #{existing_session.pending_cancel_order_id}."),
                reply_markup=build_nav_keyboard(),
            )
        ]

    if existing_session.awaiting_order_cancel_reason:
        reason = (message.text or "").strip()
        if len(reason) < 3:
            return [OutgoingMessage(text=with_footer("Please provide a brief reason for cancellation."), reply_markup=build_nav_keyboard())]

        mobile_for_cancel = derive_mobile_from_message(message, existing_mobile)
        order_id_int = existing_session.pending_cancel_order_id
        clear_order_cancel_flow(existing_session)

        if not mobile_for_cancel:
            return [
                OutgoingMessage(
                    text=with_footer("I could not identify your mobile number for order cancellation. Please share contact or send your mobile number."),
                    reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
                )
            ]

        cancel_result = await cancel_current_order(
            mobile=mobile_for_cancel,
            order_id=order_id_int,
            reason=reason,
        )

        if cancel_result.success:
            # After cancellation, re-check if there are still active orders
            recheck_orders = await fetch_all_active_orders(mobile_for_cancel)
            if recheck_orders.success and recheck_orders.orders:
                # Still has active orders — stay in active_client menu
                await save_client_profile(
                    message.user_id,
                    mobile_for_cancel,
                    "active_client",
                    existing_customer_salutation,
                )
                _, _, updated_salutation = await get_client_profile(message.user_id)
                return [
                    OutgoingMessage(text=cancel_result.message),
                    await build_main_menu_response(message.user_id, "active_client", updated_salutation),
                ]
            else:
                # No more active orders — transition to client menu
                await save_client_profile(
                    message.user_id,
                    mobile_for_cancel,
                    "client",
                    existing_customer_salutation,
                )
                _, _, updated_salutation = await get_client_profile(message.user_id)
                return [
                    OutgoingMessage(text=cancel_result.message),
                    await build_main_menu_response(message.user_id, "client", updated_salutation),
                ]

        return [
            OutgoingMessage(
                text=with_footer(cancel_result.message),
                reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
            )
        ]

    mobile_from_text = extract_mobile_from_text(message.text) if message.text else None
    if mobile_from_text:
        mobile, client_type, customer_salutation = await resolve_client_type(message.text, message.contact_phone, auto_mobile)
        await save_client_profile(message.user_id, mobile, client_type, customer_salutation)
        return [await build_main_menu_response(message.user_id, client_type, customer_salutation)]

    if message.contact_phone:
        if message.contact_user_id not in (None, message.source_user_id):
            return [OutgoingMessage(text="Please share your own mobile number using the button.")]

        mobile = normalize_mobile(message.contact_phone)
        if not mobile:
            return [OutgoingMessage(text="I could not read that mobile number. Please try again.")]

        mobile, client_type, customer_salutation = await resolve_client_type(message.text, message.contact_phone, auto_mobile)
        await save_client_profile(message.user_id, mobile, client_type, customer_salutation)
        logger.info(
            "onboarding_contact_captured user_id=%s mobile_present=%s client_type=%s",
            message.user_id,
            bool(mobile),
            client_type,
        )
        return [await build_main_menu_response(message.user_id, client_type, customer_salutation)]

    mobile, client_type, customer_salutation = existing_mobile, existing_client_type, existing_customer_salutation
    if not mobile or not client_type:
        mobile, client_type, customer_salutation = await resolve_client_type(message.text, message.contact_phone, auto_mobile)
        await save_client_profile(message.user_id, mobile, client_type, customer_salutation)

    if message.text in {"menu", "main menu", "10", "0"}:
        if mobile:
            _, client_type, customer_salutation = await resolve_client_type(message.text, message.contact_phone, mobile)
            await save_client_profile(message.user_id, mobile, client_type, customer_salutation)
        return [await build_main_menu_response(message.user_id, client_type, customer_salutation)]

    if message.text:
        selected_intent = get_intent(client_type, message.text)
        logger.info(
            "intent_resolved user_id=%s client_type=%s intent=%s text=%r",
            message.user_id,
            client_type,
            selected_intent,
            (message.text or "")[:80],
        )
        if selected_intent is None:
            return [
                OutgoingMessage(text="Please choose one of the listed menu options."),
                await build_main_menu_response(message.user_id, client_type, customer_salutation),
            ]

        if client_type == "new_user" and selected_intent in {"visit", "book_visit", "handover"}:
            mobile_for_registration = derive_mobile_from_message(message, mobile)
            if mobile_for_registration:
                await save_client_profile(message.user_id, mobile_for_registration, client_type, customer_salutation)

            existing_session.awaiting_registration_name = True
            return [
                OutgoingMessage(
                    text=with_footer("Please complete registration first. Enter your full name to continue."),
                    reply_markup=build_nav_keyboard(),
                )
            ]

        if selected_intent == "register":
            mobile_for_registration = derive_mobile_from_message(message, mobile)
            if mobile_for_registration:
                await save_client_profile(message.user_id, mobile_for_registration, client_type, customer_salutation)

            existing_session.awaiting_registration_name = True
            return [OutgoingMessage(text=with_footer("Please enter your full name for registration."), reply_markup=build_nav_keyboard())]

        if selected_intent == "new_order":
            clear_pickup_flow(existing_session)
            # active_client's "Start a new pickup/order request" uses the
            # scheduleanotherpickup.php endpoint (mode "another"), mirroring the
            # fresh pickup flow (address -> date -> time) used by client option 1.
            existing_session.pickup_mode = "another" if client_type == "active_client" else "fresh"

            # Fetch addresses FIRST before asking for date/time
            mobile_for_pickup = derive_mobile_from_message(message, mobile)
            if not mobile_for_pickup:
                return [
                    OutgoingMessage(
                        text=with_footer("I could not identify your mobile number for pickup scheduling. Please share contact or send your mobile number."),
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            address_result = await fetch_client_addresses(mobile_for_pickup)

            # Case 1: No saved addresses - redirect to add address
            if not address_result.success or not address_result.addresses:
                existing_session.address_needed_for_pickup = True
                clear_address_update_flow(existing_session)
                existing_session.awaiting_address_action = True
                return [
                    OutgoingMessage(
                        text=(
                            "No saved addresses found. Please add a delivery/pickup address first so we can schedule the pickup.\n\n"
                            "Please reply 1 to add a new address."
                        ),
                    ),
                    OutgoingMessage(
                        text=await build_address_list_message(mobile_for_pickup),
                    ),
                ]

            # One or more addresses - always show the list so the customer can
            # confirm or add/correct before the order is placed.
            existing_session.awaiting_pickup_address = True
            existing_session.pending_address_ordered_ids = [a.address_id for a in address_result.addresses]

            address_lines: list[str] = []
            if address_result.customer_name:
                address_lines.append(f"Saved addresses for {address_result.customer_name}:")
            else:
                address_lines.append("Saved addresses:")
            address_lines.append("")

            for index, address in enumerate(address_result.addresses, start=1):
                main_tag = " (Main)" if address.is_main else ""
                location_parts = [part for part in [address.address1, address.city, address.pincode] if part]
                address_lines.append(f"{index}. {', '.join(location_parts)}{main_tag}")

            address_lines.extend([
                "",
                "Select an address below, or choose Add address.",
            ])

            return [
                OutgoingMessage(
                    text=with_footer("\n".join(address_lines)),
                    reply_markup=build_number_selection_reply_markup(
                        len(address_result.addresses), include_add_address=True
                    ),
                )
            ]

        if selected_intent in {"book_visit", "visit"}:
            existing_session.awaiting_visit_date = True
            existing_session.awaiting_visit_time = False
            existing_session.pending_visit_date = None
            existing_session.pending_visit_slots = []
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Select a visit date:\n"
                        "1. Today's date\n"
                        "2. Tomorrow's date\n"
                        "3. Day after tomorrow"
                    ),
                    reply_markup=build_pickup_date_reply_markup(),
                )
            ]

        if selected_intent == "visit_history":
            mobile_for_history = derive_mobile_from_message(message, mobile)
            if not mobile_for_history:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for appointment history. Please share contact or send your mobile number.",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            return [
                OutgoingMessage(
                    text=await build_visit_history_response(mobile_for_history),
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        if selected_intent == "order_status":
            mobile_for_order_status = derive_mobile_from_message(message, mobile)
            if not mobile_for_order_status:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for order status. Please share contact or send your mobile number.",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            return [
                OutgoingMessage(
                    text=await build_order_status_response(mobile_for_order_status),
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        if selected_intent == "measurements":
            mobile_for_measurements = derive_mobile_from_message(message, mobile)
            if not mobile_for_measurements:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for saved measurements. Please share contact or send your mobile number.",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            return [
                OutgoingMessage(
                    text=await build_measurements_response(mobile_for_measurements),
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        if selected_intent == "fabric_estimate":
            mobile_for_fabric = derive_mobile_from_message(message, mobile)
            if not mobile_for_fabric:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for fabric estimate request. Please share contact or send your mobile number.",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            fabric_result = await raise_fabric_alert(mobile_for_fabric)
            handover_result = await request_human_handover(mobile_for_fabric)

            if fabric_result.success and handover_result.success:
                return [
                    OutgoingMessage(
                        text=f"{fabric_result.message}\n{handover_result.message}",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            return [
                OutgoingMessage(
                    text=f"{fabric_result.message}\n{handover_result.message}",
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        if selected_intent == "bulk_orders":
            mobile_for_bulk = derive_mobile_from_message(message, mobile)
            if not mobile_for_bulk:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for bulk order enquiry. Please share contact or send your mobile number.",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            bulk_result = await create_bulk_order_enquiry(mobile_for_bulk)
            handover_result = await request_human_handover(mobile_for_bulk)

            if bulk_result.success and handover_result.success:
                return [
                    OutgoingMessage(
                        text=f"{bulk_result.message}\n{handover_result.message}",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            return [
                OutgoingMessage(
                    text=f"{bulk_result.message}\n{handover_result.message}",
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        if selected_intent == "alteration_pickup_recent":
            clear_pickup_flow(existing_session)
            existing_session.pickup_mode = "alteration"

            mobile_for_alteration = derive_mobile_from_message(message, mobile)
            if not mobile_for_alteration:
                return [
                    OutgoingMessage(
                        text=with_footer("I could not identify your mobile number for this request. Please share contact or send your mobile number."),
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            delivered_result = await fetch_delivered_orders(mobile_for_alteration)
            if not delivered_result.success or not delivered_result.orders:
                return [
                    OutgoingMessage(
                        text=with_footer(delivered_result.message),
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            existing_session.awaiting_alteration_order = True
            existing_session.pending_alteration_order_ids = [order.order_id for order in delivered_result.orders]

            order_lines: list[str] = ["Here are your recently delivered orders (last 30 days):", ""]
            for index, order in enumerate(delivered_result.orders, start=1):
                line = f"{index}. Order #{order.order_id} — delivered {order.delivered_date}"
                if order.item_summary:
                    line += f" ({order.item_summary})"
                order_lines.append(line)

            order_lines.extend([
                "",
                "Select the order for alteration pickup below.",
            ])

            return [
                OutgoingMessage(
                    text=with_footer("\n".join(order_lines)),
                    reply_markup=build_number_selection_reply_markup(len(delivered_result.orders)),
                )
            ]

        if selected_intent == "handover":
            mobile_for_handover = derive_mobile_from_message(message, mobile)
            if not mobile_for_handover:
                return [
                    OutgoingMessage(
                        text="I could not identify your mobile number for human handover. Please share contact or send your mobile number.",
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            handover_result = await request_human_handover(mobile_for_handover)
            return [
                OutgoingMessage(
                    text=handover_result.message,
                    reply_markup=build_menu_reply_markup(client_type),
                )
            ]

        if selected_intent == "fabric_delivery":
            existing_session.awaiting_fabric_delivery_notes = True
            return [
                OutgoingMessage(
                    text=with_footer(
                        "Add a note about the fabric delivery (optional).\n"
                        "Example: 3 metres of cotton fabric for a shirt.\n"
                        "Reply *Skip* to continue without a note."
                    ),
                    reply_markup=build_nav_keyboard(),
                )
            ]

        if selected_intent == "order_changes":
            mobile_for_change = derive_mobile_from_message(message, mobile)
            if not mobile_for_change:
                return [
                    OutgoingMessage(
                        text=with_footer("I could not identify your mobile number for order change request. Please share contact or send your mobile number."),
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            clear_order_change_flow(existing_session)

            # Fetch active orders so the customer can choose which order to modify
            active_orders_result = await fetch_all_active_orders(mobile_for_change)
            if not active_orders_result.success or not active_orders_result.orders:
                return [
                    OutgoingMessage(
                        text=with_footer(
                            active_orders_result.message if not active_orders_result.success
                            else "You have no active orders to modify at this time."
                        ),
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            existing_session.awaiting_order_change_select = True
            existing_session.pending_change_order_ids = [int(order.order_id) for order in active_orders_result.orders]

            order_lines: list[str] = ["Here are your current active orders:", ""]
            for index, order in enumerate(active_orders_result.orders, start=1):
                order_lines.append(f"{index}. Order #{order.order_id} — {order.stage_label}")
                if order.order_date:
                    order_lines.append(f"   Ordered: {order.order_date}")
                if order.pickup_date:
                    pickup_line = f"   Pickup: {order.pickup_date}"
                    if order.pickup_time:
                        pickup_line += f" ({order.pickup_time})"
                    order_lines.append(pickup_line)
                order_lines.append("")

            order_lines.append("Select the order you want to modify below.")

            return [
                OutgoingMessage(
                    text=with_footer("\n".join(order_lines)),
                    reply_markup=build_number_selection_reply_markup(len(active_orders_result.orders)),
                )
            ]

        if selected_intent == "order_cancel":
            clear_order_cancel_flow(existing_session)
            mobile_for_cancel = derive_mobile_from_message(message, mobile)
            if not mobile_for_cancel:
                return [
                    OutgoingMessage(
                        text=with_footer("I could not identify your mobile number for order cancellation. Please share contact or send your mobile number."),
                        reply_markup=build_contact_keyboard(),
                    )
                ]

            # Fetch all active orders so the customer can choose which one to cancel
            orders_result = await fetch_all_active_orders(mobile_for_cancel)
            if not orders_result.success or not orders_result.orders:
                return [
                    OutgoingMessage(
                        text=with_footer(
                            orders_result.message if not orders_result.success
                            else "You have no active orders to cancel at this time."
                        ),
                        reply_markup=build_menu_reply_markup(existing_client_type or "active_client"),
                    )
                ]

            existing_session.awaiting_order_cancel_select = True
            existing_session.pending_cancel_order_ids = [int(order.order_id) for order in orders_result.orders]

            order_lines: list[str] = ["Here are your current active orders:", ""]
            for index, order in enumerate(orders_result.orders, start=1):
                order_lines.append(f"{index}. Order #{order.order_id} — {order.stage_label}")
                if order.order_date:
                    order_lines.append(f"   Ordered: {order.order_date}")
                if order.pickup_date:
                    pickup_line = f"   Pickup: {order.pickup_date}"
                    if order.pickup_time:
                        pickup_line += f" ({order.pickup_time})"
                    order_lines.append(pickup_line)
                order_lines.append("")

            order_lines.append("Select the order you want to cancel below.")

            return [
                OutgoingMessage(
                    text=with_footer("\n".join(order_lines)),
                    reply_markup=build_number_selection_reply_markup(len(orders_result.orders)),
                )
            ]

        if selected_intent == "address_update":
            mobile_for_address = derive_mobile_from_message(message, mobile)
            if not mobile_for_address:
                return [
                    OutgoingMessage(
                        text=with_footer("I could not identify your mobile number for address update. Please share contact or send your mobile number."),
                        reply_markup=build_menu_reply_markup(client_type),
                    )
                ]

            # Store address IDs in order for numbered reference in delete/update flows
            address_result = await fetch_client_addresses(mobile_for_address)

            clear_address_update_flow(existing_session)
            existing_session.pending_address_list_ids = (
                [a.address_id for a in address_result.addresses] if address_result.success else []
            )
            existing_session.awaiting_address_action = True
            return [
            OutgoingMessage(
                text=await build_address_list_message(mobile_for_address),
                reply_markup=build_selection_reply_markup(["Add address", "Delete address"]),
            )
            ]

        intent_response = await build_intent_response(selected_intent, client_type)
        if intent_response is not None:
            return intent_response

        return [OutgoingMessage(text=f"Selected option: {selected_intent}")]

    return []


async def handle_incoming_message(message: IncomingMessage) -> list[OutgoingMessage]:
    """Handle one inbound message and persist every mutated session state.

    All channels call this shared function. The `finally` block protects flow
    state across the many early returns above, including address, pickup,
    order, registration, and navigation paths.
    """
    token = begin_session_scope()
    try:
        return await _handle_incoming_message(message)
    finally:
        try:
            await save_active_sessions()
        finally:
            end_session_scope(token)
