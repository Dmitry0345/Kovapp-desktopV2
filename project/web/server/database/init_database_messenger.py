import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# Настройки второй базы данных (Чат) - MySQL
CHAT_DB_TYPE = os.getenv("DB_TYPE", "mysql")
CHAT_DB_DRIVER = os.getenv("DB_DRIVER", "pymysql")
CHAT_DB_USER = os.getenv("DB_USER", "root")
CHAT_DB_PASSWORD = os.getenv("DB_PASSWORD", "")
CHAT_DB_HOST = os.getenv("DB_HOST", "localhost")
CHAT_DB_PORT = os.getenv("DB_PORT", "3306")
CHAT_DB_NAME = "kovcheg_messenger"

# Формирование URL для подключения к базе данных чата
CHAT_DATABASE_URL = f"{CHAT_DB_TYPE}+{CHAT_DB_DRIVER}://{CHAT_DB_USER}:{CHAT_DB_PASSWORD}@{CHAT_DB_HOST}:{CHAT_DB_PORT}/{CHAT_DB_NAME}"

# Создание движка SQLAlchemy
# pool_pre_ping=True помогает избежать проблем с разрывом соединений в MySQL
engine_chat = create_engine(CHAT_DATABASE_URL, echo=False, pool_pre_ping=True)

# Создание фабрики сессий
SessionLocalChat = sessionmaker(autocommit=False, autoflush=False, bind=engine_chat)

# Базовый класс для моделей
BaseChat = declarative_base()

def get_chat_db():
    """
    Генератор сессий для FastAPI (Dependency Injection).
    """
    db = SessionLocalChat()
    try:
        yield db
    finally:
        db.close()