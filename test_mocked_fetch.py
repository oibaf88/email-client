import re
from app import extract_batched_payloads, decode_bytes

def test_extract_batched_payloads():
    fetch_data = [
        (b'1 (UID 1002 FLAGS (\\Seen) BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)] {25}', b'From: sender2\r\n\r\n'),
        b')',
        (b'2 (UID 1001 FLAGS () BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)] {25}', b'From: sender1\r\n\r\n'),
        b')'
    ]

    results = extract_batched_payloads(fetch_data)

    assert len(results) == 2

    uid1, payload1, flags1 = results[0]
    assert uid1 == '1002'
    assert payload1 == b'From: sender2\r\n\r\n'
    assert flags1 == ['\\Seen']

    uid2, payload2, flags2 = results[1]
    assert uid2 == '1001'
    assert payload2 == b'From: sender1\r\n\r\n'
    assert flags2 == []
    print("All tests passed.")

if __name__ == '__main__':
    test_extract_batched_payloads()
