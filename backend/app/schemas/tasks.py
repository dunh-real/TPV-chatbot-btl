"""Tham số tác vụ UC37; không cho client truyền code thực thi hoặc đường dẫn server."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskKind = Literal["Review", "Summarize", "TaskExtract", "Draft", "Aggregate", "Slide"]
TaskStatus = Literal["Queued", "Processing", "Completed", "Failed", "Cancelled"]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: TaskKind
    parameters: dict = Field(default_factory=dict)
    notify_on_finish: bool = True


class ReviewParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=1, max_length=1000)
    noi_gui: str = Field(default="", max_length=500)
    rule_set: str = Field(default="", max_length=100)


class TaskNotify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
