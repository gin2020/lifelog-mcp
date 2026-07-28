from pydantic import BaseModel


class FinanceQueryResult(BaseModel):
    answer: str
