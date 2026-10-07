from io import BytesIO
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile

import geopandas as gpd
import pytest
from pyproj import CRS
from fastapi.testclient import TestClient
from shapely.geometry import GeometryCollection, Point, Polygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import db, main, reader


DATA = Path(__file__).parent / "data"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(db, "engine", engine)
    with TestClient(main.app) as test_client:
        yield test_client
    engine.dispose()


def make_zip(parts: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def sample_zip_parts() -> dict[str, bytes]:
    with ZipFile(DATA / "sample.zip") as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.parametrize(
    "filename, feature_count, crs",
    [("sample.kml", 3, "EPSG:4326"), ("sample.zip", 1, "EPSG:32631")],
)
def test_upload_and_read_measurements(
    client: TestClient, filename: str, feature_count: int, crs: str
) -> None:
    response = client.post(
        "/api/files/", files={"file": (filename, (DATA / filename).read_bytes())}
    )

    assert response.status_code == 201, response.text
    info = response.json()
    assert info["filename"] == filename
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == feature_count
    assert info["crs"] == crs
    assert len(info["id"]) == 12
    assert client.get(f"/api/files/{info['id']}/").json() == info

    measurements = client.get(f"/api/files/{info['id']}/measurements/")
    assert measurements.status_code == 200
    features = measurements.json()
    assert len(features) == feature_count
    assert [feature["feature_id"] for feature in features] == list(range(feature_count))
    assert all(feature["crs"] == crs for feature in features)
    polygon = next(feature for feature in features if feature["geometry_type"] == "Polygon")
    assert polygon["measurement"]["type"] == "area"
    assert polygon["measurement"]["unit"] == "m2"
    assert polygon["properties"]
    if filename == "sample.zip":
        assert polygon["measurement"]["value"] == pytest.approx(1000000)
        assert polygon["measurement_crs"] == crs
        assert polygon["geometry"]["coordinates"][0][0] == [500000, 0]
    else:
        assert polygon["geometry"]["coordinates"][0][0][:2] == [3, 0]
        assert polygon["measurement_crs"] == "EPSG:32631"
        line = next(feature for feature in features if feature["geometry_type"] == "LineString")
        assert line["measurement"]["unit"] == "m"
        point = next(feature for feature in features if feature["geometry_type"] == "Point")
        assert point["measurement"] is None
    assert not list(reader.PROJECT_ROOT.glob(".geo-measure-*"))


def test_unsupported_extension_is_saved_as_failed(client: TestClient) -> None:
    response = client.post("/api/files/", files={"file": ("notes.txt", b"hello")})

    assert response.status_code == 400
    assert ".kml and .zip" in response.json()["detail"]
    with Session(db.engine) as session:
        record = session.scalar(select(db.File))
        assert record.status == "FAILED"
        assert record.error == response.json()["detail"]


def test_zip_without_shapefile(client: TestClient) -> None:
    response = client.post(
        "/api/files/", files={"file": ("empty.zip", make_zip({"notes.txt": b"hello"}))}
    )

    assert response.status_code == 400
    assert "exactly one .shp" in response.json()["detail"]


@pytest.mark.parametrize("extension", [".shx", ".dbf", ".prj"])
def test_missing_shapefile_parts(client: TestClient, extension: str) -> None:
    parts = {
        name: data for name, data in sample_zip_parts().items()
        if not name.endswith(extension)
    }
    response = client.post("/api/files/", files={"file": ("missing.zip", make_zip(parts))})

    assert response.status_code == 400
    assert extension in response.json()["detail"]
    if extension == ".prj":
        assert "CRS is missing" in response.json()["detail"]


def test_zip_with_two_shapefiles(client: TestClient) -> None:
    parts = sample_zip_parts()
    parts["other.shp"] = parts["square.shp"]
    response = client.post("/api/files/", files={"file": ("two.zip", make_zip(parts))})

    assert response.status_code == 400
    assert "exactly one .shp" in response.json()["detail"]


@pytest.mark.parametrize("name", ["../escape.txt", "/escape.txt", r"..\escape.txt", "C:/escape.txt"])
def test_zip_path_traversal(client: TestClient, name: str) -> None:
    response = client.post(
        "/api/files/", files={"file": ("unsafe.zip", make_zip({name: b"hello"}))}
    )

    assert response.status_code == 400
    assert "unsafe path" in response.json()["detail"]
    assert not list(reader.PROJECT_ROOT.glob(".geo-measure-*"))


def test_upload_size_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "MAX_UPLOAD_SIZE", 8)

    response = client.post("/api/files/", files={"file": ("large.kml", b"123456789")})

    assert response.status_code == 400
    assert "Upload exceeds" in response.json()["detail"]
    assert not list(reader.PROJECT_ROOT.glob(".geo-measure-*"))


@pytest.mark.parametrize("suffix", ["", "measurements/"])
def test_unknown_file(client: TestClient, suffix: str) -> None:
    response = client.get(f"/api/files/unknown/{suffix}")

    assert response.status_code == 404


def test_null_empty_and_collection_geometries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = gpd.GeoDataFrame(
        {"value": [float("nan"), 1, 2, 3]},
        geometry=[None, Polygon(), Point(), GeometryCollection([Point(), Point(3, 0)])],
        crs="EPSG:4326",
    )
    monkeypatch.setattr(main, "read_upload", lambda upload: frame)

    response = client.post("/api/files/", files={"file": ("mixed.kml", b"placeholder")})

    assert response.status_code == 201, response.text
    features = client.get(f"/api/files/{response.json()['id']}/measurements/").json()
    assert [feature["measurement"] for feature in features] == [None, None, None, None]
    assert all(feature["note"] for feature in features)
    assert features[0]["geometry"] is None
    assert features[0]["properties"]["value"] is None
    assert features[1]["geometry"] == {"type": "Polygon", "coordinates": []}
    assert features[2]["geometry"] == {"type": "Point", "coordinates": []}
    assert features[3]["geometry"]["type"] == "GeometryCollection"
    assert features[3]["geometry"]["geometries"][0]["coordinates"] == []


def test_processing_failure_rolls_back_features(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = main.measure_geometry
    count = 0

    def fail_on_second_feature(geometry: BaseGeometry | None, crs: CRS) -> dict:
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("Feature cannot be measured.")
        return original(geometry, crs)

    monkeypatch.setattr(main, "measure_geometry", fail_on_second_feature)

    response = client.post(
        "/api/files/", files={"file": ("sample.kml", (DATA / "sample.kml").read_bytes())}
    )

    assert response.status_code == 400
    with Session(db.engine) as session:
        record = session.scalar(select(db.File))
        assert record.status == "FAILED"
        assert record.error == "Feature cannot be measured."
        assert record.feature_count == 0
        assert session.scalar(select(db.Feature)) is None
