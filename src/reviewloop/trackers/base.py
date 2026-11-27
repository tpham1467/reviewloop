from typing import Protocol

from pydantic import BaseModel


class Ticket(BaseModel):
    key: str
    title: str
    body: str
    url: str


class TrackerAdapter(Protocol):
    def fetch(self, keys: list[str]) -> list[Ticket]: ...
