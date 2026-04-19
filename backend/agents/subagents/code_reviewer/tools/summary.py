from langchain_core.tools import tool


@tool
def summarize_review_scope(scope: str) -> str:
    """Return a short code-review scope label."""

    return f"review-scope:{scope.strip()}"


TOOLS = [summarize_review_scope]

