# main.py

import fastapi
import requests
import uvicorn
import os
import cloudinary
import cloudinary.uploader
import cloudinary.api
from urllib.parse import quote
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware # Import CORSMiddleware

# Load environment variables from .env file (for local development)
load_dotenv()

# --- Cloudinary Configuration ---
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME") or "ddeazpmcd"
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY") or "193187914314353"
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET") or "g352-BZO2OGstejYakcniC-fbeQ"

if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    print("ERROR: Cloudinary credentials are not fully set in environment variables.")
    print("Please set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET.")
else:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
    print("Cloudinary configured successfully.")


# Create a FastAPI application instance
app = fastapi.FastAPI(
    title="Instagram Media Uploader to Cloudinary",
    description="Fetches media from an Instagram URL, uploads it to Cloudinary, and returns Cloudinary links. Accessible by anyone with network access.",
    version="2.2.0", # Incremented version
)

# --- CORS Middleware Configuration ---
# This allows requests from any origin.
# For more restrictive CORS, specify origins in the allow_origins list.
# e.g., allow_origins=["https://yourfrontend.com", "http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True, # Allows cookies to be included in requests
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, etc.)
    allow_headers=["*"],  # Allows all headers
)
# --- End CORS Middleware Configuration ---


# The base URL of the external Instagram API
EXTERNAL_API_BASE_URL = "https://api.yabes-desu.workers.dev/download/instagram/v2?url="


def _extract_media_urls_from_yabes_response(yabes_api_response: dict) -> list[str]:
    """
    Extracts individual media URLs from the parsed response of the yabes-desu API.
    """
    media_urls = []
    media_data_payload = yabes_api_response.get('data')

    if not media_data_payload or not isinstance(media_data_payload, dict):
        print(f"No 'data' payload found in yabes-desu response or it's not a dictionary. Response: {yabes_api_response}")
        return []

    urls_from_payload = media_data_payload.get('url')

    if isinstance(urls_from_payload, list):
        for url_item in urls_from_payload:
            if isinstance(url_item, str) and url_item.startswith('http'):
                media_urls.append(url_item)
    elif isinstance(urls_from_payload, str) and urls_from_payload.startswith('http'):
        media_urls.append(urls_from_payload)
    
    if not media_urls:
        print(f"Could not extract any media URLs from yabes-desu data payload: {media_data_payload}")

    return media_urls


@app.get("/process_instagram_and_upload/", tags=["Instagram to Cloudinary"])
async def process_instagram_and_upload_to_cloudinary(
    instagram_url: str = fastapi.Query(..., description="The full URL of the Instagram post/reel.")
):
    """
    Fetches media from an Instagram URL, uploads it to Cloudinary,
    and returns information about the uploaded Cloudinary assets.
    """
    if not CLOUDINARY_CLOUD_NAME:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudinary service is not configured on the server."
        )

    if not instagram_url:
        raise fastapi.HTTPException(status_code=400, detail="Instagram URL cannot be empty.")

    encoded_instagram_url = quote(instagram_url, safe='')
    external_api_full_url = f"{EXTERNAL_API_BASE_URL}{encoded_instagram_url}"

    print(f"Step 1: Requesting data from external Instagram API: {external_api_full_url}")

    yabes_data = {}
    try:
        response = requests.get(external_api_full_url, timeout=30)
        if response.status_code != 200:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
                detail=f"External API (yabes-desu) returned HTTP {response.status_code}: {response.text[:200]}"
            )
        yabes_data = response.json()

    except requests.exceptions.Timeout:
        print(f"Timeout error occurred while contacting yabes-desu API.")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The request to the external Instagram API (yabes-desu) timed out."
        )
    except requests.exceptions.RequestException as req_err:
        print(f"An unexpected error occurred with the external request to yabes-desu: {req_err}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail=f"Error connecting to external Instagram API (yabes-desu): {req_err}"
        )
    except ValueError: 
        print(f"Failed to decode JSON from yabes-desu API. Response text: {response.text[:200]}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="External Instagram API (yabes-desu) returned invalid JSON."
        )

    if not isinstance(yabes_data, dict) or 'success' not in yabes_data:
        print(f"Unexpected response structure from yabes-desu (missing 'success' field): {yabes_data}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="Received an unexpected response structure from the external Instagram API (yabes-desu)."
        )

    if yabes_data.get('success') is not True:
        error_message = yabes_data.get('message') or yabes_data.get('error', 'Unknown error from yabes-desu API, "success" flag was not true.')
        print(f"Error from yabes-desu API (success: {yabes_data.get('success')}): {error_message}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Failed to fetch data from Instagram via external API (yabes-desu): {error_message}"
        )

    if 'data' not in yabes_data or not isinstance(yabes_data.get('data'), dict):
        print(f"Unexpected response structure from yabes-desu ('data' field missing or not a dict when success is true): {yabes_data}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="External Instagram API (yabes-desu) reported success but provided no valid data."
        )

    print("Step 2: Successfully fetched data from yabes-desu API (JSON indicates success).")
    
    source_media_urls = _extract_media_urls_from_yabes_response(yabes_data)

    if not source_media_urls:
        print("No media URLs found in the response from yabes-desu.")
        yabes_data_field = yabes_data.get("data", {})
        return {
            "message": "No media items found or extractable for the given Instagram URL via the external API.",
            "instagram_url": instagram_url,
            "original_yabes_desu_data_summary": {
                "caption": yabes_data_field.get("caption"),
                "username": yabes_data_field.get("username"),
                "is_video": yabes_data_field.get("isVideo")
            },
            "cloudinary_uploads": []
        }

    print(f"Step 3: Found {len(source_media_urls)} media item(s) to upload to Cloudinary.")

    cloudinary_uploads = []
    for index, media_url in enumerate(source_media_urls):
        print(f"Uploading media {index+1}/{len(source_media_urls)} from {media_url} to Cloudinary...")
        try:
            upload_result = cloudinary.uploader.upload(
                media_url,
                resource_type="auto",
                folder="instagram_imports"
            )
            cloudinary_uploads.append({
                "source_url": media_url,
                "cloudinary_url": upload_result.get("secure_url"),
                "public_id": upload_result.get("public_id"),
                "resource_type": upload_result.get("resource_type"),
                "format": upload_result.get("format"),
                "width": upload_result.get("width"),
                "height": upload_result.get("height"),
            })
            print(f"Successfully uploaded to Cloudinary: {upload_result.get('secure_url')}")
        except cloudinary.exceptions.Error as e:
            print(f"Error uploading {media_url} to Cloudinary: {e}")
            cloudinary_uploads.append({
                "source_url": media_url,
                "error": f"Cloudinary upload failed: {str(e)}"
            })
        except Exception as e:
            print(f"Unexpected error uploading {media_url} to Cloudinary: {e}")
            cloudinary_uploads.append({
                "source_url": media_url,
                "error": f"An unexpected error occurred during Cloudinary upload: {str(e)}"
            })

    print("Step 4: Finished processing all media items.")
    
    yabes_data_field = yabes_data.get("data", {})
    return {
        "message": "Processing complete.",
        "instagram_url": instagram_url,
        "original_yabes_desu_data_summary": {
             "caption": yabes_data_field.get("caption"),
             "username": yabes_data_field.get("username"),
             "likes": yabes_data_field.get("like"),
             "comments": yabes_data_field.get("comment"),
             "is_video": yabes_data_field.get("isVideo")
        },
        "cloudinary_uploads": cloudinary_uploads
    }

if __name__ == "__main__":
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
         print("\nWARNING: Cloudinary is not configured. Uploads will fail. Please check your .env file or environment variables.\n")
    
    # host="0.0.0.0" makes the server accessible from your network, not just localhost.
    uvicorn.run(app, host="0.0.0.0", port=8000)
