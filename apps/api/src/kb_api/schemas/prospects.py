from kb_contracts.domain import Prospect
from pydantic import BaseModel


class ProspectPage(BaseModel):
    items: list[Prospect]
    next_cursor: str | None = None
