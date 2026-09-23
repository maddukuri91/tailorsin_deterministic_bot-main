from dataclasses import dataclass

from services.http_client import http_post


BASE_URL = "https://crm.tailorsin.com/tailorsin-api/api/customfabricestimation.php"


@dataclass
class FabricEstimationResult:
    success: bool
    message: str


async def custom_fabric_estimation(
    client_name: str,
    primary_no: str,
    secondary_no: str | None = None,
) -> FabricEstimationResult:
    """
    Request a custom fabric estimation via customfabricestimation.php.

    Sends client_name, primary_no and secondary_no to the CRM.
    """
    payload = {
        "client_name": client_name,
        "primary_no": primary_no,
        "secondary_no": secondary_no,
    }

    try:
        response = await http_post(BASE_URL, json_body=payload)
        data = response.json() if response.content else {}
    except Exception:
        return FabricEstimationResult(
            success=False,
            message="Unable to submit your fabric estimation request right now. Please try again shortly.",
        )

    if response.status_code == 200 and str(data.get("status", "")).lower() == "success":
        return FabricEstimationResult(
            success=True,
            message=str(data.get("message") or "Fabric estimation request has been submitted."),
        )

    return FabricEstimationResult(
        success=False,
        message=str(data.get("message") or "Unable to submit fabric estimation request."),
    )