import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Loader2,
  Plus,
  QrCode,
  RefreshCcw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import "./styles.css";

type Account = {
  account_id: string;
  name: string;
  status: string;
  login_status: string;
  avatar?: string | null;
  note?: string | null;
  user_id?: string | null;
  uid?: string | null;
  douyin_id?: string | null;
  sec_uid?: string | null;
  employee_no?: string | null;
  totp?: string | null;
  updated_at?: string | null;
};

type ResolveResult = {
  uid?: string | null;
  sec_uid: string;
  unique_id?: string | null;
  short_id?: string | null;
  nickname?: string | null;
  source_account_id: string | null;
  from_cache: boolean;
};

const api = {
  accounts: "/api/accounts",
  qrGenerate: "/api/auth/qrcode/generate",
  qrPoll: (sessionId: string) => `/api/auth/qrcode/poll?session_id=${encodeURIComponent(sessionId)}`,
  checkLogin: "/api/creator/check-login-status",
  deleteInvalid: "/api/accounts/invalid",
  deleteAccount: (id: string) => `/api/accounts/${encodeURIComponent(id)}`,
  resolveUser: (keyword: string) => `/api/douyin/web/resolve_user_sec_uid?keyword=${encodeURIComponent(keyword)}`,
};

function App() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [qrOpen, setQrOpen] = useState(false);
  const [qrImage, setQrImage] = useState<string | null>(null);
  const [qrStatus, setQrStatus] = useState<"idle" | "loading" | "code" | "confirmed" | "error">("idle");
  const [note, setNote] = useState("");
  const [lookupType, setLookupType] = useState<"uid" | "douyin_id">("uid");
  const [lookupValue, setLookupValue] = useState("");
  const [lookupResult, setLookupResult] = useState<ResolveResult | null>(null);
  const [message, setMessage] = useState("");
  const pollRef = useRef<number | null>(null);

  const filtered = useMemo(() => {
    const text = keyword.trim().toLowerCase();
    if (!text) return accounts;
    return accounts.filter((account) =>
      [account.name, account.account_id, account.user_id, account.uid, account.douyin_id, account.sec_uid, account.employee_no, account.totp, account.note]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(text)),
    );
  }, [accounts, keyword]);

  const stats = useMemo(() => {
    const online = accounts.filter((item) => item.login_status === "logged_in").length;
    const abnormal = accounts.filter((item) => item.status !== "valid" || ["session_expired", "error"].includes(item.login_status)).length;
    return { total: accounts.length, online, abnormal };
  }, [accounts]);

  useEffect(() => {
    loadAccounts();
    return () => stopPolling();
  }, []);

  async function request<T>(url: string, options?: RequestInit): Promise<T> {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.success === false) {
      throw new Error(body.detail || body.message || `HTTP ${response.status}`);
    }
    return body;
  }

  async function loadAccounts() {
    setLoading(true);
    try {
      const body = await request<{ items: Account[] }>(api.accounts);
      setAccounts(body.items || []);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setLoading(false);
    }
  }

  function stopPolling() {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  async function startQrLogin() {
    stopPolling();
    setQrStatus("loading");
    setQrImage(null);
    setMessage("");
    try {
      const body = await request<{ qr_id: string; qr_image: string }>(api.qrGenerate, {
        method: "POST",
        body: JSON.stringify({ note: note.trim() || null, headless: true }),
      });
      setQrImage(body.qr_image);
      setQrStatus("code");
      pollRef.current = window.setInterval(async () => {
        try {
          const poll = await request<{ status: string; message?: string }>(api.qrPoll(body.qr_id));
          if (poll.status === "confirmed") {
            stopPolling();
            setQrStatus("confirmed");
            await loadAccounts();
            setTimeout(() => {
              setQrOpen(false);
              setQrStatus("idle");
              setQrImage(null);
              setNote("");
            }, 600);
          } else if (poll.status === "failed" || poll.status === "expired") {
            stopPolling();
            setQrStatus("error");
            setMessage(poll.message || "二维码已失效");
          }
        } catch (error) {
          stopPolling();
          setQrStatus("error");
          setMessage(String(error));
        }
      }, 1800);
    } catch (error) {
      setQrStatus("error");
      setMessage(String(error));
    }
  }

  async function checkAll() {
    setChecking(true);
    setMessage("");
    try {
      const accountIds = accounts.map((account) => account.account_id);
      await request(api.checkLogin, { method: "POST", body: JSON.stringify({ account_ids: accountIds }) });
      await loadAccounts();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setChecking(false);
    }
  }

  async function cleanInvalid() {
    setMessage("");
    try {
      const body = await request<{ count: number }>(api.deleteInvalid, { method: "DELETE" });
      setMessage(`已清理 ${body.count} 个异常账号`);
      await loadAccounts();
    } catch (error) {
      setMessage(String(error));
      await loadAccounts();
    }
  }

  async function removeAccount(accountId: string) {
    await request(api.deleteAccount(accountId), { method: "DELETE" });
    await loadAccounts();
  }

  async function resolveSecUid(event: React.FormEvent) {
    event.preventDefault();
    const value = lookupValue.trim();
    if (!value) return;
    setLookupResult(null);
    setMessage("");
    try {
      const body = await request<ResolveResult | { data: ResolveResult }>(api.resolveUser(value));
      setLookupResult("data" in body ? body.data : body);
    } catch (error) {
      setMessage(String(error));
    }
  }

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <h1>抖音账号库</h1>
          <p>集中保存扫码登录态，并用账号库 cookie 提供 uid / 抖音号到 sec_uid 的解析接口。</p>
        </div>
        <button className="primary-btn" onClick={() => setQrOpen(true)}>
          <Plus size={17} />
          添加账号
        </button>
      </section>

      <section className="summary-row" aria-label="账号摘要">
        <Metric label="账号总数" value={stats.total} />
        <Metric label="在线账号" value={stats.online} tone="good" />
        <Metric label="异常账号" value={stats.abnormal} tone={stats.abnormal ? "bad" : "neutral"} />
      </section>

      <section className="lookup-band">
        <form onSubmit={resolveSecUid} className="lookup-form">
          <div className="segmented">
            <button type="button" className={lookupType === "uid" ? "active" : ""} onClick={() => setLookupType("uid")}>
              uid
            </button>
            <button type="button" className={lookupType === "douyin_id" ? "active" : ""} onClick={() => setLookupType("douyin_id")}>
              抖音号
            </button>
          </div>
          <div className="search-input">
            <Search size={16} />
            <input value={lookupValue} onChange={(event) => setLookupValue(event.target.value)} placeholder="输入目标用户 uid 或抖音号查询 sec_uid" />
          </div>
          <button className="secondary-btn" type="submit">查询</button>
        </form>
        {lookupResult && (
          <div className="result-line">
            <span>{lookupResult.sec_uid}</span>
            <button onClick={() => navigator.clipboard.writeText(lookupResult.sec_uid)} title="复制 sec_uid">
              <Copy size={15} />
            </button>
            <small>
              {lookupResult.nickname || lookupResult.unique_id || lookupResult.uid || "目标用户"} ·{" "}
              uid {lookupResult.uid || "-"} · 抖音号 {lookupResult.unique_id || lookupResult.short_id || "-"} ·{" "}
              {lookupResult.from_cache ? "缓存命中" : `来源账号 ${lookupResult.source_account_id || "-"}`}
            </small>
          </div>
        )}
      </section>

      {message && <div className="notice">{message}</div>}

      <section className="table-toolbar">
        <div className="search-input compact">
          <Search size={16} />
          <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索账号、uid、抖音号、sec_uid" />
        </div>
        <div className="toolbar-actions">
          <button className="secondary-btn" onClick={checkAll} disabled={checking || accounts.length === 0}>
            {checking ? <Loader2 size={16} className="spin" /> : <RefreshCcw size={16} />}
            检测登录状态
          </button>
          <button className="danger-btn" onClick={cleanInvalid} disabled={accounts.length === 0}>
            <Trash2 size={16} />
            清理异常账号
          </button>
        </div>
      </section>

      <AccountTable accounts={filtered} loading={loading} onDelete={removeAccount} />

      {qrOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-panel">
            <div className="modal-head">
              <div>
                <h2>扫码绑定抖音账号</h2>
                <p>登录成功后，后端会保存 storage_state 并写入账号库。</p>
              </div>
              <button className="icon-btn" onClick={() => setQrOpen(false)} aria-label="关闭">
                <X size={18} />
              </button>
            </div>
            <label className="field">
              <span>备注</span>
              <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="可选，例如：主查询账号" />
            </label>
            <div className="qr-zone">
              {qrStatus === "idle" && (
                <button className="primary-btn" onClick={startQrLogin}>
                  <QrCode size={17} />
                  获取二维码
                </button>
              )}
              {qrStatus === "loading" && (
                <div className="status-stack">
                  <Loader2 className="spin" size={28} />
                  <span>正在初始化抖音二维码</span>
                </div>
              )}
              {qrStatus === "code" && qrImage && (
                <div className="status-stack">
                  <img src={qrImage} alt="抖音登录二维码" className="qr-image" />
                  <span>请使用抖音 App 扫码并在手机上确认</span>
                  <button className="secondary-btn" onClick={startQrLogin}>刷新二维码</button>
                </div>
              )}
              {qrStatus === "confirmed" && (
                <div className="status-stack ok">
                  <CheckCircle2 size={30} />
                  <span>绑定成功，账号库已刷新</span>
                </div>
              )}
              {qrStatus === "error" && (
                <div className="status-stack bad">
                  <AlertTriangle size={30} />
                  <span>二维码初始化或轮询失败</span>
                  <button className="secondary-btn" onClick={startQrLogin}>重新获取</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function Metric({ label, value, tone = "neutral" }: { label: string; value: number; tone?: "neutral" | "good" | "bad" }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function AccountTable({ accounts, loading, onDelete }: { accounts: Account[]; loading: boolean; onDelete: (id: string) => Promise<void> }) {
  if (loading) {
    return <div className="empty-state">正在加载账号列表...</div>;
  }
  if (!accounts.length) {
    return <div className="empty-state">暂无账号，先扫码绑定一个抖音账号。</div>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>账号</th>
            <th>平台标识</th>
            <th>sec_uid</th>
            <th>员工号 / TOTP</th>
            <th>登录状态</th>
            <th>备注</th>
            <th>更新时间</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.account_id}>
              <td>
                <div className="account-cell">
                  <img src={account.avatar || `https://api.dicebear.com/9.x/initials/svg?seed=${encodeURIComponent(account.name)}`} alt={account.name} />
                  <div>
                    <strong>{account.name}</strong>
                    <span>{account.account_id}</span>
                  </div>
                </div>
              </td>
              <td>
                <div className="mono-stack">
                  <span>userid: {account.user_id || "-"}</span>
                  <span>uid: {account.uid || "-"}</span>
                  <span>抖音号: {account.douyin_id || "-"}</span>
                </div>
              </td>
              <td className="mono-cell">{account.sec_uid || "-"}</td>
              <td>
                <div className="mono-stack">
                  <span>员工号: {account.employee_no || "-"}</span>
                  <span>TOTP: {account.totp || "-"}</span>
                </div>
              </td>
              <td>
                <StatusBadge value={account.login_status} />
              </td>
              <td>{account.note || "-"}</td>
              <td>{account.updated_at ? new Date(account.updated_at).toLocaleString() : "-"}</td>
              <td>
                <button className="icon-btn danger" onClick={() => onDelete(account.account_id)} title="删除账号">
                  <Trash2 size={16} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ value }: { value: string }) {
  const map: Record<string, string> = {
    logged_in: "在线",
    session_expired: "已掉线",
    error: "异常",
    unknown: "未知",
  };
  return <span className={`status-badge ${value}`}>{map[value] || value}</span>;
}

createRoot(document.getElementById("root")!).render(<App />);
