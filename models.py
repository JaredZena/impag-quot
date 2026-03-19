from sqlalchemy import Column, Integer, SmallInteger, String, DateTime, Float, ForeignKey, create_engine, Boolean, Text, Numeric, JSON, Enum, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from config import database_url
from urllib.parse import urlparse, parse_qs, urlencode
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
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
    embedding = Column(Vector(1536)) # Embedding vector for semantic search
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
    product_id = Column(Integer, ForeignKey("product.id", ondelete="CASCADE"))  # Keep for now, will be removed later
    
    # ===== Product columns (NEW - copied from Product table) =====
    name = Column(String, index=True, nullable=True)
    description = Column(Text, nullable=True)
    base_sku = Column(String(50), nullable=True)
    sku = Column(String(100), nullable=True)
    category_id = Column(Integer, ForeignKey("product_category.id"), nullable=True)
    unit = Column(String(50), nullable=True)  # Store as string for flexibility
    package_size = Column(Integer, nullable=True)
    iva = Column(Boolean, default=True, nullable=True)
    specifications = Column(JSON, nullable=True)
    # Note: No calculated_price columns - we'll calculate dynamically

    # ===== Supplier-specific columns (existing) =====
    supplier_sku = Column(String(100), nullable=True)
    cost = Column(Numeric(10, 2), nullable=True)
    default_margin = Column(Numeric(5, 4), nullable=True)  # Margin as decimal (0.25 = 25%). Formula: price = cost / (1 - margin)
    currency = Column(String(3), default='MXN', nullable=False)  # Currency of cost and shipping costs (MXN or USD)
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
    embedding = Column(Vector(1536)) # Embedding vector for semantic search
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)  # Soft delete timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="products")
    product = relationship("Product", back_populates="supplier_products")
    category = relationship("ProductCategory", foreign_keys=[category_id])

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
    product_id = Column(Integer, ForeignKey("product.id", ondelete="CASCADE"))  # Keep for backward compatibility
    supplier_product_id = Column(Integer, ForeignKey("supplier_product.id", ondelete="CASCADE"))  # NEW - primary reference
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=True)  # Optional override price
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    kit = relationship("Kit", back_populates="items")
    product = relationship("Product")  # Keep for backward compatibility
    supplier_product = relationship("SupplierProduct")  # NEW - primary relationship

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
    product_id = Column(Integer, ForeignKey("product.id", ondelete="CASCADE"))  # Keep for backward compatibility
    supplier_id = Column(Integer, ForeignKey("supplier.id", ondelete="CASCADE"))  # Keep for backward compatibility
    supplier_product_id = Column(Integer, ForeignKey("supplier_product.id", ondelete="CASCADE"))  # NEW - primary reference
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_cost = Column(Numeric(10, 2), nullable=False)  # (unit_price + calculated_shipping) * quantity
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    balance = relationship("Balance", back_populates="items")
    product = relationship("Product")  # Keep for backward compatibility
    supplier = relationship("Supplier")  # Keep for backward compatibility
    supplier_product = relationship("SupplierProduct")  # NEW - primary relationship

# RAG Query tracking model
class Query(Base):
    __tablename__ = "query"
    
    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Conversation(Base):
    __tablename__ = "conversation"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    user_id = Column(Integer, nullable=True)  # For future user tracking
    is_active = Column(Boolean, default=True)
    
    messages = relationship("ConversationMessage", back_populates="conversation", cascade="all, delete-orphan")

class ConversationMessage(Base):
    __tablename__ = "conversation_message"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversation.id", ondelete="CASCADE"))
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    conversation = relationship("Conversation", back_populates="messages")

class Quotation(Base):
    __tablename__ = "quotation"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)  # Google user ID (sub)
    user_email = Column(String, nullable=False, index=True)  # User email for easier querying
    user_query = Column(Text, nullable=False)  # Original query that generated the quotation
    title = Column(String(500), nullable=True)  # Optional title (can be auto-generated)
    customer_name = Column(String(200), nullable=True)  # Customer name
    customer_location = Column(String(200), nullable=True)  # Customer location
    quotation_id = Column(String(50), nullable=True)  # Generated quotation ID
    internal_quotation = Column(Text, nullable=False)  # Internal quotation markdown
    customer_quotation = Column(Text, nullable=False)  # Customer-facing quotation markdown
    raw_response = Column(Text, nullable=True)  # Full raw response from AI
    archived_at = Column(DateTime(timezone=True), nullable=True)  # Soft delete timestamp
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
class SocialPost(Base):
    __tablename__ = "social_post"

    id = Column(Integer, primary_key=True, index=True)
    date_for = Column(Date, nullable=False) # Target date (migrated from VARCHAR to DATE)
    caption = Column(Text, nullable=False)
    image_prompt = Column(Text, nullable=True)
    post_type = Column(String(50), nullable=True)
    content_tone = Column(String(50), nullable=True, index=True) # Content tone: Motivational, Technical, Humor, Educational, Inspirational, etc.
    status = Column(String(20), default="planned") # planned, posted, archived
    selected_product_id = Column(String, nullable=True)
    formatted_content = Column(JSON, nullable=True) # Full JSON response (migrated to JSONB in DB)
    external_id = Column(String(255), nullable=True, index=True) # External ID for efficient lookups (e.g., from formatted_content.id)
    # Channel-specific fields
    channel = Column(String(50), nullable=True) # wa-status, fb-post, tiktok, etc.
    carousel_slides = Column(JSON, nullable=True) # Array of slide prompts for carousels (TikTok, FB/IG)
    needs_music = Column(Boolean, default=False) # Whether this content needs background music
    user_feedback = Column(String(20), nullable=True) # 'like', 'dislike', or None
    # Topic tracking fields (used for same-day duplicate detection)
    topic = Column(Text, nullable=False) # Topic in format "Problema → Solución" (NOT NULL after migration)
    problem_identified = Column(Text, nullable=True) # Problem description from strategy phase
    topic_hash = Column(String(64), nullable=False, index=True) # SHA256 hash of normalized topic (NOT NULL after migration) - used to detect same topic on same date
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class FileMetadata(Base):
    __tablename__ = "file_metadata"

    id = Column(Integer, primary_key=True, index=True)
    file_key = Column(String(500), nullable=False, unique=True)
    original_filename = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)

    category = Column(String(50), nullable=False)
    subtype = Column(String(50), nullable=True)
    document_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(String(500), nullable=True)

    supplier_id = Column(Integer, ForeignKey("supplier.id", ondelete="SET NULL"), nullable=True)
    quotation_id = Column(Integer, ForeignKey("quotation.id", ondelete="SET NULL"), nullable=True)
    task_id = Column(Integer, nullable=True)  # FK constraint added at DB level (task table is in impag-tasks)

    uploaded_by_email = Column(String(255), nullable=False)
    uploaded_by_name = Column(String(200), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    # Document processing fields
    processing_status = Column(String(20), default='pending')  # pending, processing, completed, failed, skipped
    extracted_text = Column(Text, nullable=True)
    chunk_count = Column(Integer, default=0)
    processing_error = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunk"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("file_metadata.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    pinecone_vector_id = Column(String(200), nullable=False, unique=True)
    token_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LogisticsMetadata(Base):
    __tablename__ = "logistics_metadata"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("file_metadata.id", ondelete="CASCADE"), nullable=False)

    product_name = Column(String(300), nullable=True)
    quantity = Column(Integer, nullable=True)
    package_size = Column(String(100), nullable=True)
    package_type = Column(String(100), nullable=True)
    weight_kg = Column(Numeric(10, 2), nullable=True)
    dimensions = Column(String(100), nullable=True)

    origin = Column(String(300), nullable=True)
    destination = Column(String(300), nullable=True)
    carrier = Column(String(200), nullable=True)
    tracking_number = Column(String(200), nullable=True)
    estimated_delivery = Column(Date, nullable=True)

    cost = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), default='MXN')

    supplier_product_id = Column(Integer, ForeignKey("supplier_product.id", ondelete="SET NULL"), nullable=True)
    supplier_id = Column(Integer, ForeignKey("supplier.id", ondelete="SET NULL"), nullable=True)

    extraction_confidence = Column(String(20), default='medium')
    raw_extraction = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())


# --- Task Management Models ---

class TaskUser(Base):
    __tablename__ = "task_user"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    avatar_url = Column(String(500), nullable=True)
    role = Column(String(20), default="member", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    tasks_created = relationship("Task", foreign_keys="Task.created_by", back_populates="creator")
    tasks_assigned = relationship("Task", foreign_keys="Task.assigned_to", back_populates="assignee")
    comments = relationship("TaskComment", back_populates="user")
    categories_created = relationship("TaskCategory", back_populates="creator")


class TaskCategory(Base):
    __tablename__ = "task_category"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    color = Column(String(7), default="#6366f1", nullable=False)
    icon = Column(String(50), nullable=True)
    created_by = Column(Integer, ForeignKey("task_user.id"), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    creator = relationship("TaskUser", back_populates="categories_created")
    tasks = relationship("Task", back_populates="category")


class Task(Base):
    __tablename__ = "task"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default="pending", nullable=False, index=True)
    priority = Column(String(10), default="medium", nullable=False)
    due_date = Column(Date, nullable=True)
    category_id = Column(Integer, ForeignKey("task_category.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("task_user.id"), nullable=False, index=True)
    assigned_to = Column(Integer, ForeignKey("task_user.id"), nullable=True, index=True)
    task_number = Column(SmallInteger, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    creator = relationship("TaskUser", foreign_keys=[created_by], back_populates="tasks_created")
    assignee = relationship("TaskUser", foreign_keys=[assigned_to], back_populates="tasks_assigned")
    category = relationship("TaskCategory", back_populates="tasks")
    comments = relationship("TaskComment", back_populates="task", cascade="all, delete-orphan",
                            order_by="TaskComment.created_at.asc()")


class TaskComment(Base):
    __tablename__ = "task_comment"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("task_user.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    task = relationship("Task", back_populates="comments")
    user = relationship("TaskUser", back_populates="comments")


def get_current_task_user(db, email: str):
    """Get the TaskUser record for the given email."""
    return db.query(TaskUser).filter(
        TaskUser.email == email,
        TaskUser.is_active == True
    ).first()


def get_next_task_number(db):
    """Find the lowest available task_number (1-based) not used by any active task."""
    used = {r[0] for r in db.query(Task.task_number).filter(Task.task_number.isnot(None)).all()}
    n = 1
    while n in used:
        n += 1
    return n


# ==================== Quote Commerce Models ====================

class Quote(Base):
    __tablename__ = "quote"

    id = Column(Integer, primary_key=True, index=True)
    quote_number = Column(String(20), unique=True, nullable=False)  # Format: TEC-2026-0001

    # Status: draft, sent, viewed, accepted, rejected, expired
    status = Column(String(20), nullable=False, default="draft")

    # Customer info
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(30), nullable=False)
    customer_email = Column(String(255), nullable=True)
    customer_location = Column(String(300), nullable=True)

    # Quote content
    notes = Column(Text, nullable=True)
    validity_days = Column(Integer, nullable=False, default=15)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    iva_amount = Column(Numeric(12, 2), nullable=False, default=0)
    total = Column(Numeric(12, 2), nullable=False, default=0)

    # Lifecycle timestamps
    sent_at = Column(DateTime(timezone=True), nullable=True)
    viewed_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    expired_at = Column(DateTime(timezone=True), nullable=True)

    # Ownership
    created_by = Column(String(255), nullable=False)  # engineer email
    assigned_to = Column(String(255), nullable=True)   # engineer email

    # Public access
    access_token = Column(String(36), unique=True, nullable=True)  # UUID v4, generated on send

    # Future-proofing (Phase 3 payment)
    payment_status = Column(String(20), nullable=True)
    payment_method = Column(String(50), nullable=True)
    payment_reference = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    items = relationship("QuoteItem", back_populates="quote", cascade="all, delete-orphan", order_by="QuoteItem.sort_order")


class QuoteItem(Base):
    __tablename__ = "quote_item"

    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quote.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("product.id"), nullable=True)  # null for freeform items
    supplier_product_id = Column(Integer, ForeignKey("supplier_product.id"), nullable=True)

    description = Column(String(500), nullable=False)  # product name or freeform description
    sku = Column(String(100), nullable=True)
    quantity = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(50), nullable=True)
    unit_price = Column(Numeric(12, 2), nullable=False)
    iva_applicable = Column(Boolean, default=True)
    discount_percent = Column(Numeric(5, 2), nullable=True)  # Future: line-item discount
    discount_amount = Column(Numeric(12, 2), nullable=True)  # Future: line-item discount
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    quote = relationship("Quote", back_populates="items")


class Notification(Base):
    __tablename__ = "notification"

    id = Column(Integer, primary_key=True, index=True)
    recipient_email = Column(String(255), nullable=False, index=True)  # engineer email
    quote_id = Column(Integer, ForeignKey("quote.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(30), nullable=False)  # quote_viewed, quote_accepted
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


def get_next_quote_number(db):
    """Generate the next quote number in format TEC-{YEAR}-{XXXX}."""
    import datetime
    year = datetime.datetime.now().year
    prefix = f"TEC-{year}-"
    last = db.query(Quote).filter(
        Quote.quote_number.like(f"{prefix}%")
    ).order_by(Quote.quote_number.desc()).first()
    if last:
        last_num = int(last.quote_number.split("-")[-1])
        return f"{prefix}{last_num + 1:04d}"
    return f"{prefix}0001"


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