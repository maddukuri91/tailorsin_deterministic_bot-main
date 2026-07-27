from crm.order_status import fetch_all_orders


async def build_order_status_response(mobile: str) -> str:
    result = await fetch_all_orders(mobile)
    if not result.success:
        return result.message

    if not result.orders and not result.alterations:
        return result.message

    heading = "Your Orders"
    if result.customer_name:
        heading = f"{heading} ({result.customer_name})"

    lines: list[str] = [
        heading,
        "",
    ]

    # Show regular orders
    if result.orders:
        for index, order in enumerate(result.orders, start=1):
            # Show a status emoji based on stage
            status_emoji = {
                1: "🔄",    # log. working
                2: "📐",    # measurement
                3: "✂️",    # cutting
                4: "🧵",    # stitching
                5: "🔧",    # finishing
                6: "✅",    # ready
                7: "📦",    # finished/delivered
                8: "❌",    # cancelled
            }.get(order.stage, "📋")

            lines.append(f"{index}. {status_emoji} Order #{order.order_id} — {order.stage_label}")
            if order.order_date:
                lines.append(f"   Order Date: {order.order_date}")
            if order.pickup_date:
                pickup_line = f"   Pickup Date: {order.pickup_date}"
                if order.pickup_time:
                    pickup_line = f"{pickup_line} ({order.pickup_time})"
                lines.append(pickup_line)
            if order.last_update_date:
                lines.append(f"   Last Update: {order.last_update_date}")
            lines.append("")

    # Show alterations separately
    if result.alterations:
        lines.append("--- Pickup Alterations ---")
        lines.append("")
        for index, alt in enumerate(result.alterations, start=1):
            alt_status_emoji = "🔄" if alt.status == 0 else "✅" if alt.status == 1 else "❌"
            closed_tag = " (Closed)" if alt.order_closed else ""
            lines.append(f"{index}. {alt_status_emoji} Alteration for Order #{alt.order_id}{closed_tag}")
            lines.append(f"   Status: {alt.status_label}")
            if alt.pickup_date:
                pickup_line = f"   Pickup Date: {alt.pickup_date}"
                if alt.pickup_time_label:
                    pickup_line = f"{pickup_line} ({alt.pickup_time_label})"
                lines.append(pickup_line)
            if alt.last_update:
                lines.append(f"   Last Update: {alt.last_update}")
            lines.append("")

    lines.extend([
        "Reply with 0 for main menu or 9 to chat with a human agent.",
    ])
    return "\n".join(lines)
