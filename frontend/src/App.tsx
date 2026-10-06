import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getProject,
  listPlans,
  listVersions,
  replayPlan,
  restoreVersion,
  saveProject,
  saveVersion,
  validateProject,
} from "./api";
import { MapCanvas } from "./MapCanvas";
import { JointPlanner } from "./JointPlanner";
import type {
  MapState,
  PlanReplay,
  Project,
  SavedPlan,
  ValidationResult,
  Version,
} from "./types";

const STORAGE_KEY = "robot-map-editor-draft";

type EditHistory = { past: MapState[]; future: MapState[] };

const STATE_DEFAULTS: MapState = {
  origin: { x: 0, y: 0 },
  forbidden_zones: [],
  timed_zones: [],
  waiting_zones: [],
  checkpoints: [],
  start: null,
  end: null,
  robot_width: 0.5,
  robot_length: 0.8,
  start_orientation: 0,
  end_orientation: null,
  linear_speed: 0.5,
  angular_speed: 0.5,
  start_time: 0,
};

function normalizeState(raw: Partial<MapState> | null | undefined): MapState {
  return { ...STATE_DEFAULTS, ...(raw ?? {}) };
}

function legacyNotice(defaults: string[] | undefined): string {
  if (!defaults || defaults.length === 0) return "";
  return (
    "旧工程缺少新增字段（" +
    defaults.join("、") +
    "），已按默认值补齐；检查点与封路尚未验证，请重新检查后再保存。"
  );
}

function replayNotice(replay: PlanReplay): string {
  if (!replay.result_complete) {
    return (
      "这是旧版保存的报告，缺少字段：" +
      replay.missing_fields.join("、") +
      "；缺失字段不会被当成新的通过或失败，请重新执行检查。"
    );
  }
  if (replay.map_changed) {
    return (
      "该报告基于旧地图修订（"
      + (replay.saved_map_version ?? "未知修订")
      + "），与当前地图（"
      + (replay.current_map_version ?? "未知修订")
      + "）不同；以下原理由仅在保存时快照上重放："
      + (replay.saved_reason ?? "（无）")
      + "。"
    );
  }
  if (replay.map_snapshot_missing) {
    return "旧报告没有保存地图快照，无法确认地图是否变化；已按保存时的状态参数重放。";
  }
  return replay.consistent
    ? "重开保存方案后用保存时的地图与参数逐段重放，结论、理由与保存时一致。"
    : "保存方案重放结论与保存时不一致，结果可能来自不同算法版本，请重新检查。";
}

function savedPlanNotice(plan: SavedPlan | null): string {
  if (!plan) return "";
  if (!plan.result_complete) {
    return (
      "旧版报告字段不完整（缺少：" +
      plan.missing_fields.join("、") +
      "），不能据此判断通过或失败，请重新执行检查。"
    );
  }
  if (plan.map_changed) {
    return "地图已变化：以下为旧报告，结论仅对保存时的地图修订与机器人参数有效。";
  }
  if (plan.map_snapshot_missing) {
    return "该旧报告缺少地图快照，无法核验地图是否变化。";
  }
  return "已从保存记录恢复报告：结论、理由、路线与逐段时序均为保存时结果。";
}

function segmentLabel(segment: import("./types").Segment): string {
  const when =
    segment.time !== undefined
      ? "t=" + segment.time.toFixed(2) + "s"
      : (segment.start ?? 0).toFixed(2) + "→" + (segment.end ?? 0).toFixed(2) + "s";
  const labels: Record<string, string> = {
    start: "起点就位",
    arrive: "直行到达",
    wait_depart_arrive: "等待后出发到达",
    turn: "原地转向",
    wait: "安全位等待",
    checkpoint: "通过检查点",
  };
  let detail = labels[segment.type] ?? segment.type;
  if (segment.type === "checkpoint") {
    detail += " #" + segment.order + "（窗口 " + segment.window_start + "–" + segment.window_end + "s）";
  }
  return detail + " @ " + when;
}

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
  const [keepGeometry, setKeepGeometry] = useState(false);
  const [savedPlan, setSavedPlan] = useState<SavedPlan | null>(null);

  const projectId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("project");
  }, []);

  useEffect(() => {
    if (!projectId) return;
    getProject(Number(projectId)).then((loaded) => {
      setProject(loaded);
      let initial = normalizeState(loaded.state);
      let notice = legacyNotice(loaded.state_defaults);
      try {
        const draftRaw = localStorage.getItem(STORAGE_KEY);
        if (draftRaw) {
          const draft = JSON.parse(draftRaw);
          if (draft.projectId === loaded.id && draft.state) {
            initial = normalizeState(draft.state);
            notice =
              "已找回刷新前未保存的编辑草稿。" + (notice ? " " + notice : "");
          }
        }
      } catch {
        // 草稿损坏时忽略，使用服务器状态
      }
      setStateDirect(initial);
      setResolution(loaded.resolution);
      listVersions(loaded.id).then(setVersions);
      listPlans(loaded.id)
        .then((plans) => {
          if (plans.length > 0) {
            setSavedPlan(plans[0]);
            setResult(plans[0].result);
          }
        })
        .catch(() => undefined);
      if (notice) setMessage(notice);
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
      setStateDirect(normalizeState(created.state));
      setHistory({ past: [], future: [] });
      setResult(null);
      setSavedPlan(null);
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
      const validation = await validateProject(
        project.id,
        state,
        keepGeometry,
        result?.path ?? null
      );
      setResult(validation);
      const plans = await listPlans(project.id);
      setSavedPlan(plans.length > 0 ? plans[0] : null);
      setMessage(
        validation.replay?.ok
          ? "逐段重放通过：到达、转向、等待与检查点事件均与封路/窗口语义一致。"
          : "验证基于服务器保存的栅格和当前米制参数，不受页面缩放影响。"
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReplay() {
    if (!project) return;
    try {
      const plans = await listPlans(project.id);
      if (!plans.length) {
        setMessage("还没有已保存的核对方案，请先执行一次检查。");
        return;
      }
      const plan = plans[0];
      const replay = await replayPlan(plans[0].id);
      setSavedPlan(plan);
      setResult(plan.result);
      setMessage(
        replayNotice(replay)
      );
    } catch (error) {
      setMessage((error as Error).message);
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
    setStateDirect(normalizeState(restored.state));
    setResult(null);
    const notice = legacyNotice(restored.state_defaults);
    setMessage(`已恢复：${version.name}` + (notice ? `。 ${notice}` : ""));
  }

  function removeZone(id: string) {
    if (!state) return;
    setState({ ...state, forbidden_zones: state.forbidden_zones.filter((zone) => zone.id !== id) });
  }

  function updateTimedZone(id: string, patchValue: Partial<MapState["timed_zones"][number]>) {
    if (!state) return;
    setState({
      ...state,
      timed_zones: state.timed_zones.map((zone) =>
        zone.id === id ? { ...zone, ...patchValue } : zone
      ),
    });
  }

  function updateCheckpoint(id: string, patchValue: Partial<MapState["checkpoints"][number]>) {
    if (!state) return;
    setState({
      ...state,
      checkpoints: state.checkpoints.map((cp) =>
        cp.id === id ? { ...cp, ...patchValue } : cp
      ),
    });
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
              <h2>限时封闭区</h2>
              {state.timed_zones.length === 0 && (
                <p className="hint">橙色虚线区域在 [封闭开始, 封闭结束) 内禁止进入；开始时刻须已撤离，结束时刻起可进入。</p>
              )}
              {state.timed_zones.map((zone) => (
                <div key={zone.id} className="zone-row timed-row">
                  <label>
                    封闭开始 s
                    <input
                      type="number"
                      step="0.5"
                      min="0"
                      value={zone.close_start}
                      onChange={(event) =>
                        updateTimedZone(zone.id, { close_start: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    开放时刻 s
                    <input
                      type="number"
                      step="0.5"
                      min="0"
                      value={zone.close_end}
                      onChange={(event) =>
                        updateTimedZone(zone.id, { close_end: Number(event.target.value) })
                      }
                    />
                  </label>
                  <button
                    onClick={() =>
                      setState({
                        ...state,
                        timed_zones: state.timed_zones.filter((item) => item.id !== zone.id),
                      })
                    }
                  >
                    删除
                  </button>
                </div>
              ))}
            </section>
            <section>
              <h2>检查点</h2>
              {state.checkpoints.length === 0 && (
                <p className="hint">在画布上点选检查点；容差留空时保存与核对使用同一缺省值（1.5 格）。</p>
              )}
              {state.checkpoints.map((cp) => (
                <div key={cp.id} className="zone-row timed-row">
                  <span>
                    #{cp.order} ({cp.x.toFixed(2)}, {cp.y.toFixed(2)})
                  </span>
                  <label>
                    窗口起 s
                    <input
                      type="number"
                      step="0.5"
                      value={cp.window_start}
                      onChange={(event) =>
                        updateCheckpoint(cp.id, { window_start: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    窗口止 s
                    <input
                      type="number"
                      step="0.5"
                      value={cp.window_end}
                      onChange={(event) =>
                        updateCheckpoint(cp.id, { window_end: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label>
                    容差 m（可空）
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      value={cp.tolerance ?? ""}
                      placeholder="缺省"
                      onChange={(event) =>
                        updateCheckpoint(cp.id, {
                          tolerance: event.target.value === "" ? null : Number(event.target.value),
                        })
                      }
                    />
                  </label>
                  <button
                    onClick={() =>
                      setState({
                        ...state,
                        checkpoints: state.checkpoints
                          .filter((item) => item.id !== cp.id)
                          .map((item, index) => ({ ...item, order: index })),
                      })
                    }
                  >
                    删除
                  </button>
                </div>
              ))}
            </section>
            <section>
              <h2>通过性</h2>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={keepGeometry}
                  onChange={(event) => setKeepGeometry(event.target.checked)}
                />
                先保留原几何路线重排时间，再比较重新选路
              </label>
              <button disabled={busy} onClick={handleValidate}>检查起点到终点</button>
              <button onClick={handleReplay}>重放最近保存方案</button>
              {savedPlan && result && (
                <p className={savedPlan.map_changed || !savedPlan.result_complete ? "hint warn" : "hint"}>
                  {savedPlanNotice(savedPlan)}
                  {savedPlan.map_version ? "（报告地图修订：" + savedPlan.map_version + "）" : ""}
                </p>
              )}
              {result && (
                <div className={result.passable === true ? "pass" : result.passable === false ? "fail" : "unknown"}>
                  <strong>
                    {result.passable === true
                      ? "可以通过"
                      : result.passable === false
                        ? "不能通过"
                        : "结论字段缺失"}
                  </strong>
                  <p>{result.reason ?? "旧版保存记录未包含判断理由，无法恢复当时的结论。"}</p>
                  {result.search_completed === false && (
                    <p className="hint warn">搜索预算已耗尽（search_incomplete）：可行性尚未证明，既不是“可以通过”也不是“不能通过”。</p>
                  )}
                  {result.retain_geometry && (
                    <p className="hint">
                      原几何路线：{result.retain_geometry.status}
                      {result.retain_geometry.arrival_time !== undefined
                        ? "（到达 " + result.retain_geometry.arrival_time.toFixed(2) + " s）"
                        : ""}
                      。{result.retain_geometry.reason}
                    </p>
                  )}
                  {result.segments && result.segments.length > 0 && (
                    <details>
                      <summary>逐段过程（{result.segments.length} 段）</summary>
                      <ol className="segments">
                        {result.segments.map((segment, index) => (
                          <li key={index}>
                            {segmentLabel(segment)}
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                  {result.replay && (
                    <small>
                      重放校验：{result.replay.ok ? "全部通过" : "存在矛盾"}
                      {result.replay.missing_checkpoints.length > 0
                        ? "；缺检查点 " + result.replay.missing_checkpoints.join(", ")
                        : ""}
                    </small>
                  )}
                  {result.map_version && <small>所依据地图修订：{result.map_version}</small>}
                  {result.robot_params && (
                    <small>
                      机器人参数：宽 {String(result.robot_params.robot_width)} m × 长 {String(result.robot_params.robot_length)} m，
                      线速度 {String(result.robot_params.linear_speed)} m/s，角速度 {String(result.robot_params.angular_speed)} rad/s
                    </small>
                  )}
                  <small>算法版本：{result.algorithm ?? "未知（旧报告）"}</small>
                </div>
              )}
            </section>
            <JointPlanner projectId={project.id} state={state} />
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
