MAX_BODY_CHARS = 20_000


def truncate(text: str | None) -> str:
    return (text or "")[:MAX_BODY_CHARS]
