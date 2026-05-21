"""
Глобальный limiter для FastAPI.

Применяется через декоратор `@limiter.limit("10/minute")` на конкретные роуты —
прежде всего на /login для защиты от brute-force подбора паролей.

Ключ — IP-адрес клиента (slowapi.util.get_remote_address). За reverse-proxy
нужно убедиться что приложение видит реальный IP (X-Forwarded-For),
иначе все запросы будут считаться от одного адреса.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
