"""Authentication scaffold surface."""

from app.core.auth import create_access_token, decode_access_token, verify_admin_credentials

__all__ = ["create_access_token", "decode_access_token", "verify_admin_credentials"]
