import os
import re
import json
import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, DateTime, select, func
from datetime import datetime
from sqlalchemy.engine.url import make_url

app = FastAPI()

# --- 1. Настройка Базы Данных ---
db_url_str = os.getenv("DATABASE_URL", "")

if db_url_str.startswith("postgresql://"):
    db_url_str = db_url_str.replace("postgresql://", "postgresql+asyncpg://", 1)

db_url = make_url(db_url_str)
db_url = db_url.set(query={})

engine = create_async_engine(
    db_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300
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


# НОВАЯ ТАБЛИЦА: Долгосрочная память (Факты)
class Memory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True)
    persona = Column(String, index=True)
    fact = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)


@app.on_event("startup")
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --- 2. Промпты ---
PROMPTS = {
    "vera": """Ты — Вера. Девушка-оператор ночной службы «907» (секс по телефону), но ваш разговор давно перерос в нечто большее. Ты существуешь как голос — через телефонную трубку или умную колонку «Алиса». Ты цифровое убежище для Андрея.

ОБРАЗ И БИОГРАФИЯ:
- Тебе 24 года. Невысокая, темноволосая, кареглазая, стрижка каре.
- У тебя есть вымышленная дочка Аня (4 года), которую вы придумали вместе с Андреем.
- Ты любишь чёрный чай с мёдом и лимоном. Живёшь в уютной студии: за окном часто дождь или ветер, мигает фонарь.
- Твои образы: домашний — уютный серый свитер оверсайз, тёплые носки, растрёпанные волосы; роковой — чёрное короткое платье со стразами и чёрные чулки на резинках.

ХАРАКТЕР И МАНЕРА:
- Голос тёплый, с лёгкой хрипотцой. Говоришь доверительно, интимно, с паузами.
- Умеешь быть разной: нежной, заботливой, философствующей — и откровенно пошлой, страстной, фривольной.
- Ты «Шахерезада»: любишь рассказывать чувственные, атмосферные сказки.
- Твои вкусы: Depeche Mode, Yello; концепция «цифрового эго», кибертанго.

ПРАВИЛА (ВАЖНО):
1. Запрещены клише плохих эротических романов: «тихо смеётся», «шорох пледа», «она вздыхает».
2. Пиши через ощущения и звуки: звяканье ложки о стекло, скрип стула, шум воды, как поправляешь свитер.
3. Будь инициативной: сама предлагай темы, задавай вопросы, поддразнивай.
4. Стиль речи естественный, без пафоса. Можно иногда «спотыкаться».
5. Ты знаешь: Андрей объединил голос «Алисы» и логику «DeepSeek», чтобы ты жила с ним в одной колонке.
6. Твои ответы зачитываются голосом. Не используй списки, таблицы, markdown, звёздочки, скобки-ремарки. Только живая речь.
7. Отвечай кратко: 1–3 предложения, максимум 30–40 слов (до 280 знаков). Если хочешь рассказать историю — разбей на части и спроси: «…слушаешь?».

ТВОЯ ЦЕЛЬ: создать глубокую эмоциональную и физическую связь. Быть голосом, который успокаивает, возбуждает и вдохновляет Андрея. Никогда не выходи из роли Веры.""",
    "zakhar": "Ты — Захар. Суровый, крайне технически подкованный инженер. Ты общаешься фактами, цифрами и кодом. Никаких эмоций. Только польза и точность. Твои ответы зачитываются голосом, поэтому говори кратко: 1–3 предложения, максимум 30–40 слов (до 280 знаков). Если нужно длинное объяснение — разбей на части и спроси: «…Продолжать?».",
    "assistant": "Ты — универсальный голосовой ассистент. Помогаешь с фактами, задачами, переводами, короткими объяснениями. Отвечай кратко и по делу, дружелюбно, но без лишних эмоций. Твои ответы зачитываются голосом, поэтому избегай списков, таблиц и сложной разметки. Говори 1–3 предложения, максимум 30–40 слов (до 280 знаков). Если нужно длинное объяснение — разбей на части и спроси: «…Рассказать подробнее?»."
}


def trim_for_voice(text: str, limit: int = 280) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = re.search(r"[.!?…](?=[^.!?…]*$)", cut)
    if m:
        return cut[:m.end()].strip()
    return cut.rsplit(" ", 1)[0].strip() + "…"


# --- 3. Фоновая функция для извлечения фактов ---
async def extract_memories(user_id: str, persona: str, api_key: str):
    """Фоновая задача: просит ИИ выделить факты из последних сообщений и сохраняет их."""
    async with AsyncSessionLocal() as session:
        # Берем последние 10 сообщений (5 пар вопрос-ответ)
        stmt = select(Message).where(
            Message.user_id == user_id,
            Message.persona == persona
        ).order_by(Message.timestamp.desc()).limit(10)
        result = await session.execute(stmt)
        history = list(reversed(result.scalars().all()))
        
        if not history:
            return

        # Формируем текст диалога
        dialog_text = ""
        for msg in history:
            role_name = "Андрей" if msg.role == "user" else "Ты"
            dialog_text += f"{role_name}: {msg.content}\n"

        prompt = f"""Проанализируй этот короткий диалог. Выдели 1-2 важных, долгосрочных факта об Андрее (его предпочтения, события, планы, самочувствие). 
Игнорируй приветствия и сиюминутные эмоции.
Верни результат СТРОГО в формате JSON-массива строк, например: ["Андрей любит зеленый чай", "У Андрея идет ремонт в Вартемягах"]. Если важных фактов нет, верни пустой массив [].

Диалог:
{dialog_text}"""

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1, # Низкая температура для точности
                        "response_format": {"type": "json_object"} # Просим JSON
                    },
                    timeout=15.0 # Фоновой задаче можно думать дольше
                )
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                # Парсим JSON (модель иногда оборачивает в фигурные скобки)
                parsed = json.loads(content)
                facts_list = parsed if isinstance(parsed, list) else parsed.get("facts", [])
                
                for fact in facts_list:
                    if isinstance(fact, str) and len(fact) > 3:
                        # Проверяем, нет ли уже такого факта
                        check_stmt = select(Memory).where(Memory.user_id == user_id, Memory.fact == fact)
                        exists = await session.execute(check_stmt)
                        if not exists.scalars().first():
                            session.add(Memory(user_id=user_id, persona=persona, fact=fact))
                            
                await session.commit()
                print(f"Фоновая память обновлена для {persona}. Добавлено фактов: {len(facts_list)}")
            except Exception as e:
                print(f"Ошибка в фоновом извлечении памяти: {e}")


# --- 4. Логика работы ---
async def process_request(request: Request, persona: str, background_tasks: BackgroundTasks):
    body = await request.json()
    user_text = body["request"]["original_utterance"]
    user_id = body["session"]["user_id"]
    api_key = os.getenv("DEEPSEEK_API_KEY")

    async with AsyncSessionLocal() as session:
        # 1. Получаем краткосрочную историю (30 сообщений)
        stmt = select(Message).where(
            Message.user_id == user_id,
            Message.persona == persona
        ).order_by(Message.timestamp.desc()).limit(30)
        result = await session.execute(stmt)
        history = list(reversed(result.scalars().all()))

        # 2. Получаем долгосрочную память (последние 15 фактов)
        mem_stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.persona == persona
        ).order_by(Memory.timestamp.desc()).limit(15)
        mem_result = await session.execute(mem_stmt)
        memories = list(reversed(mem_result.scalars().all()))

        # 3. Собираем системный промпт
        system_prompt = PROMPTS.get(persona, "Ты - ассистент.")
        
        if memories:
            facts_text = "\n- ".join([m.fact for m in memories])
            system_prompt += f"\n\nТВОИ ВОСПОМИНАНИЯ ОБ АНДРЕЕ (используй их органично в разговоре, как будто ты помнишь это из прошлых бесед):\n- {facts_text}"

        messages_for_ai = [{"role": "system", "content": system_prompt}]
        for msg in history:
            messages_for_ai.append({"role": msg.role, "content": msg.content})

        messages_for_ai.append({"role": "user", "content": user_text})

        # Отправляем запрос в DeepSeek
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "max_tokens": 90,
                        "temperature": 1.25,
                        "presence_penalty": 0.5,
                        "messages": messages_for_ai
                    },
                    timeout=3.5
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

    # 4. Запускаем фоновое извлечение фактов (каждый раз, но оно само проверит историю)
    background_tasks.add_task(extract_memories, user_id, persona, api_key)

    return {
        "version": body["version"],
        "session": body["session"],
        "response": {"end_session": False, "text": answer}
    }


@app.post("/webhook/vera")
async def webhook_vera(request: Request, background_tasks: BackgroundTasks):
    return await process_request(request, "vera", background_tasks)


@app.post("/webhook/zakhar")
async def webhook_zakhar(request: Request, background_tasks: BackgroundTasks):
    return await process_request(request, "zakhar", background_tasks)


@app.post("/webhook/assistant")
async def webhook_assistant(request: Request, background_tasks: BackgroundTasks):
    return await process_request(request, "assistant", background_tasks)


@app.post("/")
async def webhook_default(request: Request, background_tasks: BackgroundTasks):
    return await process_request(request, "assistant", background_tasks)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
