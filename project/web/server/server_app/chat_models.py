# -*- coding: utf-8 -*-

from datetime import timedelta, datetime, date
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from server.database.init_database_messenger import SessionLocalChat, BaseChat, engine_chat


class ChatModel(BaseChat):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True)
    image_path = Column(String(512), nullable=True)  # Аватарка группы
    is_group = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    participants = relationship("ChatParticipantModel", back_populates="chat", cascade="all, delete-orphan")
    messages = relationship("MessageModel", back_populates="chat", cascade="all, delete-orphan")


class ChatParticipantModel(BaseChat):
    __tablename__ = "chat_participants"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"))
    user_id = Column(Integer)  # ID сотрудника из основной БД
    role = Column(String, default='participant')  # 'owner', 'admin', 'participant'
    joined_at = Column(DateTime, default=datetime.utcnow)
    last_read_message_id = Column(Integer, default=0)

    chat = relationship("ChatModel", back_populates="participants")


class MessageModel(BaseChat):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", ondelete="CASCADE"))
    sender_id = Column(Integer)
    text = Column(Text, nullable=True)

    # Поля для файлов (должны быть в БД)
    file_path = Column(String(512), nullable=True)
    file_name = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)
    is_image = Column(Boolean, default=False)

    timestamp = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)

    # Поля статусов
    is_edited = Column(Boolean, default=False)

    # НОВЫЕ ПОЛЯ ДЛЯ ГРУППИРОВКИ И СПЕЦИАЛЬНЫХ СООБЩЕНИЙ
    special = Column(String(255),
                     nullable=True)  # 'file_group', 'group_update', 'avatar_change', 'member_add', 'member_remove'
    group_id = Column(String(36), nullable=True)  # UUID для группировки сообщений
    parent_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)  # Для группировки файлов
    metadata_json = Column(JSON, nullable=True)  # Дополнительные данные в JSON

    # Связь с реакциями
    reactions_obj = relationship("MessageReactionModel",
                                 back_populates="message",
                                 cascade="all, delete-orphan")

    chat = relationship("ChatModel", back_populates="messages")


class MessageReactionModel(BaseChat):
    __tablename__ = "message_reactions"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"))
    user_id = Column(Integer)  # ID сотрудника, который поставил реакцию
    reaction_type = Column(String(20))  # 'like', 'dislike', 'sad', 'trophy', 'confetti'
    created_at = Column(DateTime, default=datetime.utcnow)

    message = relationship("MessageModel", back_populates="reactions_obj")
