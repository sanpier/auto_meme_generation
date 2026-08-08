import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pathlib import Path


def get_drive_service():
    creds = None
    token_path = os.getenv("GOOGLE_DRIVE_TOKEN_PATH", "token.json")
    credentials_path = os.getenv("GOOGLE_DRIVE_CREDENTIALS_PATH", "credentials-google-drive.json")
    scopes = ["https://www.googleapis.com/auth/drive.file"]

    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path,
                scopes,
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)

def upload_file_to_drive(file_path: str, folder_id: str | None = None):
    folder_id = folder_id or os.environ["GOOGLE_DRIVE_FOLDER_ID"]
    service = get_drive_service()
    file_path = Path(file_path)
    file_metadata = {
        "name": file_path.name,
        "parents": [folder_id],
    }

    media = MediaFileUpload(str(file_path), resumable=True)
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()

    print(f"Uploaded to Drive: {uploaded['name']} | {uploaded.get('webViewLink')}")
    return uploaded