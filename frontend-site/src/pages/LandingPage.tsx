import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import AppHeader from "../components/AppHeader";
import BrandLogo from "../components/BrandLogo";
import EmbeddedChat from "./chat/EmbeddedChat";
import logoMark from "../assets/LOGO.svg";
import icKnowledge from "../assets/landing/knowledge.svg";
import icChat from "../assets/landing/chat.svg";
import icSkill from "../assets/landing/skill.svg";
import icText from "../assets/landing/text.svg";
import icSop from "../assets/landing/sop.svg";
import icTool from "../assets/landing/tool.svg";
import hvDashboard from "../assets/staffdeck/login-preview.png";
import hvPersona from "../assets/landing/hv-persona.png";
import visualBg from "../assets/landing/visual-bg.svg?url";
import advantage1 from "../assets/landing/advantage-1.png";
import advantage2 from "../assets/landing/advantage-2.png";
import advantage3 from "../assets/landing/advantage-3.png";
import DashOverview from "./DashOverview";
import "./landing.css";

type RevealStyle = CSSProperties & { "--d"?: string };

/** Delay helper for staggered reveals. */
function d(ms: number): RevealStyle {
  return { "--d": String(ms) };
}

const SCENES = [
  { id: "hero", label: "首页" },
  { id: "story", label: "能力介绍" },
  { id: "chat", label: "开始使用" },
];

/* Floating hero icons. Order maps to .lp-orb--1..6 in landing.css, which owns
   each icon's position/size/rotation/shadow so it can adapt responsively. */
const HERO_ICONS = [icKnowledge, icChat, icSkill, icSop, icText, icTool];

/* Hero orb → dash rail slot handoff (scene 1 → scene 2). Orbs 2–3 fade out. */
const ICON_HANDOFF = [
  { hero: 0, rail: 0, rot: -26.86 },
  { hero: 4, rail: 1, rot: 14.62 },
  { hero: 3, rail: 2, rot: 25.04 },
  { hero: 5, rail: 3, rot: 18.83 },
] as const;
const HERO_FADE_ORBS = new Set([1, 2]);

type FlyerPose = {
  hero: number;
  left: number;
  top: number;
  width: number;
  height: number;
  rotate: number;
  opacity: number;
};

type PersonaPose = {
  left: number;
  top: number;
  width: number;
  height: number;
  rotate: number;
  opacity: number;
};

function smoothstep(t: number) {
  return t * t * (3 - 2 * t);
}

/* The site's last screen opens the public (no-auth) chat page. Agent id is
   hard-coded for now — this will be replaced later. */
const PUBLIC_CHAT_AGENT_ID = "agent_7c03ba539fe847a5";

/* The pinned "story" screen steps through these states while the dashboard
   window stays put: step 0 is the 资料 overview, steps 1-3 swap the feature
   card and slide the rail selection down the 能力 icons. */
const ADVANTAGES = [
  {
    tag: "优势一",
    title: "数字员工：向管理员工一样管理 AI",
    body: "每位数字员工都有自己的档案、岗位、技能配置与成长记录。你可以看到它服务了多少人、好评率如何、最近学会了什么—— AI 不再是黑盒工具，而是一位可培养、可考核、可信赖的数字同事。",
    img: advantage1,
    alt: "数字员工档案",
  },
  {
    tag: "优势二",
    title: "流程型技能：把流程真正办到底",
    body: "基于状态机驱动的 SOP 技能，多个流程可实时切换、互相串联。用户中途插问、跳出流程，数字员工也能记住进度、无缝恢复，上下文不丢——不只是回答问题，而是把事情办完。",
    img: advantage2,
    alt: "流程型技能",
  },
  {
    tag: "优势三",
    title: "OKF 企业知识本体：理解业务，而非只会检索",
    body: "将文档沉淀为主题、规则、操作手册等结构化知识本体，回答自带出处引用。数字员工理解的是你的业务口径，而不是简单的关键词匹配。",
    img: advantage3,
    alt: "企业知识本体",
  },
];

export default function LandingPage() {
  const rootRef = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Array<HTMLElement | null>>([]);
  const stepRefs = useRef<Array<HTMLDivElement | null>>([]);
  const [active, setActive] = useState(0);
  const [dashStep, setDashStep] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [handoff, setHandoff] = useState(0);
  const [flyers, setFlyers] = useState<FlyerPose[]>([]);
  const [personaFlyer, setPersonaFlyer] = useState<PersonaPose | null>(null);
  const orbRefs = useRef<Array<HTMLDivElement | null>>([]);
  const railSlotRefs = useRef<Array<HTMLDivElement | null>>([]);
  const handoffFromCache = useRef<Map<number, DOMRect>>(new Map());
  const personaRef = useRef<HTMLImageElement>(null);
  const overviewRef = useRef<HTMLDivElement>(null);
  const personaFromCache = useRef<DOMRect | null>(null);
  const prevActiveRef = useRef(0);

  const scrollToScene = (index: number) => {
    sectionRefs.current[index]?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    // Center-band detector: whichever section crosses the viewport midline is
    // the active one. Works for any section height (incl. the tall pinned
    // story track, which a fixed ratio threshold could never satisfy).
    const sections = sectionRefs.current.filter(Boolean) as HTMLElement[];
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = Number((entry.target as HTMLElement).dataset.index);
          if (entry.isIntersecting) {
            entry.target.classList.add("is-active");
            setActive(idx);
          } else {
            entry.target.classList.remove("is-active");
          }
        });
      },
      { root, rootMargin: "-49% 0px -49% 0px", threshold: 0 },
    );
    sections.forEach((section) => observer.observe(section));

    // Track which step of the pinned "story" screen is centred, so the rail
    // selection + feature card can update while the window stays pinned.
    const steps = stepRefs.current.filter(Boolean) as HTMLElement[];
    const stepObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
            setDashStep(Number((entry.target as HTMLElement).dataset.step));
          }
        });
      },
      { root, threshold: [0.5] },
    );
    steps.forEach((step) => stepObserver.observe(step));

    const onScroll = () => setScrolled(root.scrollTop > 24);
    root.addEventListener("scroll", onScroll, { passive: true });

    // Kick off the hero entrance — deferred by two frames so the browser paints
    // the hidden start state first; otherwise the fly-in transition never plays.
    const first = sectionRefs.current[0];
    let raf1 = 0;
    let raf2 = 0;
    if (first) {
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => first.classList.add("is-active"));
      });
    }

    return () => {
      observer.disconnect();
      stepObserver.disconnect();
      root.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, []);

  // Snapshot each hero orb's scattered position while scene 1 is on screen, so
  // the fly-in has a stable launch point after the orbs scroll away.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let raf = 0;
    const snapshot = () => {
      raf = 0;
      if (root.scrollTop > root.clientHeight * 0.4) return;
      for (const { hero: hi } of ICON_HANDOFF) {
        const rect = orbRefs.current[hi]
          ?.querySelector(".lp-orb-tile")
          ?.getBoundingClientRect();
        if (rect && rect.width > 0) handoffFromCache.current.set(hi, rect);
      }
      const prect = personaRef.current?.getBoundingClientRect();
      if (prect && prect.width > 0) personaFromCache.current = prect;
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(snapshot);
    };
    snapshot();
    root.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      root.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  // Time-based fly-in: when scene 2 becomes active, the cached hero orbs
  // animate from their scattered spots into the dash rail slots. The snap
  // transition is near-instant, so a scroll-scrubbed path would never be seen;
  // a one-shot animation on scene enter makes the trajectory clearly visible.
  useEffect(() => {
    if (active !== 1) {
      prevActiveRef.current = active;
      setHandoff(0);
      setFlyers([]);
      setPersonaFlyer(null);
      return;
    }

    const fromChat = prevActiveRef.current === 2;
    prevActiveRef.current = active;

    if (fromChat) {
      setHandoff(1);
      setFlyers([]);
      setPersonaFlyer(null);
      return;
    }

    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reduceMotion) {
      setHandoff(1);
      setFlyers([]);
      setPersonaFlyer(null);
      return;
    }

    // Resolve start (hero orb) rects once; the rail slot targets are measured
    // live each frame so the flyers track the dash window as it scales into
    // place (its enter transition would otherwise settle after we measured).
    const legs = ICON_HANDOFF.map(({ hero: hi, rail: ri, rot }) => {
      let from = handoffFromCache.current.get(hi);
      const slot = railSlotRefs.current[ri];
      if (!from && slot) {
        const r = slot.getBoundingClientRect();
        from = new DOMRect(r.left - 40, r.top - 220, 84, 84);
      }
      return from && slot ? { hero: hi, rail: ri, from, rot } : null;
    }).filter(Boolean) as {
      hero: number;
      rail: number;
      from: DOMRect;
      rot: number;
    }[];

    // Resolve the persona launch rect. Prefer the position snapshotted while
    // scene 1 was on screen; otherwise fall back to a spot just above the dash
    // overview so the fly-in still has a sensible trajectory.
    let personaFrom = personaFromCache.current;
    if (!personaFrom) {
      const ov = overviewRef.current?.getBoundingClientRect();
      if (ov) {
        personaFrom = new DOMRect(
          ov.left + ov.width * 0.03,
          ov.top - 220,
          200,
          140,
        );
      }
    }

    if (legs.length === 0 && !personaFrom) {
      setHandoff(1);
      setFlyers([]);
      setPersonaFlyer(null);
      return;
    }

    const DURATION = 1100;
    const CROSSFADE_FROM = 0.88;
    const startTime = performance.now();
    let raf = 0;

    const slotTarget = (rail: number) =>
      railSlotRefs.current[rail]?.querySelector("img")?.getBoundingClientRect() ??
      railSlotRefs.current[rail]?.getBoundingClientRect();

    const tick = (now: number) => {
      const raw = Math.min(1, (now - startTime) / DURATION);
      const t = smoothstep(raw);
      setHandoff(t);

      if (t >= 1) {
        // Paint slot icons / overview at full opacity first, then drop flyers.
        raf = requestAnimationFrame(() => {
          setFlyers([]);
          setPersonaFlyer(null);
        });
        return;
      }

      const flyerOpacity =
        t >= CROSSFADE_FROM ? (1 - t) / (1 - CROSSFADE_FROM) : 1;

      // Persona fly-in: from its scene-1 spot onto the real persona slot in the
      // div-based overview card, so it lands exactly where the card's own
      // persona lives, then cross-fades out as that persona fades in.
      const personaEl = overviewRef.current?.querySelector(
        ".lp-do-persona",
      ) as HTMLElement | null;
      const to = personaEl?.getBoundingClientRect();
      if (personaFrom && to && to.width > 0) {
        const fromCx = personaFrom.left + personaFrom.width / 2;
        const fromCy = personaFrom.top + personaFrom.height / 2;
        const toCx = to.left + to.width / 2;
        const toCy = to.top + to.height / 2;
        const w = personaFrom.width + (to.width - personaFrom.width) * t;
        const h = personaFrom.height + (to.height - personaFrom.height) * t;
        setPersonaFlyer({
          left: fromCx + (toCx - fromCx) * t - w / 2,
          top: fromCy + (toCy - fromCy) * t - h / 2,
          width: w,
          height: h,
          rotate: -8 * Math.pow(1 - t, 2),
          opacity: flyerOpacity,
        });
      }

      setFlyers(
        legs.flatMap(({ hero, rail, from, rot }) => {
          const to = slotTarget(rail);
          if (!to || to.width === 0) return [];
          const fromCx = from.left + from.width / 2;
          const fromCy = from.top + from.height / 2;
          const toCx = to.left + to.width / 2;
          const toCy = to.top + to.height / 2;
          const w = from.width + (to.width - from.width) * t;
          const h = from.height + (to.height - from.height) * t;
          return [
            {
              hero,
              left: fromCx + (toCx - fromCx) * t - w / 2,
              top: fromCy + (toCy - fromCy) * t - h / 2,
              width: w,
              height: h,
              rotate: rot * Math.pow(1 - t, 2),
              opacity: flyerOpacity,
            },
          ];
        }),
      );
      raf = requestAnimationFrame(tick);
    };

    // Start hidden at t=0 for a frame, then animate in.
    setHandoff(0);
    raf = requestAnimationFrame(tick);
    return () => {
      if (raf) cancelAnimationFrame(raf);
    };
  }, [active]);

  // Keyboard navigation (↑ / ↓ / PageUp / PageDown). Inside the pinned story
  // screen we advance one step at a time; otherwise we jump between scenes.
  useEffect(() => {
    const root = rootRef.current;
    const onKey = (event: KeyboardEvent) => {
      const down = event.key === "ArrowDown" || event.key === "PageDown";
      const up = event.key === "ArrowUp" || event.key === "PageUp";
      if (!down && !up) return;
      event.preventDefault();
      const inStory = active === 1;
      if (down) {
        if (root && inStory && dashStep < ADVANTAGES.length) {
          root.scrollBy({ top: window.innerHeight, behavior: "smooth" });
        } else {
          scrollToScene(Math.min(active + 1, SCENES.length - 1));
        }
      } else if (root && inStory && dashStep > 0) {
        root.scrollBy({ top: -window.innerHeight, behavior: "smooth" });
      } else {
        scrollToScene(Math.max(active - 1, 0));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, dashStep]);

  const setSectionRef = (index: number) => (el: HTMLElement | null) => {
    sectionRefs.current[index] = el;
  };
  const setStepRef = (index: number) => (el: HTMLDivElement | null) => {
    stepRefs.current[index] = el;
  };
  const setOrbRef = (index: number) => (el: HTMLDivElement | null) => {
    orbRefs.current[index] = el;
  };
  const setRailSlotRef = (index: number) => (el: HTMLDivElement | null) => {
    railSlotRefs.current[index] = el;
  };

  const handoffHero = (index: number) =>
    ICON_HANDOFF.some((m) => m.hero === index);
  const orbHidden =
    handoff > 0.02 && handoff < 0.98
      ? (index: number) => handoffHero(index)
      : () => false;
  const orbFade =
    handoff > 0 && handoff < 1
      ? (index: number) =>
          HERO_FADE_ORBS.has(index) ? Math.max(0, 1 - handoff * 2.2) : 1
      : () => 1;

  return (
    <div className="lp-root" ref={rootRef} data-scene={active} data-handoff={handoff > 0.02 ? "on" : "off"}>
      {/* animated backdrop */}
      <div className="lp-backdrop" aria-hidden>
        <div className="lp-blob lp-blob--1" />
        <div className="lp-blob lp-blob--2" />
        <div className="lp-blob lp-blob--3" />
      </div>

      {/* icon handoff layer — flies hero orbs into dash rail during scroll */}
      {flyers.length > 0 && (
        <div className="lp-icon-handoff" aria-hidden>
          {flyers.map((f) => (
            <div
              key={f.hero}
              className="lp-icon-handoff__item"
              style={{
                left: f.left,
                top: f.top,
                width: f.width,
                height: f.height,
                opacity: f.opacity,
                transform: `rotate(${f.rotate}deg)`,
              }}
            >
              <img src={HERO_ICONS[f.hero]} alt="" />
            </div>
          ))}
        </div>
      )}

      {/* persona handoff layer — flies the hero persona into the dash overview */}
      {personaFlyer && (
        <div
          className="lp-persona-fly"
          aria-hidden
          style={{
            left: personaFlyer.left,
            top: personaFlyer.top,
            width: personaFlyer.width,
            height: personaFlyer.height,
            opacity: personaFlyer.opacity,
            transform: `rotate(${personaFlyer.rotate}deg)`,
          }}
        >
          <img src={hvPersona} alt="" />
        </div>
      )}

      {/* header — shared global AppHeader inside the fixed scroll-blur wrapper */}
      <div className="lp-header" data-solid={scrolled}>
        <AppHeader
          className="h-[60px] items-center px-[32px]"
          left={<BrandLogo markSize={28} />}
          right={null}
        />
      </div>

      {/* ============================================= SCENE 1 · HERO ==== */}
      <section
        className="lp-section lp-hero"
        data-index={0}
        ref={setSectionRef(0)}
      >
        <div className="lp-orbits" aria-hidden>
          {HERO_ICONS.map((src, i) => (
            <div
              key={i}
              className={`lp-orb lp-orb--${i + 1}`}
              ref={setOrbRef(i)}
              style={{
                opacity: orbHidden(i)
                  ? 0
                  : orbFade(i) < 1
                    ? orbFade(i)
                    : undefined,
              }}
            >
              <div className="lp-orb-float">
                <div className="lp-orb-tile">
                  <img src={src} alt="" />
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="lp-hero-inner">
          <div className="lp-hero-copy">
            <span className="lp-badge">
              <img src={logoMark} alt="" />
              StaffDeck
            </span>
            <h1 className="lp-title">
              StaffDeck
              <br />
              <span className="lp-line2">数字员工运营平台</span>
            </h1>
            <p className="lp-subtitle">
              StaffDeck 用三层能力，让 AI 从「会聊天」进化为「能上岗」。像看真人员工的绩效档案一样，随时了解数字员工干了多少活、又学会了什么。
            </p>
            <button className="lp-cta" type="button">
              下载
            </button>
          </div>
        </div>

        {/* illustration + bubble + dashboard, anchored to section bottom */}
        <div className="lp-hero-visual lp-reveal lp-reveal--rise" style={d(560)}>
          <img className="lp-hv-bg" src={visualBg} alt="" aria-hidden />
          <div className="lp-hv-scene">
            <img
              className="lp-hv-persona"
              src={hvPersona}
              alt="数字员工"
              ref={personaRef}
            />
            <span className="lp-hv-bubble">我们来做什么？</span>
          </div>
          <div className="lp-hv-dash">
            <img src={hvDashboard} alt="StaffDeck 数字员工广场" />
          </div>
        </div>
      </section>

      {/* ==================================== SCENE 2 · STORY (PINNED) ==== */}
      {/* One pinned screen: the dashboard window + rail stay put while the
          scroll advances through 4 steps. Only the right feature card and the
          rail selection change — no full-screen switch. */}
      <section
        className="lp-section lp-dash lp-dash-track"
        data-index={1}
        ref={setSectionRef(1)}
        data-step={dashStep}
      >
        <div className="lp-dash-sticky">
          <div className="lp-dash-inner">
            <div className="lp-dash-window lp-reveal lp-reveal--scale" style={d(60)}>
              <div className="lp-dash-stage">
                {/* floating left menu rail — the hero icons fly into these
                    slots as scene 1 hands off, then the pill slides down the
                    能力 icons as the story steps through. */}
                <DashRail
                  active={dashStep}
                  handoff={handoff}
                  onSlotRef={setRailSlotRef}
                  onSelect={(i) =>
                    stepRefs.current[i]?.scrollIntoView({
                      behavior: "instant" as ScrollBehavior,
                      block: "start",
                    })
                  }
                />

                <div className="lp-dash-content lp-dash-content--feature">
                  {/* step 0 · 资料 overview */}
                  <div
                    className="lp-dash-overview"
                    data-on={dashStep === 0}
                    aria-hidden={dashStep !== 0}
                    ref={overviewRef}
                  >
                    <DashOverview handoff={handoff} />
                  </div>

                  {/* steps 1-3 · shared header, cross-fading feature cards */}
                  <div className="lp-featwin" data-on={dashStep >= 1}>
                    <h2 className="lp-featwin-title">
                      不是又一个聊天机器人，而是一位数字同事
                    </h2>
                    <p className="lp-featwin-sub">
                      StaffDeck 用三层能力，让 AI 从「会聊天」进化为「能上岗」。
                    </p>
                    <div className="lp-featwin-cards">
                      {ADVANTAGES.map((a, i) => (
                        <div
                          className="lp-featwin-card"
                          data-on={dashStep === i + 1}
                          key={a.tag}
                        >
                          <div className="lp-featwin-illus">
                            <img src={a.img} alt={a.alt} />
                          </div>
                          <div className="lp-featwin-copy">
                            <span className="lp-featwin-tag">{a.tag}</span>
                            <h3>{a.title}</h3>
                            <p>{a.body}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* invisible scroll markers drive the pinned sequence + snap points */}
        <div className="lp-dash-steps" aria-hidden>
          {[0, 1, 2, 3].map((s) => (
            <div
              className="lp-dash-step"
              data-step={s}
              key={s}
              ref={setStepRef(s)}
            />
          ))}
        </div>
      </section>

      {/* =========================================== SCENE 3 · CHAT ====== */}
      <section className="lp-section lp-chat" data-index={2} ref={setSectionRef(2)}>
        {/* Live, no-auth chat embedded straight into the site — reuses the real
            chat empty state + composer via EmbeddedChat. */}
        <div className="lp-chat-embed">
          <EmbeddedChat agentId={PUBLIC_CHAT_AGENT_ID} />
        </div>
      </section>
    </div>
  );
}

/** Shared dashboard menu rail. `active` selects which slot the white selection
 *  pill rests on. `handoff` hides slot icons while the scroll-driven fly layer
 *  carries them in from scene 1. */
function DashRail({
  active,
  handoff,
  onSlotRef,
  onSelect,
}: {
  active: number;
  handoff: number;
  onSlotRef: (index: number) => (el: HTMLDivElement | null) => void;
  onSelect: (index: number) => void;
}) {
  const slotRefs = useRef<Array<HTMLDivElement | null>>([]);
  const [pillY, setPillY] = useState(0);
  const [pillVisible, setPillVisible] = useState(false);

  useLayoutEffect(() => {
    const measure = () => {
      const target = slotRefs.current[active];
      if (target) setPillY(target.offsetTop);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [active]);

  useEffect(() => {
    if (handoff < 0.999) {
      setPillVisible(false);
      return;
    }
    const timer = window.setTimeout(() => setPillVisible(true), 150);
    return () => window.clearTimeout(timer);
  }, [handoff]);

  const setSlot = (i: number) => (el: HTMLDivElement | null) => {
    slotRefs.current[i] = el;
    onSlotRef(i)(el);
  };

  const slotOpacity =
    handoff >= 1
      ? 1
      : handoff >= 0.88
        ? (handoff - 0.88) / 0.12
        : 0;

  const railStyle = { "--pill-y": `${pillY}px` } as CSSProperties;

  const slotProps = (i: number, label: string) => ({
    className: "lp-dash-slot",
    ref: setSlot(i),
    role: "tab" as const,
    tabIndex: 0,
    "aria-label": label,
    "aria-selected": active === i,
    onClick: () => onSelect(i),
    onKeyDown: (e: ReactKeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect(i);
      }
    },
  });

  return (
    <div className="lp-dash-rail" style={railStyle} role="tablist">
      <div
        className="lp-dash-pill"
        style={{ opacity: pillVisible ? 1 : 0 }}
        aria-hidden
      />
      <span className="lp-dash-rail-label" aria-hidden>资料</span>
      <div {...slotProps(0, "资料")}>
        <img src={icKnowledge} alt="" style={{ opacity: slotOpacity }} />
      </div>
      <span className="lp-dash-rail-label lp-dash-rail-label--cap" aria-hidden>能力</span>
      <div {...slotProps(1, "能力一")}>
        <img src={icText} alt="" style={{ opacity: slotOpacity }} />
      </div>
      <div {...slotProps(2, "能力二")}>
        <img src={icSop} alt="" style={{ opacity: slotOpacity }} />
      </div>
      <div {...slotProps(3, "能力三")}>
        <img src={icTool} alt="" style={{ opacity: slotOpacity }} />
      </div>
    </div>
  );
}
