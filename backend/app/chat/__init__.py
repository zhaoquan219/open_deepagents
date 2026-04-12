"""Chat/message scaffold surface."""

from app.db.models import MessageRecord
from app.schemas.message import MessageCreate, MessageRead, MessageUpdate

__all__ = ["MessageCreate", "MessageRead", "MessageRecord", "MessageUpdate"]
