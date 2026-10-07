import shutil
import stat
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

import geopandas as gpd
from fastapi import UploadFile
from pyogrio.errors import DataSourceError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
MAX_EXTRACTED_SIZE = 100 * 1024 * 1024


class UploadError(ValueError):
    pass


def extract_shapefile(archive_path: Path, directory: Path) -> Path:
    try:
        with ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if sum(entry.file_size for entry in entries) > MAX_EXTRACTED_SIZE:
                raise UploadError("Extracted archive exceeds 100 MB.")

            paths = set()
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                target = (directory / name).resolve()
                if (
                    PureWindowsPath(name).drive
                    or ".." in Path(name).parts
                    or not target.is_relative_to(directory.resolve())
                ):
                    raise UploadError("Archive contains an unsafe path.")
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise UploadError("Archive contains a symbolic link.")
                if target in paths:
                    raise UploadError("Archive contains duplicate paths.")
                paths.add(target)

                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
    except (
        BadZipFile, RuntimeError, NotImplementedError,
        FileExistsError, IsADirectoryError, NotADirectoryError,
    ) as exc:
        raise UploadError("Archive cannot be read.") from exc

    shapefiles = [
        path for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() == ".shp"
    ]
    if len(shapefiles) != 1:
        raise UploadError("Archive must contain exactly one .shp file.")
    shapefile = shapefiles[0]
    parts = {
        path.suffix.lower()
        for path in shapefile.parent.iterdir()
        if path.is_file() and path.stem.lower() == shapefile.stem.lower()
    }
    missing = sorted({".shx", ".dbf"} - parts)
    if missing:
        raise UploadError(f"Shapefile is missing {', '.join(missing)}.")
    if ".prj" not in parts:
        raise UploadError("Shapefile CRS is missing (.prj file required).")
    return shapefile


def read_upload(upload: UploadFile) -> gpd.GeoDataFrame:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".kml", ".zip"}:
        raise UploadError("Only .kml and .zip files are accepted.")

    with TemporaryDirectory(prefix=".geo-measure-", dir=PROJECT_ROOT) as temporary:
        directory = Path(temporary)
        path = directory / f"upload{extension}"
        size = 0
        with path.open("wb") as destination:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise UploadError("Upload exceeds 20 MB.")
                destination.write(chunk)

        if extension == ".zip":
            extracted = directory / "extracted"
            extracted.mkdir()
            path = extract_shapefile(path, extracted)

        try:
            frame = gpd.read_file(path, engine="pyogrio")
        except (DataSourceError, ValueError) as exc:
            raise UploadError("Geospatial file cannot be read.") from exc

        if extension == ".kml":
            return frame.set_crs("EPSG:4326", allow_override=True)
        if frame.crs is None:
            raise UploadError("Shapefile CRS is missing or invalid.")
        return frame
