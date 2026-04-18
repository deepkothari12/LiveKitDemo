#!/usr/bin/env python3
"""
Audio Cleaning Module
Removes background noise and silence from audio files before transcription
"""

import os
import io
import logging
import numpy as np
import librosa
import soundfile as sf
import noisereduce as nr
from typing import Tuple, Optional
import tempfile

logger = logging.getLogger('audio_cleaner')

class AudioCleaner:
    """Handles audio preprocessing: noise reduction and silence removal"""
    
    def __init__(self):
        self.target_sr = 16000  # Target sample rate for transcription
        self.silence_threshold_db = 30  # dB below which is considered silence
        self.min_silence_duration = 0.3  # Minimum silence duration to remove (seconds)
    
    def clean_audio_from_bytes(
        self, 
        audio_bytes: bytes,
        original_filename: str = "audio.ogg"
    ) -> Tuple[Optional[bytes], Optional[dict]]:
        """
        Clean audio from bytes (downloaded from S3)
        
        Args:
            audio_bytes: Raw audio file bytes
            original_filename: Original filename (for format detection)
            
        Returns:
            Tuple of (cleaned_audio_bytes, metadata_dict)
            Returns (None, None) if processing fails
        """
        import time
        
        try:
            start_time = time.time()
            
            logger.info(f"🧹 Starting audio cleaning...")
            logger.info(f"   📦 Input size: {len(audio_bytes):,} bytes")
            
            # Step 1: Load audio
            step_start = time.time()
            logger.info(f"   ⏳ Step 1/5: Loading audio...")
            audio_array, sr = librosa.load(
                io.BytesIO(audio_bytes),
                sr=None,  # Keep original sample rate initially
                mono=True
            )
            original_duration = len(audio_array) / sr
            logger.info(f"   ✅ Loaded: {original_duration:.2f}s at {sr}Hz ({time.time()-step_start:.1f}s)")
            
            # Step 2: Resample to target rate
            step_start = time.time()
            if sr != self.target_sr:
                logger.info(f"   ⏳ Step 2/5: Resampling {sr}Hz → {self.target_sr}Hz...")
                audio_array = librosa.resample(
                    audio_array, 
                    orig_sr=sr, 
                    target_sr=self.target_sr
                )
                sr = self.target_sr
                logger.info(f"   ✅ Resampled to {self.target_sr}Hz ({time.time()-step_start:.1f}s)")
            else:
                logger.info(f"   ⏭️ Step 2/5: Already at {self.target_sr}Hz, skipping resample")
            
            # Step 3: Noise reduction
            step_start = time.time()
            logger.info(f"   ⏳ Step 3/5: Removing background noise...")
            audio_array = self._reduce_noise(audio_array, sr)
            logger.info(f"   ✅ Noise reduced ({time.time()-step_start:.1f}s)")
            
            # Step 4: Remove silence
            step_start = time.time()
            logger.info(f"   ⏳ Step 4/5: Removing silence...")
            audio_array, intervals_removed = self._remove_silence(audio_array, sr)
            cleaned_duration = len(audio_array) / sr
            logger.info(f"   ✅ Silence removed: {intervals_removed} intervals ({time.time()-step_start:.1f}s)")
            logger.info(f"   📊 Duration: {original_duration:.2f}s → {cleaned_duration:.2f}s")
            
            # Step 5: Normalize audio
            step_start = time.time()
            logger.info(f"   ⏳ Step 5/5: Normalizing volume...")
            audio_array = librosa.util.normalize(audio_array)
            logger.info(f"   ✅ Normalized ({time.time()-step_start:.1f}s)")
            
            # Convert back to bytes (use WAV for speed, OGG encoding is slow)
            step_start = time.time()
            logger.info(f"   💾 Converting to bytes (WAV format for speed)...")
            output_buffer = io.BytesIO()
            sf.write(
                output_buffer,
                audio_array,
                sr,
                format='WAV',  # WAV is much faster than OGG
                subtype='PCM_16'
            )
            cleaned_bytes = output_buffer.getvalue()
            logger.info(f"   ✅ Converted: {len(cleaned_bytes):,} bytes ({time.time()-step_start:.1f}s)")
            
            # Metadata
            metadata = {
                'original_duration_seconds': round(original_duration, 2),
                'cleaned_duration_seconds': round(cleaned_duration, 2),
                'time_saved_seconds': round(original_duration - cleaned_duration, 2),
                'sample_rate': sr,
                'original_size_bytes': len(audio_bytes),
                'cleaned_size_bytes': len(cleaned_bytes),
                'silence_intervals_removed': intervals_removed,
                'processing_time_seconds': round(time.time() - start_time, 2)
            }
            
            total_time = time.time() - start_time
            logger.info(f"✅ Audio cleaning complete! (Total: {total_time:.1f}s)")
            logger.info(f"   📊 Original: {original_duration:.2f}s ({len(audio_bytes):,} bytes)")
            logger.info(f"   📊 Cleaned: {cleaned_duration:.2f}s ({len(cleaned_bytes):,} bytes)")
            logger.info(f"   💾 Saved: {metadata['time_saved_seconds']:.2f}s")
            
            return cleaned_bytes, metadata
            
        except Exception as e:
            logger.error(f"❌ Audio cleaning failed: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None, None
    
    def _reduce_noise(self, audio_array: np.ndarray, sr: int) -> np.ndarray:
        """
        Remove background noise using spectral gating
        
        Args:
            audio_array: Audio samples
            sr: Sample rate
            
        Returns:
            Noise-reduced audio array
        """
        try:
            # Use first 0.5 seconds as noise profile (or less if audio is shorter)
            noise_sample_duration = min(0.5, len(audio_array) / sr * 0.1)
            
            # Apply noise reduction
            reduced_noise = nr.reduce_noise(
                y=audio_array,
                sr=sr,
                stationary=True,  # Assume stationary noise
                prop_decrease=0.8,  # Reduce noise by 80%
                freq_mask_smooth_hz=500,  # Smooth frequency mask
                time_mask_smooth_ms=50  # Smooth time mask
            )
            
            return reduced_noise
            
        except Exception as e:
            logger.warning(f"⚠️ Noise reduction failed, using original audio: {e}")
            return audio_array
    
    def _remove_silence(
        self, 
        audio_array: np.ndarray, 
        sr: int
    ) -> Tuple[np.ndarray, int]:
        """
        Remove silence from audio (optimized for speed)
        
        Args:
            audio_array: Audio samples
            sr: Sample rate
            
        Returns:
            Tuple of (trimmed_audio, number_of_intervals_removed)
        """
        try:
            # For very long audio (>60s), use faster parameters
            duration = len(audio_array) / sr
            if duration > 60:
                logger.info(f"      ℹ️ Long audio detected ({duration:.1f}s), using fast mode...")
                frame_length = 4096  # Larger frame = faster
                hop_length = 1024    # Larger hop = faster
            else:
                frame_length = 2048
                hop_length = 512
            
            # Trim leading and trailing silence
            trimmed, index = librosa.effects.trim(
                audio_array,
                top_db=self.silence_threshold_db,
                frame_length=frame_length,
                hop_length=hop_length
            )
            
            # Split on silence to remove internal silence
            intervals = librosa.effects.split(
                trimmed,
                top_db=self.silence_threshold_db,
                frame_length=frame_length,
                hop_length=hop_length
            )
            
            # Keep only non-silent intervals
            non_silent_segments = []
            for start, end in intervals:
                segment = trimmed[start:end]
                # Only keep segments longer than minimum duration
                if len(segment) / sr >= self.min_silence_duration:
                    non_silent_segments.append(segment)
            
            # Concatenate all non-silent segments
            if non_silent_segments:
                result = np.concatenate(non_silent_segments)
                intervals_removed = len(intervals)
            else:
                # If all segments were too short, keep the trimmed audio
                result = trimmed
                intervals_removed = 0
            
            return result, intervals_removed
            
        except Exception as e:
            logger.warning(f"⚠️ Silence removal failed, using original audio: {e}")
            return audio_array, 0
    
    def clean_audio_file(
        self, 
        input_path: str, 
        output_path: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[dict]]:
        """
        Clean audio from file path
        
        Args:
            input_path: Path to input audio file
            output_path: Path to save cleaned audio (optional, creates temp file if None)
            
        Returns:
            Tuple of (output_file_path, metadata_dict)
        """
        try:
            # Read input file
            with open(input_path, 'rb') as f:
                audio_bytes = f.read()
            
            # Clean audio
            cleaned_bytes, metadata = self.clean_audio_from_bytes(
                audio_bytes,
                os.path.basename(input_path)
            )
            
            if cleaned_bytes is None:
                return None, None
            
            # Save to output file
            if output_path is None:
                # Create temp file
                temp_fd, output_path = tempfile.mkstemp(suffix='.ogg')
                os.close(temp_fd)
            
            with open(output_path, 'wb') as f:
                f.write(cleaned_bytes)
            
            logger.info(f"💾 Cleaned audio saved to: {output_path}")
            
            return output_path, metadata
            
        except Exception as e:
            logger.error(f"❌ File cleaning failed: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None, None


# Convenience function for quick testing
def clean_audio(input_path: str, output_path: str = None) -> bool:
    """
    Quick function to clean an audio file
    
    Args:
        input_path: Path to input audio file
        output_path: Path to save cleaned audio (optional)
        
    Returns:
        True if successful, False otherwise
    """
    cleaner = AudioCleaner()
    result_path, metadata = cleaner.clean_audio_file(input_path, output_path)
    
    if result_path:
        print(f"✅ Audio cleaned successfully!")
        print(f"📊 Metadata: {metadata}")
        return True
    else:
        print(f"❌ Audio cleaning failed")
        return False


if __name__ == "__main__":
    # Test the module
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python audio_cleaner.py <input_audio_file> [output_audio_file]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    logging.basicConfig(level=logging.INFO)
    
    success = clean_audio(input_file, output_file)
    sys.exit(0 if success else 1)
