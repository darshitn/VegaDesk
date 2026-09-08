import { useEffect, useRef, useCallback } from 'react'
import { GestureRecognizer, FilesetResolver } from '@mediapipe/tasks-vision'

const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task'
const TARGET_FPS = 30

export default function HandGestureController({
  isActive,
  onRotate,
  onZoom,
  onPointerMove,
  onSelect,
  onReset,
  onFpsUpdate
}) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const recognizerRef = useRef(null)
  const requestRef = useRef(null)
  const lastVideoTimeRef = useRef(-1)

  const lastPosRef = useRef({ x: 0, y: 0, z: 0 })
  const lastGestureRef = useRef('None')
  const dwellStartTimeRef = useRef(0)
  const lastProcessTimeRef = useRef(null)
  const fpsSamplesRef = useRef([])
  const prevPinchDistRef = useRef(0.05)

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
    lastGestureRef.current = 'None'
    dwellStartTimeRef.current = 0
    lastVideoTimeRef.current = -1
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

  const handleGesture = useCallback((gesture, landmarks, timeNow) => {
    const indexTip = landmarks[8]
    const thumbTip = landmarks[4]

    const currPos = { x: 1 - indexTip.x, y: indexTip.y } // mirrored

    const dx = indexTip.x - thumbTip.x
    const dy = indexTip.y - thumbTip.y
    const dz = (indexTip.z - thumbTip.z) || 0
    const pinchDist = Math.sqrt(dx * dx + dy * dy + dz * dz)

    const cbs = callbacksRef.current

    if (gesture === 'Open_Palm') {
      const deltaX = currPos.x - lastPosRef.current.x
      const deltaY = currPos.y - lastPosRef.current.y
      if (lastGestureRef.current === 'Open_Palm') {
        cbs.onRotate?.(deltaX * 320, deltaY * 320)
      }
      // subtle zoom via palm depth (z): moving hand closer (more negative z) zooms in
      const zDelta = (landmarks[9].z - (lastPosRef.current.z ?? landmarks[9].z)) * -180
      if (Math.abs(zDelta) > 0.4) cbs.onZoom?.(zDelta)
      lastPosRef.current.z = landmarks[9].z
      dwellStartTimeRef.current = 0
      prevPinchDistRef.current = pinchDist
    } else if (pinchDist < 0.07 || gesture === 'Closed_Fist') {
      if (gesture === 'Closed_Fist') {
        // long fist = reset, short tap = ignore
        if (lastGestureRef.current !== 'Closed_Fist') dwellStartTimeRef.current = timeNow
        else if (timeNow - dwellStartTimeRef.current > 700) { cbs.onReset?.(); dwellStartTimeRef.current = timeNow }
      } else {
        // Pinch zoom: combine vertical drag + pinch distance change for intuitive zoom
        const vertical = (lastPosRef.current.y - currPos.y) * 140
        const pinchDelta = (prevPinchDistRef.current - pinchDist) * 420 // closing fingers = zoom in
        const zoomDelta = vertical + pinchDelta
        // deadzone to avoid jitter
        if (Math.abs(zoomDelta) > 0.3) {
          // clamp per-frame to avoid teleport
          const clamped = Math.max(-6, Math.min(6, zoomDelta))
          cbs.onZoom?.(clamped)
        }
      }
      prevPinchDistRef.current = pinchDist
      dwellStartTimeRef.current = 0
    } else if (gesture === 'Pointing_Up' || gesture === 'Victory') {
      const pointerX = (currPos.x * 2) - 1
      const pointerY = -(currPos.y * 2) + 1

      cbs.onPointerMove?.(pointerX, pointerY)

      if (lastGestureRef.current !== gesture) {
        dwellStartTimeRef.current = timeNow
      } else {
        const dwellTime = timeNow - dwellStartTimeRef.current
        if (dwellTime > 1100) {
          cbs.onSelect?.(pointerX, pointerY)
          dwellStartTimeRef.current = timeNow // prevent immediate re-trigger
        }
      }
    } else {
      if (lastGestureRef.current === 'Pointing_Up' || lastGestureRef.current === 'Victory') {
        cbs.onPointerMove?.(null, null)
      }
      dwellStartTimeRef.current = 0
    }

    lastPosRef.current = currPos
    lastGestureRef.current = gesture
  }, [])

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
            if (results.gestures.length > 0) {
              const gesture = results.gestures[0][0].categoryName
              const landmarks = results.landmarks[0]
              handleGesture(gesture, landmarks, now)
            } else {
              if (lastGestureRef.current === 'Pointing_Up' || lastGestureRef.current === 'Victory') {
                callbacksRef.current.onPointerMove?.(null, null)
              }
              lastGestureRef.current = 'None'
              dwellStartTimeRef.current = 0
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
