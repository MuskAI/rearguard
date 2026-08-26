import { ArrowRight, Heart, House, LogIn, RefreshCw, Sparkles, Target } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { AccountUser } from "../api";
import AccountMenu from "./AccountMenu";
import HuijianBrand from "./HuijianBrand";

type Screen = "start" | "play" | "finish";

type Sample = {
  id: string;
  src: string;
  isAi: boolean;
  title: string;
  clue: string;
};

type LeaderboardEntry = {
  id: string;
  name: string;
  score: number;
  correct: number;
};

type Props = {
  authReady: boolean;
  user: AccountUser | null;
  onHome: () => void;
  onWorkspace: () => void;
  onDeveloper: () => void;
  onLogin: () => void;
  onLogout: () => void;
};

const ROUNDS = 8;
const START_LIVES = 3;
const BEST_SCORE_KEY = "huijian-playground-best-score";
const LEADERBOARD_KEY = "huijian-playground-leaderboard";

const REAL_IMAGES = [
  ["real-car", "sample-01.webp", "街头汽车", "真实照片里的材质和反光并不完美，但彼此关系自然。"],
  ["real-cat", "sample-03.webp", "窗边的猫", "毛发、眼睛反光和窗框遮挡保留了真实镜头的细碎变化。"],
  ["real-coffee", "sample-04.webp", "三杯咖啡", "杯沿、手指和液体边界都有真实拍摄留下的轻微不规则。"],
  ["real-dog", "sample-06.webp", "公园里的狗", "毛发和背景的焦外纹理自然变化，没有大面积复制感。"],
  ["real-mountain", "sample-12.webp", "山谷晨光", "山脊、云层与树线的层次关系连续，没有明显断裂。"],
  ["real-fox", "sample-08.webp", "林间的狐狸", "主体边缘和背景的遮挡关系自然，局部细节没有机械粘连。"],
  ["real-plant", "sample-14.webp", "窗台植物", "叶片纹理和光影变化各不相同，没有模板式重复。"],
  ["real-sunset", "sample-17.webp", "海边日落", "远近景纹理随距离自然衰减，光线方向保持一致。"],
] as const;

const AI_IMAGES = [
  ["ai-cafe", "sample-02.webp", "街角咖啡馆", "菜单字符无法形成可读文字，玻璃倒影和自行车细节也不连贯。"],
  ["ai-cyclists", "sample-05.webp", "清晨骑行", "链条与辐条出现异常连接，手部和车把的边界发生粘连。"],
  ["ai-breakfast", "sample-09.webp", "早餐桌面", "餐具结构和报纸字符不稳定，浆果纹理出现重复模式。"],
  ["ai-flower-market", "sample-13.webp", "花市摊位", "手指与花茎交界处粘连，价签字符结构残缺。"],
  ["ai-station", "sample-16.webp", "车站候车厅", "时刻表文字不可读，行李箱拉杆局部断裂变形。"],
] as const;

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

function makeSample(entry: readonly [string, string, string, string], isAi: boolean): Sample {
  return { id: entry[0], src: `/playground/samples/${entry[1]}`, title: entry[2], isAi, clue: entry[3] };
}

function buildRounds(seed: number) {
  const random = randomGenerator(seed);
  const realPool = REAL_IMAGES.map((entry) => makeSample(entry, false));
  const aiPool = shuffled(AI_IMAGES.map((entry) => makeSample(entry, true)), random);
  return Array.from({ length: ROUNDS }, (_, roundIndex) => {
    const ai = aiPool[roundIndex % aiPool.length];
    const real = shuffled(realPool, random).slice(0, 5);
    return shuffled([...real, ai], random);
  });
}

function readBestScore() {
  try {
    return Number(window.localStorage.getItem(BEST_SCORE_KEY) || 0);
  } catch {
    return 0;
  }
}

function readLeaderboard(): LeaderboardEntry[] {
  try {
    const saved = JSON.parse(window.localStorage.getItem(LEADERBOARD_KEY) || "[]");
    return Array.isArray(saved)
      ? saved.filter((entry) => entry && typeof entry.name === "string" && typeof entry.score === "number").sort((a, b) => b.score - a.score || b.correct - a.correct).slice(0, 10)
      : [];
  } catch {
    return [];
  }
}

export default function Playground({ authReady, user, onHome, onWorkspace, onDeveloper, onLogin, onLogout }: Props) {
  const [seed, setSeed] = useState(() => Date.now());
  const [screen, setScreen] = useState<Screen>("start");
  const [roundIndex, setRoundIndex] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [lastCorrect, setLastCorrect] = useState(false);
  const [score, setScore] = useState(0);
  const [lives, setLives] = useState(START_LIVES);
  const [streak, setStreak] = useState(0);
  const [correctCount, setCorrectCount] = useState(0);
  const [bestScore, setBestScore] = useState(readBestScore);
  const [leaderboard, setLeaderboard] = useState(readLeaderboard);
  const [playerName, setPlayerName] = useState("");
  const [submittedId, setSubmittedId] = useState<string | null>(null);

  const rounds = useMemo(() => buildRounds(seed), [seed]);
  const round = rounds[roundIndex];
  const aiIndex = round.findIndex((sample) => sample.isAi);
  const aiSample = round[aiIndex];
  const gameIsOver = roundIndex === ROUNDS - 1 || lives === 0;

  const startGame = useCallback(() => {
    setSeed(Date.now() + Math.floor(Math.random() * 100_000));
    setScreen("play");
    setRoundIndex(0);
    setSelectedId(null);
    setRevealed(false);
    setScore(0);
    setLives(START_LIVES);
    setStreak(0);
    setCorrectCount(0);
    setPlayerName("");
    setSubmittedId(null);
  }, []);

  const finishGame = useCallback(() => {
    setScreen("finish");
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

  const chooseImage = useCallback((sample: Sample) => {
    if (revealed || screen !== "play") return;
    const correct = sample.isAi;
    setSelectedId(sample.id);
    setRevealed(true);
    setLastCorrect(correct);
    setStreak((current) => (correct ? current + 1 : 0));
    setScore((current) => current + (correct ? 100 + streak * 25 : 0));
    if (correct) setCorrectCount((current) => current + 1);
    else setLives((current) => Math.max(0, current - 1));
  }, [revealed, screen, streak]);

  useEffect(() => {
    if (screen !== "play" || !revealed) return;
    const timer = window.setTimeout(() => {
      if (gameIsOver) {
        finishGame();
        return;
      }
      setRoundIndex((current) => current + 1);
      setSelectedId(null);
      setRevealed(false);
    }, 1050);
    return () => window.clearTimeout(timer);
  }, [finishGame, gameIsOver, revealed, screen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (screen !== "play" || revealed) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("button, a, input, textarea, select")) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < round.length) chooseImage(round[index]);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [chooseImage, revealed, round, screen]);

  const submitScore = useCallback(() => {
    if (submittedId) return;
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const entry: LeaderboardEntry = { id, name: playerName.trim().slice(0, 12) || "匿名玩家", score, correct: correctCount };
    const next = [...leaderboard, entry].sort((a, b) => b.score - a.score || b.correct - a.correct).slice(0, 10);
    setLeaderboard(next);
    setSubmittedId(id);
    try {
      window.localStorage.setItem(LEADERBOARD_KEY, JSON.stringify(next));
    } catch {
      // The local leaderboard still works for this session.
    }
  }, [correctCount, leaderboard, playerName, score, submittedId]);

  const playerRank = submittedId ? leaderboard.findIndex((entry) => entry.id === submittedId) + 1 : 0;

  return (
    <div className="playground-page h-dvh min-h-0 w-full min-w-0 overflow-x-clip overflow-y-auto overscroll-y-contain">
      <header className="playground-header">
        <HuijianBrand onClick={onHome} />
        <nav className="playground-nav" aria-label="Playground 页面导航"><button type="button" onClick={onHome}><House size={16} />官网首页</button><button type="button" className="is-active" aria-current="page"><Sparkles size={16} />小游戏</button><button type="button" onClick={onDeveloper}>开发者平台</button></nav>
        <div className="playground-header-actions">{authReady && (user ? <AccountMenu compact user={user} onWorkspace={onWorkspace} onDeveloper={onDeveloper} onLogout={onLogout} /> : <button type="button" className="playground-login max-[620px]:!h-11 max-[620px]:!min-h-11 max-[620px]:!w-auto max-[620px]:!min-w-11 max-[620px]:!px-2.5" aria-label="登录账号" onClick={onLogin}><LogIn size={16} /><span className="max-[620px]:!inline">登录</span></button>)}<button type="button" className="playground-workspace max-[620px]:!min-h-11" onClick={onWorkspace}>开始鉴伪<ArrowRight size={16} /></button></div>
      </header>

      <main className="playground-main">
        {screen === "start" && <section className="simple-start" aria-labelledby="playground-title"><p className="simple-eyebrow"><Sparkles size={15} /> AI IMAGE SPOTTER</p><h1 id="playground-title">找出那张 AI 图</h1><p className="simple-lead">六张图片里，只有一张不是实拍。点一下，看看你能连中几轮。</p><div className="simple-rules"><span><strong>8</strong> 轮</span><span><strong>3</strong> 次机会</span><span><strong>1</strong> 个答案</span></div><button type="button" className="simple-start-button" onClick={startGame}>开始游戏<ArrowRight size={20} /></button><p className="simple-hint">只看图，不看标签。按 1–6 也可以选择。</p></section>}

        {screen === "play" && <section className="simple-game" aria-labelledby="round-title"><header className="simple-game-header"><div className="round-count"><span>ROUND</span><strong>{String(roundIndex + 1).padStart(2, "0")}</strong><small>/ {ROUNDS}</small></div><div className="round-progress" aria-label={`第 ${roundIndex + 1} 轮，共 ${ROUNDS} 轮`}>{Array.from({ length: ROUNDS }, (_, index) => <i key={index} className={index < roundIndex ? "is-done" : index === roundIndex ? "is-current" : ""} />)}</div><div className="game-stats"><span className="streak-stat">{streak > 1 ? `连中 ${streak}` : "先赢一轮"}</span><span className="life-stat" aria-label={`剩余 ${lives} 次机会`}>{Array.from({ length: START_LIVES }, (_, index) => <Heart key={index} size={17} fill={index < lives ? "currentColor" : "none"} className={index < lives ? "is-alive" : "is-lost"} />)}</span><strong>{score}</strong></div></header><div className="simple-question"><h2 id="round-title">哪一张是 AI 生成？</h2><p>{revealed ? (lastCorrect ? "找到了。下一轮继续。" : "这次看走眼了，答案已经揭晓。") : "只做一个选择，马上揭晓。"}</p></div><div className="simple-grid">{round.map((sample, index) => { const state = !revealed ? (selectedId === sample.id ? "selected" : "idle") : sample.isAi ? "answer" : selectedId === sample.id ? "wrong" : "muted"; return <button key={sample.id} type="button" className="simple-card" data-state={state} aria-label={`选择图片 ${index + 1}`} aria-pressed={selectedId === sample.id} disabled={revealed} onClick={() => chooseImage(sample)}><img src={sample.src} alt={`候选图片 ${index + 1}`} width={640} height={480} draggable={false} /><span className="card-number">{String(index + 1).padStart(2, "0")}</span>{revealed && <span className={`card-answer ${sample.isAi ? "is-ai" : "is-real"}`}>{sample.isAi ? "AI 生成" : "实拍"}</span>}</button>; })}</div>{revealed && <div className={`simple-feedback ${lastCorrect ? "is-correct" : "is-wrong"}`} aria-live="polite"><div><strong>{lastCorrect ? "找到了！" : "这次看走眼了"}</strong><span>{lastCorrect ? `+${100 + (streak - 1) * 25} 分 · 连中 ${streak}` : `AI 图是第 ${aiIndex + 1} 张 · 还剩 ${lives} 次机会`}</span></div><p>{aiSample.title}：{aiSample.clue}</p><span className="auto-advance-note">{gameIsOver ? "正在生成结果…" : "下一轮马上开始…"}</span></div>}<p className="simple-footer-note">{revealed ? "" : "你的第一眼，值得相信吗？"}</p></section>}

        {screen === "finish" && <section className="simple-finish" aria-labelledby="finish-title"><Target size={33} /><p className="simple-eyebrow">GAME OVER · RESULT</p><h1 id="finish-title">你的分数</h1><strong className="final-score">{score}</strong><p className="final-copy">找对了 {correctCount} / {ROUNDS} 张，{streak > 1 ? `最后保持 ${streak} 连中。` : "AI 图越来越像实拍，失误很正常。"}</p><div className="final-actions"><button type="button" onClick={startGame}><RefreshCw size={17} />再玩一次</button><button type="button" className="is-primary" onClick={onWorkspace}>去做真正的鉴伪<ArrowRight size={17} /></button></div><p className="best-score">历史最佳：{bestScore} 分</p><section className="leaderboard-panel" aria-labelledby="leaderboard-title"><div className="leaderboard-heading"><div><p>LOCAL HALL OF FAME</p><h2 id="leaderboard-title">本机榜单</h2></div><span>Top 10</span></div>{!submittedId ? <div className="score-form"><input value={playerName} maxLength={12} placeholder="输入你的名字" aria-label="输入你的名字" onChange={(event) => setPlayerName(event.target.value)} /><button type="button" onClick={submitScore}>登上榜单<ArrowRight size={16} /></button></div> : playerRank > 0 ? <p className="rank-result">已上榜：第 <strong>{playerRank}</strong> 名</p> : <p className="rank-result">本次成绩未进入 Top 10</p>}{leaderboard.length === 0 ? <p className="empty-leaderboard">还没有记录，成为第一个上榜的人。</p> : <ol>{leaderboard.map((entry, index) => <li key={entry.id} className={entry.id === submittedId ? "is-you" : ""}><span>{String(index + 1).padStart(2, "0")}</span><strong>{entry.name}</strong><small>{entry.correct}/{ROUNDS}</small><b>{entry.score}</b></li>)}</ol>}</section></section>}
        <p className="simple-disclaimer">这是一个观察小游戏，不是鉴伪结论。</p>
      </main>
    </div>
  );
}
