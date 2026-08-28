import hashlib
import secrets
from datetime import datetime, timedelta, timezone

try:
    import bcrypt as _bcrypt
    _has_bcrypt = True
except ImportError:
    _has_bcrypt = False

from passlib.context import CryptContext
from jose import JWTError, jwt

# Fix bcrypt 4/5 compatibility: passlib expects bcrypt.__about__.__version__
try:
    import bcrypt as _b
    if not hasattr(_b, "__about__"):
        import types
        _b.__about__ = types.SimpleNamespace(__version__=_b.__version__ if hasattr(_b, "__version__") else "4.0.1")
except Exception:
    pass

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _truncate(p: str) -> bytes:
    b = p.encode("utf-8")
    return b[:72]


def hash_password(password: str) -> str:
    if _has_bcrypt:
        try:
            return _bcrypt.hashpw(_truncate(password), _bcrypt.gensalt()).decode()
        except Exception:
            pass
    return pwd_context.hash(password[:72])


def verify_password(plain: str, hashed: str) -> bool:
    normalized = hashed.replace("$2y$", "$2b$").replace("$2a$", "$2b$")
    # Try bcrypt directly first (works with bcrypt 5)
    if _has_bcrypt:
        try:
            return _bcrypt.checkpw(_truncate(plain), normalized.encode())
        except Exception:
            pass
    # Fallback to passlib with truncation
    try:
        return pwd_context.verify(plain[:72], normalized)
    except Exception:
        # Last resort: try truncated bytes
        try:
            return _bcrypt.checkpw(_truncate(plain), normalized.encode()) if _has_bcrypt else False
        except Exception:
            return False


def generate_token(length: int = 80) -> str:
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_jwt_token(data: dict, secret: str, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=24))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, secret, algorithm="HS256")


def decode_jwt_token(token: str, secret: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError:
        return None
