from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/humanhandover.php"


@dataclass
class HumanHandoverResult:
	success: bool
	message: str


async def request_human_handover(mobile: str) -> HumanHandoverResult:
	payload = {"mobile": mobile}

	try:
		response = await http_post(BASE_URL, json_body=payload)
		data = response.json() if response.content else {}
	except Exception:
		return HumanHandoverResult(
			success=False,
			message="Unable to connect to a human agent right now. Please try again shortly.",
		)

	if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
		return HumanHandoverResult(
			success=True,
			message=str(data.get("message") or "A human agent will connect with you shortly."),
		)

	return HumanHandoverResult(
		success=False,
		message=str(data.get("message") or "Unable to create human handover request."),
	)