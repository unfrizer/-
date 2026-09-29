from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./delo.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})

class Base(DeclarativeBase): pass
class Profile(Base):
    __tablename__ = "profiles"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    program_id: Mapped[str] = mapped_column(String)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    items: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    message: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="sent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class ProfileIn(BaseModel):
    legal_form: Literal["ip", "ooo"]
    region: Literal["spb", "other"]
    industry: str | None = Field(None, max_length=80)
    employee_count: int = Field(ge=0, le=100000)
    business_age_months: int = Field(ge=0, le=1200)
    purpose: Literal["equipment", "development", "digital"]
    revenue: int | None = Field(None, ge=0)
    tax_debt: bool | None = None
class PlanIn(BaseModel):
    program_id: str
    deadline: datetime
    @field_validator("deadline")
    @classmethod
    def future(cls, value):
        if value.tzinfo is None: raise ValueError("deadline must include timezone")
        if value <= datetime.now(timezone.utc): raise ValueError("deadline must be in the future")
        return value

def load_programs() -> list[dict]:
    candidates = [Path("/app/data-source/demo_support_programs.json"), Path(__file__).parents[2] / "data/demo_support_programs.json"]
    for path in candidates:
        if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
    raise RuntimeError("demo catalogue is unavailable")
PROGRAMS = load_programs()
ORDER = {"eligible": 0, "check_needed": 1, "not_eligible": 2}

def evaluate(program: dict, profile: dict | None) -> dict:
    passed=[]; failed=[]; unknown=[]; known_weight=passed_weight=0; required_fail=False
    for rule in program["rules"]:
        actual = (profile or {}).get(rule["field"])
        if actual is None or actual == "": outcome="unknown"
        else:
            op, expected = rule["operator"], rule["value"]
            outcome = "pass" if ((op=="eq" and actual==expected) or (op=="in" and actual in expected) or (op=="gte" and actual>=expected) or (op=="lte" and actual<=expected) or (op=="contains" and expected in actual) or (op=="exists" and bool(actual)==expected)) else "fail"
        item={"field": rule["field"], "explanation": rule["explanation"], "required": rule["required"], "outcome": outcome}
        if outcome == "pass": passed.append(item); known_weight += rule["weight"]; passed_weight += rule["weight"]
        elif outcome == "fail": failed.append(item); known_weight += rule["weight"]; required_fail |= rule["required"]
        else: unknown.append(item)
    score = round(100 * passed_weight / known_weight) if known_weight else 0
    status = "not_eligible" if required_fail or score < 50 else "check_needed" if unknown or score < 80 else "eligible"
    return {"score": score, "status": status, "passed_rules": passed, "failed_rules": failed, "unknown_rules": unknown, "calculated_at": datetime.now(timezone.utc).isoformat()}

def get_user(x_demo_user: str | None) -> str: return x_demo_user or "demo-user"
def db() -> Session: return Session(engine)
def api_error(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message, "details": {}, "request_id": str(uuid.uuid4())})

app = FastAPI(title="ДЕЛО API", version="0.1.0")
origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET","POST","PUT","PATCH"], allow_headers=["Content-Type","X-Demo-User"])
@app.exception_handler(HTTPException)
async def errors(_: Request, exc: HTTPException):
    return __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content=exc.detail)
@app.on_event("startup")
def startup(): Base.metadata.create_all(engine)
@app.get("/health/live")
def live(): return {"status":"ok"}
@app.get("/health/ready")
def ready():
    with db() as s: s.execute(select(Profile).limit(1))
    return {"status":"ok"}
@app.post("/api/v1/session/max")
def session(): return {"user_id":"demo-user", "mode":"mock"}
@app.get("/api/v1/profile")
def get_profile(x_demo_user: str | None = Header(None)):
    with db() as s:
        row=s.get(Profile,get_user(x_demo_user)); return row.payload if row else None
@app.put("/api/v1/profile")
def put_profile(payload: ProfileIn, x_demo_user: str | None = Header(None)):
    user=get_user(x_demo_user)
    with db() as s:
        row=s.get(Profile,user)
        if row: row.payload=payload.model_dump(); row.updated_at=datetime.now(timezone.utc)
        else: s.add(Profile(user_id=user,payload=payload.model_dump()))
        s.commit()
    return payload
def profile_for(user: str) -> dict | None:
    with db() as s:
        r=s.get(Profile,user); return r.payload if r else None
@app.get("/api/v1/support-programs")
def catalogue(x_demo_user: str | None = Header(None)):
    profile=profile_for(get_user(x_demo_user)); results=[]
    for p in PROGRAMS:
        m=evaluate(p,profile); results.append({k:v for k,v in p.items() if k not in ("rules","documents")} | {"match":m})
    return sorted(results,key=lambda r:(ORDER[r["match"]["status"]],-r["match"]["score"],r["deadline"]))
@app.get("/api/v1/support-programs/{program_id}")
def details(program_id: str, x_demo_user: str | None = Header(None)):
    p=next((x for x in PROGRAMS if x["id"]==program_id),None)
    if not p: api_error(404,"not_found","Программа не найдена")
    return p | {"match":evaluate(p,profile_for(get_user(x_demo_user)))}
@app.post("/api/v1/support-programs/{program_id}/match")
def match(program_id: str, x_demo_user: str | None = Header(None)): return details(program_id,x_demo_user)
@app.post("/api/v1/plans")
def create_plan(payload: PlanIn, x_demo_user: str | None = Header(None)):
    p=next((x for x in PROGRAMS if x["id"]==payload.program_id),None)
    if not p: api_error(404,"not_found","Программа не найдена")
    plan=Plan(id=str(uuid.uuid4()),user_id=get_user(x_demo_user),program_id=p["id"],deadline=payload.deadline,items=[{"id":str(i),"title":d,"done":False} for i,d in enumerate(p["documents"])])
    with db() as s: s.add(plan); s.commit(); s.refresh(plan)
    return serialize_plan(plan)
def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
def serialize_plan(p: Plan): return {"id":p.id,"program_id":p.program_id,"deadline":as_utc(p.deadline).isoformat(),"items":p.items,"created_at":as_utc(p.created_at).isoformat()}
@app.get("/api/v1/plans")
def plans(x_demo_user: str | None = Header(None)):
    with db() as s: return [serialize_plan(p) for p in s.scalars(select(Plan).where(Plan.user_id==get_user(x_demo_user))).all()]
@app.patch("/api/v1/plans/{plan_id}/items/{item_id}")
def toggle(plan_id: str,item_id: str,payload: dict,x_demo_user: str | None = Header(None)):
    with db() as s:
        p=s.get(Plan,plan_id)
        if not p or p.user_id != get_user(x_demo_user): api_error(404,"not_found","План не найден")
        p.items=[item | ({"done":bool(payload.get("done"))} if item["id"]==item_id else {}) for item in p.items]; s.commit(); return serialize_plan(p)
@app.post("/api/v1/notifications/test")
def notify(x_demo_user: str | None = Header(None)):
    n=Notification(id=str(uuid.uuid4()),user_id=get_user(x_demo_user),message="ДЕЛО: тестовое напоминание о плане",status="sent")
    with db() as s: s.add(n); s.commit()
    return {"status":"sent","mode":"mock"}
@app.get("/api/v1/tasks")
def tasks(x_demo_user: str | None = Header(None)):
    return [{"id":p["id"],"title":"Подготовить заявку","due_at":p["deadline"],"status":"done" if all(i["done"] for i in p["items"]) else "active"} for p in plans(x_demo_user)]
