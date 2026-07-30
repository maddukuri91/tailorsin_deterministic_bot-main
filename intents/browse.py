from crm.items import fetch_browse_catalog


BROWSE_INTRO = "We tailor garments for women, men, and children. Available categories:"


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

    return "\n".join(sections)
