# geo-measure

FastAPI service for measuring features in KML and zipped shapefiles.

## Setup

Python 3.11+. Run from the project directory:

```sh
python3 -m venv .venv
export TMPDIR="$PWD" PIP_CACHE_DIR="$PWD/.pip-cache"
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

SQLite tables are created on startup in `geo-measure.db`.
Tests use an in-memory database:

```sh
TMPDIR="$PWD" .venv/bin/python -m pytest --basetemp=.pytest-tmp -q
```

## API

### POST /api/files/

Upload one .kml or .zip, up to 20 MB. A zip needs exactly one .shp and matching
.shx, .dbf, and .prj files. Unsafe archive paths are rejected; extraction is
limited to 100 MB. Invalid uploads return 400.

```sh
curl -F "file=@tests/data/sample.zip" http://localhost:8000/api/files/
```

Returns 201:

```json
{"id":"abc123abc123","filename":"sample.zip","feature_count":1,"crs":"EPSG:32631","status":"COMPLETED"}
```

### GET /api/files/{id}/

Use the ID returned by the upload:

```sh
curl http://localhost:8000/api/files/abc123abc123/
```

Returns 200:

```json
{"id":"abc123abc123","filename":"sample.zip","feature_count":1,"crs":"EPSG:32631","status":"COMPLETED"}
```

### GET /api/files/{id}/measurements/

```sh
curl http://localhost:8000/api/files/abc123abc123/measurements/
```

Returns 200 with one entry per feature:

```json
[{
  "feature_id":0,
  "geometry_type":"Polygon",
  "geometry":{"type":"Polygon","coordinates":[[[500000,0],[500000,1000],[501000,1000],[501000,0],[500000,0]]]},
  "crs":"EPSG:32631",
  "properties":{"name":"square"},
  "measurement":{"type":"area","value":1000000.0,"unit":"m2"},
  "measurement_crs":"EPSG:32631",
  "note":null
}]
```

Polygons have area in m2; lines have length in m. Points have null measurements.
Null, empty, and unsupported geometries also include a note. Both GET routes
return 404 for an unknown ID.

## Architecture

- `main.py`: app setup and routes.
- `db.py`: engine, sessions, and file/feature tables.
- `schemas.py`: response models.
- `reader.py`: upload validation and GeoPandas/pyogrio reading.
- `measure.py`: CRS selection and measurements.

Uploads create a PROCESSING record, are read from a temporary directory, then
deleted. Features and the COMPLETED status commit together. Processing errors
roll back features and save FAILED with an error message.

KML uses EPSG:4326; shapefiles require a .prj. Metre-based projected CRSs are
measured directly. Other CRSs go through EPSG:4326, then each feature uses a UTM
CRS chosen from its centroid with query_utm_crs_info. Transformers use
always_xy=True. Returned geometry keeps its original CRS.

## Design Decisions

I used FastAPI for a small API. Django with DRF would suit a larger app needing
users and an admin interface.

Per-feature UTM suits local features across several zones. One projection per
file is cheaper but may distort distant features. pyproj.Geod supports global
measurements on the ellipsoid; equal-area projections preserve area, not length.

Synchronous processing keeps setup simple. A queue would help with slow uploads
and retries. SQLite suits one instance; Postgres/PostGIS would support concurrent
writes and spatial queries.

Limitations: poles, large features, zone boundaries, and antimeridian crossings
can prevent or distort UTM measurements. Large files use memory and hold a
request open; the size check happens after multipart parsing. KML reads the
default layer. No authentication, geometry repair, or elevation measurements.
