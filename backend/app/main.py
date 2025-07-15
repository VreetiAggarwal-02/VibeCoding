import os
import uuid
import shutil
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import requests
import logging
from PyPDF2 import PdfReader
import pytesseract
from pdf2image import convert_from_path
import re
import json

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# Allow CORS for frontend dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")
HEADERS = {
    "apikey": SUPABASE_API_KEY or "",
    "Authorization": f"Bearer {SUPABASE_API_KEY}" if SUPABASE_API_KEY else "",
    "Content-Type": "application/json"
}
USER_FINANCIALS_ENDPOINT = f"{SUPABASE_URL}/rest/v1/UserFinancials" if SUPABASE_URL else None
TEMP_PDF_DIR = "temp_pdfs"
os.makedirs(TEMP_PDF_DIR, exist_ok=True)

FIELDS = [
    'gross_salary', 'basic_salary', 'hra_received', 'rent_paid',
    'deduction_80c', 'deduction_80d', 'standard_deduction',
    'professional_tax', 'tds'
]

# Utility: Extract text from PDF (text and scanned)
def extract_pdf_data(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            # If no text, try OCR
            images = convert_from_path(pdf_path)
            text = "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception as e:
        text = ""

    # Extraction logic for each field
    def extract_field(patterns, text):
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                # Remove commas and currency symbols, keep only numbers and dot
                value = match.group(1).replace(',', '').replace('₹', '').strip()
                try:
                    return float(value)
                except ValueError:
                    return value
        return ""

    data = {
        'gross_salary': extract_field([r'Gross\s*Salary\s*[:\-]?\s*([\d,\.]+)'], text),
        'basic_salary': extract_field([r'Basic\s*Salary\s*[:\-]?\s*([\d,\.]+)', r'Basic\s*[:\-]?\s*([\d,\.]+)'], text),
        'hra_received': extract_field([r'HRA\s*Received\s*[:\-]?\s*([\d,\.]+)', r'House\s*Rent\s*Allowance\s*[:\-]?\s*([\d,\.]+)'], text),
        'rent_paid': extract_field([r'Rent\s*Paid\s*[:\-]?\s*([\d,\.]+)'], text),
        'deduction_80c': extract_field([r'80C\s*Deduction\s*[:\-]?\s*([\d,\.]+)', r'Deduction\s*under\s*80C\s*[:\-]?\s*([\d,\.]+)'], text),
        'deduction_80d': extract_field([r'80D\s*Deduction\s*[:\-]?\s*([\d,\.]+)', r'Deduction\s*under\s*80D\s*[:\-]?\s*([\d,\.]+)'], text),
        'standard_deduction': extract_field([r'Standard\s*Deduction\s*[:\-]?\s*([\d,\.]+)'], text),
        'professional_tax': extract_field([r'Professional\s*Tax\s*[:\-]?\s*([\d,\.]+)'], text),
        'tds': extract_field([r'TDS\s*[:\-]?\s*([\d,\.]+)', r'Tax\s*Deducted\s*at\s*Source\s*[:\-]?\s*([\d,\.]+)'], text),
    }
    return data

def calculate_old_regime(data):
    # Extract values, default to 0 if None
    gross_salary = data.get('gross_salary') or 0
    basic_salary = data.get('basic_salary') or 0
    hra_received = data.get('hra_received') or 0
    rent_paid = data.get('rent_paid') or 0
    deduction_80c = min(data.get('deduction_80c') or 0, 150000)
    deduction_80d = data.get('deduction_80d') or 0
    standard_deduction = data.get('standard_deduction') or 50000
    professional_tax = data.get('professional_tax') or 0
    tds = data.get('tds') or 0

    # Taxable income
    taxable_income = gross_salary
    taxable_income -= standard_deduction
    taxable_income -= hra_received
    taxable_income -= deduction_80c
    taxable_income -= deduction_80d
    taxable_income -= professional_tax
    taxable_income = max(0, taxable_income)

    # Old regime slabs
    tax = 0
    slabs = [
        (250000, 0.0),
        (500000, 0.05),
        (1000000, 0.20),
        (float('inf'), 0.30)
    ]
    prev_limit = 0
    for limit, rate in slabs:
        if taxable_income > limit:
            tax += (limit - prev_limit) * rate
            prev_limit = limit
        else:
            tax += (taxable_income - prev_limit) * rate
            break
    # Cess
    tax_with_cess = tax * 1.04
    net_tax_payable = tax_with_cess - tds
    return {
        'regime': 'old',
        'taxable_income': round(taxable_income, 2),
        'total_tax': round(tax_with_cess, 2),
        'deductions': round(standard_deduction + hra_received + deduction_80c + deduction_80d + professional_tax, 2),
        'net_tax_payable': round(net_tax_payable, 2)
    }

def calculate_new_regime(data):
    gross_salary = data.get('gross_salary') or 0
    standard_deduction = data.get('standard_deduction') or 50000
    tds = data.get('tds') or 0
    # Only standard deduction allowed
    taxable_income = gross_salary - standard_deduction
    taxable_income = max(0, taxable_income)
    # New regime slabs
    tax = 0
    slabs = [
        (300000, 0.0),
        (600000, 0.05),
        (900000, 0.10),
        (1200000, 0.15),
        (1500000, 0.20),
        (float('inf'), 0.30)
    ]
    prev_limit = 0
    for limit, rate in slabs:
        if taxable_income > limit:
            tax += (limit - prev_limit) * rate
            prev_limit = limit
        else:
            tax += (taxable_income - prev_limit) * rate
            break
    tax_with_cess = tax * 1.04
    net_tax_payable = tax_with_cess - tds
    return {
        'regime': 'new',
        'taxable_income': round(taxable_income, 2),
        'total_tax': round(tax_with_cess, 2),
        'deductions': round(standard_deduction, 2),
        'net_tax_payable': round(net_tax_payable, 2)
    }

def coerce_numeric_fields(data):
    numeric_fields = [
        'gross_salary', 'basic_salary', 'hra_received', 'rent_paid',
        'deduction_80c', 'deduction_80d', 'standard_deduction',
        'professional_tax', 'tds'
    ]
    for field in numeric_fields:
        value = data.get(field)
        if value is not None and value != "":
            try:
                data[field] = float(value)
            except Exception:
                data[field] = 0
        else:
            data[field] = 0
    return data

def call_gemini_llm(messages, user_data=None):
    """
    Placeholder for Gemini LLM API call. Replace with actual Gemini integration.
    messages: list of dicts with 'role' and 'content'.
    user_data: dict with user's financial data and regime.
    """
    # For demo, return a canned response based on context
    if len(messages) == 1:
        # First message: Gemini asks a follow-up question
        return "Would you like to know how to maximize your 80C deductions or explore other tax-saving options?"
    else:
        # User replied, Gemini gives suggestions
        return "Based on your data, you can invest ₹50,000 more in PPF to maximize your 80C limit. Consider NPS for additional tax benefits."

@app.on_event("startup")
def startup_check():
    try:
        # WARNING: verify=False disables SSL certificate verification and is insecure. Use only for debugging!
        resp = requests.get(USER_FINANCIALS_ENDPOINT, headers=HEADERS, params={"select": "session_id", "limit": 1}, verify=False)
        if resp.status_code == 200:
            logging.info("Supabase REST API reachable and UserFinancials table exists.")
        else:
            logging.error(f"Supabase REST API error: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"Error connecting to Supabase REST API: {e}")

@app.post("/api/upload-pdf")
async def upload_pdf(pdf: UploadFile = File(...)):
    session_id = str(uuid.uuid4())
    pdf_path = os.path.join(TEMP_PDF_DIR, f"{session_id}.pdf")
    logging.info(f"[UPLOAD-PDF] Start: session_id={session_id}")
    try:
        with open(pdf_path, "wb") as f:
            shutil.copyfileobj(pdf.file, f)
        extracted_data = extract_pdf_data(pdf_path)
        logging.info(f"[UPLOAD-PDF] Extracted data: {extracted_data}")
        # Convert empty strings to None for numeric fields
        for key in extracted_data:
            if extracted_data[key] == "":
                extracted_data[key] = None
        # Save to Supabase
        payload = {"session_id": session_id, **extracted_data}
        resp = requests.post(USER_FINANCIALS_ENDPOINT, headers=HEADERS, json=payload, verify=False)
        logging.info(f"[UPLOAD-PDF] Supabase POST status: {resp.status_code}, response: {resp.text}")
        if resp.status_code not in (200, 201):
            logging.error(f"[UPLOAD-PDF] Supabase error: {resp.text}")
            raise HTTPException(status_code=500, detail=f"Supabase error: {resp.text}")
        logging.info(f"[UPLOAD-PDF] Success: session_id={session_id}")
        return {"session_id": session_id, "extracted_data": extracted_data}
    except Exception as e:
        logging.error(f"[UPLOAD-PDF] Exception: {e}")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/session/{session_id}")
def get_session(session_id: str):
    logging.info(f"[GET-SESSION] Fetching session_id={session_id}")
    params = {"session_id": f"eq.{session_id}"}
    resp = requests.get(USER_FINANCIALS_ENDPOINT, headers=HEADERS, params=params, verify=False)
    logging.info(f"[GET-SESSION] Supabase GET status: {resp.status_code}, response: {resp.text}")
    if resp.status_code == 200:
        data = resp.json()
        if data:
            logging.info(f"[GET-SESSION] Found data for session_id={session_id}")
            return data[0]
        else:
            logging.warning(f"[GET-SESSION] Session not found: {session_id}")
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        logging.error(f"[GET-SESSION] Supabase error: {resp.text}")
        raise HTTPException(status_code=500, detail=f"Supabase error: {resp.text}")

@app.post("/api/session/{session_id}/review")
def review_session(session_id: str, reviewed_data: dict):
    logging.info(f"[REVIEW-SESSION] session_id={session_id}, reviewed_data={reviewed_data}")
    params = {"session_id": f"eq.{session_id}"}
    resp = requests.patch(USER_FINANCIALS_ENDPOINT, headers=HEADERS, params=params, json=reviewed_data, verify=False)
    logging.info(f"[REVIEW-SESSION] Supabase PATCH status: {resp.status_code}, response: {resp.text}")
    if resp.status_code in (200, 204):
        pdf_path = os.path.join(TEMP_PDF_DIR, f"{session_id}.pdf")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            logging.info(f"[REVIEW-SESSION] Deleted temp PDF for session_id={session_id}")
        logging.info(f"[REVIEW-SESSION] Success for session_id={session_id}")
        return {"status": "success"}
    else:
        logging.error(f"[REVIEW-SESSION] Supabase error: {resp.text}")
        raise HTTPException(status_code=500, detail=f"Supabase error: {resp.text}")

@app.post("/api/calculate-tax")
def calculate_tax(payload: dict = Body(...)):
    session_id = payload.get('session_id')
    data = payload.get('data')
    if not session_id or not data:
        raise HTTPException(status_code=400, detail="Missing session_id or data")
    logging.info(f"[CALCULATE-TAX] session_id={session_id}, data={data}")
    try:
        data = coerce_numeric_fields(data)  # Ensure all numeric fields are float
        old_result = calculate_old_regime(data)
        new_result = calculate_new_regime(data)
        results = {
            'session_id': session_id,
            'old_regime': old_result,
            'new_regime': new_result
        }
        # Save results to Supabase (patch the row)
        params = {"session_id": f"eq.{session_id}"}
        resp = requests.patch(USER_FINANCIALS_ENDPOINT, headers=HEADERS, params=params, json={"tax_results": results}, verify=False)
        logging.info(f"[CALCULATE-TAX] Supabase PATCH status: {resp.status_code}, response: {resp.text}")
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=500, detail=f"Supabase error: {resp.text}")
        return results
    except Exception as e:
        logging.error(f"[CALCULATE-TAX] Exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat_with_gemini(payload: dict = Body(...)):
    session_id = payload.get('session_id')
    user_message = payload.get('user_message')
    chat_history = payload.get('chat_history', [])
    user_data = payload.get('user_data')  # Optionally pass reviewed data
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    # Build messages for Gemini
    messages = chat_history.copy() if chat_history else []
    if not messages:
        # Start conversation: Gemini asks a follow-up question
        messages = [{"role": "system", "content": "You are a helpful Indian tax advisor. Use the user's financial data to ask a relevant follow-up question after tax calculation."}]
    if user_message:
        messages.append({"role": "user", "content": user_message})
    # Call Gemini (placeholder)
    gemini_response = call_gemini_llm(messages, user_data)
    messages.append({"role": "assistant", "content": gemini_response})
    # Store chat history in Supabase
    params = {"session_id": f"eq.{session_id}"}
    resp = requests.patch(USER_FINANCIALS_ENDPOINT, headers=HEADERS, params=params, json={"chat_history": messages}, verify=False)
    logging.info(f"[CHAT] Supabase PATCH status: {resp.status_code}, response: {resp.text}")
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=500, detail=f"Supabase error: {resp.text}")
    return {"gemini_message": gemini_response, "chat_history": messages}

@app.get("/api/health")
def health_check():
    logging.info("[HEALTH] Health check endpoint called.")
    return {"status": "ok"} 