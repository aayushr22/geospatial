from math import isfinite

from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


def measure_geometry(geometry: BaseGeometry | None, source_crs: CRS | str) -> dict:
    result = {"measurement": None, "measurement_crs": None, "note": None}
    if geometry is None:
        result["note"] = "Geometry is null."
        return result
    if geometry.is_empty:
        result["note"] = "Geometry is empty."
        return result
    geometry_type = geometry.geom_type
    if geometry_type in {"Point", "MultiPoint"}:
        return result
    if geometry_type not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
        result["note"] = f"{geometry_type} is not measured."
        return result

    source = CRS.from_user_input(source_crs)
    axes = source.axis_info[:2]
    if source.is_projected and len(axes) == 2 and all(
        axis.unit_conversion_factor == 1.0 for axis in axes
    ):
        measured = geometry
        measurement_crs = source
    else:
        to_wgs84 = Transformer.from_crs(source, "EPSG:4326", always_xy=True)
        geographic = transform(to_wgs84.transform, geometry)
        longitude, latitude = geographic.centroid.coords[0][:2]
        candidates = query_utm_crs_info(
            datum_name="WGS 84",
            area_of_interest=AreaOfInterest(longitude, latitude, longitude, latitude),
        )
        if not candidates:
            raise ValueError("No UTM CRS is available at the feature centroid.")
        measurement_crs = CRS.from_epsg(candidates[0].code)
        to_utm = Transformer.from_crs("EPSG:4326", measurement_crs, always_xy=True)
        measured = transform(to_utm.transform, geographic)

    is_area = geometry_type in {"Polygon", "MultiPolygon"}
    value = measured.area if is_area else measured.length
    if not isfinite(value):
        raise ValueError("Feature measurement is not finite.")
    result["measurement"] = {
        "type": "area" if is_area else "length",
        "value": value,
        "unit": "m2" if is_area else "m",
    }
    result["measurement_crs"] = measurement_crs.to_string()
    return result
