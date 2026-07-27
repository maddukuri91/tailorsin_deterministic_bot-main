# BASE_CATALOGUE_URL = "https://crm.tailorsin.com/tailorsin-api/api/itemswithprice.php"

BASE_CATALOGUE_URL = "https://drive.google.com/file/d/1s67qOzn2n22lN670ir0Le462FcgGyCGL/view?usp=sharing"

def _resolve_catalog_category(client_type: str) -> int:
    normalized = (client_type or "").strip().lower()
    if normalized == "active_client":
        return 6
    if normalized == "client":
        return 6
    return 6


CATEGORY_LABELS = {
    1: "Retail 24-25",
    2: "B2B 24-25",
    6: "Retail 25-26",
    7: "B2B 25-26",
}


def _category_label(catid: int) -> str:
    return CATEGORY_LABELS.get(catid, f"Category {catid}")


async def build_pricing_response(client_type: str) -> str:
    catalogue_url = f"{BASE_CATALOGUE_URL}?view=html"

    return (
        "📋 Tap to view our complete Price Catalogue:\n"
        f"{catalogue_url}"
    )
