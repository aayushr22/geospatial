import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "geo-measure.db"
engine = create_engine(
    os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH}"),
    connect_args={"check_same_thread": False},
)
Base = declarative_base()


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(
        String(12), primary_key=True, default=lambda: uuid4().hex[:12]
    )
    filename: Mapped[str]
    crs: Mapped[str | None]
    feature_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="PROCESSING")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Feature(Base):
    __tablename__ = "features"

    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), primary_key=True)
    feature_index: Mapped[int] = mapped_column(primary_key=True)
    geometry_type: Mapped[str | None]
    geometry: Mapped[str] = mapped_column(Text)
    properties: Mapped[dict] = mapped_column(JSON)
    measurement_type: Mapped[str | None]
    measurement_value: Mapped[float | None]
    measurement_unit: Mapped[str | None]
    measurement_crs: Mapped[str | None]
    note: Mapped[str | None]


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
