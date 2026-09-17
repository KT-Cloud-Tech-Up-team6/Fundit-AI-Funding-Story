import json


def emit(event, **fields):
    """Write a credential-safe structured event."""
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)
