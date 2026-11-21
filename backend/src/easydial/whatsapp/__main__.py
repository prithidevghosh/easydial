"""Local WhatsApp webhook server. Never expose the separate iOS/session API."""
import uvicorn

if __name__ == "__main__":
    # GET verification carries a secret query parameter: suppress access logging.
    uvicorn.run("easydial.whatsapp.app:app", host="127.0.0.1", port=8001, access_log=False, workers=1)
