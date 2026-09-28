import { useEffect, useMemo, useState } from "react"
import { listProjects, rollbackProject, saveProject, uploadProject, verifyProject } from "./api"
import { MapCanvas } from "./MapCanvas"
import type { CellPose, Point, Project, Rect } from "./types"

type Mode = "zone" | "start" | "goal" | "pan"

function initialPoint(project: Project | null, kind: "start" | "goal"): Point {
  const saved = project?.last_verification
  if (saved) return kind === "start" ? saved.start : saved.goal
  const y = project ? project.height * project.resolution / 2 : 0
  return { x: kind === "start" ? 1.2 : project ? project.width * project.resolution - 1.2 : 0, y }
}

export function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [mode, setMode] = useState<Mode>("zone")
  const [robotWidth, setRobotWidth] = useState(0.5)
  const [robotLength, setRobotLength] = useState(1)
  const [start, setStart] = useState<Point>({ x: 1.2, y: 3 })
  const [goal, setGoal] = useState<Point>({ x: 8.8, y: 3 })
  const [path, setPath] = useState<CellPose[]>([])
  const [scale, setScale] = useState(45)
  const [resolution, setResolution] = useState(0.05)
  const [message, setMessage] = useState("")
  const [history, setHistory] = useState<{ origin: Project["origin"]; forbidden_zones: Rect[] }[]>([])

  useEffect(() => {
    listProjects().then(items => {
      setProjects(items)
      if (items[0]) {
        setProject(items[0])
        setStart(initialPoint(items[0], "start"))
        setGoal(initialPoint(items[0], "goal"))
        setPath(items[0].last_verification?.path ?? [])
      }
    }).catch(error => setMessage(String(error)))
  }, [])

  const imageUrl = useMemo(() => project ? `${project.image_path}?v=${project.revisions.length}` : "", [project])

  const pushHistory = (nextProject: Project) => {
    if (!project) return
    setHistory(items => [...items.slice(-49), { origin: project.origin, forbidden_zones: project.forbidden_zones }])
    setProject(nextProject)
  }

  const undo = () => {
    const previous = history.at(-1)
    if (!project || !previous) return
    setHistory(items => items.slice(0, -1))
    const next = { ...project, origin: previous.origin, forbidden_zones: previous.forbidden_zones }
    setProject(next)
    saveProject(next).catch(error => setMessage(String(error)))
  }

  const onUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    const created = await uploadProject(file, resolution, file.name.replace(/\.[^.]+$/, ""))
    const initial = await saveProject(created, "初始版本")
    setProject(initial)
    setProjects(items => [initial, ...items])
    setStart(initialPoint(initial, "start"))
    setGoal(initialPoint(initial, "goal"))
    setPath([])
    setHistory([])
  }

  const updateZones = (zones: Rect[]) => {
    if (!project) return
    pushHistory({ ...project, forbidden_zones: zones })
  }

  const updateOrigin = (field: "x" | "y" | "yaw", value: number) => {
    if (!project) return
    pushHistory({ ...project, origin: { ...project.origin, [field]: value } })
  }

  const persist = async (label?: string) => {
    if (!project) return
    const saved = await saveProject(project, label)
    setProject(saved)
    setProjects(items => items.map(item => item.id === saved.id ? saved : item))
    setMessage(label ? `${label} 已保存` : "修改已保存")
  }

  const verify = async () => {
    if (!project) return
    setMessage("正在用配置空间搜索完整机器人足迹…")
    const response = await verifyProject(project, { width: robotWidth, length: robotLength }, start, goal)
    setProject(response.project)
    setPath(response.result.path)
    setMessage(response.result.reason)
  }

  const rollback = async (revisionId: number) => {
    if (!project) return
    const rolled = await rollbackProject(project, revisionId)
    setProject(rolled)
    setMessage(`已回滚到版本 ${revisionId}`)
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h1>机器人地图编辑平台</h1>
        <section className="card">
          <h2>导入人工栅格地图</h2>
          <label>分辨率（米/像素）<input type="number" step="0.01" min="0.01" value={resolution} onChange={e => setResolution(Number(e.target.value))} /></label>
          <input type="file" accept="image/png,image/jpeg,image/bmp" onChange={onUpload} />
          {projects.length > 0 && <select value={project?.id} onChange={e => setProject(projects.find(item => item.id === e.target.value) ?? null)}>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
        </section>

        {project && <>
          <section className="card">
            <h2>编辑</h2>
            <div className="button-row">
              <button className={mode === "zone" ? "active" : ""} onClick={() => setMode("zone")}>画禁行区</button>
              <button className={mode === "start" ? "active" : ""} onClick={() => setMode("start")}>起点</button>
              <button className={mode === "goal" ? "active" : ""} onClick={() => setMode("goal")}>终点</button>
            </div>
            <div className="button-row">
              <button onClick={undo} disabled={history.length === 0}>撤回</button>
              <button onClick={() => persist("修订版本")}>保存版本</button>
            </div>
            <button className="danger" onClick={() => updateZones(project.forbidden_zones.slice(0, -1))}>删除最后禁行区</button>
          </section>

          <section className="card">
            <h2>地图原点（真实坐标）</h2>
            <label>X（米）<input type="number" step="0.1" value={project.origin.x} onChange={e => updateOrigin("x", Number(e.target.value))} /></label>
            <label>Y（米）<input type="number" step="0.1" value={project.origin.y} onChange={e => updateOrigin("y", Number(e.target.value))} /></label>
            <label>Yaw（弧度）<input type="number" step="0.05" value={project.origin.yaw} onChange={e => updateOrigin("yaw", Number(e.target.value))} /></label>
            <button onClick={() => persist()}>应用原点</button>
          </section>

          <section className="card">
            <h2>机器人尺寸</h2>
            <label>长度（米）<input type="number" step="0.05" min="0.05" value={robotLength} onChange={e => setRobotLength(Number(e.target.value))} /></label>
            <label>宽度（米）<input type="number" step="0.05" min="0.05" value={robotWidth} onChange={e => setRobotWidth(Number(e.target.value))} /></label>
            <label>起点 X<input type="number" step="0.1" value={start.x} onChange={e => setStart({ ...start, x: Number(e.target.value) })} /></label>
            <label>起点 Y<input type="number" step="0.1" value={start.y} onChange={e => setStart({ ...start, y: Number(e.target.value) })} /></label>
            <label>终点 X<input type="number" step="0.1" value={goal.x} onChange={e => setGoal({ ...goal, x: Number(e.target.value) })} /></label>
            <label>终点 Y<input type="number" step="0.1" value={goal.y} onChange={e => setGoal({ ...goal, y: Number(e.target.value) })} /></label>
            <button className="primary" onClick={verify}>检查整台机器人能否通过</button>
          </section>

          <section className="card">
            <h2>版本</h2>
            <div className="revision-list">
              {project.revisions.map(revision => <div key={revision.id} className="revision"><span>{revision.label}<small>{new Date(revision.created_at).toLocaleString()} · {revision.forbidden_zones.length} 个禁行区</small></span><button onClick={() => rollback(revision.id)}>恢复</button></div>)}
            </div>
          </section>
        </>}
      </aside>

      <main className="workspace">
        <div className="toolbar">
          <strong>{project?.name ?? "未导入地图"}</strong>
          <span>深色像素=障碍；红色=人工禁行区</span>
          <label>显示比例<input type="range" min="20" max="120" value={scale} onChange={e => setScale(Number(e.target.value))} /></label>
          <span>{scale} px/米（不参与计算）</span>
        </div>
        {message && <div className="message">{message}</div>}
        {project && <div className="canvas-scroll"><MapCanvas project={project} imageUrl={imageUrl} mode={mode} start={start} goal={goal} path={path} robot={{ width: robotWidth, length: robotLength }} pixelsPerMeter={scale} onChangeZone={updateZones} onSelectPoint={mode === "start" ? setStart : setGoal} /></div>}
      </main>
    </div>
  )
}
