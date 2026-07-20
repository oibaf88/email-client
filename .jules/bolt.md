## 2024-07-20 - Fix N+1 queries during list messages
**Learning:** Python's imaplib doesn't natively batch uid fetches if passed a list in the python loop. Looping `client.uid("FETCH", uid)` produces significant N+1 network overhead because each call executes a synchronous network trip.
**Action:** Always serialize UIDs to a single comma-separated string `uid_list = ",".join(uids)` and execute a single `client.uid("FETCH", uid_list, ...)` instead of looping. Also extract `UID <num>` from the response parts since the server can return results out of order.
