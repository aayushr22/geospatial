import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from pyproj.exceptions import ProjError
from shapely.errors import GEOSException
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import db
from .measure import measure_geometry
from .reader import read_upload
from .schemas import FeatureInfo, FileInfo


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.Base.metadata.create_all(db.engine)
    yield


app = FastAPI(title="geo-measure", lifespan=lifespan)


def geometry_json(geometry: BaseGeometry | None) -> dict | None:
    if geometry is None:
        return None
    if geometry.geom_type == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [geometry_json(part) for part in geometry.geoms],
        }
    if geometry.is_empty:
        return {"type": geometry.geom_type, "coordinates": []}
    return mapping(geometry)


@app.post("/api/files/", response_model=FileInfo, status_code=201)
def upload_file(file: UploadFile, session: Session = Depends(db.get_session)) -> db.File:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    record = db.File(filename=filename, status="PROCESSING")
    session.add(record)
    session.commit()
    file_id = record.id

    try:
        frame = read_upload(file)
        record.crs = frame.crs.to_string()
        rows = json.loads(
            frame.drop(columns=frame.geometry.name).to_json(
                orient="records", date_format="iso", default_handler=str
            )
        )
        for index, (geometry, properties) in enumerate(zip(frame.geometry, rows, strict=True)):
            result = measure_geometry(geometry, frame.crs)
            measurement = result["measurement"] or {}
            session.add(
                db.Feature(
                    file_id=file_id,
                    feature_index=index,
                    geometry_type=geometry.geom_type if geometry is not None else None,
                    geometry=json.dumps(geometry_json(geometry), allow_nan=False),
                    properties=properties,
                    measurement_type=measurement.get("type"),
                    measurement_value=measurement.get("value"),
                    measurement_unit=measurement.get("unit"),
                    measurement_crs=result["measurement_crs"],
                    note=result["note"],
                )
            )
        record.feature_count = len(frame)
        record.status = "COMPLETED"
        session.commit()
    except (ValueError, ProjError, GEOSException, SQLAlchemyError) as exc:
        session.rollback()
        record = session.get(db.File, file_id)
        record.status = "FAILED"
        record.error = str(exc)
        session.commit()
        status_code = 500 if isinstance(exc, SQLAlchemyError) else 400
        detail = "Could not store file measurements." if status_code == 500 else str(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return record


@app.get("/api/files/{file_id}/", response_model=FileInfo)
def file_info(file_id: str, session: Session = Depends(db.get_session)) -> db.File:
    record = session.get(db.File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


@app.get("/api/files/{file_id}/measurements/", response_model=list[FeatureInfo])
def file_measurements(file_id: str, session: Session = Depends(db.get_session)) -> list[dict]:
    record = session.get(db.File, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    features = session.scalars(
        select(db.Feature).where(db.Feature.file_id == file_id).order_by(db.Feature.feature_index)
    )
    results = []
    for feature in features:
        measurement = None
        if feature.measurement_type is not None:
            measurement = {
                "type": feature.measurement_type,
                "value": feature.measurement_value,
                "unit": feature.measurement_unit,
            }
        results.append(
            {
                "feature_id": feature.feature_index,
                "geometry_type": feature.geometry_type,
                "geometry": json.loads(feature.geometry),
                "crs": record.crs,
                "properties": feature.properties,
                "measurement": measurement,
                "measurement_crs": feature.measurement_crs,
                "note": feature.note,
            }
        )
    return results
