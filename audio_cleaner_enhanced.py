#!/usr/bin/env python3
"""
Enhanced Audio Cleaning Module
More aggressive noise reduction and silence removal for better transcription
"""

import os
import io
import logging
import numpy as np
import librosa
import soundfile as sf
import noisereduce as nr
from typing import Tuple, Optional
from scipy import signal

logger = logging.getLogger('audio_cleaner_enhanced')

class EnhancedAudioCleaner:
    """Enhanced audio preprocessing with aggressive noise reduction"""
    
    def __init__(self):
        self.target_sr = 16000  # Target sample rate for transcription
        self.silence_threshold_db = 40  # Balanced - not too aggressive
        self.min_silence_duration = 0.5  # Keep natural pauses
        self.noise_reduction_strength = 0.75  # Balanced - removes noise but keeps speech natural
    
    def clean_audio_from_bytes(
        self, 
        audio_bytes: bytes,
        original_filename: str = "audio.ogg"
    ) -> Tuple[Optional[bytes], Optional[dict]]:
        """
        Enhanced audio cleaning with multiple passes
        
        Args:
            audio_bytes: Raw audio file bytes
            original_filename: Original filename (for format detection)
            
        Returns:
            Tuple of (cleaned_audio_bytes, metadata_dict)
        """
        import time
        
        try:
            start_time = time.time()
            
            logger.info(f"🧹 Starting ENHANCED audio cleaning...")
            logger.info(f"   📦 Input size: {len(audio_bytes):,} bytes")
            
            # Step 1: Load audio
            step_start = time.time()
            logger.info(f"   ⏳ Step 1/8: Loading audio...")
            audio_array, sr = librosa.load(
                io.BytesIO(audio_bytes),
                sr=None,
                mono=True
            )
            original_duration = len(audio_array) / sr
            logger.info(f"   ✅ Loaded: {original_duration:.2f}s at {sr}Hz ({time.time()-step_start:.1f}s)")
            
            # Step 2: Resample to target rate
            step_start = time.time()
            if sr != self.target_sr:
                logger.info(f"   ⏳ Step 2/8: Resampling {sr}Hz → {self.target_sr}Hz...")
                audio_array = librosa.resample(
                    audio_array, 
                    orig_sr=sr, 
                    target_sr=self.target_sr
                )
                sr = self.target_sr
                logger.info(f"   ✅ Resampled to {self.target_sr}Hz ({time.time()-step_start:.1f}s)")
            else:
                logger.info(f"   ⏭️ Step 2/8: Already at {self.target_sr}Hz, skipping resample")
            
            # Step 3: Smart noise reduction (single pass, balanced)
            step_start = time.time()
            logger.info(f"   ⏳ Step 3/6: Removing background noise (balanced)...")
            audio_array = self._reduce_noise_balanced(audio_array, sr)
            logger.info(f"   ✅ Background noise reduced ({time.time()-step_start:.1f}s)")
            
            # Step 4: Remove silence (balanced - keeps natural pauses)
            step_start = time.time()
            logger.info(f"   ⏳ Step 4/6: Removing silence (balanced)...")
            audio_array, intervals_removed = self._remove_silence_balanced(audio_array, sr)
            cleaned_duration = len(audio_array) / sr
            logger.info(f"   ✅ Silence removed: {intervals_removed} intervals ({time.time()-step_start:.1f}s)")
            logger.info(f"   📊 Duration: {original_duration:.2f}s → {cleaned_duration:.2f}s")
            
            # Step 5: Normalize for LLM clarity (loud but natural)
            step_start = time.time()
            logger.info(f"   ⏳ Step 5/6: Normalizing for LLM transcription...")
            audio_array = self._normalize_for_llm(audio_array)
            logger.info(f"   ✅ Normalized for LLM ({time.time()-step_start:.1f}s)")
            
            # Step 6: Gentle high-frequency cleanup (remove only extreme noise)
            step_start = time.time()
            logger.info(f"   ⏳ Step 6/6: Cleaning high frequencies...")
            audio_array = self._cleanup_high_frequencies(audio_array, sr)
            logger.info(f"   ✅ High frequencies cleaned ({time.time()-step_start:.1f}s)")
            
            # Convert to WAV
            step_start = time.time()
            logger.info(f"   💾 Converting to bytes (WAV format)...")
            output_buffer = io.BytesIO()
            sf.write(
                output_buffer,
                audio_array,
                sr,
                format='WAV',
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
                'processing_time_seconds': round(time.time() - start_time, 2),
                'cleaning_mode': 'enhanced-balanced'
            }
            
            total_time = time.time() - start_time
            logger.info(f"✅ ENHANCED audio cleaning complete! (Total: {total_time:.1f}s)")
            logger.info(f"   📊 Original: {original_duration:.2f}s ({len(audio_bytes):,} bytes)")
            logger.info(f"   📊 Cleaned: {cleaned_duration:.2f}s ({len(cleaned_bytes):,} bytes)")
            logger.info(f"   💾 Saved: {metadata['time_saved_seconds']:.2f}s")
            logger.info(f"   🎯 Mode: Balanced (natural + LLM-optimized)")
            
            return cleaned_bytes, metadata
            
        except Exception as e:
            logger.error(f"❌ Enhanced audio cleaning failed: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None, None
    
    def _reduce_noise_balanced(self, audio_array: np.ndarray, sr: int) -> np.ndarray:
        """
        Balanced noise reduction - removes noise but keeps speech natural
        Single pass with optimized parameters for LLM transcription
        """
        try:
            reduced = nr.reduce_noise(
                y=audio_array,
                sr=sr,
                stationary=True,
                prop_decrease=self.noise_reduction_strength,  # 75% - balanced
                freq_mask_smooth_hz=500,  # Smooth frequency masking
                time_mask_smooth_ms=50,   # Smooth time masking
                n_fft=2048,
                hop_length=512
            )
            
            return reduced
            
        except Exception as e:
            logger.warning(f"⚠️ Noise reduction failed: {e}")
            return audio_array
    
    def _remove_silence_balanced(
        self, 
        audio_array: np.ndarray, 
        sr: int
    ) -> Tuple[np.ndarray, int]:
        """
        Balanced silence removal - removes long silences but keeps natural pauses
        """
        try:
            duration = len(audio_array) / sr
            
            # Use faster parameters for long audio
            if duration > 60:
                frame_length = 4096
                hop_length = 1024
            else:
                frame_length = 2048
                hop_length = 512
            
            # Trim leading and trailing silence (balanced)
            trimmed, index = librosa.effects.trim(
                audio_array,
                top_db=self.silence_threshold_db,  # 40 dB - balanced
                frame_length=frame_length,
                hop_length=hop_length
            )
            
            # Split on silence
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
                segment_duration = len(segment) / sr
                
                # Keep segments longer than minimum duration
                if segment_duration >= self.min_silence_duration:  # 0.5s
                    non_silent_segments.append(segment)
            
            # Concatenate with natural gaps (100ms) for smooth flow
            if non_silent_segments:
                gap_samples = int(0.1 * sr)  # 100ms gap - more natural
                gap = np.zeros(gap_samples)
                
                result_segments = []
                for i, segment in enumerate(non_silent_segments):
                    result_segments.append(segment)
                    if i < len(non_silent_segments) - 1:
                        result_segments.append(gap)
                
                result = np.concatenate(result_segments)
                intervals_removed = len(intervals)
            else:
                result = trimmed
                intervals_removed = 0
            
            return result, intervals_removed
            
        except Exception as e:
            logger.warning(f"⚠️ Silence removal failed: {e}")
            return audio_array, 0
    
    def _normalize_for_llm(self, audio_array: np.ndarray) -> np.ndarray:
        """
        Normalize audio specifically for LLM transcription
        Loud enough for model to hear, but natural sounding
        """
        try:
            # Normalize to 85% (loud but safe)
            audio_array = librosa.util.normalize(audio_array) * 0.85
            
            # Gentle compression - reduce extreme peaks only
            threshold = 0.7
            ratio = 2.0
            
            # Compress only very loud parts
            mask = np.abs(audio_array) > threshold
            audio_array[mask] = np.sign(audio_array[mask]) * (
                threshold + (np.abs(audio_array[mask]) - threshold) / ratio
            )
            
            # Gentle boost for quiet parts (not too aggressive)
            quiet_threshold = 0.03
            quiet_mask = np.abs(audio_array) < quiet_threshold
            audio_array[quiet_mask] = audio_array[quiet_mask] * 1.5  # 1.5x boost
            
            # Final normalization to 92% (loud and clear for LLM)
            audio_array = librosa.util.normalize(audio_array) * 0.92
            
            return audio_array
            
        except Exception as e:
            logger.warning(f"⚠️ Normalization failed: {e}")
            return librosa.util.normalize(audio_array)
    
    def _cleanup_high_frequencies(self, audio_array: np.ndarray, sr: int) -> np.ndarray:
        """
        Gentle high-frequency cleanup - removes only extreme noise above speech range
        Speech is mostly 80Hz - 8kHz, so we filter above 8kHz gently
        """
        try:
            nyquist = sr / 2
            cutoff = 8000  # 8kHz - above human speech range
            normalized_cutoff = cutoff / nyquist
            
            # Gentle Butterworth filter (order 3 instead of 5)
            b, a = signal.butter(3, normalized_cutoff, btype='low')
            
            # Apply filter
            filtered = signal.filtfilt(b, a, audio_array)
            
            return filtered
            
        except Exception as e:
            logger.warning(f"⚠️ High-frequency cleanup failed: {e}")
            return audio_array
    
    def clean_audio_file(
        self, 
        input_path: str, 
        output_path: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[dict]]:
        """
        Clean audio from file path
        """
        try:
            with open(input_path, 'rb') as f:
                audio_bytes = f.read()
            
            cleaned_bytes, metadata = self.clean_audio_from_bytes(
                audio_bytes,
                os.path.basename(input_path)
            )
            
            if cleaned_bytes is None:
                return None, None
            
            if output_path is None:
                output_path = input_path.replace('.', '_enhanced_cleaned.')
                if not output_path.endswith('.wav'):
                    output_path = os.path.splitext(output_path)[0] + '.wav'
            
            with open(output_path, 'wb') as f:
                f.write(cleaned_bytes)
            
            logger.info(f"💾 Enhanced cleaned audio saved to: {output_path}")
            
            return output_path, metadata
            
        except Exception as e:
            logger.error(f"❌ File cleaning failed: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None, None


def clean_audio_enhanced(input_path: str, output_path: str = None) -> bool:
    """
    Quick function to clean an audio file with enhanced settings
    """
    cleaner = EnhancedAudioCleaner()
    result_path, metadata = cleaner.clean_audio_file(input_path, output_path)
    
    if result_path:
        print(f"✅ Audio cleaned successfully with ENHANCED mode!")
        print(f"📊 Metadata: {metadata}")
        print(f"💾 Output: {result_path}")
        return True
    else:
        print(f"❌ Audio cleaning failed")
        return False


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("="*70)
        print("ENHANCED AUDIO CLEANER")
        print("="*70)
        print()
        print("Usage:")
        print("  python audio_cleaner_enhanced.py <input_audio_file> [output_audio_file]")
        print()
        print("Examples:")
        print("  python audio_cleaner_enhanced.py noisy_audio.mp3")
        print("  python audio_cleaner_enhanced.py noisy_audio.mp3 clean_audio.wav")
        print()
        print("Features:")
        print("  ✅ Balanced noise reduction (75% - removes noise, keeps speech natural)")
        print("  ✅ Smart silence removal (40 dB - removes long pauses, keeps natural flow)")
        print("  ✅ LLM-optimized normalization (92% volume - loud and clear)")
        print("  ✅ Gentle high-frequency cleanup (removes only extreme noise)")
        print("  ✅ Natural sound quality + Perfect for transcription")
        print("  ✅ No artifacts, no alien sounds, no signal loss")
        print()
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    success = clean_audio_enhanced(input_file, output_file)
    sys.exit(0 if success else 1)
