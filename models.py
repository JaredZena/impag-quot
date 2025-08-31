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
    PAQUETE = "PAQUETE"
    KIT = "KIT"

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
    embedded = Column(Boolean, default=False, nullable=False)  # Whether this supplier has been embedded to embeddings database
    archived_at = Column(DateTime(timezone=True), nullable=True)  # Soft delete timestamp
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
    # New columns from ProductVariant
    sku = Column(String(100), unique=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0)
    specifications = Column(JSON, nullable=True)
    default_margin = Column(Numeric(5, 4), nullable=True)  # Default margin as decimal (0.25 = 25%)
    calculated_price = Column(Numeric(10, 2), nullable=True)  # Cached calculated price for performance
    calculated_price_updated_at = Column(DateTime(timezone=True), nullable=True)  # When calculated price was last updated
    embedded = Column(Boolean, default=False, nullable=False)  # Whether this product has been embedded to embeddings database
    is_active = Column(Boolean, default=True, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)  # Soft delete timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    category = relationship("ProductCategory", back_populates="products")
    supplier_products = relationship("SupplierProduct", back_populates="product")

class SupplierProduct(Base):
    __tablename__ = "supplier_product"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("supplier.id"))
    product_id = Column(Integer, ForeignKey("product.id", ondelete="CASCADE"))
    supplier_sku = Column(String(100), nullable=True)
    cost = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0)
    lead_time_days = Column(Integer, nullable=True)
    shipping_cost = Column(Numeric(10, 2), nullable=True)  # Legacy shipping cost (deprecated)
    shipping_cost_direct = Column(Numeric(10, 2), default=0.00, nullable=False)  # Direct shipping cost per unit
    shipping_method = Column(String(20), default='DIRECT', nullable=False)  # DIRECT or OCURRE shipping method
    shipping_stage1_cost = Column(Numeric(10, 2), default=0.00)  # Stage 1 shipping cost
    shipping_stage2_cost = Column(Numeric(10, 2), default=0.00)  # Stage 2 shipping cost
    shipping_stage3_cost = Column(Numeric(10, 2), default=0.00)  # Stage 3 shipping cost
    shipping_stage4_cost = Column(Numeric(10, 2), default=0.00)  # Stage 4 shipping cost
    shipping_notes = Column(Text, nullable=True)  # Shipping logistics notes
    embedded = Column(Boolean, default=False, nullable=False)  # Whether this supplier product has been embedded to embeddings database
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)  # Soft delete timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="products")
    product = relationship("Product", back_populates="supplier_products")

class Kit(Base):
    __tablename__ = "kit"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    sku = Column(String(100), unique=True, nullable=False)
    price = Column(Numeric(10, 2), nullable=True)
    margin = Column(Numeric(5, 4), nullable=True)  # Margin as decimal (0.25 = 25%)
    is_active = Column(Boolean, default=True, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship to kit items
    items = relationship("KitItem", back_populates="kit", cascade="all, delete-orphan")

class KitItem(Base):
    __tablename__ = "kit_item"

    id = Column(Integer, primary_key=True, index=True)
    kit_id = Column(Integer, ForeignKey("kit.id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("product.id", ondelete="CASCADE"))
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=True)  # Optional override price
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    kit = relationship("Kit", back_populates="items")
    product = relationship("Product")

class Balance(Base):
    __tablename__ = "balance"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    balance_type = Column(String(20), default='QUOTATION', nullable=False)  # QUOTATION, COMPARISON, ANALYSIS
    total_amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), default='MXN', nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship to balance items
    items = relationship("BalanceItem", back_populates="balance", cascade="all, delete-orphan")

class BalanceItem(Base):
    __tablename__ = "balance_item"

    id = Column(Integer, primary_key=True, index=True)
    balance_id = Column(Integer, ForeignKey("balance.id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("product.id", ondelete="CASCADE"))
    supplier_id = Column(Integer, ForeignKey("supplier.id", ondelete="CASCADE"))
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False)
    shipping_cost = Column(Numeric(10, 2), default=0.00, nullable=False)
    total_cost = Column(Numeric(10, 2), nullable=False)  # (unit_price + shipping) * quantity
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    balance = relationship("Balance", back_populates="items")
    product = relationship("Product")
    supplier = relationship("Supplier")

# Query model removed - RAG functionality moved to separate microservice

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