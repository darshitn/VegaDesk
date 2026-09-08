import React, { useRef, useMemo, useState, useEffect } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Stars, Sparkles } from '@react-three/drei'
import { EffectComposer, Bloom, Vignette, ChromaticAberration } from '@react-three/postprocessing'
import * as THREE from 'three'

// Fibonacci sphere generator
function getFibonacciSpherePoints(samples, radius) {
  const points = []
  const phi = Math.PI * (3 - Math.sqrt(5))
  for (let i = 0; i < samples; i++) {
    const y = 1 - (i / (samples - 1)) * 2
    const r = Math.sqrt(1 - y * y)
    const theta = phi * i
    const x = Math.cos(theta) * r
    const z = Math.sin(theta) * r
    points.push(new THREE.Vector3(x * radius, y * radius, z * radius))
  }
  return points
}

const HUB_MODULES = [
  { id: 'AIBrain', title: 'AI Brain', color: '#00f0ff' },
  { id: 'SystemMonitor', title: 'System Monitor', color: '#00ff66' },
  { id: 'LiveFeeds', title: 'Live Feeds', color: '#ff0055' },
  { id: 'ProductivityHub', title: 'Productivity', color: '#ffaa00' },
  { id: 'FocusMode', title: 'Focus Mode', color: '#aa00ff' }
]

// --- Futuristic Burning Shader ---
const burningVertexShader = `
  varying vec2 vUv;
  varying vec3 vPos;
  void main() {
    vUv = uv;
    vPos = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`
const burningFragmentShader = `
  uniform float uTime;
  uniform vec3 uColor;
  uniform float uIntensity;
  varying vec2 vUv;
  varying vec3 vPos;
  // hash
  float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }
  float noise(vec2 p){
    vec2 i = floor(p);
    vec2 f = fract(p);
    float a = hash(i);
    float b = hash(i+vec2(1.0,0.0));
    float c = hash(i+vec2(0.0,1.0));
    float d = hash(i+vec2(1.0,1.0));
    vec2 u = f*f*(3.0-2.0*f);
    return mix(a,b,u.x) + (c-a)*u.y*(1.0-u.x) + (d-b)*u.x*u.y;
  }
  void main(){
    float t = uTime * 1.2;
    // radial gradient from center
    float dist = length(vPos.xz) * 0.8 + abs(vPos.y)*0.5;
    float flicker = noise(vec2(vUv.x*8.0 + t*0.7, vUv.y*3.0 - t*0.5)) * 0.5 + 0.5;
    float ember = noise(vec2(vUv.y*12.0 + t, vUv.x*4.0)) * 0.3;
    // burning edge: hotter at bottom, cooler at top
    float vertical = smoothstep(-1.0, 1.0, vPos.y + flicker*0.3);
    vec3 fire1 = vec3(1.0, 0.35, 0.05); // orange
    vec3 fire2 = vec3(1.0, 0.65, 0.15);
    vec3 fire3 = vec3(0.9, 0.15, 0.02); // deep red
    vec3 base = mix(fire3, fire1, vertical);
    base = mix(base, fire2, flicker*0.4 + ember);
    // core
    float core = 1.0 - smoothstep(0.0, 0.9, dist);
    base += core * vec3(1.0, 0.9, 0.6) * 0.6;
    // pulse
    float pulse = sin(uTime*3.0 + dist*4.0)*0.15 + 1.0;
    base *= pulse * uIntensity;
    float alpha = smoothstep(1.2, 0.2, dist) * (0.85 + flicker*0.15);
    // ember sparks
    float spark = step(0.92, flicker + ember) * step(0.5, vertical) * 0.9;
    base += vec3(1.0, 0.95, 0.7) * spark * 0.8;
    gl_FragColor = vec4(base * uColor / vec3(1.0,0.7,0.4), alpha);
  }
`

function BurningShell({ color, isHovered }) {
  const matRef = useRef()
  useFrame((state) => {
    if (matRef.current) matRef.current.uniforms.uTime.value = state.clock.elapsedTime
  })
  const uniforms = useMemo(() => ({
    uTime: { value: 0 },
    uColor: { value: new THREE.Color(color) },
    uIntensity: { value: isHovered ? 1.45 : 0.95 }
  }), [color, isHovered])
  useEffect(() => {
    if (matRef.current) matRef.current.uniforms.uIntensity.value = isHovered ? 1.45 : 0.95
  }, [isHovered])
  return (
    <mesh scale={[1.42, 1.42, 1.42]}>
      <sphereGeometry args={[0.62, 32, 32]} />
      <shaderMaterial
        ref={matRef}
        vertexShader={burningVertexShader}
        fragmentShader={burningFragmentShader}
        uniforms={uniforms}
        transparent
        blending={THREE.AdditiveBlending}
        depthWrite={false}
        side={THREE.DoubleSide}
      />
    </mesh>
  )
}

function HubNode({ position, data, onClick, isHovered, onPointerOver, onPointerOut }) {
  const meshRef = useRef()
  const ringRef = useRef()
  const lightRef = useRef()
  const downPosRef = useRef(null)

  // Click-vs-drag: orbiting the scene drags across nodes — only treat
  // pointer-up within ~5px of pointer-down as a selection click.
  const handlePointerDown = (e) => {
    downPosRef.current = [e.clientX ?? 0, e.clientY ?? 0]
  }
  const handleClick = (e) => {
    const d = downPosRef.current
    downPosRef.current = null
    if (d) {
      const dx = (e.clientX ?? 0) - d[0]
      const dy = (e.clientY ?? 0) - d[1]
      if (dx * dx + dy * dy > 25) return
    }
    onClick(data.id)
  }
  // Shared by the visible core and the larger invisible hit target
  const hitHandlers = {
    onClick: handleClick,
    onPointerDown: handlePointerDown,
    onPointerOver: (e) => { e.stopPropagation(); onPointerOver(data.id) },
    onPointerOut: (e) => { e.stopPropagation(); onPointerOut() },
  }

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime
    const s = isHovered ? 1.18 + Math.sin(t * 6) * 0.08 : 1 + Math.sin(t * 1.5 + position.x) * 0.02
    if (meshRef.current) meshRef.current.scale.set(s, s, s)
    if (ringRef.current) {
      ringRef.current.rotation.z += delta * (isHovered ? 1.2 : 0.35)
      ringRef.current.rotation.y += delta * 0.2
    }
    if (lightRef.current) lightRef.current.intensity = isHovered ? 3.5 + Math.sin(t*8)*0.8 : 1.2 + Math.sin(t*2)*0.3
  })

  return (
    <group position={position}>
      {/* Point light for burning glow */}
      <pointLight ref={lightRef} color={data.color} intensity={1.5} distance={4} decay={2} />

      {/* Inner core */}
      <mesh
        ref={meshRef}
        {...hitHandlers}
        userData={{ isHub: true, id: data.id }}
      >
        <sphereGeometry args={[0.55, 32, 32]} />
        <meshStandardMaterial color={data.color} emissive={data.color} emissiveIntensity={isHovered ? 2.2 : 1.1} roughness={0.25} metalness={0.6} />
      </mesh>

      {/* Oversized transparent hit target — makes nodes much easier to
          select (three.js raycasts transparent materials, skips invisible) */}
      <mesh {...hitHandlers} userData={{ isHub: true, id: data.id }}>
        <sphereGeometry args={[1.05, 16, 16]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>

      {/* Icosa wireframe cage — futuristic */}
      <mesh scale={[0.95, 0.95, 0.95]} rotation={[0.2, 0.4, 0]}>
        <icosahedronGeometry args={[0.62, 1]} />
        <meshBasicMaterial color={data.color} wireframe transparent opacity={isHovered ? 0.5 : 0.22} />
      </mesh>

      {/* Burning shader shell */}
      <BurningShell color={data.color} isHovered={isHovered} />

      {/* Rotating ring */}
      <mesh ref={ringRef} rotation={[Math.PI/2.5, 0, 0]}>
        <torusGeometry args={[0.78, 0.015, 16, 64]} />
        <meshBasicMaterial color={data.color} transparent opacity={isHovered ? 0.9 : 0.45} />
      </mesh>
      <mesh rotation={[0, 0, 0]} scale={[1,1,1]}>
        <ringGeometry args={[0.72, 0.74, 64]} />
        <meshBasicMaterial color={data.color} transparent opacity={0.18} side={THREE.DoubleSide} />
      </mesh>

      <Text
        position={[0, 1.45, 0]}
        fontSize={0.38}
        color={data.color}
        anchorX="center"
        anchorY="middle"
        outlineWidth={0.025}
        outlineColor="#000000"
        fillOpacity={isHovered ? 1 : 0.88}
      >
        {data.title}
      </Text>
      {isHovered && (
        <Text position={[0, -0.9, 0]} fontSize={0.16} color="#ffffff" anchorX="center" opacity={0.7}>
          PINCH TO SELECT • PINCH+DRAG TO ZOOM
        </Text>
      )}
    </group>
  )
}

function BurningCore() {
  const matRef = useRef()
  const meshRef = useRef()
  useFrame((state) => {
    const t = state.clock.elapsedTime
    if (matRef.current) {
      matRef.current.uniforms.uTime.value = t
      matRef.current.uniforms.uIntensity.value = 0.9 + Math.sin(t*0.9)*0.18
    }
    if (meshRef.current) {
      meshRef.current.rotation.y += 0.003
      meshRef.current.rotation.x += 0.0015
      const s = 1 + Math.sin(t*1.1)*0.04
      meshRef.current.scale.set(s,s,s)
    }
  })
  const uniforms = useMemo(()=>({
    uTime:{value:0},
    uColor:{value:new THREE.Color('#ff5a1f')},
    uIntensity:{value:1.0}
  }),[])
  return (
    <group>
      <pointLight color="#ff6a2a" intensity={2.5} distance={18} decay={1.8} />
      <mesh ref={meshRef}>
        <icosahedronGeometry args={[1.35, 3]} />
        <shaderMaterial ref={matRef} vertexShader={burningVertexShader} fragmentShader={burningFragmentShader} uniforms={uniforms} transparent blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      {/* inner white hot core */}
      <mesh scale={[0.55,0.55,0.55]}>
        <sphereGeometry args={[1.0, 32, 32]} />
        <meshBasicMaterial color="#fff7e0" transparent opacity={0.22} />
      </mesh>
    </group>
  )
}

function AnimatedArcs({ points, hubPoints }) {
  const linesRef = useRef()
  const matRefs = useRef([])
  const geos = useMemo(()=>{
    const all = [...points, ...hubPoints.map(h=>h.pos)]
    const out=[]
    for(let i=0;i<all.length;i++){
      for(let j=i+1;j<all.length;j++){
        if(all[i].distanceTo(all[j])<2.7){
          // curve via midpoint lifted
          const mid = new THREE.Vector3().addVectors(all[i], all[j]).multiplyScalar(0.5)
          const len = all[i].distanceTo(all[j])
          mid.normalize().multiplyScalar(mid.length() + len*0.12)
          const curve = new THREE.QuadraticBezierCurve3(all[i], mid, all[j])
          const geo = new THREE.BufferGeometry().setFromPoints(curve.getPoints(24))
          out.push({ geo, len })
        }
      }
    }
    return out
  },[points, hubPoints])
  useEffect(()=>()=>{ geos.forEach(g=>g.geo.dispose()) },[geos])
  useFrame((s)=>{
    const t = s.clock.elapsedTime
    matRefs.current.forEach((m,i)=>{
      if(m) m.uniforms.uTime.value = t + i*0.12
    })
  })
  return (
    <group ref={linesRef}>
      {geos.map((g,idx)=>{
        // pick color based on nearest hub or default cyan
        const c = idx % 3 === 0 ? '#00f0ff' : idx % 3 === 1 ? '#ff6a2a' : '#7a5cff'
        return (
          <group key={`arc-${idx}`}>
            <line geometry={g.geo}>
              <shaderMaterial
                ref={el=> matRefs.current[idx]=el}
                transparent
                uniforms={{ uTime:{value:0}, uColor:{value:new THREE.Color(c)} }}
                vertexShader={`varying float vD; void main(){ vD = position.x*0.1; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`}
                fragmentShader={`
                  uniform float uTime;
                  uniform vec3 uColor;
                  varying float vD;
                  void main(){
                    float t = fract(uTime*0.6 - vD*0.2);
                    float pulse = smoothstep(0.0,0.12,t) * (1.0 - smoothstep(0.12,0.35,t));
                    float base = 0.18 + pulse*0.85;
                    gl_FragColor = vec4(uColor*base, base*0.9);
                  }
                `}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
              />
            </line>
          </group>
        )
      })}
    </group>
  )
}

function EmberParticles({ count=280, radius=9 }) {
  const ref = useRef()
  const positions = useMemo(()=>{
    const arr = new Float32Array(count*3)
    for(let i=0;i<count;i++){
      const r = radius * (0.85 + Math.random()*0.22)
      const theta = Math.random()*Math.PI*2
      const phi = Math.acos(2*Math.random()-1)
      arr[i*3] = r*Math.sin(phi)*Math.cos(theta) + (Math.random()-0.5)*0.8
      arr[i*3+1] = r*Math.cos(phi) + (Math.random()-0.5)*0.8
      arr[i*3+2] = r*Math.sin(phi)*Math.sin(theta) + (Math.random()-0.5)*0.8
    }
    return arr
  },[count,radius])
  useFrame((s)=>{
    if(!ref.current) return
    const t = s.clock.elapsedTime
    ref.current.rotation.y += 0.0007
    // flicker size
    const mat = ref.current.material
    if(mat) mat.size = 0.09 + Math.sin(t*2.3)*0.02
  })
  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.09} color="#ff8a3a" transparent opacity={0.9} sizeAttenuation blending={THREE.AdditiveBlending} depthWrite={false} />
    </points>
  )
}

function Scene({ onSelectModule, pointerCoords, rotationDelta, zoomDelta, resetTrigger }) {
  const { camera, raycaster, gl } = useThree()
  const controlsRef = useRef()
  const groupRef = useRef()
  const [hoveredNode, setHoveredNode] = useState(null)
  const particlesCount = 180
  const radius = 8.2
  // Interaction bookkeeping: mouse drags (OrbitControls onStart), wheel zooms,
  // zoom buttons and gesture deltas all bump this — the idle auto-spin only
  // resumes 4s after the last interaction, so nodes stay still while you click.
  const lastInteractionRef = useRef(0)

  const { basePoints, hubPoints } = useMemo(() => {
    const allPoints = getFibonacciSpherePoints(particlesCount, radius)
    const step = Math.floor(particlesCount / 5)
    const hubs = []; const base=[]
    for (let i=0;i<allPoints.length;i++){
      if (i % step === 0 && hubs.length < 5) hubs.push({ pos: allPoints[i], ...HUB_MODULES[hubs.length] })
      else base.push(allPoints[i])
    }
    return { basePoints: base, hubPoints: hubs }
  }, [])

  const rotationDeltaRef = useRef(rotationDelta)
  useEffect(()=>{ rotationDeltaRef.current = rotationDelta },[rotationDelta])

  // Single source of truth for camera distance. OrbitControls handles rotation
  // only (enableZoom off — wheel is handled manually); every zoom source
  // (wheel, HUD buttons, gesture pinch) writes desiredDistRef and useFrame
  // smooths the camera toward it along the view axis.
  const desiredDistRef = useRef(20)
  useEffect(()=>{
    if(zoomDelta!==0){
      desiredDistRef.current = THREE.MathUtils.clamp(desiredDistRef.current - zoomDelta*0.045, 6, 38)
      lastInteractionRef.current = performance.now()
    }
  },[zoomDelta])

  // Wheel zoom (OrbitControls zoom is disabled to avoid two competing sources)
  useEffect(()=>{
    const el = gl.domElement
    const onWheel = (e) => {
      e.preventDefault()
      const dir = e.deltaY > 0 ? 1 : -1
      desiredDistRef.current = THREE.MathUtils.clamp(desiredDistRef.current + dir * 1.2, 6, 38)
      lastInteractionRef.current = performance.now()
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  },[gl])

  // Pointer cursor over interactive nodes
  useEffect(()=>{
    gl.domElement.style.cursor = hoveredNode ? 'pointer' : 'grab'
  },[hoveredNode, gl])

  useEffect(()=>{
    if(resetTrigger>0){
      camera.position.set(0,0,20)
      camera.lookAt(0,0,0)
      desiredDistRef.current = 20
      lastInteractionRef.current = 0
      if(groupRef.current) groupRef.current.rotation.set(0,0,0)
    }
  },[resetTrigger, camera])

  const hoveredRef = useRef(hoveredNode)
  useEffect(()=>{ hoveredRef.current = hoveredNode },[hoveredNode])
  useEffect(()=>{
    if(!pointerCoords || !groupRef.current) return
    raycaster.setFromCamera(new THREE.Vector2(pointerCoords.x, pointerCoords.y), camera)
    const hubs=[]; groupRef.current.traverse(o=>{ if(o.isMesh && o.userData?.isHub) hubs.push(o) })
    const intersects = raycaster.intersectObjects(hubs,false)
    let found=null; for(let i=0;i<intersects.length;i++){ const obj=intersects[i].object; if(obj.userData?.isHub){ found=obj.userData.id; break } }
    if(found!==hoveredRef.current) setHoveredNode(found)
    if(pointerCoords.clicked && found) onSelectModule(found)
  },[pointerCoords, camera, raycaster, onSelectModule])

  useFrame(()=>{
    const controls = controlsRef.current
    const now = performance.now()
    // Smooth the camera toward the desired distance along the view axis
    const target = controls ? controls.target : new THREE.Vector3()
    const cur = camera.position.distanceTo(target)
    const nd = THREE.MathUtils.lerp(cur, desiredDistRef.current, 0.1)
    if (Math.abs(nd - cur) > 0.005) {
      const dir = camera.position.clone().sub(target).normalize()
      camera.position.copy(target).addScaledVector(dir, nd)
    }
    // Gentle idle spin only after 4s without interaction
    if (groupRef.current && now - lastInteractionRef.current > 4000) {
      groupRef.current.rotation.y += 0.0009
      groupRef.current.rotation.x += 0.0004
    }
    if (controls) controls.update()
  })

  return (
    <>
      <color attach="background" args={['#07030a']} />
      <fog attach="fog" args={['#1a0a05', 18, 42]} />
      {/* OrbitControls always available: drag = orbit. Zoom is handled manually
          (enableZoom off) so wheel/buttons/gestures share one distance pipeline. */}
      <OrbitControls
        ref={controlsRef}
        enableDamping
        dampingFactor={0.06}
        enablePan={false}
        enableZoom={false}
        minDistance={6}
        maxDistance={38}
        onStart={() => { lastInteractionRef.current = performance.now() }}
      />
      <ambientLight intensity={0.45} />
      <directionalLight position={[6,8,5]} intensity={0.9} color="#ffb87a" />
      <pointLight position={[0,0,0]} intensity={1.6} color="#ff6a2a" distance={30} />
      <Stars radius={55} depth={55} count={3800} factor={4.2} saturation={0} fade speed={0.6} />
      <Sparkles count={90} scale={[22,22,22]} size={0.85} speed={0.28} noise={1.2} color="#ff8a3a" />
      <EmberParticles count={320} radius={9.5} />

      <group ref={groupRef}>
        <BurningCore />
        {basePoints.map((pos, idx) => (
          <mesh key={`base-${idx}`} position={pos}>
            <sphereGeometry args={[0.075 + Math.random()*0.04, 7, 7]} />
            <meshStandardMaterial color={idx%4===0 ? "#ff6a2a" : "#445588"} emissive={idx%4===0 ? "#ff3a0a" : "#1a2a4a"} emissiveIntensity={idx%4===0?0.9:0.25} transparent opacity={0.72} />
          </mesh>
        ))}
        <AnimatedArcs points={basePoints} hubPoints={hubPoints} />
        {hubPoints.map((hub) => (
          <HubNode
            key={hub.id}
            position={hub.pos}
            data={hub}
            isHovered={hoveredNode === hub.id}
            onPointerOver={(id) => setHoveredNode(id)}
            onPointerOut={() => setHoveredNode(null)}
            onClick={(id) => onSelectModule(id)}
          />
        ))}
      </group>

      {/* Futuristic vignette + bloom */}
      <EffectComposer>
        <Bloom intensity={1.25} luminanceThreshold={0.12} luminanceSmoothing={0.85} radius={0.6} />
        <Vignette eskil={false} offset={0.18} darkness={0.62} />
        <ChromaticAberration offset={[0.0007, 0.0007]} radialModulation={false} modulationOffset={0.15} />
      </EffectComposer>

      {/* Zoom HUD */}
      <group>
        {/* html overlay for zoom is outside canvas; we expose via onZoomLevel? use DOM */}
      </group>
    </>
  )
}

export default function NeuralCosmos({ onSelectModule, pointerCoords, rotationDelta, zoomDelta, resetTrigger, gesturesActive }) {
  return (
    <div className="w-full h-full relative bg-[#07030a] overflow-hidden">
      {/* Futuristic grid overlay */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.07]" style={{
        backgroundImage: `linear-gradient(rgba(255,106,42,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(255,106,42,0.4) 1px, transparent 1px)`,
        backgroundSize: '40px 40px'
      }} />
      {/* Scanlines */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.04]" style={{
        background: `repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(255,120,40,0.5) 2px, rgba(255,120,40,0.5) 3px)`
      }} />
      <Canvas camera={{ position: [0, 0, 20], fov: 58 }} dpr={[1, 1.5]} gl={{ antialias: true, powerPreference: 'high-performance' }}>
        <Scene
          onSelectModule={onSelectModule}
          pointerCoords={pointerCoords}
          rotationDelta={rotationDelta}
          zoomDelta={zoomDelta}
          resetTrigger={resetTrigger}
        />
      </Canvas>

      {/* Futuristic Zoom HUD */}
      <div className="absolute top-4 left-4 flex flex-col gap-2 pointer-events-auto">
        <div className="bg-black/70 backdrop-blur-md border border-[#ff6a2a]/40 rounded-lg px-3 py-2 shadow-[0_0_20px_rgba(255,106,42,0.25)]">
          <div className="text-[10px] tracking-[0.18em] text-[#ff8a3a]">NEURAL COSMOS • BURNING CORE v2</div>
          <div className="text-[11px] text-white/80 mt-1 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-[#ff3a0a] animate-pulse shadow-[0_0_8px_#ff3a0a]" /> Burning embers • Bloom active
          </div>
        </div>
        <div className="bg-black/60 backdrop-blur border border-white/10 rounded-lg p-2 flex flex-col gap-1.5 w-[172px]">
          <div className="text-[10px] tracking-widest text-white/60">GESTURE ZOOM</div>
          <div className="flex gap-1.5">
            <button
              onClick={() => { /* parent will handle via prop drill? use window dispatch */ window.dispatchEvent(new CustomEvent('jarvis-zoom', { detail: -4 })) }}
              className="flex-1 py-1.5 rounded bg-[#ff6a2a] text-black text-xs font-bold hover:bg-[#ff8a3a] transition-colors"
            >− Zoom Out</button>
            <button
              onClick={() => window.dispatchEvent(new CustomEvent('jarvis-zoom', { detail: 4 }))}
              className="flex-1 py-1.5 rounded bg-[#00f0ff] text-black text-xs font-bold hover:bg-[#7afcff] transition-colors"
            >+ Zoom In</button>
          </div>
          <div className="text-[10px] leading-tight text-white/55">
            <span className="text-white">Drag</span> to orbit • <span className="text-white">Scroll</span> to zoom<br/>
            <span className="text-white">Click</span> a node to open it
          </div>
          {gesturesActive && (
            <div className="text-[10px] text-[#00ff66] flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00ff66] animate-pulse" /> Gesture Tracking Active
            </div>
          )}
        </div>
      </div>

      {/* Bottom ember bar */}
      <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-[#ff6a2a] to-transparent opacity-80" />
      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 text-[10px] tracking-[0.2em] text-[#ff8a3a]/70 pointer-events-none">BURNING NEURAL MATRIX • HAND-TRACKED</div>
    </div>
  )
}
