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
from livekit.protocol import models

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# AWS S3 Configuration
AWS_S3_BUCKET = os.getenv('AWS_S3_BUCKET')
AWS_S3_REGION = os.getenv('AWS_S3_REGION', 'us-east-1')
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

# Groq API Configuration (DEPRECATED - not used, kept for backward compatibility)
# GROQ_API_KEY = os.getenv('GROQ_API_KEY')

# Colab LLM API Configuration
COLAB_API_URL = os.getenv('COLAB_API_URL')

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

# Groq client (DEPRECATED - not used)
# groq_client = None

# Speaker diarization handler (created lazily)
speaker_diarization_handler = None

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

# DEPRECATED: Groq is no longer used (using Colab with Gemma instead)
# def get_groq_client():
#     """Get or create Groq client"""
#     global groq_client
#     if groq_client is None and GROQ_API_KEY:
#         from groq import Groq
#         groq_client = Groq(api_key=GROQ_API_KEY)
#     return groq_client

def get_speaker_diarization_handler():
    """Get or create speaker diarization handler"""
    global speaker_diarization_handler
    if speaker_diarization_handler is None:
        import boto3
        from speaker_diarization_handler import SpeakerDiarizationHandler
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_S3_REGION
        )
        
        speaker_diarization_handler = SpeakerDiarizationHandler(
            s3_client=s3_client,
            colab_api_url=COLAB_API_URL,
            aws_s3_bucket=AWS_S3_BUCKET
        )
    return speaker_diarization_handler

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
    """Start recording with SPEAKER DIARIZATION - Records each participant separately"""
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
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Get current participants in the room
        logger.info(f"📋 Getting participants for room \"{room_name}\"...")
        participants_response = await api_client.room.list_participants(
            proto_room.ListParticipantsRequest(room=room_name)
        )
        
        participants = participants_response.participants
        logger.info(f"👥 Found {len(participants)} participant(s) in room")
        
        # S3 path for composite audio (backup/fallback)
        audio_s3_key = f"recordings/{room_name}/{timestamp}_composite_audio.mp3"
        
        # Configure S3 upload
        audio_s3_output = proto_egress.S3Upload(
            access_key=AWS_ACCESS_KEY_ID,
            secret=AWS_SECRET_ACCESS_KEY,
            region=AWS_S3_REGION,
            bucket=AWS_S3_BUCKET
        )
        
        # Create composite audio egress (fallback)
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
        
        # Start composite audio egress
        audio_egress_info = await api_client.egress.start_room_composite_egress(audio_egress_request)
        logger.info(f"Started composite audio recording (fallback)")
        
        # Start individual track recording for each participant
        track_egress_ids = {}
        participant_info = {}
        
        for participant in participants:
            # Store participant info
            participant_info[participant.sid] = {
                'identity': participant.identity,
                'name': participant.name or participant.identity,
                'joined_at': datetime.now().isoformat()
            }
            
            # Find audio track
            audio_track_sid = None
            for track in participant.tracks:
                if track.type == models.TrackType.AUDIO:
                    audio_track_sid = track.sid
                    break
            
            if audio_track_sid:
                # S3 path for this participant
                # NOTE: LiveKit uploads individual tracks as .ogg, not .mp3
                participant_s3_key = f"recordings/{room_name}/{timestamp}_{participant.identity}_audio.ogg"
                
                # Create track egress request
                track_egress_request = proto_egress.TrackEgressRequest(
                    room_name=room_name,
                    track_id=audio_track_sid,
                    file=proto_egress.DirectFileOutput(
                        filepath=participant_s3_key,
                        s3=audio_s3_output
                    )
                )
                
                try:
                    track_egress_info = await api_client.egress.start_track_egress(track_egress_request)
                    track_egress_ids[participant.identity] = {
                        'egress_id': track_egress_info.egress_id,
                        's3_key': participant_s3_key,
                        'track_id': audio_track_sid
                    }
                    logger.info(f"   🎙️ Started track recording for {participant.identity}")
                except Exception as track_err:
                    logger.error(f"   ⚠️ Failed to start track for {participant.identity}: {track_err}")
            else:
                logger.warning(f"   ⚠️ No audio track found for {participant.identity}")
        
        # Store recording state
        active_recordings[room_name] = {
            'egressId': audio_egress_info.egress_id,
            'startedBy': started_by,
            'startTime': datetime.now(),
            'audioS3Key': audio_s3_key,
            'participantInfo': participant_info,
            'trackEgressIds': track_egress_ids,
            'timestamp': timestamp
        }
        
        logger.info(f"Recording started for room \"{room_name}\"")
        logger.info(f"Composite: {audio_s3_key}")
        logger.info(f"Individual tracks: {len(track_egress_ids)}")

        return {
            'success': True,
            'egressId': audio_egress_info.egress_id,
            'roomName': room_name,
            'startedBy': started_by,
            'started': [
                f"Composite audio: S3 bucket {AWS_S3_BUCKET}",
                f"Individual tracks: {len(track_egress_ids)} participant(s)"
            ],
            's3Bucket': AWS_S3_BUCKET,
            'audioPath': audio_s3_key,
            'participantCount': len(track_egress_ids),
            'participants': list(track_egress_ids.keys()),
            'speakerDiarization': len(track_egress_ids) > 0
        }
        
    except Exception as err:
        logger.error(f"Failed to start recording: {err}")
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
        track_egress_ids = recording_info.get('trackEgressIds', {})
        
        # Import egress types for stop request
        from livekit.protocol import egress as proto_egress
        
        # Stop composite audio egress
        stop_request_audio = proto_egress.StopEgressRequest(egress_id=audio_egress_id)
        await api_client.egress.stop_egress(stop_request_audio)
        logger.info(f"   ⏹️ Stopped composite audio")
        
        # Stop ALL individual track egress sessions (including late joiners)
        logger.info(f"   🔄 Stopping {len(track_egress_ids)} individual tracks...")
        for speaker_name, track_info in track_egress_ids.items():
            try:
                stop_request_track = proto_egress.StopEgressRequest(egress_id=track_info['egress_id'])
                await api_client.egress.stop_egress(stop_request_track)
                logger.info(f"   ⏹️ Stopped track for {speaker_name}")
            except Exception as track_err:
                logger.error(f"   ⚠️ Failed to stop track for {speaker_name}: {track_err}")
        
        duration = (datetime.now() - recording_info['startTime']).total_seconds()
        logger.info(f"⏹️ Recording stopped for room \"{room_name}\" (duration: {duration:.1f}s)")
        
        # Store recording info for transcription
        recording_data = {
            'roomName': room_name,
            'audioS3Key': audio_s3_key,
            'duration': duration,
            'startedBy': recording_info['startedBy'],
            'trackEgressIds': track_egress_ids,
            'participantInfo': recording_info.get('participantInfo', {}),
            'timestamp': recording_info.get('timestamp')
        }
        
        # Remove from active recordings
        del active_recordings[room_name]
        
        # Start transcription with speaker diarization in background (ALWAYS use JSON format)
        if COLAB_API_URL:
            asyncio.create_task(process_recording_with_speakers_async(recording_data))
            logger.info(f"🎙️ Starting AI transcription for room \"{room_name}\"")
        
        return {
            'success': True,
            'egressId': audio_egress_id,
            'roomName': room_name,
            'duration': duration,
            'message': 'Recording stopped. Generating transcript with speaker labels...' if track_egress_ids else 'Recording stopped. Generating transcript...',
            'speakerCount': len(track_egress_ids),
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


async def start_track_for_participant(room_name: str, participant):
    """
    Start track recording for a participant who joined after recording started
    
    Args:
        room_name: Room name
        participant: Participant object from webhook
    """
    if room_name not in active_recordings:
        logger.warning(f"⚠️ No active recording for room {room_name}")
        return
    
    recording_info = active_recordings[room_name]
    
    try:
        api_client = await get_livekit_api()
        
        # Import egress types
        from livekit.protocol import egress as proto_egress
        
        # CRITICAL FIX: Get fresh participant info from API
        # The webhook participant object might not have tracks populated yet
        logger.info(f"   📋 Fetching participant info from API...")
        participants_response = await api_client.room.list_participants(
            proto_room.ListParticipantsRequest(room=room_name)
        )
        
        # Find this participant in the API response
        target_participant = None
        for p in participants_response.participants:
            if p.identity == participant.identity:
                target_participant = p
                break
        
        if not target_participant:
            logger.warning(f"⚠️ Participant {participant.identity} not found in room")
            return
        
        # Find audio track for this participant (using fresh data from API)
        audio_track_sid = None
        for track in target_participant.tracks:
            if track.type == models.TrackType.AUDIO:
                audio_track_sid = track.sid
                break
        
        if not audio_track_sid:
            logger.warning(f"⚠️ No audio track found for {participant.identity}")
            return
        
        # Use the same timestamp as the original recording
        timestamp = recording_info.get('timestamp')
        
        # S3 path for this participant
        # NOTE: LiveKit uploads individual tracks as .ogg, not .mp3
        participant_s3_key = f"recordings/{room_name}/{timestamp}_{participant.identity}_audio.ogg"
        
        # Configure S3 upload (reuse from recording_info)
        audio_s3_output = proto_egress.S3Upload(
            access_key=AWS_ACCESS_KEY_ID,
            secret=AWS_SECRET_ACCESS_KEY,
            region=AWS_S3_REGION,
            bucket=AWS_S3_BUCKET
        )
        
        # Create track egress request
        track_egress_request = proto_egress.TrackEgressRequest(
            room_name=room_name,
            track_id=audio_track_sid,
            file=proto_egress.DirectFileOutput(
                filepath=participant_s3_key,
                s3=audio_s3_output
            )
        )
        
        # Start track egress
        track_egress_info = await api_client.egress.start_track_egress(track_egress_request)
        
        # Update recording info with new participant
        if 'trackEgressIds' not in recording_info:
            recording_info['trackEgressIds'] = {}
        
        if 'participantInfo' not in recording_info:
            recording_info['participantInfo'] = {}
        
        recording_info['trackEgressIds'][participant.identity] = {
            'egress_id': track_egress_info.egress_id,
            's3_key': participant_s3_key,
            'track_id': audio_track_sid
        }
        
        # Use target_participant (from API) instead of webhook participant
        recording_info['participantInfo'][target_participant.sid] = {
            'identity': participant.identity,
            'name': target_participant.name or participant.identity,
            'joined_at': datetime.now().isoformat()
        }
        
        logger.info(f"✅ Started track recording for late joiner: {participant.identity}")
        logger.info(f"   📦 S3 Key: {participant_s3_key}")
        logger.info(f"   🎙️ Egress ID: {track_egress_info.egress_id}")
        logger.info(f"   👤 Name: {target_participant.name or participant.identity}")
        
    except Exception as e:
        logger.error(f"❌ Failed to start track for {participant.identity}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")


# ── TRANSCRIPTION & SUMMARY FUNCTIONS ──────────────
async def process_recording_with_speakers_async(recording_data: Dict):
    """Background task to transcribe with speaker diarization (JSON format) - ALWAYS"""
    room_name = recording_data['roomName']
    track_egress_ids = recording_data.get('trackEgressIds', {})
    
    logger.info(f"{'='*60}")
    logger.info(f"🎬 STARTING TRANSCRIPTION PIPELINE (JSON FORMAT)")
    logger.info(f"{'='*60}")
    logger.info(f"📍 Room: {room_name}")
    logger.info(f"👥 Speakers: {len(track_egress_ids)}")
    logger.info(f"⏱️ Duration: {recording_data.get('duration', 0):.1f}s")
    
    try:
        handler = get_speaker_diarization_handler()
        
        # Use speaker diarization if we have individual tracks
        if track_egress_ids and len(track_egress_ids) > 0:
            logger.info(f"🎭 Using speaker diarization (individual tracks)")
            
            transcript_data = await handler.process_multi_speaker_recording(
                room_name=room_name,
                recording_info=recording_data,
                duration=recording_data['duration']
            )
            
            if transcript_data:
                logger.info(f"✅ Speaker-labeled transcript generated: {len(transcript_data['utterances'])} utterances")
            else:
                logger.error(f"❌ Speaker diarization failed, falling back to composite")
                # Fallback to composite audio
                transcript_data = await handler._transcribe_composite_audio(
                    recording_data.get('audioS3Key'),
                    room_name,
                    recording_data  # Pass recording_info for participant name
                )
        else:
            logger.info(f"ℹ️ No individual tracks, using composite audio")
            # Use composite audio (still JSON format)
            transcript_data = await handler._transcribe_composite_audio(
                recording_data.get('audioS3Key'),
                room_name,
                recording_data  # Pass recording_info for participant name
            )
        
        if not transcript_data:
            logger.error(f"❌ Transcription failed completely")
            return
        
        # Save JSON transcript to S3 only (not locally, not in UI)
        await save_transcript_json_to_s3(room_name, transcript_data, recording_data)
        
        logger.info(f"{'='*60}")
        logger.info(f"🎉 TRANSCRIPTION COMPLETE: {room_name}")
        logger.info(f"{'='*60}")
        
    except Exception as e:
        logger.error(f"❌ Transcription error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")



async def save_transcript_json_to_s3(
    room_name: str,
    transcript_data: Dict,
    recording_data: Dict
):
    """Save transcript JSON directly to S3 (not locally, not in UI)"""
    logger.info(f"⏳ Saving transcript JSON to S3...")
    
    try:
        import boto3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_S3_REGION
        )
        
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_transcript_key = f"recordings/{room_name}/{timestamp_str}_transcript.json"
        
        # Convert to JSON string
        json_content = json.dumps(transcript_data, indent=2, ensure_ascii=False)
        
        # Upload directly to S3 (no local file)
        s3_client.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=s3_transcript_key,
            Body=json_content.encode('utf-8'),
            ContentType='application/json'
        )
        
        logger.info(f"✅ Transcript JSON saved to S3: s3://{AWS_S3_BUCKET}/{s3_transcript_key}")
        logger.info(f"📊 Summary:")
        logger.info(f"   👥 Speakers: {len(transcript_data.get('speakers', []))}")
        logger.info(f"   💬 Utterances: {len(transcript_data.get('utterances', []))}")
        logger.info(f"   ⏱️ Duration: {int(recording_data['duration'] / 60)}m {int(recording_data['duration'] % 60)}s")
        logger.info(f"   ☁️ S3: {s3_transcript_key}")
        
        # Store minimal info in memory (for API access, but not full transcript)
        meeting_summaries[room_name] = {
            'transcript_s3_key': s3_transcript_key,
            'timestamp': datetime.now().isoformat(),
            'duration': recording_data['duration'],
            'startedBy': recording_data['startedBy'],
            'speakerCount': len(transcript_data.get('speakers', [])),
            'utteranceCount': len(transcript_data.get('utterances', [])),
            'speakerDiarization': True
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to save transcript JSON to S3: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")



async def transcribe_audio_colab(audio_url: str, room_name: str) -> Optional[str]:
    """
    Transcribe audio using Colab LLM API with S3 presigned URL
    
    Args:
        audio_url: S3 presigned URL to the audio file
        room_name: Room name for logging purposes
    
    Returns:
        Transcription text or None if failed
    """
    logger.info(f"   ┌─────────────────────────────────────────────────")
    logger.info(f"   │ COLAB API CALL - DETAILED LOG")
    logger.info(f"   ├─────────────────────────────────────────────────")
    
    if not COLAB_API_URL:
        logger.error(f"   │ COLAB_API_URL not configured in .env file")
        logger.error(f"   │ Add: COLAB_API_URL=https://your-ngrok-url.ngrok-free.dev/transcribe")
        logger.info(f"   └─────────────────────────────────────────────────")
        return None
    
    try:
        import requests
        from datetime import datetime
        
        logger.info(f"Target URL: {COLAB_API_URL}")
        logger.info(f"Room: {room_name}")
        logger.info(f"Audio URL: {audio_url[:80]}...")
        logger.info(f" Timeout: None (unlimited - can handle any duration)")
        logger.info(f"   ├─────────────────────────────────────────────────")
        
        start_time = datetime.now()
        logger.info(f"   │ 📤 Sending POST request to Colab...")
        logger.info(f"   │ ⏰ Request started at: {start_time.strftime('%H:%M:%S')}")
        
        # Send audio URL to Colab LLM for transcription
        response = requests.post(
            COLAB_API_URL,
            json={
                "audio_url": audio_url,
                "room_name": room_name,
                "language": "en"  # "en" for English, "multilingual" for auto-detect
            },
            timeout=None  # No timeout - allow any duration
        )
        
        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds()
        
        logger.info(f"   │ 📥 Response received from Colab")
        logger.info(f"   │ ⏰ Response time: {elapsed:.2f} seconds")
        logger.info(f"   │ 📊 Status code: {response.status_code}")
        logger.info(f"   ├─────────────────────────────────────────────────")
        
        if response.status_code == 200:
            logger.info(f"   │ ✅ HTTP 200 OK - Parsing response...")
            result = response.json()
            transcription = result.get("transcription", "")
            
            logger.info(f"   │ 📝 Transcription length: {len(transcription)} characters")
            logger.info(f"   │ 🎵 Audio duration: {result.get('audio_duration_seconds', 0):.2f}s")
            logger.info(f"   │ 📊 Sample rate: {result.get('sample_rate', 0)}Hz")
            logger.info(f"   │ ✅ Status: {result.get('status', 'unknown')}")
            
            if transcription:
                logger.info(f"   │ 📄 Preview: {transcription[:100]}...")
                logger.info(f"   │ ✅ Transcription successful!")
                logger.info(f"   └─────────────────────────────────────────────────")
                return transcription
            else:
                logger.error(f"   │ ❌ Colab returned empty transcription")
                logger.error(f"   │ 📋 Full response: {result}")
                logger.info(f"   └─────────────────────────────────────────────────")
                return None
        else:
            logger.error(f"   │ ❌ HTTP {response.status_code} - Request failed")
            logger.error(f"   │ 📋 Response: {response.text[:200]}")
            logger.info(f"   └─────────────────────────────────────────────────")
            return None
            
    except requests.exceptions.Timeout:
        logger.error(f"   │ ⏱️ ❌ TIMEOUT (This should not happen with unlimited timeout)")
        logger.error(f"   │ ℹ️ Check network connection")
        logger.info(f"   └─────────────────────────────────────────────────")
        return None
    except requests.exceptions.ConnectionError:
        logger.error(f"   │ 🔌 ❌ CONNECTION ERROR")
        logger.error(f"   │ ℹ️ Cannot connect to: {COLAB_API_URL}")
        logger.error(f"   │ ℹ️ Possible causes:")
        logger.error(f"   │    - Colab server not running")
        logger.error(f"   │    - ngrok tunnel expired")
        logger.error(f"   │    - Wrong URL in .env file")
        logger.error(f"   │ ℹ️ Check Colab output for ngrok URL")
        logger.info(f"   └─────────────────────────────────────────────────")
        return None
    except Exception as err:
        logger.error(f"   │ ❌ UNEXPECTED ERROR: {err}")
        import traceback
        logger.error(f"   │ Traceback:")
        for line in traceback.format_exc().split('\n'):
            if line.strip():
                logger.error(f"   │   {line}")
        logger.info(f"   └─────────────────────────────────────────────────")
        return None

# DEPRECATED: Summary generation is no longer used (JSON format only)
async def generate_summary(transcript: str, recording_data: Dict) -> Optional[Dict]:
    """Generate meeting summary using Groq Llama API (DEPRECATED - NOT USED)"""
    logger.info(f"   ┌─────────────────────────────────────────────────")
    logger.info(f"   │ GROQ SUMMARY GENERATION - DEPRECATED (NOT USED)")
    logger.info(f"   ├─────────────────────────────────────────────────")
    
    try:
        groq = get_groq_client()
        if not groq:
            logger.error(f"   │ ❌ Groq client not initialized (missing API key)")
            logger.error(f"   │ ℹ️ Check GROQ_API_KEY in .env file")
            logger.info(f"   └─────────────────────────────────────────────────")
            return None
        
        duration_min = int(recording_data['duration'] / 60)
        
        logger.info(f"   │ 📝 Transcript length: {len(transcript)} characters")
        logger.info(f"   │ ⏱️ Meeting duration: {duration_min} minutes")
        logger.info(f"   │ 🤖 Model: llama-3.1-70b-versatile")
        logger.info(f"   │ 🌡️ Temperature: 0.3")
        logger.info(f"   │ 📊 Max tokens: 1500")
        logger.info(f"   ├─────────────────────────────────────────────────")
        logger.info(f"   │ 📤 Sending request to Groq API...")
        
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
        
        logger.info(f"   │ 📥 Response received from Groq")
        
        summary_text = response.choices[0].message.content.strip()
        logger.info(f"   │ 📝 Response length: {len(summary_text)} characters")
        logger.info(f"   ├─────────────────────────────────────────────────")
        logger.info(f"   │ 🔄 Parsing JSON response...")
        
        # Try to parse as JSON
        import json
        try:
            # Remove markdown code blocks if present
            if summary_text.startswith("```"):
                logger.info(f"   │ ℹ️ Removing markdown code blocks...")
                summary_text = summary_text.split("```")[1]
                if summary_text.startswith("json"):
                    summary_text = summary_text[4:]
                summary_text = summary_text.strip()
            
            summary_json = json.loads(summary_text)
            logger.info(f"   │ ✅ JSON parsed successfully")
            logger.info(f"   │ 🎯 Key topics: {len(summary_json.get('key_topics', []))}")
            logger.info(f"   │ 💡 Important points: {len(summary_json.get('important_points', []))}")
            logger.info(f"   │ ✓ Action items: {len(summary_json.get('action_items', []))}")
            logger.info(f"   └─────────────────────────────────────────────────")
            logger.info(f"")
            logger.info(f"{'='*60}")
            logger.info(f"🤖 GROQ LLAMA OUTPUT - SUMMARY")
            logger.info(f"{'='*60}")
            logger.info(f"Summary: {summary_json.get('summary', '')}")
            logger.info(f"")
            logger.info(f"Key Topics:")
            for i, topic in enumerate(summary_json.get('key_topics', []), 1):
                logger.info(f"  {i}. {topic}")
            logger.info(f"")
            logger.info(f"Important Points:")
            for i, point in enumerate(summary_json.get('important_points', []), 1):
                logger.info(f"  {i}. {point}")
            logger.info(f"")
            logger.info(f"Action Items:")
            action_items = summary_json.get('action_items', [])
            if action_items:
                for i, item in enumerate(action_items, 1):
                    logger.info(f"  {i}. {item}")
            else:
                logger.info(f"  (none)")
            logger.info(f"{'='*60}")
            logger.info(f"✅ Above summary was generated by GROQ LLAMA MODEL")
            logger.info(f"{'='*60}")
            logger.info(f"")
            return summary_json
        except json.JSONDecodeError as je:
            logger.error(f"   │ ⚠️ JSON parsing failed: {je}")
            logger.error(f"   │ ℹ️ Returning as plain text fallback")
            logger.info(f"   └─────────────────────────────────────────────────")
            return {
                "summary": summary_text,
                "key_topics": [],
                "important_points": [],
                "action_items": []
            }
        
    except Exception as err:
        logger.error(f"   │ ❌ Summary generation error: {err}")
        import traceback
        logger.error(f"   │ Traceback:")
        for line in traceback.format_exc().split('\n'):
            if line.strip():
                logger.error(f"   │   {line}")
        logger.info(f"   └─────────────────────────────────────────────────")
        return None

@app.get('/api/recording/summary/{room_name}')
async def get_meeting_summary(room_name: str):
    """Get meeting summary metadata (transcript is in S3, not returned here)"""
    if room_name not in meeting_summaries:
        raise HTTPException(status_code=404, detail="Summary not found. Recording may still be processing.")
    
    summary_data = meeting_summaries[room_name]
    
    # Return metadata only, not full transcript
    return {
        'room_name': room_name,
        'timestamp': summary_data['timestamp'],
        'duration': summary_data['duration'],
        'startedBy': summary_data['startedBy'],
        'speakerCount': summary_data.get('speakerCount', 0),
        'utteranceCount': summary_data.get('utteranceCount', 0),
        'speakerDiarization': summary_data.get('speakerDiarization', False),
        'transcript_s3_key': summary_data.get('transcript_s3_key'),
        'transcript_url': f"s3://{AWS_S3_BUCKET}/{summary_data.get('transcript_s3_key')}" if summary_data.get('transcript_s3_key') else None,
        'message': 'Transcript is stored in S3. Download from S3 bucket to view.'
    }

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
                    
                    # Start transcription in background (DEPRECATED - this code path is not used)
                    # if audio_s3_key:
                    #     asyncio.create_task(process_recording_async(recording_data))
                    #     logger.info(f"🎙️ Starting AI transcription for room \"{room_name}\"")
                    
                except Exception as err:
                    logger.error(f"Failed to auto-stop recording: {err}")
            
            # Clean up in-memory state
            if room_name and room_name in room_creators:
                del room_creators[room_name]
                logger.info(f"Cleaned up creator map for room=\"{room_name}\"")
                webhook_logger.info(f"✅ Cleaned up creator state")
        
        elif event.event == 'participant_connected':
            room_name = event.room.name if event.room else None
            participant = event.participant
            
            webhook_logger.info(f"👤 PARTICIPANT CONNECTED")
            webhook_logger.info(f"   Room: {room_name}")
            webhook_logger.info(f"   Participant: {participant.identity if participant else 'N/A'}")
            
            if room_name and participant:
                logger.info(f"👤 Participant {participant.identity} joined room \"{room_name}\"")
                
                # Check if recording is active for this room
                if room_name in active_recordings:
                    logger.info(f"🔴 Recording is active, starting track for late joiner: {participant.identity}")
                    webhook_logger.info(f"   🎙️ Starting track recording for late joiner")
                    
                    try:
                        # Start track recording for this participant
                        await start_track_for_participant(room_name, participant)
                    except Exception as e:
                        logger.error(f"❌ Failed to start track for late joiner {participant.identity}: {e}")
                        webhook_logger.error(f"   ❌ Track recording failed: {e}")
        
        elif event.event == 'participant_disconnected':
            room_name = event.room.name if event.room else None
            participant = event.participant
            
            webhook_logger.info(f" PARTICIPANT DISCONNECTED")
            webhook_logger.info(f" Room: {room_name}")
            webhook_logger.info(f" Participant: {participant.identity if participant else 'N/A'}")
            
            if room_name and participant:
                logger.info(f" Participant {participant.identity} left room \"{room_name}\"")
        
        elif event.event == 'egress_ended':
            # This is the critical event for recording completion!
            egress_info = event.egress_info
            room_name = egress_info.room_name if egress_info else None
            
            webhook_logger.info(f"🎬 EGRESS ENDED (RECORDING COMPLETE)")
            webhook_logger.info(f"   Room: {room_name}")
            webhook_logger.info(f"   Egress ID: {egress_info.egress_id if egress_info else 'N/A'}")
            webhook_logger.info(f"   Status: {egress_info.status if egress_info else 'N/A'}")
            
            if room_name and room_name in active_recordings:
                recording_info = active_recordings[room_name]
                duration = (datetime.now() - recording_info['startTime']).total_seconds()
                
                logger.info(f"🎬 Recording completed for room \"{room_name}\" (duration: {duration:.1f}s)")
                webhook_logger.info(f"   Duration: {duration:.1f}s")
                
                # Prepare recording data for transcription
                recording_data = {
                    'roomName': room_name,
                    'audioS3Key': recording_info.get('audioS3Key'),
                    'trackEgressIds': recording_info.get('trackEgressIds', {}),
                    'participantInfo': recording_info.get('participantInfo', {}),
                    'duration': duration,
                    'startedBy': recording_info['startedBy'],
                    'timestamp': recording_info.get('timestamp')
                }
                
                # Remove from active recordings
                del active_recordings[room_name]
                
                # Start transcription in background
                logger.info(f"🎙️ Starting transcription pipeline for room \"{room_name}\"")
                webhook_logger.info(f"   🎙️ Starting transcription pipeline")
                asyncio.create_task(process_recording_with_speakers_async(recording_data))
            else:
                logger.warning(f"⚠️ Egress ended but no active recording found for room: {room_name}")
                webhook_logger.warning(f"   ⚠️ No active recording found")
        
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