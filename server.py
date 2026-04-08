#!/usr/bin/env python3
"""
LiveKit Meet Server - Python Implementation
Core meeting functionality without recording
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from pathlib import Path

# Configure logging to both file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('livekit_server.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Main logger
logger = logging.getLogger('server')

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# LiveKit SDK
from livekit import api
from livekit.api import (
    AccessToken, VideoGrants, LiveKitAPI,
    TokenVerifier, WebhookReceiver
)
from livekit.protocol import room as proto_room

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# AWS S3 Configuration
AWS_S3_BUCKET = os.getenv('AWS_S3_BUCKET')
AWS_S3_REGION = os.getenv('AWS_S3_REGION', 'us-east-1')
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

# Groq API Configuration
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

# Local recordings directory (fallback)
RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)

# Create a separate webhook logger (don't add to basicConfig)
webhook_logger = logging.getLogger('webhook')
webhook_logger.setLevel(logging.INFO)
webhook_logger.propagate = False  # Prevent double logging
webhook_handler = logging.FileHandler('webhook_events.log', encoding='utf-8')
webhook_handler.setFormatter(logging.Formatter('%(asctime)s - WEBHOOK - %(message)s'))
webhook_logger.addHandler(webhook_handler)

# ── ENVIRONMENT VARIABLES ──────────────────────────
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY', 'devkey')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET', 'secret')
LIVEKIT_URL = os.getenv('LIVEKIT_URL', 'ws://localhost:7880')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', LIVEKIT_API_SECRET)


app = FastAPI(title="LiveKit Meet Server")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LiveKit API client (created lazily)
livekit_api = None

# Groq client (created lazily)
groq_client = None

async def get_livekit_api():
    """Get or create LiveKit API client"""
    global livekit_api
    if livekit_api is None:
        livekit_api = LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET
        )
    return livekit_api

def get_groq_client():
    """Get or create Groq client"""
    global groq_client
    if groq_client is None and GROQ_API_KEY:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    return groq_client

# Webhook receiver
webhook_receiver = WebhookReceiver(
    TokenVerifier(LIVEKIT_API_KEY, WEBHOOK_SECRET)
)

# ── DATA MODELS ────────────────────────────────────
class TokenRequest(BaseModel):
    roomName: str
    participantName: str
    isCreating: bool = False  # True if creating new room, False if joining existing

class RecordingStartRequest(BaseModel):
    roomName: str
    startedBy: str

class RecordingStopRequest(BaseModel):
    roomName: str
    requestedBy: str

# ── IN-MEMORY STATE ────────────────────────────────
# roomCreators: Map<roomName, creatorIdentity>
room_creators: Dict[str, str] = {}

# Recording state: Map<roomName, {egressId, audioEgressId, startedBy, startTime, videoPath, audioPath}>
active_recordings: Dict[str, Dict] = {}

# Meeting summaries: Map<roomName, {transcript, summary, timestamp}>
meeting_summaries: Dict[str, Dict] = {}

# ── API ENDPOINTS ──────────────────────────────────
@app.post('/api/token')
async def get_token(request: TokenRequest):
    """Generate access token for LiveKit room with Meril- prefix validation"""
    room_name = request.roomName
    participant_name = request.participantName
    is_creating = request.isCreating
    print(f"This is informatin of room name{room_name} , {participant_name} , {is_creating}")
    if not room_name or not participant_name:
        raise HTTPException(status_code=400, detail="roomName and participantName are required")
    
    # Validate room code format - must start with "Meril-"
    if not room_name.startswith("Meril-"):
        logger.warning(f" Invalid room code format: \"{room_name}\" (must start with 'Meril-')")
        raise HTTPException(
            status_code=400, 
            detail={
                "error": "invalid_room_code",
                "message": "Code is not valid",
                "room_code": room_name,
                "required_format": "Room code must start with 'Meril-'"
            }
        )
    
    # SIMPLIFIED VERSION - Skip room validation for testing
    logger.info(f" Creating/joining room \"{room_name}\" (validation skipped for testing)")
    
    # Determine if user is the room creator
    is_creator = room_name not in room_creators
    if is_creator:
        room_creators[room_name] = participant_name
        logger.info(f" Room creator set: \"{participant_name}\" for room=\"{room_name}\"")
    
    try:
        token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        token.with_identity(participant_name)
        token.with_ttl(timedelta(hours=6))
        
        grants = VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        )
        token.with_grants(grants)
        
        jwt_token = token.to_jwt()
        
        return {
            'token': jwt_token,
            'url': LIVEKIT_URL,
            'isCreator': is_creator,
            'creator': room_creators.get(room_name),
            'room_exists': False,  # Always false for testing
            'participant_count': 0
        }

    except Exception as err:
        logger.error(f'Token generation error: {err}')
        raise HTTPException(status_code=500, detail="Failed to generate token")

class RoomValidationRequest(BaseModel):
    room_code: str

@app.post('/api/validate-room')
async def validate_room(request: RoomValidationRequest):
    """Validate if a room exists and is active"""
    room_code = request.room_code.strip()
    
    if not room_code:
        raise HTTPException(status_code=400, detail="room_code is required")
    
    try:
        # Get LiveKit API client
        api_client = await get_livekit_api()
        
        # List all active rooms
        rooms_response = await api_client.room.list_rooms(
            proto_room.ListRoomsRequest()
        )
        
        # Check if the room exists in active rooms
        active_room_names = [room.name for room in rooms_response.rooms]
        room_exists = room_code in active_room_names
        
        if room_exists:
            # Get room details
            room_info = next((room for room in rooms_response.rooms if room.name == room_code), None)
            participant_count = room_info.num_participants if room_info else 0
            
            logger.info(f" Room validation successful: \"{room_code}\" exists with {participant_count} participant(s)")
            
            return {
                "status": "exists",
                "room_code": room_code,
                "participant_count": participant_count,
                "message": f"Room '{room_code}' is active"
            }
        else:
            logger.info(f" Room validation failed: \"{room_code}\" does not exist")
            raise HTTPException(
                status_code=404, 
                detail={
                    "status": "not_found",
                    "room_code": room_code,
                    "message": f"Room '{room_code}' not found"
                }
            )
            
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f'Room validation error: {err}')
        raise HTTPException(status_code=500, detail="Failed to validate room")

@app.get('/api/config')
async def get_config():
    """Get server configuration"""
    return {'livekitUrl': LIVEKIT_URL}

# ── RECORDING ENDPOINTS ────────────────────────────
@app.post('/api/recording/start')
async def start_recording(request: RecordingStartRequest):
    """Start recording a room using LiveKit Egress with S3 storage"""
    room_name = request.roomName
    started_by = request.startedBy
    
    if not room_name or not started_by:
        raise HTTPException(status_code=400, detail="roomName and startedBy are required")
    
    # Check if AWS S3 is configured
    if not AWS_S3_BUCKET or not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logger.error("AWS S3 credentials not configured")
        raise HTTPException(
            status_code=500, 
            detail="Recording storage not configured. Please set AWS S3 credentials in .env file."
        )
    
    # Check if recording already active for this room
    if room_name in active_recordings:
        existing = active_recordings[room_name]
        logger.warning(f"Recording already active for room \"{room_name}\" (started by {existing['startedBy']})")
        return JSONResponse(
            status_code=409,
            content={
                "error": "recording_already_active",
                "startedBy": existing['startedBy'],
                "startTime": existing['startTime'].isoformat()
            }
        )
    
    # Check if user is the room creator
    if room_name in room_creators and room_creators[room_name] != started_by:
        logger.warning(f"Non-creator \"{started_by}\" attempted to start recording in room \"{room_name}\"")
        raise HTTPException(
            status_code=403,
            detail="Only the meeting organizer can start recording"
        )
    
    try:
        api_client = await get_livekit_api()
        
        # Import egress types
        from livekit.protocol import egress as proto_egress
        
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        
        # S3 path for audio only (NO VIDEO)
        audio_s3_key = f"recordings/{room_name}/{timestamp}_audio.mp3"
        
        # Configure S3 upload for audio
        audio_s3_output = proto_egress.S3Upload(
            access_key=AWS_ACCESS_KEY_ID,
            secret=AWS_SECRET_ACCESS_KEY,
            region=AWS_S3_REGION,
            bucket=AWS_S3_BUCKET
        )
        
        # Create audio-only egress for transcription (NO VIDEO)
        audio_egress_request = proto_egress.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            video_only=False,
            file_outputs=[
                proto_egress.EncodedFileOutput(
                    file_type=proto_egress.EncodedFileType.MP3,
                    filepath=audio_s3_key,
                    s3=audio_s3_output
                )
            ]
        )
        
        # Start audio-only egress
        audio_egress_info = await api_client.egress.start_room_composite_egress(audio_egress_request)
        
        # Store recording state (audio only)
        active_recordings[room_name] = {
            'egressId': audio_egress_info.egress_id,
            'startedBy': started_by,
            'startTime': datetime.now(),
            'audioS3Key': audio_s3_key
        }
        
        logger.info(f"🔴 Audio recording started for room \"{room_name}\" by \"{started_by}\" (egress: {audio_egress_info.egress_id})")
        logger.info(f"📦 S3 Bucket: {AWS_S3_BUCKET}, Audio: {audio_s3_key}")
        
        return {
            'success': True,
            'egressId': audio_egress_info.egress_id,
            'roomName': room_name,
            'startedBy': started_by,
            'started': [f"Audio recording to S3 bucket: {AWS_S3_BUCKET}"],
            's3Bucket': AWS_S3_BUCKET,
            'audioPath': audio_s3_key
        }
        
    except Exception as err:
        logger.error(f"Failed to start recording for room \"{room_name}\": {err}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {str(err)}")

@app.post('/api/recording/stop')
async def stop_recording(request: RecordingStopRequest):
    """Stop an active recording"""
    room_name = request.roomName
    requested_by = request.requestedBy
    
    if not room_name or not requested_by:
        raise HTTPException(status_code=400, detail="roomName and requestedBy are required")
    
    # Check if recording is active
    if room_name not in active_recordings:
        logger.warning(f"No active recording found for room \"{room_name}\"")
        raise HTTPException(status_code=404, detail="No active recording found for this room")
    
    recording_info = active_recordings[room_name]
    
    # Check if requester is the one who started the recording
    if recording_info['startedBy'] != requested_by:
        logger.warning(f"User \"{requested_by}\" attempted to stop recording started by \"{recording_info['startedBy']}\"")
        return JSONResponse(
            status_code=403,
            content={
                "error": "permission_denied",
                "startedBy": recording_info['startedBy'],
                "message": "Only the person who started the recording can stop it"
            }
        )
    
    try:
        api_client = await get_livekit_api()
        audio_egress_id = recording_info['egressId']
        audio_s3_key = recording_info.get('audioS3Key')
        
        # Import egress types for stop request
        from livekit.protocol import egress as proto_egress
        
        # Stop audio egress session
        stop_request_audio = proto_egress.StopEgressRequest(egress_id=audio_egress_id)
        await api_client.egress.stop_egress(stop_request_audio)
        
        duration = (datetime.now() - recording_info['startTime']).total_seconds()
        logger.info(f"⏹️ Audio recording stopped for room \"{room_name}\" (duration: {duration:.1f}s)")
        
        # Store recording info for transcription
        recording_data = {
            'roomName': room_name,
            'audioS3Key': audio_s3_key,
            'duration': duration,
            'startedBy': recording_info['startedBy']
        }
        
        # Remove from active recordings
        del active_recordings[room_name]
        
        # Start transcription and summary generation in background
        if audio_s3_key and GROQ_API_KEY:
            asyncio.create_task(process_recording_async(recording_data))
            logger.info(f"🎙️ Starting AI transcription for room \"{room_name}\"")
        
        return {
            'success': True,
            'egressId': audio_egress_id,
            'roomName': room_name,
            'duration': duration,
            'message': 'Recording stopped. Generating transcript and summary...' if audio_s3_key else 'Recording stopped successfully',
            'files': {
                'audio': f"s3://{AWS_S3_BUCKET}/{audio_s3_key}" if audio_s3_key else None,
                'bucket': AWS_S3_BUCKET,
                'region': AWS_S3_REGION
            }
        }
        
    except Exception as err:
        logger.error(f"Failed to stop recording for room \"{room_name}\": {err}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to stop recording: {str(err)}")

@app.get('/api/recording/room/{room_name}')
async def get_recording_status(room_name: str):
    """Get recording status for a room"""
    if room_name in active_recordings:
        recording_info = active_recordings[room_name]
        return {
            'active': True,
            'startedBy': recording_info['startedBy'],
            'startTime': recording_info['startTime'].isoformat(),
            'egressId': recording_info['egressId']
        }
    else:
        return {
            'active': False
        }

# ── TRANSCRIPTION & SUMMARY FUNCTIONS ──────────────
async def process_recording_async(recording_data: Dict):
    """Background task to transcribe and summarize recording"""
    room_name = recording_data['roomName']
    audio_s3_key = recording_data.get('audioS3Key')

    if not audio_s3_key:
        logger.error(f"No audio S3 key provided for room \"{room_name}\"")
        return

    try:
        logger.info(f"🎙️ Starting transcription for room \"{room_name}\"")

        # Wait a bit for S3 upload to complete
        await asyncio.sleep(10)

        # Download audio from S3
        import boto3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_S3_REGION
        )

        # Download audio file temporarily
        local_audio_path = f"recordings/{room_name}_audio.mp3"
        os.makedirs("recordings", exist_ok=True)

        try:
            s3_client.download_file(AWS_S3_BUCKET, audio_s3_key, local_audio_path)
            logger.info(f"📥 Downloaded audio from S3: {audio_s3_key}")
        except Exception as e:
            logger.error(f"Failed to download audio from S3: {e}")
            return

        # Check if audio file exists locally now
        if not os.path.exists(local_audio_path):
            logger.error(f"Audio file not found after download: {local_audio_path}")
            return

        logger.info(f"📥 Audio file ready: {local_audio_path}")

        # Transcribe using Groq Whisper
        transcript = await transcribe_audio(local_audio_path)

        if not transcript:
            logger.error(f"Transcription failed for room \"{room_name}\"")
            return

        logger.info(f"✅ Transcription complete for room \"{room_name}\" ({len(transcript)} chars)")

        # Generate summary using Groq Llama
        summary = await generate_summary(transcript, recording_data)

        if not summary:
            logger.warning(f"Summary generation failed for room {room_name}, using fallback")
            # Create fallback summary structure
            summary = {
                'summary': 'Summary generation failed. Please review the transcript below.',
                'key_topics': [],
                'important_points': [],
                'action_items': []
            }

        logger.info(f"Summary processed for room {room_name}")

        # Store summary
        meeting_summaries[room_name] = {
            'transcript': transcript,
            'summary': summary,
            'timestamp': datetime.now().isoformat(),
            'duration': recording_data['duration'],
            'startedBy': recording_data['startedBy']
        }

        # Create comprehensive transcript file with summary
        transcript_dir = Path("transcripts")
        transcript_dir.mkdir(exist_ok=True)

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        transcript_filename = f"{room_name}_{timestamp_str}_transcript.txt"
        transcript_path = transcript_dir / transcript_filename

        # Format complete transcript with summary
        transcript_content = f"""MEETING TRANSCRIPT & SUMMARY
{'='*60}

Room: {room_name}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Duration: {int(recording_data['duration'] / 60)} minutes {int(recording_data['duration'] % 60)} seconds
Started by: {recording_data['startedBy']}

{'='*60}
OVERVIEW
{'='*60}

{summary.get('summary', 'No summary available')}

{'='*60}
KEY TOPICS
{'='*60}

"""

        for i, topic in enumerate(summary.get('key_topics', []), 1):
            transcript_content += f"{i}. {topic}\n"

        transcript_content += f"""
{'='*60}
IMPORTANT POINTS
{'='*60}

"""

        for i, point in enumerate(summary.get('important_points', []), 1):
            transcript_content += f"{i}. {point}\n"

        transcript_content += f"""
{'='*60}
ACTION ITEMS
{'='*60}

"""

        action_items = summary.get('action_items', [])
        if action_items:
            for i, item in enumerate(action_items, 1):
                transcript_content += f"[ ] {i}. {item}\n"
        else:
            transcript_content += "No action items identified.\n"

        transcript_content += f"""
{'='*60}
FULL TRANSCRIPT
{'='*60}

{transcript}

{'='*60}
End of Meeting Transcript
{'='*60}
"""

        # Save transcript locally
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write(transcript_content)

        logger.info(f"💾 Transcript saved locally: {transcript_path}")

        # Upload transcript to S3
        try:
            s3_transcript_key = f"recordings/{room_name}/{timestamp_str}_transcript.txt"

            s3_client.upload_file(
                str(transcript_path),
                AWS_S3_BUCKET,
                s3_transcript_key
            )

            logger.info(f"☁️ Transcript uploaded to S3: s3://{AWS_S3_BUCKET}/{s3_transcript_key}")

        except Exception as s3_err:
            logger.error(f"Failed to upload transcript to S3: {s3_err}")
            # Continue even if S3 upload fails - we have local copy

        # Clean up temp audio file
        if os.path.exists(local_audio_path):
            os.remove(local_audio_path)
            logger.info(f"🗑️ Cleaned up temp file: {local_audio_path}")

        logger.info(f"🎉 Meeting summary ready for room \"{room_name}\"")
        logger.info(f"📄 Transcript file: {transcript_filename}")
        logger.info(f"📍 Local: {transcript_path}")
        logger.info(f"☁️ S3: s3://{AWS_S3_BUCKET}/recordings/{room_name}/{timestamp_str}_transcript.txt")

    except Exception as err:
        logger.error(f"Error processing recording for room \"{room_name}\": {err}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")



async def transcribe_audio(audio_file_path: str) -> Optional[str]:
    """Transcribe audio using Groq Whisper API"""
    try:
        groq = get_groq_client()
        if not groq:
            logger.error("Groq client not initialized (missing API key)")
            return None
        
        with open(audio_file_path, "rb") as audio_file:
            transcription = groq.audio.transcriptions.create(
                file=audio_file,
                model="whisper-large-v3",
                response_format="text",
                language="en"
            )
        
        return transcription
        
    except Exception as err:
        logger.error(f"Transcription error: {err}")
        return None

async def generate_summary(transcript: str, recording_data: Dict) -> Optional[Dict]:
    """Generate meeting summary using Groq Llama API"""
    try:
        groq = get_groq_client()
        if not groq:
            logger.error("Groq client not initialized (missing API key)")
            return None
        
        duration_min = int(recording_data['duration'] / 60)
        
        prompt = f"""You are an AI assistant that creates concise meeting summaries. 

Analyze this meeting transcript and provide a structured summary in JSON format:

Meeting Duration: {duration_min} minutes
Transcript:
{transcript}

Provide a JSON response with these fields:
1. "key_topics": Array of 3-5 main topics discussed (brief phrases)
2. "important_points": Array of 5-8 key points or decisions (one sentence each)
3. "action_items": Array of action items mentioned (format: "Person: Task" or just "Task" if no person mentioned)
4. "summary": A 2-3 sentence overall summary of the meeting

Keep it concise and professional. Focus on actionable information.
Return ONLY valid JSON, no markdown formatting."""

        response = groq.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a professional meeting summarizer. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1500
        )
        
        summary_text = response.choices[0].message.content.strip()
        
        # Try to parse as JSON
        import json
        try:
            # Remove markdown code blocks if present
            if summary_text.startswith("```"):
                summary_text = summary_text.split("```")[1]
                if summary_text.startswith("json"):
                    summary_text = summary_text[4:]
                summary_text = summary_text.strip()
            
            summary_json = json.loads(summary_text)
            return summary_json
        except json.JSONDecodeError:
            logger.error("Failed to parse summary as JSON, returning as text")
            return {
                "summary": summary_text,
                "key_topics": [],
                "important_points": [],
                "action_items": []
            }
        
    except Exception as err:
        logger.error(f"Summary generation error: {err}")
        return None

@app.get('/api/recording/summary/{room_name}')
async def get_meeting_summary(room_name: str):
    """Get meeting summary for a room"""
    if room_name not in meeting_summaries:
        raise HTTPException(status_code=404, detail="Summary not found. Recording may still be processing.")
    
    return meeting_summaries[room_name]

@app.get('/api/recording/summary/{room_name}/download')
async def download_meeting_summary(room_name: str):
    """Download meeting summary as text file"""
    if room_name not in meeting_summaries:
        raise HTTPException(status_code=404, detail="Summary not found")
    
    summary_data = meeting_summaries[room_name]
    
    # Format as readable text
    text_content = f"""MEETING SUMMARY
{'='*60}

Room: {room_name}
Date: {summary_data['timestamp']}
Duration: {int(summary_data['duration'] / 60)} minutes
Started by: {summary_data['startedBy']}

{'='*60}
OVERVIEW
{'='*60}

{summary_data['summary'].get('summary', 'No summary available')}

{'='*60}
KEY TOPICS
{'='*60}

"""
    
    for i, topic in enumerate(summary_data['summary'].get('key_topics', []), 1):
        text_content += f"{i}. {topic}\n"
    
    text_content += f"""
{'='*60}
IMPORTANT POINTS
{'='*60}

"""
    
    for i, point in enumerate(summary_data['summary'].get('important_points', []), 1):
        text_content += f"{i}. {point}\n"
    
    text_content += f"""
{'='*60}
ACTION ITEMS
{'='*60}

"""
    
    action_items = summary_data['summary'].get('action_items', [])
    if action_items:
        for i, item in enumerate(action_items, 1):
            text_content += f"[ ] {i}. {item}\n"
    else:
        text_content += "No action items identified.\n"
    
    text_content += f"""
{'='*60}
FULL TRANSCRIPT
{'='*60}

{summary_data['transcript']}

{'='*60}
End of Meeting Summary
{'='*60}
"""
    
    # Return as downloadable file
    from fastapi.responses import Response
    
    filename = f"meeting_summary_{room_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    
    return Response(
        content=text_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

@app.post('/api/webhook')
async def webhook_handler(request: Request):
    """Handle LiveKit webhooks"""
    webhook_start_time = datetime.now()
    
    # Always log webhook attempts, even if they fail
    webhook_logger.info(f" WEBHOOK RECEIVED")
    webhook_logger.info(f" Source IP: {request.client.host if request.client else 'unknown'}")
    webhook_logger.info(f" Timestamp: {webhook_start_time.isoformat()}")
    
    try:
        # Get the raw body and authorization header
        body = await request.body()
        auth_header = request.headers.get('authorization', '')
        
        # Log webhook reception details
        webhook_logger.info(f" Body Size: {len(body)} bytes")
        webhook_logger.info(f" Auth Header: {auth_header[:50]}..." if auth_header else "   🔐 No Auth Header")
        webhook_logger.info(f" Body Preview: {body.decode('utf-8')[:200]}..." if body else "   📄 Empty Body")
        
        # Verify webhook signature and parse event
        try:
            # event = webhook_receiver.receive(body.decode('utf-8'), auth_header)
            event = webhook_receiver.receive(body.decode('utf-8') , auth_header)
            webhook_logger.info(f" WEBHOOK SIGNATURE VERIFIED")
        except Exception as verify_err:
            webhook_logger.error(f" WEBHOOK SIGNATURE VERIFICATION FAILED: {verify_err}")
            raise verify_err
        
        # Log event details
        webhook_logger.info(f"WEBHOOK VERIFIED & PARSED")
        webhook_logger.info(f"Event Type: {event.event}")
        webhook_logger.info(f"Room: {event.room.name if event.room else 'N/A'}")
        webhook_logger.info(f"Participant: {event.participant.identity if event.participant else 'N/A'}")
        
        # Log to main logger as well
        logger.info(f"Webhook event: {event.event} - Room: {event.room.name if event.room else 'N/A'}")
        
        if event.event == 'room_finished':
            room_name = event.room.name if event.room else None
            webhook_logger.info(f"🏁 ROOM FINISHED: {room_name}")
            
            # Auto-stop recording if still active
            if room_name and room_name in active_recordings:
                recording_info = active_recordings[room_name]
                try:
                    # Import egress types
                    from livekit.protocol import egress as proto_egress
                    
                    audio_egress_id = recording_info['egressId']
                    audio_s3_key = recording_info.get('audioS3Key')
                    
                    # Stop audio egress session
                    stop_request_audio = proto_egress.StopEgressRequest(egress_id=audio_egress_id)
                    await api_client.egress.stop_egress(stop_request_audio)
                    
                    duration = (datetime.now() - recording_info['startTime']).total_seconds()
                    logger.info(f"⏹️ Auto-stopped audio recording for room \"{room_name}\" (duration: {duration:.1f}s)")
                    webhook_logger.info(f"⏹️ Auto-stopped audio recording (duration: {duration:.1f}s)")
                    
                    # Store recording info for transcription
                    recording_data = {
                        'roomName': room_name,
                        'audioS3Key': audio_s3_key,
                        'duration': duration,
                        'startedBy': recording_info['startedBy']
                    }
                    
                    # Remove from active recordings
                    del active_recordings[room_name]
                    
                    # Start transcription in background
                    if audio_s3_key and GROQ_API_KEY:
                        asyncio.create_task(process_recording_async(recording_data))
                        logger.info(f"🎙️ Starting AI transcription for room \"{room_name}\"")
                    
                except Exception as err:
                    logger.error(f"Failed to auto-stop recording: {err}")
            
            # Clean up in-memory state
            if room_name and room_name in room_creators:
                del room_creators[room_name]
                logger.info(f"Cleaned up creator map for room=\"{room_name}\"")
                webhook_logger.info(f"✅ Cleaned up creator state")
        
        elif event.event == 'participant_connected':
            room_name = event.room.name if event.room else None
            # participant = event.participant
            participant = even.participant
            
            webhook_logger.info(f" PARTICIPANT CONNECTED")
            webhook_logger.info(f" Room: {room_name}")
            webhook_logger.info(f" Participant: {participant.identity if participant else 'N/A'}")
            
            if room_name and participant:
                logger.info(f" Participant {participant.identity} joined room \"{room_name}\"")
        ### This logic is working fine but the concept 
        elif event.event == 'participant_disconnected':
            room_name = event.room.name if event.room else None
            participant = event.participant
            
            webhook_logger.info(f" PARTICIPANT DISCONNECTED")
            webhook_logger.info(f" Room: {room_name}")
            webhook_logger.info(f" Participant: {participant.identity if participant else 'N/A'}")
            
            if room_name and participant:
                logger.info(f" Participant {participant.identity} left room \"{room_name}\"")
        
        else:
            logger.info(f"  Unhandled webhook event: {event.event}")
            webhook_logger.info(f" UNHANDLED EVENT: {event.event}")
        
        # Log processing time
        processing_time = (datetime.now() - webhook_start_time).total_seconds()
        webhook_logger.info(f" Webhook processed in {processing_time:.3f}s")
        webhook_logger.info(f"{'='*60}")
        
        return Response(status_code=200)
    
    except Exception as err:
        processing_time = (datetime.now() - webhook_start_time).total_seconds()
        logger.error(f' Webhook processing failed: {err}')
        webhook_logger.error(f" WEBHOOK PROCESSING FAILED after {processing_time:.3f}s")
        webhook_logger.error(f"   Error: {str(err)}")
        
        import traceback
        logger.error(f'Traceback: {traceback.format_exc()}')
        webhook_logger.error(f"   Traceback: {traceback.format_exc()}")
        webhook_logger.info(f"{'='*60}")
        
        return Response(status_code=400)

# ── STATIC FILES AND FRONTEND ──────────────────────
# Serve static files (if you have a public directory)
if Path("public").exists():
    app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get('/', response_class=HTMLResponse)
async def serve_frontend():
    """Serve the main HTML file"""
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Frontend not found")



@app.get('/{path:path}', response_class=HTMLResponse)
async def serve_spa(path: str):
    """Serve SPA for all other routes"""
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Frontend not found")

# ── MAIN ───────────────────────────────────────────
if __name__ == "__main__":
    PORT = int(os.getenv('PORT', 3000))
    
    print(f"\n LiveKit Meet server running at http://localhost:{PORT}")
    print(f" Connecting to LiveKit at: {LIVEKIT_URL}")
    print(f"  API Key: {LIVEKIT_API_KEY}")
    print(f" Webhook endpoint: POST /api/webhook")
    print(f" Log files:")
    print(f"   • livekit_server.log - Main server events")
    print(f"   • webhook_events.log - Webhook events\n")
    
    # Test logging system
    logger.info("LiveKit Meet server starting up")
    webhook_logger.info("🔧 WEBHOOK LOGGER INITIALIZED")
    
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        log_level="info"
    )