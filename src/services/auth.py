import pickle
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import redis
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.conf.config import config
from src.database.db import get_db
from src.repository import users as repository_users


class Auth:
    now_utc = datetime.now(timezone.utc)
    SECRET_KEY = config.SECRET_KEY_JWT
    ALGORITHM = config.ALGORITHM
    cache = redis.Redis(host=config.REDIS_DOMAIN, port=config.REDIS_PORT, db=0, password=config.REDIS_PASSWORD)

    def hash_password(self, password: str) -> str:
        pwd_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed_password = bcrypt.hashpw(password=pwd_bytes, salt=salt)
        return str(hashed_password)

    def verify_password(self, plain_password, hashed_password):
        hashed_password_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password_bytes)

    def get_password_hash(self, password: str):
        return self.hash_password(password)

    oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

    async def create_access_token(self, data: dict, expires_delta: Optional[float] = None):
        to_encode = data.copy()
        if expires_delta:
            expire = self.now_utc + timedelta(seconds=expires_delta)
        else:
            expire = self.now_utc + timedelta(minutes=15)
        to_encode.update({"iat": self.now_utc, "exp": expire, "scope": "access_token"})
        encoded_access_token = jwt.encode(to_encode, self.SECRET_KEY, algorithm=self.ALGORITHM)
        return encoded_access_token

    async def create_refresh_token(self, data: dict, expires_delta: Optional[float] = None):
        to_encode = data.copy()
        if expires_delta:
            expire = self.now_utc + timedelta(seconds=expires_delta)
        else:
            expire = self.now_utc + timedelta(days=7)
        to_encode.update({"iat": self.now_utc, "exp": expire, "scope": "refresh_token"})
        encoded_refresh_token = jwt.encode(to_encode, self.SECRET_KEY, algorithm=self.ALGORITHM)
        return encoded_refresh_token

    async def decode_refresh_token(self, refresh_token: str):
        try:
            """
            Example
            PAYLOAD:DATA
            {
              "sub": "1234567890",
              "email": "John Doe",  email will be here
              "iat": 1516239022
            }
            """
            payload = jwt.decode(refresh_token, self.SECRET_KEY, algorithms=[self.ALGORITHM])
            if payload['scope'] == 'refresh_token':
                email = payload['sub']
                return email
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid scope for token')
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Could not validate credentials')

    async def get_current_user(self, token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

        try:
            # Decode JWT
            payload = jwt.decode(token, self.SECRET_KEY, algorithms=[self.ALGORITHM])
            if payload['scope'] == 'access_token':
                email = payload["sub"]
                if email is None:
                    raise credentials_exception
            else:
                raise credentials_exception
        except JWTError as e:
            raise e

        user_hash = str(email)
        user = self.cache.get(user_hash)

        if user is None:
            print("User from database")
            user = await repository_users.get_user_by_email(email, db)
            if user is None:
                raise credentials_exception
            self.cache.set(user_hash, pickle.dumps(user))  # noqa
            self.cache.expire(user_hash, 300)  # noqa
        else:
            print("User from cache")
            user = pickle.loads(user)
        return user

    def create_email_token(self, data: dict):
        to_encode = data.copy()
        expire = self.now_utc + timedelta(days=1)
        to_encode.update({"iat": self.now_utc, "exp": expire})
        token = jwt.encode(to_encode, self.SECRET_KEY, algorithm=self.ALGORITHM)
        return token

    async def get_email_from_token(self, token: str):
        try:
            payload = jwt.decode(token, self.SECRET_KEY, algorithms=[self.ALGORITHM])
            email = payload["sub"]
            return email
        except JWTError as e:
            print(e)
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="Invalid token for email verification")


auth_service = Auth()