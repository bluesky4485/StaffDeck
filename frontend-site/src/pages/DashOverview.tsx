import profilePersona from "../assets/landing/hv-persona.png";
import dashWorklog from "../assets/landing/dash-worklog@2x.png";
import innovationCard from "../assets/landing/dash-innovation-card@2x.png";
import featModules from "../assets/landing/dash-feat-modules@2x.png";
import featOnline from "../assets/landing/dash-feat-online@2x.png";
import featCreate from "../assets/landing/dash-feat-create@2x.png";

const FEATURE_TABS = [
  { id: "innovation", label: "核心创新", active: true },
  { id: "modules", label: "功能模块" },
  { id: "online", label: "全天在线" },
  { id: "create", label: "一句话创建" },
] as const;

const SMALL_CARDS = [
  { src: featModules, alt: "6大功能模块：技能 · 知识 · 工具 · 定时任务 · 可观测 · 记忆" },
  { src: featOnline, alt: "7X24全天候在线：主动履职，不用等人来问" },
  { src: featCreate, alt: "1句话创建SOP：可打断、可恢复、可多线并行" },
] as const;

const ROLE_TAGS = ["结构化整理", "可追溯", "可追溯"];

const STATS = [
  { value: "12", label: "资料" },
  { value: "3", label: "技能" },
  { value: "20", label: "SOP" },
] as const;

function TabIcon({ type }: { type: (typeof FEATURE_TABS)[number]["id"] }) {
  switch (type) {
    case "innovation":
      return (
        <svg viewBox="0 0 14 14" aria-hidden>
          <rect x="2" y="3" width="10" height="9" rx="1.5" stroke="currentColor" fill="none" strokeWidth="1.2" />
          <path d="M2 6h10" stroke="currentColor" strokeWidth="1.2" />
          <path d="M5 1.5v2M9 1.5v2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      );
    case "modules":
      return (
        <svg viewBox="0 0 14 14" aria-hidden>
          <rect x="1.5" y="4" width="11" height="8" rx="1.2" stroke="currentColor" fill="none" strokeWidth="1.2" />
          <path d="M4.5 4V3a2.5 2.5 0 0 1 5 0v1" stroke="currentColor" strokeWidth="1.2" fill="none" />
        </svg>
      );
    case "online":
      return (
        <svg viewBox="0 0 14 14" aria-hidden>
          <circle cx="7" cy="7.5" r="5" stroke="currentColor" fill="none" strokeWidth="1.2" />
          <path d="M7 4.5v3.2l2 1.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      );
    case "create":
      return (
        <svg viewBox="0 0 14 14" aria-hidden>
          <path d="M2 10.5l7.5-7.5 2 2L4 12.5H2v-2z" stroke="currentColor" fill="none" strokeWidth="1.1" strokeLinejoin="round" />
          <path d="M8.5 3.5l2 2" stroke="currentColor" strokeWidth="1.1" />
        </svg>
      );
  }
}

export default function DashOverview({ handoff = 1 }: { handoff?: number }) {
  // The hero persona flies in from scene 1 and lands on this slot; keep the
  // card's own persona hidden until the flyer arrives, then cross-fade it in.
  const personaOpacity =
    handoff >= 1 ? 1 : handoff >= 0.88 ? (handoff - 0.88) / 0.12 : 0;

  return (
    <div className="lp-do">
      <div className="lp-do-left">
        <section className="lp-do-profile">
          <div className="lp-do-greet">
            <img
              className="lp-do-persona"
              src={profilePersona}
              alt=""
              style={{ opacity: personaOpacity }}
            />
            <div className="lp-do-greet-copy">
              <p className="lp-do-greet-title">Hello StaffDeck！</p>
              <p className="lp-do-greet-sub">我们来做什么？</p>
            </div>
          </div>

          <div className="lp-do-meta">
            <div className="lp-do-role">
              <p className="lp-do-role-text">
                #角色：知识运营官「StaffDeck」一名经验丰富的知识运营官
              </p>
              <div className="lp-do-tags">
                {ROLE_TAGS.map((tag, i) => (
                  <span className="lp-do-tag" key={`${tag}-${i}`}>
                    {tag}
                  </span>
                ))}
              </div>
            </div>
            <div className="lp-do-stats">
              {STATS.map((s, i) => (
                <div
                  className="lp-do-stat"
                  data-edge={i === 0 ? "start" : i === STATS.length - 1 ? "end" : "mid"}
                  key={s.label}
                >
                  <span className="lp-do-stat-val">{s.value}</span>
                  <span className="lp-do-stat-label">{s.label}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <nav className="lp-do-tabs" role="tablist" aria-label="能力分类">
          {FEATURE_TABS.map((tab) => (
            <span
              className="lp-do-tab"
              data-active={"active" in tab && tab.active}
              key={tab.id}
              role="tab"
              aria-selected={"active" in tab && tab.active}
            >
              <TabIcon type={tab.id} />
              {tab.label}
            </span>
          ))}
        </nav>

        <section className="lp-do-features">
          <img
            className="lp-do-feat-img lp-do-feat-img--large"
            src={innovationCard}
            alt="3大核心创新：数字员工档案 · SOP状态机 · OKF知识本体"
          />
          <div className="lp-do-feat-stack">
            {SMALL_CARDS.map((card) => (
              <img
                className="lp-do-feat-img"
                src={card.src}
                alt={card.alt}
                key={card.alt}
              />
            ))}
          </div>
        </section>
      </div>

      <aside className="lp-do-right">
        <div className="lp-do-right-head">
          <div className="lp-do-worklog-head">
            <svg viewBox="0 0 14 14" aria-hidden>
              <rect x="2.5" y="2" width="9" height="11" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.2" />
              <path d="M5 2V1.2h4V2" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              <path d="M4.8 6h4.4M4.8 9h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
            </svg>
            工作记录
          </div>
        </div>
        <img
          className="lp-do-worklog-img"
          src={dashWorklog}
          alt="工作记录时间线"
        />
      </aside>
    </div>
  );
}
