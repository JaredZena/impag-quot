from fastapi import FastAPI, Depends
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from rag_system import query_rag_system
from sqlalchemy.orm import Session
from models import get_db, Query
from typing import List
from datetime import datetime
from routes import suppliers, products, quotations
from routes.products import router as products_router
from routes.suppliers import router as suppliers_router
from routes.quotations import router as quotations_router
from routes.variants import router as variants_router
from routes.categories import router as categories_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Include routers
app.include_router(products_router)
app.include_router(suppliers_router)
app.include_router(quotations_router)
app.include_router(variants_router)
app.include_router(categories_router)

class QueryRequest(BaseModel):
    query: str

class QueryResponse(BaseModel):
    id: int
    query_text: str
    response_text: str
    created_at: datetime

    class Config:
        from_attributes = True

@app.post("/query")
async def query(request: QueryRequest, db: Session = Depends(get_db)):
    # Get response from RAG system
    response = query_rag_system(request.query)
    
    # Save query and response to database
    db_query = Query(
        query_text=request.query,
        response_text=response
    )
    db.add(db_query)
    db.commit()
    db.refresh(db_query)
    
    return {"response": response}

@app.get("/queries", response_model=List[QueryResponse])
async def get_queries(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    queries = db.query(Query).order_by(Query.created_at.desc()).offset(skip).limit(limit).all()
    return queries

@app.get("/")
def read_root():
    return {"message": "Welcome to the Quotation System API"}
