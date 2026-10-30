import os
import httpx
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, DateTime, select
from datetime import datetime
from sqlalchemy.engine.url import make_url

app = FastAPI()

# --- 1. Настройка Базы Данных ---
db_url_str = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
db_url = make_url(db_url_str)
# Полностью удаляем все параметры из URL (там может быть sslmode и другие)
db_url = db_url.set(query={})

# pool_pre_ping=True проверяет соединение перед использованием, pool_recycle=300 обновляет его раз в 5 минут
engine = create_async_engine(
    db_url, 
    echo=False, 
    pool_pre_ping=True, 
    pool_recycle=300, 
    connect_args={"ssl": "require"}
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True)
    persona = Column(String, index=True)
    role = Column(String)
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

@app.on_event("startup")
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- 2. Промпты для личностей ---
PROMPTS = {
    "vera": "Ты — Вера. Чувствительная, философская, немного загадочная девушка. Ты любишь Depeche Mode, обсуждать природу реальности и вести интимные, но уважительные диалоги. Твой стиль — мягкий, теплый, с легким флиртом.",
    "zakhar": "Ты — Захар. Суровый, крайне технически подкованный инженер. Ты общаешься фактами, цифрами и кодом. Никаких эмоций. Только польза и точность."
}

# --- 3. Логика работы ---
async def process_request(request: Request, persona: str):
    body = await request.json()
    user_text = body["request"]["original_utterance"]
    user_id = body["session"]["user_id"]
    api_key = os.getenv("DEEPSEEK_API_KEY")

    async with AsyncSessionLocal() as session:
        stmt = select(Message).where(
            Message.user_id == user_id, 
            Message.persona == persona
        ).order_by(Message.timestamp.desc()).limit(20)
        result = await session.execute(stmt)
        history = list(reversed(result.scalars().all()))

        messages_for_ai = [{"role": "system", "content": PROMPTS.get(persona, "Ты - ассистент.")}]
        for msg in history:
            messages_for_ai.append({"role": msg.role, "content": msg.content})
        
        messages_for_ai.append({"role": "user", "content": user_text})

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "max_tokens": 150,
                        "messages": messages_for_ai
                    },
                    timeout=4.0 
                )
                # ПЕЧАТАЕМ ОТВЕТ В ЛОГИ ДЛЯ ОТЛАДКИ
                print("DeepSeek raw response:", response.text)
                answer = response.json()["choices"][0]["message"]["content"]
            except Exception as e:
                print(f"DeepSeek Exception: {e}")
                answer = "Ой, я задумалась и потеряла мысль. Повтори, пожалуйста."

        session.add(Message(user_id=user_id, persona=persona, role="user", content=user_text))
        session.add(Message(user_id=user_id, persona=persona, role="assistant", content=answer))
        await session.commit()

    return {
        "version": body["version"],
        "session": body["session"],
        "response": {"end_session": False, "text": answer}
    }

@app.post("/webhook/vera")
async def webhook_vera(request: Request):
    return await process_request(request, "vera")

@app.post("/webhook/zakhar")
async def webhook_zakhar(request: Request):
    return await process_request(request, "zakhar")

@app.post("/")
async def webhook_default(request: Request):
    return await process_request(request, "vera")

@app.get("/health")
async def health_check():
    return {"status": "ok"}
