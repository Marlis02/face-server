from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
import os

load_dotenv()

MASTER_DB_URL = os.getenv("MASTER_DB_URL")

master_engine = create_engine(MASTER_DB_URL)

MasterSession = sessionmaker(
    bind=master_engine,
    autocommit=False,
    autoflush=False,
)

MasterBase = declarative_base()


def get_master_db():
    db = MasterSession()
    try:
        yield db
    finally:
        db.close()