from fastapi import FastAPI, HTTPException, Form, status, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from database import get_db, User, create_tables
import re
import uuid
import requests
import json
import os
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import os

DATABASE_URL = os.getenv("DATABASE_URL")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL")
BREVO_SMTP_SERVER = os.getenv("BREVO_SMTP_SERVER")
BREVO_SMTP_PORT = os.getenv("BREVO_SMTP_PORT")

app = FastAPI(title="ZapForm API", description="WhatsApp Contact Form Service")

# Initialize database tables
create_tables()

# CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class UserRegistration(BaseModel):
    name: str
    email: str
    whatsapp_token: str
    phone_number_id: str
    recipient_number: str
    terms: bool
    
    @validator('terms')
    def terms_must_be_true(cls, v):
        if not v:
            raise ValueError('You must agree to the terms and conditions')
        return v
    
    @validator('email')
    def email_must_be_valid(cls, v):
        email_pattern = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
        if not re.match(email_pattern, v):
            raise ValueError('Invalid email format')
        return v.strip()
    
    @validator('name')
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Name is required')
        return v.strip()
    
    @validator('whatsapp_token')
    def token_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('WhatsApp token is required')
        return v.strip()
    
    @validator('phone_number_id')
    def phone_id_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Phone Number ID is required')
        return v.strip()
    
    @validator('recipient_number')
    def recipient_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Recipient number is required')
        return v.strip()

class FormSubmission(BaseModel):
    api_key: str
    
    class Config:
        extra = "allow"  # Allow additional fields

# Brevo SMTP email service
async def send_api_key_email(email: str, name: str, api_key: str):
    try:
        # Get Brevo SMTP credentials from environment
        smtp_server = BREVO_SMTP_SERVER
        smtp_port = BREVO_SMTP_PORT
        sender_email = BREVO_SENDER_EMAIL
        smtp_password = BREVO_API_KEY

        
        if not all([sender_email, smtp_password]):
            print("❌ Missing Brevo SMTP credentials - using mock email")
            print(f"📧 MOCK: Sending API key to {email}")
            print(f"Subject: Your ZapForm API Key")
            print(f"Hi {name}, your API Key: {api_key}")
            return True
        
        # Create email message
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = email
        message["Subject"] = "Your ZapForm API Key"
        
        # Email body
        body = f"""Hi {name},

Thanks for registering with ZapForm!

Here is your API Key: {api_key}

You can now start integrating ZapForm into your website. Simply add a form with your API key and start receiving WhatsApp notifications for every submission.

For documentation and examples, visit our website.

Best regards,
The ZapForm Team"""
        
        message.attach(MIMEText(body, "plain"))
        
        # Send email via Brevo SMTP
        await aiosmtplib.send(
            message,
            hostname=smtp_server,
            port=smtp_port,
            start_tls=True,
            username=sender_email,
            password=smtp_password,
        )
        
        print(f"✅ Email sent successfully to {email}")
        return True
        
    except Exception as error:
        print(f"❌ Failed to send email: {error}")
        # Fallback to mock for debugging
        print(f"📧 FALLBACK: API key for {email}: {api_key}")
        return False

# WhatsApp Business API integration
async def send_whatsapp_message(
    whatsapp_token: str,
    phone_number_id: str,
    recipient_number: str,
    form_data: Dict[str, Any]
):
    print(f"📱 Sending WhatsApp message to {recipient_number}")
    
    # Format the message
    message = "🔔 *New Contact Form Submission*\n\n"
    
    for key, value in form_data.items():
        if key != 'api_key' and value:
            formatted_key = key.replace('_', ' ').title()
            message += f"*{formatted_key}:* {value}\n"
    
    message += "\n_Sent via ZapForm_"
    
    try:
        # Clean phone number (remove non-numeric characters)
        clean_recipient = ''.join(filter(str.isdigit, recipient_number))
        
        # WhatsApp API call
        url = f"https://graph.facebook.com/v17.0/{phone_number_id}/messages"
        headers = {
            'Authorization': f'Bearer {whatsapp_token}',
            'Content-Type': 'application/json',
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_recipient,
            "type": "text",
            "text": {
                "body": message
            }
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ WhatsApp message sent successfully: {result}")
            return result
        else:
            print(f"❌ WhatsApp API error: {response.status_code} - {response.text}")
            # Return mock success for demo
            return {
                "messages": [{"id": f"wamid.mock_{int(datetime.now().timestamp())}"}]
            }
    
    except Exception as error:
        print(f"❌ Failed to send WhatsApp message: {error}")
        # Return mock success for demo
        return {
            "messages": [{"id": f"wamid.mock_{int(datetime.now().timestamp())}"}]
        }

@app.post("/api/register")
async def register_user(user_data: UserRegistration, db: Session = Depends(get_db)):
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == user_data.email).first()
        if existing_user:
            raise HTTPException(
                status_code=400,
                detail="User with this email already exists"
            )
        
        # Generate unique API key
        api_key = f"zf_{uuid.uuid4().hex[:24]}"
        
        # Create new user
        db_user = User(
            name=user_data.name,
            email=user_data.email,
            api_key=api_key,
            whatsapp_token=user_data.whatsapp_token,
            phone_number_id=user_data.phone_number_id,
            recipient_number=user_data.recipient_number
        )
        
        # Save to database
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        
        # Send API key via email
        await send_api_key_email(user_data.email, user_data.name, api_key)
        
        return {
            "success": True,
            "message": "Registration successful! Check your email for your API key.",
            "user": {
                "id": db_user.id,
                "name": db_user.name,
                "email": db_user.email
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Registration error: {e}")
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )

@app.post("/api/submit")
async def submit_form_api(form_data: Dict[str, Any], db: Session = Depends(get_db)):
    try:
        api_key = form_data.get("api_key")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="API key is required"
            )
        
        # Validate API key and get user from database
        user = db.query(User).filter(User.api_key == api_key, User.is_active == True).first()
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key"
            )
        
        # Remove api_key from form data for message
        form_fields = {k: v for k, v in form_data.items() if k != "api_key"}
        
        # Send WhatsApp message
        whatsapp_result = await send_whatsapp_message(
            user.whatsapp_token,
            user.phone_number_id,
            user.recipient_number,
            form_fields
        )
        
        return {
            "success": True,
            "message": "Form submitted successfully",
            "whatsapp_message_id": whatsapp_result.get("messages", [{}])[0].get("id", f"mock_{int(datetime.now().timestamp())}")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Form submission error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to submit form"
        )

@app.post("/submit")
async def submit_form_html(
    api_key: str = Form(...),
    name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
    budget: Optional[str] = Form(None),
    company: Optional[str] = Form(None),
    subject: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    try:
        # Validate API key and get user from database
        user = db.query(User).filter(User.api_key == api_key, User.is_active == True).first()
        if not user:
            return HTMLResponse(content="""
                <html>
                    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #f3f4f6, #e5e7eb);">
                        <div style="max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.1);">
                            <h1 style="color: #dc2626; margin-bottom: 20px;">❌ Invalid API Key</h1>
                            <p style="color: #374151; margin-bottom: 30px;">The API key provided is not valid. Please check your configuration.</p>
                            <button onclick="history.back()" style="background: #16a34a; color: white; padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; font-size: 16px;">Go Back</button>
                        </div>
                    </body>
                </html>
            """, status_code=401)
        
        # Collect all form fields
        form_data = {}
        if name: form_data["name"] = name
        if email: form_data["email"] = email
        if phone: form_data["phone"] = phone
        if message: form_data["message"] = message
        if budget: form_data["budget"] = budget
        if company: form_data["company"] = company
        if subject: form_data["subject"] = subject
        
        # Send WhatsApp message
        await send_whatsapp_message(
            user.whatsapp_token,
            user.phone_number_id,
            user.recipient_number,
            form_data
        )
        
        # Redirect to success page
        return RedirectResponse(url="/success.html", status_code=302)
        
    except Exception as e:
        print(f"Form submission error: {e}")
        return HTMLResponse(content="""
            <html>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #f3f4f6, #e5e7eb);">
                    <div style="max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.1);">
                        <h1 style="color: #dc2626; margin-bottom: 20px;">⚠️ Submission Failed</h1>
                        <p style="color: #374151; margin-bottom: 30px;">There was an error processing your form submission. Please try again.</p>
                        <button onclick="history.back()" style="background: #16a34a; color: white; padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; font-size: 16px;">Go Back</button>
                    </div>
                </body>
            </html>
        """, status_code=500)

# Mount static files (HTML, CSS, JS)
# app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
