# referral.py
import json
import os
import time
import asyncio
from typing import Optional, List
from dataclasses import dataclass, asdict, field

@dataclass
class ReferralData:
    """Данные реферальной системы пользователя"""
    user_id: int
    referrer_id: Optional[int] = None
    trial_start_time: Optional[float] = None
    trial_completed: bool = False
    accounts_connected_count: int = 0
    referrals_count: int = 0
    discount_50: bool = False
    used_discount: bool = False
    referred_users: List[int] = field(default_factory=list)
    trial_started: bool = False
    started_work: bool = False
    
    def to_dict(self):
        return asdict(self)

class ReferralSystem:
    def __init__(self, base_dir: str = "users"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
    
    def _get_user_path(self, user_id: int) -> str:
        """Получить путь к файлу с реферальными данными"""
        return f"{self.base_dir}/user_{user_id}/referral.json"
    
    def get_user_data(self, user_id: int) -> Optional[ReferralData]:
        """Получить данные пользователя"""
        path = self._get_user_path(user_id)
        if not os.path.exists(path):
            return None
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            return ReferralData(
                user_id=data.get('user_id', user_id),
                referrer_id=data.get('referrer_id'),
                trial_start_time=data.get('trial_start_time'),
                trial_completed=data.get('trial_completed', False),
                accounts_connected_count=data.get('accounts_connected_count', 0),
                referrals_count=data.get('referrals_count', 0),
                discount_50=data.get('discount_50', False),
                used_discount=data.get('used_discount', False),
                referred_users=data.get('referred_users', []),
                trial_started=data.get('trial_started', False),
                started_work=data.get('started_work', False)
            )
        except Exception as e:
            print(f"Ошибка чтения реферальных данных для {user_id}: {e}")
            return None
    
    def save_user_data(self, user_data: ReferralData):
        """Сохранить данные пользователя"""
        path = self._get_user_path(user_data.user_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(user_data.to_dict(), f, ensure_ascii=False, indent=2)
    
    def create_user(self, user_id: int, referrer_id: Optional[int] = None) -> ReferralData:
        """Создать нового пользователя в реферальной системе"""
        user_data = ReferralData(
            user_id=user_id,
            referrer_id=referrer_id,
            referred_users=[],
            trial_started=False,
            started_work=False
        )
        self.save_user_data(user_data)
        return user_data
    
    def mark_work_started(self, user_id: int):
        """Отметить, что пользователь начал работу"""
        user_data = self.get_user_data(user_id)
        if user_data and not user_data.started_work:
            user_data.started_work = True
            # Если пользователь пришел по реферальной ссылке и это первый запуск работы
            if user_data.referrer_id and not user_data.trial_started:
                user_data.trial_started = True
                user_data.trial_start_time = time.time()
            self.save_user_data(user_data)
    
    def update_accounts_count(self, user_id: int, accounts_count: int):
        """Обновить количество подключенных аккаунтов"""
        user_data = self.get_user_data(user_id)
        if not user_data:
            user_data = self.create_user(user_id)
        
        user_data.accounts_connected_count = accounts_count
        self.save_user_data(user_data)
        return user_data
    
    def check_trial_completion(self, user_id: int) -> bool:
        """Проверить завершение триала"""
        from config import TRIAL_DURATION
        
        user_data = self.get_user_data(user_id)
        if not user_data or not user_data.trial_start_time:
            return False
        
        # Проверяем все условия триала
        trial_ended = time.time() - user_data.trial_start_time >= TRIAL_DURATION
        has_accounts = user_data.accounts_connected_count >= 1
        started_work = user_data.started_work
        
        if trial_ended and has_accounts and started_work and not user_data.trial_completed:
            user_data.trial_completed = True
            self.save_user_data(user_data)
            return True
        
        return False
    
    def add_referral(self, referrer_id: int, referred_user_id: int) -> bool:
        """Добавить засчитанного реферала"""
        referrer_data = self.get_user_data(referrer_id)
        if not referrer_data:
            return False
        
        # Проверяем, не засчитывался ли уже этот реферал
        if referred_user_id in referrer_data.referred_users:
            return False
        
        # Проверяем, что пользователь не приглашает сам себя
        if referrer_id == referred_user_id:
            return False
        
        # Добавляем реферала
        referrer_data.referred_users.append(referred_user_id)
        referrer_data.referrals_count = len(referrer_data.referred_users)
        
        # Проверяем, достигнуто ли условие для скидки
        from config import REFERRAL_REWARD_COUNT
        if referrer_data.referrals_count >= REFERRAL_REWARD_COUNT and not referrer_data.discount_50:
            referrer_data.discount_50 = True
        
        self.save_user_data(referrer_data)
        return True
    
    def can_use_discount(self, user_id: int) -> bool:
        """Может ли пользователь использовать скидку"""
        user_data = self.get_user_data(user_id)
        if not user_data:
            return False
        
        return user_data.discount_50 and not user_data.used_discount
    
    def mark_discount_used(self, user_id: int):
        """Пометить скидку как использованную"""
        user_data = self.get_user_data(user_id)
        if not user_data:
            return
        
        user_data.used_discount = True
        user_data.discount_50 = False
        self.save_user_data(user_data)
    
    def get_referral_link(self, user_id: int, bot_username: str) -> str:
        """Получить реферальную ссылку"""
        return f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    def format_progress_message(self, user_data: ReferralData, bot_username: str) -> str:
        """Форматировать сообщение с прогрессом рефералов"""
        from config import REFERRAL_REWARD_COUNT, REFERRAL_DISCOUNT_PERCENT
        
        progress = f"👥 Приглашено: {user_data.referrals_count}/{REFERRAL_REWARD_COUNT}"
        
        if user_data.discount_50:
            status = "✅ Доступна скидка 50%"
        elif user_data.used_discount:
            status = "⏳ Скидка уже использована"
        else:
            remaining = REFERRAL_REWARD_COUNT - user_data.referrals_count
            status = f"🎯 Осталось пригласить: {remaining} человек"
        
        link = self.get_referral_link(user_data.user_id, bot_username)
        
        # Информация о триале
        trial_info = ""
        if user_data.referrer_id:
            if user_data.trial_completed:
                trial_info = "\n✅ Ваш триал завершен (реферал засчитан)"
            elif user_data.trial_started:
                from config import TRIAL_DURATION
                time_passed = time.time() - user_data.trial_start_time
                hours_left = max(0, (TRIAL_DURATION - time_passed) / 3600)
                trial_info = f"\n⏳ До засчёта реферала: {hours_left:.1f} ч."
        
        message = (
            f"📊 <b>Реферальная программа</b>\n\n"
            f"{progress}\n"
            f"🎁 <b>Награда:</b> {REFERRAL_DISCOUNT_PERCENT}% скидка на любой тариф\n\n"
            f"📎 <b>Ваша ссылка:</b>\n"
            f"<code>{link}</code>\n\n"
            f"✅ <b>Условия засчёта реферала:</b>\n"
            f"1️⃣ Зашел по вашей ссылке\n"
            f"2️⃣ Нажал «Начать работу»\n"
            f"3️⃣ Подключил 1+ аккаунт\n"
            f"4️⃣ Использовал бота 24 часа\n"
            f"5️⃣ Завершил триал полностью\n\n"
            f"{status}{trial_info}"
        )
        
        return message

# Создаем глобальный экземпляр
referral_system = ReferralSystem()