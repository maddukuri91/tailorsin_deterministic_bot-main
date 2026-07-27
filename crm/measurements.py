from dataclasses import dataclass

from services.http_client import http_get


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/clientmeasurements.php"


@dataclass
class MeasurementRecord:
	form_type: str
	values: dict[str, str]


@dataclass
class ClientMeasurementsResult:
	success: bool
	message: str
	customer_name: str | None
	records: list[MeasurementRecord]


def _clean(value: object) -> str:
	if not isinstance(value, str):
		return ""
	return " ".join(value.strip().split())


def _is_hidden_field(key: str) -> bool:
	lowered = key.strip().lower()
	return lowered in {
		"id",
		"customer_id",
		"client_id",
		"mobile",
		"whatsapp",
		"tel",
		"create_at",
		"created_at",
		"update_at",
		"updated_at",
		"status",
	}


def _extract_form_records(payload: dict, form_key: str, form_type: str) -> list[MeasurementRecord]:
	form_payload = payload.get(form_key) if isinstance(payload.get(form_key), dict) else {}
	data = form_payload.get("data") if isinstance(form_payload.get("data"), list) else []

	records: list[MeasurementRecord] = []
	for item in data:
		if not isinstance(item, dict):
			continue

		values: dict[str, str] = {}
		for key, raw_value in item.items():
			if _is_hidden_field(key):
				continue

			value = _clean(raw_value)
			if value:
				values[key] = value

		if values:
			records.append(MeasurementRecord(form_type=form_type, values=values))

	return records


async def fetch_client_measurements(mobile: str) -> ClientMeasurementsResult:
	try:
		response = await http_get(BASE_URL, params={"mobile": mobile})
		payload = response.json() if response.content else {}
	except Exception:
		return ClientMeasurementsResult(
			success=False,
			message="Unable to fetch your saved measurements right now. Please try again shortly.",
			customer_name=None,
			records=[],
		)

	if str(payload.get("status", "")).lower() != "success":
		return ClientMeasurementsResult(
			success=False,
			message=str(payload.get("message") or "Unable to fetch saved measurements."),
			customer_name=None,
			records=[],
		)

	client_payload = payload.get("client") if isinstance(payload.get("client"), dict) else {}
	customer_name = _clean(client_payload.get("cname")) or None

	records: list[MeasurementRecord] = []
	records.extend(_extract_form_records(payload, "women_forms", "Women"))
	records.extend(_extract_form_records(payload, "men_forms", "Men"))

	if not records:
		return ClientMeasurementsResult(
			success=False,
			message="No saved measurements found for your profile.",
			customer_name=customer_name,
			records=[],
		)

	return ClientMeasurementsResult(
		success=True,
		message="Saved measurements fetched successfully.",
		customer_name=customer_name,
		records=records,
	)