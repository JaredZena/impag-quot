import openai
from config import openai_api_key

client = openai.OpenAI()

def generate_embeddings(texts):
    response = client.embeddings.create(input=texts, model="text-embedding-ada-002")
    return [item.embedding for item in response.data]
