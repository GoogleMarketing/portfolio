"""Grant the Firebase custom claim admin=true to the account named in ADMIN_EMAIL.
Run this once from a trusted computer, not from the public website.

Required environment variables:
  FIREBASE_SERVICE_ACCOUNT  full service-account JSON text
  FIREBASE_DATABASE_URL     Realtime Database URL
  ADMIN_EMAIL               email address of the account to promote
"""
import json
import os

import firebase_admin
from firebase_admin import auth, credentials

raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
database_url = os.environ.get("FIREBASE_DATABASE_URL", "").strip()
admin_email = os.environ.get("ADMIN_EMAIL", "").strip()
if not raw or not database_url or not admin_email:
    raise SystemExit("Set FIREBASE_SERVICE_ACCOUNT, FIREBASE_DATABASE_URL, and ADMIN_EMAIL first.")

try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)), {"databaseURL": database_url})

user = auth.get_user_by_email(admin_email)
claims = dict(user.custom_claims or {})
claims["admin"] = True
auth.set_custom_user_claims(user.uid, claims)
print(f"Admin custom claim set for Firebase UID {user.uid}. Sign out and sign in again to refresh the ID token.")
