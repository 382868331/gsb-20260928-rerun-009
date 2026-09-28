import { useEffect, useMemo, useRef, useState } from "react"
import type { CellPose, Point, Project, Rect } from "./types"

type Props = {
  project: Project
  imageUrl: string
  mode: "zone" | "start" | "goal" | "pan"
  start: Point
  goal: Point
  path: CellPose[]
  robot: { width: number; length: number }
  pixelsPerMeter: number
  onChangeZone: (zones: Rect[]) => void
  onSelectPoint: (point: Point) => void
}

function worldMatrix(project: Project, deviceScale = 1) {
  const r = project.resolution
  const c = Math.cos(project.origin.yaw)
  const s = Math.sin(project.origin.yaw)
  const h = project.height
  return {
    a: (c / r) * deviceScale,
    b: (-s / r) * deviceScale,
    c: (-s / r) * deviceScale,
    d: (-c / r) * deviceScale,
    e: ((-c * project.origin.x + s * project.origin.y) / r) * deviceScale,
    f: h * deviceScale + ((s * project.origin.x + c * project.origin.y) / r) * deviceScale,
  }
}

function toWorld(event: React.PointerEvent<HTMLCanvasElement>, project: Project, canvas: HTMLCanvasElement): Point {
  const rect = canvas.getBoundingClientRect()
  const sx = event.clientX - rect.left
  const sy = event.clientY - rect.top
  const m = worldMatrix(project)
  const denominator = m.a * m.d - m.b * m.c
  const x = (m.d * (sx - m.e) - m.c * (sy - m.f)) / denominator
  const y = (-m.b * (sx - m.e) + m.a * (sy - m.f)) / denominator
  return { x, y }
}

function drawRobot(ctx: CanvasRenderingContext2D, point: Point, yaw: number, robot: { width: number; length: number }, color: string) {
  ctx.save()
  ctx.translate(point.x, point.y)
  ctx.rotate(yaw)
  ctx.fillStyle = color
  ctx.fillRect(-robot.length / 2, -robot.width / 2, robot.length, robot.width)
  ctx.strokeStyle = "#111827"
  ctx.lineWidth = 0.015
  ctx.strokeRect(-robot.length / 2, -robot.width / 2, robot.length, robot.width)
  ctx.restore()
}

export function MapCanvas({ project, imageUrl, mode, start, goal, path, robot, pixelsPerMeter, onChangeZone, onSelectPoint }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [draft, setDraft] = useState<Rect | null>(null)
  const draftStart = useRef<Point | null>(null)
  const mapWidth = project.width * project.resolution
  const mapHeight = project.height * project.resolution

  useEffect(() => {
    const image = new Image()
    image.src = imageUrl
    image.onload = () => {
      imageRef.current = image
      redraw()
    }
  }, [imageUrl])

  const redraw = () => {
    const canvas = canvasRef.current
    const image = imageRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    const m = worldMatrix(project)
    ctx.setTransform(m.a, m.b, m.c, m.d, m.e, m.f)
    if (image) ctx.drawImage(image, 0, 0, mapWidth, mapHeight)
    else {
      ctx.fillStyle = "#f8fafc"
      ctx.fillRect(0, 0, mapWidth, mapHeight)
    }

    for (const zone of [...project.forbidden_zones, ...(draft ? [draft] : [])]) {
      ctx.fillStyle = "rgba(239, 68, 68, 0.34)"
      ctx.strokeStyle = "#dc2626"
      ctx.lineWidth = 0.025
      ctx.fillRect(zone.x, zone.y, zone.width, zone.height)
      ctx.strokeRect(zone.x, zone.y, zone.width, zone.height)
    }

    ctx.strokeStyle = "rgba(34, 197, 94, 0.75)"
    ctx.lineWidth = 0.045
    ctx.beginPath()
    path.forEach((pose, index) => index === 0 ? ctx.moveTo(pose.x, pose.y) : ctx.lineTo(pose.x, pose.y))
    ctx.stroke()
    for (const pose of path.filter((_, index) => index % Math.max(1, Math.floor(path.length / 18)) === 0)) {
      drawRobot(ctx, pose, pose.yaw, robot, "rgba(34, 197, 94, 0.25)")
    }
    drawRobot(ctx, start, 0, robot, "rgba(59, 130, 246, 0.55)")
    drawRobot(ctx, goal, 0, robot, "rgba(168, 85, 247, 0.55)")

    ctx.save()
    ctx.translate(project.origin.x, project.origin.y)
    ctx.rotate(project.origin.yaw)
    ctx.strokeStyle = "#0ea5e9"
    ctx.lineWidth = 0.08
    ctx.beginPath()
    ctx.moveTo(0, 0)
    ctx.lineTo(0.6, 0)
    ctx.moveTo(0, 0)
    ctx.lineTo(0, 0.4)
    ctx.stroke()
    ctx.fillStyle = "#0369a1"
    ctx.font = "0.28px sans-serif"
    ctx.fillText("x", 0.68, 0.08)
    ctx.fillText("y", 0.08, 0.52)
    ctx.restore()
  }

  useEffect(redraw, [project, draft, start, goal, path, robot, mapWidth, mapHeight])

  const makeRect = (a: Point, b: Point): Rect => ({
    id: `zone-${Date.now()}`,
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y),
  })

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return
    const point = toWorld(event, project, canvasRef.current)
    if (mode === "zone") {
      draftStart.current = point
      setDraft({ id: "draft", ...point, width: 0, height: 0 })
      event.currentTarget.setPointerCapture(event.pointerId)
    } else if (mode === "start" || mode === "goal") {
      onSelectPoint(point)
    }
  }

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current || !draftStart.current) return
    setDraft(makeRect(draftStart.current, toWorld(event, project, canvasRef.current)))
  }

  const onPointerUp = () => {
    if (draft && draft.width > 0.02 && draft.height > 0.02) onChangeZone([...project.forbidden_zones, draft])
    draftStart.current = null
    setDraft(null)
  }

  const style = useMemo(() => ({
    width: mapWidth * pixelsPerMeter,
    height: mapHeight * pixelsPerMeter,
  }), [mapWidth, mapHeight, pixelsPerMeter])

  return (
    <canvas
      ref={canvasRef}
      className="map-canvas"
      width={project.width}
      height={project.height}
      style={style}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    />
  )
}
