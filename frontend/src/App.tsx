import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getProject,
  listPlans,
  listVersions,
  rejudgePlan,
  restoreVersion,
  saveProject,
  saveVersion,
  timedPlan,
  validateProject,
} from "./api";
import { MapCanvas } from "./MapCanvas";
import type {
  Checkpoint,
  MapState,
  PlanSummary,
  Project,
  RejudgeResult,
  TimedPlanResult,
  ValidationResult,
  Version,
} from "./types";

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
  const [timed, setTimed] = useState<TimedPlanResult | null>(null);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [comparison, setComparison] = useState<RejudgeResult | null>(null);
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
      listPlans(loaded.id).then(setPlans);
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
    setTimed(null);
    setComparison(null);
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
    setTimed(null);
    setComparison(null);
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
    setTimed(null);
    setComparison(null);
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
      setTimed(null);
      setComparison(null);
      setVersions(await listVersions(created.id));
      setPlans(await listPlans(created.id));
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

  async function handleTimedPlan() {
    if (!project || !state) return;
    setBusy(true);
    try {
      await persist();
      const planned = await timedPlan(project.id, state, `方案 ${plans.length + 1}`);
      setTimed(planned);
      setComparison(null);
      setPlans(await listPlans(project.id));
      setMessage("时间安排与地图版本已随方案保存。");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRejudge(planId: number) {
    if (!project || !state) return;
    setBusy(true);
    try {
      const compared = await rejudgePlan(planId, state.checkpoints);
      setComparison(compared);
      setPlans(await listPlans(project.id));
      setMessage("已按修改后的时间窗口重判：保留几何路线 vs 调整等待位置。");
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
    setTimed(null);
    setComparison(null);
    setMessage(`已恢复：${version.name}`);
  }

  function removeZone(id: string) {
    if (!state) return;
    setState({ ...state, forbidden_zones: state.forbidden_zones.filter((zone) => zone.id !== id) });
  }

  function removeNoWaitZone(id: string) {
    if (!state) return;
    setState({ ...state, no_wait_zones: state.no_wait_zones.filter((zone) => zone.id !== id) });
  }

  function updateCheckpoint(id: string, patch: Partial<Checkpoint>) {
    if (!state) return;
    setState({
      ...state,
      checkpoints: state.checkpoints.map((cp) => (cp.id === id ? { ...cp, ...patch } : cp)),
    });
  }

  function removeCheckpoint(id: string) {
    if (!state) return;
    setState({ ...state, checkpoints: state.checkpoints.filter((cp) => cp.id !== id) });
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
              <h2>姿态与速度</h2>
              <label>起点朝向（度）<input type="number" step="15" value={state.start_orientation} onChange={(e) => setState({ ...state, start_orientation: Number(e.target.value) })} /></label>
              <label>终点朝向（度）<input type="number" step="15" value={state.end_orientation} onChange={(e) => setState({ ...state, end_orientation: Number(e.target.value) })} /></label>
              <label>线速度（米/秒）<input type="number" step="0.1" min="0.05" value={state.linear_speed} onChange={(e) => setState({ ...state, linear_speed: Number(e.target.value) })} /></label>
              <label>角速度（度/秒）<input type="number" step="15" min="15" value={state.angular_speed} onChange={(e) => setState({ ...state, angular_speed: Number(e.target.value) })} /></label>
              <p className="hint">同位转向会校验长方形扫掠；位置到达不等于姿态到达。</p>
            </section>
            <section>
              <h2>检查点（按顺序）</h2>
              {state.checkpoints.length === 0 && <p className="hint">在画布上选择“检查点”后点击添加。</p>}
              {state.checkpoints.map((cp, index) => (
                <div key={cp.id} className="checkpoint-card">
                  <strong>#{index + 1} {cp.name}</strong>
                  <label>名称<input value={cp.name} onChange={(e) => updateCheckpoint(cp.id, { name: e.target.value })} /></label>
                  <label>朝向（度）<input type="number" step="15" value={cp.orientation} onChange={(e) => updateCheckpoint(cp.id, { orientation: Number(e.target.value) })} /></label>
                  <label>最早（秒）<input type="number" step="1" min="0" value={cp.earliest} onChange={(e) => updateCheckpoint(cp.id, { earliest: Number(e.target.value) })} /></label>
                  <label>最晚（秒）<input type="number" step="1" min="0" value={cp.latest} onChange={(e) => updateCheckpoint(cp.id, { latest: Number(e.target.value) })} /></label>
                  <small>({cp.x.toFixed(2)}, {cp.y.toFixed(2)})</small>
                  <button onClick={() => removeCheckpoint(cp.id)}>删除</button>
                </div>
              ))}
            </section>
            <section>
              <h2>禁止停留通道</h2>
              {state.no_wait_zones.length === 0 && <p className="hint">用“禁止停留通道”工具在画布上框选。</p>}
              {state.no_wait_zones.map((zone) => (
                <div key={zone.id} className="zone-row">
                  <span>{zone.width.toFixed(2)} × {zone.height.toFixed(2)} m</span>
                  <button onClick={() => removeNoWaitZone(zone.id)}>删除</button>
                </div>
              ))}
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
            </section>
            <section>
              <h2>带时间检查点验证</h2>
              <button disabled={busy} onClick={handleTimedPlan}>按时间窗口规划</button>
              {timed && (
                <div className={timed.status === "ok" ? "pass" : "fail"}>
                  <strong>{timed.status_label}</strong>
                  <p>{timed.reason}</p>
                  {timed.total_time !== undefined && <p>总耗时：{timed.total_time.toFixed(2)} 秒</p>}
                  {timed.schedule && timed.schedule.length > 0 && (
                    <table className="schedule">
                      <thead>
                        <tr><th>节点</th><th>到达</th><th>等待</th><th>窗口</th></tr>
                      </thead>
                      <tbody>
                        {timed.schedule.map((entry, index) => (
                          <tr key={index}>
                            <td>{entry.name}</td>
                            <td>{entry.arrival.toFixed(2)}s</td>
                            <td>{entry.wait ? `${entry.wait.toFixed(2)}s` : "-"}</td>
                            <td>{entry.earliest !== undefined ? `[${entry.earliest}, ${entry.latest}]s` : "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <small>算法版本：{timed.algorithm}</small>
                </div>
              )}
            </section>
            <section>
              <h2>方案</h2>
              <ul className="versions">
                {plans.map((plan) => (
                  <li key={plan.id}>
                    <span>{plan.name}<small>{plan.status} · {new Date(plan.created_at).toLocaleString()}</small></span>
                    <button disabled={busy} onClick={() => handleRejudge(plan.id)}>重判对比</button>
                  </li>
                ))}
              </ul>
              {comparison && (
                <div className="comparison">
                  <div className={comparison.kept.status === "ok" ? "pass" : "fail"}>
                    <strong>保留几何路线：{comparison.kept.status_label}</strong>
                    <p>{comparison.kept.reason}</p>
                  </div>
                  <div className={comparison.adjusted.status === "ok" ? "pass" : "fail"}>
                    <strong>调整等待位置：{comparison.adjusted.status_label}</strong>
                    <p>{comparison.adjusted.reason}</p>
                  </div>
                </div>
              )}
            </section>
          </aside>
          <div className="canvas-wrap">
            <MapCanvas project={project} state={state} scale={scale} result={result} timed={timed} onChange={setState} />
            <p className="message">{message}</p>
          </div>
        </div>
      )}
    </main>
  );
}
