"""
File: database/models.py
Purpose:
    Определение SQLAlchemy моделей для базы данных.
    Содержит все таблицы и их связи для хранения данных пользователей,
    истории действий, сессий администраторов и запросов на кредиты.

Responsibilities:
    - Определение структуры таблиц БД
    - Определение связей между таблицами (relationships)
    - Валидация данных на уровне модели

Key Design Decisions:
    - Используется SQLAlchemy ORM для работы с БД
    - Все модели наследуются от declarative_base()
    - Используются индексы для часто запрашиваемых полей (telegram_id, user_id, timestamp)
    - Связи настроены с каскадным удалением (cascade="all, delete-orphan")

Notes:
    - База данных: SQLite (для продакшена рекомендуется PostgreSQL)
    - Все даты хранятся в UTC
    - credits хранится как Float (для точности рекомендуется Decimal)
"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from config.settings import settings

Base = declarative_base()


class User(Base):
    """
    Модель пользователя Telegram бота.
    
    Attributes:
        id: Внутренний ID пользователя (primary key)
        telegram_id: Уникальный ID пользователя из Telegram (unique, indexed)
        username: Имя пользователя в Telegram (опционально)
        credits: Баланс кредитов пользователя (default: 0.0)
        created_at: Дата создания записи (auto)
        updated_at: Дата последнего обновления (auto, onupdate)
        actions: Связь с историей действий (one-to-many)
    
    Relationships:
        - actions: Список всех действий пользователя (ActionHistory)
    """
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    credits = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Связь с историей действий
    actions = relationship("ActionHistory", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, username={self.username}, credits={self.credits})>"


class ActionHistory(Base):
    """
    Модель истории действий пользователей.
    
    Хранит все действия пользователей: генерации изображений, начисления кредитов и т.д.
    
    Attributes:
        id: Внутренний ID записи (primary key)
        user_id: ID пользователя (foreign key, indexed)
        action_type: Тип действия (например, 'api_request_nanobanana', 'credit_add')
        request_data: JSON строка с данными запроса
        response_data: JSON строка с данными ответа
        credits_spent: Количество потраченных кредитов (default: 0.0)
        cost_usd: Стоимость в реальной валюте USD (default: 0.0)
        model_name: Название модели (например, 'nanobanana', 'seedream') для фильтрации
        timestamp: Время выполнения действия (indexed)
        user: Связь с пользователем (many-to-one)
    """
    __tablename__ = 'action_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    action_type = Column(String(100), nullable=False)  # 'api_request', 'credit_add', etc.
    request_data = Column(Text, nullable=True)  # JSON строка с данными запроса
    response_data = Column(Text, nullable=True)  # JSON строка с данными ответа
    credits_spent = Column(Float, default=0.0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)  # Стоимость в USD
    model_name = Column(String(50), nullable=True, index=True)  # Название модели для фильтрации
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Связь с пользователем
    user = relationship("User", back_populates="actions")
    
    def __repr__(self):
        return f"<ActionHistory(id={self.id}, user_id={self.user_id}, action_type={self.action_type}, model_name={self.model_name}, cost_usd={self.cost_usd}, timestamp={self.timestamp})>"


class AdminSession(Base):
    """
    Модель сессий администратора для Flask админ-панели.
    
    Хранит активные сессии администраторов с токенами и временем истечения.
    
    Attributes:
        id: Внутренний ID сессии (primary key)
        session_token: Уникальный токен сессии (unique, indexed)
        created_at: Дата создания сессии (auto)
        expires_at: Дата истечения сессии (indexed)
    """
    __tablename__ = 'admin_sessions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_token = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    
    def __repr__(self):
        return f"<AdminSession(id={self.id}, expires_at={self.expires_at})>"


class CreditRequest(Base):
    """
    Модель запросов пользователей на пополнение кредитов.
    
    Пользователь может запросить пополнение баланса, администратор одобряет или отклоняет.
    
    Attributes:
        id: Внутренний ID запроса (primary key)
        user_id: ID пользователя (foreign key, indexed)
        amount: Сумма запроса (default: из констант)
        status: Статус запроса ('pending', 'approved', 'rejected')
        created_at: Дата создания запроса (indexed)
        processed_at: Дата обработки запроса администратором (nullable)
        user: Связь с пользователем (many-to-one)
    """
    __tablename__ = 'credit_requests'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    amount = Column(Float, default=1000.0, nullable=False)
    status = Column(String(50), default='pending', nullable=False)  # 'pending', 'approved', 'rejected'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    processed_at = Column(DateTime, nullable=True)
    
    # Связь с пользователем
    user = relationship("User")
    
    def __repr__(self):
        return f"<CreditRequest(id={self.id}, user_id={self.user_id}, amount={self.amount}, status={self.status})>"
