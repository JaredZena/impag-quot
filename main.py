from dotenv import load_dotenv
import os
from pinecone import Pinecone
from PyPDF2 import PdfReader
import openai
from llama_index.core import VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_pipeline import QueryPipeline
from llama_index.llms.openai import OpenAI
from fastapi import FastAPI
from pydantic import BaseModel

# Load environment variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
pinecone_api_key = os.getenv("PINECONE_API_KEY")
pinecone_environment = os.getenv("PINECONE_ENV")

# Initialize Pinecone
pc = Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
index_name = "impag"
# Check if index exists, if not create it
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=1536,  # Match OpenAI's embedding size
        metric="cosine",  # Use 'euclidean' or 'dotproduct' if preferred
    )

# Connect to the existing Pinecone index
index = pc.Index(index_name)

# Load and chunk PDFs
def load_and_chunk_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    chunks = []
    for page in reader.pages:
        text = page.extract_text()
        chunks.extend(text.split("\n\n"))
    return chunks

pdf_path = "/Users/jared/Documents/impag/Cotizaciones/COT-IMPAG030923DGO M.V.Z. EUGENIO NEVARES- GEOMEMBRANAS.pdf"
chunks = load_and_chunk_pdf(pdf_path)

client = openai.OpenAI()  # Create an OpenAI client

# Generate embeddings and store in Pinecone
def generate_embeddings(texts):
    response = client.embeddings.create(input=texts, model="text-embedding-ada-002")
    return [item.embedding for item in response.data]  # Use `.embedding` instead of `["embedding"]`

# embeddings = generate_embeddings(chunks)
# for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
#     index.upsert([(f"chunk_{i}", embedding, {"text": chunk})])

# Set up LlamaIndex and RAG pipeline
# Configure LlamaIndex settings
Settings.llm = OpenAI(model="gpt-4")
vector_index = VectorStoreIndex.from_documents([])
retriever = VectorIndexRetriever(index=vector_index, similarity_top_k=5)
llm = OpenAI(model="gpt-4", api_key=openai_api_key)
query_pipeline = QueryPipeline(
    modules={"retriever": retriever, "llm": llm},
    pipeline=["retriever", "llm"],
)

# Query the RAG system
def query_rag_system(query):
    query_embedding = generate_embeddings([query])[0]
    results = index.query(vector=query_embedding, top_k=5, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])
    response = llm.complete(f"Context: {context}\n\nQuestion: {query}")
    return response.text

# FastAPI app
app = FastAPI()

# Define a request model
class QueryRequest(BaseModel):
    query: str

@app.post("/query")
async def query(request: QueryRequest):
    response = query_rag_system(request.query)  # Access query from the request object
    return {"response": response}