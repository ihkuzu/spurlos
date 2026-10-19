from datetime import timezone

from spurlos.headers import parse_header


def raw(**fields):
    lines = [f"{k.replace('_', '-')}: {v}" for k, v in fields.items()]
    return ("\r\n".join(lines) + "\r\n\r\nbody text that must not matter\r\n").encode()


def test_basic_fields():
    h = parse_header(
        raw(
            From='"Brotkorb Shop" <Bestellung@Mail.Brotkorb-Shop.de>',
            Date="Mon, 19 Oct 2026 09:00:00 +0200",
            Subject="Ihre Bestellung",
            Message_ID="<abc@brotkorb-shop.de>",
            List_Unsubscribe="<mailto:u@brotkorb-shop.de>",
        )
    )
    assert h.address == "bestellung@mail.brotkorb-shop.de"
    assert h.domain == "mail.brotkorb-shop.de"
    assert h.name == "Brotkorb Shop"
    assert h.date.tzinfo == timezone.utc and h.date.hour == 7
    assert h.unsubscribe is True


def test_encoded_words_are_decoded():
    h = parse_header(
        raw(
            From="=?utf-8?q?M=C3=BCller_GmbH?= <info@mueller.de>",
            Subject="=?utf-8?b?R3LDvMOfZQ==?=",
        )
    )
    assert h.name == "Müller GmbH"
    assert h.subject == "Grüße"


def test_missing_message_id_gives_stable_key():
    a = parse_header(raw(From="a@shop.de", Date="Mon, 19 Oct 2026 09:00:00 +0000", Subject="x"))
    b = parse_header(raw(From="a@shop.de", Date="Mon, 19 Oct 2026 09:00:00 +0000", Subject="x"))
    assert a.key == b.key


def test_bad_date_is_none_and_no_sender_is_skipped():
    assert parse_header(raw(From="a@shop.de", Date="not a date")).date is None
    assert parse_header(raw(Subject="no sender")) is None
    assert parse_header(raw(From="undisclosed-recipients:;")) is None
