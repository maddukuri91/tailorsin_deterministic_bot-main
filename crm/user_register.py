from dataclasses import dataclass
import logging

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/clientregistration.php"
logger = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
	success: bool
	message: str


def _mask_mobile(mobile: str) -> str:
	digits_only = "".join(character for character in mobile if character.isdigit())
	if len(digits_only) <= 4:
		return digits_only
	return f"***{digits_only[-4:]}"


async def register_new_client(
	client_name: str,
	primary_no: str,
	secondary_no: str | None = None,
) -> RegistrationResult:
	"""
	Register a new client via clientregistration.php.
	"""
	try:
		payload = {
			"client_name": client_name,
			"primary_no": primary_no,
			"secondary_no": secondary_no,
		}

		response = await http_post(BASE_URL, json_body=payload)
		data = response.json()

		logger.info(
			"register_new_client request mobile=%s has_name=%s status_code=%s",
			_mask_mobile(primary_no),
			bool(client_name),
			response.status_code,
		)

		if response.status_code >= 400:
			return RegistrationResult(
				success=False,
				message=data.get("message", "Unable to register right now. Please try again."),
			)

		status = str(data.get("status", "")).strip().lower()
		if status == "success":
			return RegistrationResult(
				success=True,
				message=data.get("message", "You are registered successfully."),
			)

		return RegistrationResult(
			success=False,
			message=data.get("message", "Unable to register right now. Please try again."),
		)

	except Exception:
		logger.exception(
			"register_new_client exception mobile=%s has_name=%s",
			_mask_mobile(primary_no),
			bool(client_name),
		)
		return RegistrationResult(
			success=False,
			message="Unable to register right now. Please try again.",
		)