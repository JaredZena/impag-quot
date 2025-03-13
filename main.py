from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from rag_system import query_rag_system

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://impag-quot-f44gj8226-jaredzenas-projects.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str

@app.post("/query")
async def query(request: QueryRequest):
    response = query_rag_system(request.query)
    return {"response": response}
