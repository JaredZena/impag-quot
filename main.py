from dotenv import load_dotenv
import os
import requests
from pinecone import Pinecone
from PyPDF2 import PdfReader
import openai
from bs4 import BeautifulSoup
from llama_index.core import VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_pipeline import QueryPipeline
from llama_index.llms.openai import OpenAI
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from rapidfuzz import process

# Load environment variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
pinecone_api_key = os.getenv("PINECONE_API_KEY")
pinecone_environment = os.getenv("PINECONE_ENV")

# Initialize Pinecone
pc = Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
index_name = "impag"
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=1536,  # Match OpenAI's embedding size
        metric="cosine",
    )

index = pc.Index(index_name)
client = openai.OpenAI()

# Generate embeddings and store in Pinecone
def generate_embeddings(texts):
    response = client.embeddings.create(input=texts, model="text-embedding-ada-002")
    return [item.embedding for item in response.data]

# ✅ Shopify API - Fetch products and all their variants
def fetch_shopify_prices():
    base_url = "https://todoparaelcampo.com.mx/products.json?limit=200"
    response = requests.get(base_url)

    if response.status_code != 200:
        print(f"Error fetching Shopify API: {response.status_code}")
        return {}

    try:
        product_data = response.json()
        products = {}

        for product in product_data.get("products", []):
            title = product["title"].strip().lower()

            # ✅ Loop through all variants to get different specifications
            for variant in product.get("variants", []):
                variant_name = variant["title"].strip().lower()  # e.g., "35%", "50%"
                price = variant["price"]  # Shopify stores prices as strings

                # ✅ Create a unique product name for each variant
                full_product_name = f"{title} {variant_name}" if variant_name != "default title" else title

                products[full_product_name] = f"${price} MXN"

        print(f"✅ Fetched {len(products)} product variants from Shopify")
        return products

    except Exception as e:
        print(f"Error parsing Shopify JSON: {e}")
        return {}
    
# ✅ Function to find the best matching Shopify product names based on query
def find_best_matching_products(query, shopify_prices, threshold=85):
    """
    Uses fuzzy matching to find products that closely match the user's query.
    
    :param query: The user's input query.
    :param shopify_prices: Dictionary of product names and prices.
    :param threshold: Minimum match score (0-100) to consider a valid match.
    :return: A dictionary of best matching products with prices.
    """
    matched_products = {}

    for product in shopify_prices.keys():
        similarity_score = process.extractOne(query, [product])[1]  # Get best similarity score
        if similarity_score >= threshold:
            matched_products[product] = shopify_prices[product]  # Store matched product & price

    return matched_products

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
    print(f'🔹 Query Received: {query}')
    query_embedding = generate_embeddings([query])[0]
    results = index.query(vector=query_embedding, top_k=5, include_metadata=True)
    context = " ".join([match["metadata"]["text"] for match in results["matches"]])

    # ✅ Fetch updated Shopify product prices
    shopify_prices = fetch_shopify_prices()

    # ✅ Find the best matching products based on query (fuzzy matching)
    matched_products = find_best_matching_products(query.lower(), shopify_prices)

    # ✅ Format the matched prices into a response
    if matched_products:
        price_context = "\n".join([f"{product}: {price}" for product, price in matched_products.items()])
    else:
        price_context = "No se encontraron precios actualizados para los productos solicitados."

    print(f'🔹 Matched Products: {matched_products}')

    prompt = (f"Genera una cotización de Impag basada en el catálogo de productos y cotizaciones previas, "
              f"asegurate de incluir especificaciones completas de los productos, en la descripcion. "
              f"Si el usuario proporciona un término general (ej. geomembranas, sistemas de riego, drones agrícolas), "
              f"genera múltiples opciones con diferentes tipos, especificaciones y precios cuando estén disponibles. "
              f"Si el usuario proporciona datos tecnicos para calcular los productos (ej. acolchado agricola para dos hectareas), " 
              f"basate en otras cotizaciones para realizar los calculos y en metododologias de calculo, proporcionadas en el contexto. "
              f"Si el usuario especifica un producto con detalles exactos (ej. modelo, capacidad, dimensiones), "
              f"solo incluye ese producto en la cotización, con descripcion y especificaciones detalladas y precio. "
              f"Usa los precios de la tienda online o de cotizaciones previas para determinar precios y especificaciones, "
              f"y solo si no hay referencias, deja el precio en blanco. Responde en español.\n\n"
              f"🔹 **Precios actuales en tienda online:**\n{price_context}\n"
              f"🔹 **Contexto adicional:**\n{context}\n\n"
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
