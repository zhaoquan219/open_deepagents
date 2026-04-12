def echo_tool(text: str) -> str:
    """Sample custom tool extension for the scaffold."""
    return f"echo:{text}"


TOOLS = [echo_tool]
