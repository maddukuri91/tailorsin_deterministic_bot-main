from dataclasses import dataclass, field

from services.http_client import http_get


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/orderstatus.php"


@dataclass
class CurrentOrderDetails:
	order_id: str
	stage: int
	stage_label: str
	order_date: str
	last_update_date: str
	pickup_date: str | None
	pickup_time: str | None


@dataclass
class AlterationDetails:
	log_id: int
	order_id: int
	order_stage: int
	order_stage_label: str
	order_closed: bool
	pickup_date: str | None
	pickup_time: str | None
	pickup_time_label: str | None
	status: int
	status_label: str
	last_update: str


@dataclass
class AllActiveOrdersResult:
	success: bool
	message: str
	customer_name: str | None
	orders: list[CurrentOrderDetails] = field(default_factory=list)


@dataclass
class AllOrdersResult:
	success: bool
	message: str
	customer_name: str | None
	orders: list[CurrentOrderDetails] = field(default_factory=list)
	alterations: list[AlterationDetails] = field(default_factory=list)


@dataclass
class CurrentOrderStatusResult:
	success: bool
	message: str
	customer_name: str | None
	order: CurrentOrderDetails | None


def _clean(value: object) -> str:
	if not isinstance(value, str):
		return ""
	return " ".join(value.strip().split())


def _pickup_time_label(value: str | None) -> str | None:
	if value == "1":
		return "9 AM - 2 PM"
	if value == "2":
		return "2 PM - 9 PM"
	if value:
		return value
	return None


def _to_int(value: object) -> int:
	"""Convert a value to int, handling both int and string types from API."""
	if isinstance(value, int):
		return value
	cleaned = _clean(value)
	return int(cleaned) if cleaned else 0


async def fetch_current_order_status(mobile: str) -> CurrentOrderStatusResult:
	try:
		response = await http_get(BASE_URL, params={"mobile": mobile})
		payload = response.json() if response.content else {}
	except Exception:
		return CurrentOrderStatusResult(
			success=False,
			message="Unable to fetch your current order status right now. Please try again shortly.",
			customer_name=None,
			order=None,
		)

	if str(payload.get("status", "")).lower() != "success":
		return CurrentOrderStatusResult(
			success=False,
			message=str(payload.get("message") or "Unable to fetch current order status."),
			customer_name=None,
			order=None,
		)

	client_payload = payload.get("client") if isinstance(payload.get("client"), dict) else {}
	customer_name = _clean(client_payload.get("cname")) or None

	orders = payload.get("orders") if isinstance(payload.get("orders"), list) else []
	# Find the first non-cancelled order (stage != 8 means not cancelled)
	current_order_payload = None
	for order_item in orders:
		if isinstance(order_item, dict) and str(order_item.get("stage", "")).strip() != "8":
			current_order_payload = order_item
			break

	if current_order_payload is None:
		return CurrentOrderStatusResult(
			success=True,
			message=str(payload.get("message") or "No current open order found."),
			customer_name=customer_name,
			order=None,
		)

	pickup_time_raw = _clean(current_order_payload.get("pickup_time"))
	order = CurrentOrderDetails(
		order_id=str(current_order_payload.get("order_id") or ""),
		stage=_to_int(current_order_payload.get("stage")),
		stage_label=_clean(current_order_payload.get("stage_label")) or "Unknown",
		order_date=_clean(current_order_payload.get("order_date")),
		last_update_date=_clean(current_order_payload.get("last_update_date")),
		pickup_date=_clean(current_order_payload.get("pickup_date")) or None,
		pickup_time=_pickup_time_label(pickup_time_raw),
	)

	return CurrentOrderStatusResult(
		success=True,
		message="Current order status fetched successfully.",
		customer_name=customer_name,
		order=order,
	)


async def fetch_all_active_orders(mobile: str) -> AllActiveOrdersResult:
	"""Fetch all non-cancelled orders for a customer."""
	try:
		response = await http_get(BASE_URL, params={"mobile": mobile})
		payload = response.json() if response.content else {}
	except Exception:
		return AllActiveOrdersResult(
			success=False,
			message="Unable to fetch your orders right now. Please try again shortly.",
			customer_name=None,
		)

	if str(payload.get("status", "")).lower() != "success":
		return AllActiveOrdersResult(
			success=False,
			message=str(payload.get("message") or "Unable to fetch orders."),
			customer_name=None,
		)

	client_payload = payload.get("client") if isinstance(payload.get("client"), dict) else {}
	customer_name = _clean(client_payload.get("cname")) or None

	orders = payload.get("orders") if isinstance(payload.get("orders"), list) else []
	active_orders: list[CurrentOrderDetails] = []

	for order_item in orders:
		if isinstance(order_item, dict) and str(order_item.get("stage", "")).strip() != "8":
			pickup_time_raw = _clean(order_item.get("pickup_time"))
			order = CurrentOrderDetails(
				order_id=str(order_item.get("order_id") or ""),
				stage=_to_int(order_item.get("stage")),
				stage_label=_clean(order_item.get("stage_label")) or "Unknown",
				order_date=_clean(order_item.get("order_date")),
				last_update_date=_clean(order_item.get("last_update_date")),
				pickup_date=_clean(order_item.get("pickup_date")) or None,
				pickup_time=_pickup_time_label(pickup_time_raw),
			)
			active_orders.append(order)

	if not active_orders:
		return AllActiveOrdersResult(
			success=True,
			message="No current open orders found.",
			customer_name=customer_name,
		)

	return AllActiveOrdersResult(
		success=True,
		message="Active orders fetched successfully.",
		customer_name=customer_name,
		orders=active_orders,
	)


async def fetch_all_orders(mobile: str) -> AllOrdersResult:
	"""Fetch ALL orders for a customer, including cancelled ones."""
	try:
		response = await http_get(BASE_URL, params={"mobile": mobile})
		payload = response.json() if response.content else {}
	except Exception:
		return AllOrdersResult(
			success=False,
			message="Unable to fetch your orders right now. Please try again shortly.",
			customer_name=None,
		)

	if str(payload.get("status", "")).lower() != "success":
		return AllOrdersResult(
			success=False,
			message=str(payload.get("message") or "Unable to fetch orders."),
			customer_name=None,
		)

	client_payload = payload.get("client") if isinstance(payload.get("client"), dict) else {}
	customer_name = _clean(client_payload.get("cname")) or None

	raw_orders = payload.get("orders") if isinstance(payload.get("orders"), list) else []
	all_orders: list[CurrentOrderDetails] = []

	for order_item in raw_orders:
		if isinstance(order_item, dict):
			pickup_time_raw = _clean(order_item.get("pickup_time"))
			order = CurrentOrderDetails(
				order_id=str(order_item.get("order_id") or ""),
				stage=_to_int(order_item.get("stage")),
				stage_label=_clean(order_item.get("stage_label")) or "Unknown",
				order_date=_clean(order_item.get("order_date")),
				last_update_date=_clean(order_item.get("last_update_date")),
				pickup_date=_clean(order_item.get("pickup_date")) or None,
				pickup_time=_pickup_time_label(pickup_time_raw),
			)
			all_orders.append(order)

	# Parse alterations
	raw_alterations = payload.get("alterations") if isinstance(payload.get("alterations"), list) else []
	all_alterations: list[AlterationDetails] = []

	for alt_item in raw_alterations:
		if isinstance(alt_item, dict):
			alteration = AlterationDetails(
				log_id=_to_int(alt_item.get("log_id")),
				order_id=_to_int(alt_item.get("order_id")),
				order_stage=_to_int(alt_item.get("order_stage")),
				order_stage_label=_clean(alt_item.get("order_stage_label")) or "Unknown",
				order_closed=str(alt_item.get("order_closed", "")).lower() in {"true", "1", "yes"},
				pickup_date=_clean(alt_item.get("pickup_date")) or None,
				pickup_time=_clean(alt_item.get("pickup_time")) or None,
				pickup_time_label=_clean(alt_item.get("pickup_time_label")) or None,
				status=_to_int(alt_item.get("status")),
				status_label=_clean(alt_item.get("status_label")) or "Unknown",
				last_update=_clean(alt_item.get("last_update")),
			)
			all_alterations.append(alteration)

	if not all_orders and not all_alterations:
		return AllOrdersResult(
			success=True,
			message="No orders found.",
			customer_name=customer_name,
		)

	return AllOrdersResult(
		success=True,
		message="Orders fetched successfully.",
		customer_name=customer_name,
		orders=all_orders,
		alterations=all_alterations,
	)
