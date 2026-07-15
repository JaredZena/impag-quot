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

# WhatsApp Cloud API (Meta)
wa_phone_number_id = os.getenv("WA_PHONE_NUMBER_ID")
wa_business_account_id = os.getenv("WA_BUSINESS_ACCOUNT_ID")
wa_access_token = os.getenv("WA_ACCESS_TOKEN")
wa_app_id = os.getenv("WA_APP_ID")
wa_app_secret = os.getenv("WA_APP_SECRET")
wa_verify_token = os.getenv("WA_VERIFY_TOKEN")
wa_graph_version = os.getenv("WA_GRAPH_VERSION", "v21.0")
# Opt-in escape hatch for LOCAL sandbox testing without an app secret. In any
# real deploy WA_APP_SECRET must be set — the webhook fails closed otherwise.
wa_allow_unsigned_webhook = os.getenv("WA_ALLOW_UNSIGNED_WEBHOOK", "false").lower() == "true"
# HARD GATE: no message is ever sent to a customer while this is false. Approvals
# still work and are recorded — they just don't hit the Cloud API. Flip to true
# only after explicit sign-off (and a permanent access token + real number).
wa_sending_enabled = os.getenv("WA_SENDING_ENABLED", "false").lower() == "true"