# LiveKit Meeting Transcription Server

Real-time meeting transcription with speaker diarization, audio cleaning, and AI-powered transcription.

## Features

- 🎙️ **Speaker Diarization** - Separate audio tracks for each participant
- 🧹 **Audio Cleaning** - Noise reduction and silence removal
- 🤖 **AI Transcription** - Using Gemma 4 E4B model via Colab
- 🌍 **Multi-language** - Auto-detect and translate
- ☁️ **S3 Storage** - Automatic upload to AWS S3
- 📄 **JSON Output** - Structured transcripts with timestamps

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Create `.env` file:

```env
# LiveKit Configuration
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret
LIVEKIT_URL=wss://your-project.livekit.cloud

# AWS S3 Configuration
AWS_S3_BUCKET=your-bucket-name
AWS_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key

# Colab API Configuration
COLAB_API_URL=https://your-ngrok-url.ngrok-free.dev/transcribe
```

### 3. Start Colab Server

- Open Google Colab
- Copy code from `FIXED_COLAB_CODE.py`
- Run the notebook
- Copy the ngrok URL to `.env` as `COLAB_API_URL`

### 4. Start Server

```bash
python server.py
```

Server runs on `http://localhost:3000`

### 5. Open in Browser

Navigate to `http://localhost:3000` and start a meeting!

## Architecture

```
User → LiveKit Cloud → S3 Storage → Audio Cleaning → Colab (Gemma) → JSON Transcript
```

### Pipeline Flow:

1. **Recording** - LiveKit records individual participant tracks
2. **S3 Upload** - Audio files uploaded to S3
3. **Audio Cleaning** - Noise reduction + silence removal
4. **Transcription** - Colab processes cleaned audio
5. **JSON Generation** - Structured output with speaker labels
6. **Storage** - Final transcript saved to S3

## File Structure

```
.
├── server.py                          # Main FastAPI server
├── speaker_diarization_handler.py     # Speaker separation logic
├── audio_cleaner.py                   # Audio preprocessing
├── index.html                         # Frontend UI
├── requirements.txt                   # Python dependencies
├── .env                               # Configuration (create this)
├── FIXED_COLAB_CODE.py               # Colab server code
└── cleaned-audio/                     # Cleaned audio output folder
```

## Audio Cleaning

Cleaned audio files are automatically saved to `cleaned-audio/` folder for verification.

- Original audio: Downloaded from S3
- Cleaned audio: Noise reduced + silence removed
- Format: WAV (fast encoding)
- Location: `cleaned-audio/{room}_{speaker}_{timestamp}_cleaned.wav`

## Output Format

Transcripts are saved as JSON in S3:

```json
{
  "room_name": "Meril-room-123",
  "generated_at": "2026-04-18T12:00:00Z",
  "speakers": [
    {
      "identity": "user123",
      "display_name": "John Doe",
      "track_id": "user123"
    }
  ],
  "utterances": [
    {
      "speaker_name": "John Doe",
      "start_ms": 0,
      "end_ms": 5000,
      "original_text": "Hello",
      "english_text": "Hello",
      "language": "en",
      "audio_cleaned": true
    }
  ]
}
```

## Troubleshooting

### No transcription generated?
- Check Colab is running
- Verify `COLAB_API_URL` in `.env`
- Check logs: `livekit_server.log`

### Audio cleaning fails?
- Verify dependencies: `pip install librosa soundfile noisereduce`
- Check logs for errors
- System falls back to original audio if cleaning fails

### S3 upload issues?
- Verify AWS credentials in `.env`
- Check bucket permissions
- Ensure bucket exists in correct region

## Performance

### Audio Cleaning:
- 1-minute recording: ~5 seconds
- 5-minute recording: ~15 seconds
- 10-minute recording: ~30 seconds

### Transcription:
- Depends on Colab GPU availability
- Typically 10-20 seconds per participant

### Total Pipeline:
- ~60-90 seconds for 1-minute recording with 2 participants

## Logs

- `livekit_server.log` - Main server events
- `webhook_events.log` - Webhook events

## License

MIT
