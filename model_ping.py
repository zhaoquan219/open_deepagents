import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


load_dotenv()

raw_url = os.environ["CUSTOM_API_URL"].rstrip("/")
if raw_url.endswith("/chat/completions"):
    base_url = raw_url[: -len("/chat/completions")]
elif raw_url.endswith("/v1"):
    base_url = raw_url
else:
    base_url = f"{raw_url}/v1"

model = ChatOpenAI(
    model=os.environ["CUSTOM_API_MODEL"],
    api_key=os.environ["CUSTOM_API_KEY"],
    base_url=base_url,
    temperature=0,
)

response = model.invoke("Reply with exactly: custom api ok")
print(response.content)
