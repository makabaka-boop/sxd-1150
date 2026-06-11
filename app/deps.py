from typing import Generator, List

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserRole
from app.utils.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    username: str = payload.get("sub")
    if username is None:
        raise credentials_exception
    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if user is None:
        raise credentials_exception
    return user


def require_roles(allowed_roles: List[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {[r.value for r in allowed_roles]}",
            )
        return current_user
    return role_checker


require_admin = require_roles([UserRole.ADMIN])
require_operator = require_roles([UserRole.OPERATOR])
require_auditor = require_roles([UserRole.AUDITOR])
require_admin_or_operator = require_roles([UserRole.ADMIN, UserRole.OPERATOR])
require_admin_or_auditor = require_roles([UserRole.ADMIN, UserRole.AUDITOR])
require_all_authenticated = require_roles([UserRole.ADMIN, UserRole.OPERATOR, UserRole.AUDITOR])
