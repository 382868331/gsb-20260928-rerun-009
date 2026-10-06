import { useEffect, useState } from "react";
import {
  adoptJointCandidate,
  jointPlan,
  listJointCandidates,
} from "./api";
import type {
  ForbiddenZone,
  JointPlanResult,
  JointTask,
  MapState,
  SavedJointCandidate,
} from "./types";

function defaultTasks(state: MapState): JointTask[] {
  const reversed: MapState = {
    ...state,
    start: state.end,
    end: state.start,
    start_orientation: (state.start_orientation + Math.PI) % (2 * Math.PI),
  };
  return [
    {
      id: "A",
      state,
      committed: false,
      departure: null,
      adjust_start: 0,
      adjust_end: 20,
      adjust_step: 1,
    },
    {
      id: "B",
      state: reversed,
      committed: true,
      departure: 0,
      adjust_start: 0,
      adjust_end: 0,
      adjust_step: 1,
    },
  ];
}

export function JointPlanner({
  projectId,
  state,
}: {
  projectId: number;
  state: MapState;
}) {
  const [tasksText, setTasksText] = useState("");
  const [sharedText, setSharedText] = useState("[]");
  const [result, setResult] = useState<JointPlanResult | null>(null);
  const [candidates, setCandidates] = useState<SavedJointCandidate[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listJointCandidates(projectId).then(setCandidates).catch(() => undefined);
  }, [projectId]);

  function fillTemplate() {
    setTasksText(JSON.stringify(defaultTasks(state), null, 2));
  }

  async function run() {
    setBusy(true);
    setMessage("");
    try {
      const tasks = JSON.parse(tasksText) as JointTask[];
      const shared = JSON.parse(sharedText) as ForbiddenZone[];
      const planned = await jointPlan(projectId, tasks, shared);
      setResult(planned);
      setCandidates(await listJointCandidates(projectId));
    } catch (error) {
      setResult(null);
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function adopt(candidate: SavedJointCandidate, objective: string) {
    setBusy(true);
    setMessage("");
    try {
      const tasks = JSON.parse(tasksText) as JointTask[];
      const shared = JSON.parse(sharedText) as ForbiddenZone[];
      const adopted = await adoptJointCandidate(candidate.id, objective, tasks, shared);
      setMessage("已采用候选 " + objective + "（采用前已重新核对地图与承诺）。");
      setCandidates(await listJointCandidates(projectId));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>联合出发时刻规划</h2>
      <p className="hint">
        多条任务共享限时封闭区与冲突区；已承诺任务出发时刻不可移动，其余在可调范围内滑动。
        候选只保存规划结果，不回写任务；采用前会重新核对地图与承诺。
      </p>
      <button onClick={fillTemplate}>用当前状态生成模板</button>
      <label>
        任务（JSON）
        <textarea
          rows={10}
          value={tasksText}
          onChange={(event) => setTasksText(event.target.value)}
          placeholder="点击上方按钮生成模板"
        />
      </label>
      <label>
        共享冲突区（JSON）
        <textarea
          rows={3}
          value={sharedText}
          onChange={(event) => setSharedText(event.target.value)}
        />
      </label>
      <button onClick={run} disabled={busy || !tasksText}>
        联合规划
      </button>
      {message && <p className="message">{message}</p>}
      {result && (
        <div className="result">
          <strong>
            {result.status}
            {result.search_completed === false ? "（预算不足，未证明）" : ""}
          </strong>
          {result.reason && <p>{result.reason}</p>}
          {result.candidates.map((candidate) => (
            <div key={candidate.objective} className="zone-row">
              <span>
                {candidate.objective === "min_total_wait" ? "总等待最少" : "最晚完成最小"}
                ：等待 {candidate.total_wait.toFixed(2)}s，完成 {candidate.makespan.toFixed(2)}s
              </span>
              <small>
                {candidate.tasks
                  .map((task) => task.id + "@" + task.departure.toFixed(1) + "s")
                  .join("，")}
              </small>
            </div>
          ))}
        </div>
      )}
      {candidates.length > 0 && (
        <div>
          <h3>已保存候选</h3>
          {candidates.map((candidate) => (
            <div key={candidate.id} className="zone-row">
              <span>
                #{candidate.id} {candidate.status}
                {candidate.adopted ? "（已采用）" : ""}
                {candidate.stale
                  ? "（已失效：" + candidate.stale_reasons.join("、") + "）"
                  : ""}
              </span>
              {!candidate.stale && !candidate.adopted &&
                candidate.result.candidates.map((option) => (
                  <button
                    key={option.objective}
                    onClick={() => adopt(candidate, option.objective)}
                    disabled={busy}
                  >
                    采用{option.objective === "min_total_wait" ? "总等待最少" : "最晚完成最小"}
                  </button>
                ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
