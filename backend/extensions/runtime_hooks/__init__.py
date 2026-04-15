from extensions.runtime_hooks.attachment_hooks import RUN_INPUT_HOOKS as ATTACHMENT_RUN_INPUT_HOOKS
from extensions.runtime_hooks.attachment_hooks import UPLOAD_HOOKS
from extensions.runtime_hooks.session_state_hooks import (
    RUN_INPUT_HOOKS as SESSION_STATE_RUN_INPUT_HOOKS,
)

RUN_INPUT_HOOKS = [*SESSION_STATE_RUN_INPUT_HOOKS, *ATTACHMENT_RUN_INPUT_HOOKS]

__all__ = ["RUN_INPUT_HOOKS", "UPLOAD_HOOKS"]
