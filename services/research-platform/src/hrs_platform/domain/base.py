from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class HistoricalDate(Contract):
    original: str = ""
    start_year: int | None = Field(default=None, ge=1, le=9999)
    end_year: int | None = Field(default=None, ge=1, le=9999)
    precision: Literal["day", "month", "year", "range", "unknown"] = "unknown"
    state: str = "unknown"
    basis: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered(self):
        if self.start_year is not None and self.end_year is not None and self.end_year < self.start_year:
            raise ValueError("Date range is reversed")
        return self
