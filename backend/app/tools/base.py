"""Kiểu dùng chung cho cả tầng tool.

`ToolError` phải là MỘT class duy nhất cho mọi tool: mỗi module tự định nghĩa một
`ToolError` riêng thì `except ToolError` ở node gọi tool sẽ bắt hụt lỗi của module
khác, và lỗi tham số lại nổi lên thành lỗi 500.

Schema gửi cho model được SINH từ chữ ký hàm, không viết tay: kiểu lấy từ type hint
nên không thể lệch khi sửa hàm, còn mô tả vẫn do người viết bằng tiếng Việt. Viết
tay cả hai thì sớm muộn schema cũng nói một đằng, hàm nhận một nẻo.
"""

from __future__ import annotations

import inspect
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(ValueError):
    """Tham số gọi tool không hợp lệ - chặn ngay, không đoán ý người dùng."""


_JSON_TYPES: list[tuple[type, str]] = [
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
]


def _unwrap_optional(annotation: Any) -> list[Any]:
    """Tách `X | None` / `Optional[X]` thành danh sách nhánh không phải None."""
    origin = typing.get_origin(annotation)
    if origin in (types.UnionType, typing.Union):
        return [a for a in typing.get_args(annotation) if a is not type(None)]
    return [annotation]


def _json_type(annotation: Any) -> dict[str, Any]:
    """Một nhánh kiểu Python -> mẩu JSON Schema tương ứng."""
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}

    # Nhiều nhánh (ví dụ `list[str] | str`): lấy nhánh đầu, code vẫn nhận cả hai.
    branches = _unwrap_optional(annotation)
    annotation = branches[0] if branches else str

    origin = typing.get_origin(annotation) or annotation
    schema: dict[str, Any] = {"type": "string"}
    for python_type, json_name in _JSON_TYPES:
        if origin is python_type or (inspect.isclass(origin) and issubclass(origin, python_type)):
            schema = {"type": json_name}
            break

    if schema["type"] == "array":
        args = typing.get_args(annotation)
        schema["items"] = _json_type(args[0]) if args else {"type": "string"}
    return schema


@dataclass(slots=True)
class ToolSpec:
    """Khai báo một tool: đủ để sinh mô tả cho prompt và để gọi lại bằng tên."""

    name: str
    description: str
    parameters: dict[str, str]
    func: Callable[..., Any]
    # Tool đọc CSDL nghiệp vụ nhận `AsyncSession` làm tham số đầu; registry sẽ tự
    # mở phiên, người gọi không phải biết tool nào cần CSDL.
    needs_session: bool = False
    # Tên tham số khác mà tool vẫn hiểu, dạng {bí danh: tên thật}.
    aliases: dict[str, str] = field(default_factory=dict)
    # Tool GHI (sinh file, ghi sổ văn bản). Agent phải xin người duyệt trước khi
    # gọi; tool chỉ đọc thì cho chạy tự do.
    side_effect: bool = False

    def describe(self) -> str:
        params = "; ".join(f"{k} ({v})" for k, v in self.parameters.items()) or "(không có)"
        return f"- {self.name}: {self.description}\n  Tham số: {params}"

    def to_json_schema(self) -> dict[str, Any]:
        """Schema kiểu OpenAI function-calling, sinh từ chữ ký hàm.

        Chỉ phơi ra đúng các tham số đã khai trong `parameters` - đó cũng là tập mà
        `call_tool` chấp nhận, nên model không thể gọi bằng tham số bị chặn. Bí danh
        cố tình không phơi: chúng tồn tại để nhận đầu vào cũ, không phải để model
        có hai cách gọi cùng một thứ.
        """
        try:
            signature = inspect.signature(self.func, eval_str=True)
        except (ValueError, TypeError, NameError):
            signature = None

        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, description in self.parameters.items():
            parameter = signature.parameters.get(name) if signature else None
            annotation = parameter.annotation if parameter else inspect.Parameter.empty
            properties[name] = {**_json_type(annotation), "description": description}
            if parameter is not None and parameter.default is inspect.Parameter.empty:
                required.append(name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
