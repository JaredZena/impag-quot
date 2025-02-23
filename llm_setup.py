import openai
from config import openai_api_key
from llama_index.core import VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_pipeline import QueryPipeline
from llama_index.llms.openai import OpenAI

Settings.llm = OpenAI(model="gpt-4")
vector_index = VectorStoreIndex.from_documents([])
retriever = VectorIndexRetriever(index=vector_index, similarity_top_k=5)
llm = OpenAI(model="gpt-4", api_key=openai_api_key)
query_pipeline = QueryPipeline(
    modules={"retriever": retriever, "llm": llm},
    pipeline=["retriever", "llm"],
)
