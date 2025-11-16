from config import claude_api_key
from llama_index.llms.anthropic import Anthropic

# Set up LlamaIndex Anthropic wrapper - Using Haiku for faster and cheaper quotations
llm = Anthropic(model="claude-haiku-4-5-20251001", api_key=claude_api_key, max_tokens=4000)