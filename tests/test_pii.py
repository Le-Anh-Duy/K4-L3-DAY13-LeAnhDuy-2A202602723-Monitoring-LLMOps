from app.pii import scrub_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd_card_passport() -> None:
    out = scrub_text("CCCD 012345678901, card 4111 1111 1111 1111, passport C1234567")
    for raw in ("012345678901", "4111 1111 1111 1111", "C1234567"):
        assert raw not in out
    for tag in ("REDACTED_CCCD", "REDACTED_CREDIT_CARD", "REDACTED_PASSPORT_VN"):
        assert tag in out


def test_langfuse_mask_scrubs_nested_data() -> None:
    from app.tracing import mask_pii

    out = mask_pii(data={"q": "mail a@b.com", "docs": ["call 0901234567"], "n": 3})
    assert out == {"q": "mail [REDACTED_EMAIL]", "docs": ["call [REDACTED_PHONE_VN]"], "n": 3}
