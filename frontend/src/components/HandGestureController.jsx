import { useEffect, useRef, useCallback } from 'react'
import { GestureRecognizer, FilesetResolver } from '@mediapipe/tasks-vision'

const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'
const TARGET_FPS = 30

// ── Landmark indices (MediaPipe Hand) ──
const WRIST = 0
const THUMB_CMC = 1, THUMB_MCP = 2, THUMB_IP = 3, THUMB_TIP = 4
const INDEX_MCP = 5, INDEX_PIP = 6, INDEX_DIP = 7, INDEX_TIP = 8
const MIDDLE_MCP = 9, MIDDLE_PIP = 10, MIDDLE_DIP = 11, MIDDLE_TIP = 12
const RING_MCP = 13, RING_PIP = 14, RING_DIP = 15, RING_TIP = 16
const PINKY_MCP = 17, PINKY_PIP = 18, PINKY_DIP = 19, PINKY_TIP = 20

// ── Finger-state detection from raw landmarks ──
// Returns { thumb, index, middle, ring, pinky } each true = extended
function getFingerStates(lm) {
  // For 4-segment fingers: extended if TIP is further from WRIST than PIP
  // (in y-axis, since camera faces palm — lower y = higher in screen)
  const isExtended = (tip, pip, mcp) => {
    // Primary: tip is further from wrist than pip along the finger axis
    const tipToWrist = dist3(lm[tip], lm[WRIST])
    const pipToWrist = dist3(lm[pip], lm[WRIST])
    // Secondary: tip-to-mcp should be longer than pip-to-mcp when extended
    const tipToMcp = dist3(lm[tip], lm[mcp])
    const pipToMcp = dist3(lm[pip], lm[mcp])
    return tipToWrist > pipToWrist && tipToMcp > pipToMcp * 0.8
  }

  // Thumb: compare tip distance from pinky-mcp vs thumb-mcp distance from pinky-mcp
  // (thumb sticks out laterally, not vertically)
  const thumbExtended = (() => {
    const tipDist = dist3(lm[THUMB_TIP], lm[PINKY_MCP])
    const mcpDist = dist3(lm[THUMB_MCP], lm[PINKY_MCP])
    return tipDist > mcpDist * 1.1
  })()

  return {
    thumb: thumbExtended,
    index: isExtended(INDEX_TIP, INDEX_PIP, INDEX_MCP),
    middle: isExtended(MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP),
    ring: isExtended(RING_TIP, RING_PIP, RING_MCP),
    pinky: isExtended(PINKY_TIP, PINKY_PIP, PINKY_MCP),
  }
}

function dist3(a, b) {
  const dx = a.x - b.x, dy = a.y - b.y, dz = (a.z || 0) - (b.z || 0)
  return Math.sqrt(dx * dx + dy * dy + dz * dz)
}

function dist2(a, b) {
  const dx = a.x - b.x, dy = a.y - b.y
  return Math.sqrt(dx * dx + dy * dy)
}

// ── Hybrid gesture detection ──
// Uses classifier result as primary, falls back to landmark analysis
function detectGesture(classifierGesture, landmarks) {
  // If classifier is confident, trust it
  if (classifierGesture && classifierGesture !== 'None') {
    // Map classifier names to our canonical gestures
    if (classifierGesture === 'Open_Palm') return 'ROTATE'
    if (classifierGesture === 'Closed_Fist') return 'FIST'
    if (classifierGesture === 'Pointing_Up') return 'POINT'
    if (classifierGesture === 'Victory') return 'POINT'
    if (classifierGesture === 'ILoveYou') return 'ROTATE' // open-ish hand
    if (classifierGesture === 'Thumb_Up' || classifierGesture === 'Thumb_Down') return 'IDLE'
  }

  // Fallback: analyze finger states from landmarks
  const fingers = getFingerStates(landmarks)
  const extendedCount = [fingers.thumb, fingers.index, fingers.middle, fingers.ring, fingers.pinky].filter(Boolean).length

  // Check pinch first (thumb tip close to index tip)
  const pinchDist = dist2(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
  if (pinchDist < 0.06 && !fingers.middle && !fingers.ring) {
    return 'PINCH'
  }
  if (pinchDist < 0.08 && extendedCount <= 2) {
    return 'PINCH'
  }

  // Fist: no fingers extended (or only thumb)
  if (extendedCount <= 1 && !fingers.index && !fingers.middle) {
    return 'FIST'
  }

  // Point: only index (or index+middle) extended
  if (fingers.index && !fingers.ring && !fingers.pinky) {
    if (!fingers.middle) return 'POINT' // just index
    if (fingers.middle) return 'POINT'  // index + middle (peace sign pointer)
  }

  // Open hand: 3+ fingers extended → rotate mode
  if (extendedCount >= 3) {
    return 'ROTATE'
  }

  // Partial open (2 fingers but not point pattern) → still allow rotate
  if (extendedCount >= 2) {
    return 'ROTATE'
  }

  return 'IDLE'
}

// ── EMA smoothing ──
function emaSmooth(prev, curr, alpha = 0.45) {
  return {
    x: prev.x + alpha * (curr.x - prev.x),
    y: prev.y + alpha * (curr.y - prev.y),
    z: prev.z + alpha * ((curr.z || 0) - (prev.z || 0)),
  }
}

export default function HandGestureController({
  isActive,
  rotationAccRef,  // useRef({x:0, y:0}) — accumulator, drained by Scene
  zoomAccRef,      // useRef(0) — accumulator, drained by Scene
  onPointerMove,
  onSelect,
  onReset,
  onFpsUpdate,
  // Legacy props (kept for backward compat, used if accRefs not provided)
  onRotate,
  onZoom,
}) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const recognizerRef = useRef(null)
  const requestRef = useRef(null)
  const lastVideoTimeRef = useRef(-1)

  const smoothPosRef = useRef({ x: 0.5, y: 0.5, z: 0 })
  const lastGestureRef = useRef('IDLE')
  const dwellStartTimeRef = useRef(0)
  const lastProcessTimeRef = useRef(null)
  const fpsSamplesRef = useRef([])
  const prevPinchDistRef = useRef(0.05)
  const gestureStabilityRef = useRef({ name: 'IDLE', count: 0 })
  const hasFirstFrameRef = useRef(false)

  useEffect(() => {
    lastProcessTimeRef.current = performance.now()
  }, [])

  // Stable callback refs to avoid stale closures in RAF loop
  const callbacksRef = useRef({ onRotate, onZoom, onPointerMove, onSelect, onReset, onFpsUpdate })
  useEffect(() => {
    callbacksRef.current = { onRotate, onZoom, onPointerMove, onSelect, onReset, onFpsUpdate }
  }, [onRotate, onZoom, onPointerMove, onSelect, onReset, onFpsUpdate])

  const isActiveRef = useRef(isActive)
  useEffect(() => { isActiveRef.current = isActive }, [isActive])

  // Keep acc refs in a ref so RAF loop always sees latest
  const accRefsRef = useRef({ rotationAccRef, zoomAccRef })
  useEffect(() => { accRefsRef.current = { rotationAccRef, zoomAccRef } }, [rotationAccRef, zoomAccRef])

  // Init MediaPipe once
  useEffect(() => {
    let cancelled = false
    let recognizer = null

    const initMediaPipe = async () => {
      try {
        const vision = await FilesetResolver.forVisionTasks(
          'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm'
        )
        if (cancelled) return

        recognizer = await GestureRecognizer.createFromOptions(vision, {
          baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
          runningMode: 'VIDEO',
          numHands: 1
        })
        recognizerRef.current = recognizer
      } catch (err) {
        console.error('[HandGesture] Failed to initialize MediaPipe:', err)
        // Fallback to CPU delegate
        try {
          const vision2 = await FilesetResolver.forVisionTasks(
            'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm'
          )
          if (cancelled) return
          recognizer = await GestureRecognizer.createFromOptions(vision2, {
            baseOptions: { modelAssetPath: MODEL_URL, delegate: 'CPU' },
            runningMode: 'VIDEO',
            numHands: 1
          })
          recognizerRef.current = recognizer
        } catch (err2) {
          console.error('[HandGesture] CPU fallback also failed:', err2)
        }
      }
    }

    initMediaPipe()
    return () => {
      cancelled = true
      if (recognizer) { try { recognizer.close() } catch {} }
      recognizerRef.current = null
    }
  }, [])

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
    if (videoRef.current) videoRef.current.srcObject = null
    if (requestRef.current) {
      cancelAnimationFrame(requestRef.current)
      requestRef.current = null
    }
    lastGestureRef.current = 'IDLE'
    dwellStartTimeRef.current = 0
    lastVideoTimeRef.current = -1
    hasFirstFrameRef.current = false
  }, [])

  // Camera lifecycle
  useEffect(() => {
    if (!isActive) {
      stopCamera()
      if (callbacksRef.current.onPointerMove) callbacksRef.current.onPointerMove(null, null)
      return
    }

    let cancelled = false
    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, frameRate: { ideal: TARGET_FPS } }
        })
        if (cancelled) {
          stream.getTracks().forEach(t => t.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          try { await videoRef.current.play() } catch (e) { console.warn('[HandGesture] video play failed:', e) }
        }
      } catch (err) {
        console.error('[HandGesture] Camera access denied or failed:', err)
        if (callbacksRef.current.onFpsUpdate) callbacksRef.current.onFpsUpdate(0)
      }
    }

    startCamera()
    return () => {
      cancelled = true
      stopCamera()
    }
  }, [isActive, stopCamera])

  // ── Push helpers: write to accumulator refs if available, else call legacy callbacks ──
  const pushRotation = useCallback((dx, dy) => {
    const { rotationAccRef: rRef } = accRefsRef.current
    if (rRef && rRef.current) {
      rRef.current.x += dx
      rRef.current.y += dy
    } else {
      callbacksRef.current.onRotate?.(dx, dy)
    }
  }, [])

  const pushZoom = useCallback((delta) => {
    const { zoomAccRef: zRef } = accRefsRef.current
    if (zRef && zRef.current !== undefined) {
      zRef.current += delta
    } else {
      callbacksRef.current.onZoom?.(delta)
    }
  }, [])

  const handleGesture = useCallback((classifierGesture, landmarks, timeNow) => {
    const rawGesture = detectGesture(classifierGesture, landmarks)

    // Stabilize: require 2 consecutive same-gesture frames to switch
    if (rawGesture === gestureStabilityRef.current.name) {
      gestureStabilityRef.current.count++
    } else {
      gestureStabilityRef.current = { name: rawGesture, count: 1 }
    }
    const gesture = gestureStabilityRef.current.count >= 2
      ? rawGesture
      : lastGestureRef.current // keep previous until stabilized

    // Smooth the index tip position (primary tracking point)
    const rawPos = { x: 1 - landmarks[INDEX_TIP].x, y: landmarks[INDEX_TIP].y, z: landmarks[INDEX_TIP].z || 0 }

    if (!hasFirstFrameRef.current) {
      smoothPosRef.current = rawPos
      hasFirstFrameRef.current = true
      lastGestureRef.current = gesture
      return
    }

    const smoothed = emaSmooth(smoothPosRef.current, rawPos, 0.5)
    smoothPosRef.current = smoothed

    const cbs = callbacksRef.current

    // ── ROTATE: open palm / spread fingers — orbit the scene ──
    if (gesture === 'ROTATE') {
      if (lastGestureRef.current === 'ROTATE' || lastGestureRef.current === 'IDLE') {
        // Use palm center (landmark 9) for smoother rotation tracking
        const palmRaw = { x: 1 - landmarks[9].x, y: landmarks[9].y }
        const prevPalm = smoothPosRef.current
        const deltaX = (palmRaw.x - prevPalm.x) * 280
        const deltaY = (palmRaw.y - prevPalm.y) * 280

        // Deadzone to prevent micro-jitter
        if (Math.abs(deltaX) > 0.15 || Math.abs(deltaY) > 0.15) {
          pushRotation(
            Math.max(-8, Math.min(8, deltaX)),
            Math.max(-8, Math.min(8, deltaY))
          )
        }
      }

      // Palm depth zoom (z-axis: moving hand closer = zoom in)
      const palmZ = landmarks[9].z || 0
      const prevZ = smoothPosRef.current.z ?? palmZ
      const zDelta = (palmZ - prevZ) * -160
      if (Math.abs(zDelta) > 0.3) {
        pushZoom(Math.max(-5, Math.min(5, zDelta)))
      }
      smoothPosRef.current.z = palmZ

      // Clear pointer when rotating
      if (lastGestureRef.current === 'POINT') {
        cbs.onPointerMove?.(null, null)
      }
      dwellStartTimeRef.current = 0
      prevPinchDistRef.current = dist2(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
    }

    // ── PINCH: thumb + index close — zoom control ──
    else if (gesture === 'PINCH') {
      const pinchDist = dist2(landmarks[THUMB_TIP], landmarks[INDEX_TIP])

      if (lastGestureRef.current === 'PINCH') {
        // Combine vertical movement + pinch distance change
        const vertical = (smoothPosRef.current.y - smoothed.y) * 120
        const pinchDelta = (prevPinchDistRef.current - pinchDist) * 380
        const zoomDelta = vertical + pinchDelta

        if (Math.abs(zoomDelta) > 0.2) {
          pushZoom(Math.max(-6, Math.min(6, zoomDelta)))
        }
      }

      prevPinchDistRef.current = pinchDist

      // Clear pointer
      if (lastGestureRef.current === 'POINT') {
        cbs.onPointerMove?.(null, null)
      }
      dwellStartTimeRef.current = 0
    }

    // ── POINT: index finger (or index+middle) — cursor + dwell-select ──
    else if (gesture === 'POINT') {
      const pointerX = (smoothed.x * 2) - 1
      const pointerY = -(smoothed.y * 2) + 1

      cbs.onPointerMove?.(pointerX, pointerY)

      if (lastGestureRef.current !== 'POINT') {
        dwellStartTimeRef.current = timeNow
      } else {
        const dwellTime = timeNow - dwellStartTimeRef.current
        if (dwellTime > 1100) {
          cbs.onSelect?.(pointerX, pointerY)
          dwellStartTimeRef.current = timeNow // prevent rapid re-trigger
        }
      }
    }

    // ── FIST: closed hand — hold to reset view ──
    else if (gesture === 'FIST') {
      if (lastGestureRef.current !== 'FIST') {
        dwellStartTimeRef.current = timeNow
      } else if (timeNow - dwellStartTimeRef.current > 700) {
        cbs.onReset?.()
        dwellStartTimeRef.current = timeNow
      }

      // Clear pointer
      if (lastGestureRef.current === 'POINT') {
        cbs.onPointerMove?.(null, null)
      }
    }

    // ── IDLE: no recognizable gesture ──
    else {
      if (lastGestureRef.current === 'POINT') {
        cbs.onPointerMove?.(null, null)
      }
      dwellStartTimeRef.current = 0
    }

    // Update smoothed position for next frame's delta calculation
    smoothPosRef.current = smoothed
    lastGestureRef.current = gesture
  }, [pushRotation, pushZoom])

  // RAF loop — uses refs so it always sees latest isActive / callbacks
  useEffect(() => {
    if (!isActive) return

    const msPerFrame = 1000 / TARGET_FPS

    const processFrame = () => {
      if (!isActiveRef.current || !videoRef.current || !recognizerRef.current) {
        requestRef.current = requestAnimationFrame(processFrame)
        return
      }

      const now = performance.now()
      if (lastProcessTimeRef.current === null) lastProcessTimeRef.current = now
      const dt = now - lastProcessTimeRef.current

      if (dt >= msPerFrame && videoRef.current.readyState >= 2) {
        if (videoRef.current.currentTime !== lastVideoTimeRef.current) {
          lastVideoTimeRef.current = videoRef.current.currentTime
          try {
            const results = recognizerRef.current.recognizeForVideo(videoRef.current, now)

            if (results.landmarks && results.landmarks.length > 0) {
              const landmarks = results.landmarks[0]
              const classifierGesture = results.gestures.length > 0
                ? results.gestures[0][0].categoryName
                : 'None'
              handleGesture(classifierGesture, landmarks, now)
            } else {
              // No hand detected at all
              if (lastGestureRef.current === 'POINT') {
                callbacksRef.current.onPointerMove?.(null, null)
              }
              lastGestureRef.current = 'IDLE'
              dwellStartTimeRef.current = 0
              hasFirstFrameRef.current = false
            }
          } catch (e) {
            console.error('[HandGesture] recognize error', e)
          }
        }

        // Smoothed FPS (rolling average over last 10 frames)
        fpsSamplesRef.current.push(1000 / dt)
        if (fpsSamplesRef.current.length > 10) fpsSamplesRef.current.shift()
        const avgFps = fpsSamplesRef.current.reduce((a, b) => a + b, 0) / fpsSamplesRef.current.length
        callbacksRef.current.onFpsUpdate?.(Math.round(avgFps))

        lastProcessTimeRef.current = now
      }

      requestRef.current = requestAnimationFrame(processFrame)
    }

    requestRef.current = requestAnimationFrame(processFrame)
    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current)
      requestRef.current = null
    }
  }, [isActive, handleGesture])

  return (
    <div
      className={`absolute bottom-6 right-6 w-48 h-36 bg-black border border-[var(--accent)] rounded-lg overflow-hidden shadow-[0_0_15px_rgba(0,255,102,0.3)] z-50 pointer-events-none transition-opacity duration-300 ${isActive ? 'opacity-100' : 'opacity-0 hidden'}`}
    >
      <div className="absolute top-0 left-0 w-full bg-black/60 text-white text-[10px] p-1 text-center font-mono">
        GESTURE TRACKING
      </div>
      <video
        ref={videoRef}
        playsInline
        autoPlay
        muted
        className="w-full h-full object-cover"
        style={{ transform: 'scaleX(-1)' }}
      />
    </div>
  )
}
