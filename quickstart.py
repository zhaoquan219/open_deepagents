import os
from typing import Literal

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from tavily import TavilyClient


load_dotenv()

DEFAULT_PROMPT = os.getenv("DEEPAGENTS_PROMPT", "Explain what LangGraph is in a concise paragraph.")


def build_model():
    custom_api_key = os.getenv("CUSTOM_API_KEY")
    custom_api_url = os.getenv("CUSTOM_API_URL")
    custom_api_model = os.getenv("CUSTOM_API_MODEL")

    if custom_api_key and custom_api_url and custom_api_model:
        normalized_base_url = custom_api_url.rstrip("/")
        if normalized_base_url.endswith("/chat/completions"):
            normalized_base_url = normalized_base_url[: -len("/chat/completions")]
        elif not normalized_base_url.endswith("/v1"):
            normalized_base_url = f"{normalized_base_url}/v1"

        return ChatOpenAI(
            model=custom_api_model,
            api_key=custom_api_key,
            base_url=normalized_base_url,
            temperature=0,
        )

    return os.getenv("DEEPAGENTS_MODEL", "openai:gpt-5.4")


def build_tools():
    tavily_api_key = os.getenv("TAVILY_API_KEY")
    if not tavily_api_key:
        return []

    tavily_client = TavilyClient(api_key=tavily_api_key)

    def internet_search(
        query: str,
        max_results: int = 5,
        topic: Literal["general", "news", "finance"] = "general",
        include_raw_content: bool = False,
    ):
        """Run a web search with Tavily."""
        return tavily_client.search(
            query=query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
        )

    return [internet_search]


tools = build_tools()
model = build_model()

research_instructions = """You are an expert researcher.
Your job is to conduct thorough research and then write a polished report.
"""

if tools:
    research_instructions += """
You have access to an internet search tool as your primary means of gathering information.
Use `internet_search` when you need current information from the web.
"""
else:
    research_instructions += """
No external search tool is configured in this environment.
Rely on the model's built-in knowledge unless the user provides more context.
"""

agent = create_deep_agent(
    model=model,
    tools=tools,
    system_prompt=research_instructions,
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": DEFAULT_PROMPT}]}
)

print(result["messages"][-1].content)
