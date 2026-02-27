from dotenv import load_dotenv
import os

load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
pinecone_api_key = os.getenv("PINECONE_API_KEY")
pinecone_environment = os.getenv("PINECONE_ENV")
claude_api_key = os.getenv("CLAUDE_API_KEY")
database_url = os.getenv("DATABASE_URL")

# Cloudflare R2 Storage
r2_account_id = os.getenv("R2_ACCOUNT_ID")
r2_access_key_id = os.getenv("R2_ACCESS_KEY_ID")
r2_secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")
r2_bucket_name = os.getenv("R2_BUCKET_NAME", "impag-files")