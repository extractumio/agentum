"""
Authentication service for Agentum API.

Handles JWT token generation and validation for anonymous users.
"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jwt
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import CONFIG_DIR
from ..db.models import User

logger = logging.getLogger(__name__)

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 168  # 7 days

# Secrets file path
SECRETS_FILE: Path = CONFIG_DIR / "secrets.yaml"


class AuthService:
    """
    Service for JWT token management.

    Provides methods for token generation, validation, and user management.
    """

    def __init__(self) -> None:
        """Initialize the auth service."""
        self._jwt_secret: Optional[str] = None

    def _get_jwt_secret(self) -> str:
        """
        Get or generate the JWT secret.

        Loads from secrets.yaml if present, otherwise generates
        a new secret and persists it.
        """
        if self._jwt_secret:
            return self._jwt_secret

        secrets_data = {}

        if SECRETS_FILE.exists():
            try:
                with SECRETS_FILE.open("r", encoding="utf-8") as f:
                    secrets_data = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                logger.warning(f"Failed to parse secrets.yaml: {e}")
                secrets_data = {}

        jwt_secret = secrets_data.get("jwt_secret")

        if not jwt_secret:
            jwt_secret = secrets.token_urlsafe(32)
            secrets_data["jwt_secret"] = jwt_secret

            # Persist the new secret
            try:
                with SECRETS_FILE.open("w", encoding="utf-8") as f:
                    yaml.dump(secrets_data, f, default_flow_style=False)
                logger.info("Generated and saved new JWT secret")
            except IOError as e:
                logger.warning(f"Failed to persist JWT secret: {e}")

        self._jwt_secret = jwt_secret
        return jwt_secret

    def generate_token(self, user_id: str) -> tuple[str, int]:
        """
        Generate a JWT token for a user.

        Args:
            user_id: The user ID to encode in the token.

        Returns:
            Tuple of (token, expires_in_seconds).
        """
        secret = self._get_jwt_secret()
        expiry = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
        expires_in = JWT_EXPIRY_HOURS * 3600

        payload = {
            "sub": user_id,
            "exp": expiry,
            "iat": datetime.now(timezone.utc),
            "type": "access",
        }

        token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
        return token, expires_in

    def validate_token(self, token: str) -> Optional[str]:
        """
        Validate a JWT token and extract user ID.

        Args:
            token: The JWT token to validate.

        Returns:
            User ID if valid, None otherwise.
        """
        try:
            secret = self._get_jwt_secret()
            payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
            return payload.get("sub")
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Invalid token: {e}")
            return None

    async def get_or_create_anonymous_user(
        self,
        db: AsyncSession
    ) -> tuple[User, str, int]:
        """
        Create a new anonymous user with JWT token.

        Args:
            db: Database session.

        Returns:
            Tuple of (User, token, expires_in_seconds).
        """
        user_id = str(uuid.uuid4())
        user = User(id=user_id, type="anonymous")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        token, expires_in = self.generate_token(user_id)
        logger.info(f"Created anonymous user: {user_id}")

        return user, token, expires_in

    async def get_user_by_id(
        self,
        db: AsyncSession,
        user_id: str
    ) -> Optional[User]:
        """
        Get a user by ID.

        Args:
            db: Database session.
            user_id: The user ID to look up.

        Returns:
            User if found, None otherwise.
        """
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()


# Global auth service instance
auth_service = AuthService()

