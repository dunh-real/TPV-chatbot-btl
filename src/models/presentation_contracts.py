from pydantic import BaseModel, Field, model_validator
from typing import Literal, List

class TextBlock(BaseModel):
    id: str
    type: Literal["text"]
    text: str

class ChartBlock(BaseModel):
    id: str
    type: Literal["chart"]
    chart_type: Literal["bar", "line", "pie"]
    categories: List[str]
    series: List[dict]  # [{"name": str, "values": [float]}]

class TableBlock(BaseModel):
    id: str
    type: Literal["table"]
    headers: List[str]
    rows: List[List[str]]

DiagramType = Literal[
    "paired_grid", "three_columns", "relationship", "hub_spoke", "timeline"
]

class DiagramItem(BaseModel):
    title: str = Field(max_length=50)
    description: str = Field(default="", max_length=120)
    group: str = Field(default="", max_length=30)

class DiagramBlock(BaseModel):
    id: str
    type: Literal["diagram"]
    diagram_type: DiagramType
    center_label: str = ""
    items: List[DiagramItem]

    @model_validator(mode="after")
    def validate_item_count(self):
        limits = {
            "paired_grid": (4, 6), "three_columns": (3, 3),
            "relationship": (2, 3), "hub_spoke": (4, 6), "timeline": (4, 7),
        }
        minimum, maximum = limits[self.diagram_type]
        if not minimum <= len(self.items) <= maximum:
            raise ValueError(f"{self.diagram_type} requires {minimum}-{maximum} items")
        return self

SlideBlock = TextBlock | ChartBlock | TableBlock | DiagramBlock

class SlideSpec(BaseModel):
    id: str
    kind: Literal["cover", "text", "image_text", "chart", "table", "diagram"]
    title: str
    blocks: List[SlideBlock]
    speaker_notes: str = ""

class DeckSpec(BaseModel):
    schema_version: str = "1.0-demo"
    deck_title: str
    language: str = "vi"
    slides: List[SlideSpec]
