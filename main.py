from fastapi import FastAPI, Depends
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from models import get_db
from typing import List
from datetime import datetime
from routes import suppliers, products, quotations
from routes.products import router as products_router
from routes.suppliers import router as suppliers_router
from routes.quotations import router as quotations_router
from routes.categories import router as categories_router
from auth import verify_google_token

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

# RAG functionality moved to separate microservice
# This version focuses only on quotation processing and CRUD operations

@app.get("/")
def read_root():
    return {"message": "Welcome to the Quotation System API"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "impag-quot"}
