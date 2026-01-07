"""
Менеджер базы данных.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from config.settings import settings
from config.constants import CREDIT_REQUEST_AMOUNT, INITIAL_USER_CREDITS, get_model_cost_usd
from database.models import Base, User, ActionHistory, AdminSession, CreditRequest
from utils.logger import logger
from datetime import datetime, timedelta
from sqlalchemy import func, and_
import secrets
import json


class DatabaseManager:
    """Класс для управления базой данных."""
    
    def __init__(self):
        """Инициализация менеджера БД."""
        self.engine = create_engine(
            f'sqlite:///{settings.DATABASE_PATH}',
            echo=False,
            connect_args={'check_same_thread': False}
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных (создание таблиц)."""
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка при инициализации БД: {e}")
            raise
    
    def get_session(self) -> Session:
        """Получить сессию БД."""
        return self.SessionLocal()
    
    # Методы для работы с пользователями
    def get_or_create_user(self, telegram_id: int, username: str = None) -> User:
        """
        Получить пользователя или создать нового.
        
        Args:
            telegram_id: Telegram ID пользователя
            username: Имя пользователя (опционально)
        
        Returns:
            Объект User
        """
        session = self.get_session()
        try:
            user = session.query(User).filter(User.telegram_id == telegram_id).first()
            if not user:
                user = User(telegram_id=telegram_id, username=username, credits=INITIAL_USER_CREDITS)
                session.add(user)
                session.commit()
                logger.info(f"Создан новый пользователь: telegram_id={telegram_id}, начальный баланс: 10000.0 кредитов")
            elif username and user.username != username:
                user.username = username
                session.commit()
            # Загружаем все атрибуты перед отсоединением
            _ = user.credits  # Загружаем credits
            session.expunge(user)  # Отсоединяем от сессии
            return user
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при работе с пользователем: {e}")
            raise
        finally:
            session.close()
    
    def get_user(self, telegram_id: int) -> User:
        """Получить пользователя по Telegram ID."""
        session = self.get_session()
        try:
            user = session.query(User).filter(User.telegram_id == telegram_id).first()
            if user:
                _ = user.credits  # Загружаем credits
                session.expunge(user)  # Отсоединяем от сессии
            return user
        finally:
            session.close()
    
    def get_user_by_id(self, user_id: int) -> User:
        """Получить пользователя по ID."""
        session = self.get_session()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                _ = user.credits  # Загружаем credits
                session.expunge(user)  # Отсоединяем от сессии
            return user
        finally:
            session.close()
    
    def update_user_credits(self, user_id: int, credits_delta: float) -> bool:
        """
        Обновить баланс кредитов пользователя.
        
        Args:
            user_id: ID пользователя
            credits_delta: Изменение кредитов (может быть отрицательным)
        
        Returns:
            True если успешно
        """
        session = self.get_session()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                user.credits += credits_delta
                if user.credits < 0:
                    user.credits = 0
                session.commit()
                logger.debug(f"Обновлен баланс пользователя {user_id}: {credits_delta}, новый баланс: {user.credits}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при обновлении кредитов: {e}")
            return False
        finally:
            session.close()
    
    def get_all_users(self, limit: int = None, offset: int = 0):
        """Получить всех пользователей."""
        session = self.get_session()
        try:
            query = session.query(User).order_by(User.created_at.desc())
            if limit:
                query = query.limit(limit).offset(offset)
            return query.all()
        finally:
            session.close()
    
    # Методы для работы с историей
    def add_action(self, user_id: int, action_type: str, request_data: str = None, 
                   response_data: str = None, credits_spent: float = 0.0,
                   model_name: str = None, cost_usd: float = None) -> ActionHistory:
        """
        Добавить запись в историю действий.
        
        Args:
            user_id: ID пользователя
            action_type: Тип действия
            request_data: Данные запроса (JSON строка)
            response_data: Данные ответа (JSON строка)
            credits_spent: Потрачено кредитов
            model_name: Название модели (например, 'nanobanana', 'seedream')
            cost_usd: Стоимость в USD (если None, вычисляется из model_name)
        
        Returns:
            Объект ActionHistory
        """
        session = self.get_session()
        try:
            # Если cost_usd не указан, но есть model_name, вычисляем стоимость
            if cost_usd is None and model_name:
                cost_usd = get_model_cost_usd(model_name)
            
            action = ActionHistory(
                user_id=user_id,
                action_type=action_type,
                request_data=request_data,
                response_data=response_data,
                credits_spent=credits_spent,
                model_name=model_name,
                cost_usd=cost_usd or 0.0
            )
            session.add(action)
            session.commit()
            logger.debug(f"Добавлена запись в историю: user_id={user_id}, action_type={action_type}, model_name={model_name}, cost_usd={cost_usd}")
            return action
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при добавлении записи в историю: {e}")
            raise
        finally:
            session.close()
    
    def get_user_history(self, user_id: int, limit: int = None, offset: int = 0):
        """Получить историю действий пользователя."""
        session = self.get_session()
        try:
            query = session.query(ActionHistory).filter(
                ActionHistory.user_id == user_id
            ).order_by(ActionHistory.timestamp.desc())
            if limit:
                query = query.limit(limit).offset(offset)
            return query.all()
        finally:
            session.close()
    
    def get_all_history(self, limit: int = None, offset: int = 0, user_id: int = None):
        """Получить всю историю действий с фильтрацией."""
        session = self.get_session()
        try:
            query = session.query(ActionHistory)
            if user_id:
                query = query.filter(ActionHistory.user_id == user_id)
            query = query.order_by(ActionHistory.timestamp.desc())
            if limit:
                query = query.limit(limit).offset(offset)
            return query.all()
        finally:
            session.close()
    
    def get_action_by_id(self, action_id: int) -> ActionHistory:
        """Получить действие по ID."""
        session = self.get_session()
        try:
            return session.query(ActionHistory).filter(ActionHistory.id == action_id).first()
        finally:
            session.close()
    
    # Методы для работы с сессиями админа
    def create_admin_session(self, expires_in_seconds: int = 3600) -> str:
        """
        Создать сессию администратора.
        
        Args:
            expires_in_seconds: Время жизни сессии в секундах
        
        Returns:
            Токен сессии
        """
        session = self.get_session()
        try:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in_seconds)
            
            admin_session = AdminSession(
                session_token=token,
                expires_at=expires_at
            )
            session.add(admin_session)
            session.commit()
            logger.debug(f"Создана сессия администратора: expires_at={expires_at}")
            return token
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при создании сессии: {e}")
            raise
        finally:
            session.close()
    
    def validate_admin_session(self, token: str) -> bool:
        """
        Проверить валидность сессии администратора.
        
        Args:
            token: Токен сессии
        
        Returns:
            True если сессия валидна
        """
        session = self.get_session()
        try:
            admin_session = session.query(AdminSession).filter(
                AdminSession.session_token == token
            ).first()
            
            if admin_session and admin_session.expires_at > datetime.utcnow():
                return True
            elif admin_session:
                # Удаляем истекшую сессию
                session.delete(admin_session)
                session.commit()
            return False
        finally:
            session.close()
    
    def cleanup_expired_sessions(self):
        """Очистить истекшие сессии."""
        session = self.get_session()
        try:
            deleted = session.query(AdminSession).filter(
                AdminSession.expires_at <= datetime.utcnow()
            ).delete()
            session.commit()
            if deleted > 0:
                logger.info(f"Удалено истекших сессий: {deleted}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при очистке сессий: {e}")
        finally:
            session.close()
    
    # Методы для работы с запросами на кредиты
    def create_credit_request(self, user_id: int, amount: float = None) -> CreditRequest:
        """
        Создать запрос на кредиты.
        
        Args:
            user_id: ID пользователя
            amount: Сумма запроса (по умолчанию из констант)
        
        Returns:
            Объект CreditRequest
        """
        if amount is None:
            amount = CREDIT_REQUEST_AMOUNT
        session = self.get_session()
        try:
            credit_request = CreditRequest(
                user_id=user_id,
                amount=amount,
                status='pending'
            )
            session.add(credit_request)
            session.commit()
            # Загружаем все атрибуты перед отсоединением
            _ = credit_request.id  # Загружаем id
            _ = credit_request.user_id  # Загружаем user_id
            _ = credit_request.amount  # Загружаем amount
            _ = credit_request.status  # Загружаем status
            session.expunge(credit_request)  # Отсоединяем от сессии
            logger.debug(f"Создан запрос на кредиты: user_id={user_id}, amount={amount}")
            return credit_request
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при создании запроса на кредиты: {e}")
            raise
        finally:
            session.close()
    
    def get_credit_request(self, request_id: int) -> CreditRequest:
        """Получить запрос на кредиты по ID."""
        session = self.get_session()
        try:
            credit_request = session.query(CreditRequest).filter(CreditRequest.id == request_id).first()
            if credit_request:
                # Загружаем все атрибуты перед отсоединением
                _ = credit_request.id
                _ = credit_request.user_id
                _ = credit_request.amount
                _ = credit_request.status
                session.expunge(credit_request)  # Отсоединяем от сессии
            return credit_request
        finally:
            session.close()
    
    def approve_credit_request(self, request_id: int) -> bool:
        """
        Одобрить запрос на кредиты.
        
        Args:
            request_id: ID запроса
        
        Returns:
            True если успешно
        """
        session = self.get_session()
        try:
            credit_request = session.query(CreditRequest).filter(
                CreditRequest.id == request_id,
                CreditRequest.status == 'pending'
            ).first()
            
            if credit_request:
                # Обновляем баланс пользователя
                user = session.query(User).filter(User.id == credit_request.user_id).first()
                if user:
                    user.credits += credit_request.amount
                    credit_request.status = 'approved'
                    credit_request.processed_at = datetime.utcnow()
                    session.commit()
                    
                    # Записываем в историю
                    self.add_action(
                        user_id=credit_request.user_id,
                        action_type='credit_request_approved',
                        request_data=json.dumps({'request_id': request_id, 'amount': credit_request.amount}),
                        credits_spent=0.0
                    )
                    
                    logger.info(f"Запрос на кредиты одобрен: request_id={request_id}, user_id={credit_request.user_id}, amount={credit_request.amount}")
                    return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при одобрении запроса: {e}")
            return False
        finally:
            session.close()
    
    def reject_credit_request(self, request_id: int) -> bool:
        """
        Отклонить запрос на кредиты.
        
        Args:
            request_id: ID запроса
        
        Returns:
            True если успешно
        """
        session = self.get_session()
        try:
            credit_request = session.query(CreditRequest).filter(
                CreditRequest.id == request_id,
                CreditRequest.status == 'pending'
            ).first()
            
            if credit_request:
                credit_request.status = 'rejected'
                credit_request.processed_at = datetime.utcnow()
                session.commit()
                
                logger.info(f"Запрос на кредиты отклонен: request_id={request_id}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при отклонении запроса: {e}")
            return False
        finally:
            session.close()
    
    # Методы для получения статистики по моделям и периодам
    def get_model_statistics(self, period: str = 'all', user_id: int = None, 
                             start_date: datetime = None, end_date: datetime = None) -> dict:
        """
        Получить статистику по моделям за указанный период.
        
        Args:
            period: Период ('all', 'day', 'week', 'month', 'quarter')
            user_id: ID пользователя (опционально, для фильтрации по пользователю)
            start_date: Начальная дата (опционально, переопределяет period)
            end_date: Конечная дата (опционально, переопределяет period)
        
        Returns:
            Словарь со статистикой: {
                'total_requests': int,
                'total_cost_usd': float,
                'by_model': {
                    'nanobanana': {'count': int, 'cost_usd': float},
                    'seedream': {'count': int, 'cost_usd': float}
                }
            }
        """
        session = self.get_session()
        try:
            # Определяем диапазон дат
            if start_date is None or end_date is None:
                end_date = datetime.utcnow()
                if period == 'day':
                    start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
                elif period == 'week':
                    start_date = end_date - timedelta(days=7)
                elif period == 'month':
                    start_date = end_date - timedelta(days=30)
                elif period == 'quarter':
                    start_date = end_date - timedelta(days=90)
                elif period == 'all':
                    start_date = datetime(1970, 1, 1)  # Начало эпохи
                else:
                    start_date = datetime(1970, 1, 1)
            
            # Базовый запрос
            query = session.query(ActionHistory).filter(
                and_(
                    ActionHistory.timestamp >= start_date,
                    ActionHistory.timestamp <= end_date,
                    ActionHistory.action_type.like('api_request_%')
                )
            )
            
            # Фильтр по пользователю, если указан
            if user_id:
                query = query.filter(ActionHistory.user_id == user_id)
            
            # Получаем все записи
            actions = query.all()
            
            # Агрегируем статистику
            stats = {
                'total_requests': 0,
                'total_cost_usd': 0.0,
                'by_model': {}
            }
            
            for action in actions:
                stats['total_requests'] += 1
                stats['total_cost_usd'] += action.cost_usd or 0.0
                
                model_name = action.model_name or 'unknown'
                if model_name not in stats['by_model']:
                    stats['by_model'][model_name] = {'count': 0, 'cost_usd': 0.0}
                
                stats['by_model'][model_name]['count'] += 1
                stats['by_model'][model_name]['cost_usd'] += action.cost_usd or 0.0
            
            # Округляем значения
            stats['total_cost_usd'] = round(stats['total_cost_usd'], 2)
            for model in stats['by_model']:
                stats['by_model'][model]['cost_usd'] = round(stats['by_model'][model]['cost_usd'], 2)
            
            return stats
        finally:
            session.close()
    
    def get_user_model_statistics(self, user_id: int, period: str = 'all') -> dict:
        """
        Получить статистику по моделям для конкретного пользователя.
        
        Args:
            user_id: ID пользователя
            period: Период ('all', 'day', 'week', 'month', 'quarter')
        
        Returns:
            Словарь со статистикой (см. get_model_statistics)
        """
        return self.get_model_statistics(period=period, user_id=user_id)


# Создаем глобальный экземпляр менеджера БД
db_manager = DatabaseManager()
