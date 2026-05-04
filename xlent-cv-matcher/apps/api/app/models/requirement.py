import uuid

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), default="should", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    extracted_by: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
