from dataclasses import dataclass
import logging

from services.http_client import http_get

BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/getclient"
logger = logging.getLogger(__name__)


@dataclass
class CustomerProfile:
    client_type: str
    customer_salutation: str | None = None


def _mask_mobile(mobile: str) -> str:
    digits_only = "".join(character for character in mobile if character.isdigit())
    if len(digits_only) <= 4:
        return digits_only
    return f"***{digits_only[-4:]}"


def _first_non_empty(data: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_customer_salutation(data: dict) -> str | None:
    salutation = _first_non_empty(data, ("customer_salutation", "salutation", "salute", "title", "prefix"))
    full_name = _first_non_empty(data, ("customer_name", "full_name", "display_name", "name", "cname"))

    if full_name is None:
        first_name = _first_non_empty(data, ("first_name", "firstname", "firstName"))
        last_name = _first_non_empty(data, ("last_name", "lastname", "lastName"))
        if first_name or last_name:
            full_name = " ".join(part for part in (first_name, last_name) if part)

    if salutation and full_name:
        return f"{salutation} {full_name}".strip()

    return full_name or salutation


async def lookup_customer_profile(mobile: str) -> CustomerProfile:

    try:

        response = await http_get(
            BASE_URL,
            params={"mobile": mobile},
        )

        logger.info(
            "lookup_customer_profile request mobile=%s status_code=%s",
            _mask_mobile(mobile),
            response.status_code,
        )

        if response.status_code != 200:
            logger.warning(
                "lookup_customer_profile fallback new_user due to non-200 response mobile=%s",
                _mask_mobile(mobile),
            )
            return CustomerProfile(client_type="new_user")

        data = response.json()

        # API contract observed:
        # - type: active_client|client|new_user
        # - client: {cname, salute, ...}
        client_type = data.get("type") or data.get("client_type") or "new_user"

        client_payload = data.get("client") if isinstance(data.get("client"), dict) else {}
        salutation_from_client = _build_customer_salutation(client_payload)

        # CRM returns type=new_user even for newly registered clients without orders.
        # Treat records that have a real client object as client for menu routing.
        if client_type == "new_user" and client_payload:
            client_type = "client"

        return CustomerProfile(
            client_type=client_type,
            customer_salutation=salutation_from_client or _build_customer_salutation(data),
        )

    except Exception as e:
        logger.exception(
            "lookup_customer_profile exception for mobile=%s error=%s",
            _mask_mobile(mobile),
            e,
        )
        return CustomerProfile(client_type="new_user")


async def get_client_type(mobile: str):
    return (await lookup_customer_profile(mobile)).client_type