from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, create_engine, Boolean, Text, Numeric, JSON, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from config import database_url
from urllib.parse import urlparse, parse_qs, urlencode
from sqlalchemy.sql import func
import enum

Base = declarative_base()

class ProductUnit(enum.Enum):
    PIEZA = "PIEZA"
    ROLLO = "ROLLO"
    METRO = "METRO"
    KG = "KG"

class Supplier(Base):
    __tablename__ = "supplier"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    common_name = Column(String, nullable=True)  # Common name or trading name of the supplier
    legal_name = Column(String, nullable=True)  # Legal/registered name of the supplier
    rfc = Column(String, index=True, nullable=True)  # RFC is optional and indexed for faster lookups
    description = Column(Text, nullable=True)
    contact_name = Column(String, nullable=True)  # Full name of the contact person
    contact_common_name = Column(String, nullable=True)  # Common name/nickname of the contact person
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    website_url = Column(String, nullable=True)  # Website URL of the supplier
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    products = relationship("SupplierProduct", back_populates="supplier")

class ProductCategory(Base):
    __tablename__ = "product_category"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    products = relationship("Product", back_populates="category")

class Product(Base):
    __tablename__ = "product"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(Text, nullable=True)
    base_sku = Column(String(50), nullable=True)
    category_id = Column(Integer, ForeignKey("product_category.id"))
    unit = Column(Enum(ProductUnit), nullable=False, default=ProductUnit.PIEZA)
    package_size = Column(Integer, nullable=True)  # Number of units in a package (e.g., 1000 pieces per package)
    iva = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    category = relationship("ProductCategory", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product")

class ProductVariant(Base):
    __tablename__ = "product_variant"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("product.id"))
    sku = Column(String(100), unique=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0)
    specifications = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    product = relationship("Product", back_populates="variants")
    supplier_products = relationship("SupplierProduct", back_populates="variant")

class SupplierProduct(Base):
    __tablename__ = "supplier_product"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("supplier.id"))
    variant_id = Column(Integer, ForeignKey("product_variant.id", ondelete="CASCADE"))
    supplier_sku = Column(String(100), nullable=True)
    cost = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0)
    lead_time_days = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="products")
    variant = relationship("ProductVariant", back_populates="supplier_products")

class Query(Base):
    __tablename__ = "query"

    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(Text)
    response_text = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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