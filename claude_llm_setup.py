from config import claude_api_key
from llama_index.llms.anthropic import Anthropic

# Set up LlamaIndex Anthropic wrapper
llm = Anthropic(model="claude-sonnet-4-20250514", api_key=claude_api_key, max_tokens=4000)