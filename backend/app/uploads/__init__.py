"""Upload scaffold surface."""

from app.db.models import UploadRecord
from app.schemas.upload import UploadRead

__all__ = ["UploadRead", "UploadRecord"]
