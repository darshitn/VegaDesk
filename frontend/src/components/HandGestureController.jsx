import { useEffect, useRef, useCallback } from 'react'
import { HandLandmarker, FilesetResolver } from '@mediapipe/tasks-vision'

// ── Model & Performance ──
const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task'
const WASM_CDN  = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm'
const TARGET_FPS = 30

// ── MediaPipe Hand Landmark Indices ──
const WRIST      = 0
const THUMB_MCP  = 2
const THUMB_TIP  = 4
const INDEX_MCP  = 5
const INDEX_PIP  = 6
const INDEX_TIP  = 8
const MIDDLE_MCP = 9
const MIDDLE_PIP = 10
const MIDDLE_TIP = 12
const RING_MCP   = 13
const RING_PIP   = 14
const RING_TIP   = 16
const PINKY_MCP  = 17
const PINKY_PIP  = 18
const PINKY_TIP  = 20

// ── Gesture States ──
const STATE = {
  IDLE:       'IDLE',
  ROTATE:     'ROTATE',
  POINT:      'POINT',
  PINCH:      'PINCH',
  PINCH_DRAG: 'PINCH_DRAG',
  FIST:       'FIST',
}

// ── Hysteresis thresholds (normalized to hand size) ──
const PINCH_ENTER_THRESHOLD = 0.28  // relative to hand reference length
const PINCH_EXIT_THRESHOLD  = 0.40  // higher than enter → prevents flicker
const PINCH_CLICK_MAX_TIME  = 350   // ms — quick pinch+release = click
const PINCH_CLICK_MAX_DIST  = 0.04  // normalized — max movement during pinch to count as click
const FIST_HOLD_TIME        = 700   // ms to trigger reset

// ═══════════════════════════════════════════════════════════════════════════
// One Euro Filter — adaptive low-pass filter for smooth yet responsive tracking
// Ref: https://cristal.univ-lille.fr/~casiez/1euro/
// ═══════════════════════════════════════════════════════════════════════════
class OneEuroFilter {
  constructor(minCutoff = 1.0, beta = 0.007, dCutoff = 1.0) {
    this.minCutoff = minCutoff
    this.beta = beta
    this.dCutoff = dCutoff
    this.xPrev = null
    this.dxPrev = 0
    this.tPrev = null
  }

  _alpha(cutoff, dt) {
    const tau = 1.0 / (2 * Math.PI * cutoff)
    return 1.0 / (1.0 + tau / dt)
  }

  filter(x, timestamp) {
    if (this.tPrev === null) {
      this.xPrev = x
      this.tPrev = timestamp
      return x
    }
    const dt = Math.max((timestamp - this.tPrev) / 1000, 0.001) // seconds
    this.tPrev = timestamp

    // Derivative
    const dx = (x - this.xPrev) / dt
    const adx = this._alpha(this.dCutoff, dt)
    const dxHat = adx * dx + (1 - adx) * this.dxPrev
    this.dxPrev = dxHat

    // Adaptive cutoff
    const cutoff = this.minCutoff + this.beta * Math.abs(dxHat)
    const ax = this._alpha(cutoff, dt)
    const xHat = ax * x + (1 - ax) * this.xPrev
    this.xPrev = xHat
    return xHat
  }

  reset() {
    this.xPrev = null
    this.dxPrev = 0
    this.tPrev = null
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Utility functions
// ═══════════════════════════════════════════════════════════════════════════
function dist3(a, b) {
  const dx = a.x - b.x, dy = a.y - b.y, dz = (a.z || 0) - (b.z || 0)
  return Math.sqrt(dx * dx + dy * dy + dz * dz)
}

function dist2(a, b) {
  const dx = a.x - b.x, dy = a.y - b.y
  return Math.sqrt(dx * dx + dy * dy)
}

/** Reference hand size: wrist → middle finger MCP distance */
function getHandRefLength(lm) {
  return dist3(lm[WRIST], lm[MIDDLE_MCP]) || 0.15 // fallback to prevent divide-by-zero
}

/** Check if a finger is extended by comparing tip-wrist dist vs pip-wrist dist */
function isFingerExtended(lm, tip, pip, mcp) {
  const tipToWrist = dist3(lm[tip], lm[WRIST])
  const pipToWrist = dist3(lm[pip], lm[WRIST])
  const tipToMcp   = dist3(lm[tip], lm[mcp])
  const pipToMcp   = dist3(lm[pip], lm[mcp])
  return tipToWrist > pipToWrist && tipToMcp > pipToMcp * 0.75
}

/** Check if thumb is extended (lateral check against pinky side) */
function isThumbExtended(lm) {
  const tipDist = dist3(lm[THUMB_TIP], lm[PINKY_MCP])
  const mcpDist = dist3(lm[THUMB_MCP], lm[PINKY_MCP])
  return tipDist > mcpDist * 1.05
}

/** Get finger extension states */
function getFingerStates(lm) {
  return {
    thumb:  isThumbExtended(lm),
    index:  isFingerExtended(lm, INDEX_TIP,  INDEX_PIP,  INDEX_MCP),
    middle: isFingerExtended(lm, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP),
    ring:   isFingerExtended(lm, RING_TIP,   RING_PIP,   RING_MCP),
    pinky:  isFingerExtended(lm, PINKY_TIP,  PINKY_PIP,  PINKY_MCP),
  }
}

/** Count extended fingers */
function countExtended(fingers) {
  return [fingers.thumb, fingers.index, fingers.middle, fingers.ring, fingers.pinky]
    .filter(Boolean).length
}

// ═══════════════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════════════
export default function HandGestureController({
  isActive,
  rotationAccRef,
  zoomAccRef,
  onPointerMove,
  onSelect,
  onReset,
  onFpsUpdate,
  // Legacy compat
  onRotate,
  onZoom,
}) {
  const videoRef      = useRef(null)
  const streamRef     = useRef(null)
  const landmarkerRef = useRef(null)
  const requestRef    = useRef(null)
  const lastVideoTimeRef   = useRef(-1)
  const lastProcessTimeRef = useRef(null)
  const fpsSamplesRef = useRef([])

  // ── Gesture state machine ──
  const gestureStateRef = useRef(STATE.IDLE)
  const stateDataRef    = useRef({
    pinchStartTime: 0,
    pinchStartPos: { x: 0, y: 0 },
    fistStartTime: 0,
    prevPalmPos: null,
    prevPinchDist: 0,
    clickCooldown: 0,      // prevent rapid re-clicks
  })

  // ── One Euro Filters for smooth tracking ──
  const filtersRef = useRef({
    cursorX:  new OneEuroFilter(1.5, 0.01, 1.0),
    cursorY:  new OneEuroFilter(1.5, 0.01, 1.0),
    palmX:    new OneEuroFilter(1.0, 0.005, 1.0),
    palmY:    new OneEuroFilter(1.0, 0.005, 1.0),
    palmZ:    new OneEuroFilter(0.8, 0.003, 1.0),
  })

  const isActiveRef = useRef(isActive)
  useEffect(() => { isActiveRef.current = isActive }, [isActive])

  useEffect(() => { lastProcessTimeRef.current = performance.now() }, [])

  // Stable callback refs
  const callbacksRef = useRef({ onRotate, onZoom, onPointerMove, onSelect, onReset, onFpsUpdate })
  useEffect(() => {
    callbacksRef.current = { onRotate, onZoom, onPointerMove, onSelect, onReset, onFpsUpdate }
  }, [onRotate, onZoom, onPointerMove, onSelect, onReset, onFpsUpdate])

  const accRefsRef = useRef({ rotationAccRef, zoomAccRef })
  useEffect(() => { accRefsRef.current = { rotationAccRef, zoomAccRef } }, [rotationAccRef, zoomAccRef])

  // ── Initialize HandLandmarker ──
  useEffect(() => {
    let cancelled = false
    let landmarker = null

    const init = async () => {
      const tryCreate = async (delegate) => {
        const vision = await FilesetResolver.forVisionTasks(WASM_CDN)
        if (cancelled) return null
        return HandLandmarker.createFromOptions(vision, {
          baseOptions: { modelAssetPath: MODEL_URL, delegate },
          runningMode: 'VIDEO',
          numHands: 1,
        })
      }

      try {
        landmarker = await tryCreate('GPU')
        if (!cancelled) landmarkerRef.current = landmarker
      } catch (err) {
        console.warn('[HandGesture] GPU init failed, falling back to CPU:', err.message)
        try {
          landmarker = await tryCreate('CPU')
          if (!cancelled) landmarkerRef.current = landmarker
        } catch (err2) {
          console.error('[HandGesture] CPU fallback also failed:', err2)
        }
      }
    }

    init()
    return () => {
      cancelled = true
      if (landmarker) { try { landmarker.close() } catch {} }
      landmarkerRef.current = null
    }
  }, [])

  // ── Camera lifecycle ──
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
    gestureStateRef.current = STATE.IDLE
    lastVideoTimeRef.current = -1
    // Reset filters
    Object.values(filtersRef.current).forEach(f => f.reset())
  }, [])

  useEffect(() => {
    if (!isActive) {
      stopCamera()
      callbacksRef.current.onPointerMove?.(null, null)
      return
    }

    let cancelled = false
    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, frameRate: { ideal: TARGET_FPS } }
        })
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          try { await videoRef.current.play() } catch (e) { console.warn('[HandGesture] play failed:', e) }
        }
      } catch (err) {
        console.error('[HandGesture] Camera access denied:', err)
        callbacksRef.current.onFpsUpdate?.(0)
      }
    }

    startCamera()
    return () => { cancelled = true; stopCamera() }
  }, [isActive, stopCamera])

  // ── Push helpers ──
  const pushRotation = useCallback((dx, dy) => {
    const { rotationAccRef: rRef } = accRefsRef.current
    if (rRef?.current) { rRef.current.x += dx; rRef.current.y += dy }
    else callbacksRef.current.onRotate?.(dx, dy)
  }, [])

  const pushZoom = useCallback((delta) => {
    const { zoomAccRef: zRef } = accRefsRef.current
    if (zRef?.current !== undefined) zRef.current += delta
    else callbacksRef.current.onZoom?.(delta)
  }, [])

  // ═══════════════════════════════════════════════════════════════════════
  // Core frame processor — runs the state machine
  // ═══════════════════════════════════════════════════════════════════════
  const processLandmarks = useCallback((landmarks, timeNow) => {
    const lm  = landmarks
    const cbs = callbacksRef.current
    const sd  = stateDataRef.current
    const f   = filtersRef.current

    // ── Measurements ──
    const handRef   = getHandRefLength(lm)
    const pinchDist = dist2(lm[THUMB_TIP], lm[INDEX_TIP]) / handRef // normalized
    const fingers   = getFingerStates(lm)
    const extCount  = countExtended(fingers)

    // Index fingertip position (mirrored for natural feel)
    const rawCursorX = 1 - lm[INDEX_TIP].x
    const rawCursorY = lm[INDEX_TIP].y
    const cursorX = f.cursorX.filter(rawCursorX, timeNow)
    const cursorY = f.cursorY.filter(rawCursorY, timeNow)

    // Palm center (landmark 9 = middle finger MCP — good proxy)
    const rawPalmX = 1 - lm[MIDDLE_MCP].x
    const rawPalmY = lm[MIDDLE_MCP].y
    const rawPalmZ = lm[MIDDLE_MCP].z || 0
    const palmX = f.palmX.filter(rawPalmX, timeNow)
    const palmY = f.palmY.filter(rawPalmY, timeNow)
    const palmZ = f.palmZ.filter(rawPalmZ, timeNow)

    const prevState = gestureStateRef.current

    // ═══════════════════════════════════════════════════════════════════
    // State machine transitions
    // ═══════════════════════════════════════════════════════════════════

    // ── PINCH detection (highest priority — overrides other states) ──
    const isPinching = pinchDist < PINCH_ENTER_THRESHOLD
    const wasPinching = prevState === STATE.PINCH || prevState === STATE.PINCH_DRAG
    const stillPinching = wasPinching && pinchDist < PINCH_EXIT_THRESHOLD

    if (isPinching && !wasPinching) {
      // ── Enter PINCH ──
      gestureStateRef.current = STATE.PINCH
      sd.pinchStartTime = timeNow
      sd.pinchStartPos  = { x: cursorX, y: cursorY }
      sd.prevPinchDist  = pinchDist

      // Clear pointer when entering pinch
      if (prevState === STATE.POINT) {
        cbs.onPointerMove?.(null, null)
      }
    }
    else if (stillPinching) {
      // ── Stay in PINCH or transition to PINCH_DRAG ──
      const elapsed = timeNow - sd.pinchStartTime
      const moved   = dist2(sd.pinchStartPos, { x: cursorX, y: cursorY })

      if (prevState === STATE.PINCH && (elapsed > PINCH_CLICK_MAX_TIME || moved > PINCH_CLICK_MAX_DIST)) {
        // Held too long or moved too much → it's a drag (zoom)
        gestureStateRef.current = STATE.PINCH_DRAG
      }

      if (gestureStateRef.current === STATE.PINCH_DRAG) {
        // ── Zoom: vertical movement + pinch distance change ──
        const verticalDelta = (sd.prevPalmPos ? (sd.prevPalmPos.y - palmY) : 0) * 140
        const pinchDelta    = (sd.prevPinchDist - pinchDist) * 300
        const zoomDelta     = verticalDelta + pinchDelta

        if (Math.abs(zoomDelta) > 0.15) {
          pushZoom(Math.max(-6, Math.min(6, zoomDelta)))
        }
      }

      sd.prevPinchDist = pinchDist
    }
    else if (wasPinching && !stillPinching) {
      // ── Release pinch ──
      const elapsed = timeNow - sd.pinchStartTime
      const moved   = dist2(sd.pinchStartPos, { x: cursorX, y: cursorY })

      if (prevState === STATE.PINCH && elapsed < PINCH_CLICK_MAX_TIME &&
          moved < PINCH_CLICK_MAX_DIST && timeNow - sd.clickCooldown > 400) {
        // ── Quick pinch+release = CLICK ──
        const clickX = (sd.pinchStartPos.x * 2) - 1
        const clickY = -(sd.pinchStartPos.y * 2) + 1
        cbs.onPointerMove?.(clickX, clickY)
        cbs.onSelect?.(clickX, clickY)
        sd.clickCooldown = timeNow
      }

      gestureStateRef.current = STATE.IDLE
      sd.prevPinchDist = pinchDist
    }

    // ── Non-pinch states ──
    else if (!wasPinching && !isPinching) {
      // FIST: 0-1 fingers extended (not thumb alone)
      if (extCount <= 1 && !fingers.index && !fingers.middle) {
        if (prevState !== STATE.FIST) {
          gestureStateRef.current = STATE.FIST
          sd.fistStartTime = timeNow
        } else if (timeNow - sd.fistStartTime > FIST_HOLD_TIME) {
          cbs.onReset?.()
          sd.fistStartTime = timeNow // prevent rapid re-triggers
        }

        if (prevState === STATE.POINT) cbs.onPointerMove?.(null, null)
      }
      // POINT: index extended, others curled
      else if (fingers.index && !fingers.ring && !fingers.pinky && extCount <= 3) {
        gestureStateRef.current = STATE.POINT

        const pointerX = (cursorX * 2) - 1
        const pointerY = -(cursorY * 2) + 1
        cbs.onPointerMove?.(pointerX, pointerY)
      }
      // ROTATE: 3+ fingers extended (open palm)
      else if (extCount >= 3) {
        gestureStateRef.current = STATE.ROTATE

        if (prevState === STATE.ROTATE && sd.prevPalmPos) {
          const dx = (palmX - sd.prevPalmPos.x) * 280
          const dy = (palmY - sd.prevPalmPos.y) * 280

          // Deadzone
          if (Math.abs(dx) > 0.1 || Math.abs(dy) > 0.1) {
            pushRotation(
              Math.max(-8, Math.min(8, dx)),
              Math.max(-8, Math.min(8, dy))
            )
          }
        }

        // Palm depth zoom
        if (sd.prevPalmPos) {
          const zDelta = (palmZ - sd.prevPalmPos.z) * -160
          if (Math.abs(zDelta) > 0.25) {
            pushZoom(Math.max(-4, Math.min(4, zDelta)))
          }
        }

        if (prevState === STATE.POINT) cbs.onPointerMove?.(null, null)
      }
      // IDLE: catch-all
      else {
        if (prevState === STATE.POINT) cbs.onPointerMove?.(null, null)
        gestureStateRef.current = STATE.IDLE
      }
    }

    // ── Store palm position for next frame ──
    sd.prevPalmPos = { x: palmX, y: palmY, z: palmZ }

  }, [pushRotation, pushZoom])

  // ═══════════════════════════════════════════════════════════════════════
  // RAF loop
  // ═══════════════════════════════════════════════════════════════════════
  useEffect(() => {
    if (!isActive) return

    const msPerFrame = 1000 / TARGET_FPS

    const processFrame = () => {
      if (!isActiveRef.current || !videoRef.current || !landmarkerRef.current) {
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
            const results = landmarkerRef.current.detectForVideo(videoRef.current, now)

            if (results.landmarks && results.landmarks.length > 0) {
              processLandmarks(results.landmarks[0], now)
            } else {
              // No hand detected
              if (gestureStateRef.current === STATE.POINT) {
                callbacksRef.current.onPointerMove?.(null, null)
              }
              gestureStateRef.current = STATE.IDLE
              stateDataRef.current.prevPalmPos = null
              // Reset filters when hand lost
              Object.values(filtersRef.current).forEach(f => f.reset())
            }
          } catch (e) {
            console.error('[HandGesture] detect error:', e)
          }
        }

        // Smoothed FPS
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
  }, [isActive, processLandmarks])

  // ═══════════════════════════════════════════════════════════════════════
  // Render — camera preview
  // ═══════════════════════════════════════════════════════════════════════
  return (
    <div
      className={`absolute bottom-6 right-6 w-48 h-36 bg-black border border-[var(--accent)] rounded-lg overflow-hidden shadow-[0_0_15px_rgba(0,255,102,0.3)] z-50 pointer-events-none transition-opacity duration-300 ${isActive ? 'opacity-100' : 'opacity-0 hidden'}`}
    >
      <div className="absolute top-0 left-0 w-full bg-black/60 text-white text-[10px] p-1 text-center font-mono z-10">
        {gestureStateRef.current !== STATE.IDLE
          ? `✋ ${gestureStateRef.current}`
          : 'GESTURE TRACKING'
        }
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
