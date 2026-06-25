"""
Punto de entrada: arranca FastAPI + Bot de Telegram en el mismo event loop.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from api import app as fastapi_app

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_telegram_app = None


@fastapi_app.on_event("startup")
async def start_telegram_bot():
    global _telegram_app
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "tu_token_aqui":
        print("⚠️  TELEGRAM_TOKEN no configurado — solo se inicia el dashboard web")
        return
    from bot import build_bot
    _telegram_app = build_bot()
    await _telegram_app.initialize()
    await _telegram_app.start()
    await _telegram_app.updater.start_polling(drop_pending_updates=True)
    print("✅ Bot de Telegram iniciado (polling)")


@fastapi_app.on_event("shutdown")
async def stop_telegram_bot():
    global _telegram_app
    if _telegram_app:
        await _telegram_app.updater.stop()
        await _telegram_app.stop()
        await _telegram_app.shutdown()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    print(f"✅ Dashboard disponible en http://0.0.0.0:{port}")
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port, log_level="warning")
