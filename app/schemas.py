from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: Literal["PROCESSING", "COMPLETED", "FAILED"]


class Measurement(BaseModel):
    type: Literal["area", "length"]
    value: float
    unit: Literal["m2", "m"]


class FeatureInfo(BaseModel):
    feature_id: int
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str
    properties: dict[str, Any]
    measurement: Measurement | None
    measurement_crs: str | None
    note: str | None = None
