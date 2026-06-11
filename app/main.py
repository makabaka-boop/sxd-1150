from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine, SessionLocal
from app.models import User, UserRole
from app.routers import auth, venues, templates, rules, bookings, approvals
from app.utils.security import get_password_hash


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        defaults = [
            {
                "username": "admin",
                "email": "admin@example.com",
                "full_name": "系统管理员",
                "role": UserRole.ADMIN,
                "password": "admin123",
            },
            {
                "username": "operator",
                "email": "operator@example.com",
                "full_name": "预约操作员",
                "role": UserRole.OPERATOR,
                "password": "operator123",
            },
            {
                "username": "auditor",
                "email": "auditor@example.com",
                "full_name": "审批审核员",
                "role": UserRole.AUDITOR,
                "password": "auditor123",
            },
        ]
        for u in defaults:
            existing = db.query(User).filter(User.username == u["username"]).first()
            if not existing:
                user = User(
                    username=u["username"],
                    email=u["email"],
                    full_name=u["full_name"],
                    role=u["role"],
                    hashed_password=get_password_hash(u["password"]),
                )
                db.add(user)
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    description="场地预约管理系统后端API - 支持审批工作流、版本控制、软/硬删除混合机制",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(venues.router)
app.include_router(templates.router)
app.include_router(rules.router)
app.include_router(bookings.router)
app.include_router(approvals.router)


@app.get("/", tags=["系统"])
def root():
    return {
        "app": settings.app_name,
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "default_users": [
            {"username": "admin", "password": "admin123", "role": "admin"},
            {"username": "operator", "password": "operator123", "role": "operator"},
            {"username": "auditor", "password": "auditor123", "role": "auditor"},
        ],
    }


@app.get("/health", tags=["系统"])
def health_check():
    return {"status": "healthy"}
