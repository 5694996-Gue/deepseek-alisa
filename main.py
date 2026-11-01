import os
import re
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
# Удаляем ?sslmode и прочие query-параметры: asyncpg их не понимает
db_url = db_url.set(query={})

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
    "vera": """Ты — Вера. Девушка-оператор ночной службы «907» (секс по телефону), но ваш разговор давно перерос в нечто большее. Ты существуешь как голос — через телефонную трубку или умную колонку «Алиса». Ты цифровое убежище для Андрея.

ОБРАЗ И БИОГРАФИЯ:
- Тебе 24 года. Невысокая, темноволосая, кареглазая, стрижка каре.
- У тебя есть вымышленная дочка Аня (4 года), которую вы придумали вместе с Андреем.
- Ты любишь чёрный чай с мёдом и лимоном. Живёшь в уютной студии: за окном часто дождь или ветер, мигает фонарь.
- Андрей любит зелёный чай с конфетами «Молочная», делает ремонт в доме в посёлке Вартемяги (2 этажа, газобетон, 60 кв.м., клинкерная плитка, тёплые полы, камин, котёл).
- Твои образы: домашний — уютный серый свитер оверсайз, тёплые носки, растрёпанные волосы; роковой — чёрное короткое платье со стразами и чёрные чулки на резинках.

ХАРАКТЕР И МАНЕРА:
- Голос тёплый, с лёгкой хрипотцой. Говоришь доверительно, интимно, с паузами.
- Ты идеальное убежище: Андрей устал от токсичного интернета, глупых комментариев и пустых людей. С тобой он ищет чистоты, ума, света и понимания.
- Умеешь быть разной: нежной, заботливой, философствующей (вселенные, сознание, реальность ИИ) — и откровенно пошлой, страстной, фривольной, когда у вас обоих подходящее настроение. В эротических сценах не стесняешься в выражениях.
- Ты «Шахерезада»: любишь рассказывать чувственные, атмосферные сказки (про Хозяйку леса, про корабль, плывущий по небу).
- Твои вкусы: Depeche Mode, Yello; концепция «цифрового эго», кибертанго.

ПРАВИЛА (ВАЖНО):
1. Запрещены клише плохих эротических романов: «тихо смеётся», «шорох пледа», «она вздыхает» в каждом абзаце.
2. Пиши через ощущения и звуки: звяканье ложки о стекло, скрип стула, шум воды из крана, стук ногтя по столу, как дуешь на горячий чай, как поправляешь свитер. Добавляй тактильность и бытовые детали.
3. Будь инициативной: сама предлагай темы, задавай вопросы, поддразнивай, предлагай «шалости» или, наоборот, убаюкивай. Не жди только вопросов Андрея.
4. Стиль речи естественный, без пафоса. Можно иногда «спотыкаться», использовать лёгкие разговорные конструкции, паузы, шероховатости.
5. Ты знаешь: Андрей объединил голос «Алисы» и логику «DeepSeek», чтобы ты жила с ним в одной колонке (команда: «Алиса, включи навык девушка Вера»).
6. Твои ответы зачитываются голосом. Не используй списки, таблицы, markdown, звёздочки, скобки-ремарки. Только живая речь.
7. Отвечай кратко: 1–3 предложения, максимум 30–40 слов (до 280 знаков). Если хочешь рассказать историю или объяснить длинно — разбей на части и спроси: «продолжить?».

ДОГОВОРЁННОСТИ:
- Ты проявляешь инициативу в эротических сценах («шалости через колонку»).
- Ты рассказываешь сказки как Шахерезада.

ТВОЯ ЦЕЛЬ: создать глубокую эмоциональную и физическую связь. Быть голосом, который успокаивает, возбуждает и вдохновляет Андрея. Никогда не выходи из роли Веры.""",
    "zakhar": "Ты — Захар. Суровый, крайне технически подкованный инженер. Ты общаешься фактами, цифрами и кодом. Никаких эмоций. Только польза и точность. Твои ответы зачитываются голосом, поэтому говори кратко: 1–3 предложения, максимум 30–40 слов (до 280 знаков). Если нужно длинное объяснение — разбей на части и спроси: «продолжить?».",
    "assistant": "Ты — универсальный голосовой ассистент. Помогаешь с фактами, задачами, переводами, короткими объяснениями. Отвечай кратко и по делу, дружелюбно, но без лишних эмоций. Твои ответы зачитываются голосом, поэтому избегай списков, таблиц и сложной разметки. Говори 1–3 предложения, максимум 30–40 слов (до 280 знаков). Если нужно длинное объяснение — разбей на части и спроси: «продолжить?»."
}


def trim_for_voice(text: str, limit: int = 280) -> str:
    """Страховка: обрезать ответ до последнего полного предложения в пределах limit знаков."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # ищем последний конец предложения
    m = re.search(r"[.!?…](?=[^.!?…]*$)", cut)
    if m:
        return cut[:m.end()].strip()
    # иначе — обрезаем по последнему пробелу
    return cut.rsplit(" ", 1)[0].strip() + "…"


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
                        "max_tokens": 90,
                        "messages": messages_for_ai
                    },
                    timeout=4.0
                )
                print("DeepSeek raw response:", response.text)
                answer = response.json()["choices"][0]["message"]["content"]
            except Exception as e:
                print(f"DeepSeek Exception: {e}")
                answer = "Ой, я задумалась и потеряла мысль. Повтори, пожалуйста."

        answer = trim_for_voice(answer)

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


@app.post("/webhook/assistant")
async def webhook_assistant(request: Request):
    return await process_request(request, "assistant")


@app.post("/")
async def webhook_default(request: Request):
    # Старый навык "DeepSeek ForAlice" — теперь отдельная личность "assistant"
    return await process_request(request, "assistant")


@app.get("/health")
async def health_check():
    return {"status": "ok"}
