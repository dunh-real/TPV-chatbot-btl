"""Hợp đồng quản trị model và cấu hình Agent; tên trường theo BE."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=200)
    type: Literal["llm", "embedding", "reranker"] = "llm"
    endpointUrl: str | None = None
    maxContextLength: int = Field(default=32768, ge=512, le=2000000)


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: float = Field(default=0.2, ge=0, le=2)
    maxAnswerTokens: int = Field(default=2048, ge=1, le=65536)
    language: str = Field(default="vi", min_length=1, max_length=40)
    topK: int = Field(default=8, ge=1, le=50)
    topP: float = Field(default=1, gt=0, le=1)
    enableThinking: bool = False


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ModelConfig
    systemPrompt: str = Field(default="", max_length=32000)
    parameters: Parameters = Field(default_factory=Parameters)
    requireCitation: bool = True
    # null: không thu hẹp thêm; []: không được truy cập phân vùng nào.
    allowedPartitionIds: list[int] | None = None

    @model_validator(mode="after")
    def valid_config(self):
        if self.model.type != "llm":
            raise ValueError("Agent phải dùng model loại llm")
        if self.parameters.maxAnswerTokens >= self.model.maxContextLength:
            raise ValueError("maxAnswerTokens phải nhỏ hơn maxContextLength")
        if self.allowedPartitionIds is not None and any(i <= 0 for i in self.allowedPartitionIds):
            raise ValueError("ID phân vùng phải là số nguyên dương")
        return self


class ModelTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: ModelConfig
    input: str = Field(default="Trả lời ngắn: kết nối thành công.", min_length=1, max_length=4000)
    documents: list[str] = Field(default_factory=lambda: ["Kết nối thành công"], max_length=10)

    @model_validator(mode="before")
    @classmethod
    def flat_model(cls, data):
        if isinstance(data, dict) and "code" in data and "model" not in data:
            data = dict(data)
            data["model"] = {key: data.pop(key) for key in ("code", "type", "endpointUrl", "maxContextLength") if key in data}
        return data


class AgentTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: AgentConfig
    request: str = Field(min_length=1, max_length=4000)
    file_id: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=50)
