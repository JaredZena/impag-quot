from pinecone import Pinecone
import os
from config import pinecone_api_key

pc = Pinecone(api_key=pinecone_api_key)
index_name = "impag"
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=1536,
        metric="cosine",
    )

index = pc.Index(index_name)
