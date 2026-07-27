from crm.items import fetch_browse_catalog


BROWSE_INTRO = (
    "We stitch Women's, Men's, and Kids wear with custom fitting, design preferences, and finishing support. Here are the categories currently available:\n\n"
)


async def build_browse_response() -> str:
    catalog = await fetch_browse_catalog()

    sections: list[str] = [BROWSE_INTRO.rstrip(), ""]

    if catalog.womens_wear:
        sections.append("Women's wear:")
        sections.extend(f"- {item}" for item in catalog.womens_wear)
        sections.append("")

    if catalog.mens_wear:
        sections.append("Men's wear:")
        sections.extend(f"- {item}" for item in catalog.mens_wear)
        sections.append("")

    if catalog.kids_wear:
        sections.append("Kids wear:")
        sections.extend(f"- {item}" for item in catalog.kids_wear)
        sections.append("")

    sections.append("Reply with 0 for the main menu or 9 to chat with a human agent.")
    return "\n".join(sections)