"""Router raíz de la API. Los routers de cada dominio se registran aquí."""

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")

# TODO: registrar routers, p. ej. api_router.include_router(chat.router)
