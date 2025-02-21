from dotenv import load_dotenv
import os
import requests
from bs4 import BeautifulSoup
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
        metric="cosine",
    )

# Connect to the existing Pinecone index
index = pc.Index(index_name)

client = openai.OpenAI()  # Create an OpenAI client

# Generate embeddings and store in Pinecone
def generate_embeddings(texts):
    response = client.embeddings.create(input=texts, model="text-embedding-ada-002")
    return [item.embedding for item in response.data]

# Function to scrape product prices from Shopify store
def fetch_shopify_prices():
    base_url = "https://todoparaelcampo.com.mx/collections"
    response = requests.get(base_url)

    if response.status_code != 200:
        print(f"Error fetching Shopify store: {response.status_code}")
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    products = {}

    # Find all product listings
    product_containers = soup.find_all("div", class_="product-card")

    for product in product_containers:
        title_tag = product.find("h2", class_="product-card__title")
        price_tag = product.find("span", class_="price")

        if title_tag and price_tag:
            product_name = title_tag.text.strip()
            product_price = price_tag.text.strip()
            products[product_name.lower()] = product_price

    return products

# Set up LlamaIndex and RAG pipeline
Settings.llm = OpenAI(model="gpt-4")
vector_index = VectorStoreIndex.from_documents([])
retriever = VectorIndexRetriever(index=vector_index, similarity_top_k=5)
llm = OpenAI(model="gpt-4", api_key=openai_api_key)
query_pipeline = QueryPipeline(
    modules={"retriever": retriever, "llm": llm},
    pipeline=["retriever", "llm"],
)

# Query the RAG system with real-time pricing
def query_rag_system(query):
    query_embedding = generate_embeddings([query])[0]
    results = index.query(vector=query_embedding, top_k=5, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    # Fetch real-time prices from Shopify
    shopify_prices = fetch_shopify_prices()

    # Match relevant products from Shopify store
    price_context = ""
    for product, price in shopify_prices.items():
        if product in query.lower():
            price_context += f"{product}: {price}\n"

    prompt = (f"Genera una cotización de Impag basada en el catálogo de productos y cotizaciones previas. "
              f"Si el usuario proporciona un término general (ej. geomembranas, sistemas de riego, drones agrícolas), "
              f"genera múltiples opciones con diferentes tipos, especificaciones y precios cuando estén disponibles. "
              f"Si el usuario especifica un producto con detalles exactos (ej. modelo, capacidad, dimensiones), "
              f"solo incluye ese producto en la cotización. Usa cotizaciones previas para determinar precios, "
              f"y si no hay referencias, deja el precio en blanco. Responde en español.\n\n"
              f"Precios actuales en tienda online:\n{price_context}\n"
              f"Contexto adicional:\n{context}\n\n"
              f"Pregunta: {query}")

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
    response = query_rag_system(request.query)
    return {"response": response}
