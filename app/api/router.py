"""Router raíz de la API. Los routers de cada dominio se registran aquí."""

from fastapi import APIRouter

from app.api import chat

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(chat.router)
