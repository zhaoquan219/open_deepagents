from __future__ import annotations

from langchain.agents.middleware import SummarizationMiddleware

from app.runtime_config import config

MIDDLEWARE = [
    SummarizationMiddleware(
        model=config.model(),
        trigger=("tokens", 100_000),
        keep=("messages", 20),
    )
]
