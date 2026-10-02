"""Question / answer validation. Pending, cancelled and timeout never mean consent."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ImageInput(BaseModel):
    path: str | None = Field(None, max_length=4096, description="Absolute local PNG/JPEG/WebP/GIF path, read by the MCP process.")
    data_url: str | None = Field(None, max_length=7_000_000, description="Alternatively a base64 image data URL.")
    caption: str = Field("", max_length=500)

    @model_validator(mode="after")
    def _one_source(self) -> "ImageInput":
        if bool(self.path) == bool(self.data_url):
            raise ValueError("Provide exactly one of path or data_url")
        return self


class OptionInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    images: list[ImageInput] = Field(default_factory=list, max_length=3)

    @field_validator("label")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label is empty")
        return v


Mode = Literal["text", "single", "multiple"]


def _choice_rules(mode: str, options: list) -> None:
    if mode == "text" and options:
        raise ValueError("Text questions cannot contain options")
    if mode != "text" and len(options) < 2:
        raise ValueError("Choice questions require at least two options")
    if len({o.id for o in options}) != len(options):
        raise ValueError("Option IDs must be unique")


class QuestionItem(BaseModel):
    """One question of a multi-question ask."""
    question: str = Field(min_length=1, max_length=16000)
    images: list[ImageInput] = Field(default_factory=list, max_length=6)
    mode: Mode = "text"
    options: list[OptionInput] = Field(default_factory=list, max_length=12)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question is empty")
        return v

    @model_validator(mode="after")
    def _rules(self) -> "QuestionItem":
        _choice_rules(self.mode, self.options)
        return self


MAX_GROUP = 5


class AskInput(BaseModel):
    request_key: str = Field(min_length=1, max_length=150)
    work_id: str = Field(min_length=1, max_length=160)
    work_title: str = Field(min_length=1, max_length=160)
    question: str = Field("", max_length=16000)
    images: list[ImageInput] = Field(default_factory=list, max_length=6)
    mode: Mode = "text"
    options: list[OptionInput] = Field(default_factory=list, max_length=12)
    questions: list[QuestionItem] = Field(default_factory=list, max_length=MAX_GROUP)
    wait_seconds: int = Field(1800, ge=10, le=86400)

    @field_validator("question")
    @classmethod
    def _strip_q(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _rules(self) -> "AskInput":
        if self.questions:
            # Several questions answered together; top-level `question` (optional) is just an intro line.
            if self.options or self.images or self.mode != "text":
                raise ValueError("With `questions`, put mode/options/images inside each question item")
            return self
        if not self.question:
            raise ValueError("Provide `question`, or `questions` for several at once")
        if self.mode == "text" and self.options:
            raise ValueError("Text questions cannot contain options")
        if self.mode != "text" and len(self.options) < 2:
            raise ValueError("Choice questions require at least two options")
        if len({o.id for o in self.options}) != len(self.options):
            raise ValueError("Option IDs must be unique")
        return self


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: list[str] = Field(default_factory=list, max_length=12)
    text: str = Field("", max_length=20000)
    notes: dict[str, str] = Field(default_factory=dict)
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: dict[str, str]) -> dict[str, str]:
        if any(len(n) > 4000 for n in v.values()):
            raise ValueError("note too long")
        return v


def empty_draft() -> dict:
    return Draft().model_dump()
