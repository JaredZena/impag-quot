from sqlalchemy import Column, Integer, String, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from config import database_url
from urllib.parse import urlparse, parse_qs, urlencode

Base = declarative_base()

class Query(Base):
    __tablename__ = "queries"

    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(String, nullable=False)
    response_text = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Parse the database URL to get the endpoint ID
parsed_url = urlparse(database_url)
endpoint_id = parsed_url.hostname.split('.')[0]  # Get the endpoint ID from the hostname

# Add the endpoint ID to the connection options
query_params = parse_qs(parsed_url.query)
query_params['options'] = [f'endpoint={endpoint_id}']
new_query = urlencode(query_params, doseq=True)

# Reconstruct the URL with the new query parameters and explicit psycopg2 driver
modified_url = parsed_url._replace(query=new_query).geturl()
if not modified_url.startswith('postgresql+psycopg2://'):
    modified_url = modified_url.replace('postgresql://', 'postgresql+psycopg2://')

# Database setup with explicit driver configuration
engine = create_engine(
    modified_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args={
        "application_name": "impag-quot"
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
Base.metadata.create_all(bind=engine)

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close() 