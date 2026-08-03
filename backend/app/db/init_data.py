"""
初始化脚本：创建默认管理员账号

运行方式：python -m app.db.init_data
"""
import logging
import os

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import User, SearchProvider
from app.db.auth import get_password_hash

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_PASSWORD = "admin123"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)


def init_default_user():
    """创建默认管理员账号"""
    if ADMIN_PASSWORD == DEFAULT_ADMIN_PASSWORD:
        logger.warning(
            "ADMIN_PASSWORD 未配置，使用默认密码 admin123。"
            "生产环境请在 .env 中设置 ADMIN_PASSWORD。"
        )

    db = SessionLocal()
    try:
        # 检查是否已存在 admin 用户
        existing_user = db.query(User).filter(User.username == "admin").first()
        if existing_user:
            print("管理员账号已存在，跳过创建")
            return

        # 创建 admin 用户
        admin_user = User(
            username="admin",
            password_hash=get_password_hash(ADMIN_PASSWORD),
            is_active=True,
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)

        print(f"成功创建管理员账号:")
        print(f"  用户名：admin")
        print(f"  用户 ID: {admin_user.id}")

    except Exception as e:
        db.rollback()
        print(f"创建管理员账号失败：{e}")
        raise
    finally:
        db.close()


def seed_search_providers():
    """种子数据：确保默认搜索 Provider 存在"""
    db = SessionLocal()
    try:
        # DuckDuckGo — 免费兜底，无需 API Key
        existing = db.query(SearchProvider).filter(
            SearchProvider.provider_type == "duckduckgo"
        ).first()
        if not existing:
            db.add(SearchProvider(
                name="DuckDuckGo",
                provider_type="duckduckgo",
                enabled=True,
                priority=10,
            ))
            db.commit()
            print("种子数据：已创建 DuckDuckGo 搜索 Provider (priority=10)")
        else:
            print("DuckDuckGo 搜索 Provider 已存在，跳过")
    except Exception as e:
        db.rollback()
        print(f"种子搜索 Provider 失败：{e}")
    finally:
        db.close()


def sync_system_skills() -> None:
    """将内置标准 SKILL.md 目录同步到 Skill V2 元数据。"""
    from app.skills.service import SkillService

    db = SessionLocal()
    try:
        skills = SkillService(db).sync_system_catalog()
        db.commit()
        print(f"系统 Skill 已同步：{len(skills)} 个")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_default_user()
    seed_search_providers()
    sync_system_skills()
