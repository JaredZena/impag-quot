from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://impag-admin-app.vercel.app",  # Production domain Product Manager
        "https://impag-quot-web.vercel.app",  # Production domain Cotizador
        "http://localhost:5173",              # Local development
        "http://localhost:3000"               # Alternative local port
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
app.include_router(logistics_router)

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
async def create_conversation(conversation: ConversationCreate, db: Session = Depends(get_db)):
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
async def get_conversations(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
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
async def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
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
async def get_conversation_messages(conversation_id: int, db: Session = Depends(get_db)):
    """Get all messages in a conversation."""
    messages = db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id
    ).order_by(ConversationMessage.created_at.asc()).all()
    
    return messages

@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
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
async def query(request: QueryRequest, db: Session = Depends(get_db)):
    # Lazy import to ensure route is always registered
    query_rag_system_with_history = get_rag_query_function()
    
    # Convert Pydantic models to dicts for RAG system
    chat_history = [{"role": msg.role, "content": msg.content} for msg in (request.messages or [])]
    
    # Get response from RAG system with conversation context
    response = query_rag_system_with_history(
        query=request.query,
        chat_history=chat_history,
        customer_name=request.customer_name,
        customer_location=request.customer_location
    )
    
    # Save query and response to database
    db_query = Query(
        query_text=request.query,
        response_text=response
    )
    db.add(db_query)
    db.commit()
    db.refresh(db_query)
    
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
    
    return {"response": response, "conversation_id": request.conversation_id}

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
