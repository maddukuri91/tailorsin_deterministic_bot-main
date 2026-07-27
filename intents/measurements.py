from crm.measurements import fetch_client_measurements


MAX_FIELDS_PER_RECORD = 14


def _labelize(field_name: str) -> str:
    return field_name.replace("_", " ").strip().title()


async def build_measurements_response(mobile: str) -> str:
    result = await fetch_client_measurements(mobile)
    if not result.success:
        return result.message

    heading = "Your Saved Measurements"
    if result.customer_name:
        heading = f"{heading} ({result.customer_name})"

    lines: list[str] = [heading, ""]

    for index, record in enumerate(result.records, start=1):
        lines.append(f"{index}. {record.form_type} Measurement Record")

        shown = 0
        for key, value in record.values.items():
            lines.append(f"- {_labelize(key)}: {value}")
            shown += 1
            if shown >= MAX_FIELDS_PER_RECORD:
                break

        if len(record.values) > MAX_FIELDS_PER_RECORD:
            lines.append(f"- ...and {len(record.values) - MAX_FIELDS_PER_RECORD} more measurements")

        lines.append("")

    lines.append("Reply with 0 for main menu or 9 to chat with a human agent.")
    return "\n".join(lines)