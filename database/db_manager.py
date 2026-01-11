"""
Менеджер базы данных.
"""
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from config.settings import settings
from config.constants import CREDIT_REQUEST_AMOUNT, INITIAL_USER_CREDITS, get_model_cost_usd, MODE_SEEDREAM
from database.models import Base, User, ActionHistory, AdminSession, CreditRequest, FileCache, FaceReferenceSet, FaceReferenceSetImage
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
                user = User(
                    telegram_id=telegram_id, 
                    username=username, 
                    credits=INITIAL_USER_CREDITS,
                    selected_mode=MODE_SEEDREAM  # По умолчанию Seedream
                )
                session.add(user)
                session.commit()
                logger.info(f"Создан новый пользователь: telegram_id={telegram_id}, начальный баланс: 10000.0 кредитов, режим: {MODE_SEEDREAM}")
            elif username and user.username != username:
                user.username = username
                session.commit()
            # Загружаем все атрибуты перед отсоединением
            _ = user.credits  # Загружаем credits
            _ = user.selected_mode  # Загружаем selected_mode
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
                _ = user.selected_mode  # Загружаем selected_mode
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
                _ = user.selected_mode  # Загружаем selected_mode
                session.expunge(user)  # Отсоединяем от сессии
            return user
        finally:
            session.close()
    
    def update_user_mode(self, telegram_id: int, mode: str) -> bool:
        """
        Обновить выбранный режим пользователя.
        
        Args:
            telegram_id: Telegram ID пользователя
            mode: Режим генерации ('nanobanana' или 'seedream')
        
        Returns:
            True если успешно обновлено, False если пользователь не найден
        """
        session = self.get_session()
        try:
            user = session.query(User).filter(User.telegram_id == telegram_id).first()
            if user:
                user.selected_mode = mode
                session.commit()
                logger.debug(f"Обновлен режим пользователя {telegram_id}: {mode}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при обновлении режима пользователя: {e}")
            return False
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
            credits_spent: Потрачено виртуальных кредитов бота
            model_name: Название модели (например, 'nanobanana', 'seedream')
            cost_usd: Стоимость в реальных кредитах Higgsfield (если None, вычисляется из model_name)
                     Примечание: Название параметра оставлено для совместимости, но хранятся кредиты, а не USD
        
        Returns:
            Объект ActionHistory
        """
        session = self.get_session()
        try:
            # Если cost_usd не указан, но есть model_name, вычисляем стоимость в кредитах Higgsfield
            if cost_usd is None and model_name:
                cost_usd = get_model_cost_usd(model_name)  # Возвращает кредиты Higgsfield
            
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
            
            # Агрегируем статистику (cost_usd уже хранит реальные кредиты Higgsfield)
            stats = {
                'total_requests': 0,
                'total_cost_usd': 0.0,  # Название для совместимости, но храним реальные кредиты Higgsfield
                'by_model': {}
            }
            
            for action in actions:
                stats['total_requests'] += 1
                # cost_usd уже хранит реальные кредиты Higgsfield (не USD!)
                real_credits = action.cost_usd or 0.0
                stats['total_cost_usd'] += real_credits
                
                model_name = action.model_name or 'unknown'
                if model_name not in stats['by_model']:
                    stats['by_model'][model_name] = {'count': 0, 'cost_usd': 0.0}  # Название для совместимости
                
                stats['by_model'][model_name]['count'] += 1
                stats['by_model'][model_name]['cost_usd'] += real_credits
            
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
    
    def get_users_credits_statistics(self, period: str = 'all') -> list:
        """
        Получить статистику по кредитам (тратам) по пользователям за период.
        
        Args:
            period: Период ('all', 'day', 'week', 'month', 'quarter')
        
        Returns:
            Список словарей с информацией о пользователях и их тратах:
            [
                {
                    'user_id': int,
                    'username': str,
                    'telegram_id': int,
                    'total_credits_spent': float,
                    'total_requests': int
                },
                ...
            ]
        """
        session = self.get_session()
        try:
            # Определяем диапазон дат
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
            
            # Запрос для получения статистики по пользователям
            # cost_usd уже хранит реальные кредиты Higgsfield (не USD!)
            query = session.query(
                ActionHistory.user_id,
                func.sum(ActionHistory.cost_usd).label('total_credits'),
                func.count(ActionHistory.id).label('total_requests')
            ).filter(
                and_(
                    ActionHistory.timestamp >= start_date,
                    ActionHistory.timestamp <= end_date,
                    ActionHistory.action_type.like('api_request_%'),
                    ActionHistory.cost_usd > 0
                )
            ).group_by(ActionHistory.user_id).order_by(func.sum(ActionHistory.cost_usd).desc())
            
            results = query.all()
            
            # Формируем список с информацией о пользователях
            # cost_usd уже содержит реальные кредиты Higgsfield
            users_stats = []
            for user_id, total_credits, total_requests in results:
                user = self.get_user_by_id(user_id)
                users_stats.append({
                    'user_id': user_id,
                    'username': user.username if user else None,
                    'telegram_id': user.telegram_id if user else user_id,
                    'total_credits_spent': round(float(total_credits or 0.0), 2),
                    'total_requests': int(total_requests or 0)
                })
            
            return users_stats
        finally:
            session.close()
    
    # Методы для работы с кэшем файлов
    def get_file_cache(self, file_hash: str) -> Optional[str]:
        """
        Получить кэшированную ссылку на файл по хешу.
        
        Args:
            file_hash: SHA256 хеш файла
        
        Returns:
            URL загруженного файла в Higgsfield или None если не найден или истек
        """
        session = self.get_session()
        try:
            cache = session.query(FileCache).filter(
                FileCache.file_hash == file_hash,
                FileCache.expires_at > datetime.utcnow()
            ).first()
            
            if cache:
                # Загружаем URL в переменную перед отсоединением
                higgsfield_url = cache.higgsfield_url
                
                # Обновляем время последнего использования
                cache.last_used_at = datetime.utcnow()
                session.commit()
                
                logger.debug(f"Найдена кэшированная ссылка для file_hash={file_hash[:16]}...")
                return higgsfield_url
            
            return None
        finally:
            session.close()
    
    def save_file_cache(self, file_hash: str, higgsfield_url: str, expires_at: datetime) -> FileCache:
        """
        Сохранить ссылку на файл в кэш.
        
        Args:
            file_hash: SHA256 хеш файла
            higgsfield_url: URL загруженного файла в Higgsfield
            expires_at: Дата истечения ссылки
        
        Returns:
            Объект FileCache
        """
        session = self.get_session()
        try:
            # Проверяем, существует ли уже запись
            cache = session.query(FileCache).filter(FileCache.file_hash == file_hash).first()
            
            if cache:
                # Обновляем существующую запись
                cache.higgsfield_url = higgsfield_url
                cache.expires_at = expires_at
                cache.last_used_at = datetime.utcnow()
            else:
                # Создаем новую запись
                cache = FileCache(
                    file_hash=file_hash,
                    higgsfield_url=higgsfield_url,
                    expires_at=expires_at
                )
                session.add(cache)
            
            session.commit()
            
            # Загружаем все атрибуты перед отсоединением
            _ = cache.higgsfield_url
            _ = cache.expires_at
            _ = cache.last_used_at
            _ = cache.file_hash
            session.expunge(cache)  # Теперь безопасно отсоединяем
            
            logger.debug(f"Сохранен кэш файла: file_hash={file_hash[:16]}..., expires_at={expires_at}")
            return cache
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при сохранении кэша файла: {e}")
            raise
        finally:
            session.close()
    
    def cleanup_expired_file_cache(self) -> int:
        """
        Очистить истекшие записи кэша файлов.
        
        Returns:
            Количество удаленных записей
        """
        session = self.get_session()
        try:
            deleted = session.query(FileCache).filter(
                FileCache.expires_at <= datetime.utcnow()
            ).delete()
            session.commit()
            if deleted > 0:
                logger.info(f"Удалено истекших записей кэша файлов: {deleted}")
            return deleted
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при очистке кэша файлов: {e}")
            return 0
        finally:
            session.close()
    
    # Методы для работы с наборами референсов
    def create_face_reference_set(self, user_id: int, name: str) -> FaceReferenceSet:
        """
        Создать новый набор референсов.
        
        Args:
            user_id: ID пользователя
            name: Название набора
        
        Returns:
            Объект FaceReferenceSet
        """
        session = self.get_session()
        try:
            reference_set = FaceReferenceSet(
                user_id=user_id,
                name=name
            )
            session.add(reference_set)
            session.commit()
            # Загружаем атрибуты перед отвязкой от сессии
            session.refresh(reference_set)
            # Получаем ID до expunge для логирования
            set_id = reference_set.id
            session.expunge(reference_set)
            logger.debug(f"Создан набор референсов: user_id={user_id}, name={name}, id={set_id}")
            return reference_set
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при создании набора референсов: {e}")
            raise
        finally:
            session.close()
    
    def get_user_face_reference_sets(self, user_id: int) -> list:
        """
        Получить все наборы референсов пользователя.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Список объектов FaceReferenceSet
        """
        session = self.get_session()
        try:
            sets = session.query(FaceReferenceSet).filter(
                FaceReferenceSet.user_id == user_id
            ).order_by(FaceReferenceSet.created_at.desc()).all()
            
            for ref_set in sets:
                session.expunge(ref_set)
            
            return sets
        finally:
            session.close()
    
    def get_face_reference_set(self, set_id: int, user_id: int = None) -> FaceReferenceSet:
        """
        Получить набор референсов по ID.
        
        Args:
            set_id: ID набора
            user_id: ID пользователя (опционально, для проверки владельца)
        
        Returns:
            Объект FaceReferenceSet или None
        """
        session = self.get_session()
        try:
            query = session.query(FaceReferenceSet).filter(FaceReferenceSet.id == set_id)
            if user_id:
                query = query.filter(FaceReferenceSet.user_id == user_id)
            
            ref_set = query.first()
            if ref_set:
                session.expunge(ref_set)
            return ref_set
        finally:
            session.close()
    
    def update_face_reference_set_name(self, set_id: int, user_id: int, new_name: str) -> bool:
        """
        Обновить название набора референсов.
        
        Args:
            set_id: ID набора
            user_id: ID пользователя (для проверки владельца)
            new_name: Новое название набора
        
        Returns:
            True если успешно обновлено
        """
        session = self.get_session()
        try:
            ref_set = session.query(FaceReferenceSet).filter(
                FaceReferenceSet.id == set_id,
                FaceReferenceSet.user_id == user_id
            ).first()
            
            if ref_set:
                ref_set.name = new_name
                session.commit()
                logger.debug(f"Обновлено название набора: set_id={set_id}, new_name={new_name}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при обновлении названия набора: {e}")
            return False
        finally:
            session.close()
    
    def delete_face_reference_set(self, set_id: int, user_id: int) -> bool:
        """
        Удалить набор референсов.
        
        Args:
            set_id: ID набора
            user_id: ID пользователя (для проверки владельца)
        
        Returns:
            True если успешно удалено
        """
        session = self.get_session()
        try:
            ref_set = session.query(FaceReferenceSet).filter(
                FaceReferenceSet.id == set_id,
                FaceReferenceSet.user_id == user_id
            ).first()
            
            if ref_set:
                session.delete(ref_set)
                session.commit()
                logger.debug(f"Удален набор референсов: set_id={set_id}, user_id={user_id}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при удалении набора референсов: {e}")
            return False
        finally:
            session.close()
    
    def add_image_to_face_reference_set(self, set_id: int, file_path: str, file_hash: str = None) -> FaceReferenceSetImage:
        """
        Добавить изображение в набор референсов.
        
        Args:
            set_id: ID набора
            file_path: Путь к файлу изображения (может быть строкой или Path объектом)
            file_hash: SHA256 хеш файла (опционально)
        
        Returns:
            Объект FaceReferenceSetImage
        """
        session = self.get_session()
        try:
            # Конвертируем file_path в строку, если это Path объект
            if hasattr(file_path, '__str__') and not isinstance(file_path, str):
                file_path = str(file_path)
            
            image = FaceReferenceSetImage(
                set_id=set_id,
                file_path=file_path,
                file_hash=file_hash
            )
            session.add(image)
            session.commit()
            session.expunge(image)
            logger.debug(f"Добавлено изображение в набор: set_id={set_id}, file_path={file_path}")
            return image
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при добавлении изображения в набор: {e}")
            raise
        finally:
            session.close()
    
    def remove_image_from_face_reference_set(self, image_id: int, set_id: int, user_id: int) -> bool:
        """
        Удалить изображение из набора референсов.
        
        Args:
            image_id: ID изображения
            set_id: ID набора
            user_id: ID пользователя (для проверки владельца)
        
        Returns:
            True если успешно удалено
        """
        session = self.get_session()
        try:
            # Проверяем, что набор принадлежит пользователю
            ref_set = session.query(FaceReferenceSet).filter(
                FaceReferenceSet.id == set_id,
                FaceReferenceSet.user_id == user_id
            ).first()
            
            if not ref_set:
                return False
            
            image = session.query(FaceReferenceSetImage).filter(
                FaceReferenceSetImage.id == image_id,
                FaceReferenceSetImage.set_id == set_id
            ).first()
            
            if image:
                session.delete(image)
                session.commit()
                logger.debug(f"Удалено изображение из набора: image_id={image_id}, set_id={set_id}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при удалении изображения из набора: {e}")
            return False
        finally:
            session.close()
    
    def update_face_reference_set_image_path(self, image_id: int, new_file_path: str) -> bool:
        """
        Обновить путь к файлу изображения в наборе референсов.
        
        Args:
            image_id: ID изображения
            new_file_path: Новый путь к файлу
        
        Returns:
            True если успешно обновлено
        """
        session = self.get_session()
        try:
            # Конвертируем new_file_path в строку, если это Path объект
            if hasattr(new_file_path, '__str__') and not isinstance(new_file_path, str):
                new_file_path = str(new_file_path)
            
            image = session.query(FaceReferenceSetImage).filter(
                FaceReferenceSetImage.id == image_id
            ).first()
            
            if image:
                image.file_path = new_file_path
                session.commit()
                logger.debug(f"Обновлен путь изображения в наборе: image_id={image_id}, new_path={new_file_path}")
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Ошибка при обновлении пути изображения в наборе: {e}")
            return False
        finally:
            session.close()
    
    def get_face_reference_set_images(self, set_id: int, user_id: int = None) -> list:
        """
        Получить все изображения набора референсов.
        
        Args:
            set_id: ID набора
            user_id: ID пользователя (опционально, для проверки владельца)
        
        Returns:
            Список объектов FaceReferenceSetImage
        """
        session = self.get_session()
        try:
            query = session.query(FaceReferenceSetImage).filter(
                FaceReferenceSetImage.set_id == set_id
            )
            
            if user_id:
                # Проверяем владельца через join
                query = query.join(FaceReferenceSet).filter(
                    FaceReferenceSet.user_id == user_id
                )
            
            images = query.order_by(FaceReferenceSetImage.created_at).all()
            
            for image in images:
                session.expunge(image)
            
            return images
        finally:
            session.close()


# Создаем глобальный экземпляр менеджера БД
db_manager = DatabaseManager()
