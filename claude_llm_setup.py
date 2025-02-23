from config import claude_api_key
from anthropic import Anthropic as AnthropicClient, HUMAN_PROMPT, AI_PROMPT
from llama_index.core import VectorStoreIndex
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_pipeline import QueryPipeline
from llama_index.llms.anthropic import Anthropic

# Initialize Anthropic client
anthropic_client = AnthropicClient(api_key=claude_api_key)

# Set up LlamaIndex
vector_index = VectorStoreIndex.from_documents([])
retriever = VectorIndexRetriever(index=vector_index, similarity_top_k=5)
llm = Anthropic(model="claude-3-5-sonnet-20241022", api_key=claude_api_key)
query_pipeline = QueryPipeline(
    modules={"retriever": retriever, "llm": llm},
    pipeline=["retriever", "llm"],
)
