# LiveKit Meeting Transcription with Speaker Diarization

Real-time meeting transcription system with speaker separation, audio cleaning, and chronological conversation flow.

## Features

✅ **Speaker Diarization** - Separate audio tracks for each participant
✅ **Enhanced Audio Cleaning** - 95% noise removal, silence removal
✅ **Real Timestamps** - Chronological conversation flow
✅ **Multi-language Support** - English, Hindi, Gujarati (Hinglish)
✅ **Full Transcripts** - JSON + merged text format
✅ **S3 Storage** - Automatic upload of transcripts and cleaned audio

## Architecture

- **Frontend**: HTML/JavaScript (LiveKit client)
- **Backend**: FastAPI (Python)
- **LiveKit Cloud**: Real-time audio streaming
- **Transcription**: Colab with Gemma 4 E4B model
- **Storage**: AWS S3

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Create `.env` file:

```env
# LiveKit Configuration
LIVEKIT_URL=wss://test-rb218b47.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# Colab API (for transcription)
COLAB_API_URL=https://your-ngrok-url.ngrok-free.dev/transcribe

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_S3_BUCKET=livekit-recordings-2026
AWS_S3_REGION=us-east-1
```

### 3. Setup Colab Transcription Server

1. Open Google Colab
2. Copy code from `COLAB_FASTAPI_WITH_TIMESTAMPS.py`
3. Update ngrok token
4. Run notebook
5. Copy ngrok URL to `.env`

### 4. Start Server

```bash
python server.py
```

Server runs at: http://localhost:3000

### 5. Join Meeting

Open browser: http://localhost:3000

## Workflow

1. **Meeting Starts** → LiveKit records separate audio tracks per participant
2. **Meeting Ends** → Audio files uploaded to S3
3. **Audio Cleaning** → Enhanced cleaning (95% noise removal)
4. **Transcription** → Colab processes each speaker's audio
5. **Timestamp Detection** → Real timestamps from audio analysis
6. **Chronological Sorting** → Utterances sorted by actual speech time
7. **S3 Upload** → JSON transcript + full text transcript

## Output Files

After each meeting, S3 contains:

```
recordings/{room_name}/
  ├── {timestamp}_transcript.json          # Detailed JSON with timestamps
  ├── {timestamp}_full_transcript.txt      # Merged conversation
  ├── {participant1}_cleaned.wav           # Cleaned audio
  └── {participant2}_cleaned.wav           # Cleaned audio
```

## Testing

### Test Multi-Speaker Transcription

```bash
python test_multi_speaker.py audio1.mp3 Speaker1 audio2.wav Speaker2
```

Output:
- `test-transcripts/test_multi_speaker_*.json`
- `test-transcripts/test_multi_speaker_*_full_transcript.txt`
- `cleaned-audio/test_*_cleaned.wav`

### Test Audio Cleaning

```bash
python test_audio_cleaning.py your_audio.mp3
```

Creates 3 versions: standard, enhanced, ultra

## Audio Cleaning

**Enhanced Cleaning (Active):**
- 95% noise reduction
- 35 dB silence threshold
- Pre-emphasis filter
- Two-pass noise reduction
- Dynamic compression
- 8 processing steps

**Ultra Cleaning (Available):**
- 98% noise reduction
- 30 dB silence threshold
- Three-pass noise reduction
- Spectral gating
- Click/pop removal
- 12 processing steps

## Transcription

**Model:** Gemma 4 E4B (via Colab)

**Features:**
- 30-second chunking for long audio
- Real timestamps using Voice Activity Detection
- Automatic spelling correction
- Multi-language support
- Chronological ordering

## File Structure

```
.
├── server.py                           # Main FastAPI server
├── speaker_diarization_handler.py      # Speaker separation logic
├── audio_cleaner_enhanced.py           # Enhanced audio cleaning (active)
├── audio_cleaner_ultra.py              # Ultra audio cleaning (optional)
├── test_multi_speaker.py               # Multi-speaker test script
├── test_full_transcription.py          # Full transcription test
├── test_transcription.py               # Basic transcription test
├── test_audio_cleaning.py              # Audio cleaning comparison
├── COLAB_FASTAPI_WITH_TIMESTAMPS.py    # Colab server code
├── UPDATE_YOUR_COLAB.txt               # Colab update guide
├── QUICK_START.txt                     # Quick start guide
├── requirements.txt                    # Python dependencies
├── index.html                          # Meeting UI
└── README.md                           # This file
```

## Configuration

### Switch to Ultra Cleaning

To use ultra-aggressive cleaning (98% noise removal):

1. Open `speaker_diarization_handler.py`
2. Change:
   ```python
   from audio_cleaner_enhanced import EnhancedAudioCleaner
   self.audio_cleaner = EnhancedAudioCleaner()
   ```
   To:
   ```python
   from audio_cleaner_ultra import UltraAudioCleaner
   self.audio_cleaner = UltraAudioCleaner()
   ```

### Update Colab Code

See `UPDATE_YOUR_COLAB.txt` for step-by-step instructions.

## Troubleshooting

### Transcripts Not Chronological

**Solution:** Update Colab code to include timestamp detection
- See `UPDATE_YOUR_COLAB.txt`
- Use `COLAB_FASTAPI_WITH_TIMESTAMPS.py`

### Audio Still Noisy

**Solution:** Switch to ultra cleaning
- Test with: `python test_audio_cleaning.py your_audio.mp3`
- Compare enhanced vs ultra
- Update `speaker_diarization_handler.py` if needed

### Spelling Errors in Transcription

**Solution:** Update Colab code
- Use `COLAB_FASTAPI_WITH_TIMESTAMPS.py`
- Includes automatic spelling correction

### Missing Dependencies

```bash
pip install -r requirements.txt
```

## Support

For issues or questions, check:
- `QUICK_START.txt` - Quick start guide
- `UPDATE_YOUR_COLAB.txt` - Colab update instructions
- `TEST_MULTI_SPEAKER_USAGE.txt` - Testing guide

## License

MIT License
