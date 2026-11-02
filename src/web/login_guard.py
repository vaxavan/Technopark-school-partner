"""Защита входа от подбора пароля.

После 5 неудачных попыток для одного e-mail с одного адреса (или 20 попыток с одного адреса для любых
e-mail) вход с этого адреса закрывается на 15 минут, даже с верным паролем. Счётчики хранятся в кэше
(на сервере — Redis), поэтому общие для всех процессов gunicorn."""
from django.core.cache import cache

PER_ACCOUNT = 5
PER_ADDRESS = 20
WINDOW_SECONDS = 15 * 60
LOCKED_MESSAGE = "Слишком много неудачных попыток входа. Попробуйте через 15 минут."


def _keys(ip, email):
    email = (email or "").strip().lower()
    return (f"login-fail:{ip}:{email}", PER_ACCOUNT), (f"login-fail:{ip}", PER_ADDRESS)


def is_locked(ip, email):
    return any(cache.get(key, 0) >= limit for key, limit in _keys(ip, email))


def register_failure(ip, email):
    for key, _ in _keys(ip, email):
        cache.add(key, 0, WINDOW_SECONDS)
        try:
            cache.incr(key)
        except ValueError:          # ключ успел истечь между add и incr
            cache.set(key, 1, WINDOW_SECONDS)


def register_success(ip, email):
    cache.delete(_keys(ip, email)[0][0])
