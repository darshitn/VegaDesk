import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# Support both dev and PyInstaller bundled paths
if getattr(__import__('sys'), 'frozen', False):
    # PyInstaller: keep DB next to executable for persistence
    base_dir = os.path.dirname(__import__('sys').executable)
else:
    base_dir = os.path.dirname(__file__)

DB_PATH = os.path.join(base_dir, "jarvis.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String(500), index=True, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)

class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, default="", nullable=False)
