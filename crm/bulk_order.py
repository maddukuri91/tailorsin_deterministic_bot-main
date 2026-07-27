from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/bulkorder.php"


@dataclass
class BulkOrderResult:
	success: bool
	message: str
	enquiry_id: int | None = None


async def create_bulk_order_enquiry(mobile: str) -> BulkOrderResult:
	payload = {"mobile": mobile}

	try:
		response = await http_post(BASE_URL, json_body=payload)
		data = response.json() if response.content else {}
	except Exception:
		return BulkOrderResult(
			success=False,
			message="Unable to submit your bulk order enquiry right now. Please try again shortly.",
		)

	if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
		enquiry_id_raw = data.get("enquiry_id") or data.get("alert_id")
		enquiry_id = int(enquiry_id_raw) if str(enquiry_id_raw).isdigit() else None
		message = str(data.get("message") or "Your bulk order enquiry has been submitted.")
		if enquiry_id is not None:
			message = f"{message} (Enquiry ID: {enquiry_id})"
		return BulkOrderResult(success=True, message=message, enquiry_id=enquiry_id)

	return BulkOrderResult(
		success=False,
		message=str(data.get("message") or "Unable to submit bulk order enquiry."),
	)