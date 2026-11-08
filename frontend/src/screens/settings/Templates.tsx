import { useEffect, useState, type JSX } from "react";
import { api } from "@/shared/api/http";
import { SettingsLayout } from "./SettingsLayout";

type Template = {
  fingerprint: string;
  institution: string;
  header_row_index: number;
  date_column: string;
  date_format: string;
  narration_column: string;
  delimiter: string;
  debit_column: string | null;
  credit_column: string | null;
  amount_column: string | null;
  sign_column: string | null;
  credit_indicators: string[];
  account_marker_pattern: string | null;
  account_marker_map: Record<string, string>;
  default_account: string | null;
};

export function SettingsTemplates(): JSX.Element {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [editing, setEditing] = useState<Template | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function load() {
    const res = await api.getJSON<{ templates: Template[] }>("/api/templates");
    setTemplates(res.templates);
  }

  useEffect(() => {
    api
      .getJSON<{ templates: Template[] }>("/api/templates")
      .then((res) => setTemplates(res.templates))
      .catch((e) => setBanner(String(e)));
  }, []);

  async function save(t: Template) {
    setBanner(null);
    try {
      await api.putJSON(`/api/templates/${t.fingerprint}`, t);
      setEditing(null);
      await load();
    } catch (e) {
      setBanner(String(e));
    }
  }

  async function remove(fingerprint: string) {
    if (!window.confirm(`Delete template ${fingerprint}?`)) return;
    setBanner(null);
    try {
      await api.del(`/api/templates/${fingerprint}`);
      await load();
    } catch (e) {
      setBanner(String(e));
    }
  }

  return (
    <SettingsLayout>
      {banner && (
        <div className="callout warn" style={{ marginBottom: "1.0769rem" }}>
          {banner}
        </div>
      )}

      {templates.length === 0 && !banner && (
        <div className="callout info">
          No import templates saved yet. Templates are created automatically when you import a
          file and map its columns.
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill,minmax(21.5385rem,1fr))",
          gap: "0.9231rem",
        }}
      >
        {templates.map((t) => (
          <div key={t.fingerprint} className="panel" style={{ marginBottom: 0 }}>
            <div
              className="panel-head"
              style={{ flexDirection: "column", alignItems: "flex-start", gap: 3 }}
            >
              <div>{t.institution}</div>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: "0.7692rem",
                  color: "var(--text3)",
                  fontWeight: 400,
                }}
              >
                {t.fingerprint} · CSV
              </div>
            </div>
            <div style={{ padding: "0.6154rem 1.0769rem 0.4615rem", display: "flex", flexDirection: "column", gap: "0.3846rem" }}>
              {(
                [
                  ["Date col", t.date_column],
                  ["Narration col", t.narration_column],
                  ["Default account", t.default_account ?? "—"],
                ] as [string, string][]
              ).map(([label, val]) => (
                <div key={label} style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8462rem" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "0.7692rem", color: "var(--text3)" }}>
                    {label}
                  </span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "0.8462rem", color: "var(--text2)" }}>
                    {val}
                  </span>
                </div>
              ))}
            </div>
            <div
              style={{
                display: "flex",
                gap: "0.4615rem",
                padding: "0.6154rem 0.9231rem",
                borderTop: "1px solid var(--border)",
                background: "var(--bg)",
              }}
            >
              <button className="btn sm" onClick={() => setEditing({ ...t })}>
                Edit
              </button>
              <button
                className="btn sm"
                style={{ borderColor: "rgba(248,113,113,0.4)", color: "var(--red)" }}
                onClick={() => remove(t.fingerprint)}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <div className="panel" style={{ marginTop: "1.5385rem" }}>
          <div className="panel-head">
            Edit — {editing.fingerprint}
            <div className="right">
              <button className="btn sm" onClick={() => setEditing(null)}>
                Cancel
              </button>
            </div>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save(editing);
            }}
            style={{ padding: "1.1538rem", display: "flex", flexDirection: "column", gap: "0.9231rem" }}
          >
            {(
              [
                ["institution", "Institution"],
                ["date_column", "Date column"],
                ["date_format", "Date format (strptime)"],
                ["narration_column", "Narration column"],
                ["default_account", "Default account"],
              ] as const
            ).map(([field, label]) => (
              <div className="field" key={field} style={{ marginBottom: 0 }}>
                <label>{label}</label>
                <input
                  type="text"
                  value={(editing[field as keyof Template] as string) ?? ""}
                  onChange={(e) =>
                    setEditing({ ...editing, [field]: e.target.value || null })
                  }
                />
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.6154rem" }}>
              <button type="button" className="btn" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="submit" className="btn primary">
                Save template
              </button>
            </div>
          </form>
        </div>
      )}
    </SettingsLayout>
  );
}
