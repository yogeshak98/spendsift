import { useState } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";
import { Callout } from "../../../components/Callout";

type Props<R> = {
  file: File;
  onResult: (res: R) => void;
  onCancel?: () => void;
};

// The password is typed into local state, posted once as a multipart body
// field to the loopback backend, and cleared in `finally` whatever happens.
export function PdfUnlock<R = unknown>({ file, onResult, onCancel }: Props<R>) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ description: string; resolution: string | null } | null>(
    null,
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("password", password);
      const res = await api.postForm<R>("/api/import/unlock", form);
      onResult(res);
    } catch (err) {
      const coded = parseCodedError(err);
      setError(
        coded
          ? { description: coded.description, resolution: coded.resolution }
          : { description: "Could not unlock this PDF.", resolution: null },
      );
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <form className="pdf-unlock" onSubmit={submit}>
      <p>{file.name} is password protected.</p>
      <input
        type="password"
        autoComplete="new-password"
        aria-label="PDF password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button type="submit" className="primary" disabled={busy}>
        Unlock
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      )}
      {error && (
        <Callout kind="bad">
          {error.description}
          {error.resolution && <> {error.resolution}</>}
        </Callout>
      )}
    </form>
  );
}
