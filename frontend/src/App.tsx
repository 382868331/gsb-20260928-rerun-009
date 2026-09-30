import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getProject,
  listChecks,
  listVersions,
  restoreVersion,
  saveProject,
  saveVersion,
  validateProject,
} from "./api";
import { MapCanvas } from "./MapCanvas";
import type { MapState, Project, ValidationResult, Version } from "./types";

const STORAGE_KEY = "robot-map-editor-draft";

type EditHistory = { past: MapState[]; future: MapState[] };

export default function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [state, setStateDirect] = useState<MapState | null>(null);
  const [history, setHistory] = useState<EditHistory>({ past: [], future: [] });
  const [scale, setScale] = useState(24);
  const [resolution, setResolution] = useState(0.1);
  const [name, setName] = useState("门禁通道工程");
  const [file, setFile] = useState<File | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [checks, setChecks] = useState<import("./types").CheckRecord[]>([]);
  const [versionName, setVersionName] = useState("");
  const [result, setResult] = useState<ValidationResult | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const projectId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("project");
  }, []);

  useEffect(() => {
    if (!projectId) return;
    getProject(Number(projectId)).then((loaded) => {
      setProject(loaded);
      setStateDirect(loaded.state);
      setResolution(loaded.resolution);
      listVersions(loaded.id).then(setVersions);
      listChecks(loaded.id).then(setChecks);
    }).catch((error) => setMessage(error.message));
  }, [projectId]);

  useEffect(() => {
    if (!state || !project) return;
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ projectId: project.id, state, savedAt: new Date().toISOString() })
    );
  }, [project, state]);

  function setState(next: MapState, record = true) {
    if (!state) return;
    if (record) {
      setHistory((old) => ({ past: [...old.past, state].slice(-50), future: [] }));
    }
    setStateDirect(next);
    setResult(null);
  }

  function undo() {
    if (!state || history.past.length === 0) return;
    const previous = history.past[history.past.length - 1];
    setHistory({
      past: history.past.slice(0, -1),
      future: [state, ...history.future],
    });
    setStateDirect(previous);
    setResult(null);
  }

  function redo() {
    if (!state || history.future.length === 0) return;
    const next = history.future[0];
    setHistory({
      past: [...history.past, state],
      future: history.future.slice(1),
    });
    setStateDirect(next);
    setResult(null);
  }

  async function importProject(sample = false, sampleKind = "doors") {
    setBusy(true);
    try {
      const created = await createProject({
        name,
        resolution,
        sampleKind: sample ? sampleKind : undefined,
        file: sample ? undefined : file ?? undefined,
      });
      window.history.replaceState(null, "", `?project=${created.id}`);
      setProject(created);
      setStateDirect(created.state);
      setHistory({ past: [], future: [] });
      setResult(null);
      setVersions(await listVersions(created.id));
      setChecks(await listChecks(created.id));
      setMessage("原图已单独保存，后续修订不会覆盖原图。");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function persist() {
    if (!project || !state) return;
    const saved = await saveProject(project.id, state);
    setProject(saved);
    setVersions(await listVersions(project.id));
  }

  async function handleValidate() {
    if (!project || !state) return;
    setBusy(true);
    try {
      await persist();
      const validation = await validateProject(project.id, state);
      setResult(validation);
      setChecks(await listChecks(project.id));
      setMessage("验证在连续转向中检查整机扫掠外廓；结论独立存档，页面缩放不参与米制计算。");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function createSavedVersion() {
    if (!project || !state) return;
    await saveVersion(project.id, versionName || `版本 ${versions.length + 1}`, state);
    setVersions(await listVersions(project.id));
    setVersionName("");
  }

  async function handleRestore(version: Version) {
    if (!project || !state) return;
    setHistory((old) => ({ past: [...old.past, state], future: [] }));
    const restored = await restoreVersion(version.id);
    setStateDirect(restored.state);
    setResult(null);
    setMessage(`已恢复：${version.name}`);
  }

  function removeZone(id: string) {
    if (!state) return;
    setState({ ...state, forbidden_zones: state.forbidden_zones.filter((zone) => zone.id !== id) });
  }

  return (
    <main>
      <header>
        <div>
          <h1>机器人地图编辑平台</h1>
          <p>先完成可行走区域编辑、原点校准和整机通过性验证</p>
        </div>
        <div className="scale-control">
          <label>显示比例</label>
          <input
            type="range"
            min="10"
            max="48"
            value={scale}
            onChange={(event) => setScale(Number(event.target.value))}
          />
          <strong>{scale} px/m</strong>
        </div>
      </header>

      {!project || !state ? (
        <section className="import-card">
          <h2>导入人工栅格地图</h2>
          <label>工程名<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>分辨率（米/格）<input type="number" step="0.01" min="0.01" value={resolution} onChange={(e) => setResolution(Number(e.target.value))} /></label>
          <label>灰度栅格图（深色=障碍）<input type="file" accept="image/png,image/jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <div className="actions">
            <button disabled={busy || !file} onClick={() => importProject(false)}>导入地图</button>
            <button disabled={busy} onClick={() => importProject(true, "doors")}>使用宽窄门示例</button>
            <button disabled={busy} onClick={() => importProject(true, "turn")}>使用连续转向示例</button>
          </div>
          {message && <p className="message">{message}</p>}
        </section>
      ) : (
        <div className="workspace">
          <aside>
            <section>
              <h2>地图原点</h2>
              <label>X（米）<input type="number" step="0.1" value={state.origin.x} onChange={(e) => setState({ ...state, origin: { ...state.origin, x: Number(e.target.value) } })} /></label>
              <label>Y（米）<input type="number" step="0.1" value={state.origin.y} onChange={(e) => setState({ ...state, origin: { ...state.origin, y: Number(e.target.value) } })} /></label>
            </section>
            <section>
              <h2>机器人外廓</h2>
              <label>宽（米）<input type="number" step="0.05" min="0.01" value={state.robot_width} onChange={(e) => setState({ ...state, robot_width: Number(e.target.value) })} /></label>
              <label>长（米）<input type="number" step="0.05" min="0.01" value={state.robot_length} onChange={(e) => setState({ ...state, robot_length: Number(e.target.value) })} /></label>
              <label>起点朝向（度）<input type="number" step="5" value={state.start_orientation} onChange={(e) => setState({ ...state, start_orientation: Number(e.target.value) })} /></label>
              <label>终点朝向（度）<input type="number" step="5" value={state.end_orientation} onChange={(e) => setState({ ...state, end_orientation: Number(e.target.value) })} /></label>
              <p className="hint">检查起点到终点之间 5° 分段的整机扫掠外廓；180° 可用于反向通过对照。</p>
            </section>
            <section>
              <h2>修订</h2>
              <div className="actions">
                <button onClick={undo} disabled={history.past.length === 0}>撤回</button>
                <button onClick={redo} disabled={history.future.length === 0}>重做</button>
                <button onClick={() => persist()}>保存</button>
              </div>
              <label>版本名<input value={versionName} onChange={(e) => setVersionName(e.target.value)} placeholder="例如 增加围栏" /></label>
              <button onClick={createSavedVersion}>保存成版本</button>
            </section>
            <section>
              <h2>版本</h2>
              <ul className="versions">
                {versions.map((version) => (
                  <li key={version.id}>
                    <span>{version.name}<small>{new Date(version.created_at).toLocaleString()}</small></span>
                    <button onClick={() => handleRestore(version)}>恢复</button>
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <h2>禁行区</h2>
              {state.forbidden_zones.length === 0 && <p className="hint">在画布上拖拽逻辑由两次点击完成。</p>}
              {state.forbidden_zones.map((zone) => (
                <div key={zone.id} className="zone-row">
                  <span>{zone.width.toFixed(2)} × {zone.height.toFixed(2)} m</span>
                  <button onClick={() => removeZone(zone.id)}>删除</button>
                </div>
              ))}
            </section>
            <section>
              <h2>通过性</h2>
              <button disabled={busy} onClick={handleValidate}>检查起点到终点</button>
              {result && (
                <div className={result.passable ? "pass" : "fail"}>
                  <strong>{result.passable ? "可以通过" : "不能通过"}</strong>
                  <p>{result.reason}</p>
                  <small>算法版本：{result.algorithm}</small>
                </div>
              )}
              <h3>历史检查</h3>
              <ul className="versions">
                {checks.map((check) => (
                  <li key={check.id}>
                    <span>
                      {check.passable ? "通过" : "受阻"}
                      <small>{new Date(check.created_at).toLocaleString()} · {check.algorithm}</small>
                    </span>
                    <button onClick={() => setResult(check.result)}>查看</button>
                  </li>
                ))}
              </ul>
            </section>
          </aside>
          <div className="canvas-wrap">
            <MapCanvas project={project} state={state} scale={scale} result={result} onChange={setState} />
            <p className="message">{message}</p>
          </div>
        </div>
      )}
    </main>
  );
}
