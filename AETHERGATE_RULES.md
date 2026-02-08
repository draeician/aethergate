# AetherGate: Vibe Coding Rules & Architecture

## 1. Core Stack
- **Language:** Python 3.12+
- **Framework:** FastAPI (Async Native)
- **Database:** SQLModel (SQLAlchemy + Pydantic)
- **Engine:** LiteLLM (Proxy Logic)
- **Format:** Ruff / Black (Line length 88)

## 2. Architecture Constraints
- **Blueprint First:** Do not generate implementation code without first confirming the file structure/imports.
- **One File, One Turn:** Generate or edit only one major component per response to maintain context.
- **Strict Typing:** All functions must have type hints (`def foo(x: int) -> str:`).
- **Error Handling:** API endpoints must return structured JSON errors, not 500 crashes.
- **Security:** - API Keys are NEVER stored in plain text (Hash SHA-256).
  - User Balance is the source of truth for access.

## 3. The "Reseller" Logic
- **Prepaid:** Users must have `balance > 0` to infer.
- **Dynamic:** Models/Tiers are defined in DB, not hardcoded.
- **Audit:** Every request is logged (Metadata always, Content optional).

## 4. Development Workflow
1. **Scaffold:** Create file/folder structure.
2. **Define:** Write `models.py` (Data Layer).
3. **Verify:** Run a test script to ensure DB writes work.
4. **Implement:** Write the API logic.
