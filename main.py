import time

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from models import get_db, Query, Conversation, ConversationMessage, Quotation
from typing import List, Optional
from datetime import datetime
from routes import suppliers, products, quotations
from routes.products import router as products_router
from routes.suppliers import router as suppliers_router
from routes.quotations import router as quotations_router
from routes.categories import router as categories_router
from routes.kits import router as kits_router
from routes.balance import router as balance_router
from routes.quotation_history import router as quotation_history_router
from routes.social import router as social_router
from routes.files import router as files_router
from routes.logistics import router as logistics_router
from routes.tasks_mgmt import router as tasks_mgmt_router
from routes.task_categories import router as task_categories_router
from routes.task_comments import router as task_comments_router
from routes.task_users import router as task_users_router
from routes.quotes import router as quotes_router
from routes.notifications import router as notifications_router
from routes.public_quotes import router as public_quotes_router
from routes.whatsapp import router as whatsapp_router
from routes.roadmap import router as roadmap_router
from routes.customers import router as customers_router
from auth import verify_google_token

# Lazy import for RAG system to ensure route registration even if import fails
def get_rag_query_function():
    """Lazy import of RAG system to handle import errors gracefully."""
    try:
        from rag_system_moved.rag_system import query_rag_system_with_history
        return query_rag_system_with_history
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"RAG system not available: {str(e)}. Please check that all dependencies are installed."
        )

from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

# Serve static files for public quote pages
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://impag-admin-app.vercel.app",  # Production domain Product Manager
        "https://impag-quot-web.vercel.app",  # Production domain Cotizador
        "http://localhost:5173",              # Local development
        "http://localhost:3000",              # Alternative local port
        "https://todoparaelcampo.com.mx",    # New storefront
        "https://www.todoparaelcampo.com.mx" # www variant
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)

# Include routers
app.include_router(products_router)
app.include_router(suppliers_router)
app.include_router(quotations_router)
app.include_router(categories_router)
app.include_router(kits_router)
app.include_router(balance_router)
app.include_router(quotation_history_router)
app.include_router(social_router, prefix="/social", tags=["Social"])
app.include_router(files_router)
app.include_router(whatsapp_router)
app.include_router(roadmap_router)
app.include_router(customers_router)
app.include_router(logistics_router)
app.include_router(tasks_mgmt_router)
app.include_router(task_categories_router)
app.include_router(task_comments_router)
app.include_router(task_users_router)
app.include_router(quotes_router)
app.include_router(notifications_router)
app.include_router(public_quotes_router)

class Message(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str

class QueryRequest(BaseModel):
    query: str
    messages: Optional[List[Message]] = []  # Optional chat history for conversation context
    conversation_id: Optional[int] = None  # Optional conversation ID to save messages
    customer_name: Optional[str] = None  # Customer name for quotation
    customer_location: Optional[str] = None  # Customer location for quotation

class QueryResponse(BaseModel):
    id: int
    query_text: str
    response_text: str
    complexity_tier: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    is_active: bool
    message_count: Optional[int] = 0

    class Config:
        from_attributes = True

class ConversationMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True

class ConversationCreate(BaseModel):
    title: str

@app.post("/conversations", response_model=ConversationResponse)
async def create_conversation(conversation: ConversationCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Create a new conversation."""
    db_conversation = Conversation(title=conversation.title)
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)
    
    return {
        **db_conversation.__dict__,
        "message_count": 0
    }

@app.get("/conversations", response_model=List[ConversationResponse])
async def get_conversations(skip: int = 0, limit: int = 50, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Get all conversations, ordered by most recent."""
    conversations = db.query(Conversation).filter(
        Conversation.is_active == True
    ).order_by(Conversation.updated_at.desc()).offset(skip).limit(limit).all()
    
    # Add message count to each conversation
    result = []
    for conv in conversations:
        message_count = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conv.id
        ).count()
        result.append({
            **conv.__dict__,
            "message_count": message_count
        })
    
    return result

@app.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Get a specific conversation."""
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    message_count = db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id
    ).count()
    
    return {
        **conversation.__dict__,
        "message_count": message_count
    }

@app.get("/conversations/{conversation_id}/messages", response_model=List[ConversationMessageResponse])
async def get_conversation_messages(conversation_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Get all messages in a conversation."""
    messages = db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id
    ).order_by(ConversationMessage.created_at.asc()).all()
    
    return messages

@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Delete a conversation (soft delete)."""
    conversation = db.query(Conversation).filter(
        Conversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    conversation.is_active = False
    db.commit()
    
    return {"message": "Conversation deleted successfully"}

@app.post("/query")
async def query(request: QueryRequest, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    # Lazy import to ensure route is always registered
    query_rag_system_with_history = get_rag_query_function()

    # Convert Pydantic models to dicts for RAG system
    chat_history = [{"role": msg.role, "content": msg.content} for msg in (request.messages or [])]

    # Get response from RAG system with conversation context
    t_start = time.perf_counter()
    try:
        result = query_rag_system_with_history(
            query=request.query,
            chat_history=chat_history,
            customer_name=request.customer_name,
            customer_location=request.customer_location
        )
    except Exception as e:
        error_str = str(e)
        if "credit balance is too low" in error_str or "billing" in error_str.lower():
            raise HTTPException(
                status_code=402,
                detail="ANTHROPIC_CREDITS_EXHAUSTED: Los créditos de la API de Claude se agotaron. Recarga en https://console.anthropic.com/settings/billing"
            )
        raise HTTPException(status_code=500, detail=f"Error generando cotización: {error_str[:300]}")

    response = result['quotation']
    complexity_tier = result.get('complexity_tier')

    # Save query and response to database. Logging must never cost the user a
    # successfully generated quotation (1-3 LLM calls already spent).
    query_id = None
    try:
        db_query = Query(
            query_text=request.query,
            response_text=response,
            complexity_tier=complexity_tier,
            user_email=user.get("email"),
            retrieved_chunk_ids=result.get("retrieved_chunk_ids") or [],
            latency_ms=int((time.perf_counter() - t_start) * 1000),
        )
        db.add(db_query)
        db.commit()
        db.refresh(db_query)
        query_id = db_query.id
    except Exception as e:
        db.rollback()
        print(f"⚠️ Query logging failed (quotation still returned): {e}")
    
    # If conversation_id is provided, save messages to conversation
    if request.conversation_id:
        conversation = db.query(Conversation).filter(
            Conversation.id == request.conversation_id
        ).first()
        
        if conversation:
            # Save user message
            user_message = ConversationMessage(
                conversation_id=request.conversation_id,
                role="user",
                content=request.query
            )
            db.add(user_message)
            
            # Save assistant message
            assistant_message = ConversationMessage(
                conversation_id=request.conversation_id,
                role="assistant",
                content=response
            )
            db.add(assistant_message)
            
            # Update conversation timestamp
            conversation.updated_at = datetime.utcnow()
            db.commit()
    
    return {"response": response, "complexity_tier": complexity_tier,
            "conversation_id": request.conversation_id, "query_id": query_id,
            "quote_candidates": result.get("quote_candidates") or []}


class QueryFeedbackRequest(BaseModel):
    feedback: int  # 1 = útil, -1 = no útil
    feedback_text: Optional[str] = Field(default=None, max_length=2000)


@app.post("/queries/{query_id}/feedback")
async def submit_query_feedback(
    query_id: int,
    request: QueryFeedbackRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Record thumbs up/down (and optional correction) on a generated quotation."""
    if request.feedback not in (1, -1):
        raise HTTPException(status_code=422, detail="feedback must be 1 or -1")
    db_query = db.query(Query).filter(Query.id == query_id).first()
    if not db_query:
        raise HTTPException(status_code=404, detail="Query not found")
    db_query.feedback = request.feedback
    db_query.feedback_text = request.feedback_text
    db_query.feedback_by = user.get("email")
    db_query.feedback_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "query_id": query_id, "feedback": request.feedback}

@app.get("/queries", response_model=List[QueryResponse])
async def get_queries(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    queries = db.query(Query).order_by(Query.created_at.desc()).offset(skip).limit(limit).all()
    return queries

@app.get("/")
def read_root():
    return {"message": "Welcome to the Quotation System API"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "impag-quot"}
