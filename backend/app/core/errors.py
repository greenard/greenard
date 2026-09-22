"""Erreurs applicatives à code stable.

Le frontend traduit le `code` (FR/EN) ; `message` est un libellé anglais de repli pour les logs
et les clients sans i18n. `params` alimente l'interpolation des traductions.
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400

    def __init__(self, code: str, message: str, status_code: int | None = None, **params: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "params": self.params}


class NotFound(AppError):
    status_code = 404


class Forbidden(AppError):
    status_code = 403


class Unauthorized(AppError):
    status_code = 401


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})
