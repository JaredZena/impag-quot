from config import claude_api_key
from llama_index.llms.anthropic import Anthropic

# Set up LlamaIndex Anthropic wrapper
llm = Anthropic(model="claude-3-7-sonnet-20250219", api_key=claude_api_key, max_tokens=4000)