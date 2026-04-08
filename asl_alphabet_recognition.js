// ═══════════════════════════════════════════════════════════
// ASL Alphabet Recognition System
// Complete A-Z letter recognition for American Sign Language
// ═══════════════════════════════════════════════════════════

/**
 * Recognizes ASL alphabet letters from hand landmarks
 * @param {Array} landmarks - 21 hand landmarks from MediaPipe
 * @param {string} handedness - "Left" or "Right"
 * @returns {Object|null} - {letter, confidence, description}
 */
function recognizeASLLetter(landmarks, handedness) {
  // Landmark indices
  const WRIST = 0;
  const THUMB_CMC = 1, THUMB_MCP = 2, THUMB_IP = 3, THUMB_TIP = 4;
  const INDEX_MCP = 5, INDEX_PIP = 6, INDEX_DIP = 7, INDEX_TIP = 8;
  const MIDDLE_MCP = 9, MIDDLE_PIP = 10, MIDDLE_DIP = 11, MIDDLE_TIP = 12;
  const RING_MCP = 13, RING_PIP = 14, RING_DIP = 15, RING_TIP = 16;
  const PINKY_MCP = 17, PINKY_PIP = 18, PINKY_DIP = 19, PINKY_TIP = 20;

  // Helper functions
  const distance = (p1, p2) => Math.sqrt(
    Math.pow(p1.x - p2.x, 2) + Math.pow(p1.y - p2.y, 2) + Math.pow(p1.z - p2.z, 2)
  );

  const isFingerExtended = (tipIdx, pipIdx) => landmarks[tipIdx].y < landmarks[pipIdx].y - 0.02;
  
  const isFingerCurled = (tipIdx, mcpIdx) => landmarks[tipIdx].y > landmarks[mcpIdx].y;

  const isThumbOut = () => {
    if (handedness === 'Right') {
      return landmarks[THUMB_TIP].x < landmarks[THUMB_MCP].x - 0.05;
    } else {
      return landmarks[THUMB_TIP].x > landmarks[THUMB_MCP].x + 0.05;
    }
  };

  const isThumbUp = () => landmarks[THUMB_TIP].y < landmarks[THUMB_MCP].y - 0.05;

  const areFingersTogether = (tip1, tip2) => distance(landmarks[tip1], landmarks[tip2]) < 0.05;

  // Check finger states
  const indexExtended = isFingerExtended(INDEX_TIP, INDEX_PIP);
  const middleExtended = isFingerExtended(MIDDLE_TIP, MIDDLE_PIP);
  const ringExtended = isFingerExtended(RING_TIP, RING_PIP);
  const pinkyExtended = isFingerExtended(PINKY_TIP, PINKY_PIP);
  
  const indexCurled = isFingerCurled(INDEX_TIP, INDEX_MCP);
  const middleCurled = isFingerCurled(MIDDLE_TIP, MIDDLE_MCP);
  const ringCurled = isFingerCurled(RING_TIP, RING_MCP);
  const pinkyCurled = isFingerCurled(PINKY_TIP, PINKY_MCP);

  const thumbOut = isThumbOut();
  const thumbUp = isThumbUp();

  // Count extended fingers
  const extendedCount = [indexExtended, middleExtended, ringExtended, pinkyExtended].filter(Boolean).length;

  // ═══════════════════════════════════════════════════════════
  // ASL ALPHABET RECOGNITION
  // ═══════════════════════════════════════════════════════════

  // A - Fist with thumb resting on the side of fingers
  // Official: "Make a fist with your thumb resting on the side of your fingers"
  if (!indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const thumbOnSide = thumbOut && Math.abs(landmarks[THUMB_TIP].y - landmarks[INDEX_MCP].y) < 0.08;
    const thumbRestingOnSide = landmarks[THUMB_TIP].x > landmarks[INDEX_MCP].x - 0.04 &&
                               landmarks[THUMB_TIP].x < landmarks[INDEX_MCP].x + 0.04;
    if (thumbOnSide && thumbRestingOnSide) {
      return { letter: 'A', confidence: 0.87, description: 'Fist with thumb on side' };
    }
  }

  // B - All four fingers straight up, thumb folded across palm
  // Official: "Extend all four fingers straight up with your thumb folded across your palm"
  if (indexExtended && middleExtended && ringExtended && pinkyExtended) {
    const fingersTogether = areFingersTogether(INDEX_TIP, MIDDLE_TIP) && 
                           areFingersTogether(MIDDLE_TIP, RING_TIP) &&
                           areFingersTogether(RING_TIP, PINKY_TIP);
    const thumbAcrossPalm = landmarks[THUMB_TIP].y > landmarks[THUMB_MCP].y &&
                           landmarks[THUMB_TIP].x > landmarks[INDEX_MCP].x - 0.04 &&
                           landmarks[THUMB_TIP].x < landmarks[PINKY_MCP].x + 0.04;
    const fingersUp = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.08;
    if (fingersTogether && thumbAcrossPalm && fingersUp) {
      return { letter: 'B', confidence: 0.90, description: 'Four fingers up, thumb across palm' };
    }
  }

  // C - Curve fingers and thumb to form 'C' shape
  // Official: "Curve your fingers and thumb to form the shape of the letter 'C'"
  if (indexCurled && middleCurled && ringCurled && pinkyCurled) {
    const cShape = distance(landmarks[INDEX_TIP], landmarks[THUMB_TIP]) > 0.08 &&
                   distance(landmarks[INDEX_TIP], landmarks[THUMB_TIP]) < 0.16;
    const curvedFingers = landmarks[INDEX_TIP].y > landmarks[INDEX_PIP].y - 0.02;
    const thumbCurved = landmarks[THUMB_TIP].y > landmarks[THUMB_IP].y - 0.02;
    if (cShape && curvedFingers && thumbCurved) {
      return { letter: 'C', confidence: 0.85, description: 'Curved C shape' };
    }
  }

  // D - Index finger straight up, other fingers curl to touch thumb
  // Official: "Extend your index finger straight up while curling the remaining fingers to touch your thumb"
  if (indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const indexStraightUp = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.08;
    const thumbTouchesMiddle = distance(landmarks[THUMB_TIP], landmarks[MIDDLE_TIP]) < 0.05;
    const otherFingersCurled = middleCurled && ringCurled && pinkyCurled;
    if (indexStraightUp && thumbTouchesMiddle && otherFingersCurled) {
      return { letter: 'D', confidence: 0.89, description: 'Index up, fingers touch thumb' };
    }
  }

  // E - Curl all fingers towards palm, touching thumb
  // Official: "Curl all your fingers towards your palm, touching your thumb"
  if (indexCurled && middleCurled && ringCurled && pinkyCurled) {
    const fingersCurledTight = landmarks[INDEX_TIP].y > landmarks[INDEX_MCP].y &&
                               landmarks[MIDDLE_TIP].y > landmarks[MIDDLE_MCP].y;
    const thumbTouchingFingers = distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) < 0.06;
    if (fingersCurledTight && thumbTouchingFingers) {
      return { letter: 'E', confidence: 0.83, description: 'Fingers curled touching thumb' };
    }
  }

  // F - Index tip touches thumb tip (circle), other three fingers extended
  // Official: "Touch the tip of your index finger to your thumb, forming a small circle, while the other three fingers are extended"
  if (middleExtended && ringExtended && pinkyExtended) {
    const circleFormed = distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) < 0.04;
    const threeUp = landmarks[MIDDLE_TIP].y < landmarks[MIDDLE_MCP].y - 0.05 &&
                    landmarks[RING_TIP].y < landmarks[RING_MCP].y - 0.05 &&
                    landmarks[PINKY_TIP].y < landmarks[PINKY_MCP].y - 0.05;
    if (circleFormed && threeUp) {
      return { letter: 'F', confidence: 0.91, description: 'Circle with three fingers up' };
    }
  }

  // G - Index finger and thumb extended, parallel, other fingers curled
  // Official: "Extend your index finger and thumb, keeping them parallel, while the other fingers stay curled"
  if (indexExtended && !middleExtended && !ringExtended && !pinkyExtended && thumbOut) {
    const parallel = Math.abs(landmarks[INDEX_TIP].y - landmarks[THUMB_TIP].y) < 0.06;
    const horizontal = landmarks[INDEX_TIP].x < landmarks[INDEX_MCP].x - 0.05 ||
                      landmarks[INDEX_TIP].x > landmarks[INDEX_MCP].x + 0.05;
    const othersCurled = middleCurled && ringCurled && pinkyCurled;
    if (parallel && horizontal && othersCurled) {
      return { letter: 'G', confidence: 0.85, description: 'Index and thumb parallel sideways' };
    }
  }

  // H - Index and middle fingers extended together, rest curled
  // Official: "Extend both your index and middle fingers together while keeping the rest curled"
  if (indexExtended && middleExtended && !ringExtended && !pinkyExtended) {
    const together = areFingersTogether(INDEX_TIP, MIDDLE_TIP);
    const horizontal = Math.abs(landmarks[INDEX_TIP].x - landmarks[INDEX_MCP].x) > 0.05;
    const othersCurled = ringCurled && pinkyCurled;
    if (together && horizontal && othersCurled) {
      return { letter: 'H', confidence: 0.87, description: 'Two fingers sideways together' };
    }
  }

  // I - Pinky finger extended, rest curled in fist
  // Official: "Extend your pinky finger while the rest of your fingers stay curled in a fist"
  if (!indexExtended && !middleExtended && !ringExtended && pinkyExtended) {
    const pinkyUp = landmarks[PINKY_TIP].y < landmarks[PINKY_MCP].y - 0.05;
    const othersCurled = indexCurled && middleCurled && ringCurled;
    if (pinkyUp && othersCurled) {
      return { letter: 'I', confidence: 0.93, description: 'Pinky up only' };
    }
  }

  // K - Index and middle fingers apart like 'V', thumb in between
  // Official: "Extend your index and middle fingers apart like a 'V' while placing your thumb in between"
  if (indexExtended && middleExtended && !ringExtended && !pinkyExtended) {
    const vShape = distance(landmarks[INDEX_TIP], landmarks[MIDDLE_TIP]) > 0.05;
    const thumbBetween = landmarks[THUMB_TIP].y < landmarks[INDEX_MCP].y &&
                        landmarks[THUMB_TIP].x > landmarks[INDEX_TIP].x - 0.03 &&
                        landmarks[THUMB_TIP].x < landmarks[MIDDLE_TIP].x + 0.03;
    const indexHigher = landmarks[INDEX_TIP].y < landmarks[MIDDLE_TIP].y - 0.02;
    if (vShape && thumbBetween && indexHigher) {
      return { letter: 'K', confidence: 0.84, description: 'V shape with thumb between' };
    }
  }

  // L - Index finger and thumb at right angle forming 'L'
  // Official: "Extend your index finger and thumb at a right angle to form the letter 'L'"
  if (indexExtended && !middleExtended && !ringExtended && !pinkyExtended && thumbOut) {
    const indexUp = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.08;
    const thumbOut = landmarks[THUMB_TIP].x < landmarks[THUMB_MCP].x - 0.05 ||
                     landmarks[THUMB_TIP].x > landmarks[THUMB_MCP].x + 0.05;
    const rightAngle = Math.abs(landmarks[INDEX_TIP].y - landmarks[THUMB_TIP].y) > 0.08;
    const othersCurled = middleCurled && ringCurled && pinkyCurled;
    if (indexUp && thumbOut && rightAngle && othersCurled) {
      return { letter: 'L', confidence: 0.92, description: 'L shape (right angle)' };
    }
  }

  // M - Curl fingers over thumb, covering it with three fingers
  // Official: "Curl your fingers over your thumb, covering it with three fingers"
  if (!indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const allCurled = indexCurled && middleCurled && ringCurled && pinkyCurled;
    const thumbCovered = landmarks[THUMB_TIP].y > landmarks[INDEX_MCP].y - 0.02 &&
                        landmarks[THUMB_TIP].y < landmarks[RING_MCP].y + 0.02;
    const threeFingers = landmarks[INDEX_TIP].y > landmarks[THUMB_TIP].y - 0.03 &&
                        landmarks[MIDDLE_TIP].y > landmarks[THUMB_TIP].y - 0.03 &&
                        landmarks[RING_TIP].y > landmarks[THUMB_TIP].y - 0.03;
    if (allCurled && thumbCovered && threeFingers) {
      return { letter: 'M', confidence: 0.80, description: 'Three fingers over thumb' };
    }
  }

  // N - Similar to M, but cover thumb with only two fingers
  // Official: "Similar to 'M', but cover your thumb with only two fingers"
  if (!indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const allCurled = indexCurled && middleCurled && ringCurled && pinkyCurled;
    const thumbCovered = landmarks[THUMB_TIP].y > landmarks[INDEX_MCP].y - 0.02 &&
                        landmarks[THUMB_TIP].y < landmarks[MIDDLE_MCP].y + 0.02;
    const twoFingers = landmarks[INDEX_TIP].y > landmarks[THUMB_TIP].y - 0.03 &&
                      landmarks[MIDDLE_TIP].y > landmarks[THUMB_TIP].y - 0.03;
    const ringNotCovering = landmarks[RING_TIP].y < landmarks[THUMB_TIP].y + 0.02;
    if (allCurled && thumbCovered && twoFingers && ringNotCovering) {
      return { letter: 'N', confidence: 0.78, description: 'Two fingers over thumb' };
    }
  }

  // O - Form 'O' shape with all fingers and thumb touching
  // Official: "Form an 'O' shape with all your fingers and thumb touching"
  if (indexCurled && middleCurled && ringCurled && pinkyCurled) {
    const thumbIndexTouch = distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) < 0.04;
    const allFingersTogether = distance(landmarks[INDEX_TIP], landmarks[MIDDLE_TIP]) < 0.04 &&
                              distance(landmarks[MIDDLE_TIP], landmarks[RING_TIP]) < 0.04 &&
                              distance(landmarks[RING_TIP], landmarks[PINKY_TIP]) < 0.04;
    const circleShape = landmarks[INDEX_TIP].y > landmarks[INDEX_PIP].y - 0.03;
    if (thumbIndexTouch && allFingersTogether && circleShape) {
      return { letter: 'O', confidence: 0.90, description: 'O shape (all fingers touch)' };
    }
  }

  // P - Make 'K' sign and tilt downward
  // Official: "Make the 'K' sign and tilt it downward"
  if (indexExtended && middleExtended && !ringExtended && !pinkyExtended) {
    const vShape = distance(landmarks[INDEX_TIP], landmarks[MIDDLE_TIP]) > 0.04;
    const pointingDown = landmarks[INDEX_TIP].y > landmarks[WRIST].y - 0.02;
    const thumbBetween = landmarks[THUMB_TIP].y > landmarks[INDEX_MCP].y - 0.03;
    if (vShape && pointingDown && thumbBetween) {
      return { letter: 'P', confidence: 0.81, description: 'K tilted down' };
    }
  }

  // Q - Make 'G' sign and tilt downward
  // Official: "Make the 'G' sign and tilt it downward"
  if (indexExtended && !middleExtended && !ringExtended && !pinkyExtended && thumbOut) {
    const parallel = Math.abs(landmarks[INDEX_TIP].y - landmarks[THUMB_TIP].y) < 0.06;
    const pointingDown = landmarks[INDEX_TIP].y > landmarks[WRIST].y - 0.02;
    const othersCurled = middleCurled && ringCurled && pinkyCurled;
    if (parallel && pointingDown && othersCurled) {
      return { letter: 'Q', confidence: 0.79, description: 'G tilted down' };
    }
  }

  // R - Cross index and middle fingers, rest curled
  // Official: "Cross your index and middle fingers while keeping the rest curled"
  if (indexExtended && middleExtended && !ringExtended && !pinkyExtended) {
    const crossed = Math.abs(landmarks[INDEX_TIP].x - landmarks[MIDDLE_TIP].x) < 0.03 &&
                   Math.abs(landmarks[INDEX_DIP].x - landmarks[MIDDLE_DIP].x) < 0.04;
    const bothUp = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.05 &&
                  landmarks[MIDDLE_TIP].y < landmarks[MIDDLE_MCP].y - 0.05;
    const othersCurled = ringCurled && pinkyCurled;
    if (crossed && bothUp && othersCurled) {
      return { letter: 'R', confidence: 0.86, description: 'Crossed fingers' };
    }
  }

  // S - Fist with thumb resting across the front of fingers
  // Official: "Make a fist with your thumb resting across the front of your fingers"
  if (!indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const allCurled = indexCurled && middleCurled && ringCurled && pinkyCurled;
    const thumbAcrossFront = landmarks[THUMB_TIP].y < landmarks[INDEX_MCP].y + 0.02 &&
                            landmarks[THUMB_TIP].y > landmarks[INDEX_MCP].y - 0.05 &&
                            landmarks[THUMB_TIP].x > landmarks[INDEX_MCP].x - 0.02;
    if (allCurled && thumbAcrossFront) {
      return { letter: 'S', confidence: 0.88, description: 'Fist, thumb across front' };
    }
  }

  // T - Tuck thumb between index and middle fingers
  // Official: "Tuck your thumb between your index and middle fingers"
  if (!indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const allCurled = indexCurled && middleCurled && ringCurled && pinkyCurled;
    const thumbTucked = landmarks[THUMB_TIP].y > landmarks[INDEX_MCP].y - 0.03 &&
                       landmarks[THUMB_TIP].y < landmarks[MIDDLE_MCP].y + 0.03 &&
                       landmarks[THUMB_TIP].x > landmarks[INDEX_MCP].x - 0.02 &&
                       landmarks[THUMB_TIP].x < landmarks[MIDDLE_MCP].x + 0.02;
    if (allCurled && thumbTucked) {
      return { letter: 'T', confidence: 0.84, description: 'Thumb tucked between fingers' };
    }
  }

  // U - Index and middle fingers extended together, rest curled
  // Official: "Extend your index and middle fingers together while keeping the rest curled"
  if (indexExtended && middleExtended && !ringExtended && !pinkyExtended) {
    const together = areFingersTogether(INDEX_TIP, MIDDLE_TIP);
    const upright = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.06 &&
                   landmarks[MIDDLE_TIP].y < landmarks[MIDDLE_MCP].y - 0.06;
    const vertical = Math.abs(landmarks[INDEX_TIP].x - landmarks[INDEX_MCP].x) < 0.04;
    const othersCurled = ringCurled && pinkyCurled;
    if (together && upright && vertical && othersCurled) {
      return { letter: 'U', confidence: 0.91, description: 'Two fingers up together' };
    }
  }

  // V - Index and middle fingers apart in 'V' shape
  // Official: "Extend your index and middle fingers apart in a 'V' shape"
  if (indexExtended && middleExtended && !ringExtended && !pinkyExtended) {
    const apart = distance(landmarks[INDEX_TIP], landmarks[MIDDLE_TIP]) > 0.06;
    const bothUp = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.05 &&
                  landmarks[MIDDLE_TIP].y < landmarks[MIDDLE_MCP].y - 0.05;
    const othersCurled = ringCurled && pinkyCurled;
    if (apart && bothUp && othersCurled) {
      return { letter: 'V', confidence: 0.93, description: 'V shape (fingers apart)' };
    }
  }

  // W - Index, middle, and ring fingers extended apart, forming 'W'
  // Official: "Extend your index, middle, and ring fingers apart, forming a 'W'"
  if (indexExtended && middleExtended && ringExtended && !pinkyExtended) {
    const spread = distance(landmarks[INDEX_TIP], landmarks[MIDDLE_TIP]) > 0.04 &&
                  distance(landmarks[MIDDLE_TIP], landmarks[RING_TIP]) > 0.04;
    const allUp = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.05 &&
                 landmarks[MIDDLE_TIP].y < landmarks[MIDDLE_MCP].y - 0.05 &&
                 landmarks[RING_TIP].y < landmarks[RING_MCP].y - 0.05;
    const pinkyCurled = landmarks[PINKY_TIP].y > landmarks[PINKY_MCP].y;
    if (spread && allUp && pinkyCurled) {
      return { letter: 'W', confidence: 0.89, description: 'Three fingers apart (W)' };
    }
  }

  // X - Curl index finger into hooked shape, rest curled
  // Official: "Curl your index finger into a hooked shape while keeping the rest curled"
  if (!middleExtended && !ringExtended && !pinkyExtended) {
    const indexBent = landmarks[INDEX_TIP].y > landmarks[INDEX_PIP].y &&
                     landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y;
    const indexHooked = landmarks[INDEX_DIP].y < landmarks[INDEX_PIP].y;
    const othersCurled = middleCurled && ringCurled && pinkyCurled;
    if (indexBent && indexHooked && othersCurled) {
      return { letter: 'X', confidence: 0.82, description: 'Index hooked' };
    }
  }

  // Y - Thumb and pinky extended out, middle fingers folded
  // Official: "Extend your thumb and pinky out while keeping the middle fingers folded"
  if (!indexExtended && !middleExtended && !ringExtended && pinkyExtended && thumbOut) {
    const pinkyOut = landmarks[PINKY_TIP].y < landmarks[PINKY_MCP].y - 0.05;
    const thumbOut = landmarks[THUMB_TIP].x < landmarks[THUMB_MCP].x - 0.05 ||
                     landmarks[THUMB_TIP].x > landmarks[THUMB_MCP].x + 0.05;
    const middleFolded = indexCurled && middleCurled && ringCurled;
    if (pinkyOut && thumbOut && middleFolded) {
      return { letter: 'Y', confidence: 0.92, description: 'Thumb and pinky out (Y)' };
    }
  }

  // Z - Use index finger to trace 'Z' shape in air (static: pointing)
  // Official: "Use your index finger to trace a 'Z' shape in the air"
  // Note: Motion-based, using static pointing approximation
  if (indexExtended && !middleExtended && !ringExtended && !pinkyExtended) {
    const pointing = landmarks[INDEX_TIP].y < landmarks[INDEX_MCP].y - 0.08;
    const othersCurled = middleCurled && ringCurled && pinkyCurled;
    // Z requires motion, so we use lower confidence for static detection
    if (pointing && othersCurled) {
      return { letter: 'Z', confidence: 0.72, description: 'Index pointing (Z motion needed)' };
    }
  }

  return null;
}

/**
 * Recognizes ASL numbers 0-10
 */
function recognizeASLNumber(landmarks, handedness) {
  // Similar structure to letter recognition
  // Implementation for numbers 0-10
  // ... (to be implemented)
  return null;
}

// Export functions
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { recognizeASLLetter, recognizeASLNumber };
}
