from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field, field_validator


class Draw(BaseModel):
    draw_date: date
    mains: tuple[int, int, int, int, int]
    mega: int

    @field_validator("mains")
    @classmethod
    def validate_mains(cls, value: tuple[int, ...]):
        if len(value) != 5 or len(set(value)) != 5:
            raise ValueError("mains must contain five unique numbers")
        if any(number < 1 or number > 47 for number in value):
            raise ValueError("main number outside 1–47")
        return tuple(sorted(value))

    @field_validator("mega")
    @classmethod
    def validate_mega(cls, value: int):
        if not 1 <= value <= 27:
            raise ValueError("mega outside 1–27")
        return value


class Ticket(BaseModel):
    mains: tuple[int, int, int, int, int]
    mega: int

    @field_validator("mains")
    @classmethod
    def validate_mains(cls, value: tuple[int, ...]):
        if len(value) != 5 or len(set(value)) != 5:
            raise ValueError("ticket mains must contain five unique numbers")
        if any(number < 1 or number > 47 for number in value):
            raise ValueError("main number outside 1–47")
        return tuple(sorted(value))

    @field_validator("mega")
    @classmethod
    def validate_mega(cls, value: int):
        if not 1 <= value <= 27:
            raise ValueError("mega outside 1–27")
        return value


class LockedLine(Ticket):
    bundle_id: str
    generated_timestamp_utc: datetime
    intended_draw_date: date
    game_rules_version: str
    strategy: str
    line_id: int = Field(ge=1)
