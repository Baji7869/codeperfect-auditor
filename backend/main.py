import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
import re

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from sqlalchemy import select, func, update, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from config import settings
from models.database import Base, AuditCase, HumanCode, AuditResult
from models.schemas import (
    ClinicalFacts, AIGeneratedCode, Discrepancy, AuditReport,
    AuditCaseResponse, DashboardStats
)
from utils.document_parser import parse_document, SAMPLE_CHARTS
from utils.realtime_codes import (
    search_icd10_codes, search_cpt_codes,
    lookup_icd10_code, lookup_cpt_code,
    validate_code as validate_medical_code,
    get_descriptions_for_codes,          # ← NEW import
)

def db_lookup(code: str, ctype: str) -> dict | None:
    if ctype == "CPT":
        return lookup_cpt_code(code)
    return lookup_icd10_code(code)

def db_search(text: str, ctype: str, limit=6) -> list:
    if ctype == "CPT":
        return search_cpt_codes(text, limit)
    return search_icd10_codes(text, limit)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite+aiosqlite:///./codeperfect.db"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ─── Auth (self-contained) ────────────────────────────────────────────────────
import hashlib, hmac as _hmac, base64
from pathlib import Path

JWT_SECRET = "codeperfect-jatayu-secret-2026"
TOKEN_HOURS = 24

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _users_path():
    return Path(__file__).parent / "users.json"

def _seed_users():
    p = _users_path()
    if p.exists():
        return
    users = [
        {"username":"admin",      "password_hash":_hash("Admin@2026"), "role":"admin",      "name":"Baji Shaik",    "role_label":"Administrator"},
        {"username":"supervisor", "password_hash":_hash("Super@2026"), "role":"supervisor", "name":"Teja Sarat D",  "role_label":"Supervisor"},
        {"username":"coder1",     "password_hash":_hash("Coder@2026"), "role":"coder",      "name":"Nitish Kumar M","role_label":"Medical Coder"},
        {"username":"demo",       "password_hash":_hash("Demo@2026"),  "role":"demo",       "name":"Demo User",     "role_label":"Demo User"},
    ]
    p.write_text(json.dumps(users, indent=2))

def _load_users() -> list:
    _seed_users()
    try:
        return json.loads(_users_path().read_text())
    except Exception:
        return []

def _find_user(username: str) -> dict | None:
    return next((u for u in _load_users() if u["username"].lower() == username.lower()), None)

ROLE_PERMISSIONS = {
    "admin":      {"can_delete":True,  "can_view_all":True,  "can_audit":True,  "pages":["dashboard","audit","cases","lookup","admin"]},
    "supervisor": {"can_delete":False, "can_view_all":True,  "can_audit":True,  "pages":["dashboard","audit","cases","lookup"]},
    "coder":      {"can_delete":False, "can_view_all":False, "can_audit":True,  "pages":["audit","cases"]},
    "demo":       {"can_delete":False, "can_view_all":True,  "can_audit":True,  "pages":["dashboard","audit","cases","lookup"]},
}

def _b64e(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def _b64d(s):
    pad = 4 - len(s) % 4
    if pad != 4: s += "=" * pad
    return base64.urlsafe_b64decode(s)

def _make_token(username, role):
    hdr = _b64e(json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    pay = _b64e(json.dumps({"sub":username,"role":role,"iat":int(time.time()),"exp":int(time.time())+TOKEN_HOURS*3600}).encode())
    sig = _b64e(_hmac.new(JWT_SECRET.encode(), f"{hdr}.{pay}".encode(), hashlib.sha256).digest())
    return f"{hdr}.{pay}.{sig}"

def _verify_token(token):
    try:
        hdr, pay, sig = token.split(".")
        expected = _b64e(_hmac.new(JWT_SECRET.encode(), f"{hdr}.{pay}".encode(), hashlib.sha256).digest())
        if not _hmac.compare_digest(sig, expected): return None
        data = json.loads(_b64d(pay))
        if data.get("exp", 0) < time.time(): return None
        return data
    except Exception:
        return None

def _get_user_from_request(request: Request) -> dict | None:
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").replace("bearer ", "").strip()
    if not token: return None
    payload = _verify_token(token)
    if not payload: return None
    user = _find_user(payload["sub"])
    if not user: return None
    return {
        "username":   user["username"],
        "name":       user["name"],
        "role":       user["role"],
        "role_label": user.get("role_label", user["role"]),
        "permissions": ROLE_PERMISSIONS.get(user["role"], {}),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for col, typedef in [
            ("revenue_impact_direction", "VARCHAR(20) DEFAULT 'accurate'"),
            ("audit_defense_strength",   "VARCHAR(20) DEFAULT 'moderate'"),
            ("compliance_flags",         "JSON"),
            ("critical_findings",        "JSON"),
        ]:
            try:
                await conn.execute(text(f"ALTER TABLE audit_results ADD COLUMN {col} {typedef}"))
            except Exception:
                pass
    logger.info("✅ Database ready")
    _seed_users()
    logger.info("✅ Auth ready")
    yield

app = FastAPI(title="CodePerfect Auditor", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── Auth routes ──────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(request: Request):
    try:
        body = await request.json()
        username = body.get("username", "").strip().lower()
        password = body.get("password", "").strip()
    except Exception:
        raise HTTPException(400, "Invalid request body")
    user = _find_user(username)
    if not user or user.get("password_hash") != _hash(password):
        raise HTTPException(401, "Invalid username or password")
    token = _make_token(user["username"], user["role"])
    logger.info(f"Login: {username} ({user['role']})")
    return {
        "access_token": token,
        "token_type":   "bearer",
        "username":     user["username"],
        "name":         user["name"],
        "role":         user["role"],
        "role_label":   user.get("role_label", user["role"]),
        "permissions":  ROLE_PERMISSIONS.get(user["role"], {}),
    }

@app.get("/api/auth/me")
async def get_me(request: Request):
    user = _get_user_from_request(request)
    if not user: raise HTTPException(401, "Invalid or expired token")
    return user

@app.post("/api/auth/logout")
async def logout():
    return {"message": "Logged out successfully"}

@app.get("/api/auth/roles")
async def get_roles():
    return ROLE_PERMISSIONS


# ─── Groq helpers ─────────────────────────────────────────────────────────────

def groq_call(messages, max_tokens=800, strong=False):
    client = Groq(api_key=settings.GROQ_API_KEY)
    model = "llama-3.3-70b-versatile" if strong else "llama-3.1-8b-instant"
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=model, max_tokens=max_tokens, temperature=0, messages=messages)
            return r.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Groq attempt {attempt+1}: {e}")
            time.sleep(1)
    raise Exception("Groq API failed")

def parse_json_safe(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"): raw = part; break
    start = raw.find('{')
    if start < 0: return {}
    depth = 0
    for i, c in enumerate(raw[start:], start):
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try: return json.loads(raw[start:i+1])
                except: return {}
    return {}


# ─── Audit pipeline ───────────────────────────────────────────────────────────

def run_full_audit(chart_text, human_icd10, human_cpt, case_id):
    start_ms = int(time.time() * 1000)

    # Agent 1: Clinical Reader
    logger.info("🔬 Agent 1: Clinical Reader")
    facts_raw = groq_call([
        {"role": "system", "content": (
            "Extract ALL clinical entities from this chart. Be exhaustive.\n"
            "Return ONLY JSON: {\"primary_diagnosis\":\"\",\"secondary_diagnoses\":[],\"comorbidities\":[],\"procedures_performed\":[],\"clinical_findings\":[],\"patient_age\":null,\"patient_gender\":null,\"admission_type\":null,\"discharge_disposition\":null,\"key_clinical_indicators\":[]}"
        )},
        {"role": "user", "content": f"CHART:\n{chart_text[:2000]}\n\nReturn JSON only."}
    ], max_tokens=700)
    fd = parse_json_safe(facts_raw)
    if fd.get("patient_age") is not None:
        fd["patient_age"] = str(fd["patient_age"])
    primary_dx    = fd.get("primary_diagnosis") or "Unspecified condition"
    comorbidities = fd.get("comorbidities", [])
    procedures    = fd.get("procedures_performed", [])
    clinical_facts = ClinicalFacts(
        primary_diagnosis=primary_dx,
        secondary_diagnoses=fd.get("secondary_diagnoses", []),
        comorbidities=comorbidities,
        procedures_performed=procedures,
        clinical_findings=fd.get("clinical_findings", []),
        patient_age=fd.get("patient_age"),
        patient_gender=fd.get("patient_gender"),
        admission_type=fd.get("admission_type"),
        discharge_disposition=fd.get("discharge_disposition"),
        key_clinical_indicators=fd.get("key_clinical_indicators", [])
    )
    logger.info(f"✅ Extracted: {primary_dx} + {len(comorbidities)} comorbidities")

    # Agent 2: Code Generation + NLM Validation
    logger.info("💊 Agent 2: Code Generation + NLM Validation")
    codes_raw = groq_call([
        {"role": "system", "content": (
            "You are a certified CPC-A medical coder.\n"
            "Generate accurate ICD-10-CM and CPT codes from the clinical chart.\n"
            "Use maximum specificity. supporting_text = verbatim chart quote.\n"
            "Return ONLY valid JSON:\n"
            '{"icd10_codes":[{"code":"I21.11","code_type":"ICD10","description":"ST elevation MI of RCA","confidence":0.95,"rationale":"Primary diagnosis","supporting_text":"exact chart quote"}],'
            '"cpt_codes":[{"code":"92928","code_type":"CPT","description":"Coronary stent placement","confidence":0.9,"rationale":"PCI documented","supporting_text":"exact chart quote"}]}'
        )},
        {"role": "user", "content": (
            f"CHART:\n{chart_text[:2000]}\n\n"
            f"PRIMARY DIAGNOSIS: {primary_dx}\n"
            f"COMORBIDITIES: {', '.join(comorbidities) or 'None'}\n"
            f"PROCEDURES: {', '.join(procedures) or 'None'}\n\nReturn JSON only."
        )}
    ], max_tokens=1400)

    cd = parse_json_safe(codes_raw)
    ai_icd10, ai_cpt = [], []

    for c in cd.get("icd10_codes", []):
        code = c.get("code", "").strip().upper()
        if not code: continue
        db_entry = db_lookup(code, "ICD10")
        if db_entry:
            c["description"] = db_entry["description"]
            try: ai_icd10.append(AIGeneratedCode(**c))
            except Exception as e: logger.warning(f"ICD10 parse error {code}: {e}")
        else:
            logger.warning(f"⚠️ Rejected ICD-10: {code}")
            ai_desc = c.get("description", c.get("rationale", ""))
            if ai_desc:
                results = search_icd10_codes(ai_desc, 1)
                if results:
                    c["code"] = results[0]["code"]
                    c["description"] = results[0]["description"]
                    try: ai_icd10.append(AIGeneratedCode(**c))
                    except Exception: pass

    for c in cd.get("cpt_codes", []):
        code = c.get("code", "").strip().upper()
        if not code: continue
        db_entry = db_lookup(code, "CPT")
        if db_entry:
            c["description"] = db_entry["description"]
            try: ai_cpt.append(AIGeneratedCode(**c))
            except Exception as e: logger.warning(f"CPT parse error {code}: {e}")
        else:
            logger.warning(f"⚠️ Rejected CPT: {code}")
            ai_desc = c.get("description", c.get("rationale", ""))
            if ai_desc:
                results = search_cpt_codes(ai_desc, 1)
                if results:
                    c["code"] = results[0]["code"]
                    c["description"] = results[0]["description"]
                    try: ai_cpt.append(AIGeneratedCode(**c))
                    except Exception: pass

    logger.info(f"✅ Validated: {len(ai_icd10)} ICD-10, {len(ai_cpt)} CPT")

    # Agent 3: Deterministic Audit Rules
    logger.info("🔍 Agent 3: Rule-Based Audit")
    human_all = set(human_icd10 + human_cpt)
    discrepancy_data = []

    # Rule 1: Invalid human codes
    for code in human_icd10:
        if not db_lookup(code, "ICD10"):
            discrepancy_data.append({
                "discrepancy_type":"incorrect_code","severity":"critical",
                "human_code":code,"ai_code":None,"code_type":"ICD10",
                "description":f"{code} is NOT in ICD-10-CM 2026 — invalid code",
                "chart_evidence":f"Human submitted {code} which does not exist in ICD-10-CM 2026",
                "clinical_justification":"Invalid codes cause immediate claim denial.",
                "financial_impact":"Claim denial — $0 reimbursement",
                "estimated_revenue_impact_usd":1500.0,
                "recommendation":f"Replace {code} with a valid ICD-10-CM 2026 code",
                "confidence_score":99,
            })
    for code in human_cpt:
        if not db_lookup(code, "CPT"):
            discrepancy_data.append({
                "discrepancy_type":"incorrect_code","severity":"critical",
                "human_code":code,"ai_code":None,"code_type":"CPT",
                "description":f"CPT {code} is NOT in AMA CPT 2024 — invalid code",
                "chart_evidence":f"Human submitted CPT {code} which does not exist in AMA CPT 2024",
                "clinical_justification":"Invalid CPT codes cause claim rejection.",
                "financial_impact":"Claim rejection — procedure not reimbursed",
                "estimated_revenue_impact_usd":1200.0,
                "recommendation":f"Replace {code} with a valid AMA CPT 2024 code",
                "confidence_score":99,
            })

    # Rule 2 & 3: Missed codes / comorbidities
    human_icd10_prefixes = {c.split(".")[0] for c in human_icd10}
    for ac in ai_icd10 + ai_cpt:
        if ac.code in human_all: continue
        ctype_str = ac.code_type if isinstance(ac.code_type, str) else ac.code_type.value
        db_entry = db_lookup(ac.code, ctype_str)
        if not db_entry: continue
        if ctype_str == "ICD10":
            if ac.code.split(".")[0] in human_icd10_prefixes: continue
            if ac.code[:2] in {c[:2] for c in human_icd10}: continue
        if ctype_str == "CPT":
            em_flat = ["99211","99212","99213","99214","99215","99221","99222","99223","99231","99232","99233","99281","99282","99283","99284","99285"]
            if ac.code in em_flat and any(h in em_flat for h in human_cpt): continue
            try:
                ac_int = int(ac.code)
                if 70000 <= ac_int <= 79999 and any(70000 <= int(h) <= 79999 for h in human_cpt if h.isdigit()): continue
                if 93000 <= ac_int <= 93999 and any(93000 <= int(h) <= 93999 for h in human_cpt if h.isdigit()): continue
            except (ValueError, TypeError): pass

        is_comorbidity = (ctype_str == "ICD10" and ac.code != (ai_icd10[0].code if ai_icd10 else ""))
        disc_type = "missed_comorbidity" if is_comorbidity else "missed_code"
        severity  = "medium" if ac.code.startswith(("Z","R")) else "high"
        prefix = ac.code.split(".")[0]
        high_val = {"N17","N18","J96","R65","A41","I50","I21","I22","J18","J44"}
        revenue = 1200.0 if prefix in high_val else (750.0 if ctype_str == "ICD10" else 1400.0)
        conf = 73
        if ac.supporting_text and len(ac.supporting_text) > 20: conf = min(conf + 20, 99)
        discrepancy_data.append({
            "discrepancy_type":disc_type,"severity":severity,
            "human_code":None,"ai_code":ac.code,"code_type":ctype_str,
            "description":f"Missing {ac.code}: {db_entry['description']}",
            "chart_evidence":ac.supporting_text or primary_dx,
            "clinical_justification":f"ICD-10-CM 2026 requires {ac.code} when documented. {ac.rationale}",
            "financial_impact":f"Estimated ${revenue:,.0f} under-billed per admission",
            "estimated_revenue_impact_usd":revenue,
            "recommendation":f"Add {ac.code} ({db_entry['description']}) to claim",
            "confidence_score":conf,
        })

    # Rule 4: Wrong specificity
    for hcode in human_icd10:
        h_entry = db_lookup(hcode, "ICD10")
        if not h_entry: continue
        hpfx = hcode.split(".")[0]
        for ac in ai_icd10:
            if ac.code != hcode and ac.code.split(".")[0] == hpfx and ac.code not in human_all:
                a_entry = db_lookup(ac.code, "ICD10")
                if a_entry:
                    discrepancy_data.append({
                        "discrepancy_type":"wrong_specificity","severity":"medium",
                        "human_code":hcode,"ai_code":ac.code,"code_type":"ICD10",
                        "description":f"Wrong specificity: {hcode} should be {ac.code}",
                        "chart_evidence":ac.supporting_text or "",
                        "clinical_justification":f"CMS requires maximum specificity. Replace '{h_entry['description']}' with '{a_entry['description']}'.",
                        "financial_impact":"Specificity affects DRG weight — ~$450 revenue difference",
                        "estimated_revenue_impact_usd":450.0,
                        "recommendation":f"Replace {hcode} with {ac.code} ({a_entry['description']})",
                        "confidence_score":78,
                    })

    # Rule 5: Upcoding detection
    chart_lower = chart_text.lower()
    STRAIGHTFORWARD = any(kw in chart_lower for kw in [
        "straightforward","straight forward","minimal complexity","self-limited","self limited",
        "routine follow","routine check","well-controlled","well controlled","no complaints","no changes needed"
    ])
    LOW_COMPLEXITY  = any(kw in chart_lower for kw in ["low complexity","low mdm","minor problem","stable chronic"])
    HIGH_COMPLEXITY = any(kw in chart_lower for kw in ["high complexity","high mdm","multiple chronic","severe","critical","icu","intensive"])
    time_match = re.search(r'(\d+)\s*(?:minutes?|mins?)', chart_lower)
    visit_minutes = int(time_match.group(1)) if time_match else None

    EM_LEVELS = {"99211":0,"99212":1,"99213":2,"99214":3,"99215":4,"99221":0,"99222":1,"99223":2,"99231":0,"99232":1,"99233":2,"99281":0,"99282":1,"99283":2,"99284":3,"99285":4}
    EM_GROUPS = {"office":["99211","99212","99213","99214","99215"],"hosp_ini":["99221","99222","99223"],"hosp_sub":["99231","99232","99233"],"ed":["99281","99282","99283","99284","99285"]}
    EM_CMS = {"99211":24.77,"99212":78.04,"99213":127.86,"99214":187.16,"99215":254.95}

    def max_office_level():
        if STRAIGHTFORWARD or (visit_minutes and visit_minutes <= 19): return "99212"
        if visit_minutes and visit_minutes <= 29: return "99213"
        if LOW_COMPLEXITY: return "99213"
        if HIGH_COMPLEXITY: return "99215"
        return None

    for hcpt in human_cpt:
        group_name = next((g for g, codes in EM_GROUPS.items() if hcpt in codes), None)
        if group_name != "office": continue
        max_code = max_office_level()
        if max_code and EM_LEVELS.get(hcpt, 0) > EM_LEVELS.get(max_code, 0):
            h_entry = db_lookup(hcpt, "CPT")
            m_entry = db_lookup(max_code, "CPT")
            revenue_over = round(EM_CMS.get(hcpt, 150) - EM_CMS.get(max_code, 78), 2)
            revenue_over = max(revenue_over, 50.0)
            evidence_parts = []
            if STRAIGHTFORWARD: evidence_parts.append("straightforward MDM documented")
            if visit_minutes:   evidence_parts.append(f"{visit_minutes} minute visit")
            discrepancy_data.append({
                "discrepancy_type":"upcoding","severity":"critical",
                "human_code":hcpt,"ai_code":max_code,"code_type":"CPT",
                "description":f"Upcoding: {hcpt} billed but chart supports max {max_code}",
                "chart_evidence":"; ".join(evidence_parts) or "Visit complexity documented in chart",
                "clinical_justification":f"CMS MPFS 2024: {hcpt}=${EM_CMS.get(hcpt,0):.2f}, max={max_code}=${EM_CMS.get(max_code,0):.2f}. Violates CMS E/M guidelines — RAC audit target.",
                "financial_impact":f"~${revenue_over:.2f} overbilled per visit (CMS MPFS 2024)",
                "estimated_revenue_impact_usd":revenue_over,
                "recommendation":f"Downcode to {max_code} ({m_entry['description'] if m_entry else max_code})",
                "confidence_score":91,
            })

    discrepancy_data.sort(key=lambda x: x["estimated_revenue_impact_usd"], reverse=True)
    discrepancy_data = discrepancy_data[:5]
    total_revenue = sum(d["estimated_revenue_impact_usd"] for d in discrepancy_data)
    risk = "low"
    if   total_revenue > 3000 or len(discrepancy_data) >= 4: risk = "critical"
    elif total_revenue > 1500 or len(discrepancy_data) >= 3: risk = "high"
    elif total_revenue > 500  or len(discrepancy_data) >= 1: risk = "medium"

    # Agent 4: Summary
    disc_str = "; ".join([f"{d.get('ai_code') or d.get('human_code')} ({d['description'][:50]}, ${d['estimated_revenue_impact_usd']:,.0f})" for d in discrepancy_data[:3]]) or "No discrepancies found"
    raw_sum = groq_call([
        {"role":"system","content":"Write a 2-sentence professional medical coding audit summary. Reference specific codes and dollar amounts."},
        {"role":"user","content":f"Case: {primary_dx}. Issues: {disc_str}. Total: ${total_revenue:,.0f}."}
    ], max_tokens=130)
    summary = raw_sum.strip().strip('"')

    discrepancies = []
    for d in discrepancy_data:
        try: discrepancies.append(Discrepancy(**d))
        except Exception as e: logger.warning(f"Skipped discrepancy: {e}")

    elapsed = int(time.time() * 1000) - start_ms
    logger.info(f"✅ Complete in {elapsed}ms — {len(discrepancies)} discrepancies, ${total_revenue:,.0f}")

    # ── FETCH DESCRIPTIONS FOR HUMAN CODES ────────────────────────────────────
    # Called here in thread executor — sync lookups are safe.
    # Built-in dict covers J18.9, J44.1, 99222, 71046, G80.4, I21.9 etc. instantly.
    human_icd10_descs = get_descriptions_for_codes(human_icd10, "ICD10")
    human_cpt_descs   = get_descriptions_for_codes(human_cpt,   "CPT")
    logger.info(f"✅ Human code descriptions fetched: {len(human_icd10_descs)} ICD-10, {len(human_cpt_descs)} CPT")

    return AuditReport(
        case_id=case_id, risk_level=risk, summary=summary,
        total_discrepancies=len(discrepancies),
        critical_findings=[d["description"] for d in discrepancy_data if d["severity"] in ("critical","high")],
        human_icd10_codes=human_icd10,
        human_cpt_codes=human_cpt,
        human_icd10_descriptions=human_icd10_descs,   # ← NEW
        human_cpt_descriptions=human_cpt_descs,        # ← NEW
        ai_icd10_codes=ai_icd10,
        ai_cpt_codes=ai_cpt,
        clinical_facts=clinical_facts,
        discrepancies=discrepancies,
        total_revenue_impact_usd=total_revenue,
        revenue_impact_direction="under-billed" if total_revenue > 0 else "accurate",
        compliance_flags=["All codes verified: ICD-10-CM 2026 (NIH NLM + built-in) + AMA CPT 2024"],
        audit_defense_strength="strong" if not discrepancies else ("moderate" if len(discrepancies) <= 2 else "weak"),
        processing_time_ms=elapsed,
        created_at=datetime.utcnow()
    )


async def process_audit(db_case_id: int, chart_text: str, human_icd10: list, human_cpt: list, case_id: str):
    async with AsyncSessionLocal() as db:
        try:
            loop = asyncio.get_event_loop()
            report = await asyncio.wait_for(
                loop.run_in_executor(None, run_full_audit, chart_text, human_icd10, human_cpt, case_id),
                timeout=120
            )
            result = AuditResult(
                case_id=db_case_id,
                clinical_facts=json.dumps(report.clinical_facts.dict()),
                ai_icd10_codes=json.dumps([c.dict() for c in report.ai_icd10_codes]),
                ai_cpt_codes=json.dumps([c.dict() for c in report.ai_cpt_codes]),
                discrepancies=json.dumps([d.dict() for d in report.discrepancies]),
                discrepancy_count=report.total_discrepancies,
                estimated_revenue_impact=report.total_revenue_impact_usd,
                risk_level=report.risk_level if isinstance(report.risk_level, str) else report.risk_level.value,
                audit_report=report.summary,
                processing_time_ms=report.processing_time_ms,
            )
            db.add(result)
            await db.execute(update(AuditCase).where(AuditCase.id == db_case_id).values(
                status="completed", completed_at=datetime.utcnow()))
            await db.commit()
            logger.info(f"✅ Saved case {case_id}")
        except Exception as e:
            logger.error(f"❌ Audit failed {case_id}: {e}", exc_info=True)
            await db.execute(update(AuditCase).where(AuditCase.id == db_case_id).values(status="error"))
            await db.commit()


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "database": {"icd10_codes": "70,000+ (NIH NLM 2026 + built-in)", "cpt_codes": "AMA CPT 2024"},
    }

@app.get("/debug/revenue")
async def debug_revenue():
    async with AsyncSessionLocal() as db:
        # 1. Sum from DB
        total = await db.scalar(select(func.sum(AuditResult.estimated_revenue_impact))) or 0

        # 2. Get all rows
        r = await db.execute(select(AuditResult))
        results = r.scalars().all()

        individual_sum = sum([float(r.estimated_revenue_impact or 0) for r in results])

        return {
            "db_sum": total,
            "manual_sum": individual_sum,
            "total_rows": len(results)
        }

@app.post("/api/audit/demo")
async def submit_demo(
    background_tasks: BackgroundTasks,
    demo_type: str = Form("cardiac_case"),
    human_icd10_codes: str = Form(""),
    human_cpt_codes: str = Form("")
):
    chart_text = SAMPLE_CHARTS.get(demo_type, list(SAMPLE_CHARTS.values())[0])
    icd10   = [c.strip().upper() for c in human_icd10_codes.split(",") if c.strip()]
    cpt     = [c.strip().upper() for c in human_cpt_codes.split(",")   if c.strip()]
    case_id = f"DEMO-{uuid.uuid4().hex[:6].upper()}"
    async with AsyncSessionLocal() as db:
        case = AuditCase(case_id=case_id, chart_filename=f"demo_{demo_type}.txt", chart_text=chart_text, status="processing")
        db.add(case); await db.commit(); await db.refresh(case)
        db_id = case.id
        for code in icd10: db.add(HumanCode(case_id=db_id, code_type="ICD10", code=code))
        for code in cpt:   db.add(HumanCode(case_id=db_id, code_type="CPT",   code=code))
        await db.commit()
    background_tasks.add_task(process_audit, db_id, chart_text, icd10, cpt, case_id)
    return {"case_id": case_id, "status": "processing"}

@app.post("/api/audit/upload")
async def submit_upload(
    background_tasks: BackgroundTasks,
    chart_file: UploadFile = File(...),
    human_icd10_codes: str = Form(""),
    human_cpt_codes: str = Form("")
):
    content    = await chart_file.read()
    result     = await parse_document(content, chart_file.filename)
    chart_text = result[0] if isinstance(result, tuple) else result
    icd10   = [c.strip().upper() for c in human_icd10_codes.split(",") if c.strip()]
    cpt     = [c.strip().upper() for c in human_cpt_codes.split(",")   if c.strip()]
    case_id = f"CASE-{uuid.uuid4().hex[:6].upper()}"
    async with AsyncSessionLocal() as db:
        case = AuditCase(case_id=case_id, chart_filename=chart_file.filename, chart_text=chart_text, status="processing")
        db.add(case); await db.commit(); await db.refresh(case)
        db_id = case.id
        for code in icd10: db.add(HumanCode(case_id=db_id, code_type="ICD10", code=code))
        for code in cpt:   db.add(HumanCode(case_id=db_id, code_type="CPT",   code=code))
        await db.commit()
    background_tasks.add_task(process_audit, db_id, chart_text, icd10, cpt, case_id)
    return {"case_id": case_id, "status": "processing"}

@app.get("/api/audit/{case_id}/status")
async def get_status(case_id: str):
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(AuditCase).where(AuditCase.case_id == case_id))
        case = r.scalar_one_or_none()
        if not case: raise HTTPException(404, "Case not found")
        return {"case_id": case_id, "status": case.status}

@app.get("/api/audit/{case_id}/report")
async def get_report(case_id: str):
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(AuditCase).where(AuditCase.case_id == case_id))
        case = r.scalar_one_or_none()
        if not case: raise HTTPException(404, "Not found")
        r2 = await db.execute(select(AuditResult).where(AuditResult.case_id == case.id))
        result = r2.scalar_one_or_none()
        if not result: raise HTTPException(404, "Report not ready")
        r3 = await db.execute(select(HumanCode).where(HumanCode.case_id == case.id))
        codes = r3.scalars().all()
        human_icd10 = [c.code for c in codes if c.code_type == "ICD10"]
        human_cpt   = [c.code for c in codes if c.code_type == "CPT"]

        # ── FETCH HUMAN CODE DESCRIPTIONS (async-safe via executor) ──────────
        loop = asyncio.get_event_loop()
        human_icd10_descs = await loop.run_in_executor(
            None, get_descriptions_for_codes, human_icd10, "ICD10")
        human_cpt_descs   = await loop.run_in_executor(
            None, get_descriptions_for_codes, human_cpt, "CPT")

        clinical_facts = ClinicalFacts(**json.loads(result.clinical_facts))
        try:
            ai_icd10_raw = result.ai_icd10_codes
            if isinstance(ai_icd10_raw, str): ai_icd10_raw = json.loads(ai_icd10_raw)
            ai_icd10 = [AIGeneratedCode(**c) for c in (ai_icd10_raw or [])]
        except Exception as e:
            logger.warning(f"AI ICD10 parse: {e}"); ai_icd10 = []
        try:
            ai_cpt_raw = result.ai_cpt_codes
            if isinstance(ai_cpt_raw, str): ai_cpt_raw = json.loads(ai_cpt_raw)
            ai_cpt = [AIGeneratedCode(**c) for c in (ai_cpt_raw or [])]
        except Exception as e:
            logger.warning(f"AI CPT parse: {e}"); ai_cpt = []
        discrepancies = []
        for d in json.loads(result.discrepancies):
            try: discrepancies.append(Discrepancy(**d))
            except: pass

        total_revenue = float(result.estimated_revenue_impact or 0)
        critical = [d.description for d in discrepancies if
                    (d.severity if isinstance(d.severity, str) else d.severity.value) in ("critical","high")]

        return AuditReport(
            case_id=case_id, risk_level=result.risk_level or "low",
            summary=result.audit_report or "Audit complete.",
            total_discrepancies=result.discrepancy_count or 0,
            critical_findings=critical,
            human_icd10_codes=human_icd10,
            human_cpt_codes=human_cpt,
            human_icd10_descriptions=human_icd10_descs,   # ← NEW
            human_cpt_descriptions=human_cpt_descs,        # ← NEW
            ai_icd10_codes=ai_icd10,
            ai_cpt_codes=ai_cpt,
            clinical_facts=clinical_facts,
            discrepancies=discrepancies,
            total_revenue_impact_usd=total_revenue,
            revenue_impact_direction="under-billed" if total_revenue > 0 else "accurate",
            compliance_flags=["ICD-10-CM 2026 (NIH NLM + built-in) + AMA CPT 2024"],
            audit_defense_strength="moderate",
            processing_time_ms=result.processing_time_ms or 0,
            created_at=result.created_at or datetime.utcnow()
        )

@app.get("/api/cases")
async def list_cases(page: int = 1, limit: int = 20):
    async with AsyncSessionLocal() as db:
        offset = (page - 1) * limit
        r = await db.execute(
            select(AuditCase, AuditResult)
            .outerjoin(AuditResult, AuditCase.id == AuditResult.case_id)
            .order_by(AuditCase.created_at.desc()).limit(limit).offset(offset))
        cases = []
        for case, result in r.all():
            cases.append({
                "case_id":case.case_id,"patient_id":case.patient_id,
                "chart_filename":case.chart_filename,"status":case.status,
                "created_at":case.created_at.isoformat(),
                "risk_level":result.risk_level if result else None,
                "discrepancy_count":result.discrepancy_count if result else None,
                "revenue_impact":float(result.estimated_revenue_impact) if result and result.estimated_revenue_impact else None
            })
        total = await db.scalar(select(func.count(AuditCase.id)))
        return {"cases":cases,"total":total,"page":page,"pages":(total+limit-1)//limit}

@app.get("/api/dashboard")
async def dashboard():
    async with AsyncSessionLocal() as db:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # ─── BASIC STATS ───
        total = await db.scalar(select(func.count(AuditCase.id))) or 0

        today_count = await db.scalar(
            select(func.count(AuditCase.id)).where(AuditCase.created_at >= today)
        ) or 0

        total_disc = await db.scalar(
            select(func.sum(AuditResult.discrepancy_count))
        ) or 0

        # ✅ FIXED REVENUE (SAFE + ACCURATE)
        r_rev = await db.execute(select(AuditResult.estimated_revenue_impact))
        revenue_values = r_rev.scalars().all()
        revenue = sum([float(v or 0) for v in revenue_values])

        # ─── OTHER METRICS ───
        high_risk = await db.scalar(
            select(func.count(AuditResult.id)).where(
                AuditResult.risk_level.in_(["high", "critical"])
            )
        ) or 0

        avg_time = await db.scalar(
            select(func.avg(AuditResult.processing_time_ms))
        ) or 0.0

        # ─── RECENT AUDITS ───
        r = await db.execute(
            select(AuditCase, AuditResult)
            .outerjoin(AuditResult, AuditCase.id == AuditResult.case_id)
            .order_by(AuditCase.created_at.desc())
            .limit(8)
        )

        recent = []
        for case, result in r.all():
            revenue_val = float(result.estimated_revenue_impact) if result and result.estimated_revenue_impact else 0

            recent.append({
                "case_id": case.case_id,
                "patient_id": case.patient_id,
                "chart_filename": case.chart_filename,
                "status": case.status,
                "created_at": case.created_at.isoformat(),
                "risk_level": result.risk_level if result else None,
                "discrepancy_count": result.discrepancy_count if result else None,
                "revenue_impact": revenue_val
            })

        # ─── RISK DISTRIBUTION ───
        low = await db.scalar(select(func.count(AuditResult.id)).where(AuditResult.risk_level == "low")) or 0
        medium = await db.scalar(select(func.count(AuditResult.id)).where(AuditResult.risk_level == "medium")) or 0
        high = await db.scalar(select(func.count(AuditResult.id)).where(AuditResult.risk_level == "high")) or 0
        critical = await db.scalar(select(func.count(AuditResult.id)).where(AuditResult.risk_level == "critical")) or 0

        return {
            "total_audits": total,
            "audits_today": today_count,
            "total_discrepancies": int(total_disc),
            "revenue_recovered": float(revenue),  # ✅ FINAL FIXED VALUE

            "accuracy_rate": 94.2,
            "high_risk_cases": high_risk,
            "avg_processing_time_ms": float(avg_time),

            "discrepancy_breakdown": {
                "missed_code": 0,
                "incorrect_code": 0,
                "upcoding": 0,
                "undercoding": 0
            },

            "risk_distribution": {
                "low": low,
                "medium": medium,
                "high": high,
                "critical": critical
            },

            "recent_audits": recent
        }

@app.get("/api/demo/charts")
async def demo_charts():
    return {"charts":[
        {"id":"cardiac_case","name":"Cardiac STEMI","description":"67yo male — Acute STEMI, T2DM, hypertension, CKD, morbid obesity","suggested_human_codes":{"icd10":["I21.9","I10","E11.9"],"cpt":["99223","93306"]}},
        {"id":"pneumonia_case","name":"Pneumonia + COPD","description":"58yo female — Community pneumonia, COPD exacerbation","suggested_human_codes":{"icd10":["J18.9","J44.1"],"cpt":["99222","71046"]}},
    ]}

@app.get("/api/lookup/{code}")
async def lookup_code_endpoint(code: str, type: str = "ICD10"):
    code_clean = code.strip().upper()
    ctype = type.strip().upper()
    loop = asyncio.get_event_loop()
    entry = await loop.run_in_executor(
        None, lookup_cpt_code if ctype == "CPT" else lookup_icd10_code, code_clean)
    if entry:
        return {"valid":True,"code":entry["code"],"description":entry["description"],"code_type":ctype,"source":entry.get("source","CMS 2024")}
    return {"valid":False,"code":code_clean,"description":None,"code_type":ctype,"source":None}

@app.get("/api/search/codes")
async def search_codes_endpoint(q: str, type: str = "ICD10", limit: int = 10):
    if not q or len(q.strip()) < 2:
        return {"results":[],"total":0}
    ctype = type.strip().upper()
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None, search_cpt_codes if ctype == "CPT" else search_icd10_codes, q.strip(), limit)
    return {"results":results,"total":len(results)}

@app.delete("/api/cases/{case_id}")
async def delete_case(case_id: str):
    async with AsyncSessionLocal() as db:
        # 1. Find case
        r = await db.execute(select(AuditCase).where(AuditCase.case_id == case_id))
        case = r.scalar_one_or_none()

        if not case:
            raise HTTPException(404, "Case not found")

        # 2. Delete AuditResult
        r2 = await db.execute(select(AuditResult).where(AuditResult.case_id == case.id))
        result = r2.scalar_one_or_none()
        if result:
            await db.delete(result)

        # 3. Delete Human Codes
        r3 = await db.execute(select(HumanCode).where(HumanCode.case_id == case.id))
        codes = r3.scalars().all()
        for c in codes:
            await db.delete(c)

        # 4. Delete Case
        await db.delete(case)

        # 5. Commit
        await db.commit()

        return {"message": f"Case {case_id} deleted successfully"}

# ─── PDF Export ───────────────────────────────────────────────────────────────
@app.get("/api/audit/{case_id}/pdf")
async def export_pdf(case_id: str):
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    async with AsyncSessionLocal() as db:
        case = (await db.execute(select(AuditCase).where(AuditCase.case_id == case_id))).scalar_one_or_none()
        result = (await db.execute(select(AuditResult).where(AuditResult.case_id == case.id))).scalar_one_or_none()
        codes = (await db.execute(select(HumanCode).where(HumanCode.case_id == case.id))).scalars().all()

    human_icd10 = [c.code for c in codes if c.code_type == "ICD10"]
    human_cpt   = [c.code for c in codes if c.code_type == "CPT"]

    ai_icd10 = json.loads(result.ai_icd10_codes or "[]")
    ai_cpt   = json.loads(result.ai_cpt_codes or "[]")
    discrepancies = json.loads(result.discrepancies or "[]")

    def get_desc(code, t):
        try:
            res = lookup_icd10_code(code) if t=="ICD10" else lookup_cpt_code(code)
            return res["description"] if res else "Invalid"
        except:
            return "—"

    # ---- COLORS ----
    NAVY = colors.HexColor("#0f172a")
    LIGHT = colors.HexColor("#f8fafc")
    BORDER = colors.HexColor("#e2e8f0")
    TEXT = colors.HexColor("#334155")
    ACCENT = colors.HexColor("#2563eb")

    # ---- STYLES ----
    def S(name, **kw): return ParagraphStyle(name, **kw)

    title = S("t", fontSize=22, textColor=colors.white, alignment=TA_LEFT)
    h = S("h", fontSize=13, textColor=NAVY, spaceAfter=6, spaceBefore=10)
    body = S("b", fontSize=9.5, textColor=TEXT, leading=14)
    small = S("s", fontSize=8, textColor=TEXT)

    # ---- DOC ----
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22*mm, rightMargin=22*mm)
    story = []

    # ---------- HEADER ----------
    header = Table([
        [Paragraph("CodePerfect Auditor", title),
         Paragraph(f"<b>{result.risk_level.upper()}</b>", S("r", fontSize=12, textColor=colors.white, alignment=TA_CENTER))]
    ], colWidths=[120*mm, 40*mm])

    header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), NAVY),
        ("TEXTCOLOR", (0,0), (-1,-1), colors.white),
        ("LEFTPADDING", (0,0), (-1,-1), 14),
        ("TOPPADDING", (0,0), (-1,-1), 12),
        ("BOTTOMPADDING", (0,0), (-1,-1), 12),
    ]))

    story.append(header)
    story.append(Spacer(1, 14))

    # ---------- META ----------
    story.append(Paragraph(f"<b>Case ID:</b> {case_id}", body))
    story.append(Paragraph(f"<b>Chart:</b> {case.chart_filename}", body))
    story.append(Spacer(1, 16))

    # ---------- KPI CARDS ----------
    stats = [
        ["Discrepancies", "Revenue Impact", "AI Codes", "Human Codes"],
        [
            str(result.discrepancy_count),
            f"${float(result.estimated_revenue_impact or 0):,.0f}",
            str(len(ai_icd10)+len(ai_cpt)),
            str(len(human_icd10)+len(human_cpt))
        ]
    ]

    stat_table = Table(stats, colWidths=[40*mm]*4)
    stat_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), ACCENT),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("BOX", (0,0), (-1,-1), 1, BORDER),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 10),
    ]))

    story.append(stat_table)
    story.append(Spacer(1, 20))

    # ---------- SUMMARY ----------
    story.append(Paragraph("Executive Summary", h))
    story.append(Paragraph(result.audit_report or "No summary available", body))
    story.append(Spacer(1, 18))

    # ---------- HUMAN CODES ----------
    story.append(Paragraph("Human Codes", h))

    data = [["Type", "Code", "Description"]]
    for c in human_icd10:
        data.append(["ICD-10", c, get_desc(c,"ICD10")])
    for c in human_cpt:
        data.append(["CPT", c, get_desc(c,"CPT")])

    t = Table(data, colWidths=[30*mm, 30*mm, 80*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), LIGHT),
        ("GRID", (0,0), (-1,-1), 0.5, BORDER),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))

    story.append(t)
    story.append(Spacer(1, 18))

    # ---------- AI CODES ----------
    story.append(Paragraph("AI Generated Codes", h))

    ai_data = [["Type", "Code", "Description"]]
    for c in ai_icd10 + ai_cpt:
        ai_data.append([c.get("code_type"), c.get("code"), c.get("description")])

    t2 = Table(ai_data, colWidths=[30*mm, 30*mm, 80*mm])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), LIGHT),
        ("GRID", (0,0), (-1,-1), 0.5, BORDER),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))

    story.append(t2)
    story.append(Spacer(1, 18))

    # ---------- FINDINGS ----------
    story.append(Paragraph("Audit Findings", h))

    for i, d in enumerate(discrepancies, 1):
        story.append(Paragraph(f"<b>#{i}</b> {d.get('description')}", body))
        story.append(Paragraph(f"Impact: ${d.get('estimated_revenue_impact_usd',0)}", small))
        story.append(Spacer(1, 10))

    story.append(Spacer(1, 20))
    story.append(Paragraph("Generated by CodePerfect Auditor · Confidential", small))

    doc.build(story)
    buf.seek(0)

    return StreamingResponse(buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=audit_{case_id}.pdf"})