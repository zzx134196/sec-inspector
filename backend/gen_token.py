from jose import jwt
import datetime

SECRET_KEY = "7b4c9e2a8f1d6c3b5e9a2f8c7b4d9e6a3f1b8c7d5e9a2f8c7b4d9e6a3f1b8c"
ALGORITHM = "HS256"
JWT_ISSUER = "gov-backend"
JWT_AUDIENCE = "gov-platform"

payload = {
    "username": "admin",
    "is_admin": True,
    "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1),
    "iss": JWT_ISSUER,
    "aud": JWT_AUDIENCE,
}
token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
print(token)
