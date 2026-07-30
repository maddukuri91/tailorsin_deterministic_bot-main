from channels.telegram import parse_callback_query_update, parse_telegram_update


def test_telegram_parses_text_and_callback_updates():
    message = parse_telegram_update(
        {"message": {"chat": {"id": 10}, "from": {"id": 11}, "text": "hello"}}
    )
    callback = parse_callback_query_update(
        {
            "callback_query": {
                "id": "callback-1",
                "data": "menu_order_status",
                "from": {"id": 11},
                "message": {"chat": {"id": 10}},
            }
        }
    )

    assert message and message.user_id == 10 and message.text == "hello"
    assert callback and callback.user_id == 10 and callback.text == "order_status"
    assert callback.metadata and callback.metadata["is_menu_selection"] is True
