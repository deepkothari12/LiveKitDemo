#!/usr/bin/env python3
"""
Speaker Diarization Handler
Records individual participant tracks and merges transcriptions with speaker labels
Generates detailed JSON format with timestamps and language detection
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import boto3
import requests
from audio_cleaner_enhanced import EnhancedAudioCleaner

logger = logging.getLogger('speaker_diarization')

class SpeakerDiarizationHandler:
    """Handles speaker-separated transcription"""
    
    def __init__(self, s3_client, colab_api_url: str, aws_s3_bucket: str):
        self.s3_client = s3_client
        self.colab_api_url = colab_api_url
        self.aws_s3_bucket = aws_s3_bucket
        self.audio_cleaner = EnhancedAudioCleaner()  # Use ENHANCED audio cleaner (95% noise removal)
        
        # Create cleaned-audio folder if it doesn't exist
        self.cleaned_audio_dir = Path("cleaned-audio")
        self.cleaned_audio_dir.mkdir(exist_ok=True)
        logger.info(f"📁 Cleaned audio will be saved to: {self.cleaned_audio_dir.absolute()}")
    
    async def process_multi_speaker_recording(
        self, 
        room_name: str,
        recording_info: Dict,
        duration: float
    ) -> Optional[Dict]:
        """
        Process recording with multiple speakers
        
        Args:
            room_name: Room identifier
            recording_info: Recording metadata with participant tracks
            duration: Recording duration in seconds
            
        Returns:
            Complete JSON structure with speakers and utterances
        """
        logger.info(f"🎭 Starting speaker diarization for room: {room_name}")
        logger.info(f"👥 Participants: {len(recording_info.get('trackEgressIds', {}))}")
        
        # Wait MUCH longer for S3 uploads to complete (LiveKit Cloud can be slow)
        # Individual track files take longer to upload than composite
        wait_time = max(45, int(duration * 0.5))  # At least 45 seconds, or 50% of recording duration
        logger.info(f"⏳ Waiting {wait_time} seconds for S3 uploads to complete...")
        logger.info(f"   ℹ️ Individual track files can take longer to upload than composite")
        await asyncio.sleep(wait_time)
        logger.info(f"✅ Wait complete, proceeding with transcription")
        
        track_egress_ids = recording_info.get('trackEgressIds', {})
        participant_info = recording_info.get('participantInfo', {})
        
        if not track_egress_ids:
            logger.warning(f"⚠️ No individual tracks found, cannot generate detailed transcript")
            return None
        
        # Build speakers list
        speakers = []
        source_files = []
        
        logger.info(f"📋 Building speakers list...")
        for speaker_identity, track_info in track_egress_ids.items():
            s3_key = track_info['s3_key']
            
            # Find participant display name
            display_name = speaker_identity
            for pid, pinfo in participant_info.items():
                if pinfo['identity'] == speaker_identity:
                    display_name = pinfo['name']
                    break
            
            speakers.append({
                "identity": speaker_identity,
                "display_name": display_name,
                "track_id": speaker_identity
            })
            
            source_files.append(s3_key)
            logger.info(f"   👤 {display_name} → {s3_key}")
        
        # Verify files exist in S3 before transcribing (with retry)
        logger.info(f"🔍 Verifying S3 files exist (with retry)...")
        missing_files = []
        for speaker_identity, track_info in track_egress_ids.items():
            s3_key = track_info['s3_key']
            
            # Try up to 3 times with 10 second delays
            found = False
            for attempt in range(3):
                try:
                    self.s3_client.head_object(Bucket=self.aws_s3_bucket, Key=s3_key)
                    logger.info(f"   ✅ {s3_key} exists in S3")
                    found = True
                    break
                except Exception as e:
                    if attempt < 2:
                        logger.warning(f"   ⏳ {s3_key} not found yet, waiting 10s... (attempt {attempt+1}/3)")
                        await asyncio.sleep(10)
                    else:
                        logger.error(f"   ❌ {s3_key} NOT FOUND in S3 after 3 attempts")
                        logger.error(f"   ℹ️ LiveKit egress may not have uploaded this file")
                        logger.error(f"   ℹ️ Check LiveKit Cloud egress logs")
                        missing_files.append(s3_key)
        
        # If ANY files are missing, fall back to composite
        if missing_files:
            logger.error(f"❌ {len(missing_files)} individual track file(s) missing from S3")
            logger.error(f"   Missing files: {missing_files}")
            logger.error(f"   ℹ️ This is a LiveKit egress upload issue, not a code issue")
            logger.error(f"   ℹ️ Falling back to composite audio")
            return None
        
        # Transcribe each participant's audio separately
        all_utterances = []
        speaker_join_times = {}  # Track when each speaker joined
        
        for idx, (speaker_identity, track_info) in enumerate(track_egress_ids.items()):
            s3_key = track_info['s3_key']
            
            # Find display name and join time
            display_name = speaker_identity
            join_time_offset = 0
            
            for pid, pinfo in participant_info.items():
                if pinfo['identity'] == speaker_identity:
                    display_name = pinfo['name']
                    # Use join time if available, otherwise estimate based on order
                    if 'joined_at' in pinfo:
                        # Parse ISO format timestamp
                        try:
                            from dateutil import parser
                            join_dt = parser.parse(pinfo['joined_at'])
                            # Calculate offset from first participant
                            if idx == 0:
                                speaker_join_times['first'] = join_dt
                            else:
                                join_time_offset = (join_dt - speaker_join_times['first']).total_seconds() * 1000  # Convert to ms
                        except:
                            # Fallback: estimate 5 seconds between each speaker
                            join_time_offset = idx * 5000
                    else:
                        # Estimate: each speaker joins 5 seconds apart
                        join_time_offset = idx * 5000
                    break
            
            logger.info(f"🎙️ Transcribing {display_name} (offset: {join_time_offset}ms)...")
            
            utterances = await self._transcribe_speaker_track_detailed(
                s3_key, 
                speaker_identity,
                display_name,
                room_name,
                time_offset_ms=join_time_offset  # Pass offset to adjust timestamps
            )
            
            if utterances:
                all_utterances.extend(utterances)
                logger.info(f"✅ {display_name}: {len(utterances)} utterances")
            else:
                logger.warning(f"⚠️ Failed to transcribe {display_name}")
        
        if not all_utterances:
            logger.error(f"❌ No utterances generated")
            return None
        
        # Sort utterances by timestamp
        all_utterances.sort(key=lambda x: x['start_ms'])
        
        # Build complete JSON structure
        result = {
            "room_name": room_name,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "models": {
                "transcription": "whisper-colab",
                "translation": "auto"
            },
            "source_files": source_files,
            "speakers": speakers,
            "utterances": all_utterances
        }
        
        logger.info(f"✅ Speaker diarization complete: {len(all_utterances)} utterances")
        
        # Generate full merged transcript
        logger.info(f"📝 Generating full merged transcript...")
        full_transcript = self._generate_full_transcript(result)
        
        # Upload full transcript to S3
        transcript_s3_key = f"transcripts/{room_name}_full_transcript.txt"
        logger.info(f"📤 Uploading full transcript to S3: {transcript_s3_key}")
        
        try:
            self.s3_client.put_object(
                Bucket=self.aws_s3_bucket,
                Key=transcript_s3_key,
                Body=full_transcript.encode('utf-8'),
                ContentType='text/plain; charset=utf-8'
            )
            logger.info(f"✅ Full transcript uploaded to S3")
            
            # Add transcript location to result
            result["full_transcript_s3_key"] = transcript_s3_key
            
        except Exception as e:
            logger.error(f"⚠️ Failed to upload full transcript to S3: {e}")
        
        return result
    
    async def _transcribe_speaker_track_detailed(
        self, 
        s3_key: str, 
        speaker_identity: str,
        speaker_name: str,
        room_name: str,
        time_offset_ms: int = 0  # Offset to adjust timestamps for when speaker joined
    ) -> Optional[List[Dict]]:
        """
        Transcribe a single speaker's audio track with detailed utterances
        Includes audio cleaning (noise reduction + silence removal)
        
        Returns list of utterances with timestamps, language, and translations
        """
        try:
            # Step 1: Download audio from S3
            logger.info(f"   📥 Downloading audio from S3: {s3_key}")
            response = self.s3_client.get_object(Bucket=self.aws_s3_bucket, Key=s3_key)
            original_audio_bytes = response['Body'].read()
            logger.info(f"   ✅ Downloaded: {len(original_audio_bytes):,} bytes")
            
            # Step 2: Clean audio (noise reduction + silence removal)
            logger.info(f"   🧹 Cleaning audio (noise reduction + silence removal)...")
            cleaned_audio_bytes, cleaning_metadata = self.audio_cleaner.clean_audio_from_bytes(
                original_audio_bytes,
                s3_key.split('/')[-1]
            )
            
            if cleaned_audio_bytes is None:
                logger.warning(f"   ⚠️ Audio cleaning failed, using original audio")
                cleaned_audio_bytes = original_audio_bytes
                cleaning_metadata = None
            else:
                logger.info(f"   ✅ Audio cleaned successfully")
                if cleaning_metadata:
                    logger.info(f"   📊 Time saved: {cleaning_metadata['time_saved_seconds']:.2f}s")
                    logger.info(f"   📊 Size: {cleaning_metadata['original_size_bytes']:,} → {cleaning_metadata['cleaned_size_bytes']:,} bytes")
            
            # Step 3: Save cleaned audio locally for verification
            local_filename = f"{room_name}_{speaker_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_cleaned.wav"
            local_path = self.cleaned_audio_dir / local_filename
            
            logger.info(f"   💾 Saving cleaned audio locally: {local_filename}")
            with open(local_path, 'wb') as f:
                f.write(cleaned_audio_bytes)
            logger.info(f"   ✅ Saved to: {local_path.absolute()}")
            
            # Step 4: Upload cleaned audio to S3 (for debugging/verification)
            cleaned_s3_key = s3_key.replace('.ogg', '_cleaned.wav')
            logger.info(f"   📤 Uploading cleaned audio to S3: {cleaned_s3_key}")
            self.s3_client.put_object(
                Bucket=self.aws_s3_bucket,
                Key=cleaned_s3_key,
                Body=cleaned_audio_bytes,
                ContentType='audio/wav'
            )
            logger.info(f"   ✅ Cleaned audio uploaded")
            
            # Step 5: Generate presigned URL for cleaned audio
            logger.info(f"   📦 Generating presigned URL...")
            presigned_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.aws_s3_bucket,
                    'Key': cleaned_s3_key
                },
                ExpiresIn=3600
            )
            logger.info(f"   ✅ Presigned URL generated: {presigned_url[:80]}...")
            
            # Step 6: Call Colab API with cleaned audio
            logger.info(f"   📤 Sending cleaned audio to Colab API: {self.colab_api_url}")
            logger.info(f"   📍 Room: {room_name}_{speaker_name}")
            
            response = requests.post(
                self.colab_api_url,
                json={
                    "audio_url": presigned_url,
                    "room_name": f"{room_name}_{speaker_name}",
                    "language": "multilingual",  # Auto-detect language
                    "detailed": True  # Request detailed response with timestamps
                },
                timeout=None
            )
            
            logger.info(f"   📥 Response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"   ✅ Response received successfully")
                
                # Check if detailed format with timestamps is returned
                if "utterances" in result and result["utterances"]:
                    # Colab returned detailed format with REAL timestamps
                    utterances = result["utterances"]
                    logger.info(f"   📊 Detailed format with timestamps: {len(utterances)} utterances")
                    
                    # Convert to our format
                    formatted_utterances = []
                    for utt in utterances:
                        formatted_utterances.append({
                            "speaker_identity": speaker_identity,
                            "speaker_name": speaker_name,
                            "track_id": speaker_identity,
                            "start_ms": utt.get("start_ms", 0),
                            "end_ms": utt.get("end_ms", 0),
                            "language": result.get("language", "en"),
                            "original_text": utt.get("text", ""),
                            "english_text": utt.get("text", "")
                        })
                    
                    utterances = formatted_utterances
                    logger.info(f"   ✅ Using REAL timestamps from Colab")
                else:
                    # Colab returned simple format, convert it (ESTIMATED timestamps)
                    transcription = result.get("transcription", "")
                    logger.info(f"   📝 Simple format: {len(transcription)} chars, converting...")
                    logger.warning(f"   ⚠️ Using ESTIMATED timestamps (update Colab for real timestamps)")
                    utterances = self._convert_simple_to_detailed(
                        transcription,
                        speaker_identity,
                        speaker_name,
                        result  # Pass full Colab response for language/translation info
                    )
                    logger.info(f"   ✅ Converted to {len(utterances)} utterances (estimated timestamps)")
                
                # Add speaker info and cleaning metadata to each utterance
                for utterance in utterances:
                    # Ensure speaker info is set
                    utterance["speaker_identity"] = speaker_identity
                    utterance["speaker_name"] = speaker_name
                    utterance["track_id"] = speaker_identity
                    
                    # Adjust timestamps with offset (for speaker join time)
                    utterance["start_ms"] += time_offset_ms
                    utterance["end_ms"] += time_offset_ms
                    
                    if cleaning_metadata:
                        utterance["audio_cleaned"] = True
                        utterance["cleaning_metadata"] = cleaning_metadata
                
                return utterances
            else:
                logger.error(f"   ❌ Transcription failed for {speaker_name}: HTTP {response.status_code}")
                logger.error(f"   📋 Response body: {response.text[:500]}")
                logger.error(f"   🔗 URL used: {presigned_url[:100]}...")
                logger.error(f"   🎯 Colab API: {self.colab_api_url}")
                logger.error(f"   ℹ️ Check your Colab server logs for details")
                return None
                
        except requests.exceptions.ConnectionError as e:
            logger.error(f"   🔌 Connection error for {speaker_name}: Cannot connect to Colab API")
            logger.error(f"   🎯 URL: {self.colab_api_url}")
            logger.error(f"   ℹ️ Is your Colab server running?")
            logger.error(f"   ℹ️ Is the ngrok tunnel active?")
            return None
        except requests.exceptions.Timeout as e:
            logger.error(f"   ⏱️ Timeout error for {speaker_name}: Request took too long")
            return None
        except Exception as e:
            logger.error(f"   ❌ Unexpected error transcribing {speaker_name}: {e}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return None
    
    def _convert_simple_to_detailed(
        self,
        transcription: str,
        speaker_identity: str,
        speaker_name: str,
        colab_response: Dict = None
    ) -> List[Dict]:
        """
        Convert simple transcription to detailed format
        (Used when Colab doesn't return detailed format)
        """
        if not transcription:
            return []
        
        # Get language and English translation from Colab response
        detected_language = "en"
        english_text = transcription
        
        if colab_response:
            detected_language = colab_response.get("language", "en")
            english_text = colab_response.get("english_transcription", transcription)
        
        # Split by sentences (simple approach)
        sentences = [s.strip() for s in transcription.replace('?', '.').replace('!', '.').split('.') if s.strip()]
        
        utterances = []
        current_time = 0
        
        for sentence in sentences:
            # Estimate duration based on word count (rough estimate: 2 words per second)
            word_count = len(sentence.split())
            duration_ms = max(1000, word_count * 500)  # At least 1 second
            
            utterances.append({
                "speaker_identity": speaker_identity,
                "speaker_name": speaker_name,
                "track_id": speaker_identity,
                "start_ms": current_time,
                "end_ms": current_time + duration_ms,
                "language": detected_language,
                "original_text": sentence,
                "english_text": sentence if detected_language == "en" else english_text
            })
            
            current_time += duration_ms + 500  # Add 500ms pause between sentences
        
        return utterances
    
    def _generate_full_transcript(self, result: Dict) -> str:
        """
        Generate full merged transcript from JSON result
        
        Args:
            result: Complete JSON structure with speakers and utterances
            
        Returns:
            Formatted full transcript as string
        """
        lines = []
        
        # Header
        lines.append("=" * 70)
        lines.append("FULL MERGED TRANSCRIPT")
        lines.append("=" * 70)
        lines.append("")
        lines.append(f"Room: {result['room_name']}")
        lines.append(f"Generated: {result['generated_at']}")
        lines.append(f"Total Utterances: {len(result['utterances'])}")
        lines.append("")
        
        # Speakers
        speaker_names = [s['display_name'] for s in result['speakers']]
        lines.append(f"Speakers: {', '.join(speaker_names)}")
        lines.append("")
        
        # Utterance distribution
        lines.append("Utterance Distribution:")
        for speaker in result['speakers']:
            speaker_name = speaker['display_name']
            count = sum(1 for u in result['utterances'] if u['speaker_name'] == speaker_name)
            lines.append(f"  {speaker_name}: {count} utterances")
        lines.append("")
        
        lines.append("=" * 70)
        lines.append("CONVERSATION")
        lines.append("=" * 70)
        lines.append("")
        
        # Full conversation (merged)
        conversation_parts = []
        for utterance in result['utterances']:
            speaker = utterance['speaker_name']
            text = utterance['original_text']
            conversation_parts.append(f"{speaker}: {text}")
        
        lines.append(" ".join(conversation_parts))
        lines.append("")
        
        lines.append("=" * 70)
        lines.append("END OF TRANSCRIPT")
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    async def _transcribe_composite_audio(
        self, 
        s3_key: str,
        room_name: str,
        recording_info: Dict = None
    ) -> Optional[Dict]:
        """Fallback: transcribe composite audio with participant name if available"""
        try:
            logger.info(f"   📦 Generating presigned URL for composite audio...")
            presigned_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.aws_s3_bucket,
                    'Key': s3_key
                },
                ExpiresIn=3600
            )
            logger.info(f"   ✅ Presigned URL generated")
            
            logger.info(f"   📤 Sending composite audio to Colab API...")
            response = requests.post(
                self.colab_api_url,
                json={
                    "audio_url": presigned_url,
                    "room_name": room_name,
                    "language": "multilingual"
                },
                timeout=None
            )
            
            logger.info(f"   📥 Response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                transcription = result.get("transcription", "")
                english_transcription = result.get("english_transcription", transcription)
                detected_language = result.get("language", "auto")
                
                logger.info(f"   ✅ Transcription received: {len(transcription)} chars")
                logger.info(f"   🌍 Language: {detected_language}")
                
                # Try to get participant name from recording_info
                speaker_name = "Unknown"
                speaker_identity = "unknown"
                
                if recording_info:
                    participant_info = recording_info.get('participantInfo', {})
                    if participant_info:
                        # Get first participant (for single participant recordings)
                        first_participant = next(iter(participant_info.values()), None)
                        if first_participant:
                            speaker_name = first_participant.get('name', 'Unknown')
                            speaker_identity = first_participant.get('identity', 'unknown')
                            logger.info(f"   👤 Using participant name: {speaker_name}")
                
                # Return simple format with participant name and translation
                return {
                    "room_name": room_name,
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "models": {
                        "transcription": "whisper-colab",
                        "translation": "auto"
                    },
                    "source_files": [s3_key],
                    "speakers": [{
                        "identity": speaker_identity,
                        "display_name": speaker_name,
                        "track_id": "composite"
                    }] if speaker_name != "Unknown" else [],
                    "utterances": [{
                        "speaker_identity": speaker_identity,
                        "speaker_name": speaker_name,
                        "track_id": "composite",
                        "start_ms": 0,
                        "end_ms": 0,
                        "language": detected_language,
                        "original_text": transcription,
                        "english_text": english_transcription
                    }]
                }
            else:
                logger.error(f"   ❌ Composite transcription failed: HTTP {response.status_code}")
                logger.error(f"   📋 Response: {response.text[:500]}")
                return None
                
        except Exception as e:
            logger.error(f"   ❌ Error transcribing composite: {e}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return None
