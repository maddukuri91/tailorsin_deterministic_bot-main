from dataclasses import dataclass

from services.http_client import http_get, http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/clientaddress.php"


@dataclass
class AddressRecord:
    address_id: int
    address1: str
    city: str
    pincode: str
    is_main: bool
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class AddressListResult:
    success: bool
    message: str
    customer_name: str | None
    addresses: list[AddressRecord]


@dataclass
class AddressUpsertResult:
    success: bool
    message: str
    address_id: int | None = None


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _to_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "main"}


def _to_float(value: object) -> float | None:
    try:
        v = float(str(value))
        return v
    except (TypeError, ValueError):
        return None


async def fetch_client_addresses(mobile: str) -> AddressListResult:
    try:
        response = await http_get(BASE_URL, params={"mobile": mobile})
        payload = response.json() if response.content else {}
    except Exception:
        return AddressListResult(
            success=False,
            message="Unable to fetch saved addresses right now. Please try again shortly.",
            customer_name=None,
            addresses=[],
        )

    if str(payload.get("status", "")).lower() != "success":
        return AddressListResult(
            success=False,
            message=str(payload.get("message") or "Unable to fetch addresses."),
            customer_name=None,
            addresses=[],
        )

    client_payload = payload.get("client") if isinstance(payload.get("client"), dict) else {}
    customer_name = _clean(client_payload.get("cname")) or None

    addresses_payload = payload.get("addresses") if isinstance(payload.get("addresses"), list) else []
    addresses: list[AddressRecord] = []

    for item in addresses_payload:
        if not isinstance(item, dict):
            continue

        address_id = _to_int(item.get("id") or item.get("address_id"))
        if address_id is None:
            continue

        address1 = _clean(item.get("address1") or item.get("address") or item.get("line1"))
        city = _clean(item.get("city"))
        pincode = _clean(item.get("pincode"))
        is_main = _to_bool(item.get("is_main") or item.get("main") or item.get("set_main"))
        latitude = _to_float(item.get("lat") or item.get("latitude"))
        longitude = _to_float(item.get("lng") or item.get("longitude") or item.get("lon"))

        addresses.append(
            AddressRecord(
                address_id=address_id,
                address1=address1,
                city=city,
                pincode=pincode,
                is_main=is_main,
                latitude=latitude,
                longitude=longitude,
            )
        )

    return AddressListResult(
        success=True,
        message="Address list fetched successfully.",
        customer_name=customer_name,
        addresses=addresses,
    )


async def add_client_address(
    mobile: str,
    address1: str,
    city: str,
    pincode: str,
    lat: float | None = None,
    lng: float | None = None,
) -> AddressUpsertResult:
    payload: dict[str, object] = {
        "mobile": mobile,
        "action": "add",
        "address1": address1.strip(),
        "city": city.strip(),
        "pincode": pincode.strip(),
    }

    if lat is not None:
        payload["lat"] = lat
    if lng is not None:
        payload["lng"] = lng

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return AddressUpsertResult(
            success=False,
            message="Unable to add address right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        return AddressUpsertResult(
            success=True,
            message=str(data.get("message") or "address added successfully"),
            address_id=_to_int(data.get("address_id")),
        )

    return AddressUpsertResult(
        success=False,
        message=str(data.get("message") or "Unable to add address."),
    )


DELETE_BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/deleteaddress.php"


async def delete_client_address(mobile: str, address_id: int) -> AddressUpsertResult:
    payload = {
        "mobile": mobile,
        "action": "delete",
        "address_id": address_id,
    }

    try:
        response = await http_post(DELETE_BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return AddressUpsertResult(
            success=False,
            message="Unable to delete address right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        return AddressUpsertResult(
            success=True,
            message=str(data.get("message") or "address deleted successfully"),
        )

    return AddressUpsertResult(
        success=False,
        message=str(data.get("message") or "Unable to delete address."),
    )


async def update_client_address(
    mobile: str,
    address_id: int,
    address1: str | None = None,
    set_main: bool | None = None,
) -> AddressUpsertResult:
    payload: dict[str, object] = {
        "mobile": mobile,
        "action": "update",
        "address_id": address_id,
    }

    cleaned_address = (address1 or "").strip()
    if cleaned_address:
        payload["address1"] = cleaned_address

    if set_main is True:
        payload["set_main"] = 1

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return AddressUpsertResult(
            success=False,
            message="Unable to update address right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        return AddressUpsertResult(
            success=True,
            message=str(data.get("message") or "address updated successfully"),
            address_id=_to_int(data.get("address_id")) or address_id,
        )

    return AddressUpsertResult(
        success=False,
        message=str(data.get("message") or "Unable to update address."),
    )