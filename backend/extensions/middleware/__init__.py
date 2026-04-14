from extensions.middleware.audit_middleware import MIDDLEWARE as AUDIT_MIDDLEWARE

MIDDLEWARE = [*AUDIT_MIDDLEWARE]

__all__ = ["MIDDLEWARE"]
