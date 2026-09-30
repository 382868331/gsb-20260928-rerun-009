import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getProject,
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

  async function importProject(sample = false) {
    setBusy(true);
    try {
      const created = await createProject({
        name,
        resolution,
        file: sample ? undefined : file ?? undefined,
      });
      window.history.replaceState(null, "", `?project=${created.id}`);
      setProject(created);
      setStateDirect(created.state);
      setHistory({ past: [], future: [] });
      setResult(null);
      setVersions(await listVersions(created.id));
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
      setResult(await validateProject(project.id, state));
      setMessage("验证基于服务器保存的栅格和当前米制参数，不受页面缩放影响。");
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

  function removeCheckpoint(index: number) {
    if (!state) return;
    setState({
      ...state,
      checkpoints: state.checkpoints.filter((_, item) => item !== index),
    });
  }

  function updateCheckpoint(index: number, orientation: number | null) {
    if (!state) return;
    setState({
      ...state,
      checkpoints: state.checkpoints.map((checkpoint, item) =>
        item === index ? { ...checkpoint, orientation } : checkpoint
      ),
    });
  }

  const statusLabels: Record<string, string> = {
    passable: "可以到达且满足全部姿态要求",
    "orientation-unreachable": "位置可达但无法调整到指定朝向",
    "passage-blocked": "通道过窄，整机外廓无法通过",
    "route-not-found": "尚未找到无碰撞路线",
    "start-invalid": "起点处机器人外廓与障碍冲突",
  };

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
            <button disabled={busy} onClick={() => importProject(true)}>使用宽窄门示例</button>
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
              <p className="hint">按完整矩形外廓膨胀障碍；允许水平或竖直穿门，不把机器人降成中心点。</p>
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
              <h2>起点姿态</h2>
              <label>起点朝向（度）<input type="number" step="5" value={Math.round(((state.start?.orientation ?? 0) * 180) / Math.PI)} onChange={(e) => state.start && setState({ ...state, start: { ...state.start, orientation: (Number(e.target.value) * Math.PI) / 180 } })} /></label>
              <label>终点朝向（度，空=不要求）<input type="number" step="5" value={state.end?.orientation == null ? "" : Math.round((state.end.orientation * 180) / Math.PI)} onChange={(e) => state.end && setState({ ...state, end: { ...state.end, orientation: e.target.value === "" ? null : (Number(e.target.value) * Math.PI) / 180 } })} /></label>
              <p className="hint">路线完成同时要求终点位置和朝向；同点转向按整个旋转扫掠检查碰撞。</p>
            </section>
            <section>
              <h2>朝向检查点</h2>
              {state.checkpoints.length === 0 && <p className="hint">选择“检查点”工具后在画布点击；留空朝向表示只经过该位置。</p>}
              {state.checkpoints.map((checkpoint, index) => (
                <div key={index} className="zone-row">
                  <span>
                    #{index + 1}（{checkpoint.x.toFixed(1)}, {checkpoint.y.toFixed(1)}）
                    <input
                      type="number"
                      step="5"
                      placeholder="朝向°（空=任意）"
                      value={checkpoint.orientation == null ? "" : Math.round((checkpoint.orientation * 180) / Math.PI)}
                      onChange={(event) => updateCheckpoint(
                        index,
                        event.target.value === "" ? null : (Number(event.target.value) * Math.PI) / 180
                      )}
                    />
                  </span>
                  <button onClick={() => removeCheckpoint(index)}>删除</button>
                </div>
              ))}
            </section>
            <section>
              <h2>通过性</h2>
              <button disabled={busy} onClick={handleValidate}>检查起点到终点</button>
              {result && (
                <div className={result.passable ? "pass" : "fail"}>
                  <strong>{statusLabels[result.status] ?? (result.passable ? "可以通过" : "不能通过")}</strong>
                  <p>{result.reason}</p>
                  {result.failed_stage && <small>失败阶段：{result.failed_stage}</small>}
                  <small>算法版本：{result.algorithm}</small>
                </div>
              )}
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
