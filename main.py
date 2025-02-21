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
from fastapi.middleware.cors import CORSMiddleware

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

client = openai.OpenAI()  # Create an OpenAI client

# Generate embeddings and store in Pinecone
def generate_embeddings(texts):
    response = client.embeddings.create(input=texts, model="text-embedding-ada-002")
    return [item.embedding for item in response.data]  # Use `.embedding` instead of `["embedding"]`

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
    
    prompt = (f"Genera una cotización de Impag basada en el catálogo de productos y cotizaciones previas. "
            f"Si el usuario proporciona un término general (ej. geomembranas, sistemas de riego, drones agrícolas), "
            f"genera múltiples opciones con diferentes tipos, especificaciones y precios cuando estén disponibles. "
            f"Si el usuario especifica un producto con detalles exactos (ej. modelo, capacidad, dimensiones), "
            f"solo incluye ese producto en la cotización. Usa cotizaciones previas para determinar precios, "
            f"y si no hay referencias, deja el precio en blanco. Responde en español.\n\n"
            f"Context: {context}\n\nQuestion: {query}")

    response = llm.complete(prompt)
    return response.text

# FastAPI app
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://impag-quot-rkwav5gwd-jaredzenas-projects.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define a request model
class QueryRequest(BaseModel):
    query: str

@app.post("/query")
async def query(request: QueryRequest):
    response = query_rag_system(request.query)  # Access query from the request object
    return {"response": response}