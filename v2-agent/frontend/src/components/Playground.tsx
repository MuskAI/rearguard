import {
  ArrowRight,
  Check,
  Clock3,
  Code2,
  Eye,
  House,
  LogIn,
  Maximize2,
  RefreshCw,
  Sparkles,
  Target,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AccountUser } from "../api";
import AccountMenu from "./AccountMenu";
import HuijianBrand from "./HuijianBrand";

type GameMode = "relaxed" | "timed";

type RealSample = {
  id: string;
  src: string;
};

type AiSample = {
  id: string;
  src: string;
  title: string;
  clues: [string, string, string];
};

type Candidate = {
  id: string;
  src: string;
  isAi: boolean;
  aiSample?: AiSample;
};

interface Props {
  authReady: boolean;
  user: AccountUser | null;
  onHome: () => void;
  onWorkspace: () => void;
  onDeveloper: () => void;
  onLogin: () => void;
  onLogout: () => void;
}

const TOTAL_ROUNDS = 5;
const ROUND_SECONDS = 30;
const BEST_SCORE_KEY = "huijian-playground-best-score";

const REAL_SAMPLES: RealSample[] = [
  { id: "real-car", src: "/playground/samples/sample-01.webp" },
  { id: "real-cat", src: "/playground/samples/sample-03.webp" },
  { id: "real-coffee", src: "/playground/samples/sample-04.webp" },
  { id: "real-dog", src: "/playground/samples/sample-06.webp" },
  { id: "real-forest", src: "/playground/samples/sample-07.webp" },
  { id: "real-fox", src: "/playground/samples/sample-08.webp" },
  { id: "real-landscape", src: "/playground/samples/sample-10.webp" },
  { id: "real-meadow", src: "/playground/samples/sample-11.webp" },
  { id: "real-mountain", src: "/playground/samples/sample-12.webp" },
  { id: "real-plant", src: "/playground/samples/sample-14.webp" },
  { id: "real-retriever", src: "/playground/samples/sample-15.webp" },
  { id: "real-sunset", src: "/playground/samples/sample-17.webp" },
];

const AI_SAMPLES: AiSample[] = [
  {
    id: "ai-cafe",
    src: "/playground/samples/sample-02.webp",
    title: "街角咖啡馆",
    clues: ["菜单字符看似规整，但笔画结构无法形成可读文字", "玻璃倒影与室内物体的位置关系不一致", "自行车辐条与车架的连接细节不连贯"],
  },
  {
    id: "ai-cyclists",
    src: "/playground/samples/sample-05.webp",
    title: "清晨骑行",
    clues: ["自行车链条与辐条出现不合理的连接", "手部和车把接触处的边界含混", "远处骑行者与背景轮廓局部融合"],
  },
  {
    id: "ai-breakfast",
    src: "/playground/samples/sample-09.webp",
    title: "早餐桌面",
    clues: ["餐具齿数与边缘结构存在异常", "浆果的纹理和排列出现重复模式", "报纸字符具有文字外观，却无法正常阅读"],
  },
  {
    id: "ai-flower-market",
    src: "/playground/samples/sample-13.webp",
    title: "花市摊位",
    clues: ["手指与花茎的交界处发生粘连", "部分花瓣形态呈现机械式重复", "价签字符结构残缺且语义不成立"],
  },
  {
    id: "ai-station",
    src: "/playground/samples/sample-16.webp",
    title: "车站候车厅",
    clues: ["时刻表文字不可读，行列结构也不稳定", "地面反射与人物位置没有完全对应", "行李箱拉杆在局部出现断裂和变形"],
  },
];

function randomGenerator(seed: number) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffled<T>(items: T[], random: () => number) {
  const copy = [...items];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const target = Math.floor(random() * (index + 1));
    [copy[index], copy[target]] = [copy[target], copy[index]];
  }
  return copy;
}

function buildRounds(seed: number): Candidate[][] {
  const random = randomGenerator(seed);
  const aiOrder = shuffled(AI_SAMPLES, random);
  return aiOrder.slice(0, TOTAL_ROUNDS).map((aiSample) => {
    const realCandidates = shuffled(REAL_SAMPLES, random).slice(0, 5).map((sample) => ({
      ...sample,
      isAi: false,
    }));
    return shuffled<Candidate>([
      ...realCandidates,
      { ...aiSample, isAi: true, aiSample },
    ], random);
  });
}

function storedBestScore() {
  try {
    return Number(window.localStorage.getItem(BEST_SCORE_KEY) || 0);
  } catch {
    return 0;
  }
}

export default function Playground({
  authReady,
  user,
  onHome,
  onWorkspace,
  onDeveloper,
  onLogin,
  onLogout,
}: Props) {
  const [seed, setSeed] = useState(() => Date.now());
  const [mode, setMode] = useState<GameMode>("relaxed");
  const [roundIndex, setRoundIndex] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(ROUND_SECONDS);
  const [score, setScore] = useState(0);
  const [correctCount, setCorrectCount] = useState(0);
  const [streak, setStreak] = useState(0);
  const [lastEarned, setLastEarned] = useState(0);
  const [finished, setFinished] = useState(false);
  const [bestScore, setBestScore] = useState(storedBestScore);
  const [preview, setPreview] = useState<{ candidate: Candidate; index: number } | null>(null);
  const roundHeadingRef = useRef<HTMLHeadingElement>(null);
  const closePreviewRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const rounds = useMemo(() => buildRounds(seed), [seed]);
  const round = rounds[roundIndex] || rounds[0];
  const aiCandidate = round.find((candidate) => candidate.isAi)!;
  const selectedWasCorrect = Boolean(!timedOut && selectedId && selectedId === aiCandidate.id);
  const modeLocked = roundIndex > 0 || selectedId !== null || revealed;

  const finishGame = useCallback(() => {
    setFinished(true);
    setBestScore((current) => {
      const next = Math.max(current, score);
      try {
        window.localStorage.setItem(BEST_SCORE_KEY, String(next));
      } catch {
        // The game still works when storage is unavailable.
      }
      return next;
    });
  }, [score]);

  const closePreview = useCallback(() => {
    const previousFocus = previousFocusRef.current;
    setPreview(null);
    window.requestAnimationFrame(() => previousFocus?.focus());
  }, []);

  const nextRound = useCallback(() => {
    if (roundIndex >= TOTAL_ROUNDS - 1) {
      finishGame();
      return;
    }
    setRoundIndex((current) => current + 1);
    setSelectedId(null);
    setRevealed(false);
    setTimedOut(false);
    setSecondsLeft(ROUND_SECONDS);
    setLastEarned(0);
    window.requestAnimationFrame(() => roundHeadingRef.current?.focus({ preventScroll: true }));
  }, [finishGame, roundIndex]);

  const confirmSelection = useCallback(() => {
    if (!selectedId || revealed || finished) return;
    const correct = selectedId === aiCandidate.id;
    const earned = correct ? 100 + (mode === "timed" ? secondsLeft * 2 : 0) : 0;
    setRevealed(true);
    setLastEarned(earned);
    if (correct) {
      setScore((current) => current + earned);
      setCorrectCount((current) => current + 1);
      setStreak((current) => current + 1);
    } else {
      setStreak(0);
    }
  }, [aiCandidate.id, finished, mode, revealed, secondsLeft, selectedId]);

  const restart = useCallback((nextMode = mode) => {
    setSeed(Date.now() + Math.floor(Math.random() * 100_000));
    setMode(nextMode);
    setRoundIndex(0);
    setSelectedId(null);
    setRevealed(false);
    setTimedOut(false);
    setSecondsLeft(ROUND_SECONDS);
    setScore(0);
    setCorrectCount(0);
    setStreak(0);
    setLastEarned(0);
    setFinished(false);
    setPreview(null);
    window.requestAnimationFrame(() => roundHeadingRef.current?.focus({ preventScroll: true }));
  }, [mode]);

  useEffect(() => {
    if (mode !== "timed" || revealed || finished) return;
    if (secondsLeft <= 0) {
      setTimedOut(true);
      setRevealed(true);
      setStreak(0);
      return;
    }
    const timer = window.setTimeout(() => setSecondsLeft((current) => current - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [finished, mode, revealed, secondsLeft]);

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape" && preview) {
        event.preventDefault();
        closePreview();
        return;
      }
      const target = event.target as HTMLElement | null;
      if (target?.closest("button, a, input, textarea, select, [contenteditable='true']")) return;
      if (preview || finished) return;
      const candidateIndex = Number(event.key) - 1;
      if (!revealed && candidateIndex >= 0 && candidateIndex < round.length) {
        event.preventDefault();
        setSelectedId(round[candidateIndex].id);
        return;
      }
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (revealed) nextRound();
      else confirmSelection();
    };
    window.addEventListener("keydown", handleKeyboard);
    return () => window.removeEventListener("keydown", handleKeyboard);
  }, [closePreview, confirmSelection, finished, nextRound, preview, revealed, round]);

  useEffect(() => {
    if (!preview) return;
    closePreviewRef.current?.focus();
  }, [preview]);

  function openPreview(candidate: Candidate, index: number, trigger: HTMLElement) {
    previousFocusRef.current = trigger;
    setPreview({ candidate, index });
  }

  function candidateState(candidate: Candidate) {
    if (!revealed) return selectedId === candidate.id ? "selected" : "idle";
    if (candidate.isAi) return "answer";
    if (selectedId === candidate.id) return "wrong";
    return "real";
  }

  return (
    <div className="playground-page">
      <header className="playground-header">
        <HuijianBrand onClick={onHome} />
        <nav className="playground-nav" aria-label="Playground 页面导航">
          <button type="button" onClick={onHome}><House size={17} />官网首页</button>
          <button type="button" className="is-active" aria-current="page"><Sparkles size={17} />Playground</button>
          <button type="button" onClick={onDeveloper}><Code2 size={17} />开发者平台</button>
        </nav>
        <div className="playground-header-actions">
          {authReady && (user ? (
            <AccountMenu compact user={user} onWorkspace={onWorkspace} onDeveloper={onDeveloper} onLogout={onLogout} />
          ) : (
            <button type="button" className="playground-login" aria-label="登录账号" onClick={onLogin}><LogIn size={17} /><span>登录</span></button>
          ))}
          <button type="button" className="playground-workspace" onClick={onWorkspace}>开始鉴伪<ArrowRight size={17} /></button>
        </div>
      </header>

      <main className="playground-main">
        <section className="playground-intro" aria-labelledby="playground-title">
          <div>
            <p className="playground-eyebrow"><Sparkles size={15} /> 慧鉴实验室 · 第一个小游戏</p>
            <h1 id="playground-title" tabIndex={-1}>找出那张 AI 图</h1>
            <p>六张图片中只有一张由 AI 生成。先凭观察做出选择，再查看值得复核的细节。</p>
          </div>
          <dl className="playground-stats" aria-label="本局统计">
            <div><dt>轮次</dt><dd>{finished ? TOTAL_ROUNDS : roundIndex + 1}<small> / {TOTAL_ROUNDS}</small></dd></div>
            <div><dt>得分</dt><dd>{score}</dd></div>
            <div><dt>连对</dt><dd>{streak}</dd></div>
            <div><dt>最佳</dt><dd>{bestScore}</dd></div>
          </dl>
        </section>

        {!finished ? (
          <section className="playground-game" aria-labelledby="playground-round-title">
            <div className="playground-toolbar">
              <div className="playground-mode-group">
                <span>挑战方式</span>
                <div className="playground-segmented" aria-label="选择挑战方式">
                  <button type="button" className={mode === "relaxed" ? "is-selected" : ""} aria-pressed={mode === "relaxed"} disabled={modeLocked} onClick={() => restart("relaxed")}><Eye size={16} />轻松</button>
                  <button type="button" className={mode === "timed" ? "is-selected" : ""} aria-pressed={mode === "timed"} disabled={modeLocked} onClick={() => restart("timed")}><Clock3 size={16} />计时</button>
                </div>
                <small>{modeLocked ? "本局模式已锁定" : "首轮选择后锁定"}</small>
              </div>

              <div className="playground-round-progress" aria-label={`第 ${roundIndex + 1} 轮，共 ${TOTAL_ROUNDS} 轮`}>
                {Array.from({ length: TOTAL_ROUNDS }, (_, index) => (
                  <span key={index} className={index < roundIndex ? "is-complete" : index === roundIndex ? "is-current" : ""} />
                ))}
              </div>

              <div className="playground-toolbar-actions">
                {mode === "timed" ? (
                  <div className={`playground-timer ${secondsLeft <= 8 ? "is-urgent" : ""}`} aria-live="polite"><Clock3 size={18} /><strong>{secondsLeft}</strong><span>秒</span></div>
                ) : (
                  <div className="playground-shortcut"><kbd>1</kbd>–<kbd>6</kbd> 选择 · <kbd>Enter</kbd> 确认</div>
                )}
                {!revealed && <button type="button" className="playground-toolbar-confirm" disabled={!selectedId} onClick={confirmSelection}><Zap size={17} />确认选择</button>}
              </div>
            </div>

            <div className="playground-round-heading">
              <h2 id="playground-round-title" ref={roundHeadingRef} tabIndex={-1}>哪一张不是实拍？</h2>
              <p>{selectedId && !revealed ? "已选中一张。确认后将公布答案。" : revealed ? "答案已公布，看看线索是否与你的观察一致。" : "点击图片选择；放大镜只用于查看细节。"}</p>
            </div>

            <div className="playground-grid">
              {round.map((candidate, index) => {
                const state = candidateState(candidate);
                return (
                  <figure key={candidate.id} className="playground-candidate" data-state={state}>
                    <div className="playground-image-frame">
                      <button
                        type="button"
                        className="playground-choice"
                        aria-label={`选择候选图片 ${index + 1}`}
                        aria-pressed={selectedId === candidate.id}
                        disabled={revealed}
                        onClick={() => setSelectedId(candidate.id)}
                      >
                        <img src={candidate.src} alt={`候选图片 ${index + 1}`} width={640} height={640} draggable={false} />
                      </button>
                      <button type="button" className="playground-zoom" aria-label={`放大候选图片 ${index + 1}`} title="放大查看细节" onClick={(event) => openPreview(candidate, index, event.currentTarget)}><Maximize2 size={18} /></button>
                      <span className="playground-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                      {revealed && (
                        <span className={`playground-answer-badge ${candidate.isAi ? "is-ai" : "is-real"}`}>
                          {candidate.isAi ? <><Sparkles size={14} /> AI 生成</> : <><Check size={14} /> 实拍</>}
                        </span>
                      )}
                    </div>
                    <figcaption>
                      <span>{revealed && selectedId === candidate.id ? (candidate.isAi ? "你找到了" : "你的选择") : `候选 ${index + 1}`}</span>
                      {!revealed && selectedId === candidate.id && <strong><Check size={14} /> 已选择</strong>}
                    </figcaption>
                  </figure>
                );
              })}
            </div>

            {revealed && (
              <section className={`playground-feedback ${selectedWasCorrect ? "is-correct" : "is-wrong"}`} aria-live="polite" aria-label="本轮结果">
                <div className="playground-feedback-verdict">
                  <span>{selectedWasCorrect ? <Target size={24} /> : timedOut ? <Clock3 size={24} /> : <Eye size={24} />}</span>
                  <div>
                    <p>{selectedWasCorrect ? "判断正确" : timedOut ? "本轮时间到" : "这次没有选中"}</p>
                    <h3>{selectedWasCorrect ? `+${lastEarned} 分` : `AI 图是候选 ${round.indexOf(aiCandidate) + 1}`}</h3>
                  </div>
                </div>
                <div className="playground-feedback-clues">
                  <p><Sparkles size={15} /> 这张“{aiCandidate.aiSample?.title}”值得复核的细节</p>
                  <ul>{aiCandidate.aiSample?.clues.map((clue) => <li key={clue}>{clue}</li>)}</ul>
                </div>
                <button type="button" className="playground-next" onClick={nextRound}>{roundIndex === TOTAL_ROUNDS - 1 ? "查看成绩" : "下一轮"}<ArrowRight size={18} /></button>
              </section>
            )}

            {!revealed && (
              <div className="playground-actionbar">
                <p aria-live="polite">{selectedId ? `已选择候选 ${round.findIndex((candidate) => candidate.id === selectedId) + 1}` : "还没有选择图片"}</p>
                <button type="button" disabled={!selectedId} onClick={confirmSelection}><Zap size={18} />确认选择</button>
              </div>
            )}
          </section>
        ) : (
          <section className="playground-finish" aria-labelledby="playground-finish-title">
            <div className="playground-finish-mark"><Target size={34} /></div>
            <p>本局完成</p>
            <h2 id="playground-finish-title">你找对了 {correctCount} / {TOTAL_ROUNDS} 张</h2>
            <strong>{score}<small> 分</small></strong>
            <p>{correctCount === TOTAL_ROUNDS ? "观察力很敏锐。下一步是用多条证据验证直觉。" : correctCount >= 3 ? "已经抓住不少视觉异常，再多观察文字、手部和结构关系。" : "AI 图越来越像实拍，单凭肉眼犯错很正常。"}</p>
            <div>
              <button type="button" onClick={() => restart()}><RefreshCw size={18} />再来一局</button>
              <button type="button" className="is-primary" onClick={onWorkspace}>用慧鉴AI检测内容<ArrowRight size={18} /></button>
            </div>
          </section>
        )}

        <aside className="playground-method-note" aria-label="玩法与样本说明">
          <strong>这是一场观察练习，不是鉴伪结论。</strong>
          <span>视觉异常只是启发式线索；正式判断还应结合模型、水印、元数据和可信来源。AI 样本由 GPT Image 为本项目生成，实拍样本来自 Unsplash。</span>
        </aside>
      </main>

      {preview && (
        <div className="playground-lightbox" role="dialog" aria-modal="true" aria-label={`放大查看候选图片 ${preview.index + 1}`} onMouseDown={(event) => { if (event.currentTarget === event.target) closePreview(); }}>
          <div className="playground-lightbox-panel">
            <div className="playground-lightbox-header"><span>候选 {preview.index + 1} · 细节查看</span><button ref={closePreviewRef} type="button" aria-label="关闭图片预览" onClick={closePreview}><X size={21} /></button></div>
            <img src={preview.candidate.src} alt={`候选图片 ${preview.index + 1} 的放大视图`} width={640} height={640} />
            <p>放大查看不会提交答案。按 Esc 关闭。</p>
          </div>
        </div>
      )}
    </div>
  );
}
