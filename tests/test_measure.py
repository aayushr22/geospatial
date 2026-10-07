import pytest
from pyproj import Transformer
from shapely.geometry import (
    GeometryCollection, LineString, MultiLineString, MultiPoint,
    MultiPolygon, Point, Polygon, box,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.measure import measure_geometry


@pytest.mark.parametrize(
    "epsg, northing", [(32631, 0), (32632, 0), (32731, 9998000)]
)
def test_geographic_square_uses_its_utm_zone(epsg: int, northing: int) -> None:
    to_geographic = Transformer.from_crs(epsg, 4326, always_xy=True)
    square = transform(
        to_geographic.transform, box(500000, northing, 501000, northing + 1000)
    )

    result = measure_geometry(square, "EPSG:4326")

    assert result["measurement"]["type"] == "area"
    assert result["measurement"]["unit"] == "m2"
    assert result["measurement"]["value"] == pytest.approx(1000000, rel=0.01)
    assert result["measurement_crs"] == f"EPSG:{epsg}"


def test_geographic_line_length() -> None:
    to_geographic = Transformer.from_crs(32631, 4326, always_xy=True)
    line = transform(
        to_geographic.transform, LineString([(500000, 100), (501000, 100)])
    )

    result = measure_geometry(line, "EPSG:4326")

    assert result["measurement"]["type"] == "length"
    assert result["measurement"]["value"] == pytest.approx(1000, rel=0.01)
    assert result["measurement"]["unit"] == "m"


@pytest.mark.parametrize("geometry", [Point(3, 0), MultiPoint([(3, 0), (4, 0)])])
def test_points_have_no_measurement(geometry: BaseGeometry) -> None:
    result = measure_geometry(geometry, "EPSG:4326")

    assert result["measurement"] is None
    assert result["measurement_crs"] is None
    assert result["note"] is None


@pytest.mark.parametrize(
    "geometry, note",
    [
        (None, "Geometry is null."),
        (Polygon(), "Geometry is empty."),
        (Point(), "Geometry is empty."),
        (GeometryCollection([Point(3, 0)]), "GeometryCollection is not measured."),
    ],
)
def test_unmeasured_geometries_have_a_note(geometry: BaseGeometry | None, note: str) -> None:
    result = measure_geometry(geometry, "EPSG:4326")

    assert result["measurement"] is None
    assert result["note"] == note


@pytest.mark.parametrize(
    "geometry, expected_type, value",
    [
        (MultiPolygon([box(0, 0, 1000, 1000), box(2000, 0, 3000, 1000)]), "area", 2000000),
        (MultiLineString([[(0, 0), (3, 0)], [(0, 0), (0, 4)]]), "length", 7),
    ],
)
def test_projected_metres_are_measured_directly(
    geometry: BaseGeometry, expected_type: str, value: float
) -> None:
    result = measure_geometry(geometry, "EPSG:32631")

    assert result["measurement"]["type"] == expected_type
    assert result["measurement"]["value"] == pytest.approx(value)
    assert result["measurement_crs"] == "EPSG:32631"


def test_projected_feet_are_converted_through_utm() -> None:
    result = measure_geometry(
        LineString([(980000, 190000), (981000, 190000)]), "EPSG:2263"
    )

    assert result["measurement"]["value"] == pytest.approx(304.8006096, rel=0.01)
    assert result["measurement"]["unit"] == "m"
    assert result["measurement_crs"] == "EPSG:32618"


def test_polar_feature_has_a_clear_error() -> None:
    with pytest.raises(ValueError, match="No UTM CRS"):
        measure_geometry(box(0, 87, 1, 88), "EPSG:4326")
