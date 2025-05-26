import fastapi
import requests
import uvicorn
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api
from urllib.parse import quote
from dotenv import load_dotenv # For local development
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables from .env file FIRST (for local development)
# On Render, these will be overridden/set by Render's environment variables
load_dotenv()
print("INFO: main.py script started.")

# --- Cloudinary Configuration ---
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME") or "ddeazpmcd"
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY") or "193187914314353"
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET") or "g352-BZO2OGstejYakcniC-fbeQ"

print(f"INFO: CLOUDINARY_CLOUD_NAME: {'Set' if CLOUDINARY_CLOUD_NAME else 'Not Set'}")
# Avoid printing actual keys/secrets to logs
print(f"INFO: CLOUDINARY_API_KEY: {'Set' if CLOUDINARY_API_KEY else 'Not Set'}")
print(f"INFO: CLOUDINARY_API_SECRET: {'Set' if CLOUDINARY_API_SECRET else 'Not Set'}")


if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    print("CRITICAL WARNING: Cloudinary credentials are NOT fully set in environment variables.")
    print("Please ensure CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET are set in Render's environment.")
    # The application might still start, but any Cloudinary dependent endpoint will fail.
else:
    try:
        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True
        )
        print("INFO: Cloudinary configured successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to configure Cloudinary: {e}")
        print("CRITICAL ERROR: The application might not function correctly or at all due to Cloudinary configuration failure.")
        # Depending on how critical Cloudinary is, you might want to raise SystemExit here
        # For now, it will attempt to continue, but endpoints will likely fail.

# Create a FastAPI application instance
app = fastapi.FastAPI(
    title="Instagram Media Uploader to Cloudinary",
    description="Fetches media from an Instagram URL, uploads it to Cloudinary, and returns Cloudinary links.",
    version="2.2.2", # Incremented version
)
print("INFO: FastAPI app instance created.")

# --- CORS Middleware Configuration ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("INFO: CORS middleware configured.")
# --- End CORS Middleware Configuration ---

EXTERNAL_API_BASE_URL = "https://api.yabes-desu.workers.dev/download/instagram/v2?url="


def _extract_media_urls_from_yabes_response(yabes_api_response: dict) -> list[str]:
    media_urls = []
    media_data_payload = yabes_api_response.get('data')
    if not media_data_payload or not isinstance(media_data_payload, dict):
        print(f"DEBUG: No 'data' payload or not a dict in yabes response: {yabes_api_response}")
        return []
    urls_from_payload = media_data_payload.get('url')
    if isinstance(urls_from_payload, list):
        for url_item in urls_from_payload:
            if isinstance(url_item, str) and url_item.startswith('http'):
                media_urls.append(url_item)
    elif isinstance(urls_from_payload, str) and urls_from_payload.startswith('http'):
        media_urls.append(urls_from_payload)
    if not media_urls:
        print(f"DEBUG: Could not extract media URLs from payload: {media_data_payload}")
    return media_urls

@app.get("/process_instagram_and_upload/", tags=["Instagram to Cloudinary"])
async def process_instagram_and_upload_to_cloudinary(
    instagram_url: str = fastapi.Query(..., description="The full URL of the Instagram post/reel.")
):
    print(f"INFO: Received request for /process_instagram_and_upload/ with URL: {instagram_url}")
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]): # Re-check for safety
        print("ERROR: Cloudinary not configured at request time.")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudinary service is not configured on the server. Admin check required."
        )

    if not instagram_url:
        raise fastapi.HTTPException(status_code=400, detail="Instagram URL cannot be empty.")

    encoded_instagram_url = quote(instagram_url, safe='')
    external_api_full_url = f"{EXTERNAL_API_BASE_URL}{encoded_instagram_url}"
    print(f"DEBUG: Step 1: Requesting data from external API: {external_api_full_url}")

    yabes_data = {}
    response_text_summary = "N/A" # For logging in case of JSON decode error
    try:
        response = requests.get(external_api_full_url, timeout=45)
        response_text_summary = response.text[:200] if response else "No response object"
        response.raise_for_status()
        yabes_data = response.json()
    except requests.exceptions.Timeout:
        print(f"ERROR: Timeout contacting yabes-desu API: {external_api_full_url}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request to external Instagram API (yabes-desu) timed out."
        )
    except requests.exceptions.HTTPError as http_err:
        print(f"ERROR: HTTP error from yabes-desu API: {http_err}. Response: {response_text_summary}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f"External API (yabes-desu) returned HTTP {response.status_code if response else 'N/A'}: {response_text_summary}"
        )
    except requests.exceptions.RequestException as req_err:
        print(f"ERROR: RequestException with yabes-desu API: {req_err}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f"Error connecting to external Instagram API (yabes-desu): {req_err}"
        )
    except ValueError: # JSONDecodeError is a subclass of ValueError
        print(f"ERROR: Failed to decode JSON from yabes-desu API. Response text: {response_text_summary}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="External Instagram API (yabes-desu) returned invalid JSON."
        )

    if not isinstance(yabes_data, dict) or 'success' not in yabes_data:
        print(f"ERROR: Unexpected response structure from yabes-desu (no 'success' field): {yabes_data}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="Unexpected response structure from external Instagram API (yabes-desu)."
        )
    if yabes_data.get('success') is not True:
        error_message = yabes_data.get('message') or yabes_data.get('error', 'Unknown error, "success" was not true.')
        print(f"ERROR: yabes-desu API reported failure (success: False): {error_message}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Failed to fetch data via external API (yabes-desu): {error_message}"
        )
    if 'data' not in yabes_data or not isinstance(yabes_data.get('data'), dict):
        print(f"ERROR: yabes-desu success=true but 'data' field missing/invalid: {yabes_data}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="External Instagram API (yabes-desu) reported success but provided no valid data."
        )

    print("DEBUG: Step 2: Successfully fetched data from yabes-desu API.")
    source_media_urls = _extract_media_urls_from_yabes_response(yabes_data)

    if not source_media_urls:
        print("INFO: No media URLs found in yabes-desu response for this Instagram URL.")
        yabes_data_field = yabes_data.get("data", {})
        return {
            "message": "No media items found or extractable for the given Instagram URL.",
            "instagram_url": instagram_url,
            "original_yabes_desu_data_summary": {
                "caption": yabes_data_field.get("caption"),
                "username": yabes_data_field.get("username"),
                "is_video": yabes_data_field.get("isVideo")
            },
            "cloudinary_uploads": []
        }

    print(f"DEBUG: Step 3: Found {len(source_media_urls)} media item(s) to upload.")
    cloudinary_uploads = []
    for index, media_url in enumerate(source_media_urls):
        print(f"DEBUG: Uploading media {index+1}/{len(source_media_urls)} from {media_url} to Cloudinary...")
        try:
            upload_result = cloudinary.uploader.upload(
                media_url, resource_type="auto", folder="instagram_imports", timeout=60
            )
            cloudinary_uploads.append({
                "source_url": media_url, "cloudinary_url": upload_result.get("secure_url"),
                "public_id": upload_result.get("public_id"), "resource_type": upload_result.get("resource_type"),
                "format": upload_result.get("format"), "width": upload_result.get("width"),
                "height": upload_result.get("height"),
            })
            print(f"INFO: Successfully uploaded to Cloudinary: {upload_result.get('secure_url')}")
        except cloudinary.exceptions.Error as e:
            print(f"ERROR: Cloudinary upload failed for {media_url}: {e}")
            cloudinary_uploads.append({"source_url": media_url, "error": f"Cloudinary upload failed: {str(e)}"})
        except Exception as e:
            print(f"ERROR: Unexpected error uploading {media_url} to Cloudinary: {e}")
            cloudinary_uploads.append({"source_url": media_url, "error": f"Unexpected error during Cloudinary upload: {str(e)}"})

    print("DEBUG: Step 4: Finished processing all media items.")
    yabes_data_field = yabes_data.get("data", {})
    return {
        "message": "Processing complete.", "instagram_url": instagram_url,
        "original_yabes_desu_data_summary": {
             "caption": yabes_data_field.get("caption"), "username": yabes_data_field.get("username"),
             "likes": yabes_data_field.get("like"), "comments": yabes_data_field.get("comment"),
             "is_video": yabes_data_field.get("isVideo")
        },
        "cloudinary_uploads": cloudinary_uploads
    }

# This block is crucial for how Render starts your app if using `python main.py`
if __name__ == "__main__":
    print("INFO: Executing `if __name__ == \"__main__\":` block.")
    
    # Render provides the PORT environment variable.
    # Default to 8000 for local development if PORT is not set.
    port_str = os.environ.get("PORT")
    if port_str:
        print(f"INFO: PORT environment variable found: '{port_str}'")
        try:
            port = int(port_str)
        except ValueError:
            print(f"ERROR: Invalid PORT environment variable '{port_str}'. Defaulting to 8000.")
            port = 8000
    else:
        print("INFO: PORT environment variable not found. Defaulting to 8000 for local dev.")
        port = 8000

    # host="0.0.0.0" is ESSENTIAL for Render to correctly map requests.
    host = "0.0.0.0"
    
    # Check Cloudinary config again before starting server, as it's critical.
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
         print("\nCRITICAL STARTUP WARNING: Cloudinary is not configured. Uploads WILL FAIL.")
         print("Ensure CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET are set as environment variables in Render.\n")
    else:
        print("INFO: Cloudinary appears to be configured based on environment variables presence.")

    print(f"INFO: Attempting to start Uvicorn server on host '{host}' and port {port}.")
    try:
        uvicorn.run(app, host=host, port=port)
        # If uvicorn.run() exits normally, it means the server was stopped, e.g. by a signal.
        # If it raises an exception (e.g. port already in use locally), it will be caught below.
        print("INFO: Uvicorn server has stopped.")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to start Uvicorn server: {e}")
        # This is a common place to see "OSError: [Errno 98] Address already in use" locally
        # On Render, other issues might prevent binding.
        # Exit with an error code if server fails to start, this might give Render more info.
        import sys
        sys.exit(1)
else:
    print("INFO: Script is imported, not run directly. Uvicorn should be started by an external command (e.g., Render's start command directly calling uvicorn).")

