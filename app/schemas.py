from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# --- Contestant Schemas ---

class RegisterRequest(BaseModel):
    team_name: str = Field(..., min_length=1, max_length=30, description="Unique team name")
    member1_name: str = Field(..., min_length=1, max_length=50, description="Primary contestant name")
    member2_name: Optional[str] = Field(None, max_length=50, description="Optional secondary member name")

class RegisterResponse(BaseModel):
    success: bool
    team_name: str
    generated_password: str
    message: str

class LoginRequest(BaseModel):
    team_name: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=1)

class LoginResponse(BaseModel):
    success: bool
    team_name: str
    session_token: str
    message: str

class AnswerSubmitRequest(BaseModel):
    question_number: int = Field(..., ge=1, le=50)
    selected_answer: str = Field(..., pattern=r"^[A-D]$")

class QuizAutoSubmitRequest(BaseModel):
    reason: str = Field(..., pattern=r"^(manual|timeout|visibility_change|window_blur|administrator_action|multiple_tabs|violation_limit)$")

class QuizViolationRequest(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=100)
    event_type: str = Field("visibility_change", max_length=50)

class QuizViolationResponse(BaseModel):
    status: str
    violation_count: int
    remaining_warnings: int
    message: str

class QuizSubmissionResponse(BaseModel):
    success: bool
    message: str
    submission_reason: str
    submitted_at: str

# --- Administrator Schemas ---

class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)

class AdminLoginResponse(BaseModel):
    success: bool
    username: str
    session_token: str

class AdminTerminateRequest(BaseModel):
    team_id: int

class AdminAuthorizeRequest(BaseModel):
    team_id: int
    reset_quiz: bool = False
