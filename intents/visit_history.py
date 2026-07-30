from crm.book_appointment import fetch_appointment_history


async def build_visit_history_response(mobile: str) -> str:
    history = await fetch_appointment_history(mobile)
    if not history.success:
        return history.message

    heading = "Your Store Visit History"
    if history.customer_name:
        heading = f"{heading} ({history.customer_name})"

    lines: list[str] = [heading, ""]
    for index, appointment in enumerate(history.appointments, start=1):
        lines.append(
            f"{index}. {appointment.bookdate_display} | {appointment.booktime} | {appointment.store_name} | {appointment.status_label}"
        )

    return "\n".join(lines)
