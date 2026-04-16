"""
Test client for Colab LLM Transcription API
Tests both URL-based and file upload approaches
"""

import requests
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get Colab API URL from .env
API_URL = os.getenv('COLAB_API_URL', 'https://spotless-exerciser-sterilize.ngrok-free.dev/transcribe')
# 'https://fiona-hyperfine-ping.ngrok-free.dev/transcribe')

def test_with_url(audio_url: str, room_name: str = "test-room"):
    """
    Test transcription with S3 presigned URL (NEW METHOD)
    This is how your server will call the Colab API
    """
    print(f"� Testing URL-based transcription...")
    print(f"📍 API: {API_URL}")
    print(f"🔗 Audio URL: {audio_url[:80]}...")
    
    try:
        response = requests.post(
            API_URL,
            json={
                "audio_url": audio_url,
                "room_name": room_name
            },
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Success!")
            print(f"📝 Transcription: {result.get('transcription')}")
            print(f"⏱️ Duration: {result.get('audio_duration_seconds', 0):.2f}s")
            print(f"🎵 Sample Rate: {result.get('sample_rate')}Hz")
            return result
        else:
            print("❌ Failed!")
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
    except requests.exceptions.Timeout:
        print("❌ Request timeout (>120s)")
        return None
    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to {API_URL}")
        print("   Is your Colab server running?")
        print("   Is the ngrok tunnel active?")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def test_health_check():
    """Test if Colab API is online"""
    print(f"🏥 Checking API health...")
    
    try:
        # Remove /transcribe from URL for health check
        base_url = API_URL.replace('/transcribe', '')
        response = requests.get(base_url, timeout=10)
        
        if response.status_code == 200:
            print("✅ API is online!")
            print(f"📋 Response: {response.json()}")
            return True
        else:
            print(f"⚠️ API returned status {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ API is offline: {e}")
        return False

# ═══════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("="*60)
    print("COLAB LLM TRANSCRIPTION API - TEST CLIENT")
    print("="*60)
    print()
    
    # Test 1: Health check
    print("TEST 1: Health Check")
    print("-"*60)
    test_health_check()
    print()
    
    # Test 2: Transcription with URL
    print("TEST 2: Transcription with S3 URL")
    print("-"*60)
    
    # Example: Use a public audio URL for testing
    # Replace this with your actual S3 presigned URL
    test_audio_url = "https://example.com/sample-audio.mp3"
    
    print("⚠️ To test with real audio:")
    print("   1. Generate a presigned URL from your S3 bucket")
    print("   2. Replace 'test_audio_url' variable above")
    print("   3. Run this script again")
    print()
    
    # Uncomment to test with real URL:
    # test_with_url(test_audio_url, "test-room-123")
    
    print("="*60)
    print("✅ Test client ready!")
    print(f"📍 Configured API: {API_URL}")
    print("="*60)